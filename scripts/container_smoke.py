#!/usr/bin/env python3
"""Build and exercise a specific baseline -> candidate Docker upgrade/recovery pair.

Host dependencies: Python standard library, Git and a local Linux Docker engine.
Runtime containers have no external network, published ports or host bind mounts.
The synthetic reviewer is scripted; this is not customer or production acceptance.
"""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "scripts/fixtures/industrial_faq_synthetic.json"
OWNER_LABEL = "io.zhiyuan.container-smoke"
EDIT_MARKER = "【容器升级后合成改稿】仅验证版本与重审状态，不新增产品事实。"
HTTP_PROBE = r'''
import json, sys, urllib.request, urllib.error
data = json.load(sys.stdin)
body = None if data.get('body') is None else json.dumps(data['body']).encode('utf-8')
req = urllib.request.Request('http://127.0.0.1:8765' + data['path'], data=body,
    method=data['method'], headers={'Content-Type': 'application/json'})
try:
    result = urllib.request.urlopen(req, timeout=20)
except urllib.error.HTTPError as exc:
    result = exc
with result:
    print(json.dumps({'status': result.status, 'body': result.read().decode('utf-8')}))
'''


class SmokeError(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise SmokeError(message)


def runtime_environment(data_dir="/app/.data"):
    env = {
        "PYTHON_DOTENV_DISABLED": "1", "SAAS_MODE": "false", "APP_ENV": "local",
        "DIAGNOSTIC_LOG_DIR": "",
        "DATABASE_URL": f"sqlite:///{data_dir}/demo.db",
        "RAG_RETRIEVAL_MODE": "lexical", "RAG_VECTOR_PATH": f"{data_dir}/qdrant",
        "RAG_EMBEDDING_CACHE_DIR": f"{data_dir}/models", "RAG_EMBEDDING_API_KEY": "",
        "AIHOT_ENABLED": "false", "GITHUB_ENABLED": "false", "MODEL_PRICING_JSON": "[]",
        "LANGCHAIN_HANDLER": "", "BACKEND_CORS_ORIGINS": "http://127.0.0.1:8765",
    }
    for provider in ("DEEPSEEK", "QWEN", "DOUBAO", "KIMI", "OPENAI"):
        env[f"{provider}_API_KEY"] = ""
    for namespace in ("LANGSMITH", "LANGCHAIN"):
        env.update({f"{namespace}_TRACING": "false", f"{namespace}_TRACING_V2": "false", f"{namespace}_API_KEY": ""})
    for task in ("DEFAULT_LLM", "TOPIC_SCORE", "DRAFT_GENERATION", "CARD_GENERATION", "COMPLIANCE_CHECK"):
        env[f"{task}_PROVIDER"], env[f"{task}_MODEL"] = "local", "local-rule-based-v0"
    return env


def context_file_allowed(name):
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in name.split("/")):
        return False
    if any(part.startswith(".env") or part in {".git", ".data", ".venv", "node_modules", "__pycache__", "dist"}
           for part in path.parts):
        return False
    if path.suffix in {".db", ".sqlite", ".sqlite3", ".log", ".pyc", ".zip", ".pem", ".key"} or name.endswith(("-wal", "-shm", ".runtime.lock")):
        return False
    return name in {"Dockerfile", ".dockerignore"} or path.parts[0] in {"backend", "frontend", "scripts", "docs"}


def git(*args):
    result = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, check=True)
    return result.stdout


def prepare_context(destination, baseline_ref=None):
    """Copy only publishable build inputs; never open filesystem .env or data files."""
    destination.mkdir()
    revision = git("rev-parse", "--verify", "--end-of-options", f"{baseline_ref or 'HEAD'}^{{commit}}").decode().strip()
    require(bool(re.fullmatch(r"[0-9a-f]{40,64}", revision)), "Git did not resolve a commit")
    entries = []

    def write(name, content, mode):
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        target.chmod(0o755 if mode & 0o111 else 0o644)
        entries.append((name, hashlib.sha256(content).hexdigest(), target.stat().st_mode & 0o777))

    if baseline_ref:
        # Archive only committed objects, not the current working tree or its ignored files.
        with tempfile.TemporaryFile() as stream:
            subprocess.run(["git", "archive", "--format=tar", revision], cwd=ROOT, stdout=stream, check=True)
            stream.seek(0)
            with tarfile.open(fileobj=stream) as archive:
                for member in archive:
                    if member.isfile() and context_file_allowed(member.name):
                        write(member.name, archive.extractfile(member).read(), member.mode)
    else:
        for name in git("ls-files", "--cached", "--others", "--exclude-standard", "-z").decode().split("\0"):
            if not name or not context_file_allowed(name):
                continue
            source = ROOT / name
            if source.is_symlink():
                raise SmokeError("Build context contains a symbolic-link file")
            if source.is_file():
                if any(parent.is_symlink() for parent in source.parents if parent != ROOT and ROOT in parent.parents):
                    raise SmokeError("Build context contains a symbolic-link parent")
                write(name, source.read_bytes(), source.stat().st_mode)
    require((destination / "Dockerfile").is_file(), "Build context lacks Dockerfile")
    digest = hashlib.sha256(json.dumps(sorted(entries), separators=(",", ":")).encode()).hexdigest()
    return {"commit": revision, "source": "git_archive" if baseline_ref else "working_tree",
            "build_context_sha256": digest, "file_count": len(entries)}


def call_json(transport, method, path, body=None, expected=200):
    response = transport(method, path, body)
    require(response["status"] == expected, f"{method} {path}: expected {expected}, got {response['status']}")
    return json.loads(response["body"])


def seed_workflow(call, material):
    require(call("GET", "/api/knowledge/documents")["total"] == 0, "Database is not empty")
    require(call("GET", "/api/agent-runs") == [], "Database contains prior runs")
    kb = call("POST", "/api/v04/knowledge-bases", {"name": "容器验收合成资料", "purpose": "临时合成验收，无客户数据"}, 201)
    source = "https://example.com/synthetic-industrial/spec"
    note = call("POST", "/api/evidence/notes", {
        "knowledge_base_id": kb["id"], **{key: material[key] for key in ("title", "content", "version_label")},
        "source_url": source, "provider": "manual", "rights_basis": "own",
        "rights_note": "仓库虚构样本；脚本模拟审核，不代表客户授权或人工验收。",
        "expires_at": (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
        "citations": [{**item, "source_url": source + "#paragraph-1"} for item in material["citations"]],
    }, 201)
    scope = {"knowledge_base_id": kb["id"]}
    call("POST", f"/api/evidence/notes/{note['id']}/index", scope, 409)
    call("POST", f"/api/evidence/notes/{note['id']}/review", {**scope, "decision": "verify", "confirmed_sources": True,
        "note": "容器测试脚本模拟审核合成材料。"})
    indexed = call("POST", f"/api/evidence/notes/{note['id']}/index", scope)
    brand = call("POST", "/api/brands", {"name": "容器合成星桥（虚构）", "knowledge_base_id": kb["id"], "data_policy": "local_only"}, 201)
    facts = [{"product_model": item["product_model"], "parameter": item["parameter"]} for item in material["citations"]]
    run = call("POST", "/api/agent-runs", {"goal": "合成星桥 XP-24 额定电压与额定流量答疑", "provider": "local",
        "auto_score": False, "use_rag": True, "workflow_key": "product_faq", "knowledge_base_id": kb["id"],
        "brand_profile_id": brand["id"], "required_facts": facts}, 201)
    deadline = time.monotonic() + 120
    while run["status"] in {"pending", "running"} and time.monotonic() < deadline:
        time.sleep(.25)
        run = call("GET", f"/api/agent-runs/{run['id']}")
    require(run["status"] == "awaiting_review", "Synthetic workflow did not reach human review")
    call("GET", f"/api/agent-runs/{run['id']}/delivery", expected=409)
    call("POST", f"/api/agent-runs/{run['id']}/review", {"decision": "approve", "note": "脚本模拟批准；不是客户批准。"})
    return {"run_id": run["id"], "draft_id": run["draft_id"], "knowledge_base_id": kb["id"],
            "document_id": indexed["document_id"], "note_id": note["id"], "required_facts": facts}


def verify_workflow(call, ids, material):
    run = call("GET", f"/api/agent-runs/{ids['run_id']}")
    require(run["status"] == "approved", "Approved state did not persist")
    delivery = call("GET", f"/api/agent-runs/{ids['run_id']}/delivery")
    content_hash = delivery["review"]["content_hash"]
    require(bool(re.fullmatch(r"[0-9a-f]{64}", content_hash)), "Missing approved content hash")
    require(content_hash == run["result_json"]["review"]["content_hash"], "Run and delivery review hashes differ")
    require(delivery["body_text"] == run["draft"]["body_text"], "Delivery differs from approved draft")
    require(delivery["body_text"] in delivery["markdown"] and content_hash in delivery["markdown"], "Markdown omits approved body/hash")
    require(bool(delivery["citations"]), "Delivery has no citations")
    for citation in delivery["citations"]:
        require(citation["document_id"] == ids["document_id"] and citation["version_label"] == material["version_label"], "Wrong source/version")
        require(citation["verification_status"] == "verified" and bool(citation["locators"]), "Citation lacks verified locator")
        require(citation["marker"] in delivery["markdown"] and citation["source_uri"] in delivery["markdown"], "Markdown citation missing")
    answer = call("POST", "/api/v04/rag/answer", {"query": "合成星桥 XP-24 额定电压与额定流量", "provider": "local",
        "knowledge_base_id": ids["knowledge_base_id"], "required_facts": ids["required_facts"]})
    require(not answer["refused"] and answer["answerability"] == "required_facts_present", "Restored material cannot answer required facts")
    require(all(item["value"] in answer["answer"] for item in material["citations"]), "Required values missing")
    usage = call("GET", f"/api/agent-runs/{ids['run_id']}/model-runs")["summary"]
    require(usage["recorded_external_request_count"] == 0, "External model record found")
    fields = ("document_id", "chunk_id", "marker", "source_uri", "document_content_hash", "version_label", "verification_status", "locators")
    signature = {"status": run["status"], "review_hash": content_hash,
        "body_sha256": hashlib.sha256(delivery["body_text"].encode()).hexdigest(), "title": delivery["title"],
        "citations": [{key: item[key] for key in fields} for item in delivery["citations"]]}
    return signature, delivery


def revise_and_approve(call, ids):
    run = call("GET", f"/api/agent-runs/{ids['run_id']}")
    call("PUT", f"/api/drafts/{ids['draft_id']}", {"body_text": run["draft"]["body_text"] + "\n\n" + EDIT_MARKER})
    require(call("GET", f"/api/agent-runs/{ids['run_id']}")["status"] == "awaiting_review", "Edit did not invalidate approval")
    call("GET", f"/api/agent-runs/{ids['run_id']}/delivery", expected=409)
    call("POST", f"/api/agent-runs/{ids['run_id']}/review", {"decision": "approve", "note": "脚本模拟候选版本改稿后重审。"})


class Docker:
    def __init__(self, output):
        self.token = uuid.uuid4().hex
        self.prefix = f"zhiyuan-smoke-{self.token[:12]}"
        self.output = output
        self.containers, self.volumes, self.images = [], [], []
        self.trace = []

    def run(self, *args, input=None, timeout=180, log=None, check=True):
        started = time.monotonic()
        try:
            result = subprocess.run(["docker", *args], input=input, text=True, capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            text = lambda value: value.decode(errors="replace") if isinstance(value, bytes) else value or ""
            (self.output / (log or "docker-timeout.log")).write_text(text(exc.stdout) + text(exc.stderr), encoding="utf-8")
            raise SmokeError(f"Docker {' '.join(args[:2])} timed out; see saved logs") from None
        self.trace.append({"operation": " ".join(args[:2]), "exit_code": result.returncode,
                           "elapsed_ms": round((time.monotonic() - started) * 1000, 2)})
        if log:
            (self.output / log).write_text(result.stdout + result.stderr, encoding="utf-8")
        if check and result.returncode:
            if not log:
                (self.output / f"command-failure-{len(self.trace)}.log").write_text(result.stdout + result.stderr, encoding="utf-8")
            raise SmokeError(f"Docker {' '.join(args[:2])} failed (exit {result.returncode}); see saved logs")
        return result

    def preflight(self):
        require(shutil.which("docker") is not None, "Docker CLI is unavailable; container acceptance was not run")
        endpoint = os.environ.get("DOCKER_HOST") if not os.environ.get("DOCKER_CONTEXT") else None
        endpoint = endpoint or self.run("context", "inspect", "--format", "{{.Endpoints.docker.Host}}").stdout.strip()
        require(endpoint.startswith("unix://"), "Only a local Unix-socket Docker engine is supported")
        require(self.run("info", "--format", "{{.OSType}}").stdout.strip() == "linux", "A Linux Docker engine is required")
        return self.run("version", "--format", "{{.Server.Version}}").stdout.strip()

    def build(self, context, role):
        image = f"{self.prefix}:{role}"
        self.images.append(image)
        self.run("build", "--label", f"{OWNER_LABEL}={self.token}", "--tag", image, str(context), timeout=1800, log=f"build-{role}.log")
        return image, self.run("image", "inspect", "--format", "{{.Id}}", image).stdout.strip()

    def volume(self, role):
        name = f"{self.prefix}-{role}"
        self.volumes.append(name)
        self.run("volume", "create", "--label", f"{OWNER_LABEL}={self.token}", name)
        return name

    def container(self, role, image, volume, *, data_dir="/app/.data", command=(), backup_volume=None, detach=False):
        name = f"{self.prefix}-{role}"
        self.containers.append(name)
        args = ["run", "--name", name, "--label", f"{OWNER_LABEL}={self.token}", "--network", "none",
                "--mount", f"type=volume,src={volume},dst=/app/.data"]
        if backup_volume:
            args += ["--mount", f"type=volume,src={backup_volume},dst=/backup,readonly"]
        for key, value in runtime_environment(data_dir).items():
            args += ["--env", f"{key}={value}"]
        args += ["--detach"] if detach else []
        result = self.run(*args, image, *command, log=f"{role}.log")
        return name if detach else result.stdout.strip()

    def http(self, container, method, path, body=None):
        require(path.startswith("/") and not path.startswith("//"), "Only local API paths are allowed")
        result = self.run("exec", "-i", container, "python", "-c", HTTP_PROBE,
                          input=json.dumps({"method": method, "path": path, "body": body}), timeout=30)
        return json.loads(result.stdout)

    def api(self, container):
        return lambda method, path, body=None, expected=200: call_json(
            lambda method, path, body: self.http(container, method, path, body), method, path, body, expected)

    def ready(self, container):
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            try:
                call = self.api(container)
                require(call("GET", "/api/ready")["status"] == "ready", "Application is not ready")
                health = call("GET", "/api/health")
                require(health["scope"] == "local-single-user", "Container is not in local mode")
                page = self.http(container, "GET", "/")
                require(page["status"] == 200 and 'id="root"' in page["body"] and "/assets/" in page["body"], "Built frontend is not served")
                return health
            except (SmokeError, ValueError):
                time.sleep(.5)
        raise SmokeError("Container readiness timed out")

    def cleanup(self):
        errors = []
        for name in self.containers:
            try:
                result = self.run("logs", "--tail", "150", name, check=False)
                (self.output / f"{name}.log").write_text(result.stdout + result.stderr, encoding="utf-8")
            except (SmokeError, OSError, subprocess.SubprocessError):
                errors.append(f"Could not collect container logs: {name}")
        for kind, names, template in (
            ("container", self.containers, "{{ index .Config.Labels \"" + OWNER_LABEL + "\" }}"),
            ("volume", self.volumes, "{{ index .Labels \"" + OWNER_LABEL + "\" }}"),
            ("image", self.images, "{{ index .Config.Labels \"" + OWNER_LABEL + "\" }}"),
        ):
            for name in names:
                try:
                    inspected = self.run(kind, "inspect", "--format", template, name, check=False)
                    if inspected.returncode:
                        continue
                    if inspected.stdout.strip() != self.token:
                        errors.append(f"Refused to delete resource without this run's ownership label: {name}")
                        continue
                    result = self.run(kind, "rm", *(["--force"] if kind == "container" else []), name, check=False)
                    if result.returncode:
                        errors.append(f"Could not remove {kind}: {name}")
                except (SmokeError, OSError, subprocess.SubprocessError):
                    errors.append(f"Cleanup failed for {kind}: {name}")
        return {"errors": errors, "scope": "only resources labelled with this run ID; build cache is retained"}


def execute(docker, baseline_ref, report):
    report["docker_version"] = docker.preflight()
    dataset = json.loads(FIXTURE.read_text(encoding="utf-8"))
    material = next(item for item in dataset["documents"] if item["key"] == "spec")
    report["fixture"] = {"dataset_id": dataset["dataset_id"], "sha256": hashlib.sha256(FIXTURE.read_bytes()).hexdigest()}
    with tempfile.TemporaryDirectory(prefix="zhiyuan-container-context-") as temporary:
        baseline_context, candidate_context = Path(temporary) / "baseline", Path(temporary) / "candidate"
        report["baseline"] = prepare_context(baseline_context, baseline_ref)
        report["candidate"] = prepare_context(candidate_context)
        baseline_image, report["baseline"]["image_id"] = docker.build(baseline_context, "baseline")
        candidate_image, report["candidate"]["image_id"] = docker.build(candidate_context, "candidate")
    primary, restored = docker.volume("primary"), docker.volume("restored")
    stages = report["stages"]

    def verify(name, stage, expected=None):
        signature, delivery = verify_workflow(docker.api(name), ids, material)
        if expected is not None:
            require(signature == expected, f"{stage}: approved content/citations differ")
        stages[stage] = {"passed": True, **signature}
        (docker.output / f"{stage}.delivery.md").write_text(delivery["markdown"], encoding="utf-8")
        return signature

    base = docker.container("baseline", baseline_image, primary, detach=True)
    report["baseline"]["health"] = docker.ready(base)
    ids = seed_workflow(docker.api(base), material)
    report["workflow_ids"] = ids
    original = verify(base, "baseline_approved")
    docker.run("restart", "--time", "30", base)
    docker.ready(base)
    verify(base, "baseline_restart", original)
    docker.run("stop", "--time", "30", base)
    backup = docker.container("backup", baseline_image, primary, command=("python", "scripts/local_backup.py", "backup",
        "--data-dir", "/app/.data", "--output", "/app/.data/checkpoint.zip", "--offline-confirm"))
    report["backup"] = json.loads(backup)
    require(report["backup"]["vector_index"] == "absent", "Lexical fixture unexpectedly created a vector index")

    candidate = docker.container("candidate", candidate_image, primary, detach=True)
    report["candidate"]["health"] = docker.ready(candidate)
    verify(candidate, "candidate_reads_baseline", original)
    revise_and_approve(docker.api(candidate), ids)
    updated = verify(candidate, "candidate_reapproved")
    require(updated["review_hash"] != original["review_hash"], "Candidate edit did not produce a new approved hash")
    docker.run("restart", "--time", "30", candidate)
    docker.ready(candidate)
    verify(candidate, "candidate_restart", updated)
    docker.run("stop", "--time", "30", candidate)

    recovery = docker.container("restore", baseline_image, restored, backup_volume=primary,
        command=("python", "scripts/local_backup.py", "restore", "--archive", "/backup/checkpoint.zip",
                 "--destination", "/app/.data/restored", "--offline-confirm"))
    report["restore"] = json.loads(recovery)
    rollback = docker.container("rollback", baseline_image, restored, data_dir="/app/.data/restored", detach=True)
    docker.ready(rollback)
    verify(rollback, "baseline_on_restored_snapshot", original)
    docker.run("start", candidate)
    docker.ready(candidate)
    verify(candidate, "original_volume_unchanged_by_restore", updated)
    docker.run("stop", "--time", "30", rollback)
    recovered_candidate = docker.container("recovered-candidate", candidate_image, restored, data_dir="/app/.data/restored", detach=True)
    docker.ready(recovered_candidate)
    verify(recovered_candidate, "candidate_on_restored_snapshot", original)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-ref", required=True, help="Committed, locally available baseline Git ref; no fetch is performed")
    parser.add_argument("--output", type=Path, default=ROOT / ".data/validation/container-smoke/report.json")
    args = parser.parse_args(argv)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    report = {"started_at": datetime.now(timezone.utc).isoformat(), "status": "running", "stages": {},
        "isolation": {"runtime_network": "none", "published_ports": [], "host_bind_mounts": [],
                      "sample_origin": "synthetic", "generation": "local_rules", "retrieval": "lexical"},
        "cost": {"provider_invoice_amount": None, "model_tokens": None, "reason": "No external model network; costs are not measured."},
        "limits": ["Only the recorded baseline/candidate pair is exercised, not arbitrary downgrade or migration compatibility.",
                   "Rollback restores the pre-upgrade snapshot to a new volume; it never runs old code on the upgraded database.",
                   "Qdrant/BGE container restoration is not assessed: lexical mode creates no vector index.",
                   "No real data, paid models, SaaS, Compose/proxy/public network or customer review is exercised.",
                   "Image builds require network access to public package/base-image registries; runtime containers have none."]}
    docker = Docker(args.output.parent)
    started = time.monotonic()
    try:
        execute(docker, args.baseline_ref, report)
        report["status"] = "passed"
    except (SmokeError, OSError, subprocess.SubprocessError, ValueError, KeyError, KeyboardInterrupt) as exc:
        report["status"] = "failed"
        report["error"] = str(exc) if isinstance(exc, SmokeError) else type(exc).__name__
    finally:
        if docker.containers or docker.volumes or docker.images:
            report["cleanup"] = docker.cleanup()
            if report["cleanup"]["errors"]:
                report["status"] = "failed"
        report["command_trace"] = docker.trace
        report["elapsed_ms"] = round((time.monotonic() - started) * 1000, 2)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "report": str(args.output), "error": report.get("error")}, ensure_ascii=False))
    return 0 if report["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
