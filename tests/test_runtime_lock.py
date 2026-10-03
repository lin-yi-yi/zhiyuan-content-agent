"""Local SQLite startup ownership, using temporary files and real uvicorn processes.

No user database, .env, launcher, online model or external network is used.
"""
import http.client
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import time

import pytest

from app.db.runtime_lock import DatabaseRuntimeLockError, local_database_runtime_lock


def test_same_database_lock_covers_relative_symlink_and_sqlite_uri_aliases(tmp_path, monkeypatch):
    database = tmp_path / "a db.sqlite"
    database.touch()
    (tmp_path / "alias.sqlite").symlink_to(database)
    monkeypatch.chdir(tmp_path)
    with local_database_runtime_lock(f"sqlite:///{database}"):
        for url in ("sqlite:///a db.sqlite", "sqlite:///alias.sqlite",
                    f"sqlite:///file:{str(database).replace(' ', '%20')}?uri=true"):
            with pytest.raises(DatabaseRuntimeLockError, match="已由其他服务实例使用"):
                with local_database_runtime_lock(url):
                    pytest.fail("A second owner must not enter startup")
    # A retained lock file does not mean the process still owns the database.
    assert database.with_name(database.name + ".runtime.lock").is_file()
    with local_database_runtime_lock(f"sqlite:///{database}"):
        pass


def test_independent_databases_have_independent_lifetimes(tmp_path):
    with local_database_runtime_lock(f"sqlite:///{tmp_path / 'one.db'}"):
        with local_database_runtime_lock(f"sqlite:///{tmp_path / 'two.db'}"):
            pass


@pytest.mark.parametrize("url,enabled", [
    ("sqlite://", True), ("sqlite:///:memory:", True),
    ("sqlite:///file::memory:?cache=shared&uri=true", True),
    ("sqlite:///file:memory-name?mode=memory&cache=shared&uri=true", True),
    ("postgresql://private-user:private-secret@example.invalid/database", True),
    ("sqlite:///disabled-for-saas.db", False),
])
def test_memory_non_sqlite_and_disabled_saas_paths_do_not_create_local_locks(tmp_path, monkeypatch, url, enabled):
    monkeypatch.chdir(tmp_path)
    with local_database_runtime_lock(url, enabled=enabled):
        pass
    assert list(tmp_path.iterdir()) == []


def test_setup_failure_releases_lock_without_deleting_its_inode(tmp_path):
    url = f"sqlite:///{tmp_path / 'test.db'}"
    with pytest.raises(RuntimeError, match="synthetic init failure"):
        with local_database_runtime_lock(url):
            raise RuntimeError("synthetic init failure")
    lock = tmp_path / "test.db.runtime.lock"
    inode = lock.stat().st_ino
    with local_database_runtime_lock(url):
        assert lock.stat().st_ino == inode


def test_unsafe_lock_or_missing_parent_error_never_exposes_database_path(tmp_path):
    database = tmp_path / "private-database-name.sqlite"
    protected = tmp_path / "existing.txt"
    protected.write_text("preserve", encoding="utf-8")
    database.with_name(database.name + ".runtime.lock").symlink_to(protected)
    for path in (database, tmp_path / "missing-private-parent" / "data.db"):
        with pytest.raises(DatabaseRuntimeLockError) as error:
            with local_database_runtime_lock(f"sqlite:///{path}"):
                pytest.fail("Unsafe/unavailable lock must stop startup")
        assert str(tmp_path) not in str(error.value) and path.name not in str(error.value)
    assert protected.read_text(encoding="utf-8") == "preserve"


CHILD = """
import os
import sys
import dotenv
dotenv.load_dotenv = lambda *args, **kwargs: False
import uvicorn
sys.argv = ['uvicorn', 'app.main:app', '--host', '127.0.0.1',
            '--port', os.environ['SYNTHETIC_TEST_PORT'], '--lifespan', 'on', '--log-level', 'error']
uvicorn.main()
"""


def _free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def _start(database, port, working_directory):
    env = {**os.environ,
           "SAAS_MODE": "false", "DATABASE_URL": f"sqlite:///{database}",
           "RAG_RETRIEVAL_MODE": "lexical", "DEFAULT_LLM_PROVIDER": "local",
           "DEFAULT_LLM_MODEL": "local-rule-based-v0",
           "AIHOT_ENABLED": "false", "GITHUB_ENABLED": "false",
           "PYTHON_DOTENV_DISABLED": "1", "PYTHONDONTWRITEBYTECODE": "1",
           "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "backend"),
           "SYNTHETIC_TEST_PORT": str(port)}
    return subprocess.Popen([sys.executable, "-c", CHILD], cwd=working_directory,
                            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def _ready(process, port):
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        if process.poll() is not None:
            _, errors = process.communicate()
            pytest.fail(f"Synthetic server exited during startup: {errors}")
        try:
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=0.25)
            try:
                connection.request("GET", "/api/health")
                response = connection.getresponse()
                response.read()
                if response.status == 200:
                    return
            finally:
                connection.close()
        except (OSError, http.client.HTTPException):
            pass
        time.sleep(0.05)
    pytest.fail("Synthetic uvicorn did not become healthy within the bounded startup timeout")


def _stop(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate(timeout=5)


def _states(database):
    with sqlite3.connect(database) as db:
        return db.execute("SELECT id,status,error_message,result_json FROM agent_runs ORDER BY id").fetchall()


@pytest.mark.parametrize("same_port", [True, False], ids=["same-port", "different-port"])
def test_second_uvicorn_exits_before_recovery_and_owner_shutdown_allows_restart(tmp_path, same_port):
    database = tmp_path / "synthetic-private-database.sqlite"
    first_port = _free_port()
    second_port = first_port if same_port else _free_port()
    while not same_port and second_port == first_port:
        second_port = _free_port()
    processes = []
    try:
        first = _start(database, first_port, tmp_path)
        processes.append(first)
        _ready(first, first_port)
        # These persisted states stand for the first instance's live work; no
        # online model is needed to reproduce startup recovery corrupting them.
        with sqlite3.connect(database) as db:
            for status in ("pending", "running"):
                cursor = db.execute(
                    "INSERT INTO agent_runs (goal,mode,provider,status,current_step,result_json) VALUES (?,?,?,?,?,?)",
                    ("synthetic active task", "inspiration", "local", status, "topic_ideas", "{}"))
                db.execute(
                    "INSERT INTO agent_steps (run_id,step_index,key,label,status) VALUES (?,?,?,?,?)",
                    (cursor.lastrowid, 1, "topic_ideas", "synthetic step", status))
        before = _states(database)
        second = _start(database, second_port, tmp_path)
        processes.append(second)
        _, errors = second.communicate(timeout=25)
        assert second.returncode != 0
        assert "已由其他服务实例使用" in errors
        assert str(database) not in errors
        assert _states(database) == before
        assert first.poll() is None

        _stop(first)
        restarted_port = _free_port()
        restarted = _start(database, restarted_port, tmp_path)
        processes.append(restarted)
        _ready(restarted, restarted_port)
        recovered = _states(database)
        assert [row[1] for row in recovered] == ["failed", "failed"]
        assert all(json.loads(row[3])["failure"]["code"] == "INTERRUPTED" for row in recovered)
    finally:
        for process in reversed(processes):
            _stop(process)
