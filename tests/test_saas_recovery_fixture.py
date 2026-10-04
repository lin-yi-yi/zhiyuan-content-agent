"""Synthetic recovery launcher tests: temporary data, real HTTP/SQLite/Qdrant.

No real .env, model cache, account, external network or paid model is used.
Application imports occur only in the isolated server subprocess.
"""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import time
import zipfile

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import saas_recovery_fixture as fixture
import saas_backup

TOKEN = "a1" * 16
PASSWORD = "Recovery-Synthetic-Test-Only-0123"
SCRIPT = ROOT / "scripts" / "saas_recovery_fixture.py"


def command(root, *extra):
    return [sys.executable, str(SCRIPT), "--fixture-root", str(root), "--fixture-token", TOKEN, *extra]


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@contextmanager
def service(root, store="source"):
    port = free_port()
    origin = f"http://127.0.0.1:{port}"
    env = dict(os.environ, DEEPSEEK_API_KEY="never-use-fixture-secret",
               MODEL_PRICING_JSON='[{"private":"never-use-pricing"}]',
               DATABASE_URL=f"sqlite:///{root.parent / 'must-not-create.db'}")
    process = subprocess.Popen(command(root, "--store-name", store, "--port", str(port)),
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=ROOT, env=env)
    try:
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            if process.poll() is not None:
                output = process.communicate(timeout=3)
                pytest.fail(f"Fixture exited before startup: {output}")
            try:
                result = httpx.get(origin + "/health", timeout=.5, trust_env=False)
                if result.status_code == 200:
                    break
            except httpx.TransportError:
                pass
            time.sleep(.05)
        else:
            pytest.fail("Fixture did not start before timeout")
        with httpx.Client(base_url=origin, headers={"Origin": origin}, timeout=15, trust_env=False) as client:
            yield client, process
    finally:
        if process.poll() is None:
            process.terminate()
        try:
            stdout, stderr = process.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate(timeout=3)
            pytest.fail("Fixture failed to stop cleanly")
        assert "never-use-fixture-secret" not in stdout + stderr
        assert "never-use-pricing" not in stdout + stderr
        assert TOKEN not in stdout + stderr
        assert "Application shutdown complete." in stdout + stderr


def probe(client):
    response = client.get("/__recovery__/isolation", headers={"X-Recovery-Fixture-Token": TOKEN})
    assert response.status_code == 200, response.text
    return response.json()


def test_ownership_claims_only_empty_or_missing_root_and_retains_digest(tmp_path):
    root = tmp_path / "fixture"
    assert fixture.initialize_fixture_root(root, TOKEN) == root.resolve()
    marker = root / fixture.OWNER_FILE
    data = json.loads(marker.read_text())
    assert data == {"format": fixture.OWNER_FORMAT, "token_sha256": hashlib.sha256(TOKEN.encode()).hexdigest()}
    assert TOKEN not in marker.read_text()
    assert marker.stat().st_mode & 0o777 == 0o600
    assert list(root.iterdir()) == [marker]
    assert fixture.initialize_fixture_root(root, TOKEN) == root.resolve()
    assert fixture.require_fixture_root(root, TOKEN) == root.resolve()
    store = fixture.prepare_fixture_store(root, "restored", TOKEN)
    (store / "synthetic-data").write_text("Only synthetic fixture data")
    assert fixture.prepare_fixture_store(root, "restored", TOKEN) == store


def test_require_never_claims_or_creates_and_refuses_unowned_nonempty(tmp_path):
    root = tmp_path / "fixture"
    with pytest.raises(fixture.FixtureError):
        fixture.require_fixture_root(root, TOKEN)
    assert not root.exists()
    root.mkdir()
    existing = root / "existing.db"
    existing.write_bytes(b"preserve existing data")
    with pytest.raises(fixture.FixtureError):
        fixture.initialize_fixture_root(root, TOKEN)
    assert existing.read_bytes() == b"preserve existing data"
    assert not (root / fixture.OWNER_FILE).exists()


@pytest.mark.parametrize("token", ["", "a" * 31, "a" * 33, "AB" * 16, "z1" * 16, "a" * 32 + "\n"])
def test_invalid_token_cannot_create_root(tmp_path, token):
    root = tmp_path / "fixture"
    with pytest.raises(fixture.FixtureError):
        fixture.initialize_fixture_root(root, token)
    assert not root.exists()


def test_wrong_token_cannot_open_or_create_store(tmp_path):
    root = fixture.initialize_fixture_root(tmp_path / "fixture", TOKEN)
    with pytest.raises(fixture.FixtureError):
        fixture.prepare_fixture_store(root, "source", "b2" * 16)
    assert not (root / "source").exists()


@pytest.mark.parametrize("bad_marker", ["fifo", "directory", "oversized", "invalid-digest", "malformed"])
def test_nonregular_or_malformed_marker_is_rejected_without_reading_data(tmp_path, bad_marker):
    root = tmp_path / "fixture"
    root.mkdir()
    marker = root / fixture.OWNER_FILE
    if bad_marker == "fifo":
        os.mkfifo(marker)
    elif bad_marker == "directory":
        marker.mkdir()
    elif bad_marker == "oversized":
        marker.write_text("x" * 1025)
    elif bad_marker == "invalid-digest":
        marker.write_text(json.dumps({"format": fixture.OWNER_FORMAT, "token_sha256": "非ASCII"}))
    else:
        marker.write_text("{")
    with pytest.raises(fixture.FixtureError):
        fixture.require_fixture_root(root, TOKEN)


@pytest.mark.parametrize("entry", ["root", "marker", "store", "database", "directory", "hardlink"])
def test_links_cannot_redirect_fixture_data(tmp_path, entry):
    root = tmp_path / "fixture"
    outside = tmp_path / "outside"
    outside.mkdir()
    if entry == "root":
        root.symlink_to(outside, target_is_directory=True)
        with pytest.raises(fixture.FixtureError):
            fixture.initialize_fixture_root(root, TOKEN)
        assert not list(outside.iterdir())
        return
    fixture.initialize_fixture_root(root, TOKEN)
    if entry == "marker":
        marker = root / fixture.OWNER_FILE
        target = outside / "marker"
        marker.rename(target)
        marker.symlink_to(target)
        with pytest.raises(fixture.FixtureError):
            fixture.require_fixture_root(root, TOKEN)
        return
    store = root / "source"
    if entry == "store":
        store.symlink_to(outside, target_is_directory=True)
    else:
        store.mkdir()
        if entry == "directory":
            (store / "tenants").symlink_to(outside, target_is_directory=True)
        else:
            target = outside / "database"
            target.write_bytes(b"existing")
            if entry == "database":
                (store / "control.db").symlink_to(target)
            else:
                os.link(target, store / "control.db")
    with pytest.raises(fixture.FixtureError):
        fixture.prepare_fixture_store(root, "source", TOKEN)


@pytest.mark.parametrize("store", ["../outside", "source/child", "", "/tmp", "unexpected"])
def test_store_names_cannot_escape_parent(tmp_path, store):
    root = fixture.initialize_fixture_root(tmp_path / "fixture", TOKEN)
    with pytest.raises(fixture.FixtureError):
        fixture.prepare_fixture_store(root, store, TOKEN)
    assert len(list(root.iterdir())) == 1


def test_environment_resets_inherited_keys_prices_paths_and_tracing(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "environ", {"DEEPSEEK_API_KEY": "secret", "LANGCHAIN_HANDLER": "remote",
                                      "OTEL_EXPORTER_OTLP_ENDPOINT": "https://invalid.test",
                                      "MODEL_PRICING_JSON": "secret", "SAAS_DATA_DIR": "/do/not/use"})
    fixture.configure_fixture_environment(tmp_path, 8765)
    assert os.environ["SAAS_DATA_DIR"] == str(tmp_path)
    assert os.environ["DATABASE_URL"] == "sqlite:///:memory:"
    assert os.environ["PYTHON_DOTENV_DISABLED"] == "1"
    assert os.environ["DEEPSEEK_API_KEY"] == ""
    assert os.environ["MODEL_PRICING_JSON"] == "[]"
    assert os.environ["DEFAULT_LLM_PROVIDER"] == "local"
    assert os.environ["LANGCHAIN_TRACING_V2"] == "false"
    assert os.environ["OTEL_SDK_DISABLED"] == "true"
    assert "OTEL_EXPORTER_OTLP_ENDPOINT" not in os.environ
    assert "LANGCHAIN_HANDLER" not in os.environ
    assert "secret" not in os.environ.values()


def test_outbound_guard_denies_connect_dns_udp_but_retains_incoming_socket_operations():
    state = fixture.IsolationState()
    original = socket.socket.connect
    with fixture.deny_outbound_connections(state):
        with socket.socket() as server:
            server.bind(("127.0.0.1", 0))
            server.listen(1)
            with pytest.raises(PermissionError):
                server.connect(("127.0.0.1", 1))
            with pytest.raises(PermissionError):
                server.connect_ex(("192.0.2.1", 443))
        with pytest.raises(PermissionError):
            socket.create_connection(("192.0.2.1", 443))
        with pytest.raises(PermissionError):
            socket.getaddrinfo("must-not-resolve.example.invalid", 443)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
            with pytest.raises(PermissionError):
                udp.sendto(b"synthetic", ("192.0.2.1", 53))
    assert state.outbound_connection_attempts == 5
    assert socket.socket.connect is original


def test_initialize_cli_does_not_import_application_or_open_business_store(tmp_path):
    root = tmp_path / "fixture"
    source = ("import runpy, sys; f = runpy.run_path(sys.argv[1]); "
              "assert f['main'](sys.argv[2:]) == 0; "
              "assert not any(m == 'app' or m.startswith('app.') for m in sys.modules)")
    result = subprocess.run([sys.executable, "-c", source, str(SCRIPT), "--fixture-root", str(root),
                             "--fixture-token", TOKEN, "--initialize-only"], cwd=ROOT,
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"initialized": True, "fixture_mode": "synthetic_recovery"}
    assert [p.name for p in root.iterdir()] == [fixture.OWNER_FILE]


def test_cli_refuses_unowned_data_without_exposing_path_or_token(tmp_path):
    root = tmp_path / "private-existing-data"
    root.mkdir()
    (root / "preserve.txt").write_text("existing")
    result = subprocess.run(command(root, "--initialize-only"), cwd=ROOT, capture_output=True, text=True, timeout=10)
    assert result.returncode == 1
    assert str(root) not in result.stdout + result.stderr
    assert TOKEN not in result.stdout + result.stderr
    assert (root / "preserve.txt").read_text() == "existing"


def test_real_http_saas_qdrant_and_stopped_recovery_are_isolated(tmp_path):
    root = tmp_path / "fixture"
    archive = tmp_path / "synthetic-checkpoint.zip"
    marker = "ORG-RECOVERY-SYNTHETIC"
    with service(root) as (client, _process):
        assert client.get("/health").json()["scope"] == "saas-single-host-pilot"
        assert client.get("/api/knowledge/documents").status_code == 401
        assert client.get("/__recovery__/isolation").status_code == 404
        assert client.get("/__recovery__/isolation", headers={"X-Recovery-Fixture-Token": "b2" * 16}).status_code == 404
        assert client.get("/__recovery__/isolation", headers={"X-Recovery-Fixture-Token": TOKEN + "extra"}).status_code == 404
        initial = probe(client)
        assert initial["health_mode"] == "saas"
        assert initial["process_uid"] == os.getuid()
        assert initial["tenant_count"] == 0
        assert initial["dotenv_disabled"] is True and initial["pricing_configured"] is False
        # Prove the real app's service lock, before a Qdrant lock even exists.
        with pytest.raises(saas_backup.BackupError):
            saas_backup.create_backup(root / "source", archive, offline_confirm=True)
        assert not archive.exists()
        account = client.post("/api/saas/auth/register", json={"name": "Synthetic recovery owner",
            "email": "recovery@example.invalid", "password": PASSWORD, "organization_name": "Synthetic recovery"})
        assert account.status_code == 201, account.text
        state = account.json()
        organization = state["active_organization_id"]
        client.headers.update({"X-CSRF-Token": state["csrf_token"], "X-Organization-ID": organization})
        uploaded = client.post("/api/knowledge/documents", json={"title": marker,
            "content": marker + " — " + "Synthetic recovery data only. " * 5, "format": "text"})
        assert uploaded.status_code == 201, uploaded.text
        metadata = uploaded.json()["document"]["metadata"]
        assert metadata["embedding_provider"] == fixture.SYNTHETIC_PROVIDER
        assert metadata["embedding_model"] == fixture.SYNTHETIC_MODEL
        assert metadata["vector_collection"].endswith("_3")
        for mode in ("semantic", "hybrid"):
            result = client.post("/api/v04/rag/search", json={"query": marker, "retrieval_mode": mode})
            assert result.status_code == 200 and result.json()["total"] > 0, result.text
            assert marker in result.text
        chatted = client.post("/api/models/chat", json={"provider": "local", "user_prompt": "Synthetic only"})
        assert chatted.status_code == 200, chatted.text
        remote = client.post("/api/models/chat", json={"provider": "deepseek", "user_prompt": "Never submit externally"})
        assert remote.status_code == 409
        current = probe(client)
        assert current["tenant_count"] == 1 and current["local_model_run_count"] == 1
        assert current["total_external_calls"] == current["outbound_connection_attempts"] == 0
        assert current["embedding_dimension"] == 3 and current["semantic_quality_evaluated"] is False
        assert current["vector_stores"] == [{"organization_id": organization,
            "relative_path": f"tenants/{organization}/vectors", "opened": True}]
        serialized = json.dumps(current)
        assert str(root) not in serialized and TOKEN not in serialized and PASSWORD not in serialized
        assert "recovery@example.invalid" not in serialized
        with pytest.raises(saas_backup.BackupError):
            saas_backup.create_backup(root / "source", archive, offline_confirm=True)
        assert not archive.exists()
    assert not (tmp_path / "must-not-create.db").exists()
    assert not (root / "source" / "unused-model-cache").exists()
    assert not (root / "source" / "unused-vectors").exists()
    assert not (root / "source" / "legacy-disabled.db").exists()
    saas_backup.create_backup(root / "source", archive, offline_confirm=True)
    with zipfile.ZipFile(archive) as bundle:
        assert fixture.OWNER_FILE not in bundle.namelist()
        assert any(name.startswith(f"tenants/{organization}/vectors/") for name in bundle.namelist())
    saas_backup.restore_backup(archive, root / "restored", offline_confirm=True)
    with service(root, "restored") as (client, _process):
        logged_in = client.post("/api/saas/auth/login", json={"email": "recovery@example.invalid", "password": PASSWORD})
        assert logged_in.status_code == 200, logged_in.text
        state = logged_in.json()
        client.headers.update({"X-CSRF-Token": state["csrf_token"], "X-Organization-ID": organization})
        for mode in ("semantic", "hybrid"):
            result = client.post("/api/v04/rag/search", json={"query": marker, "retrieval_mode": mode})
            assert result.status_code == 200 and result.json()["total"] > 0, result.text
            assert marker in result.text
        restored = probe(client)
        assert restored["tenant_count"] == restored["local_model_run_count"] == 1
        assert restored["total_external_calls"] == restored["outbound_connection_attempts"] == 0
        assert restored["vector_stores"] == current["vector_stores"]
    with sqlite3.connect(root / "restored" / "control.db") as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
