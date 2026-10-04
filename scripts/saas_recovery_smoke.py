#!/usr/bin/env python3
"""Disposable two-organization SaaS/Qdrant recovery drill, local or isolated Docker.

Synthetic embeddings test persistence and isolation, never semantic model quality.
The runner creates its own directories/volumes; it accepts no existing business store.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import socket
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
import zipfile

from container_smoke import Docker, OWNER_LABEL, ROOT, SmokeError, prepare_context, require, runtime_environment


FIXTURE = "scripts/saas_recovery_fixture.py"
MODEL = "zhiyuan-recovery-synthetic-3d-v1"
HTTP_PROBE = r'''
import json, sys, urllib.request, urllib.error
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args): return None
try:
    data = json.load(sys.stdin)
    path = data['path']
    if not path.startswith('/') or path.startswith('//'): raise ValueError()
    body = None if data.get('body') is None else json.dumps(data['body']).encode()
    request = urllib.request.Request('http://127.0.0.1:8765' + path, data=body,
        method=data['method'], headers={'Content-Type':'application/json', **data.get('headers', {})})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try: response = opener.open(request, timeout=20)
    except urllib.error.HTTPError as error: response = error
    with response:
        payload = response.read(2 * 1024 * 1024 + 1)
        if len(payload) > 2 * 1024 * 1024: raise ValueError()
        result = {'status':response.status, 'body':payload.decode(),
                  'headers':{key.lower():value for key,value in response.headers.items()}}
except Exception:
    result = {'status':0, 'body':'Transport failed', 'headers':{}}
print(json.dumps(result))
'''


def fingerprint(directory):
    require(directory.is_dir() and not directory.is_symlink(), "Store directory is unavailable")
    rows = []
    for path in sorted(directory.rglob("*")):
        require(not path.is_symlink(), "Store contains a symbolic link")
        mode = path.stat().st_mode
        require(stat.S_ISREG(mode) or stat.S_ISDIR(mode), "Store contains a special file")
        if stat.S_ISREG(mode):
            rows.append((path.relative_to(directory).as_posix(), hashlib.sha256(path.read_bytes()).hexdigest()))
    require(bool(rows), "Store has no files")
    return {"files": len(rows), "sha256": hashlib.sha256(json.dumps(rows).encode()).hexdigest()}


def file_operation(payload):
    """Internal fixed-path operations on runner-owned roots, never arbitrary paths."""
    from saas_recovery_fixture import require_fixture_root
    root = require_fixture_root(payload["root"], payload["token"])
    operation = payload["operation"]
    if operation == "fingerprint":
        require(payload["store"] in {"source", "restored"}, "Invalid store name")
        return fingerprint(root / payload["store"])
    if operation == "absent":
        target = root / "rejected"
        return {"absent": not target.exists() and not target.is_symlink()}
    source = require_fixture_root(payload.get("source_root", str(root)), payload["token"])
    archive = source / "checkpoint.zip"
    require(archive.is_file() and not archive.is_symlink(), "Snapshot unavailable")
    with zipfile.ZipFile(archive) as bundle:
        manifest = json.loads(bundle.read("manifest.json"))
        entries = manifest["files"]
        # The backup module supplies the authoritative whitelist and manifest checks.
        from saas_backup import _manifest
        _manifest(bundle)
        vector_entries = [entry for entry in entries if "/vectors/" in entry["path"]]
        require(len({entry["path"].split('/')[1] for entry in vector_entries}) == 2,
                "Snapshot must contain both organizations' real vector stores")
        require(all(hashlib.sha256(bundle.read(entry["path"])).hexdigest() == entry["sha256"]
                    for entry in entries), "Snapshot member hash mismatch")
        if operation == "summary":
            return {"files": len(entries), "bytes": sum(entry["size"] for entry in entries),
                    "vector_files": len(vector_entries), "vector_organizations": 2,
                    "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest()}
        if operation == "tamper":
            target = root / "tampered.zip"
            require(not target.exists() and not target.is_symlink(), "Tampered fixture already exists")
            altered = vector_entries[0]["path"]
            with zipfile.ZipFile(target, "x", compression=zipfile.ZIP_DEFLATED) as output:
                for info in bundle.infolist():
                    content = bundle.read(info.filename)
                    if info.filename == altered:
                        require(bool(content), "Vector fixture is empty")
                        content = content[:-1] + bytes([content[-1] ^ 1])
                    output.writestr(info, content)
            target.chmod(0o600)
            return {"tampered_vector_member": True}
        if operation == "matches_manifest":
            restored = root / "restored"
            require(restored.is_dir() and not restored.is_symlink(), "Restored directory unavailable")
            for entry in entries:
                target = restored / entry["path"]
                require(all(parent.is_dir() and not parent.is_symlink()
                            for parent in target.parents if parent == restored or restored in parent.parents),
                        "Restored directory contains a symbolic link")
                require(target.is_file() and not target.is_symlink() and target.stat().st_nlink == 1,
                        "Restored member unavailable")
                require(hashlib.sha256(target.read_bytes()).hexdigest() == entry["sha256"],
                        "Restored member hash differs")
            return {"matching_files": len(entries), "vector_files": len(vector_entries)}
    raise SmokeError("Unknown file operation")


class Runtime:
    def initialize(self, volume):
        result = self.command(volume, ["python", FIXTURE, "--fixture-root", self.root(volume),
            "--fixture-token", self.token, "--initialize-only"])
        require(result.returncode == 0, "Fixture ownership initialization failed")

    def fs(self, volume, operation, *, store="source", source=None):
        payload = {"root": self.root(volume), "token": self.token, "operation": operation, "store": store}
        if source is not None:
            payload["source_root"] = self.source_root(source)
        result = self.command(volume, ["python", "scripts/saas_recovery_smoke.py", "--internal-operation"],
                              source=source, input=json.dumps(payload))
        require(result.returncode == 0, "Fixture file verification failed")
        return json.loads(result.stdout)

    def backup(self, volume, *, expected_success=True):
        result = self.command(volume, ["python", "scripts/saas_backup.py", "backup", "--data-dir",
            self.root(volume) + "/source", "--output", self.root(volume) + "/checkpoint.zip", "--offline-confirm"])
        require((result.returncode == 0) == expected_success, "Unexpected live/offline backup result")
        if not expected_success:
            # A failure from any other cause must not pass the active-lock assertion.
            require("活跃服务或向量索引锁" in result.stdout + result.stderr, "Backup was not refused by the active lock")
            return {"active_lock_refused": True}
        result = json.loads(result.stdout)
        return {"files": result["files"], "bytes": result["bytes"]}

    def restore(self, source, target, *, corrupt=False, expected_success=True):
        name, destination = ("tampered.zip", "rejected") if corrupt else ("checkpoint.zip", "restored")
        result = self.command(target, ["python", "scripts/saas_backup.py", "restore", "--archive",
            self.source_root(source) + "/" + name, "--destination", self.root(target) + "/" + destination,
            "--offline-confirm"], source=source)
        require((result.returncode == 0) == expected_success, "Unexpected restore result")
        if not expected_success:
            message = result.stdout + result.stderr
            require(("哈希" if corrupt else "空目录") in message, "Restore failed for an unexpected reason")
            return {"rejected": True, "reason": "hash_mismatch" if corrupt else "existing_destination"}
        return {"files": json.loads(result.stdout)["files"]}

    def ready(self, handle):
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            try:
                response = self.transport(handle)("GET", "/api/health")
                health = json.loads(response["body"])
                if response["status"] == 200 and health.get("scope") == "saas-single-host-pilot":
                    self.isolation(handle, populated=False)
                    return health
            except (OSError, ValueError, SmokeError):
                pass
            time.sleep(.2)
        raise SmokeError("SaaS fixture startup failed or timed out")

    def isolation(self, handle, *, populated=True):
        response = self.transport(handle)("GET", "/__recovery__/isolation", headers={"X-Recovery-Fixture-Token": self.token})
        require(response["status"] == 200, "Fixture isolation probe failed")
        value = json.loads(response["body"])
        require(value["health_mode"] == "saas" and value["embedding_provider"] == "synthetic"
            and value["embedding_model"] == MODEL and value["embedding_dimension"] == 3,
            "Fixture embedding mode differs")
        require(value["total_external_calls"] == 0 and value["outbound_connection_attempts"] == 0,
                "Fixture attempted an external call")
        if populated:
            require(value["tenant_count"] == 2 and len(value["vector_stores"]) == 2,
                    "Both real tenant vector stores were not exercised")
        return value


class DockerRuntime(Runtime, Docker):
    def isolation(self, handle, *, populated=True):
        result = super().isolation(handle, populated=populated)
        require(result["process_uid"] > 0, "Recovery container unexpectedly runs as root")
        return result

    def prepare(self):
        version = self.preflight()
        with tempfile.TemporaryDirectory(prefix="zhiyuan-saas-build-") as folder:
            context = Path(folder) / "candidate"
            info = prepare_context(context)
            self.image, info["image_id"] = self.build(context, "saas-recovery")
        return {**info, "docker_version": version}

    def root(self, volume): return "/app/.data"
    def source_root(self, volume): return "/backup"

    def command(self, volume, argv, *, source=None, input=None, detach=False, role=None):
        name = f"{self.prefix}-{role or uuid.uuid4().hex[:8]}"
        self.containers.append(name)
        args = ["run", "--name", name, "--label", f"{OWNER_LABEL}={self.token}", "--network", "none",
                "--mount", f"type=volume,src={volume},dst=/app/.data"]
        if source:
            args += ["--mount", f"type=volume,src={source},dst=/backup,readonly"]
        for key, value in runtime_environment().items():
            args += ["--env", f"{key}={value}"]
        if detach: args += ["--detach"]
        if input is not None: args += ["--interactive"]
        result = self.run(*args, self.image, *argv, input=input, check=False)
        if detach:
            require(result.returncode == 0, "SaaS container launch failed")
            return name
        return result

    def start(self, role, volume, store):
        return self.command(volume, ["python", FIXTURE, "--fixture-root", self.root(volume), "--store-name", store,
            "--fixture-token", self.token, "--port", "8765"], detach=True, role=role)

    def stop(self, handle):
        self.run("stop", "--time", "30", handle)
        state = json.loads(self.run("container", "inspect", "--format", "{{json .State}}", handle).stdout)
        # Uvicorn can re-raise SIGTERM after successful lifespan cleanup (143).
        require(not state["Running"] and not state["OOMKilled"] and state["ExitCode"] in {0, 143},
                "Container did not shut down normally")
        logs = self.run("logs", "--tail", "30", handle)
        require("Application shutdown complete." in logs.stdout + logs.stderr,
                "Container lifespan cleanup was not confirmed")
    def origin(self, handle): return "http://127.0.0.1:8765"

    def transport(self, handle):
        def call(method, path, body=None, headers=None):
            require(path.startswith('/') and not path.startswith('//'), "Invalid API path")
            started = time.monotonic()
            try:
                result = subprocess.run(["docker", "exec", "-i", handle, "python", "-c", HTTP_PROBE],
                    input=json.dumps({"method":method,"path":path,"body":body,"headers":headers or {}}),
                    text=True, capture_output=True, timeout=30)
                require(result.returncode == 0, "HTTP probe failed")
                return json.loads(result.stdout)
            except (subprocess.SubprocessError, ValueError, OSError):
                # Authentication stdout can contain Cookie/CSRF. Never persist it on error.
                raise SmokeError("HTTP probe failed") from None
            finally:
                self.trace.append({"operation":"private HTTP probe", "elapsed_ms":round((time.monotonic()-started)*1000,2)})
        return call


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args): return None


class LocalRuntime(Runtime):
    def __init__(self, output):
        self.output, self.token = output, uuid.uuid4().hex
        self.temporary = tempfile.TemporaryDirectory(prefix="zhiyuan-saas-recovery-")
        self.processes, self.trace, self.logs = [], [], []
        self.process_logs = {}

    def prepare(self):
        info = prepare_context(Path(self.temporary.name) / "source-context")
        return {**info,"python":platform.python_version(),"system":platform.system(),"machine":platform.machine()}

    def volume(self, role):
        target=Path(self.temporary.name)/role
        target.mkdir(mode=0o700)
        return target

    def root(self, volume): return str(volume)
    def source_root(self, volume): return str(volume)

    def command(self, volume, argv, *, source=None, input=None):
        try:
            return subprocess.run([sys.executable,*argv[1:]],cwd=ROOT,input=input,text=True,capture_output=True,timeout=120)
        except (OSError,subprocess.SubprocessError):
            raise SmokeError("Local fixture command failed") from None

    def start(self, role, volume, store):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1",0));port=listener.getsockname()[1]
        log=(self.output/f"local-{role}.log").open('w',encoding='utf-8')
        self.logs.append(log)
        process=subprocess.Popen([sys.executable,str(ROOT/FIXTURE),"--fixture-root",str(volume),
            "--store-name",store,"--fixture-token",self.token,"--port",str(port)],cwd=ROOT,stdout=log,stderr=log)
        self.processes.append(process)
        self.process_logs[process] = Path(log.name)
        return process,port

    def stop(self, handle):
        process=handle[0]
        if process.poll() is None:
            process.terminate()
            try: process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                process.kill();process.wait(timeout=5)
                raise SmokeError("Fixture required forced termination; orderly recovery unproven") from None
        require(process.returncode in {0,-15}, "Fixture did not shut down normally")
        require("Application shutdown complete." in self.process_logs[process].read_text(),
                "Fixture lifespan cleanup was not confirmed")

    def origin(self, handle): return f"http://127.0.0.1:{handle[1]}"

    def transport(self, handle):
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
        def call(method,path,body=None,headers=None):
            require(path.startswith('/') and not path.startswith('//'), "Invalid API path")
            data=None if body is None else json.dumps(body).encode()
            request=urllib.request.Request(self.origin(handle)+path,data=data,method=method,
                headers={"Content-Type":"application/json",**(headers or {})})
            try:
                try: response=opener.open(request,timeout=20)
                except urllib.error.HTTPError as error: response=error
                with response:
                    payload=response.read(2*1024*1024+1)
                    require(len(payload)<=2*1024*1024,"HTTP response exceeded fixture limit")
                    return {"status":response.status,"body":payload.decode(),
                            "headers":{key.lower():value for key,value in response.headers.items()}}
            except (OSError,ValueError):
                raise SmokeError("Local HTTP probe failed") from None
        return call

    def cleanup(self):
        errors=[]
        for process in self.processes:
            try: self.stop((process,0))
            except (SmokeError,OSError,subprocess.SubprocessError): errors.append("Owned local fixture process cleanup failed")
        for log in self.logs: log.close()
        if not any(process.poll() is None for process in self.processes):
            try: self.temporary.cleanup()
            except OSError: errors.append("Owned temporary data cleanup failed")
        return {"errors":errors,"scope":"only this runner's processes and temporary directory"}


def execute(runtime, report):
    from saas_recovery_scenario import seed, verify, mutate
    report["candidate"]=runtime.prepare()
    primary,restored=runtime.volume("primary"),runtime.volume("restored")
    runtime.initialize(primary);runtime.initialize(restored)
    source=runtime.start("source",primary,"source")
    report["candidate"]["health"]=runtime.ready(source)
    stages=report["stages"]
    # Exercise the actual application lock before any tenant opens Qdrant.
    stages["live_empty_store_backup_refused"]={"passed":True,**runtime.backup(primary,expected_success=False)}
    state,signature=seed(runtime.transport(source),origin=runtime.origin(source))
    stages["seeded_two_organizations"]={"passed":True,"signature":signature}
    report["source_isolation"]=runtime.isolation(source)
    runtime.stop(source)
    report["backup"]=runtime.backup(primary)
    report["archive"]=runtime.fs(primary,"summary")
    source=runtime.start("source-updated",primary,"source");runtime.ready(source)
    # A new source document after the snapshot makes accidental original-volume overwrite observable.
    updated_state,updated_signature=mutate(runtime.transport(source),state,origin=runtime.origin(source))
    require(updated_signature!=signature,"Post-snapshot source mutation was not observed")
    stages["source_updated_after_snapshot"]={"passed":True,"signature":updated_signature}
    runtime.stop(source)
    before=runtime.fs(primary,"fingerprint")
    runtime.fs(primary,"tamper")
    stages["corrupt_archive_refused"]={"passed":True,**runtime.restore(primary,restored,corrupt=True,expected_success=False)}
    require(runtime.fs(restored,"absent")["absent"],"Corrupt restore published data")
    report["restore"]=runtime.restore(primary,restored)
    stages["restored_files_match_manifest"]={"passed":True,**runtime.fs(restored,"matches_manifest",source=primary)}
    restored_before=runtime.fs(restored,"fingerprint",store="restored")
    stages["existing_destination_refused"]={"passed":True,**runtime.restore(primary,restored,expected_success=False)}
    require(runtime.fs(restored,"fingerprint",store="restored")==restored_before,"Rejected restore changed destination")
    require(runtime.fs(primary,"fingerprint")==before,"Restore altered original stopped store")
    stages["original_files_unchanged"]={"passed":True,**before}
    recovered=runtime.start("recovered",restored,"restored");runtime.ready(recovered)
    recovered_signature=verify(runtime.transport(recovered),state,origin=runtime.origin(recovered))
    require(recovered_signature==signature,"Restored logical signature differs")
    stages["restored_login_queries_roles_usage"]={"passed":True,"signature":recovered_signature}
    report["restored_isolation"]=runtime.isolation(recovered)
    source=runtime.start("source-retained",primary,"source");runtime.ready(source)
    retained=verify(runtime.transport(source),updated_state,origin=runtime.origin(source))
    require(retained==updated_signature,"Original store lost post-snapshot changes")
    stages["original_retains_new_document"]={"passed":True,"signature":retained}
    runtime.isolation(source)
    runtime.stop(recovered);runtime.stop(source)
    report["shutdown"]={"source":"lifespan_completed", "restored":"lifespan_completed"}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime",choices=["local","docker"],default="docker")
    parser.add_argument("--output",type=Path,default=ROOT/".data/validation/saas-recovery/report.json")
    parser.add_argument("--internal-operation",action="store_true",help=argparse.SUPPRESS)
    args=parser.parse_args(argv)
    if args.internal_operation:
        try: print(json.dumps(file_operation(json.load(sys.stdin))));return 0
        except Exception: print(json.dumps({"error":"FIXTURE_FILE_OPERATION_FAILED"}));return 2
    args.output.parent.mkdir(parents=True,exist_ok=True)
    report={"started_at":datetime.now(timezone.utc).isoformat(),"status":"running","runtime":args.runtime,"stages":{},
        "isolation":{"sample_origin":"synthetic","embedding":"synthetic-constant-3d","vector_store":"real-qdrant-local",
            "generation":"local-rules",
            "runtime_network_guard":"docker-network-none" if args.runtime=="docker" else "python-socket-guard-not-os-firewall",
            "http_access":"docker-exec-loopback" if args.runtime=="docker" else "ephemeral-loopback-ports",
            "docker_published_ports":[],"host_bind_mounts":[]},
        "limits":["Same candidate image/source only; not cross-version upgrade or downgrade.",
            "Synthetic vectors validate persistence and isolation, not BGE downloads, cache or semantic quality.",
            "New login works after restoring the control database; old sessions are not claimed revoked.",
            "No production data, paid models, public TLS, Compose, capacity or customer acceptance.",
            "Docker builds need public registry network; local mode uses the current Python environment."],
        "cost":{"provider_invoice_amount":None,"model_tokens":None}}
    runtime=DockerRuntime(args.output.parent) if args.runtime=="docker" else LocalRuntime(args.output.parent)
    started=time.monotonic()
    try:
        execute(runtime,report);report["status"]="passed"
    except (Exception,KeyboardInterrupt) as error:
        report["status"]="failed"
        # Driver/transport failures must not serialize auth response bodies or private state.
        report["error"]=str(error) if isinstance(error,SmokeError) else type(error).__name__
    finally:
        report["cleanup"]=runtime.cleanup()
        if report["cleanup"]["errors"]: report["status"]="failed"
        report["command_trace"]=runtime.trace
        report["elapsed_ms"]=round((time.monotonic()-started)*1000,2)
        args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({"status":report["status"],"report":str(args.output),"error":report.get("error")},ensure_ascii=False))
    return 0 if report["status"]=="passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
