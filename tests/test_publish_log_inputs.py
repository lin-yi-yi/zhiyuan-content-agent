"""Metrics and publication instants retain valid measurements and honest baselines."""
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401
from app.api.routes.publish_logs import router
from app.db.session import Base, get_db
from app.models.content_prediction import ContentPrediction
from app.models.draft import Draft
from app.models.metric import Metric
from app.models.publish_log import PublishLog
from app.models.topic import Topic


PLATFORM = "xiaohongshu"
COUNTS = ("views", "likes", "favorites", "comments", "shares", "new_followers")
OPTIONAL_COUNTS = ("impressions", "profile_visits")
RATES = ("click_rate", "follow_conversion_rate")


@pytest.fixture
def database(tmp_path, monkeypatch):
    monkeypatch.setenv("SAAS_MODE", "false")
    engine = create_engine(f"sqlite:///{tmp_path / 'publish-inputs.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    engine.dispose()


@pytest.fixture
def client(database):
    application = FastAPI()
    application.include_router(router, prefix="/api")

    def sessions():
        with database() as db:
            yield db

    application.dependency_overrides[get_db] = sessions
    with TestClient(application) as api:
        yield api


@pytest.fixture
def draft_id(database):
    with database() as db:
        topic = Topic(title="合成发布时刻测试")
        db.add(topic)
        db.flush()
        draft = Draft(topic_id=topic.id, body_text="合成测试资料，无真实平台数据。")
        db.add(draft)
        db.commit()
        return draft.id


def publish(client, draft_id, **changes):
    response = client.post("/api/publish-logs", json={"draft_id": draft_id, "platform": PLATFORM, **changes})
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
def log_id(client, draft_id):
    return publish(client, draft_id)["id"]


@pytest.mark.parametrize("field", COUNTS + OPTIONAL_COUNTS)
def test_negative_count_is_422_and_does_not_write_metric(client, database, log_id, field):
    response = client.post(f"/api/publish-logs/{log_id}/metrics", json={field: -1})
    assert response.status_code == 422, response.text
    assert response.json()["detail"][0]["loc"] == ["body", field]
    with database() as db:
        assert db.query(Metric).count() == 0


@pytest.mark.parametrize("field", RATES)
@pytest.mark.parametrize("value", [-0.01, 1.01, "NaN", "Infinity", "-Infinity"])
def test_invalid_rate_is_422_without_writing_or_settling(client, database, log_id, draft_id, field, value):
    with database() as db:
        baseline = ContentPrediction(draft_id=draft_id, platform=PLATFORM, publish_log_id=log_id, status="locked")
        db.add(baseline)
        db.commit()
        baseline_id = baseline.id
    response = client.post(f"/api/publish-logs/{log_id}/metrics", json={field: value})
    assert response.status_code == 422, response.text
    with database() as db:
        assert db.query(Metric).count() == 0
        assert db.get(ContentPrediction, baseline_id).status == "locked"


@pytest.mark.parametrize("field", RATES)
@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity", "1e999"])
def test_raw_nonfinite_json_rate_returns_422_instead_of_error_serialization_500(client, log_id, field, literal):
    response = client.post(f"/api/publish-logs/{log_id}/metrics", content=f'{{"{field}":{literal}}}',
                           headers={"Content-Type": "application/json"})
    assert response.status_code == 422, response.text
    assert response.json()["detail"][0]["loc"] == ["body", field]


@pytest.mark.parametrize("explicit", [False, True])
def test_explicit_zero_and_omitted_default_counts_are_real_records(client, database, log_id, explicit):
    payload = {field: 0 for field in COUNTS + OPTIONAL_COUNTS + RATES} if explicit else {}
    response = client.post(f"/api/publish-logs/{log_id}/metrics", json=payload)
    assert response.status_code == 201, response.text
    result = response.json()
    assert all(result[field] == 0 for field in COUNTS)
    assert all(result[field] == (0 if explicit else None) for field in OPTIONAL_COUNTS + RATES)
    with database() as db:
        assert db.query(Metric).count() == 1
    assert client.get(f"/api/publish-logs/{log_id}/metrics").json()[0]["views"] == 0


def test_rate_upper_bound_one_is_valid(client, log_id):
    response = client.post(f"/api/publish-logs/{log_id}/metrics", json={field: 1 for field in RATES})
    assert response.status_code == 201, response.text
    assert all(response.json()[field] == 1 for field in RATES)


@pytest.mark.parametrize("timestamp", ["2026-01-16T00:30:00+08:00", "2026-01-15T16:30:00Z", "2026-01-15T16:30:00"])
def test_publication_is_stored_utc_naive_and_returned_explicit_utc(client, database, draft_id, timestamp):
    result = publish(client, draft_id, published_at=timestamp)
    expected = datetime(2026, 1, 15, 16, 30)
    with database() as db:
        actual = db.get(PublishLog, result["id"]).published_at
        assert actual == expected and actual.tzinfo is None
    for output in (result, client.get("/api/publish-logs").json()[0]):
        published = datetime.fromisoformat(output["published_at"].replace("Z", "+00:00"))
        assert published == expected.replace(tzinfo=timezone.utc)
        for field in ("published_at", "created_at", "updated_at"):
            parsed = datetime.fromisoformat(output[field].replace("Z", "+00:00"))
            assert parsed.utcoffset() == timedelta(0)


def test_missing_publication_timestamp_remains_unknown(client, draft_id):
    assert publish(client, draft_id)["published_at"] is None


def seed_prediction(database, draft_id, stamp, **changes):
    with database() as db:
        row = ContentPrediction(draft_id=draft_id, platform=PLATFORM, created_at=stamp, **changes)
        db.add(row)
        db.commit()
        return row.id


def test_backfilled_publication_does_not_link_after_the_fact_prediction(client, database, draft_id):
    prediction_id = seed_prediction(database, draft_id, datetime(2026, 1, 15, 17))
    log = publish(client, draft_id, published_at="2026-01-16T00:30:00+08:00")
    response = client.post(f"/api/publish-logs/{log['id']}/metrics", json={"views": 0})
    assert response.status_code == 201, response.text
    with database() as db:
        row = db.get(ContentPrediction, prediction_id)
        assert row.publish_log_id is None and row.status == "draft"


def test_automatic_link_chooses_latest_eligible_unlinked_prediction(client, database, draft_id):
    cutoff = datetime(2026, 1, 15, 16, 30)
    oldest = seed_prediction(database, draft_id, cutoff - timedelta(hours=2))
    chosen = seed_prediction(database, draft_id, cutoff - timedelta(hours=1))
    settled = seed_prediction(database, draft_id, cutoff - timedelta(minutes=30), status="settled")
    too_late = seed_prediction(database, draft_id, cutoff + timedelta(minutes=1))
    with database() as db:
        existing_log = PublishLog(draft_id=draft_id, platform=PLATFORM, published_at=cutoff)
        db.add(existing_log)
        db.commit()
        previous_log_id = existing_log.id
    linked = seed_prediction(database, draft_id, cutoff - timedelta(minutes=15), publish_log_id=previous_log_id, status="locked")
    created = publish(client, draft_id, published_at="2026-01-16T00:30:00+08:00")
    with database() as db:
        assert db.get(ContentPrediction, chosen).publish_log_id == created["id"]
        assert db.get(ContentPrediction, chosen).status == "locked"
        assert db.get(ContentPrediction, linked).publish_log_id == previous_log_id
        for row_id in (oldest, settled, too_late):
            assert db.get(ContentPrediction, row_id).publish_log_id is None


def test_future_publication_still_uses_record_creation_cutoff(client, database, draft_id):
    future = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=1)
    prediction_id = seed_prediction(database, draft_id, future)
    publish(client, draft_id, published_at=(future + timedelta(days=1)).isoformat())
    with database() as db:
        assert db.get(ContentPrediction, prediction_id).publish_log_id is None
