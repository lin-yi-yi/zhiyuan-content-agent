"""Recovery runner failure gates; command doubles are not Docker acceptance.

Archive checks use real temporary SQLite snapshots. No service, Docker engine,
existing data, Git operation or external model is started by these tests.
"""
import io
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from types import SimpleNamespace
import urllib.error

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import container_smoke
import saas_backup
import saas_recovery_fixture as fixture
import saas_recovery_smoke as smoke

TOKEN = "a1" * 16
COOKIE = "synthetic-cookie-must-not-be-logged"
CSRF = "synthetic-csrf-must-not-be-logged"


def completed(stdout="", stderr="", returncode=0):
    return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)


def test_docker_commands_confine_data_to_owned_volumes_and_network_none(tmp_path, monkeypatch):
    runtime = smoke.DockerRuntime(tmp_path)
    runtime.image = "synthetic-test-image"
    commands = []
    monkeypatch.setenv("DEEPSEEK_API_KEY", COOKIE)
    monkeypatch.setenv("DATABASE_URL", "sqlite:////must-not-use-private.db")

    def capture(*args, **kwargs):
        commands.append(args)
        return completed("synthetic-container-id")

    monkeypatch.setattr(runtime, "run", capture)
    runtime.start("source", "owned-source-volume", "source")
    runtime.command("owned-target-volume", ["python", "scripts/saas_backup.py", "restore"], source="owned-source-volume")
    for args in commands:
        assert args[args.index("--network") + 1] == "none"
        assert not any(value in args for value in ("--publish", "-p", "--publish-all", "-P", "--privileged", "--net=host"))
        mounts = [args[index + 1] for index, value in enumerate(args) if value == "--mount"]
        assert mounts and all(mount.startswith("type=volume,") for mount in mounts)
        assert "type=bind" not in " ".join(args)
        assert COOKIE not in " ".join(args)
        assert "/must-not-use-private.db" not in " ".join(args)
        assert f"{smoke.OWNER_LABEL}={runtime.token}" in args
    assert "type=volume,src=owned-source-volume,dst=/backup,readonly" in commands[1]
    assert runtime.origin("source") == "http://127.0.0.1:8765"


@pytest.mark.parametrize("code", [0, 143])
def test_docker_stop_accepts_only_completed_lifespan_even_when_uvicorn_reraises_sigterm(tmp_path, monkeypatch, code):
    runtime = smoke.DockerRuntime(tmp_path)

    def run(*args, **kwargs):
        if args[:2] == ("container", "inspect"):
            return completed(json.dumps({"Running": False, "OOMKilled": False, "ExitCode": code}))
        if args[0] == "logs":
            return completed(stderr="INFO: Application shutdown complete.\n")
        return completed()

    monkeypatch.setattr(runtime, "run", run)
    runtime.stop("owned-fixture")


@pytest.mark.parametrize("state,logs", [
    ({"Running": False, "OOMKilled": True, "ExitCode": 0}, "Application shutdown complete."),
    ({"Running": False, "OOMKilled": False, "ExitCode": 137}, "Application shutdown complete."),
    ({"Running": True, "OOMKilled": False, "ExitCode": 0}, "Application shutdown complete."),
    ({"Running": False, "OOMKilled": False, "ExitCode": 1}, "Application shutdown complete."),
    ({"Running": False, "OOMKilled": False, "ExitCode": 143}, "Shutting down"),
    ({"Running": False, "OOMKilled": False, "ExitCode": 0}, ""),
])
def test_docker_stop_refuses_oom_kill_still_running_and_incomplete_cleanup(tmp_path, monkeypatch, state, logs):
    runtime = smoke.DockerRuntime(tmp_path)

    def run(*args, **kwargs):
        if args[:2] == ("container", "inspect"):
            return completed(json.dumps(state))
        return completed(logs if args[0] == "logs" else "")

    monkeypatch.setattr(runtime, "run", run)
    with pytest.raises(smoke.SmokeError):
        runtime.stop("owned-fixture")


@pytest.mark.parametrize("code,log,accepted", [
    (0, "Application shutdown complete.", True), (-15, "Application shutdown complete.", True),
    (-9, "Application shutdown complete.", False), (0, "Shutting down", False),
])
def test_local_stop_also_requires_clean_exit_and_lifespan_receipt(tmp_path, code, log, accepted):
    runtime = smoke.LocalRuntime(tmp_path)

    class Process:
        returncode = code
        def poll(self): return self.returncode

    process = Process()
    log_path = tmp_path / "fixture.log"
    log_path.write_text(log)
    runtime.process_logs[process] = log_path
    try:
        if accepted:
            runtime.stop((process, 8765))
        else:
            with pytest.raises(smoke.SmokeError):
                runtime.stop((process, 8765))
    finally:
        runtime.temporary.cleanup()


@pytest.mark.parametrize("failure", ["timeout", "bad-json", "exit", "os-error"])
def test_docker_private_http_failure_never_saves_cookie_csrf_or_response(tmp_path, monkeypatch, failure):
    runtime = smoke.DockerRuntime(tmp_path)
    private = json.dumps({"Cookie": COOKIE, "csrf_token": CSRF})

    def fail(argv, **kwargs):
        assert COOKIE not in " ".join(argv) and CSRF not in " ".join(argv)
        payload = json.loads(kwargs["input"])
        assert payload["headers"] == {"Cookie": COOKIE, "X-CSRF-Token": CSRF}
        if failure == "timeout":
            raise subprocess.TimeoutExpired(argv, 30, output=private, stderr=private)
        if failure == "bad-json":
            return completed(private + "malformed")
        if failure == "os-error":
            raise OSError(private)
        return completed(private, private, 1)

    monkeypatch.setattr(smoke.subprocess, "run", fail)
    with pytest.raises(smoke.SmokeError) as error:
        runtime.transport("owned-fixture")("POST", "/api/saas/auth/login", body={"password": CSRF},
            headers={"Cookie": COOKIE, "X-CSRF-Token": CSRF})
    assert str(error.value) == "HTTP probe failed"
    assert COOKIE not in str(runtime.trace) and CSRF not in str(runtime.trace)
    assert not list(tmp_path.iterdir())


def test_inline_container_probe_redacts_transport_exception(monkeypatch, capsys):
    class Opener:
        def open(self, *_args, **_kwargs):
            raise urllib.error.URLError(COOKIE + CSRF)

    monkeypatch.setattr(smoke.urllib.request, "build_opener", lambda *_args: Opener())
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"method": "POST", "path": "/api/saas/auth/login",
        "body": {"password": CSRF}, "headers": {"Cookie": COOKIE}})))
    exec(compile(smoke.HTTP_PROBE, "synthetic-http-probe", "exec"), {})
    output = capsys.readouterr().out
    assert json.loads(output) == {"status": 0, "body": "Transport failed", "headers": {}}
    assert COOKIE not in output and CSRF not in output


def test_local_private_transport_error_is_fixed_and_does_not_create_logs(tmp_path, monkeypatch):
    class Opener:
        def open(self, *_args, **_kwargs):
            raise urllib.error.URLError(COOKIE + CSRF)

    monkeypatch.setattr(smoke.urllib.request, "build_opener", lambda *_args: Opener())
    runtime = smoke.LocalRuntime(tmp_path)
    try:
        with pytest.raises(smoke.SmokeError, match="^Local HTTP probe failed$"):
            runtime.transport((None, 8765))("GET", "/api/saas/session", headers={"Cookie": COOKIE, "X-CSRF-Token": CSRF})
        assert not list(tmp_path.iterdir())
    finally:
        runtime.temporary.cleanup()


def create_database(path, marker):
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE synthetic_recovery (marker TEXT NOT NULL)")
        db.execute("INSERT INTO synthetic_recovery VALUES (?)", (marker,))


@pytest.fixture
def snapshot(tmp_path):
    source = fixture.initialize_fixture_root(tmp_path / "source-root", TOKEN)
    target = fixture.initialize_fixture_root(tmp_path / "target-root", TOKEN)
    store = fixture.prepare_fixture_store(source, "source", TOKEN)
    create_database(store / "control.db", "control")
    for index, organization in enumerate(("a" * 32, "b" * 32)):
        folder = store / "tenants" / organization
        create_database(folder / "content.db", f"synthetic-organization-{index}")
        (folder / "vectors").mkdir()
        # These file-gate tests exercise archive integrity, not Qdrant quality.
        (folder / "vectors" / "meta.json").write_text(json.dumps({"synthetic": True, "dimension": 3}))
    saas_backup.create_backup(store, source / "checkpoint.zip", offline_confirm=True)
    return source, target


def file_op(root, operation, **extra):
    return smoke.file_operation({"root": str(root), "token": TOKEN, "operation": operation, **extra})


def test_owned_archive_verification_detects_corruption_without_changing_original(snapshot):
    source, target = snapshot
    before = file_op(source, "fingerprint", store="source")
    summary = file_op(source, "summary")
    assert summary["vector_organizations"] == 2 and summary["vector_files"] == 2
    assert file_op(source, "tamper") == {"tampered_vector_member": True}
    with pytest.raises(saas_backup.BackupError, match="哈希"):
        saas_backup.restore_backup(source / "tampered.zip", target / "rejected", offline_confirm=True)
    assert file_op(target, "absent") == {"absent": True}
    assert file_op(source, "fingerprint", store="source") == before
    assert file_op(source, "summary") == summary
    assert not list(target.glob(".saas-restore-*"))


def test_restored_manifest_comparison_and_original_fingerprint_detect_changes(snapshot):
    source, target = snapshot
    before = file_op(source, "fingerprint", store="source")
    saas_backup.restore_backup(source / "checkpoint.zip", target / "restored", offline_confirm=True)
    assert file_op(target, "matches_manifest", source_root=str(source))["vector_files"] == 2
    assert file_op(source, "fingerprint", store="source") == before
    with sqlite3.connect(target / "restored" / "control.db") as db:
        db.execute("UPDATE synthetic_recovery SET marker='changed synthetic value'")
    with pytest.raises(smoke.SmokeError, match="hash differs"):
        file_op(target, "matches_manifest", source_root=str(source))
    assert file_op(source, "fingerprint", store="source") == before
    with sqlite3.connect(source / "source" / "control.db") as db:
        db.execute("UPDATE synthetic_recovery SET marker='newer source'")
    assert file_op(source, "fingerprint", store="source") != before


def test_file_operations_require_matching_owner_and_fixed_store(snapshot):
    source, target = snapshot
    with pytest.raises(fixture.FixtureError):
        smoke.file_operation({"root": str(source), "token": "c" * 32, "operation": "summary"})
    with pytest.raises(smoke.SmokeError, match="Invalid store"):
        file_op(source, "fingerprint", store="../target-root")
    (target / "source").symlink_to(source / "source", target_is_directory=True)
    with pytest.raises(smoke.SmokeError, match="directory"):
        file_op(target, "fingerprint", store="source")


def test_manifest_comparison_refuses_a_restored_directory_redirect(snapshot):
    source, target = snapshot
    redirected = source / "separate-synthetic-store"
    saas_backup.restore_backup(source / "checkpoint.zip", redirected, offline_confirm=True)
    (target / "restored").symlink_to(redirected, target_is_directory=True)
    with pytest.raises(smoke.SmokeError):
        file_op(target, "matches_manifest", source_root=str(source))


def test_broken_rejected_directory_link_does_not_count_as_absent(snapshot):
    _source, target = snapshot
    (target / "rejected").symlink_to(target / "nonexistent-synthetic-target", target_is_directory=True)
    assert file_op(target, "absent") == {"absent": False}


def test_bad_internal_file_operation_reports_only_fixed_error(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"root": str(tmp_path / COOKIE), "token": CSRF,
                                                           "operation": "summary"})))
    assert smoke.main(["--internal-operation"]) == 2
    output = capsys.readouterr().out
    assert json.loads(output) == {"error": "FIXTURE_FILE_OPERATION_FAILED"}
    assert COOKIE not in output and CSRF not in output


def test_missing_docker_replaces_prior_pass_report_with_explicit_failure(tmp_path, monkeypatch, capsys):
    output = tmp_path / "report.json"
    output.write_text('{"status":"passed","stages":{"old":{"passed":true}}}')
    monkeypatch.setattr(container_smoke.shutil, "which", lambda _name: None)
    assert smoke.main(["--runtime", "docker", "--output", str(output)]) == 2
    report = json.loads(output.read_text())
    assert report["status"] == "failed" and report["stages"] == {}
    assert "Docker CLI is unavailable" in report["error"]
    assert report["cost"]["provider_invoice_amount"] is None
    assert report["cleanup"]["errors"] == []
    assert json.loads(capsys.readouterr().out)["status"] == "failed"


def test_main_error_receipt_does_not_serialize_private_exception_payload(tmp_path, monkeypatch, capsys):
    output = tmp_path / "report.json"

    def fail(*_args):
        raise RuntimeError(COOKIE + CSRF)

    monkeypatch.setattr(smoke, "execute", fail)
    assert smoke.main(["--runtime", "docker", "--output", str(output)]) == 2
    report = json.loads(output.read_text())
    assert report["status"] == "failed" and report["error"] == "RuntimeError"
    recorded = output.read_text() + capsys.readouterr().out
    assert COOKIE not in recorded and CSRF not in recorded
