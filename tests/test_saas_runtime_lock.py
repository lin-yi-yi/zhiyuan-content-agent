"""SaaS lifecycle ownership: real temporary stores, flock and uvicorn processes.

No user stores, dotenv files, paid models or public network are used.
"""
import asyncio
import hashlib
import os
from pathlib import Path
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from app.db.runtime_lock import DatabaseRuntimeLockError, saas_data_runtime_lock

from test_runtime_lock import _free_port, _ready, _stop
from test_saas_operations import backup


@pytest.fixture
def saas_environment(monkeypatch, tmp_path):
    data = tmp_path / "synthetic-private-saas"
    monkeypatch.setenv("SAAS_MODE", "true")
    monkeypatch.setenv("SAAS_PUBLIC_ORIGIN", "http://testserver")
    monkeypatch.setenv("SAAS_SECURE_COOKIE", "false")
    monkeypatch.setenv("SAAS_DATA_DIR", str(data))
    monkeypatch.setenv("RAG_RETRIEVAL_MODE", "lexical")
    from app.agent_core.vector_store import close_vector_stores
    from app.db.session import close_tenant_stores
    from app.saas.store import close_control_db
    close_vector_stores()
    close_tenant_stores()
    close_control_db()
    yield data
    close_vector_stores()
    close_tenant_stores()
    close_control_db()


def assert_locked(data):
    with pytest.raises(DatabaseRuntimeLockError, match="已有服务或备份任务运行"):
        with saas_data_runtime_lock(data):
            pytest.fail("Another owner must not enter the data lifetime")


def test_root_aliases_share_lock_and_release_keeps_same_inode(tmp_path, monkeypatch):
    data = tmp_path / "store"
    alias = tmp_path / "alias"
    with saas_data_runtime_lock(data):
        alias.symlink_to(data, target_is_directory=True)
        monkeypatch.chdir(tmp_path)
        assert_locked("store")
        assert_locked(alias)
        lock = data / ".service.lock"
        inode = lock.stat().st_ino
    with saas_data_runtime_lock(data):
        assert lock.stat().st_ino == inode
    assert lock.is_file()


@pytest.mark.parametrize("kind", ["symlink", "directory", "fifo", "root_file"])
def test_invalid_lock_fails_before_control_creation_without_exposing_path(tmp_path, kind):
    data = tmp_path / "private-lock-root"
    private = tmp_path / "private-file"
    private.write_text("synthetic keep", encoding="utf-8")
    if kind == "root_file":
        data.write_text("synthetic keep", encoding="utf-8")
    else:
        data.mkdir()
        lock = data / ".service.lock"
        if kind == "symlink":
            lock.symlink_to(private)
        elif kind == "directory":
            lock.mkdir()
        else:
            os.mkfifo(lock)
    with pytest.raises(DatabaseRuntimeLockError, match="无法取得 SaaS 运行锁") as error:
        with saas_data_runtime_lock(data):
            pytest.fail("Invalid lock must stop startup")
    assert "private" not in str(error.value) and str(tmp_path) not in str(error.value)
    assert private.read_text(encoding="utf-8") == "synthetic keep"
    assert not (data / "control.db").exists()


def test_second_testclient_lifespan_rejects_before_initialization_or_owner_cleanup(saas_environment, monkeypatch):
    from app import main
    from app.saas import store
    data = saas_environment
    real_init, real_close = store.init_control_db, store.close_control_db
    events = []

    def initialize():
        events.append("init")
        real_init()

    def close():
        events.append("close")
        real_close()

    monkeypatch.setattr(store, "init_control_db", initialize)
    monkeypatch.setattr(store, "close_control_db", close)
    with TestClient(main.app) as owner:
        engine = store.get_control_engine()
        assert owner.get("/api/health").status_code == 200
        with pytest.raises(DatabaseRuntimeLockError, match="已有服务或备份任务运行"):
            with TestClient(main.app):
                pytest.fail("Second lifespan must never initialize stores")
        assert events == ["init"]
        assert store.get_control_engine() is engine
        assert_locked(data)
        assert owner.get("/api/health").status_code == 200
    assert events == ["init", "close"]
    with TestClient(main.app):
        assert events == ["init", "close", "init"]
    assert (data / ".service.lock").is_file()


def test_active_lifespan_blocks_backup_without_any_vector_index(saas_environment, tmp_path):
    from app import main
    data = saas_environment
    archive = tmp_path / "checkpoint.zip"
    with TestClient(main.app):
        assert (data / "control.db").is_file()
        assert not (data / "tenants").exists()
        with pytest.raises(backup.BackupError, match="活跃服务或向量索引锁"):
            backup.create_backup(data, archive, offline_confirm=True)
        assert not archive.exists()
    result = backup.create_backup(data, archive, offline_confirm=True)
    assert result["files"] == 1 and archive.is_file()


@pytest.mark.parametrize("failure", ["none", "initialization", "vectors"])
def test_every_cleanup_runs_before_unlock_even_after_partial_startup(saas_environment, monkeypatch, failure):
    from app import main
    from app.agent_core import vector_store
    from app.db import session
    from app.saas import store
    data = saas_environment
    real_init = store.init_control_db
    calls = []

    def initialize():
        assert_locked(data)
        real_init()
        if failure == "initialization":
            raise RuntimeError("synthetic initialization failure")

    def cleanup(name, real):
        def run():
            assert_locked(data)
            calls.append(name)
            real()
            if failure == name:
                raise RuntimeError("synthetic cleanup failure")
        return run

    monkeypatch.setattr(store, "init_control_db", initialize)
    monkeypatch.setattr(vector_store, "close_vector_stores", cleanup("vectors", vector_store.close_vector_stores))
    monkeypatch.setattr(session, "close_tenant_stores", cleanup("tenants", session.close_tenant_stores))
    monkeypatch.setattr(store, "close_control_db", cleanup("control", store.close_control_db))

    async def scenario():
        async with main.lifespan(main.app):
            assert failure != "initialization"
            assert_locked(data)

    if failure == "none":
        asyncio.run(scenario())
    else:
        with pytest.raises(RuntimeError, match="synthetic"):
            asyncio.run(scenario())
    assert calls == ["vectors", "tenants", "control"]
    with saas_data_runtime_lock(data):
        pass


def test_invalid_configuration_releases_lock_without_initializing_control(saas_environment, monkeypatch):
    from app import main
    monkeypatch.setenv("SAAS_PUBLIC_ORIGIN", "invalid")

    async def scenario():
        async with main.lifespan(main.app):
            pytest.fail("Invalid public origin must prevent startup")

    with pytest.raises(RuntimeError, match="SAAS_PUBLIC_ORIGIN"):
        asyncio.run(scenario())
    assert not (saas_environment / "control.db").exists()
    with saas_data_runtime_lock(saas_environment):
        pass


def _start(data, port, working_directory):
    env = {**os.environ, "SAAS_MODE": "true", "SAAS_DATA_DIR": str(data),
           "SAAS_PUBLIC_ORIGIN": f"http://127.0.0.1:{port}", "SAAS_SECURE_COOKIE": "false",
           "DATABASE_URL": "sqlite:///:memory:", "RAG_RETRIEVAL_MODE": "lexical",
           "DEFAULT_LLM_PROVIDER": "local", "AIHOT_ENABLED": "false", "GITHUB_ENABLED": "false",
           "PYTHON_DOTENV_DISABLED": "1", "PYTHONDONTWRITEBYTECODE": "1",
           "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "backend")}
    return subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
                             "--port", str(port), "--workers", "1", "--lifespan", "on", "--log-level", "error"],
                            cwd=working_directory, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def test_direct_uvicorn_second_process_exits_before_touching_control_and_restart_succeeds(tmp_path):
    data = tmp_path / "synthetic-private-process-root"
    processes = []
    try:
        first_port = _free_port()
        first = _start(data, first_port, tmp_path)
        processes.append(first)
        _ready(first, first_port)
        before = {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                  for path in data.iterdir() if path.name.startswith("control.db")}
        second_port = _free_port()
        while second_port == first_port:
            second_port = _free_port()
        second = _start(data, second_port, tmp_path)
        processes.append(second)
        _, errors = second.communicate(timeout=25)
        assert second.returncode != 0 and "已有服务或备份任务运行" in errors
        assert str(data) not in errors
        assert {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                for path in data.iterdir() if path.name.startswith("control.db")} == before
        assert first.poll() is None
        _stop(first)
        restarted_port = _free_port()
        restarted = _start(data, restarted_port, tmp_path)
        processes.append(restarted)
        _ready(restarted, restarted_port)
        assert (data / ".service.lock").is_file()
    finally:
        for process in reversed(processes):
            _stop(process)
