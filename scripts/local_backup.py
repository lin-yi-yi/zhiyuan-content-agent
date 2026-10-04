#!/usr/bin/env python3
"""Offline local SQLite/Qdrant snapshots; explicit paths, private archives, no overwrite."""
import argparse
from contextlib import ExitStack
from datetime import datetime, timezone
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import sys
import tempfile
import zipfile
import zlib

# These helpers use only the standard library and never load application settings/.env.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import saas_backup as common


BackupError = common.BackupError
FORMAT = "zhiyuan-local-backup"


def _path(value):
    path = Path(value).absolute()
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise BackupError("所选路径含符号链接，请使用真实路径。")
    return path.resolve()


def _name(value):
    return (isinstance(value, str) and 0 < len(value) <= 255 and value not in {".", ".."}
            and not any(char in value for char in "/\\:") and not any(ord(char) < 32 for char in value))


def _kind(name):
    if not isinstance(name, str):
        return None
    if name == "demo.db":
        return "sqlite"
    if name == "qdrant/meta.json":
        return "vector"
    parts = name.split("/")
    if len(parts) == 4 and parts[:2] == ["qdrant", "collection"] and _name(parts[2]) and parts[3] == "storage.sqlite":
        return "sqlite"
    return None


def _excluded(path):
    # Only the explicit Qdrant file allowlist is ever archived, even for names not listed here.
    parts = [part.lower() for part in path.parts]
    return (common._excluded(path) or any(part in {"models", "cache", ".cache", "secrets", "credentials"} for part in parts)
            or any(part.endswith(".lock") for part in parts)
            or path.suffix.lower() in {".key", ".pem", ".p12", ".pfx"}
            or path.name.lower().startswith(("credentials.", "secrets.", "id_rsa", "id_ed25519")))


def _private_parents(root, target):
    parent = root
    for part in target.relative_to(root).parts[:-1]:
        parent = parent / part
        parent.mkdir(exist_ok=True, mode=0o700)


def _collect(database, vectors):
    files = [(database, "demo.db", "sqlite")]
    if vectors is not None and vectors.exists():
        for current, directories, names in os.walk(vectors, followlinks=False):
            folder = Path(current)
            if any((folder / name).is_symlink() for name in directories):
                raise BackupError("向量目录含符号链接。")
            for name in names:
                path = folder / name
                common._regular(path)
                relative = Path("qdrant") / path.relative_to(vectors)
                if _excluded(relative):
                    continue
                kind = _kind(relative.as_posix())
                if kind is None:
                    raise BackupError("向量目录含不支持的文件；仅支持当前本地 Qdrant 的 meta.json 和 collection/*/storage.sqlite。")
                files.append((path, relative.as_posix(), kind))
    if len(files) > common.MAX_FILES or sum(path.stat().st_size for path, _, _ in files) > common.MAX_TOTAL_BYTES:
        raise BackupError("备份超过本工具支持的文件数或容量。")
    return files


def _validate_vectors(root, paths):
    vector_paths = {path for path in paths if path.startswith("qdrant/")}
    if not vector_paths:
        return
    try:
        metadata = root / "qdrant/meta.json"
        if metadata.stat().st_size > common.MAX_MANIFEST_BYTES:
            raise ValueError
        value = json.loads(metadata.read_bytes())
        collections, aliases = value["collections"], value.get("aliases", {})
        if not isinstance(collections, dict) or not isinstance(aliases, dict):
            raise ValueError
        if not all(_name(name) and isinstance(config, dict) for name, config in collections.items()):
            raise ValueError
        if not all(_name(alias) and isinstance(name, str) and name in collections for alias, name in aliases.items()):
            raise ValueError
        if vector_paths != {"qdrant/meta.json", *(f"qdrant/collection/{name}/storage.sqlite" for name in collections)}:
            raise ValueError
    except (OSError, ValueError, TypeError, KeyError, UnicodeError):
        raise BackupError("向量索引元数据无效、路径越界或集合文件不完整。") from None


def create_backup(database, vectors, archive, *, offline_confirm=False, allow_missing_vectors=False):
    if not offline_confirm:
        raise BackupError("请先停止所有写入者，再提供 --offline-confirm。")
    database, output = _path(database), _path(archive)
    vectors = _path(vectors) if vectors is not None else None
    common._regular(database)
    if vectors is not None:
        if (vectors.exists() and not vectors.is_dir()) or (not vectors.exists() and not allow_missing_vectors):
            raise BackupError("所选向量目录不存在或不是目录；没有索引时请明确使用 --without-vectors。")
        if database.is_relative_to(vectors):
            raise BackupError("业务数据库不能放在向量目录内。")
        if output.is_relative_to(vectors):
            raise BackupError("备份文件必须位于向量目录之外。")
    if output.exists():
        raise BackupError("备份文件已存在，拒绝覆盖。")
    if not output.parent.is_dir():
        raise BackupError("备份目标的父目录不存在。")
    with ExitStack() as stack:
        # Same canonical database-scoped lock as app.db.runtime_lock; never unlink it.
        stack.enter_context(common._lock(database.with_name(database.name + ".runtime.lock"), create=True))
        vector_state = "excluded" if vectors is None else "included" if vectors.exists() else "absent"
        if vector_state == "included":
            stack.enter_context(common._lock(vectors / ".lock", create=True))
        files = _collect(database, vectors)
        with tempfile.TemporaryDirectory(prefix="local-snapshot-") as temporary:
            staging, entries = Path(temporary), []
            for path, relative, kind in files:
                target = staging / relative
                _private_parents(staging, target)
                common._sqlite_copy(path, target) if kind == "sqlite" else shutil.copyfile(path, target)
                os.chmod(target, 0o600)
                entries.append({"path": relative, "kind": kind, "size": target.stat().st_size, "sha256": common._hash(target)})
            _validate_vectors(staging, {entry["path"] for entry in entries})
            if sum(entry["size"] for entry in entries) > common.MAX_TOTAL_BYTES:
                raise BackupError("一致性快照超过本工具支持的容量。")
            if vector_state == "absent" and vectors.exists():
                raise BackupError("备份期间向量目录发生变化，请确认所有写入者已经停止。")
            manifest = {"format": FORMAT, "version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
                        "vector_index": vector_state, "files": entries}
            manifest_bytes = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
            if len(manifest_bytes) > common.MAX_MANIFEST_BYTES:
                raise BackupError("备份清单超过大小限制。")
            descriptor, filename = tempfile.mkstemp(prefix=".local-backup-", suffix=".zip", dir=output.parent)
            os.close(descriptor)
            temporary_archive = Path(filename)
            try:
                with zipfile.ZipFile(temporary_archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
                    bundle.writestr("manifest.json", manifest_bytes)
                    for entry in entries:
                        bundle.write(staging / entry["path"], entry["path"])
                os.link(temporary_archive, output)
            finally:
                temporary_archive.unlink(missing_ok=True)
    return {"archive": str(output), "files": len(entries), "bytes": sum(entry["size"] for entry in entries),
            "vector_index": vector_state}


def _manifest(bundle):
    infos = bundle.infolist()
    names = [info.filename for info in infos]
    if len(infos) > common.MAX_FILES + 1 or len(set(names)) != len(names) or "manifest.json" not in names:
        raise BackupError("备份成员重复、缺失或过多。")
    if any(info.is_dir() or stat.S_IFMT(info.external_attr >> 16) not in {0, stat.S_IFREG} for info in infos):
        raise BackupError("备份包含符号链接或非普通文件。")
    info = bundle.getinfo("manifest.json")
    if info.file_size > common.MAX_MANIFEST_BYTES:
        raise BackupError("备份清单过大。")
    try:
        manifest = json.loads(bundle.read(info))
        entries = manifest["files"]
        if (manifest["format"] != FORMAT or type(manifest["version"]) is not int or manifest["version"] != 1
                or not isinstance(entries, list) or manifest["vector_index"] not in {"included", "absent", "excluded"}):
            raise ValueError
        paths = [entry["path"] for entry in entries]
        if len(set(paths)) != len(paths) or "demo.db" not in paths or set(names) != {"manifest.json", *paths}:
            raise ValueError
        if manifest["vector_index"] != "included" and paths != ["demo.db"]:
            raise ValueError
        for entry in entries:
            path = entry["path"]
            if (not _kind(path) or entry["kind"] != _kind(path) or _excluded(PurePosixPath(path))
                    or type(entry["size"]) is not int or entry["size"] < 0
                    or not re.fullmatch(r"[a-f0-9]{64}", entry["sha256"])
                    or bundle.getinfo(path).file_size != entry["size"]):
                raise ValueError
        if sum(entry["size"] for entry in entries) > common.MAX_TOTAL_BYTES:
            raise ValueError
    except (ValueError, TypeError, KeyError, UnicodeError):
        raise BackupError("备份清单无效、路径越界或容量超过限制。") from None
    return manifest


def restore_backup(archive, destination, *, offline_confirm=False):
    if not offline_confirm:
        raise BackupError("恢复前请停止所有写入者并提供 --offline-confirm。")
    archive, destination = _path(archive), _path(destination)
    common._regular(archive)
    if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
        raise BackupError("只允许恢复到不存在或全新的空目录，不覆盖现有数据。")
    if not destination.parent.is_dir():
        raise BackupError("恢复目录的父目录不存在。")
    staging = Path(tempfile.mkdtemp(prefix=".local-restore-", dir=destination.parent))
    try:
        with zipfile.ZipFile(archive) as bundle:
            manifest = _manifest(bundle)
            for entry in manifest["files"]:
                target = staging / entry["path"]
                _private_parents(staging, target)
                copied = 0
                with bundle.open(entry["path"]) as original, target.open("xb") as output:
                    os.chmod(target, 0o600)
                    while chunk := original.read(common.CHUNK):
                        copied += len(chunk)
                        if copied > entry["size"]:
                            raise BackupError("备份文件超出声明大小。")
                        output.write(chunk)
                if copied != entry["size"] or common._hash(target) != entry["sha256"]:
                    raise BackupError("备份哈希校验失败，数据未恢复。")
                if entry["kind"] == "sqlite":
                    common._sqlite_check(target)
            _validate_vectors(staging, {entry["path"] for entry in manifest["files"]})
            if manifest["vector_index"] == "included":
                (staging / "qdrant").mkdir(exist_ok=True, mode=0o700)
        # Do not remove the existing destination or anything another writer created.
        if destination.is_symlink() or (destination.exists() and (not destination.is_dir() or any(destination.iterdir()))):
            raise BackupError("恢复目标发生变化，已停止。")
        os.rename(staging, destination)
        return {"destination": str(destination), "files": len(manifest["files"]), "vector_index": manifest["vector_index"]}
    except (zipfile.BadZipFile, RuntimeError, EOFError, NotImplementedError, zlib.error):
        raise BackupError("备份压缩包损坏或格式不受支持。") from None
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    backup = commands.add_parser("backup")
    source = backup.add_mutually_exclusive_group(required=True)
    source.add_argument("--data-dir", help="明确选择包含 demo.db 和可选 qdrant/ 的本地数据目录")
    source.add_argument("--database", help="明确选择自定义 SQLite 文件，不接受数据库 URL")
    indexes = backup.add_mutually_exclusive_group()
    indexes.add_argument("--vectors", help="自定义 Qdrant 目录；与 --database 配合使用")
    indexes.add_argument("--without-vectors", action="store_true", help="明确仅备份数据库，不保留向量索引")
    backup.add_argument("--output", required=True)
    backup.add_argument("--offline-confirm", action="store_true")
    restore = commands.add_parser("restore")
    restore.add_argument("--archive", required=True)
    restore.add_argument("--destination", required=True)
    restore.add_argument("--offline-confirm", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "backup":
            if args.data_dir and args.vectors:
                raise BackupError("自定义索引路径请同时使用 --database 和 --vectors。")
            if args.database and not (args.vectors or args.without_vectors):
                raise BackupError("自定义数据库必须同时明确 --vectors 或 --without-vectors。")
            database = Path(args.data_dir) / "demo.db" if args.data_dir else args.database
            vectors = None if args.without_vectors else Path(args.data_dir) / "qdrant" if args.data_dir else args.vectors
            result = create_backup(database, vectors, args.output, offline_confirm=args.offline_confirm,
                                   allow_missing_vectors=bool(args.data_dir))
        else:
            result = restore_backup(args.archive, args.destination, offline_confirm=args.offline_confirm)
    except (BackupError, OSError) as exc:
        parser.exit(2, f"备份/恢复未完成：{str(exc) if isinstance(exc, BackupError) else '文件系统操作失败，请检查目录权限。'}\n")
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
