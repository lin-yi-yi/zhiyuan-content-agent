#!/usr/bin/env python3
"""Exercise private diagnostics through disposable real Uvicorn processes.

All data and credentials are synthetic. This creates its own temporary stores,
never uses an operator's configured log directory, and performs no model call.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

from container_smoke import ROOT, SmokeError, require
from saas_recovery_fixture import (IsolationState, deny_outbound_connections,
    initialize_fixture_root, require_fixture_root, prepare_fixture_store)
from saas_recovery_smoke import LocalRuntime

SECRET = "SYNTHETIC-PRIVATE-DIAGNOSTIC-4c5e37"


def serve(args):
    root = require_fixture_root(args.fixture_root, args.fixture_token)
    data = prepare_fixture_store(root, args.store, args.fixture_token)
    from evaluate_industrial_faq import configure_offline
    configure_offline(data)
    os.environ.update({"MODEL_PRICING_JSON": "[]", "DIAGNOSTIC_LOG_DIR": str(root / "logs") if args.logging else "",
        "DIAGNOSTIC_LOG_MAX_BYTES": "16384", "DIAGNOSTIC_LOG_BACKUP_COUNT": "2"})
    state = IsolationState()
    with deny_outbound_connections(state):
        sys.path.insert(0, str(ROOT / "backend"))
        from fastapi import HTTPException, Request
        from fastapi.routing import APIRoute
        from app.main import app
        from app.core.diagnostics import diagnostic_sink_status
        from app.db.session import SessionLocal
        from app.models.model_run import ModelRun
        from sqlalchemy import select, func

        def authorize(request):
            supplied = request.headers.get("X-Fixture-Token", "")
            if (request.client is None or request.client.host != "127.0.0.1"
                    or not hmac.compare_digest(supplied, args.fixture_token)):
                raise HTTPException(404, "Not found")

        def status(request: Request):
            authorize(request)
            with SessionLocal() as db:
                calls = db.scalar(select(func.count()).select_from(ModelRun))
            return {"sink": diagnostic_sink_status(), "model_records": calls,
                    "outgoing_attempts": state.outbound_connection_attempts}

        armed = False

        def arm(request: Request):
            nonlocal armed
            authorize(request)
            if args.fault not in {"write", "fsync"} or armed:
                raise HTTPException(409, "Fixture fault cannot be armed")
            target = (root / "logs" / "diagnostics.jsonl").stat()
            original = getattr(os, args.fault)

            def fail(descriptor, *values):
                info = os.fstat(descriptor)
                if (info.st_dev, info.st_ino) == (target.st_dev, target.st_ino):
                    raise OSError(SECRET)
                return original(descriptor, *values)

            setattr(os, args.fault, fail)
            armed = True
            return {"armed": True}

        app.router.routes.insert(0, APIRoute("/__diagnostics__/status", status, methods=["GET"]))
        app.router.routes.insert(0, APIRoute("/__diagnostics__/arm", arm, methods=["POST"]))
        import uvicorn
        uvicorn.run(app, host="127.0.0.1", port=args.port, workers=1, proxy_headers=False,
                    access_log=False, log_level="info")


class DiagnosticRuntime(LocalRuntime):
    def create(self, name):
        root = self.volume(name)
        initialize_fixture_root(root, self.token)
        (root / "logs").mkdir(mode=0o700)
        return root

    def start(self, role, root, *, store="source", logging=True, fault="none"):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        log = (self.output / f"{role}.log").open("w", encoding="utf-8")
        self.logs.append(log)
        argv = [sys.executable, str(ROOT / "scripts/diagnostic_smoke.py"), "--serve",
                "--fixture-root", str(root), "--fixture-token", self.token, "--store", store,
                "--port", str(port), "--fault", fault]
        if logging:
            argv.append("--logging")
        process = subprocess.Popen(argv, cwd=ROOT, stdout=log, stderr=log)
        self.processes.append(process)
        self.process_logs[process] = Path(log.name)
        return process, port

    def ready(self, handle):
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            require(handle[0].poll() is None, "Diagnostic fixture exited before startup")
            try:
                response = self.transport(handle)("GET", "/api/health")
                body = json.loads(response["body"])
                if response["status"] == 200 and body.get("scope") == "local-single-user":
                    return body
            except (SmokeError, OSError, ValueError):
                pass
            time.sleep(.1)
        raise SmokeError("Diagnostic fixture readiness timed out")

    def status(self, handle):
        response = self.transport(handle)("GET", "/__diagnostics__/status", headers={"X-Fixture-Token": self.token})
        require(response["status"] == 200, "Diagnostic fixture status unavailable")
        body = json.loads(response["body"])
        require(body["model_records"] == 0 and body["outgoing_attempts"] == 0, "Fixture attempted model or network use")
        return body


def query(directory, request_id):
    result = subprocess.run([sys.executable, str(ROOT / "scripts/diagnostic_query.py"),
        "--directory", str(directory), "--request-id", request_id], cwd=ROOT,
        text=True, capture_output=True, timeout=20)
    require(SECRET not in result.stdout + result.stderr, "Private marker escaped the diagnostic query")
    require(not result.stderr, "Diagnostic query wrote unexpected stderr")
    return json.loads(result.stdout), result.returncode


def tree_signature(directory):
    return {p.name: (p.stat().st_mode, p.stat().st_size, p.stat().st_mtime_ns,
                    hashlib.sha256(p.read_bytes()).hexdigest()) for p in directory.iterdir()}


def assert_found(directory, request_id, events):
    before = tree_signature(directory)
    report, code = query(directory, request_id)
    require(code == 0 and report["match_status"] == "found" and report["complete"] is False,
            "Retained request cannot be queried")
    require(set(events) <= {item["event"] for item in report["records"]}, "Correlated events are missing")
    require(tree_signature(directory) == before, "Read-only query changed private files")
    return {"events": sorted({item["event"] for item in report["records"]}),
            "record_count": len(report["records"]), "query_wrote_files": False}


def execute(runtime, report):
    report["candidate"] = runtime.prepare()
    root = runtime.create("normal")
    logs = root / "logs"
    stages = report["stages"]
    handle = runtime.start("disabled", root, logging=False)
    report["candidate"]["health"] = runtime.ready(handle)
    require(runtime.status(handle)["sink"]["state"] == "disabled", "Default logging is not disabled")
    runtime.stop(handle)
    require(not list(logs.iterdir()), "Disabled logging created files")
    stages["default_disabled"] = {"passed": True}

    handle = runtime.start("enabled", root)
    runtime.ready(handle)
    api = runtime.transport(handle)
    failed = api("POST", "/api/not-found/" + SECRET + "?token=" + SECRET,
        body={"prompt": SECRET}, headers={"Authorization": SECRET, "Cookie": SECRET, "X-Request-ID": "f" * 32})
    require(failed["status"] in {404, 405}, "Synthetic missing endpoint unexpectedly succeeded")
    old_id = failed["headers"]["x-request-id"]
    require(old_id != "f" * 32, "Client request ID was trusted")
    stages["request_lookup"] = {"passed": True, **assert_found(logs, old_id, {"http_response"})}
    created = api("POST", "/api/agent-runs", {"goal": SECRET, "provider": "local", "auto_score": False,
        "use_rag": True, "workflow_key": "product_faq", "required_facts": [{"product_model": "SYN-1", "parameter": "额定电压"}]})
    require(created["status"] == 201, "Synthetic workflow was not accepted")
    workflow_id = created["headers"]["x-request-id"]
    run_id = json.loads(created["body"])["id"]
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        value = json.loads(api("GET", f"/api/agent-runs/{run_id}")["body"])
        if value["status"] == "failed":
            break
        time.sleep(.1)
    require(value["status"] == "failed", "Empty evidence did not stop the workflow")
    runtime.status(handle)
    runtime.stop(handle)
    stages["accepted_request_failed_workflow"] = {"passed": True,
        **assert_found(logs, workflow_id, {"http_response", "workflow_started", "workflow_step_failed", "workflow_finished"})}
    correlated, _ = query(logs, workflow_id)
    require(all(item["request_id"] == workflow_id for item in correlated["records"]),
            "Workflow query crossed request IDs")
    workflow_events = [item for item in correlated["records"] if item["event"].startswith("workflow_")]
    require(all(item["agent_run_id"] == run_id and item["workflow_attempt"] == 1 for item in workflow_events)
            and any(item["event"] == "workflow_finished" and item["status"] == "failed" for item in workflow_events)
            and any(item["event"] == "http_response" and item["http_status"] == 201 for item in correlated["records"]),
            "Request acceptance and failed workflow correlation disagree")
    stages["accepted_request_failed_workflow"].update(http_status=201, workflow_status="failed", workflow_attempt=1)
    require(all(SECRET.encode() not in path.read_bytes() for path in logs.iterdir()), "Private marker reached the log store")

    handle = runtime.start("restarted", root)
    runtime.ready(handle)
    stages["restart_retains_request"] = {"passed": True, **assert_found(logs, old_id, {"http_response"})}
    # Different data directory: failure must come from the log lock, not the DB lock.
    rejected = runtime.start("conflicting-owner", root, store="restored")
    try:
        rejected[0].wait(timeout=20)
    except subprocess.TimeoutExpired:
        raise SmokeError("Second diagnostic owner was not rejected") from None
    require(rejected[0].returncode != 0, "Second diagnostic owner unexpectedly started")
    text = runtime.process_logs[rejected[0]].read_text()
    require("DiagnosticConfigurationError" in text and not (root / "restored/industrial-faq.db").exists(),
            "Second startup failed for an unrelated reason")
    runtime.processes.remove(rejected[0])  # An expected failed startup is already reaped.
    owner_check = runtime.transport(handle)("GET", "/api/health")
    require(owner_check["status"] == 200, "Rejected startup damaged the active owner")
    stages["second_owner_rejected"] = {"passed": True,
        **assert_found(logs, owner_check["headers"]["x-request-id"], {"http_response"})}

    for _ in range(130):
        latest = runtime.transport(handle)("GET", "/api/health")
        require(latest["status"] == 200, "Rotation changed business responses")
    runtime.status(handle)
    runtime.stop(handle)
    data_files = [p for p in logs.iterdir() if p.name == "diagnostics.jsonl" or p.name.startswith("diagnostics.jsonl.")]
    require(len(data_files) == 3 and all(p.stat().st_size <= 16384 and p.stat().st_mode & 0o777 == 0o600 for p in data_files),
            "Rotation exceeded configured file count, size or permissions")
    missing, code = query(logs, old_id)
    require(code == 1 and missing["match_status"] == "not_observed" and missing["complete"] is False,
            "Evicted request was treated as complete history")
    stages["bounded_rotation"] = {"passed": True, "files": 3, "max_bytes": 16384,
        "evicted_match": "not_observed", **assert_found(logs, latest["headers"]["x-request-id"], {"http_response"})}

    for fault in ("write", "fsync"):
        failure_root = runtime.create(fault)
        broken = runtime.start(fault, failure_root, fault=fault)
        runtime.ready(broken)
        api = runtime.transport(broken)
        armed = api("POST", "/__diagnostics__/arm", headers={"X-Fixture-Token": runtime.token})
        require(armed["status"] == 200, "Fault fixture could not be armed")
        after_failure = tree_signature(failure_root / "logs")
        for _ in range(2):
            response = api("GET", "/api/health")
            require(response["status"] == 200 and len(response["headers"]["x-request-id"]) == 32,
                    "Diagnostic failure changed business health or correlation")
        status = runtime.status(broken)
        require(status["sink"]["state"] == "degraded" and status["sink"]["dropped"] >= 3,
                "Failed persistence did not report degradation")
        require(tree_signature(failure_root / "logs") == after_failure,
                "Degraded log store continued to write")
        runtime.stop(broken)
        output = runtime.process_logs[broken[0]].read_text()
        require(output.count('"event":"diagnostic_sink_degraded"') == 1 and SECRET not in output,
                "Sink failure notice was missing, repeated or private")
        stages[f"{fault}_failure_isolated"] = {"passed": True, **status, "notice_count": 1,
                                             "writes_after_degradation": False}

    # Malformed imported/tampered records must not leak their contents in query output.
    with (logs / "diagnostics.jsonl").open("ab") as stream:
        stream.write(json.dumps({"password": SECRET}).encode())
    partial, code = query(logs, latest["headers"]["x-request-id"])
    require(code == 2 and partial["scan_status"] == "partial", "Partial record was treated as reliable history")
    stages["malformed_query_is_unknown"] = {"passed": True, "scan_status": partial["scan_status"]}
    for path in runtime.output.glob("*.log"):
        require(SECRET not in path.read_text(), "Application stderr exposed the private marker")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / ".data/validation/diagnostic-smoke/report.json")
    parser.add_argument("--serve", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--fixture-root", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--fixture-token", help=argparse.SUPPRESS)
    parser.add_argument("--store", choices=["source", "restored"], default="source", help=argparse.SUPPRESS)
    parser.add_argument("--port", type=int, default=8790, help=argparse.SUPPRESS)
    parser.add_argument("--logging", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--fault", choices=["none", "write", "fsync"], default="none", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.serve:
        serve(args)
        return 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    runtime = DiagnosticRuntime(args.output.parent)
    report = {"started_at": datetime.now(timezone.utc).isoformat(), "status": "running", "stages": {},
        "scope": "synthetic local subprocesses and temporary files; no production capacity or crash durability claim",
        "limits": ["Python socket guard, not an OS firewall", "No paid models, customer data or external notifications",
                   "Fault injection is targeted write/fsync failure, not a full disk or power outage"]}
    started = time.monotonic()
    try:
        execute(runtime, report)
        report["status"] = "passed"
    except (Exception, KeyboardInterrupt) as error:
        report["status"] = "failed"
        report["error"] = str(error) if isinstance(error, SmokeError) else type(error).__name__
    finally:
        report["cleanup"] = runtime.cleanup()
        if report["cleanup"]["errors"]:
            report["status"] = "failed"
        report["elapsed_ms"] = round((time.monotonic() - started) * 1000, 2)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"status": report["status"], "error": report.get("error"), "report": str(args.output)}))
    return 0 if report["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
