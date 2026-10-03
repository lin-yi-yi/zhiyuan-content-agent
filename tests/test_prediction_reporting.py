"""Immutable prediction baselines and measured-only reporting on synthetic SQLite."""
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import sys

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401
from app.api.routes import predictions, publish_logs, reports
from app.db.session import Base, get_db
from app.models.content_prediction import ContentPrediction
from app.models.draft import Draft
from app.models.metric import Metric
from app.models.publish_log import PublishLog
from app.models.topic import Topic


STAMP = datetime(2026, 1, 15, 12)
PLATFORM = "xiaohongshu"


@pytest.fixture
def database(monkeypatch, tmp_path):
    monkeypatch.setenv("SAAS_MODE", "false")
    engine = create_engine(f"sqlite:///{tmp_path / 'prediction-reporting.db'}",
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    engine.dispose()


@pytest.fixture
def client(database):
    application = FastAPI()
    for router in (predictions.router, publish_logs.router, reports.router):
        application.include_router(router, prefix="/api")

    def sessions():
        with database() as db:
            yield db

    application.dependency_overrides[get_db] = sessions
    with TestClient(application) as api:
        yield api


def draft_id(database, label="synthetic"):
    with database() as db:
        topic = Topic(title=f"合成选题-{label}", content_angle=f"角度-{label}")
        db.add(topic)
        db.flush()
        draft = Draft(topic_id=topic.id, body_text="合成测试草稿，不包含真实账号或发布数据。",
                      template_key=f"template-{label}", content_type=f"type-{label}")
        db.add(draft)
        db.commit()
        return draft.id


def prediction(client, database, draft, **changes):
    payload = {"draft_id": draft, "platform": PLATFORM, "predicted_views": 1000}
    payload.update(changes)
    response = client.post("/api/predictions", json=payload)
    assert response.status_code == 201, response.text
    result = response.json()
    # Fixed synthetic timestamps make publication cutoffs independent of test speed.
    with database() as db:
        db.get(ContentPrediction, result["id"]).created_at = STAMP - timedelta(hours=1)
        db.commit()
    return result


def publish(client, database, draft, *, platform=PLATFORM):
    response = client.post("/api/publish-logs", json={"draft_id": draft, "platform": platform})
    assert response.status_code == 201, response.text
    result = response.json()
    with database() as db:
        row = db.get(PublishLog, result["id"])
        row.created_at, row.published_at = STAMP, STAMP
        db.commit()
    return result


def metric(client, log_id, views, **changes):
    payload = {"views": views}
    payload.update(changes)
    response = client.post(f"/api/publish-logs/{log_id}/metrics", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def report(client):
    response = client.post("/api/reports/weekly", json={"start_date": "2026-01-15", "end_date": "2026-01-15"})
    assert response.status_code == 201, response.text
    return response.json()


def test_unpublished_draft_prediction_remains_editable(client, database):
    baseline = prediction(client, database, draft_id(database))
    response = client.put(f"/api/predictions/{baseline['id']}", json={"predicted_views": 1200, "predicted_favorites": 6})
    assert response.status_code == 200, response.text
    assert response.json()["predicted_views"] == 1200
    assert response.json()["predicted_save_rate"] == pytest.approx(0.005)


def test_locked_and_settled_values_cannot_rewrite_original_prediction_error(client, database):
    draft = draft_id(database)
    baseline = prediction(client, database, draft)
    log = publish(client, database, draft)
    for stage in ("locked", "settled"):
        if stage == "settled":
            metric(client, log["id"], 100)
        assert client.get(f"/api/predictions/{baseline['id']}").json()["status"] == stage
        for changes in ({"predicted_views": 100}, {"status": "draft"}, {"publish_log_id": None},
                        {"rationale": "事后重写的合成解释"}):
            denied = client.put(f"/api/predictions/{baseline['id']}", json=changes)
            assert denied.status_code == 409, denied.text
    current = client.get(f"/api/predictions/{baseline['id']}").json()
    assert current["predicted_views"] == 1000 and current["publish_log_id"] == log["id"]
    # A newer historical row created after publication must not replace the valid baseline.
    with database() as db:
        db.add(ContentPrediction(draft_id=draft, platform=PLATFORM, publish_log_id=log["id"],
                                 predicted_views=100, status="settled", created_at=STAMP + timedelta(minutes=1)))
        db.commit()
    calibration = report(client)["prediction_calibration"]
    assert calibration["prediction_count"] == 1
    assert calibration["top_misses"][0]["prediction"]["id"] == baseline["id"]
    assert calibration["top_misses"][0]["prediction_error"]["views_error_rate"] == pytest.approx(-0.9)


def test_legacy_locked_or_linked_records_are_immutable_independently(client, database):
    for status, linked in (("locked", False), ("settled", False), ("draft", True)):
        draft = draft_id(database, f"legacy-{status}")
        with database() as db:
            log = None
            if linked:
                log = PublishLog(draft_id=draft, platform=PLATFORM, created_at=STAMP, published_at=STAMP)
                db.add(log)
                db.flush()
            baseline = ContentPrediction(draft_id=draft, platform=PLATFORM, status=status,
                                         publish_log_id=log.id if log else None, predicted_views=1000,
                                         created_at=STAMP - timedelta(hours=1))
            db.add(baseline)
            db.commit()
            prediction_id = baseline.id
        denied = client.put(f"/api/predictions/{prediction_id}", json={"predicted_views": 100})
        assert denied.status_code == 409, denied.text
        assert client.get(f"/api/predictions/{prediction_id}").json()["predicted_views"] == 1000


@pytest.mark.parametrize("include_log_id", [False, True])
def test_new_prediction_cannot_be_added_after_publication(client, database, include_log_id):
    draft = draft_id(database)
    log = publish(client, database, draft)
    metric(client, log["id"], 100)
    payload = {"draft_id": draft, "platform": PLATFORM, "predicted_views": 100}
    if include_log_id:
        payload["publish_log_id"] = log["id"]
    denied = client.post("/api/predictions", json=payload)
    assert denied.status_code == 409, denied.text
    with database() as db:
        assert db.query(ContentPrediction).count() == 0


def test_publication_on_another_platform_does_not_block_a_new_baseline(client, database):
    draft = draft_id(database)
    publish(client, database, draft)
    baseline = prediction(client, database, draft, platform="douyin")
    assert baseline["platform"] == "douyin"


def test_attach_is_idempotent_for_same_log_and_cannot_reassociate(client, database):
    draft = draft_id(database)
    baseline = prediction(client, database, draft)
    first = publish(client, database, draft)
    second = publish(client, database, draft)
    same = client.post(f"/api/predictions/{baseline['id']}/attach-log", json={"publish_log_id": first["id"]})
    assert same.status_code == 200, same.text
    other = client.post(f"/api/predictions/{baseline['id']}/attach-log", json={"publish_log_id": second["id"]})
    assert other.status_code == 409, other.text
    assert client.get(f"/api/predictions/{baseline['id']}").json()["publish_log_id"] == first["id"]


def test_unlinked_prediction_cannot_replace_an_existing_linked_baseline(client, database):
    draft = draft_id(database)
    older = prediction(client, database, draft, predicted_views=100)
    chosen = prediction(client, database, draft)
    log = publish(client, database, draft)
    rewritten = client.put(f"/api/predictions/{older['id']}", json={"predicted_views": 1000})
    assert rewritten.status_code == 409, rewritten.text
    denied = client.post(f"/api/predictions/{older['id']}/attach-log", json={"publish_log_id": log["id"]})
    assert denied.status_code == 409, denied.text
    metric(client, log["id"], 100)
    snapshot = report(client)["prediction_calibration"]["top_misses"][0]
    assert snapshot["prediction"]["id"] == chosen["id"]
    assert snapshot["prediction_error"]["views_error_rate"] == pytest.approx(-0.9)


@pytest.mark.parametrize("publication_offset, prediction_offset, expected", [
    (-10, -10, 200),  # Equality at the earlier cutoff is allowed.
    (-10, -5, 409),   # Before record creation, but after actual publication.
    (10, 5, 409),     # Future publication cannot move the creation cutoff forward.
])
def test_attach_uses_earlier_publication_or_record_creation_cutoff(
    client, database, publication_offset, prediction_offset, expected,
):
    if expected == 200:
        aware_instant = STAMP.replace(tzinfo=timezone.utc).astimezone(timezone(timedelta(hours=8)))
        aware_prediction = ContentPrediction(created_at=aware_instant)
        naive_utc_log = PublishLog(created_at=STAMP, published_at=aware_instant)
        assert predictions.prediction_precedes_publication(aware_prediction, naive_utc_log)
        aware_prediction.created_at = aware_instant + timedelta(microseconds=1)
        assert not predictions.prediction_precedes_publication(aware_prediction, naive_utc_log)
    draft = draft_id(database)
    with database() as db:
        log = PublishLog(draft_id=draft, platform=PLATFORM, created_at=STAMP,
                         published_at=STAMP + timedelta(minutes=publication_offset))
        baseline = ContentPrediction(draft_id=draft, platform=PLATFORM, predicted_views=1000,
                                     status="draft", created_at=STAMP + timedelta(minutes=prediction_offset))
        db.add_all([log, baseline])
        db.commit()
        log_id, prediction_id = log.id, baseline.id
    response = client.post(f"/api/predictions/{prediction_id}/attach-log", json={"publish_log_id": log_id})
    assert response.status_code == expected, response.text
    with database() as db:
        assert db.get(ContentPrediction, prediction_id).publish_log_id == (log_id if expected == 200 else None)


def test_report_excludes_unlinked_mismatched_late_draft_and_ambiguous_baselines(client, database):
    for kind in ("unlinked", "wrong-draft", "wrong-platform", "late", "draft-status", "ambiguous"):
        draft = draft_id(database, kind)
        other_draft = draft_id(database, f"other-{kind}") if kind == "wrong-draft" else draft
        with database() as db:
            log = PublishLog(draft_id=draft, platform=PLATFORM, created_at=STAMP, published_at=STAMP)
            db.add(log)
            db.flush()
            values = dict(draft_id=other_draft, platform="douyin" if kind == "wrong-platform" else PLATFORM,
                          publish_log_id=None if kind == "unlinked" else log.id, predicted_views=1000,
                          status="draft" if kind == "draft-status" else "locked",
                          created_at=STAMP + timedelta(minutes=1) if kind == "late" else STAMP - timedelta(hours=1))
            db.add(ContentPrediction(**values))
            if kind == "ambiguous":
                db.add(ContentPrediction(**values))
            db.add(Metric(publish_log_id=log.id, views=100))
            db.commit()
    result = report(client)
    assert result["performance_summary"]["data_coverage"]["measured_posts"] == 6
    assert result["prediction_calibration"]["prediction_count"] == 0
    assert result["prediction_calibration"]["top_misses"] == []
    assert result["prediction_calibration"]["measured_without_baseline_count"] == 6
    assert result["prediction_calibration"]["relative_error_sample_count"] == 0
    assert result["prediction_calibration"]["undefined_relative_error_count"] == 0
    assert result["prediction_calibration"]["avg_abs_view_error_rate"] is None


def test_missing_metrics_are_excluded_but_real_zero_is_measured(client, database):
    created = {}
    for label in ("missing", "zero", "measured"):
        draft = draft_id(database, label)
        prediction(client, database, draft)
        created[label] = publish(client, database, draft)
    metric(client, created["zero"]["id"], 0)
    metric(client, created["measured"]["id"], 100)
    result = report(client)
    summary = result["performance_summary"]
    assert summary["data_coverage"] == {
        "published_posts": 3, "measured_posts": 2, "missing_metric_posts": 1,
        "missing_metric_log_ids": [created["missing"]["id"]],
    }
    assert summary["totals"]["posts"] == 3 and summary["totals"]["views"] == 100
    calibration = result["prediction_calibration"]
    assert calibration["prediction_count"] == 2
    assert calibration["excluded_missing_metrics"] == 1
    assert calibration["measured_without_baseline_count"] == 0
    assert calibration["avg_abs_view_error_rate"] == pytest.approx(0.95)
    assert {item["views"] for item in calibration["top_misses"]} == {0, 100}
    for field in ("best_topics", "worst_topics", "angle_performance", "template_performance", "content_type_performance"):
        assert "missing" not in str(result[field])
    assert "missing" not in str(result["recommendations"])


def test_all_missing_metrics_produce_coverage_without_performance_claims(client, database):
    draft = draft_id(database, "not-observed")
    prediction(client, database, draft)
    log = publish(client, database, draft)
    result = report(client)
    assert result["performance_summary"]["totals"]["posts"] == 1
    assert result["performance_summary"]["data_coverage"] == {
        "published_posts": 1, "measured_posts": 0, "missing_metric_posts": 1,
        "missing_metric_log_ids": [log["id"]],
    }
    assert result["prediction_calibration"]["prediction_count"] == 0
    assert result["prediction_calibration"]["excluded_missing_metrics"] == 1
    assert result["performance_summary"]["rates"] is None
    for field in ("best_topics", "worst_topics", "angle_performance", "template_performance", "content_type_performance"):
        assert result[field]["items"] == []
    assert "not-observed" not in str(result["recommendations"])


def test_latest_metric_with_same_timestamp_uses_largest_id(client, database):
    draft = draft_id(database)
    prediction(client, database, draft)
    log = publish(client, database, draft)
    first = metric(client, log["id"], 100)
    last = metric(client, log["id"], 200)
    with database() as db:
        for metric_id in (first["id"], last["id"]):
            db.get(Metric, metric_id).collected_at = STAMP + timedelta(hours=1)
        db.commit()
    result = report(client)
    assert result["performance_summary"]["totals"]["views"] == 200
    assert result["prediction_calibration"]["top_misses"][0]["prediction_error"]["views_error_rate"] == pytest.approx(-0.8)


def test_zero_prediction_has_absolute_error_but_no_defined_relative_error(client, database):
    draft = draft_id(database, "zero-prediction")
    prediction(client, database, draft, predicted_views=0)
    log = publish(client, database, draft)
    metric(client, log["id"], 100)
    calibration = report(client)["prediction_calibration"]
    assert calibration["prediction_count"] == 1
    assert calibration["relative_error_sample_count"] == 0
    assert calibration["undefined_relative_error_count"] == 1
    assert calibration["avg_abs_view_error_rate"] is None
    assert calibration["avg_abs_views_error"] == 100
    assert calibration["underestimated_count"] == 1
    error = calibration["top_misses"][0]["prediction_error"]
    assert error["views_error"] == 100
    assert error["views_error_rate"] is None


def test_relative_error_average_excludes_zero_prediction_denominators(client, database):
    for label, predicted in (("zero-prediction", 0), ("positive-prediction", 1000)):
        draft = draft_id(database, label)
        prediction(client, database, draft, predicted_views=predicted)
        log = publish(client, database, draft)
        metric(client, log["id"], 100)
    calibration = report(client)["prediction_calibration"]
    assert calibration["prediction_count"] == 2
    assert calibration["relative_error_sample_count"] == 1
    assert calibration["undefined_relative_error_count"] == 1
    assert calibration["avg_abs_view_error_rate"] == pytest.approx(0.9)
    assert calibration["avg_abs_views_error"] == 500
    assert calibration["underestimated_count"] == calibration["overestimated_count"] == 1


def set_report_labels(database, draft, label=None):
    with database() as db:
        row = db.get(Draft, draft)
        db.get(Topic, row.topic_id).content_angle = f"角度-{label}" if label else None
        row.template_key = f"模板-{label}" if label else None
        row.content_type = f"类型-{label}" if label else None
        db.commit()


def test_small_unlabelled_sample_reports_observations_not_content_strategy(client, database):
    for label in ("measured", "missing"):
        draft = draft_id(database, label)
        set_report_labels(database, draft)
        prediction(client, database, draft)
        log = publish(client, database, draft)
        if label == "measured":
            metric(client, log["id"], 100, favorites=7, comments=2)
    result = report(client)
    assert result["performance_summary"]["data_coverage"]["measured_posts"] == 1
    text = "\n".join(result["recommendations"]["items"])
    assert "护栏" in text
    assert "尚未录入" in text
    assert "预测" in text and "误差" in text
    for strategy in ("继续做", "减少发布", "最佳模板", "封面节奏", "下周优先"):
        assert strategy not in text


def test_three_unlabelled_samples_request_labels_and_comparison_data(client, database):
    for index in range(3):
        draft = draft_id(database, f"unlabelled-{index}")
        set_report_labels(database, draft)
        log = publish(client, database, draft)
        metric(client, log["id"], (index + 1) * 100, favorites=10)
    result = report(client)
    assert result["performance_summary"]["data_coverage"]["measured_posts"] == 3
    text = "\n".join(result["recommendations"]["items"])
    assert "标签" in text and "补" in text
    assert "样本" in text
    for strategy in ("继续做", "减少发布", "最佳模板", "封面节奏", "下周优先", "偏弱"):
        assert strategy not in text


def test_single_label_group_cannot_be_both_recommended_and_reduced(client, database):
    for index in range(3):
        draft = draft_id(database, f"single-group-{index}")
        set_report_labels(database, draft, "唯一合成标签")
        log = publish(client, database, draft)
        metric(client, log["id"], (index + 1) * 100, favorites=10)
    result = report(client)
    for field in ("angle_performance", "template_performance", "content_type_performance"):
        assert len(result[field]["items"]) == 1
        assert result[field]["items"][0]["posts"] == 3
    text = "\n".join(result["recommendations"]["items"])
    assert "对照" in text
    assert "观察" in text or "观测" in text
    for strategy in ("偏弱", "减少发布", "继续做", "最佳模板", "封面节奏", "下周优先"):
        assert strategy not in text
