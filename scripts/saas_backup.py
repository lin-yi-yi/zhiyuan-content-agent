#!/usr/bin/env python3
"""Offline, allowlisted SaaS snapshots. No extractall and no existing-store restore."""
import argparse
from contextlib import contextmanager, ExitStack
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import stat
import tempfile
import zipfile


ROOT = Path(__file__).resolve().parents[1]
CHUNK = 1024 * 1024
MAX_FILES = 20_000
MAX_TOTAL_BYTES = 20 * 1024 ** 3
MAX_MANIFEST_BYTES = 10 * 1024 ** 2
ORG = r"[a-f0-9]{32}"


class BackupError(ValueError):
    pass


def _allowed(name):
    path = PurePosixPath(name)
    return (isinstance(name, str) and len(name) <= 2048 and "\\" not in name
            and not any(ord(char) < 32 for char in name) and not path.is_absolute()
            and all(part not in {"", ".", ".."} for part in name.split("/"))
            and (name == "control.db" or bool(re.fullmatch(rf"tenants/{ORG}/content\.db", name))
                 or bool(re.fullmatch(rf"tenants/{ORG}/vectors/.+", name))))


def _excluded(path):
    return (any(part == ".env" or part.startswith(".env.") for part in path.parts)
            or path.name == ".lock" or path.suffix.lower() in {".safetensors", ".onnx", ".pt", ".pth"}
            or path.name.endswith(("-wal", "-shm", "-journal"))
            or path.name in {"pytorch_model.bin", "tf_model.h5", "flax_model.msgpack"})


def _regular(path):
    if path.is_symlink() or not path.is_file():
        raise BackupError("数据目录含符号链接或非普通文件，已拒绝备份。")


@contextmanager
def _lock(path, *, create=False):
    if path.is_symlink():
        raise BackupError("锁文件不能是符号链接。")
    flags = os.O_RDWR | (os.O_CREAT if create else 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise BackupError("锁文件不是普通文件。")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise BackupError("检测到活跃服务或向量索引锁；请先停止服务。") from None
        yield
    finally:
        os.close(descriptor)


def _sqlite_check(path):
    try:
        with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2) as database:
            if database.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise BackupError("SQLite 完整性检查失败。")
    except sqlite3.Error:
        raise BackupError("SQLite 完整性检查失败。") from None


def _sqlite_copy(source, target):
    if any(Path(str(source) + suffix).is_symlink() for suffix in ("-wal", "-shm", "-journal")):
        raise BackupError("SQLite 附属文件不能是符号链接。")
    _sqlite_check(source)
    try:
        with sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True, timeout=2) as original:
            with sqlite3.connect(target) as snapshot:
                original.backup(snapshot)
    except sqlite3.Error:
        raise BackupError("无法创建 SQLite 一致性快照。") from None
    _sqlite_check(target)


def _hash(path):
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK), b""):
            value.update(chunk)
    return value.hexdigest()


def _collect(root):
    control = root / "control.db"
    _regular(control)
    files, locks = [(control, "sqlite")], []
    tenants = root / "tenants"
    if tenants.is_symlink():
        raise BackupError("组织目录不能是符号链接。")
    if tenants.exists():
        for tenant in sorted(tenants.iterdir()):
            if tenant.is_symlink() or not tenant.is_dir() or not re.fullmatch(ORG, tenant.name):
                raise BackupError("组织目录名称或类型无效。")
            database = tenant / "content.db"
            if database.exists() or database.is_symlink():
                _regular(database)
                files.append((database, "sqlite"))
            vectors = tenant / "vectors"
            if vectors.is_symlink():
                raise BackupError("向量目录不能是符号链接。")
            if not vectors.exists():
                continue
            for current, directories, names in os.walk(vectors, followlinks=False):
                folder = Path(current)
                if any((folder / name).is_symlink() for name in directories):
                    raise BackupError("向量目录含符号链接。")
                for name in names:
                    path = folder / name
                    _regular(path)
                    if name == ".lock":
                        locks.append(path)
                    elif not _excluded(path.relative_to(root)):
                        kind = "sqlite" if path.suffix.lower() in {".db", ".sqlite", ".sqlite3"} else "vector"
                        # WAL is included in its SQLite backup, never replayed twice.
                        if not name.endswith(("-wal", "-shm", "-journal")):
                            files.append((path, kind))
    if len(files) > MAX_FILES or sum(path.stat().st_size for path, _ in files) > MAX_TOTAL_BYTES:
        raise BackupError("备份超过本工具支持的文件数或容量。")
    return files, locks


def create_backup(data_dir, archive, *, offline_confirm=False):
    if not offline_confirm:
        raise BackupError("先停止服务，再显式提供 --offline-confirm；此参数确认跨库快照期间没有其他写入者。")
    source, output = Path(data_dir).absolute(), Path(archive).absolute()
    if source.is_symlink() or not source.is_dir():
        raise BackupError("数据目录必须是现有的真实目录。")
    if output.exists() or output.is_symlink():
        raise BackupError("备份文件已存在，拒绝覆盖。")
    if (output.parent.resolve() / output.name).is_relative_to(source.resolve()):
        raise BackupError("备份必须保存在 SaaS 数据目录之外。")
    if not output.parent.is_dir():
        raise BackupError("备份目标的父目录不存在。")
    with ExitStack() as stack:
        stack.enter_context(_lock(source / ".service.lock", create=True))
        files, locks = _collect(source)
        for path in locks:
            stack.enter_context(_lock(path))
        with tempfile.TemporaryDirectory(prefix="saas-snapshot-") as temporary:
            staging, entries = Path(temporary), []
            for path, kind in files:
                relative = path.relative_to(source).as_posix()
                if not _allowed(relative):
                    raise BackupError("备份内容不在允许的路径内。")
                target = staging / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                _sqlite_copy(path, target) if kind == "sqlite" else shutil.copyfile(path, target)
                entries.append({"path": relative, "kind": kind, "size": target.stat().st_size, "sha256": _hash(target)})
            if sum(entry["size"] for entry in entries) > MAX_TOTAL_BYTES:
                raise BackupError("一致性快照超过本工具支持的容量。")
            manifest = {"format": "zhiyuan-saas-backup", "version": 1,
                        "created_at": datetime.now(timezone.utc).isoformat(), "files": entries,
                        "scope": "control database, tenant databases and vector indexes; no env or model cache"}
            manifest_bytes = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
            if len(manifest_bytes) > MAX_MANIFEST_BYTES:
                raise BackupError("备份清单超过大小限制。")
            descriptor, filename = tempfile.mkstemp(prefix=".saas-backup-", suffix=".zip", dir=output.parent)
            os.close(descriptor)
            temporary_archive = Path(filename)
            try:
                with zipfile.ZipFile(temporary_archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
                    bundle.writestr("manifest.json", manifest_bytes)
                    for entry in entries:
                        bundle.write(staging / entry["path"], entry["path"])
                # Atomic publication without overwriting an existing output.
                os.link(temporary_archive, output)
            finally:
                temporary_archive.unlink(missing_ok=True)
    return {"archive": str(output), "files": len(entries), "bytes": sum(entry["size"] for entry in entries)}


def _manifest(bundle):
    infos = bundle.infolist()
    names = [info.filename for info in infos]
    if len(infos) > MAX_FILES + 1 or len(set(names)) != len(names) or "manifest.json" not in names:
        raise BackupError("备份成员重复、缺失或过多。")
    if any(info.is_dir() or stat.S_ISLNK(info.external_attr >> 16)
           or (stat.S_IFMT(info.external_attr >> 16) not in {0, stat.S_IFREG}) for info in infos):
        raise BackupError("备份包含符号链接或非普通文件。")
    info = bundle.getinfo("manifest.json")
    if info.file_size > MAX_MANIFEST_BYTES:
        raise BackupError("备份清单过大。")
    try:
        manifest = json.loads(bundle.read(info))
        entries = manifest["files"]
        if manifest["format"] != "zhiyuan-saas-backup" or manifest["version"] != 1 or not isinstance(entries, list):
            raise ValueError
        paths = [entry["path"] for entry in entries]
        if (len(set(paths)) != len(paths) or "control.db" not in paths
                or set(names) != {"manifest.json", *paths}):
            raise ValueError
        for entry in entries:
            path = entry["path"]
            if (not _allowed(path) or _excluded(PurePosixPath(path)) or entry["kind"] not in {"sqlite", "vector"}
                    or type(entry["size"]) is not int or entry["size"] < 0
                    or not re.fullmatch(r"[a-f0-9]{64}", entry["sha256"])
                    or bundle.getinfo(path).file_size != entry["size"]):
                raise ValueError
            if (path == "control.db" or path.endswith("/content.db") or PurePosixPath(path).suffix.lower() in {".db", ".sqlite", ".sqlite3"}) and entry["kind"] != "sqlite":
                raise ValueError
        if sum(entry["size"] for entry in entries) > MAX_TOTAL_BYTES:
            raise ValueError
    except (ValueError, TypeError, KeyError, UnicodeError):
        raise BackupError("备份清单无效、路径越界或容量超过限制。") from None
    return entries


def restore_backup(archive, destination, *, offline_confirm=False):
    if not offline_confirm:
        raise BackupError("恢复前必须提供 --offline-confirm，确认服务已经停止。")
    archive, destination = Path(archive).absolute(), Path(destination).absolute()
    _regular(archive)
    if destination.is_symlink() or (destination.exists() and (not destination.is_dir() or any(destination.iterdir()))):
        raise BackupError("只允许恢复到不存在或全新的空目录，不覆盖现有数据。")
    if not destination.parent.is_dir():
        raise BackupError("恢复目录的父目录不存在。")
    staging = Path(tempfile.mkdtemp(prefix=".saas-restore-", dir=destination.parent))
    try:
        with zipfile.ZipFile(archive) as bundle:
            entries = _manifest(bundle)
            for entry in entries:
                target, digest, copied = staging / entry["path"], hashlib.sha256(), 0
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                with bundle.open(entry["path"]) as original, target.open("xb") as output:
                    os.chmod(target, 0o600)
                    while chunk := original.read(CHUNK):
                        copied += len(chunk)
                        if copied > entry["size"]:
                            raise BackupError("备份文件超出声明大小。")
                        digest.update(chunk)
                        output.write(chunk)
                if copied != entry["size"] or digest.hexdigest() != entry["sha256"]:
                    raise BackupError("备份哈希校验失败，数据未恢复。")
                if entry["kind"] == "sqlite":
                    _sqlite_check(target)
        if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
            raise BackupError("恢复目标发生变化，已停止。")
        os.rename(staging, destination)
        return {"destination": str(destination), "files": len(entries)}
    except (zipfile.BadZipFile, RuntimeError, EOFError):
        raise BackupError("备份压缩包损坏或格式不受支持。") from None
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    backup = commands.add_parser("backup")
    backup.add_argument("--data-dir", default=os.getenv("SAAS_DATA_DIR", str(ROOT / ".data" / "saas")))
    backup.add_argument("--output", required=True)
    backup.add_argument("--offline-confirm", action="store_true")
    restore = commands.add_parser("restore")
    restore.add_argument("--archive", required=True)
    restore.add_argument("--destination", required=True)
    restore.add_argument("--offline-confirm", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = (create_backup(args.data_dir, args.output, offline_confirm=args.offline_confirm)
                  if args.command == "backup" else restore_backup(args.archive, args.destination, offline_confirm=args.offline_confirm))
    except (BackupError, OSError) as exc:
        parser.exit(2, f"备份/恢复未完成：{str(exc) if isinstance(exc, BackupError) else '文件系统操作失败，请检查目录权限。'}\n")
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
