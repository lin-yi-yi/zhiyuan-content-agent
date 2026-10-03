"""Pilot observations use isolated synthetic runs and the real delivery gate."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from app.api.routes.pilot import router
from app.db.session import get_db
from app.models.agent_run import AgentRun, AgentStep
from app.models.draft import Draft
from app.models.evidence_note import EvidenceNote
from app.models.knowledge_base import KnowledgeChunk
from app.models.pilot_record import PilotRecord
from app.models.topic import Topic
from app.schemas.agent_run import AgentReviewCreate
from app.schemas.pilot import PilotRecordInput
from app.services.content_growth_agent import review_agent_run
from app.services.pilot import save_pilot_record
from app.services.review_lifecycle import invalidate_review_for_edit
from app.services.workflow_support import WorkflowConflict
from test_delivery import database, seed as seed_evidence


DAY = datetime(2026, 10, 3, 12)
PERIOD = {"start_date": "2026-10-03", "end_date": "2026-10-03"}


@pytest.fixture
def api(database):
    app = FastAPI()
    app.include_router(router, prefix="/api")

    def sessions():
        with database() as db:
            yield db

    app.dependency_overrides[get_db] = sessions
    with TestClient(app) as client:
        yield client


def seed_run(database, *, approved=False, status="failed", created_at=DAY, goal="合成效率任务"):
    with database() as db:
        run = AgentRun(goal=goal, status=status, created_at=created_at)
        if approved:
            topic = Topic(title=goal)
            db.add(topic)
            db.flush()
            draft = Draft(topic_id=topic.id, body_text=goal, selected_title=goal, status="awaiting_review")
            db.add(draft)
            db.flush()
            run.draft_id = draft.id
            run.status, run.current_step = "awaiting_review", "human_review"
            run.result_json = {"_request": {"use_rag": False}}
        db.add(run)
        db.flush()
        if approved:
            db.add(AgentStep(run_id=run.id, step_index=1, key="human_review", label="人工审核", status="awaiting_review"))
        db.commit()
        if approved:
            review_agent_run(run.id, AgentReviewCreate(decision="approve", note="合成审核"), db)
        return run.id


def payload(**changes):
    return {"version": 0, "cohort": "合成试点", "outcome": "accepted", "baseline_minutes": 60,
            "actual_work_minutes": 30, "support_minutes": 5, "baseline_reference": "同类人工任务计时单 A",
            "acceptance_reference": "合成验收记录 A", "note": "合成数据，不是客户收益", **changes}


def save(api, run_id, **changes):
    response = api.put(f"/api/pilot/records/{run_id}", json=payload(**changes))
    assert response.status_code == 200, response.text
    return response.json()


def report(api):
    response = api.get("/api/pilot/report", params=PERIOD)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    return response.json()


def test_unrecorded_run_differs_from_nonexistent_run(database, api):
    run_id = seed_run(database)
    response = api.get(f"/api/pilot/records/{run_id}")
    assert response.status_code == 200
    assert response.json() == {"run_id": run_id, "record": None}
    assert response.headers["cache-control"] == "no-store"
    assert api.get("/api/pilot/records/9999").status_code == 404
    assert api.put("/api/pilot/records/9999", json=payload(outcome="abandoned")).status_code == 404


def test_record_binds_approved_hash_and_does_not_export_actor_or_internal_data(database, api):
    run_id = seed_run(database, approved=True)
    record = save(api, run_id, cohort="  合成试点  ")
    assert record["version"] == 1 and record["source"] == "self_reported"
    assert record["cohort"] == "合成试点" and len(record["content_hash"]) == 64
    assert record["created_at"].endswith("Z") and record["updated_at"].endswith("Z")
    assert "created_by" not in record and "updated_by" not in record
    with database() as db:
        row = db.get(PilotRecord, run_id)
        assert row.created_by == row.updated_by == "local_user"
        assert row.content_hash == db.get(AgentRun, run_id).result_json["review"]["content_hash"]
    changed = save(api, run_id, version=1, actual_work_minutes=40)
    assert changed["version"] == 2 and changed["created_at"] == record["created_at"]
    assert api.get(f"/api/pilot/records/{run_id}").json()["record"] == changed
    assert api.put(f"/api/pilot/records/{run_id}", json=payload(version=1)).status_code == 409


@pytest.mark.parametrize("status", ["pending", "running", "awaiting_review", "rejected", "failed", "cancelled", "completed"])
def test_acceptance_cannot_bypass_real_approval(database, api, status):
    run_id = seed_run(database, status=status)
    response = api.put(f"/api/pilot/records/{run_id}", json=payload())
    assert response.status_code == 409
    with database() as db:
        assert db.get(PilotRecord, run_id) is None
    # Failed/no-draft work must remain reportable without requiring a success.
    record = save(api, run_id, outcome="abandoned")
    assert record["content_hash"] is None


@pytest.mark.parametrize("changes", [
    {"baseline_minutes": 0}, {"baseline_minutes": -1}, {"actual_work_minutes": -1}, {"support_minutes": -1},
    {"baseline_minutes": 1e-308}, {"baseline_minutes": 1e308}, {"actual_work_minutes": 1e308},
    {"support_minutes": 1e308}, {"cohort": "  "}, {"actual_work_minutes": True}, {"support_minutes": False},
    {"baseline_minutes": True}, {"actual_work_minutes": "4"}, {"version": True}, {"version": -1},
    {"baseline_reference": "  "}, {"acceptance_reference": "  "}, {"outcome": "verified"},
    {"cohort": "a" * 81}, {"note": "a" * 1001}, {"baseline_reference": "a" * 501},
    {"acceptance_reference": "a" * 501}, {"content_hash": "forged"}, {"source": "verified_customer"},
    {"updated_by": "forged-user"},
])
def test_inputs_reject_invalid_or_forged_observations(database, api, changes):
    run_id = seed_run(database, approved=True)
    response = api.put(f"/api/pilot/records/{run_id}", json=payload(**changes))
    assert response.status_code == 422, response.text
    assert api.get(f"/api/pilot/records/{run_id}").json()["record"] is None


@pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity", "1e999"])
def test_nonfinite_numbers_return_json_safe_validation(database, api, token):
    run_id = seed_run(database)
    response = api.put(f"/api/pilot/records/{run_id}", content=(
        '{"version":0,"baseline_minutes":' + token + ',"baseline_reference":"synthetic"}'
    ), headers={"Content-Type": "application/json"})
    assert response.status_code == 422, response.text
    assert isinstance(response.json()["detail"], list)


@pytest.mark.parametrize("change", ["draft", "approval_revoked", "expired", "revoked", "changed_chunk"])
def test_changed_content_or_evidence_makes_acceptance_stale(database, api, change):
    ids = seed_evidence(database)
    with database() as db:
        db.get(AgentRun, ids["run"]).created_at = DAY
        db.commit()
    save(api, ids["run"])
    with database() as db:
        if change == "draft":
            db.get(Draft, ids["draft"]).body_text += "新增未审核文字"
        elif change == "approval_revoked":
            draft = db.get(Draft, ids["draft"])
            invalidate_review_for_edit(draft, db, "合成内容编辑")
        elif change == "expired":
            db.get(EvidenceNote, ids["note"]).expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        elif change == "revoked":
            db.get(EvidenceNote, ids["note"]).review_status = "revoked"
        else:
            db.get(KnowledgeChunk, ids["chunk"]).content = "被更换的事实依据"
        db.commit()
    result = report(api)
    assert result["accepted_runs"] == result["comparable_runs"] == 0
    assert result["stale_acceptances"] == 1
    assert result["items"][0]["acceptance_current"] is False
    assert result["items"][0]["record"]["outcome"] == "accepted"
    assert result["saved_minutes_total"] is None
    assert api.put(f"/api/pilot/records/{ids['run']}", json=payload(version=1)).status_code == 409


def test_reapproval_of_changed_content_requires_explicit_new_acceptance(database, api):
    run_id = seed_run(database, approved=True)
    original = save(api, run_id)
    with database() as db:
        run = db.get(AgentRun, run_id)
        draft = db.get(Draft, run.draft_id)
        invalidate_review_for_edit(draft, db, "合成修改")
        draft.body_text += "重新核对后的修改"
        db.commit()
        review_agent_run(run_id, AgentReviewCreate(decision="approve"), db)
    assert report(api)["stale_acceptances"] == 1
    changed = save(api, run_id, version=1, acceptance_reference="新版本合成验收记录 B")
    assert changed["content_hash"] != original["content_hash"]
    assert report(api)["accepted_runs"] == 1


def test_report_includes_failures_missing_data_and_negative_savings(database, api):
    accepted = seed_run(database, approved=True)
    negative = seed_run(database, approved=True)
    incomplete = seed_run(database, approved=True)
    missing = seed_run(database)
    rejected = seed_run(database)
    abandoned = seed_run(database)
    pending = seed_run(database)
    save(api, accepted, baseline_minutes=60, actual_work_minutes=30, support_minutes=5)
    save(api, negative, baseline_minutes=10, actual_work_minutes=50, support_minutes=15)
    save(api, incomplete, support_minutes=None)
    save(api, rejected, outcome="rejected")
    save(api, abandoned, outcome="abandoned")
    save(api, pending, outcome="pending", baseline_minutes=None, actual_work_minutes=None, support_minutes=None)
    result = report(api)
    assert result["total_runs"] == 7 and result["recorded_runs"] == 6 and result["missing_records"] == 1
    assert result["accepted_runs"] == 3 and result["comparable_runs"] == 2
    assert result["rejected_runs"] == result["abandoned_runs"] == result["pending_runs"] == 1
    assert result["baseline_minutes_total"] == 70 and result["actual_minutes_total"] == 100
    assert result["saved_minutes_total"] == -30 and result["savings_rate"] == pytest.approx(-30 / 70)
    assert {item["run_id"] for item in result["items"]} == {accepted, negative, incomplete, missing, rejected, abandoned, pending}
    assert next(item for item in result["items"] if item["run_id"] == missing)["record"] is None


def test_zero_is_measured_but_missing_is_not_zero(database, api):
    run_id = seed_run(database, approved=True)
    save(api, run_id, baseline_minutes=10, actual_work_minutes=0, support_minutes=0)
    result = report(api)
    assert result["actual_minutes_total"] == 0 and result["savings_rate"] == 1
    save(api, run_id, version=1, baseline_minutes=10, actual_work_minutes=None, support_minutes=0)
    result = report(api)
    assert result["accepted_runs"] == 1 and result["comparable_runs"] == 0
    for key in ("baseline_minutes_total", "actual_minutes_total", "saved_minutes_total", "savings_rate"):
        assert result[key] is None


def test_report_uses_run_utc_creation_date_with_inclusive_days(database, api):
    before = seed_run(database, created_at=DAY.replace(day=2, hour=23, minute=59, second=59))
    first = seed_run(database, created_at=DAY.replace(hour=0, minute=0, second=0))
    last = seed_run(database, created_at=DAY.replace(hour=23, minute=59, second=59, microsecond=999999))
    after = seed_run(database, created_at=DAY.replace(day=4, hour=0))
    save(api, before, outcome="abandoned")
    save(api, first, outcome="abandoned")
    save(api, last, outcome="abandoned")
    save(api, after, outcome="abandoned")
    result = report(api)
    assert {item["run_id"] for item in result["items"]} == {first, last}
    assert all(item["created_at"].endswith("Z") for item in result["items"])
    empty = api.get("/api/pilot/report", params={"start_date": "2025-01-01", "end_date": "2025-01-01"}).json()
    assert empty["items"] == [] and empty["total_runs"] == 0 and empty["saved_minutes_total"] is None
    assert api.get("/api/pilot/report", params={"start_date": "2026-10-04", "end_date": "2026-10-03"}).status_code == 422
    assert api.get("/api/pilot/report").status_code == 422
    assert api.get("/api/pilot/report", params={"start_date": "bad", "end_date": "2026-10-03"}).status_code == 422


@pytest.mark.parametrize("initial_version", [0, 1])
def test_simultaneous_inserts_and_updates_have_one_winner(database, api, initial_version):
    run_id = seed_run(database)
    if initial_version:
        save(api, run_id, outcome="pending")
    barrier = Barrier(2)

    def compete(label):
        with database() as db:
            barrier.wait(timeout=5)
            try:
                result = save_pilot_record(run_id, PilotRecordInput(version=initial_version, note=label), db)
                return result.version, label
            except WorkflowConflict:
                return "conflict", label

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(compete, ("first", "second")))
    assert sorted(str(result[0]) for result in results) == [str(initial_version + 1), "conflict"]
    with database() as db:
        assert db.query(PilotRecord).count() == 1
        winner = next(label for version, label in results if version != "conflict")
        assert db.get(PilotRecord, run_id).note == winner


def test_failed_acceptance_update_keeps_previous_record(database, api):
    run_id = seed_run(database)
    original = save(api, run_id, outcome="abandoned")
    assert api.put(f"/api/pilot/records/{run_id}", json=payload(version=1)).status_code == 409
    assert api.get(f"/api/pilot/records/{run_id}").json()["record"] == original


def test_database_rejects_negative_effort_even_without_schema(database, api):
    run_id = seed_run(database)
    save(api, run_id, outcome="pending")
    with database() as db:
        db.get(PilotRecord, run_id).support_minutes = -1
        with pytest.raises(IntegrityError):
            db.commit()
