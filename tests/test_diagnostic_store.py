"""Private diagnostic writer contracts using real temporary files and flock."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import stat
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.core import diagnostic_store as module
from app.core.diagnostic_schema import validate_record
from app.core.diagnostic_store import DiagnosticStore, StoreError, LOG_NAME, LOCK_NAME, MARKER_NAME, MARKER_FORMAT


def payload(index=1, **changes):
    return {"event": "http_response", "at": "2026-10-04T15:00:00+00:00", "request_id": "a" * 32,
            "organization_id": None, "agent_run_id": index, "agent_step_id": None,
            "workflow_attempt": None, "step_key": None, "status": None, "code": None,
            "error_type": None, "elapsed_ms": 1.5, "http_status": 200, "usage_outcome": None,
            "method": "GET", "route": "/api/agent-runs/{run_id}", **changes}


@pytest.fixture
def directory(tmp_path):
    target = tmp_path / "private-diagnostics"
    target.mkdir(mode=0o700)
    return target


def rows(directory, backups=20):
    result = []
    for path in [*(directory / f"{LOG_NAME}.{index}" for index in range(backups, 0, -1)), directory / LOG_NAME]:
        if path.exists():
            data = path.read_bytes()
            assert not data or data.endswith(b"\n")
            for line in data.splitlines(keepends=True):
                assert len(line) <= 4096
                value = json.loads(line)
                assert validate_record(value) == value
                result.append(value)
    return result


def test_private_versioned_files_complete_records_and_idempotent_close(directory):
    store = DiagnosticStore(directory)
    assert store.status() == {"state": "healthy", "written": 0, "dropped": 0, "error_code": None}
    original = payload()
    assert store.append(original)
    assert "schema_version" not in original
    assert rows(directory) == [{**original, "schema_version": 1}]
    assert json.loads((directory / MARKER_NAME).read_text()) == {"format": MARKER_FORMAT, "schema_version": 1}
    assert {path.name for path in directory.iterdir()} == {MARKER_NAME, LOG_NAME, LOCK_NAME}
    for path in directory.iterdir():
        info = path.stat()
        assert stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink == 1 and info.st_uid == os.getuid()
    lock_inode = (directory / LOCK_NAME).stat().st_ino
    store.close()
    store.close()
    assert not store.append(payload())
    assert store.status() == {"state": "closed", "written": 1, "dropped": 1, "error_code": None}
    assert (directory / LOCK_NAME).stat().st_ino == lock_inode
    reopened = DiagnosticStore(directory)
    assert reopened.append(payload(2))
    reopened.close()
    assert [item["agent_run_id"] for item in rows(directory)] == [1, 2]
    assert (directory / LOCK_NAME).stat().st_ino == lock_inode


@pytest.mark.parametrize("kwargs", [{"max_bytes": 8191}, {"max_bytes": 16777217}, {"max_bytes": True},
    {"max_bytes": 8192.0}, {"backup_count": 0}, {"backup_count": 21}, {"backup_count": True}, {"backup_count": 1.0}])
def test_invalid_limits_do_not_create_files(directory, kwargs):
    with pytest.raises(StoreError, match="^Diagnostic store initialization failed\\.$"):
        DiagnosticStore(directory, **kwargs)
    assert not list(directory.iterdir())


@pytest.mark.parametrize("kind", ["relative", "missing", "file", "link", "public", "foreign-owner"])
def test_unsafe_directories_are_rejected_without_claiming_them(tmp_path, monkeypatch, kind):
    directory = tmp_path / "private-diagnostics"
    if kind == "relative":
        monkeypatch.chdir(tmp_path)
        directory = Path("relative")
    elif kind == "file":
        directory.write_text("private existing data")
    elif kind == "link":
        target = tmp_path / "real"
        target.mkdir(mode=0o700)
        directory.symlink_to(target, target_is_directory=True)
    elif kind in {"public", "foreign-owner"}:
        directory.mkdir(mode=0o700)
        if kind == "public":
            directory.chmod(0o750)
        else:
            current = os.getuid()
            monkeypatch.setattr(module.os, "getuid", lambda: current + 1)
    with pytest.raises(StoreError) as error:
        DiagnosticStore(directory)
    assert str(directory) not in str(error.value)
    if kind in {"relative", "missing"}:
        assert not directory.exists()
    if kind == "file":
        assert directory.read_text() == "private existing data"
    if kind in {"public", "foreign-owner", "link"}:
        assert not list(directory.iterdir())


@pytest.mark.parametrize("name", ["private.env", "unrelated.txt", LOG_NAME + ".0", LOG_NAME + ".01", LOG_NAME + ".21"])
def test_unrelated_files_are_neither_adopted_nor_deleted(directory, name):
    existing = directory / name
    existing.write_text("synthetic secret must stay private")
    existing.chmod(0o600)
    with pytest.raises(StoreError):
        DiagnosticStore(directory)
    assert existing.read_text() == "synthetic secret must stay private"
    assert {path.name for path in directory.iterdir()} == {name}


@pytest.mark.parametrize("kind", ["missing-marker", "wrong-marker", "bool-version", "marker-extra", "duplicate-marker", "truncated-line",
                                  "retention-reduced", "size-reduced"])
def test_existing_incompatible_history_is_preserved_not_silently_repaired(directory, kind):
    store = DiagnosticStore(directory)
    assert store.append(payload())
    store.close()
    kwargs = {}
    if kind == "missing-marker":
        (directory / MARKER_NAME).unlink()
    elif kind == "wrong-marker":
        (directory / MARKER_NAME).write_text('{}')
    elif kind == "bool-version":
        (directory / MARKER_NAME).write_text(json.dumps({"format": MARKER_FORMAT, "schema_version": True}))
    elif kind == "marker-extra":
        (directory / MARKER_NAME).write_text(json.dumps({"format": MARKER_FORMAT, "schema_version": 1, "secret": "forbidden"}))
    elif kind == "duplicate-marker":
        (directory / MARKER_NAME).write_text('{"format":"' + MARKER_FORMAT + '","schema_version":1,"schema_version":1}')
    elif kind == "truncated-line":
        with (directory / LOG_NAME).open("ab") as stream:
            stream.write(b'{"incomplete":')
    elif kind == "retention-reduced":
        (directory / f"{LOG_NAME}.2").write_bytes(b"")
        (directory / f"{LOG_NAME}.2").chmod(0o600)
        kwargs = {"backup_count": 1}
    else:
        (directory / LOG_NAME).write_bytes(b"x" * 8192 + b"\n")
        kwargs = {"max_bytes": 8192}
    before = {path.name: path.read_bytes() for path in directory.iterdir()}
    with pytest.raises(StoreError):
        DiagnosticStore(directory, **kwargs)
    assert {path.name: path.read_bytes() for path in directory.iterdir()} == before


@pytest.mark.parametrize("name", [LOG_NAME, MARKER_NAME, LOCK_NAME])
@pytest.mark.parametrize("kind", ["link", "hardlink", "public", "fifo"])
def test_all_managed_files_require_private_regular_single_link_inodes(directory, tmp_path, name, kind):
    store = DiagnosticStore(directory)
    store.close()
    path = directory / name
    if kind == "public":
        path.chmod(0o640)
    else:
        original = tmp_path / "retained-original"
        path.rename(original)
        if kind == "link":
            path.symlink_to(original)
        elif kind == "hardlink":
            os.link(original, path)
        else:
            os.mkfifo(path, 0o600)
    with pytest.raises(StoreError):
        DiagnosticStore(directory)
    assert path.exists()


def test_lifetime_exclusive_lock_cross_process_and_after_close(directory):
    script = """import sys
from app.core.diagnostic_store import DiagnosticStore, StoreError
try:
    store = DiagnosticStore(sys.argv[1])
except StoreError:
    print('refused'); sys.exit(2)
store.close(); print('opened')
"""
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "backend")}
    store = DiagnosticStore(directory)
    try:
        refused = subprocess.run([sys.executable, "-c", script, str(directory)], env=env, text=True,
                                 capture_output=True, timeout=10)
        assert refused.returncode == 2 and refused.stdout.strip() == "refused"
        assert not refused.stderr
    finally:
        store.close()
    reopened = subprocess.run([sys.executable, "-c", script, str(directory)], env=env, text=True,
                             capture_output=True, timeout=10)
    assert reopened.returncode == 0 and reopened.stdout.strip() == "opened"


def test_rotations_are_bounded_ordered_private_and_resumable(directory):
    store = DiagnosticStore(directory, max_bytes=8192, backup_count=2)
    for index in range(1, 101):
        assert store.append(payload(index)), store.status()
    assert store.status()["written"] == 100
    store.close()
    assert {path.name for path in directory.iterdir()} == {MARKER_NAME, LOCK_NAME, LOG_NAME, LOG_NAME + ".1", LOG_NAME + ".2"}
    records = rows(directory)
    identifiers = [row["agent_run_id"] for row in records]
    assert 1 < identifiers[0] < 100 and identifiers == list(range(identifiers[0], 101))
    for path in directory.glob(LOG_NAME + "*"):
        assert 0 < path.stat().st_size <= 8192 and stat.S_IMODE(path.stat().st_mode) == 0o600
    reopened = DiagnosticStore(directory, max_bytes=8192, backup_count=2)
    assert reopened.append(payload(101))
    reopened.close()
    assert rows(directory)[-1]["agent_run_id"] == 101


def test_threaded_appends_never_interleave_json_lines(directory):
    store = DiagnosticStore(directory)
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda index: store.append(payload(index)), range(1, 101)))
    store.close()
    assert all(results)
    assert sorted(row["agent_run_id"] for row in rows(directory)) == list(range(1, 101))


@pytest.mark.parametrize("changes", [{"prompt": "secret prompt"}, {"exception": "secret stack"},
    {"api_key": "secret key"}, {"request_id": "secret-cookie"}, {"error_type": "secret exception"},
    {"route": "/api/secret?token=secret"}, {"event": "secret text"}, {"schema_version": True},
    {"schema_version": 1.0}, {"schema_version": 2}, {"elapsed_ms": float("inf")}])
def test_secret_or_invalid_records_are_rejected_without_poisoning_valid_writes(directory, changes):
    store = DiagnosticStore(directory)
    assert not store.append(payload(**changes))
    assert store.status() == {"state": "healthy", "written": 0, "dropped": 1, "error_code": "INVALID_RECORD"}
    assert (directory / LOG_NAME).read_bytes() == b""
    assert store.append(payload())
    assert store.status()["error_code"] is None
    store.close()
    assert b"secret" not in (directory / LOG_NAME).read_bytes()


def test_complete_line_limit_includes_newline_even_if_schema_later_expands(directory, monkeypatch):
    store = DiagnosticStore(directory)
    # Independently protect the sink's byte bound if a future validator changes.
    monkeypatch.setattr(module, "validate_record", lambda _record: {"future": "x" * 4096})
    assert not store.append(payload())
    assert store.status()["error_code"] == "INVALID_RECORD"
    assert (directory / LOG_NAME).read_bytes() == b""
    store.close()


def test_short_writes_are_completed_and_fsynced(directory, monkeypatch):
    store = DiagnosticStore(directory)
    original_write, original_fsync = os.write, os.fsync
    counts, synced = [], []

    def short_write(descriptor, data):
        if descriptor == store._active_fd:
            counts.append(len(data))
            return original_write(descriptor, data[:7])
        return original_write(descriptor, data)

    def sync(descriptor):
        synced.append(descriptor)
        return original_fsync(descriptor)

    monkeypatch.setattr(module.os, "write", short_write)
    monkeypatch.setattr(module.os, "fsync", sync)
    assert store.append(payload())
    assert len(counts) > 1 and store._active_fd in synced
    assert len(rows(directory)) == 1
    store.close()


@pytest.mark.parametrize("failure", ["partial", "zero", "fsync"])
def test_uncertain_write_stops_future_writes_but_retains_lifetime_lock(directory, monkeypatch, failure):
    store = DiagnosticStore(directory)
    original_write, original_fsync = os.write, os.fsync
    calls = []

    def write(descriptor, data):
        if descriptor != store._active_fd:
            return original_write(descriptor, data)
        calls.append(1)
        if failure == "zero":
            return 0
        if failure == "partial":
            if len(calls) == 1:
                return original_write(descriptor, data[:5])
            raise OSError("secret error must not escape")
        return original_write(descriptor, data)

    def sync(descriptor):
        if descriptor == store._active_fd and failure == "fsync":
            raise OSError("secret error must not escape")
        return original_fsync(descriptor)

    monkeypatch.setattr(module.os, "write", write)
    monkeypatch.setattr(module.os, "fsync", sync)
    assert not store.append(payload())
    assert store.status() == {"state": "degraded", "written": 0, "dropped": 1, "error_code": "WRITE_FAILED"}
    before = (directory / LOG_NAME).read_bytes()
    assert not store.append(payload(2))
    assert (directory / LOG_NAME).read_bytes() == before
    with pytest.raises(StoreError):
        DiagnosticStore(directory)
    assert "secret" not in json.dumps(store.status())
    store.close()
    assert (directory / LOCK_NAME).exists()


def test_directory_fsync_failure_rejects_initialization_and_releases_lock(directory, monkeypatch):
    original = os.fsync

    def failed(descriptor):
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise OSError("private path and secret")
        return original(descriptor)

    with monkeypatch.context() as patch:
        patch.setattr(module.os, "fsync", failed)
        with pytest.raises(StoreError) as error:
            DiagnosticStore(directory)
        assert "secret" not in str(error.value)
    recovered = DiagnosticStore(directory)
    assert recovered.append(payload())
    recovered.close()


def test_rotation_directory_fsync_failure_degrades_without_continuing_append(directory, monkeypatch):
    store = DiagnosticStore(directory, max_bytes=8192, backup_count=2)
    while (directory / LOG_NAME).stat().st_size + 2 * len(json.dumps({**payload(), "schema_version": 1}).encode()) < 8192:
        assert store.append(payload())
    original = os.fsync

    def failed(descriptor):
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise OSError("secret directory path")
        return original(descriptor)

    monkeypatch.setattr(module.os, "fsync", failed)
    for _ in range(5):
        if not store.append(payload()):
            break
    assert store.status()["state"] == "degraded"
    assert store.status()["error_code"] == "WRITE_FAILED"
    assert (directory / (LOG_NAME + ".1")).exists()
    assert (directory / LOG_NAME).read_bytes() == b""
    store.close()


@pytest.mark.parametrize("target", ["directory", LOG_NAME, LOCK_NAME, MARKER_NAME])
def test_replaced_files_or_directory_cannot_receive_further_events(directory, tmp_path, target):
    store = DiagnosticStore(directory)
    assert store.append(payload())
    if target == "directory":
        directory.rename(tmp_path / "original-directory")
        directory.mkdir(mode=0o700)
    else:
        path = directory / target
        path.rename(tmp_path / "original-file")
        path.write_bytes(b"")
        path.chmod(0o600)
    before = {path.name: path.read_bytes() for path in directory.iterdir()}
    assert not store.append(payload(2))
    assert store.status()["state"] == "degraded"
    assert store.status()["error_code"] == "STORE_CHANGED"
    assert {path.name: path.read_bytes() for path in directory.iterdir()} == before
    store.close()


def test_marker_replacement_during_write_is_not_adopted_as_new_baseline(directory, tmp_path, monkeypatch):
    store = DiagnosticStore(directory)
    original_write = os.write

    def replace_marker(descriptor, data):
        count = original_write(descriptor, data)
        if descriptor == store._active_fd:
            marker = directory / MARKER_NAME
            marker.rename(tmp_path / "old-marker")
            marker.write_text(json.dumps({"format": MARKER_FORMAT, "schema_version": 1}))
            marker.chmod(0o600)
        return count

    monkeypatch.setattr(module.os, "write", replace_marker)
    assert not store.append(payload())
    assert store.status()["state"] == "degraded" and store.status()["error_code"] == "STORE_CHANGED"
    store.close()
