"""Transactional operator decisions over real temporary SQLite ledgers."""
from concurrent.futures import ThreadPoolExecutor
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from threading import Barrier

import pytest
from sqlalchemy import event as sql_event, func, select

from app.saas import commerce, store
from app.saas.commerce_models import UsageCounter, UsageEvent
from app.saas.models import AuditLog
from app.saas.usage_reconciliation import reconcile_usage
from test_saas_commerce import control, ORG_A, ORG_B


ROOT = Path(__file__).resolve().parents[1]
REQUEST = "synthetic-private-request"
DIGEST = hashlib.sha256(REQUEST.encode()).hexdigest()


def options(**changes):
    return {"operation_id": "d" * 32, "decision": "confirmed_not_accepted",
            "operator_label": "synthetic-operator", "operator_uid": 1001,
            "reason": "Checked synthetic incident: no HTTP acceptance", "evidence_ref": "INC-SYNTHETIC-001",
            **changes}


def reconcile(**changes):
    return reconcile_usage(ORG_A, DIGEST, **options(**changes))


def ledger(token):
    with store.session_factory() as db:
        item = db.get(UsageEvent, token)
        count = db.get(UsageCounter, (item.organization_id, item.period, item.metric))
        return item.status, count.reserved, count.settled, db.scalar(select(func.count()).select_from(AuditLog))


def test_preview_keeps_reservation_and_has_no_audit_or_private_token(control):
    token = commerce.reserve_usage(ORG_A, REQUEST, amount=2)
    result = reconcile()
    assert result["mode"] == "preview" and result["after"] == "refunded"
    assert result["reserved_after"] == result["settled_after"] == 0
    assert ledger(token) == ("reserved", 2, 0, 0)
    assert token not in json.dumps(result) and REQUEST not in json.dumps(result)


@pytest.mark.parametrize("decision,status,settled", [("confirmed_accepted", "settled", 2),
                                                    ("confirmed_not_accepted", "refunded", 0)])
def test_decision_and_audit_commit_together_and_replay_exactly_once(control, decision, status, settled):
    token = commerce.reserve_usage(ORG_A, REQUEST, amount=2)
    first = reconcile(decision=decision, apply=True)
    assert first["after"] == status and first["mode"] == "applied"
    assert ledger(token) == (status, 0, settled, 1)
    assert reconcile(decision=decision, apply=True) == {**first, "mode": "replayed"}
    assert ledger(token) == (status, 0, settled, 1)
    with store.session_factory() as db:
        audit = db.get(AuditLog, "d" * 32)
        assert audit.actor_user_id is None
        assert audit.details_json["operator_uid"] == 1001
        assert audit.details_json["operator_label"] == "synthetic-operator"
        assert audit.details_json["reason"] == options()["reason"]
        assert audit.details_json["evidence_ref"] == "INC-SYNTHETIC-001"
        assert audit.details_json["result"]["before"] == "reserved"
        assert token not in json.dumps(audit.details_json)
    # A late automatic finalizer cannot reverse the operator's terminal decision.
    assert commerce.finalize_usage(token, status != "settled")["status"] == status


@pytest.mark.parametrize("changed", [{"decision": "confirmed_accepted"}, {"reason": "different reason"},
                                    {"evidence_ref": "OTHER"}, {"operator_label": "other"}, {"operator_uid": 1002}])
def test_reusing_operation_with_changed_inputs_is_rejected(control, changed):
    token = commerce.reserve_usage(ORG_A, REQUEST)
    reconcile(apply=True)
    with pytest.raises(commerce.UsageConflict):
        reconcile(**changed, apply=True)
    assert ledger(token) == ("refunded", 0, 0, 1)


def test_wrong_org_or_short_hash_cannot_target_another_reservation(control):
    token = commerce.reserve_usage(ORG_A, REQUEST)
    with pytest.raises(commerce.CommerceError):
        reconcile_usage(ORG_B, DIGEST, **options(apply=True))
    with pytest.raises(commerce.CommerceError):
        reconcile_usage(ORG_A, DIGEST[:12], **options(apply=True))
    assert ledger(token) == ("reserved", 1, 0, 0)
    reconcile(apply=True)
    with pytest.raises(commerce.UsageConflict):
        reconcile_usage(ORG_B, DIGEST, **options(apply=True))


@pytest.mark.parametrize("changed", [{"decision": "unknown"}, {"decision": []}, {"reason": ""}, {"evidence_ref": " "},
                                    {"reason": "line\nbreak"}, {"operator_label": ""},
                                    {"operator_uid": True}, {"apply": "yes"}, {"operation_id": "wrong"}])
def test_unknown_or_unattributed_decision_never_changes_ledger(control, changed):
    token = commerce.reserve_usage(ORG_A, REQUEST)
    with pytest.raises(commerce.CommerceError):
        reconcile(**changed)
    assert ledger(token) == ("reserved", 1, 0, 0)


def test_audit_failure_rolls_back_the_entire_resolution(control):
    token = commerce.reserve_usage(ORG_A, REQUEST)
    def fail_audit(*_):
        raise RuntimeError("synthetic audit storage failure")
    sql_event.listen(AuditLog, "before_insert", fail_audit)
    try:
        with pytest.raises(RuntimeError):
            reconcile(apply=True)
    finally:
        sql_event.remove(AuditLog, "before_insert", fail_audit)
    assert ledger(token) == ("reserved", 1, 0, 0)


def test_preview_is_not_authorization_to_override_a_finished_event(control):
    token = commerce.reserve_usage(ORG_A, REQUEST)
    reconcile()
    commerce.finalize_usage(token, True)
    with pytest.raises(commerce.UsageConflict):
        reconcile(apply=True)
    assert ledger(token) == ("settled", 0, 1, 0)


def test_damaged_counter_is_not_silently_recomputed(control):
    token = commerce.reserve_usage(ORG_A, REQUEST)
    with store.session_factory() as db:
        item = db.get(UsageEvent, token)
        db.get(UsageCounter, (ORG_A, item.period, item.metric)).reserved += 1
        db.commit()
    with pytest.raises(commerce.CommerceError, match="不一致"):
        reconcile(apply=True)
    assert ledger(token) == ("reserved", 2, 0, 0)


def test_old_month_resolution_does_not_change_current_month(control, monkeypatch):
    old_period = {"label": "2025-12"}
    with monkeypatch.context() as old:
        old.setattr(commerce, "_period", lambda: old_period)
        token = commerce.reserve_usage(ORG_A, REQUEST)
    current = commerce.reserve_usage(ORG_A, "synthetic-current-month")
    result = reconcile(apply=True)
    assert result["period"] == "2025-12"
    assert ledger(token) == ("refunded", 0, 0, 1)
    assert ledger(current) == ("reserved", 1, 0, 1)


def test_concurrent_exact_retries_commit_one_adjustment_and_one_audit(control):
    token = commerce.reserve_usage(ORG_A, REQUEST)
    barrier = Barrier(6)
    def act(_):
        barrier.wait()
        return reconcile(apply=True)["mode"]
    with ThreadPoolExecutor(max_workers=6) as pool:
        modes = list(pool.map(act, range(6)))
    assert modes.count("applied") == 1 and modes.count("replayed") == 5
    assert ledger(token) == ("refunded", 0, 0, 1)


def test_concurrent_conflicting_decisions_cannot_overwrite_each_other(control):
    token = commerce.reserve_usage(ORG_A, REQUEST)
    barrier = Barrier(2)
    def act(index):
        barrier.wait()
        try:
            return reconcile(operation_id=str(index + 1) * 32,
                             decision="confirmed_accepted" if index else "confirmed_not_accepted", apply=True)["after"]
        except commerce.UsageConflict:
            return "conflict"
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(act, range(2)))
    assert outcomes.count("conflict") == 1
    winner = next(value for value in outcomes if value != "conflict")
    assert ledger(token) == (winner, 0, int(winner == "settled"), 1)


def cli(directory, *extra, include_directory=True):
    command = [sys.executable, str(ROOT / "scripts/saas_admin.py")]
    if include_directory:
        command += ["--data-dir", str(directory)]
    command += ["reconcile-usage", "--organization", ORG_A, "--request-hash", DIGEST,
                "--operation-id", "e" * 32, "--decision", "confirmed_not_accepted",
                "--operator", "synthetic-cli", "--reason", "Synthetic request never accepted",
                "--evidence-ref", "INC-SYNTHETIC-CLI", *extra]
    return subprocess.run(command, env={**os.environ, "SAAS_ENV_FILE": str(directory / "never-load.env")},
                          capture_output=True, text=True, timeout=15)


def test_cli_requires_explicit_directory_offline_confirmation_and_inactive_lock(control):
    token = commerce.reserve_usage(ORG_A, REQUEST)
    directory = Path(os.environ["SAAS_DATA_DIR"])
    assert cli(directory, include_directory=False).returncode == 2
    assert cli(directory, "--apply").returncode == 2
    preview = cli(directory)
    assert preview.returncode == 0, preview.stderr
    assert json.loads(preview.stdout)["mode"] == "preview"
    with (directory / ".service.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        blocked = cli(directory, "--apply", "--offline-confirm")
        assert blocked.returncode == 2 and "活跃" in blocked.stderr
        assert ledger(token) == ("reserved", 1, 0, 0)
    applied = cli(directory, "--apply", "--offline-confirm")
    assert applied.returncode == 0, applied.stderr
    assert json.loads(applied.stdout)["mode"] == "applied"
    assert json.loads(cli(directory, "--apply", "--offline-confirm").stdout)["mode"] == "replayed"
    assert ledger(token) == ("refunded", 0, 0, 1)
    with store.session_factory() as db:
        assert db.get(AuditLog, "e" * 32).details_json["operator_uid"] == os.geteuid()
