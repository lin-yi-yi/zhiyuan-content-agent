"""Read-only private retained-log queries; all input and fault fixtures are synthetic."""
import builtins
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/diagnostic_query.py"
SPEC = importlib.util.spec_from_file_location("diagnostic_query_tool", SCRIPT)
query = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(query)
REQUEST = "a" * 32
OTHER = "b" * 32
SECRET = "SYNTHETIC-PRIVATE-DO-NOT-PRINT-9752"


def record(**fields):
    return {"schema_version": 1, "event": "http_response", "at": "2026-10-04T01:02:03+00:00",
            "request_id": REQUEST, "organization_id": None, "agent_run_id": None, "agent_step_id": None,
            "workflow_attempt": None, "step_key": None, "status": None, "code": None,
            "error_type": None, "elapsed_ms": 1.0, "http_status": 200, "usage_outcome": None,
            "method": "GET", "route": "/api/agent-runs/{run_id}", **fields}


def encoded(value):
    return json.dumps(value, separators=(",", ":")).encode() + b"\n"


def write(path, content):
    path.write_bytes(content)
    path.chmod(0o600)


@pytest.fixture
def logs(tmp_path):
    directory = tmp_path / SECRET
    directory.mkdir(mode=0o700)
    write(directory / query.MARKER_NAME, encoded(query.MARKER))
    write(directory / query.LOCK_NAME, b"")
    write(directory / query.LOG_NAME, b"")
    return directory


def invoke(directory, request_id=REQUEST, limit=50):
    result, code = query.query(directory, request_id, limit)
    assert set(result) == {"schema_version", "scan_status", "match_status", "complete", "records",
                           "counts", "truncated", "reason_codes"}
    assert set(result["counts"]) == {"scanned", "matched", "returned", "invalid", "changed"}
    assert result["complete"] is False
    rendered = json.dumps(result)
    assert SECRET not in rendered and str(directory) not in rendered
    assert all(query.validate_record(item) == item for item in result["records"])
    return result, code


def cli(arguments, cwd):
    return subprocess.run([sys.executable, "-I", "-S", str(SCRIPT), *arguments], cwd=cwd,
        env={"PATH": os.defpath}, text=True, capture_output=True, timeout=10)


def tree_state(root):
    return {str(path.relative_to(root)): (path.stat().st_mode, path.stat().st_mtime_ns,
             hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None)
            for path in [root, *sorted(root.rglob("*"))]}


def test_rotations_oldest_first_limit_counts_all_records_and_keeps_same_request_events(logs):
    write(logs / f"{query.LOG_NAME}.20", encoded(record(agent_step_id=1, organization_id=OTHER)))
    write(logs / f"{query.LOG_NAME}.1", encoded(record(request_id=OTHER)) + encoded(record(agent_step_id=2)))
    write(logs / query.LOG_NAME, encoded(record(agent_step_id=3)))
    result, code = invoke(logs, limit=2)
    assert code == 0 and result["scan_status"] == "ok" and result["match_status"] == "found"
    assert [item["agent_step_id"] for item in result["records"]] == [1, 2]
    assert result["counts"] == {"scanned": 4, "matched": 3, "returned": 2, "invalid": 0, "changed": 0}
    assert result["truncated"] is True and result["reason_codes"] == []


@pytest.mark.parametrize("content", [b"", encoded(record(request_id=OTHER))])
def test_not_observed_is_only_about_retained_window(logs, content):
    write(logs / query.LOG_NAME, content)
    result, code = invoke(logs)
    assert code == 1 and result["scan_status"] == "ok" and result["match_status"] == "not_observed"
    assert result["records"] == [] and result["complete"] is False


def test_real_cli_reads_while_writer_lock_held_and_does_not_change_files(logs, tmp_path):
    write(logs / query.LOG_NAME, encoded(record()))
    (tmp_path / ".env").write_text("INVALID_SECRET=" + SECRET)
    before = tree_state(tmp_path)
    with (logs / query.LOCK_NAME).open("rb") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        process = cli(["--directory", str(logs), "--request-id", REQUEST], tmp_path)
    assert process.returncode == 0 and process.stderr == ""
    assert json.loads(process.stdout)["scan_status"] == "ok"
    assert SECRET not in process.stdout
    assert tree_state(tmp_path) == before


def test_real_writer_rotation_and_retention_are_readable_without_disturbing_writer(tmp_path):
    from app.core import diagnostic_store
    assert (query.MARKER_NAME, query.MARKER["format"], query.LOG_NAME, query.LOCK_NAME,
            query.MAX_FILE_BYTES, query.MAX_BACKUPS) == (diagnostic_store.MARKER_NAME,
        diagnostic_store.MARKER_FORMAT, diagnostic_store.LOG_NAME, diagnostic_store.LOCK_NAME,
        diagnostic_store.MAX_FILE_BYTES, diagnostic_store.MAX_BACKUPS)
    directory = tmp_path / "real-writer"
    directory.mkdir(mode=0o700)
    store = diagnostic_store.DiagnosticStore(directory, max_bytes=8192, backup_count=1)
    try:
        assert store.append(record())
        for number in range(1, 81):
            assert store.append(record(request_id=OTHER, agent_step_id=number))
        before = tree_state(directory)
        result, code = invoke(directory)
        assert code == 1 and result["match_status"] == "not_observed"  # Oldest event was rotated out.
        result, code = invoke(directory, request_id=OTHER, limit=200)
        assert code == 0 and 0 < result["counts"]["matched"] < 80
        steps = [item["agent_step_id"] for item in result["records"]]
        assert steps == sorted(steps) and steps[-1] == 80
        assert tree_state(directory) == before
        assert store.append(record(request_id=OTHER, agent_step_id=81))
        assert store.status()["state"] == "healthy"
    finally:
        store.close()


@pytest.mark.parametrize("arguments", [[], ["--directory"],
    ["--directory", SECRET, "--request-id", REQUEST],
    ["--directory", "/" + SECRET, "--request-id", "A" * 32],
    ["--directory", "/" + SECRET, "--request-id", REQUEST + "0"],
    ["--directory", "/" + SECRET, "--request-id", SECRET],
    ["--directory", "/" + SECRET, "--request-id", REQUEST, "--request-id", REQUEST],
    ["--directory", "/" + SECRET, "--directory", "/tmp", "--request-id", REQUEST],
    ["--directory", "/" + SECRET, "--request-id", REQUEST, "--limit", "1", "--limit", "2"],
    ["--dir", "/" + SECRET, "--request-id", REQUEST],
    ["--directory", "/" + SECRET, "--request-id", REQUEST, "--" + SECRET],
    *[["--directory", "/" + SECRET, "--request-id", REQUEST, "--limit", limit]
      for limit in ["0", "201", "-1", "NaN", "1.1", "1e2", SECRET, "9" * 1000]],
])
def test_argument_errors_are_json_without_filesystem_access(arguments, capsys, monkeypatch):
    monkeypatch.setattr(query, "_open_directory", lambda *_: pytest.fail("invalid arguments inspected files"))
    assert query.main(arguments) == 2
    captured = capsys.readouterr()
    value = json.loads(captured.out)
    assert captured.err == "" and SECRET not in captured.out
    assert value["reason_codes"] == ["INVALID_ARGUMENT"] and value["scan_status"] == "unavailable"


@pytest.mark.parametrize("directory", ["relative", "~/private", "/tmp/../private", "/tmp/private/.", "/tmp/private/", "/bad\0path"])
def test_directory_requires_explicit_unambiguous_absolute_path(directory):
    result, code = invoke(directory)
    assert code == 2 and result["reason_codes"] == ["INVALID_ARGUMENT"]


@pytest.mark.parametrize("fault", ["missing", "file", "symlink", "public", "wrong_owner"])
def test_private_directory_boundary(logs, tmp_path, monkeypatch, fault):
    target = logs
    if fault == "missing":
        target = tmp_path / "absent"
    elif fault == "file":
        target = logs / query.LOG_NAME
    elif fault == "symlink":
        target = tmp_path / "alias"
        target.symlink_to(logs, target_is_directory=True)
    elif fault == "public":
        logs.chmod(0o755)
    else:
        actual = os.getuid()
        monkeypatch.setattr(query.os, "getuid", lambda: actual + 1)
    result, code = invoke(target)
    assert code == 2 and result["scan_status"] == "unavailable"
    assert result["match_status"] == "unknown" and result["records"] == []


def test_parent_system_alias_is_allowed_but_final_component_is_not(logs, tmp_path):
    alias = tmp_path / "parent-alias"
    alias.symlink_to(tmp_path, target_is_directory=True)
    write(logs / query.LOG_NAME, encoded(record()))
    result, code = invoke(alias / logs.name)
    assert code == 0 and result["counts"]["matched"] == 1


@pytest.mark.parametrize("fault", ["missing", "unknown_format", "extra", "duplicate", "bool_version", "large", "malformed"])
def test_marker_is_required_exact_and_checked_before_log_bodies(logs, monkeypatch, fault):
    marker = logs / query.MARKER_NAME
    content = {"unknown_format": encoded({**query.MARKER, "format": SECRET}),
               "extra": encoded({**query.MARKER, "secret": SECRET}),
               "duplicate": b'{"format":"zhiyuan-diagnostics-jsonl","schema_version":1,"schema_version":1}',
               "bool_version": encoded({**query.MARKER, "schema_version": True}),
               "large": SECRET.encode() * 1000, "malformed": SECRET.encode()}
    marker.unlink() if fault == "missing" else write(marker, content[fault])
    monkeypatch.setattr(query, "_scan_file", lambda *_: pytest.fail("invalid marker must prevent log scan"))
    result, code = invoke(logs)
    assert code == 2 and result["scan_status"] == "unavailable" and result["records"] == []


@pytest.mark.parametrize("fault", ["mode", "symlink", "hardlink", "fifo", "directory"])
def test_unsafe_log_file_is_never_read_or_blocked(logs, tmp_path, fault):
    active = logs / query.LOG_NAME
    active.unlink()
    if fault == "symlink":
        private = tmp_path / "private"
        write(private, SECRET.encode())
        active.symlink_to(private)
    elif fault == "hardlink":
        private = tmp_path / "private"
        write(private, SECRET.encode())
        os.link(private, active)
    elif fault == "fifo":
        os.mkfifo(active, 0o600)
    elif fault == "directory":
        active.mkdir(mode=0o700)
    else:
        write(active, SECRET.encode())
        active.chmod(0o640)
    result, code = invoke(logs)
    assert code == 2 and result["scan_status"] == "unavailable"
    assert result["reason_codes"] == ["UNSAFE_FILE"]


@pytest.mark.parametrize("bad_line,reason", [
    (SECRET.encode() + b"\n", "INVALID_RECORD"),
    (b"\xff" + SECRET.encode() + b"\n", "INVALID_RECORD"),
    (encoded({**record(), "prompt": SECRET}), "INVALID_RECORD"),
    (encoded(record(event=SECRET)), "INVALID_RECORD"),
    (encoded(record(schema_version=2)), "INVALID_RECORD"),
    (encoded(record()).replace(b'"schema_version":1', b'"schema_version":1,"schema_version":1'), "INVALID_RECORD"),
    (encoded(record()).replace(b'"elapsed_ms":1.0', b'"elapsed_ms":NaN'), "INVALID_RECORD"),
    (encoded(record())[:-1], "PARTIAL_LINE"),
    (b"x" * query.MAX_RECORD_BYTES + b"\n", "RECORD_TOO_LARGE"),
])
def test_bad_records_after_limit_are_still_detected_and_never_returned(logs, bad_line, reason):
    write(logs / query.LOG_NAME, encoded(record()) + encoded(record()) + bad_line)
    result, code = invoke(logs, limit=1)
    assert code == 2 and result["scan_status"] == "partial" and result["match_status"] == "found"
    assert result["counts"] == {"scanned": 3, "matched": 2, "returned": 1, "invalid": 1, "changed": 0}
    assert result["reason_codes"] == [reason] and result["truncated"] is True


def test_oversized_multichunk_line_is_drained_without_counting_its_tail_as_records(logs):
    write(logs / query.LOG_NAME, b"x" * 140000 + b"\n" + encoded(record()))
    result, code = invoke(logs)
    assert code == 2 and result["counts"]["scanned"] == 2 and result["counts"]["matched"] == 1
    assert result["counts"]["invalid"] == 1


def test_oversized_file_is_not_read(logs, monkeypatch):
    with (logs / query.LOG_NAME).open("wb") as stream:
        stream.truncate(query.MAX_FILE_BYTES + 1)
    monkeypatch.setattr(query, "_scan_file", lambda *_: pytest.fail("oversized file must not be read"))
    result, code = invoke(logs)
    assert code == 2 and result["reason_codes"] == ["FILE_TOO_LARGE"]
    assert result["counts"]["scanned"] == 0 and result["match_status"] == "unknown"


def test_unknown_entry_is_not_read_and_more_than_21_logs_is_unavailable(logs):
    write(logs / query.LOG_NAME, encoded(record()))
    os.mkfifo(logs / SECRET, 0o600)
    result, code = invoke(logs)
    assert code == 2 and result["scan_status"] == "partial" and result["reason_codes"] == ["UNKNOWN_ENTRY"]
    for index in range(1, 21):
        write(logs / f"{query.LOG_NAME}.{index}", b"")
    result, code = invoke(logs)
    assert code == 2 and result["scan_status"] == "unavailable" and result["reason_codes"] == ["TOO_MANY_FILES"]
    assert result["counts"]["scanned"] == 0


def test_marker_without_any_log_is_unavailable(logs):
    (logs / query.LOG_NAME).unlink()
    result, code = invoke(logs)
    assert code == 2 and result["reason_codes"] == ["NO_LOG_FILES"]


@pytest.mark.parametrize("change", ["append", "truncate", "rotate", "replace", "marker", "rename_directory"])
def test_active_changes_are_partial_even_when_records_were_found(logs, monkeypatch, change):
    write(logs / query.LOG_NAME, encoded(record()))
    original = query._scan_file
    def scan(*args):
        original(*args)
        active = logs / query.LOG_NAME
        if change == "append":
            with active.open("ab") as stream:
                stream.write(encoded(record()))
        elif change == "truncate":
            active.write_bytes(b"")
        elif change == "rotate":
            active.rename(logs / f"{query.LOG_NAME}.1")
            write(active, b"")
        elif change == "replace":
            active.unlink()
            write(active, encoded(record(request_id=OTHER)))
        elif change == "marker":
            write(logs / query.MARKER_NAME, encoded({**query.MARKER, "extra": SECRET}))
        else:
            logs.rename(logs.with_name("moved"))
            logs.mkdir(mode=0o700)
    monkeypatch.setattr(query, "_scan_file", scan)
    result, code = invoke(logs)
    assert code == 2 and result["scan_status"] == "partial" and result["match_status"] == "found"
    assert result["counts"]["changed"] >= 1 and len(result["records"]) == 1


@pytest.mark.parametrize("replacement", ["symlink", "fifo", "hardlink", "regular"])
def test_file_redirect_between_stat_and_open_does_not_read_replacement(logs, tmp_path, monkeypatch, replacement):
    active = logs / query.LOG_NAME
    write(active, encoded(record()))
    outside = tmp_path / "outside"
    write(outside, SECRET.encode())
    original = query._open_file
    def redirect(directory_fd, name, expected):
        if name == query.LOG_NAME:
            active.unlink()
            if replacement == "symlink":
                active.symlink_to(outside)
            elif replacement == "fifo":
                os.mkfifo(active, 0o600)
            elif replacement == "hardlink":
                os.link(outside, active)
            else:
                write(active, encoded(record()))
        return original(directory_fd, name, expected)
    monkeypatch.setattr(query, "_open_file", redirect)
    result, code = invoke(logs)
    assert code == 2 and result["counts"]["scanned"] == 0 and result["records"] == []


def test_concurrent_append_never_reads_beyond_initial_size(logs, monkeypatch):
    content = encoded(record())
    active = logs / query.LOG_NAME
    write(active, content)
    inode = active.stat().st_ino
    actual_read = os.read
    read_bytes = 0
    def append_after_read(descriptor, size):
        nonlocal read_bytes
        data = actual_read(descriptor, size)
        if os.fstat(descriptor).st_ino == inode:
            read_bytes += len(data)
            with active.open("ab") as stream:
                stream.write(encoded(record(agent_step_id=99)))
        return data
    monkeypatch.setattr(query.os, "read", append_after_read)
    result, code = invoke(logs)
    assert code == 2 and result["counts"]["matched"] == 1 and read_bytes == len(content)
    assert result["records"][0]["agent_step_id"] is None


def test_file_size_and_return_limit_inclusive_boundaries(logs, monkeypatch):
    content = encoded(record()) * 201
    write(logs / query.LOG_NAME, content)
    monkeypatch.setattr(query, "MAX_FILE_BYTES", len(content))
    result, code = invoke(logs, limit=200)
    assert code == 0 and result["counts"]["matched"] == 201 and result["counts"]["returned"] == 200
    assert result["truncated"] is True
    with (logs / query.LOG_NAME).open("ab") as stream:
        stream.write(b"\n")
    result, code = invoke(logs, limit=200)
    assert code == 2 and result["reason_codes"] == ["FILE_TOO_LARGE"]


def test_internal_failure_message_cannot_escape_reason_allowlist(logs, monkeypatch):
    def fail(*args):
        raise query.ScanError(SECRET)
    monkeypatch.setattr(query, "_marker", fail)
    result, code = invoke(logs)
    assert code == 2 and result["reason_codes"] == ["SCAN_UNAVAILABLE"]


def test_directory_redirect_after_open_only_reads_pinned_original_and_reports_change(logs, tmp_path, monkeypatch):
    write(logs / query.LOG_NAME, encoded(record()))
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o700)
    write(outside / query.LOG_NAME, SECRET.encode())
    original = query._marker
    def redirect(*args):
        original(*args)
        logs.rename(logs.with_name("original"))
        logs.symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(query, "_marker", redirect)
    result, code = invoke(logs)
    assert code == 2 and result["match_status"] == "found" and "DIRECTORY_CHANGED" in result["reason_codes"]


def test_read_failure_and_no_matches_remains_unknown(logs, monkeypatch):
    write(logs / query.LOG_NAME, encoded(record(request_id=OTHER)))
    def fail(*args):
        raise OSError(SECRET)
    monkeypatch.setattr(query, "_scan_file", fail)
    result, code = invoke(logs)
    assert code == 2 and result["match_status"] == "unknown" and result["reason_codes"] == ["READ_FAILED"]


def test_only_side_effect_free_schema_import_and_no_network_or_database(logs, monkeypatch):
    import runpy
    write(logs / query.LOG_NAME, encoded(record()))
    imported = []
    original = builtins.__import__
    def guarded_import(name, *args, **kwargs):
        imported.append(name)
        assert name not in {"app.main", "app.core.config", "dotenv", "sqlalchemy", "sqlite3"}
        return original(name, *args, **kwargs)
    def forbidden(*args, **kwargs):
        pytest.fail("offline query attempted network")
    monkeypatch.setattr(builtins, "__import__", guarded_import)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    module = runpy.run_path(str(SCRIPT), run_name="isolated_diagnostic_query")
    result, code = module["query"](logs, REQUEST)
    assert code == 0 and result["counts"]["matched"] == 1
    assert "app.core.diagnostic_schema" in imported
