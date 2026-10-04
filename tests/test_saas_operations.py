"""Real offline snapshots, hostile archives and isolated operator CLI tests."""
from datetime import datetime, timezone
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import subprocess
import sys
import zipfile

import pytest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("saas_backup_tool", ROOT / "scripts/saas_backup.py")
backup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backup)
ORG = "a" * 32


def sqlite_file(path, marker="synthetic-data"):
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE payload (value TEXT)")
        db.execute("INSERT INTO payload VALUES (?)", (marker,))


@pytest.fixture
def data(tmp_path):
    root = tmp_path / "saas"
    sqlite_file(root / "control.db", "synthetic-control")
    sqlite_file(root / "tenants" / ORG / "content.db", "synthetic-tenant")
    vectors = root / "tenants" / ORG / "vectors"
    sqlite_file(vectors / "collection" / "storage.sqlite", "synthetic-vector")
    (vectors / "meta.json").write_text('{"synthetic": true}')
    (vectors / ".lock").touch()
    (vectors / ".env").write_text("KEY=excluded-synthetic")
    (vectors / "excluded.safetensors").write_bytes(b"excluded model weights")
    (root / "models").mkdir()
    (root / "models" / "model.bin").write_bytes(b"excluded model cache")
    (root / ".env.saas").write_text("KEY=excluded-synthetic")
    return root


def test_backup_restore_roundtrip_hashes_sqlite_and_vectors(data, tmp_path):
    archive, restored = tmp_path / "snapshot.zip", tmp_path / "restored"
    result = backup.create_backup(data, archive, offline_confirm=True)
    assert result["files"] == 4
    assert stat.S_IMODE(archive.stat().st_mode) == 0o600
    with zipfile.ZipFile(archive) as bundle:
        assert not any(".env" in name or "safetensors" in name or "model.bin" in name or ".lock" in name for name in bundle.namelist())
        manifest = json.loads(bundle.read("manifest.json"))
        for entry in manifest["files"]:
            assert hashlib.sha256(bundle.read(entry["path"])).hexdigest() == entry["sha256"]
    backup.restore_backup(archive, restored, offline_confirm=True)
    for path, marker in [("control.db", "synthetic-control"),
                         (f"tenants/{ORG}/content.db", "synthetic-tenant"),
                         (f"tenants/{ORG}/vectors/collection/storage.sqlite", "synthetic-vector")]:
        with sqlite3.connect(restored / path) as db:
            assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
            assert db.execute("SELECT value FROM payload").fetchone() == (marker,)
    assert (restored / f"tenants/{ORG}/vectors/meta.json").read_text() == '{"synthetic": true}'


def test_sqlite_snapshot_includes_committed_wal_records(data, tmp_path):
    database = sqlite3.connect(data / "control.db")
    try:
        database.execute("PRAGMA journal_mode=WAL")
        database.execute("INSERT INTO payload VALUES ('committed-wal-record')")
        database.commit()
        assert Path(str(data / "control.db") + "-wal").exists()
        backup.create_backup(data, tmp_path / "wal.zip", offline_confirm=True)
    finally:
        database.close()
    backup.restore_backup(tmp_path / "wal.zip", tmp_path / "wal-restored", offline_confirm=True)
    with sqlite3.connect(tmp_path / "wal-restored/control.db") as restored:
        assert restored.execute("SELECT count(*) FROM payload").fetchone() == (2,)


def test_offline_confirmation_and_active_service_lock_are_required(data, tmp_path):
    with pytest.raises(backup.BackupError, match="offline-confirm"):
        backup.create_backup(data, tmp_path / "no.zip")
    with (data / ".service.lock").open("a+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(backup.BackupError, match="活跃"):
            backup.create_backup(data, tmp_path / "locked.zip", offline_confirm=True)
    assert not (tmp_path / "locked.zip").exists()


def test_active_qdrant_lock_is_not_bypassed_by_confirmation(data, tmp_path):
    with (data / f"tenants/{ORG}/vectors/.lock").open("a+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(backup.BackupError, match="活跃"):
            backup.create_backup(data, tmp_path / "vector-locked.zip", offline_confirm=True)


def test_symlinks_in_data_or_sqlite_sidecars_are_rejected(data, tmp_path):
    secret = tmp_path / "outside"
    secret.write_text("outside")
    link = data / f"tenants/{ORG}/vectors/outside"
    link.symlink_to(secret)
    with pytest.raises(backup.BackupError, match="符号链接"):
        backup.create_backup(data, tmp_path / "symlink.zip", offline_confirm=True)
    link.unlink()
    Path(str(data / "control.db") + "-wal").symlink_to(secret)
    with pytest.raises(backup.BackupError, match="符号链接"):
        backup.create_backup(data, tmp_path / "wal-symlink.zip", offline_confirm=True)


def crafted_archive(path, files, *, symlink=None, change_hash=False):
    entries = [{"path": name, "kind": "sqlite" if name == "control.db" else "vector",
                "size": len(content), "sha256": hashlib.sha256(content).hexdigest()} for name, content in files.items()]
    if change_hash:
        entries[0]["sha256"] = "0" * 64
    manifest = {"format": "zhiyuan-saas-backup", "version": 1, "files": entries}
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


@pytest.mark.parametrize("name", ["../escape", "/absolute", f"tenants/{ORG}/vectors/../../escape",
                                  f"tenants/{ORG}/vectors/back\\slash", ".env", "tenants/wrong/content.db",
                                  f"tenants/{ORG}/vectors/storage.sqlite-wal"])
def test_restore_rejects_traversal_and_unlisted_scope(data, tmp_path, name):
    archive = tmp_path / "hostile.zip"
    crafted_archive(archive, {"control.db": (data / "control.db").read_bytes(), name: b"bad"})
    with pytest.raises(backup.BackupError):
        backup.restore_backup(archive, tmp_path / "bad-target", offline_confirm=True)
    assert not (tmp_path / "bad-target").exists()
    assert not (tmp_path / "escape").exists()


def test_restore_rejects_symlink_hash_mismatch_and_corrupt_sqlite(data, tmp_path):
    archive = tmp_path / "bad.zip"
    vector = f"tenants/{ORG}/vectors/link"
    crafted_archive(archive, {"control.db": (data / "control.db").read_bytes(), vector: b"outside"}, symlink=vector)
    with pytest.raises(backup.BackupError, match="符号链接"):
        backup.restore_backup(archive, tmp_path / "bad-target", offline_confirm=True)
    crafted_archive(archive, {"control.db": (data / "control.db").read_bytes()}, change_hash=True)
    with pytest.raises(backup.BackupError, match="哈希"):
        backup.restore_backup(archive, tmp_path / "bad-target", offline_confirm=True)
    crafted_archive(archive, {"control.db": b"synthetic not a SQLite database"})
    with pytest.raises(backup.BackupError, match="SQLite"):
        backup.restore_backup(archive, tmp_path / "bad-target", offline_confirm=True)
    assert not (tmp_path / "bad-target").exists()


def test_restore_never_overwrites_data_or_existing_backup(data, tmp_path):
    archive = tmp_path / "snapshot.zip"
    backup.create_backup(data, archive, offline_confirm=True)
    with pytest.raises(backup.BackupError, match="拒绝覆盖"):
        backup.create_backup(data, archive, offline_confirm=True)
    with pytest.raises(backup.BackupError, match="offline-confirm"):
        backup.restore_backup(archive, tmp_path / "target")
    with pytest.raises(backup.BackupError, match="空目录"):
        backup.restore_backup(archive, data, offline_confirm=True)
    destination = tmp_path / "empty"
    destination.mkdir()
    backup.restore_backup(archive, destination, offline_confirm=True)
    assert (destination / "control.db").exists()


@pytest.fixture
def admin_data(tmp_path, monkeypatch):
    from app.saas import store
    from app.saas.models import Organization
    directory = tmp_path / "admin-data"
    monkeypatch.setenv("SAAS_DATA_DIR", str(directory))
    store.init_control_db()
    with store.session_factory() as db:
        db.add(Organization(id=ORG, name="Synthetic Organization", created_at=datetime.now(timezone.utc)))
        db.commit()
    yield directory
    store.close_control_db()


def admin(directory, *arguments):
    env = {**os.environ, "SAAS_ENV_FILE": str(directory / "nonexistent.env")}
    return subprocess.run([sys.executable, str(ROOT / "scripts/saas_admin.py"), "--data-dir", str(directory), *arguments],
                          env=env, text=True, capture_output=True, timeout=15)


def test_admin_lists_existing_org_and_applies_explicit_plan_without_payment(admin_data):
    listing = admin(admin_data, "list-orgs")
    assert listing.returncode == 0, listing.stderr
    assert json.loads(listing.stdout)[0]["plan"] == "trial"
    changed = admin(admin_data, "set-plan", "--organization", ORG, "--plan", "team",
                    "--ai-requests", "250", "--documents", "400", "--members", "5")
    assert changed.returncode == 0, changed.stderr
    assert json.loads(changed.stdout)["limits"]["ai_requests"] == 250
    assert "未执行支付" in changed.stdout
    assert json.loads(admin(admin_data, "list-orgs").stdout)[0]["plan"] == "team"
    assert admin(admin_data, "set-plan", "--organization", ORG, "--plan", "team").returncode != 0
    assert admin(admin_data, "set-plan", "--organization", "b" * 32, "--plan", "trial").returncode != 0
    assert json.loads(admin(admin_data, "reserved-usage", "--organization", ORG).stdout)["reservations"] == []


def test_admin_does_not_create_missing_database(tmp_path):
    target = tmp_path / "missing"
    assert admin(target, "list-orgs").returncode != 0
    assert not target.exists()


@pytest.fixture
def launcher(tmp_path):
    project = tmp_path / "launcher"
    (project / "scripts").mkdir(parents=True)
    shutil.copy(ROOT / "scripts/start_saas.sh", project / "scripts/start_saas.sh")
    (project / "frontend/dist").mkdir(parents=True)
    (project / "frontend/dist/index.html").write_text("synthetic build")
    (project / "backend").mkdir()
    (project / "backend/uvicorn.py").write_text('''import fcntl,json,os,sys
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path.cwd()/'.env')
handle=open(Path(os.environ['SAAS_DATA_DIR'])/'.service.lock','a+')
locked=False
try: fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
except BlockingIOError: locked=True
print(json.dumps({'args':sys.argv[1:],'mode':os.environ['SAAS_MODE'],'url':os.environ['DATABASE_URL'],'origin':os.environ['SAAS_PUBLIC_ORIGIN'],'secure':os.environ['SAAS_SECURE_COOKIE'],'data':os.environ['SAAS_DATA_DIR'],'locked':locked,'old_env_loaded':'SYNTHETIC_OLD_ENV' in os.environ}))
''')
    (project / ".env").write_text("SYNTHETIC_OLD_ENV=must-not-load")
    return project


def launch(project, **overrides):
    env = {key: value for key, value in os.environ.items() if not key.startswith("SAAS_")}
    env.update({"SAAS_PYTHON": sys.executable, "SAAS_ENV_FILE": str(project / ".env.saas"), **overrides})
    return subprocess.run(["bash", str(project / "scripts/start_saas.sh")], env=env, text=True,
                          capture_output=True, timeout=15)


def test_launcher_isolated_defaults_loopback_single_worker_delegates_lock_to_lifespan(launcher):
    result = launch(launcher, SAAS_MODE="false", DATABASE_URL="sqlite:///do-not-use-demo.db")
    assert result.returncode == 0, result.stderr
    state = json.loads(result.stdout.splitlines()[-1])
    assert state["mode"] == "true" and state["secure"] == "false"
    assert state["origin"] == "http://127.0.0.1:8766"
    assert state["args"] == ["app.main:app", "--host", "127.0.0.1", "--port", "8766", "--workers", "1"]
    assert not state["locked"] and not state["old_env_loaded"]
    assert state["url"].endswith("/.data/saas/legacy-disabled.db")


def test_launcher_requires_frontend_and_secure_public_origin(launcher):
    assert launch(launcher, SAAS_PUBLIC_ORIGIN="http://public.example.invalid").returncode != 0
    assert launch(launcher, SAAS_PUBLIC_ORIGIN="https://public.example.invalid", SAAS_SECURE_COOKIE="false").returncode != 0
    (launcher / "frontend/dist/index.html").unlink()
    result = launch(launcher)
    assert result.returncode != 0 and "前端构建" in result.stderr
