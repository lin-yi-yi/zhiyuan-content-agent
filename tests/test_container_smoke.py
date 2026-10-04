"""Container orchestration contracts only; these tests do not claim Docker ran."""
import importlib.util
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.api.routes import agent_runs, brands, delivery, drafts, evidence, knowledge, v04
from app.db.session import get_db
from app.llm.local import LocalRuleBasedClient
from app.services import content_growth_agent as workflow
from test_evidence_notes import database


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("container_smoke", ROOT / "scripts/container_smoke.py")
smoke = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(smoke)


@pytest.mark.parametrize("name", [".env", "frontend/.env.production", "backend/.env", "scripts/.env.saas",
    "../outside", "/absolute", "frontend/node_modules/pkg/index.js", "backend/__pycache__/x.pyc",
    ".data/demo.db", "backend/demo.db", "docs/private.key", "docs/raw.log", "output/report.json"])
def test_build_context_excludes_secrets_private_data_and_non_source_paths(name):
    assert not smoke.context_file_allowed(name)


@pytest.mark.parametrize("name", ["Dockerfile", ".dockerignore", "frontend/package-lock.json",
    "frontend/src/App.tsx", "backend/app/main.py", "scripts/local_backup.py", "docs/README.md"])
def test_build_context_keeps_actual_dockerfile_inputs(name):
    assert smoke.context_file_allowed(name)


def test_candidate_context_uses_current_source_without_opening_excluded_files(tmp_path, monkeypatch):
    root = tmp_path / "repository"
    (root / "frontend").mkdir(parents=True)
    (root / "Dockerfile").write_text("FROM scratch\n")
    (root / "frontend/index.html").write_text("synthetic current source")
    # A broken alias acts as a no-read sentinel; excluded configuration is never opened.
    (root / "frontend/.env.local").symlink_to(tmp_path / "must-not-read")
    monkeypatch.setattr(smoke, "ROOT", root)
    monkeypatch.setattr(smoke, "git", lambda *args: b"a" * 40 if args[0] == "rev-parse" else b"Dockerfile\0frontend/index.html\0frontend/.env.local\0")
    result = smoke.prepare_context(tmp_path / "context")
    assert result["file_count"] == 2
    assert result["source"] == "working_tree"
    assert len(result["build_context_sha256"]) == 64
    assert not (tmp_path / "context/frontend/.env.local").exists()
    assert (tmp_path / "context/frontend/index.html").read_text() == "synthetic current source"


def test_baseline_archive_uses_committed_inputs_and_preserves_executable_mode(tmp_path, monkeypatch):
    root = tmp_path / "repository"
    (root / "scripts").mkdir(parents=True)
    (root / "Dockerfile").write_text("FROM scratch\n")
    script = root / "scripts/start.sh"
    script.write_text("#!/bin/sh\necho synthetic-baseline\n")
    script.chmod(0o755)
    (root / "scripts/.env").write_text("SYNTHETIC_SECRET=excluded\n")
    def git(*args):
        return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout.strip()
    git("init", "--quiet")
    git("add", ".")
    git("-c", "user.name=Synthetic test", "-c", "user.email=synthetic@example.com", "-c", "commit.gpgsign=false",
        "-c", "core.hooksPath=/dev/null", "commit", "--quiet", "-m", "synthetic baseline")
    commit = git("rev-parse", "HEAD")
    script.write_text("#!/bin/sh\necho synthetic-candidate\n")
    monkeypatch.setattr(smoke, "ROOT", root)
    baseline = smoke.prepare_context(tmp_path / "baseline", commit)
    candidate = smoke.prepare_context(tmp_path / "candidate")
    assert baseline["commit"] == candidate["commit"] == commit
    assert baseline["build_context_sha256"] != candidate["build_context_sha256"]
    assert "synthetic-baseline" in (tmp_path / "baseline/scripts/start.sh").read_text()
    assert "synthetic-candidate" in (tmp_path / "candidate/scripts/start.sh").read_text()
    assert (tmp_path / "baseline/scripts/start.sh").stat().st_mode & 0o111
    assert not (tmp_path / "baseline/scripts/.env").exists()
    assert not (tmp_path / "candidate/scripts/.env").exists()


def test_all_runtime_containers_are_networkless_volume_only_and_ignore_host_model_configuration(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-host-key-not-forwarded")
    monkeypatch.setenv("DATABASE_URL", "sqlite:////must-not-use.db")
    commands = []
    docker = smoke.Docker(tmp_path)
    monkeypatch.setattr(docker, "run", lambda *args, **kwargs: commands.append(args) or SimpleNamespace(stdout="{}", returncode=0))
    docker.container("app", "image:synthetic", "volume-primary", detach=True)
    docker.container("restore", "image:synthetic", "volume-new", backup_volume="volume-primary", command=("python", "scripts/local_backup.py", "restore"))
    for args in commands:
        assert args[args.index("--network") + 1] == "none"
        assert "--publish" not in args and "-p" not in args and "--env-file" not in args
        mounts = [args[index + 1] for index, item in enumerate(args) if item == "--mount"]
        assert all(value.startswith("type=volume,") for value in mounts)
        assert "synthetic-host-key-not-forwarded" not in " ".join(args)
        assert "DATABASE_URL=sqlite:////app/.data/demo.db" in args
        assert "PYTHON_DOTENV_DISABLED=1" in args and "RAG_RETRIEVAL_MODE=lexical" in args
        assert "DEFAULT_LLM_PROVIDER=local" in args and "OPENAI_API_KEY=" in args
    assert "type=volume,src=volume-primary,dst=/backup,readonly" in commands[1]


def test_cleanup_refuses_other_runs_resources_and_continues_cleaning_owned_ones(tmp_path, monkeypatch):
    docker = smoke.Docker(tmp_path)
    docker.volumes = ["ours", "not-ours"]
    removed = []
    def run(*args, **kwargs):
        if args[1] == "inspect":
            return SimpleNamespace(returncode=0, stdout=docker.token if args[-1] == "ours" else "other-owner", stderr="")
        removed.append(args[-1])
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    monkeypatch.setattr(docker, "run", run)
    result = docker.cleanup()
    assert removed == ["ours"]
    assert len(result["errors"]) == 1 and "not-ours" in result["errors"][0]


def test_missing_docker_writes_fresh_failure_report_instead_of_passing(tmp_path, monkeypatch):
    output = tmp_path / "report.json"
    output.write_text('{"status":"passed"}')
    monkeypatch.setattr(smoke.shutil, "which", lambda name: None)
    assert smoke.main(["--baseline-ref", "synthetic", "--output", str(output)]) == 2
    report = json.loads(output.read_text())
    assert report["status"] == "failed" and report["stages"] == {}
    assert "not run" in report["error"]
    assert report["cost"]["provider_invoice_amount"] is None


def test_real_api_workflow_verifier_and_edit_invalidation_without_docker(database, monkeypatch):
    application = FastAPI()
    for router in (evidence.router, v04.router, agent_runs.router, brands.router, delivery.router, drafts.router, knowledge.router):
        application.include_router(router, prefix="/api")
    def sessions():
        with database() as db:
            yield db
    application.dependency_overrides[get_db] = sessions
    monkeypatch.setattr(workflow, "SessionLocal", database)
    monkeypatch.setattr(LocalRuleBasedClient, "_log_run", lambda *args, **kwargs: None)
    material = next(item for item in json.loads(smoke.FIXTURE.read_text())["documents"] if item["key"] == "spec")
    with TestClient(application) as client:
        def transport(method, path, body):
            response = client.request(method, path, json=body)
            return {"status": response.status_code, "body": response.text}
        call = lambda method, path, body=None, expected=200: smoke.call_json(transport, method, path, body, expected)
        ids = smoke.seed_workflow(call, material)
        original, original_delivery = smoke.verify_workflow(call, ids, material)
        smoke.revise_and_approve(call, ids)
        updated, updated_delivery = smoke.verify_workflow(call, ids, material)
        assert updated["review_hash"] != original["review_hash"]
        assert updated["body_sha256"] != original["body_sha256"]
        assert updated["citations"] == original["citations"]
        assert smoke.EDIT_MARKER in updated_delivery["body_text"]
        assert smoke.EDIT_MARKER not in original_delivery["body_text"]


def test_verifier_rejects_missing_citations_instead_of_accepting_a_valid_looking_hash():
    run = {"status": "approved", "draft": {"body_text": "synthetic"}, "result_json": {"review": {"content_hash": "a" * 64}}}
    delivery = {"review": {"content_hash": "a" * 64}, "body_text": "synthetic", "markdown": "synthetic " + "a" * 64, "citations": []}
    call = lambda method, path: delivery if path.endswith("/delivery") else run
    with pytest.raises(smoke.SmokeError, match="no citations"):
        smoke.verify_workflow(call, {"run_id": 1}, {})
