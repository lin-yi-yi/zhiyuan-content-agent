"""Filesystem observations only: temporary fixtures, no service/model/real data access."""
from datetime import datetime
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/ops_health.py"
SPEC = importlib.util.spec_from_file_location("ops_health_tool", SCRIPT)
ops = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ops)
SECRET = "SYNTHETIC-PRIVATE-DIRECTORY-9137"


def invoke(arguments, capsys):
    code = ops.main(arguments)
    captured = capsys.readouterr()
    assert captured.err == ""
    assert SECRET not in captured.out and "Traceback" not in captured.out
    value = json.loads(captured.out)
    assert set(value) == {"schema_version", "checked_at", "status", "reason", "checks"}
    assert value["schema_version"] == 1
    assert datetime.fromisoformat(value["checked_at"]).utcoffset().total_seconds() == 0
    for item in value["checks"]:
        assert set(item) == {"role", "status", "reason", "total_bytes", "free_bytes",
                             "min_free_bytes", "min_free_percent"}
    return code, value


def cli(arguments, cwd):
    # -S proves the operator script does not require installed application packages.
    return subprocess.run([sys.executable, "-I", "-S", "-B", str(SCRIPT), *arguments],
        cwd=cwd, env={"PATH": os.defpath}, capture_output=True, text=True, timeout=10)


def tree_state(root):
    return {str(path.relative_to(root)): (path.stat().st_mode, path.stat().st_mtime_ns,
             hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None)
            for path in [root, *sorted(root.rglob("*"))]}


def test_real_cli_low_space_and_recovery_leave_tree_unchanged(tmp_path):
    data = tmp_path / SECRET
    data.mkdir()
    (data / "content.db").write_bytes(b"synthetic database bytes, never opened by checker")
    (data / ".env").write_text("PRIVATE=" + SECRET)
    before = tree_state(tmp_path)
    normal = ["--directory", f"database={data}", "--min-free-bytes", "1"]
    outcomes = [cli(normal, tmp_path),
                cli([*normal[:-1], str(2 ** 63 - 1)], tmp_path),
                cli(normal, tmp_path)]
    assert [item.returncode for item in outcomes] == [0, 1, 0]
    assert [json.loads(item.stdout)["status"] for item in outcomes] == ["ok", "low", "ok"]
    for item in outcomes:
        assert item.stderr == "" and SECRET not in item.stdout and str(tmp_path) not in item.stdout
    assert tree_state(tmp_path) == before


@pytest.mark.parametrize("arguments", [
    [], ["--directory"], ["--directory", f"database={SECRET}"],
    ["--directory", f"{SECRET}=/tmp", "--min-free-bytes", "1"],
    ["--directory", "database=", "--min-free-bytes", "1"],
    ["--directory", f"database={SECRET}", "--directory", "database=/tmp", "--min-free-bytes", "1"],
    ["--directory", f"database={SECRET}", "--min-free-bytes", "1", f"--{SECRET}"],
    ["--dir", f"database={SECRET}", "--min-free-bytes", "1"],
    ["--directory", f"database={SECRET}", "--min-free-bytes", "1", "--min-free-bytes", "2"],
    ["--directory", f"database={SECRET}", "--min-free-percent", "5", "--min-free-percent", "6"],
    *[["--directory", f"database={SECRET}", "--min-free-bytes", value]
      for value in ["0", "-1", "1.0", "1e3", "NaN", str(2 ** 63), "9" * 1000, SECRET]],
    *[["--directory", f"database={SECRET}", "--min-free-percent", value]
      for value in ["0", "-1", "100.000001", "0.0000001", "1e1", "NaN", "Infinity", ".5", "5.", SECRET]],
])
def test_all_parameter_errors_are_sanitized_json(arguments, capsys, monkeypatch):
    def no_stat(*args, **kwargs):
        pytest.fail("Invalid arguments must not inspect any path")
    monkeypatch.setattr(ops, "os", SimpleNamespace(stat=no_stat))
    code, result = invoke(arguments, capsys)
    assert code == 2 and result["status"] == "unknown"
    assert result["reason"] == "INVALID_ARGUMENT" and result["checks"] == []


def test_real_cli_parameter_error_and_help_do_not_echo_sensitive_values(tmp_path):
    failed = cli([f"--{SECRET}", f"database={tmp_path / SECRET}"], tmp_path)
    assert failed.returncode == 2 and json.loads(failed.stdout)["reason"] == "INVALID_ARGUMENT"
    assert failed.stderr == "" and SECRET not in failed.stdout and str(tmp_path) not in failed.stdout
    help_result = cli(["--help"], tmp_path)
    assert help_result.returncode == 0 and "ROLE=PATH" in help_result.stdout
    assert str(tmp_path) not in help_result.stdout and help_result.stderr == ""


@pytest.mark.parametrize("free,byte_limit,percent_limit,status", [
    (100, "100", "10", "ok"), (99, "100", "9", "low"),
    (99, "1", "10", "low"), (100, None, "10.000001", "low"),
    (100, None, "9.999999", "ok"), (1000, None, "100", "ok"),
    (999, None, "100", "low"), (0, "1", None, "low"),
])
def test_exact_threshold_boundary_and_either_threshold_can_trigger(tmp_path, capsys, monkeypatch,
                                                                 free, byte_limit, percent_limit, status):
    monkeypatch.setattr(ops.shutil, "disk_usage", lambda _: SimpleNamespace(total=1000, used=0, free=free))
    args = ["--directory", f"database={tmp_path}"]
    if byte_limit is not None:
        args += ["--min-free-bytes", byte_limit]
    if percent_limit is not None:
        args += ["--min-free-percent", percent_limit]
    code, result = invoke(args, capsys)
    assert code == (1 if status == "low" else 0) and result["status"] == status
    assert result["checks"][0]["free_bytes"] == free


def test_smallest_percent_is_compared_without_float_rounding(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(ops.shutil, "disk_usage", lambda _: SimpleNamespace(total=100_000_000, used=0, free=1))
    code, result = invoke(["--directory", f"vectors={tmp_path}", "--min-free-percent", "0.000001"], capsys)
    assert code == 0 and result["checks"][0]["min_free_percent"] == 0.000001


@pytest.mark.parametrize("values", [
    (0, 0, 0), (-1, 0, 0), (100, 0, -1), (100, 0, 101), (100, -1, 50), (100, 101, 50),
    (float("inf"), 0, 1), (100, 0, float("nan")), (100, False, 50),
    (100, 0, True), (100.0, 0, 50), (100, 0, "50"), (None, 0, 1),
])
def test_invalid_capacity_is_unknown_without_echoing_values(tmp_path, capsys, monkeypatch, values):
    monkeypatch.setattr(ops.shutil, "disk_usage", lambda _: SimpleNamespace(total=values[0], used=values[1], free=values[2]))
    code, result = invoke(["--directory", f"database={tmp_path}", "--min-free-bytes", "1"], capsys)
    assert code == 2 and result["checks"][0]["reason"] == "INVALID_DISK_USAGE"
    assert result["checks"][0]["total_bytes"] is None and result["checks"][0]["free_bytes"] is None


def test_directory_errors_do_not_probe_parent_or_create_paths(tmp_path, capsys, monkeypatch):
    file = tmp_path / SECRET
    file.write_text("synthetic")
    def no_usage(*args, **kwargs):
        pytest.fail("Invalid targets must not inspect the ancestor filesystem")
    monkeypatch.setattr(ops.shutil, "disk_usage", no_usage)
    before = tree_state(tmp_path)
    code, result = invoke(["--directory", f"database={tmp_path / 'missing'}",
                          "--directory", f"vectors={file}", "--directory", f"backup={file / 'child'}",
                          "--min-free-bytes", "1"], capsys)
    assert code == 2
    assert [item["reason"] for item in result["checks"]] == ["PATH_NOT_FOUND", "NOT_DIRECTORY", "NOT_DIRECTORY"]
    assert tree_state(tmp_path) == before


@pytest.mark.parametrize("phase,reason", [("stat", "STAT_FAILED"), ("usage", "DISK_USAGE_FAILED")])
def test_unexpected_failures_are_fixed_codes_without_exception_details(tmp_path, capsys, monkeypatch, phase, reason):
    def failed(*args, **kwargs):
        raise type(SECRET, (Exception,), {})(SECRET)
    if phase == "stat":
        monkeypatch.setattr(ops, "os", SimpleNamespace(stat=failed))
    else:
        monkeypatch.setattr(ops.shutil, "disk_usage", failed)
    code, result = invoke(["--directory", f"saas={tmp_path}", "--min-free-bytes", "1"], capsys)
    assert code == 2 and result["checks"][0]["reason"] == reason


def test_unknown_has_priority_and_shared_filesystems_are_not_added(tmp_path, capsys, monkeypatch):
    inspected = []
    def disk_usage(path):
        inspected.append(path)
        return SimpleNamespace(total=1000, used=500, free=500)
    monkeypatch.setattr(ops.shutil, "disk_usage", disk_usage)
    code, result = invoke(["--directory", f"database={tmp_path}", "--directory", f"vectors={tmp_path}",
                          "--directory", f"backup={tmp_path / 'missing'}", "--min-free-bytes", "501"], capsys)
    assert code == 2 and result["status"] == "unknown"
    assert [item["status"] for item in result["checks"]] == ["low", "low", "unknown"]
    assert inspected == [str(tmp_path), str(tmp_path)]
    assert "total_bytes" not in result and "free_bytes" not in result


def test_relative_directory_and_symlink_inspect_only_current_target(tmp_path, capsys, monkeypatch):
    target = tmp_path / SECRET
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    monkeypatch.chdir(tmp_path)
    code, result = invoke(["--directory", "vectors=link", "--min-free-bytes", "1"], capsys)
    assert code == 0 and result["checks"][0]["role"] == "vectors"
    target.rmdir()
    code, result = invoke(["--directory", "vectors=link", "--min-free-bytes", "1"], capsys)
    assert code == 2 and result["checks"][0]["reason"] == "PATH_NOT_FOUND"


def test_standalone_process_never_reads_configuration_data_or_opens_network(tmp_path):
    data = tmp_path / SECRET
    data.mkdir()
    (data / "content.db").write_bytes(b"not a database; must never be opened")
    (tmp_path / ".env").write_text("DATABASE_URL=" + SECRET)
    (tmp_path / ".env.saas").write_text("SAAS_DATA_DIR=" + SECRET)
    bootstrap = """
import os, runpy, sys
script, fixture = sys.argv[1:]
def guard(event, args):
    if event.startswith('socket.') or event == 'sqlite3.connect':
        raise AssertionError('forbidden operation')
    if event == 'import' and args[0].split('.')[0] in {'app', 'dotenv', 'sqlalchemy', 'sqlite3', 'httpx'}:
        raise AssertionError('forbidden application dependency')
    if event == 'open' and isinstance(args[0], str) and os.path.abspath(args[0]).startswith(fixture + os.sep):
        raise AssertionError('forbidden data or config read')
    if event in {'os.listdir', 'os.scandir'} and os.path.abspath(str(args[0])).startswith(fixture):
        raise AssertionError('forbidden tree traversal')
sys.addaudithook(guard)
sys.argv = ['ops_health.py', '--directory', 'saas=' + fixture + '/' + 'SYNTHETIC-PRIVATE-DIRECTORY-9137', '--min-free-bytes', '1']
runpy.run_path(script, run_name='__main__')
"""
    before = tree_state(tmp_path)
    result = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", bootstrap, str(SCRIPT), str(tmp_path)],
                            cwd=tmp_path, env={"PATH": os.defpath}, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert result.stderr == "" and json.loads(result.stdout)["status"] == "ok"
    assert SECRET not in result.stdout and tree_state(tmp_path) == before
