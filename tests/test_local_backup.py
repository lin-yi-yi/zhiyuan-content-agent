"""Private offline snapshots use only temporary synthetic databases and vector stores."""
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys
import zipfile

import pytest
from qdrant_client import QdrantClient, models


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("local_backup_tool", ROOT / "scripts/local_backup.py")
backup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(backup)


def sqlite_file(path, marker="synthetic-private-content"):
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE payload (value TEXT)")
        db.execute("INSERT INTO payload VALUES (?)", (marker,))
    return path


@pytest.fixture
def data(tmp_path):
    root = tmp_path / "source"
    sqlite_file(root / "demo.db")
    client = QdrantClient(path=str(root / "qdrant"))
    client.create_collection("synthetic", vectors_config=models.VectorParams(size=2, distance=models.Distance.COSINE))
    client.upsert("synthetic", [models.PointStruct(id=1, vector=[1.0, 0.0], payload={"text": "synthetic-private-vector"})])
    client.close()
    return root


def snapshot(data, tmp_path, name="snapshot.zip"):
    archive = tmp_path / name
    backup.create_backup(data / "demo.db", data / "qdrant", archive, offline_confirm=True)
    return archive


def test_real_sqlite_and_qdrant_roundtrip_are_private_and_queryable(data, tmp_path):
    archive = snapshot(data, tmp_path)
    assert stat.S_IMODE(archive.stat().st_mode) == 0o600
    with zipfile.ZipFile(archive) as bundle:
        manifest = json.loads(bundle.read("manifest.json"))
        assert manifest["vector_index"] == "included"
        assert {entry["path"] for entry in manifest["files"]} == {
            "demo.db", "qdrant/meta.json", "qdrant/collection/synthetic/storage.sqlite"}
        assert str(data) not in bundle.read("manifest.json").decode()
        for entry in manifest["files"]:
            assert hashlib.sha256(bundle.read(entry["path"])).hexdigest() == entry["sha256"]
    restored = tmp_path / "restored"
    result = backup.restore_backup(archive, restored, offline_confirm=True)
    assert result["files"] == 3
    assert stat.S_IMODE(restored.stat().st_mode) == 0o700
    for path in restored.rglob("*"):
        assert stat.S_IMODE(path.stat().st_mode) == (0o700 if path.is_dir() else 0o600)
    with sqlite3.connect(restored / "demo.db") as db:
        assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert db.execute("SELECT value FROM payload").fetchone() == ("synthetic-private-content",)
    client = QdrantClient(path=str(restored / "qdrant"))
    try:
        hits = client.query_points("synthetic", query=[1.0, 0.0]).points
        assert hits[0].payload == {"text": "synthetic-private-vector"}
    finally:
        client.close()


@pytest.mark.parametrize("relative", ["demo.db", "qdrant/collection/synthetic/storage.sqlite"])
def test_committed_wal_records_are_snapshotted_not_sidecars(data, tmp_path, relative):
    database = sqlite3.connect(data / relative)
    try:
        database.execute("PRAGMA journal_mode=WAL")
        database.execute("CREATE TABLE wal_marker (value TEXT)")
        database.execute("INSERT INTO wal_marker VALUES ('synthetic committed WAL')")
        database.commit()
        assert Path(str(data / relative) + "-wal").exists()
        archive = snapshot(data, tmp_path)
    finally:
        database.close()
    with zipfile.ZipFile(archive) as bundle:
        assert not any(name.endswith(("-wal", "-shm", "-journal")) for name in bundle.namelist())
    restored = tmp_path / "restored"
    backup.restore_backup(archive, restored, offline_confirm=True)
    with sqlite3.connect(restored / relative) as db:
        assert db.execute("SELECT value FROM wal_marker").fetchone() == ("synthetic committed WAL",)


def test_active_application_lock_and_index_lock_cannot_be_overridden(data, tmp_path):
    from app.db.runtime_lock import local_database_runtime_lock
    with local_database_runtime_lock(f"sqlite:///{data / 'demo.db'}"):
        with pytest.raises(backup.BackupError, match="活跃"):
            snapshot(data, tmp_path)
    client = QdrantClient(path=str(data / "qdrant"))
    try:
        with pytest.raises(backup.BackupError, match="活跃"):
            snapshot(data, tmp_path)
    finally:
        client.close()
    assert not (tmp_path / "snapshot.zip").exists()
    snapshot(data, tmp_path)


def test_backup_holds_locks_through_publication_and_releases_after_error(data, tmp_path, monkeypatch):
    original_copy = backup.common._sqlite_copy

    def check_locks(source, target):
        for path in (data / "demo.db.runtime.lock", data / "qdrant/.lock"):
            with path.open("a+") as handle:
                with pytest.raises(BlockingIOError):
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        raise backup.BackupError("synthetic interrupted snapshot")

    monkeypatch.setattr(backup.common, "_sqlite_copy", check_locks)
    with pytest.raises(backup.BackupError, match="interrupted"):
        snapshot(data, tmp_path)
    assert not (tmp_path / "snapshot.zip").exists()
    monkeypatch.setattr(backup.common, "_sqlite_copy", original_copy)
    snapshot(data, tmp_path)


def test_environment_keys_caches_and_locks_are_not_archived(data, tmp_path):
    for relative in (".env", ".env.local", "secret.pem", "private.key", "id_rsa", "credentials.json",
                     "models/weights.bin", ".cache/arbitrary", "cache/nested/arbitrary", "secrets/key.txt",
                     "qdrant.runtime.lock", "weights.safetensors", "model.onnx"):
        path = data / "qdrant" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("synthetic-never-archive-secret")
    (data / ".env").write_text("synthetic-private-configuration")
    (data / "models").mkdir()
    (data / "models/weights.bin").write_bytes(b"synthetic outside index")
    archive = snapshot(data, tmp_path)
    with zipfile.ZipFile(archive) as bundle:
        assert len(bundle.namelist()) == 4
        assert not any(b"synthetic-never-archive-secret" in bundle.read(name) for name in bundle.namelist())


@pytest.mark.parametrize("relative", ["demo.db", "demo.db-wal", "qdrant", "qdrant/meta.json", "qdrant/collection/synthetic",
                                      "qdrant/ignored.env", "demo.db.runtime.lock", "qdrant/.lock"])
def test_symlinks_are_rejected_even_for_excluded_files(data, tmp_path, relative):
    path = data / relative
    if path.exists():
        original = tmp_path / "original"
        path.rename(original)
    else:
        original = tmp_path / "outside"
        original.write_text("synthetic private file")
    path.symlink_to(original)
    with pytest.raises(backup.BackupError, match="符号链接"):
        snapshot(data, tmp_path)
    assert not (tmp_path / "snapshot.zip").exists()


def test_unknown_vector_files_are_rejected_instead_of_silently_lost(data, tmp_path):
    (data / "qdrant/unrecognized.dat").write_bytes(b"synthetic")
    with pytest.raises(backup.BackupError, match="不支持"):
        snapshot(data, tmp_path)


def crafted_archive(path, files, *, mutate=None, symlink=None, duplicate=None, extra=None):
    entries = [{"path": name, "kind": backup._kind(name) or "vector", "size": len(content),
                "sha256": hashlib.sha256(content).hexdigest()} for name, content in files.items()]
    manifest = {"format": backup.FORMAT, "version": 1, "vector_index": "included", "files": entries}
    if mutate:
        mutate(manifest)
    with zipfile.ZipFile(path, "w") as bundle:
        bundle.writestr("manifest.json", json.dumps(manifest))
        for name, content in files.items():
            if name == symlink:
                info = zipfile.ZipInfo(name)
                info.create_system = 3
                info.external_attr = (stat.S_IFLNK | 0o777) << 16
                bundle.writestr(info, content)
            else:
                bundle.writestr(name, content)
        if extra:
            bundle.writestr(extra, b"synthetic unlisted")
        if duplicate:
            with pytest.warns(UserWarning, match="Duplicate"):
                bundle.writestr(duplicate, files[duplicate])


@pytest.mark.parametrize("name", ["../escape", "/absolute", "qdrant/../escape", "qdrant/back\\slash", "qdrant//meta.json",
                                  "qdrant/collection/../storage.sqlite", "qdrant/collection/C:/storage.sqlite",
                                  "qdrant/.env", "qdrant/private.key", "qdrant/.lock", "qdrant/models/model.bin",
                                  "demo.db-wal", "control.db", "qdrant/collection/a/storage.sqlite-wal"])
def test_restore_rejects_path_traversal_and_out_of_scope_members(data, tmp_path, name):
    archive = tmp_path / "hostile.zip"
    crafted_archive(archive, {"demo.db": (data / "demo.db").read_bytes(), name: b"synthetic hostile"})
    target = tmp_path / "restore"
    with pytest.raises(backup.BackupError):
        backup.restore_backup(archive, target, offline_confirm=True)
    assert not target.exists()
    assert not (tmp_path / "escape").exists()
    assert not list(tmp_path.glob(".local-restore-*"))


@pytest.mark.parametrize("fault", ["symlink", "duplicate", "hash", "sqlite", "kind", "size", "manifest", "extra", "collection_path"])
def test_restore_rejects_corruption_and_keeps_preexisting_empty_target(data, tmp_path, fault):
    archive = tmp_path / "bad.zip"
    files = {"demo.db": (data / "demo.db").read_bytes()}
    kwargs = {}
    if fault in {"symlink", "duplicate"}:
        kwargs[fault] = "demo.db"
    elif fault == "hash":
        kwargs["mutate"] = lambda value: value["files"][0].update(sha256="0" * 64)
    elif fault == "sqlite":
        files["demo.db"] = b"synthetic not SQLite"
    elif fault == "kind":
        kwargs["mutate"] = lambda value: value["files"][0].update(kind="vector")
    elif fault == "size":
        kwargs["mutate"] = lambda value: value["files"][0].update(size=-1)
    elif fault == "manifest":
        kwargs["mutate"] = lambda value: value.update(files=[[], 1])
    elif fault == "extra":
        kwargs["extra"] = "unlisted"
    else:
        files["qdrant/meta.json"] = json.dumps({"collections": {"../../escape": {}}, "aliases": {}}).encode()
    crafted_archive(archive, files, **kwargs)
    target = tmp_path / "empty"
    target.mkdir()
    inode = target.stat().st_ino
    with pytest.raises(backup.BackupError):
        backup.restore_backup(archive, target, offline_confirm=True)
    assert target.stat().st_ino == inode
    assert list(target.iterdir()) == []
    assert not list(tmp_path.glob(".local-restore-*"))


def test_restore_will_not_clobber_existing_or_concurrently_created_data(data, tmp_path, monkeypatch):
    archive = snapshot(data, tmp_path)
    with pytest.raises(backup.BackupError, match="空目录"):
        backup.restore_backup(archive, data, offline_confirm=True)
    target = tmp_path / "new"
    original = backup.common._sqlite_check

    def concurrent_writer(path):
        target.mkdir(exist_ok=True)
        (target / "keep.txt").write_text("synthetic concurrently created data")
        return original(path)

    monkeypatch.setattr(backup.common, "_sqlite_check", concurrent_writer)
    with pytest.raises(backup.BackupError, match="目标发生变化"):
        backup.restore_backup(archive, target, offline_confirm=True)
    assert (target / "keep.txt").read_text() == "synthetic concurrently created data"
    assert list(target.iterdir()) == [target / "keep.txt"]


def test_output_protection_confirmation_and_empty_directory_restore(data, tmp_path):
    with pytest.raises(backup.BackupError, match="offline-confirm"):
        backup.create_backup(data / "demo.db", data / "qdrant", tmp_path / "backup.zip")
    with pytest.raises(backup.BackupError, match="目录之外"):
        backup.create_backup(data / "demo.db", data / "qdrant", data / "qdrant/backup.zip", offline_confirm=True)
    archive = snapshot(data, tmp_path)
    digest = backup.common._hash(archive)
    with pytest.raises(backup.BackupError, match="拒绝覆盖"):
        snapshot(data, tmp_path)
    assert backup.common._hash(archive) == digest
    target = tmp_path / "empty"
    target.mkdir()
    with pytest.raises(backup.BackupError, match="offline-confirm"):
        backup.restore_backup(archive, target)
    backup.restore_backup(archive, target, offline_confirm=True)
    assert (target / "demo.db").is_file()


def cli(*args, env=None, cwd=ROOT):
    return subprocess.run([sys.executable, str(ROOT / "scripts/local_backup.py"), *map(str, args)],
                          cwd=cwd, env=env, text=True, capture_output=True, timeout=30)


def test_cli_requires_explicit_sources_and_never_reads_dotenv_or_environment_defaults(tmp_path):
    working = tmp_path / "working"
    custom = sqlite_file(working / "custom.sqlite", "synthetic selected custom database")
    poison = tmp_path / "must-not-create.db"
    (working / ".env").write_text(f"DATABASE_URL=sqlite:///{poison}\nRAG_VECTOR_PATH={tmp_path / 'no-index'}\n")
    env = {**os.environ, "DATABASE_URL": f"sqlite:///{poison}", "RAG_VECTOR_PATH": str(tmp_path / "no-index"), "SAAS_MODE": "true"}
    archive = tmp_path / "custom.zip"
    missing = cli("backup", "--output", archive, "--offline-confirm", env=env, cwd=working)
    assert missing.returncode == 2
    ambiguous = cli("backup", "--database", custom, "--output", archive, "--offline-confirm", env=env, cwd=working)
    assert ambiguous.returncode == 2 and "--vectors" in ambiguous.stderr
    result = cli("backup", "--database", custom, "--without-vectors", "--output", archive, "--offline-confirm", env=env, cwd=working)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["vector_index"] == "excluded"
    assert "synthetic selected custom database" not in result.stdout + result.stderr
    assert not poison.exists() and not (tmp_path / "no-index").exists()
    target = tmp_path / "custom-restored"
    restored = cli("restore", "--archive", archive, "--destination", target, "--offline-confirm", env=env)
    assert restored.returncode == 0, restored.stderr
    with sqlite3.connect(target / "demo.db") as db:
        assert db.execute("SELECT value FROM payload").fetchone() == ("synthetic selected custom database",)


def test_default_layout_without_index_is_reported_and_custom_missing_index_fails(tmp_path):
    data = tmp_path / "source"
    database = sqlite_file(data / "demo.db")
    archive = tmp_path / "database-only.zip"
    result = cli("backup", "--data-dir", data, "--output", archive, "--offline-confirm")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["vector_index"] == "absent"
    assert not (data / "qdrant").exists()
    result = cli("backup", "--database", database, "--vectors", data / "typo", "--output", tmp_path / "bad.zip", "--offline-confirm")
    assert result.returncode == 2
    assert not (tmp_path / "bad.zip").exists()


def test_cli_obeys_live_application_lock_in_another_process(data, tmp_path):
    from app.db.runtime_lock import local_database_runtime_lock
    archive = tmp_path / "cli-locked.zip"
    with local_database_runtime_lock(f"sqlite:///{data / 'demo.db'}"):
        result = cli("backup", "--data-dir", data, "--output", archive, "--offline-confirm")
        assert result.returncode == 2 and "活跃" in result.stderr
        assert "synthetic-private-content" not in result.stdout + result.stderr
        assert not archive.exists()
    result = cli("backup", "--data-dir", data, "--output", archive, "--offline-confirm")
    assert result.returncode == 0, result.stderr


def test_custom_database_and_separate_index_paths_restore_to_documented_layout(data, tmp_path):
    database = data / "custom.sqlite"
    (data / "demo.db").rename(database)
    sqlite_file(data / "demo.db", "synthetic unselected default database")
    vectors = tmp_path / "custom-index"
    (data / "qdrant").rename(vectors)
    archive = tmp_path / "custom-layout.zip"
    result = cli("backup", "--database", database, "--vectors", vectors, "--output", archive, "--offline-confirm")
    assert result.returncode == 0, result.stderr
    target = tmp_path / "custom-restored"
    backup.restore_backup(archive, target, offline_confirm=True)
    with sqlite3.connect(target / "demo.db") as db:
        assert db.execute("SELECT value FROM payload").fetchone() == ("synthetic-private-content",)
    client = QdrantClient(path=str(target / "qdrant"))
    try:
        assert client.query_points("synthetic", query=[1.0, 0.0]).points[0].payload == {"text": "synthetic-private-vector"}
    finally:
        client.close()


def test_limits_and_invalid_zip_fail_without_partial_restore(data, tmp_path, monkeypatch):
    archive = snapshot(data, tmp_path)
    monkeypatch.setattr(backup.common, "MAX_TOTAL_BYTES", 1)
    with pytest.raises(backup.BackupError, match="容量"):
        snapshot(data, tmp_path, "too-big.zip")
    with pytest.raises(backup.BackupError, match="容量"):
        backup.restore_backup(archive, tmp_path / "restored", offline_confirm=True)
    archive.write_bytes(b"not a zip archive")
    with pytest.raises(backup.BackupError, match="损坏"):
        backup.restore_backup(archive, tmp_path / "restored", offline_confirm=True)
    assert not (tmp_path / "restored").exists()
