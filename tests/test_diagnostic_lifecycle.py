"""Diagnostic lifetime/schema integration with private temporary stores only.

No dotenv, real data, network model or persistent application configuration.
The filesystem failure cases target only this test's already-open JSONL FD.
"""
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import errno
import json
import logging
import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core import diagnostic_schema as schema
from app.core import diagnostic_store as stores
from app.core import diagnostics
from app.db.runtime_lock import saas_data_runtime_lock


SECRET = "SYNTHETIC-DIAGNOSTIC-PRIVATE-784cd9"
REQUEST_ID = "a3" * 16


@pytest.fixture(autouse=True)
def diagnostic_environment(monkeypatch):
    # Assert cleanup instead of resetting process globals and hiding leaked owners.
    assert diagnostics._active_store is None
    assert not diagnostics._active_lifetimes
    for key in ("DIAGNOSTIC_LOG_DIR", "DIAGNOSTIC_LOG_MAX_BYTES", "DIAGNOSTIC_LOG_BACKUP_COUNT"):
        monkeypatch.delenv(key, raising=False)
    yield
    assert diagnostics._active_store is None
    assert not diagnostics._active_lifetimes


@pytest.fixture
def logs(tmp_path, monkeypatch):
    directory = tmp_path / "private-logs"
    directory.mkdir(mode=0o700)
    monkeypatch.setenv("DIAGNOSTIC_LOG_DIR", str(directory))
    return directory


@pytest.fixture
def saas_main(tmp_path, monkeypatch):
    monkeypatch.setenv("SAAS_MODE", "true")
    monkeypatch.setenv("SAAS_PUBLIC_ORIGIN", "http://testserver")
    monkeypatch.setenv("SAAS_SECURE_COOKIE", "false")
    monkeypatch.setenv("SAAS_DATA_DIR", str(tmp_path / "private-saas"))
    monkeypatch.setenv("RAG_RETRIEVAL_MODE", "lexical")
    from app import main
    from app.agent_core.vector_store import close_vector_stores
    from app.db.session import close_tenant_stores
    from app.saas.store import close_control_db
    close_vector_stores()
    close_tenant_stores()
    close_control_db()
    yield main, tmp_path / "private-saas"
    close_vector_stores()
    close_tenant_stores()
    close_control_db()


def records(directory):
    return [json.loads(line) for path in sorted(directory.glob("diagnostics.jsonl*"))
            for line in path.read_text(encoding="utf-8").splitlines()]


def emit(event="http_response", **kwargs):
    with diagnostics.request_diagnostic_context(REQUEST_ID):
        diagnostics.emit_diagnostic(event, route="/api/health", **kwargs)


def valid_record(**changes):
    record = {name: None for name in schema.FIELDS}
    record.update(schema_version=1, event="http_response", at=datetime.now(timezone.utc).isoformat(),
                  request_id=REQUEST_ID, http_status=200, method="GET", route="/api/items/{item_id}")
    return {**record, **changes}


def lightweight_app():
    @asynccontextmanager
    async def lifespan(app):
        with diagnostics.persistent_diagnostics():
            yield
    app = FastAPI(lifespan=lifespan)
    app.add_middleware(diagnostics.RequestDiagnosticsMiddleware)

    @app.get("/health")
    async def health():
        return {"status": "ok"}
    return app


def test_default_disabled_lifetimes_overlap_without_files_and_later_enable(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with diagnostics.persistent_diagnostics() as first:
        with diagnostics.persistent_diagnostics() as second:
            emit(http_status=200)
            assert first is second is None
            assert diagnostics.diagnostic_sink_status()["state"] == "disabled"
    assert list(tmp_path.iterdir()) == []
    directory = tmp_path / "logs"
    directory.mkdir(mode=0o700)
    monkeypatch.setenv("DIAGNOSTIC_LOG_DIR", str(directory))
    with diagnostics.persistent_diagnostics():
        emit(http_status=200)
    assert len(records(directory)) == 1


@pytest.mark.parametrize("kind", ["relative", "missing", "public", "symlink", "file"])
def test_configured_directory_must_be_existing_private_absolute_directory(tmp_path, monkeypatch, kind):
    path = tmp_path / "private-secret-location"
    if kind in {"relative", "public"}:
        path.mkdir(mode=0o700 if kind == "relative" else 0o755)
        if kind == "public":
            path.chmod(0o755)  # Independent of the test runner's umask.
    elif kind == "symlink":
        target = tmp_path / "target"
        target.mkdir(mode=0o700)
        path.symlink_to(target, target_is_directory=True)
    elif kind == "file":
        path.write_text(SECRET)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DIAGNOSTIC_LOG_DIR", path.name if kind == "relative" else str(path))
    with pytest.raises(diagnostics.DiagnosticConfigurationError) as error:
        with diagnostics.persistent_diagnostics():
            pytest.fail("Unsafe configuration must refuse startup")
    assert str(path) not in str(error.value) and SECRET not in str(error.value)
    assert "private-secret-location" not in str(error.value)
    assert not list(tmp_path.rglob(stores.LOG_NAME))
    if kind == "missing":
        assert not path.exists()


@pytest.mark.parametrize("second", ["same", "disabled", "different"])
def test_overlapping_enabled_lifetime_cannot_replace_or_close_owner(logs, tmp_path, monkeypatch, second):
    other = tmp_path / "other-logs"
    other.mkdir(mode=0o700)
    with diagnostics.persistent_diagnostics() as owner:
        emit(http_status=200)
        monkeypatch.setenv("DIAGNOSTIC_LOG_DIR", {"same": str(logs), "disabled": "", "different": str(other)}[second])
        with pytest.raises(diagnostics.DiagnosticConfigurationError, match="已有应用实例占用"):
            with diagnostics.persistent_diagnostics():
                pytest.fail("A second lifetime must not inherit another app's logger")
        assert diagnostics._active_store is owner
        assert owner.status()["state"] == "healthy"
        emit(http_status=201)
        assert owner.status()["written"] == 2
    assert owner.status()["state"] == "closed"
    assert not list(other.iterdir())
    assert [item["http_status"] for item in records(logs)] == [200, 201]


def test_enable_is_refused_while_disabled_lifetime_exists_then_can_restart(logs, monkeypatch):
    monkeypatch.delenv("DIAGNOSTIC_LOG_DIR")
    with diagnostics.persistent_diagnostics():
        monkeypatch.setenv("DIAGNOSTIC_LOG_DIR", str(logs))
        with pytest.raises(diagnostics.DiagnosticConfigurationError):
            with diagnostics.persistent_diagnostics():
                pytest.fail("An existing unscoped lifetime must not inherit a new sink")
        assert not list(logs.iterdir())
    with diagnostics.persistent_diagnostics():
        emit(http_status=200)
    assert len(records(logs)) == 1


def test_configuration_failure_releases_data_lock_without_control_initialization(saas_main, monkeypatch, tmp_path):
    main, data = saas_main
    monkeypatch.setenv("DIAGNOSTIC_LOG_DIR", str(tmp_path / "missing-logs"))
    with pytest.raises(diagnostics.DiagnosticConfigurationError):
        with TestClient(main.app):
            pytest.fail("Missing log directory must refuse startup")
    assert not (data / "control.db").exists()
    with saas_data_runtime_lock(data):
        pass


def test_control_initialization_failure_releases_both_locks_and_allows_restart(saas_main, logs, monkeypatch):
    main, data = saas_main
    from app.saas import store
    real_init = store.init_control_db

    def partial_failure():
        real_init()
        raise RuntimeError("synthetic startup failure")
    monkeypatch.setattr(store, "init_control_db", partial_failure)
    with pytest.raises(RuntimeError, match="synthetic startup failure"):
        with TestClient(main.app):
            pytest.fail("Failed initialization must not serve")
    assert diagnostics.diagnostic_sink_status()["state"] == "disabled"
    with saas_data_runtime_lock(data):
        reopened = stores.DiagnosticStore(logs)
        reopened.close()
    monkeypatch.setattr(store, "init_control_db", real_init)
    with TestClient(main.app) as client:
        assert client.get("/api/health").status_code == 200


def test_sink_stays_open_through_all_application_store_cleanup(saas_main, logs, monkeypatch):
    main, _data = saas_main
    from app.agent_core import vector_store
    from app.db import session
    from app.saas import store
    closed = []

    def close(name, original):
        def cleanup():
            assert diagnostics.diagnostic_sink_status()["state"] == "healthy"
            emit("workflow_finished", status="failed", agent_run_id=len(closed) + 1)
            closed.append(name)
            original()
        return cleanup
    monkeypatch.setattr(vector_store, "close_vector_stores", close("vectors", vector_store.close_vector_stores))
    monkeypatch.setattr(session, "close_tenant_stores", close("tenants", session.close_tenant_stores))
    monkeypatch.setattr(store, "close_control_db", close("control", store.close_control_db))
    with TestClient(main.app):
        pass
    assert closed == ["vectors", "tenants", "control"]
    assert len(records(logs)) == 3
    assert diagnostics.diagnostic_sink_status()["state"] == "disabled"


def test_two_real_testclients_refuse_overlap_but_restart_preserves_old_request_id(saas_main, logs, tmp_path, monkeypatch):
    main, first_data = saas_main
    handlers = tuple(diagnostics.LOGGER.handlers)
    second_data = tmp_path / "second-saas"
    with TestClient(main.app) as first:
        old = first.get("/api/health").headers["x-request-id"]
        # Different data root ensures the *diagnostic* lifetime refuses this
        # second instance, rather than the earlier SaaS data lock doing so.
        monkeypatch.setenv("SAAS_DATA_DIR", str(second_data))
        with pytest.raises(diagnostics.DiagnosticConfigurationError):
            with TestClient(main.app):
                pytest.fail("Second client must not replace the original sink")
        assert not (second_data / "control.db").exists()
        monkeypatch.setenv("SAAS_DATA_DIR", str(first_data))
        assert first.get("/api/health").status_code == 200
    with TestClient(main.app) as restarted:
        new = restarted.get("/api/health").headers["x-request-id"]
    assert new != old
    persisted_ids = [item["request_id"] for item in records(logs)]
    assert persisted_ids.count(old) == persisted_ids.count(new) == 1
    assert len(persisted_ids) == 3  # One record per actual response, without duplicate handlers.
    assert tuple(diagnostics.LOGGER.handlers) == handlers


def test_logger_failure_does_not_block_durable_record_or_business(logs, monkeypatch):
    def broken(*_args, **_kwargs):
        raise OSError(SECRET)
    monkeypatch.setattr(diagnostics.LOGGER, "log", broken)
    with TestClient(lightweight_app()) as client:
        response = client.get("/health")
        assert response.status_code == 200
        assert diagnostics.diagnostic_sink_status()["written"] == 1
    assert records(logs)[0]["request_id"] == response.headers["x-request-id"]
    assert SECRET not in (logs / stores.LOG_NAME).read_text()


def test_plain_logger_records_are_not_persisted_and_foreign_handler_survives(logs):
    handler = logging.NullHandler()
    diagnostics.LOGGER.addHandler(handler)
    try:
        with diagnostics.persistent_diagnostics():
            diagnostics.configure_diagnostics_logging()
            diagnostics.LOGGER.info(SECRET)
            emit(http_status=200)
        assert handler in diagnostics.LOGGER.handlers
        assert len(records(logs)) == 1
        assert SECRET not in (logs / stores.LOG_NAME).read_text()
    finally:
        diagnostics.LOGGER.removeHandler(handler)


def test_append_false_emits_one_fixed_notice_without_changing_business(logs, monkeypatch, capsys):
    with TestClient(lightweight_app()) as client:
        owner = diagnostics._active_store
        monkeypatch.setattr(owner, "append", lambda _payload: False)
        monkeypatch.setattr(owner, "status", lambda: {"state": "degraded", "written": 0,
                                                     "dropped": 1, "error_code": "WRITE_FAILED"})
        for _ in range(2):
            assert client.get("/health").status_code == 200
    errors = capsys.readouterr().err
    assert errors.count("DIAGNOSTIC_SINK_DEGRADED") == 1 and SECRET not in errors


def test_close_failure_emits_fixed_notice_and_releases_lifecycle(logs, monkeypatch, capsys):
    with diagnostics.persistent_diagnostics() as owner:
        original = stores.os.close
        target_fd = owner._active_fd

        def close_then_fail(descriptor):
            original(descriptor)
            if descriptor == target_fd:
                raise OSError(SECRET)

        monkeypatch.setattr(stores.os, "close", close_then_fail)
    assert owner.status()["error_code"] == "CLOSE_FAILED"
    assert diagnostics.diagnostic_sink_status()["state"] == "disabled"
    errors = capsys.readouterr().err
    assert errors.count("DIAGNOSTIC_SINK_DEGRADED") == 1 and SECRET not in errors
    monkeypatch.setattr(stores.os, "close", original)
    with diagnostics.persistent_diagnostics():
        emit(http_status=200)
    assert len(records(logs)) == 1


@pytest.mark.parametrize("operation", ["write", "fsync"])
def test_real_targeted_filesystem_failure_stops_writes_and_keeps_http_result(logs, monkeypatch, capsys, operation):
    with TestClient(lightweight_app()) as client:
        owner = diagnostics._active_store
        target_fd = owner._active_fd
        original = getattr(stores.os, operation)
        failed_calls = []

        def fail_target(descriptor, *args):
            if descriptor == target_fd:
                failed_calls.append(descriptor)
                raise OSError(errno.ENOSPC, SECRET)
            return original(descriptor, *args)
        monkeypatch.setattr(stores.os, operation, fail_target)
        failed = client.get("/health")
        assert failed.status_code == 200 and failed.headers["x-request-id"]
        before = (logs / stores.LOG_NAME).read_bytes()
        assert client.get("/health").status_code == 200
        assert (logs / stores.LOG_NAME).read_bytes() == before
        state = diagnostics.diagnostic_sink_status()
        assert state == {"state": "degraded", "written": 0, "dropped": 2, "error_code": "WRITE_FAILED"}
        assert len(failed_calls) == 1
        with pytest.raises(stores.StoreError):
            stores.DiagnosticStore(logs)
    errors = capsys.readouterr().err
    assert errors.count("DIAGNOSTIC_SINK_DEGRADED") == 1
    assert SECRET not in errors and SECRET.encode() not in before
    # Restore syscall before reopening: the previous owner has released its FD.
    monkeypatch.setattr(stores.os, operation, original)
    reopened = stores.DiagnosticStore(logs)
    reopened.close()


@pytest.mark.parametrize("event", sorted(schema.EVENTS))
def test_all_existing_event_types_retain_schema_and_context(logs, event):
    with diagnostics.persistent_diagnostics():
        emit(event, agent_run_id=7, agent_step_id=8, workflow_attempt=2, status="failed",
             step_key="retrieve_context", code="STEP_FAILED", error_type="RuntimeError",
             http_status=503, method="POST", elapsed_ms=12.4, usage_outcome="unknown")
    record, = records(logs)
    assert schema.validate_record(record) == record
    assert set(record) == schema.FIELDS and record["schema_version"] == 1
    assert record["event"] == event and record["request_id"] == REQUEST_ID
    assert record["agent_run_id"] == 7 and record["workflow_attempt"] == 2


@pytest.mark.parametrize("changes", [
    {"extra_private_payload": SECRET}, {"event": SECRET}, {"schema_version": True}, {"schema_version": 1.0},
    {"schema_version": 2}, {"status": SECRET}, {"code": SECRET}, {"error_type": SECRET},
    {"method": SECRET}, {"usage_outcome": SECRET}, {"step_key": SECRET},
    {"request_id": SECRET}, {"organization_id": SECRET}, {"agent_run_id": True}, {"agent_step_id": -1},
    {"workflow_attempt": 2 ** 63}, {"http_status": True}, {"elapsed_ms": float("nan")},
    {"elapsed_ms": float("inf")}, {"at": "2026-10-04T00:00:00"},
    {"route": "https://example.invalid/" + SECRET}, {"route": "//example.invalid/" + SECRET},
    {"route": "/api/health?token=" + SECRET}, {"route": "/api/health#" + SECRET},
    {"route": "/api/health\n" + SECRET}, {"route": "/api/health\x00" + SECRET},
])
def test_invalid_record_is_rejected_without_persisting_any_untrusted_fields(logs, changes):
    record = valid_record(**changes)
    assert schema.validate_record(record) is None
    with diagnostics.persistent_diagnostics() as owner:
        assert owner.append(record) is False
        assert owner.status()["error_code"] == "INVALID_RECORD"
    assert records(logs) == []


@pytest.mark.parametrize("route", [None, "<unmatched>", "/", "/api/items/{item_id}", "/assets/{path:path}"])
def test_supported_registered_route_shapes_remain_valid(route):
    record = valid_record(route=route)
    assert schema.validate_record(record) == record


def test_http_routes_never_persist_request_body_path_query_or_forged_id(logs):
    with TestClient(lightweight_app()) as client:
        response = client.post(f"/missing/{SECRET}?token={SECRET}",
            headers={"x-request-id": "f" * 32, "authorization": SECRET, "cookie": SECRET},
            json={"prompt": SECRET, "content": SECRET})
    assert response.status_code == 404
    record, = records(logs)
    assert record["route"] == "<unmatched>"
    assert record["request_id"] == response.headers["x-request-id"] != "f" * 32
    assert SECRET not in (logs / stores.LOG_NAME).read_text()


def test_emitter_drops_invalid_enums_and_routes_from_stream_and_file(logs, monkeypatch):
    emitted = []
    monkeypatch.setattr(diagnostics.LOGGER, "log", lambda _level, value: emitted.append(json.loads(value)))
    with diagnostics.persistent_diagnostics():
        diagnostics.emit_diagnostic("http_failed", route="https://private.invalid/" + SECRET,
            status=SECRET, code=SECRET, error_type=SECRET, step_key=SECRET, method=SECRET, usage_outcome=SECRET)
        diagnostics.emit_diagnostic(SECRET, route="/api/health")
    assert len(emitted) == 1 and len(records(logs)) == 1
    assert SECRET not in json.dumps(emitted) + json.dumps(records(logs))
    for field in ("route", "status", "code", "error_type", "step_key", "method", "usage_outcome"):
        assert emitted[0][field] is None
