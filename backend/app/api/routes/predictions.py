"""发布前预测 API。"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.content_prediction import ContentPrediction
from app.models.draft import Draft
from app.models.publish_log import PublishLog
from app.schemas.content_prediction import (
    ContentPredictionCreate,
    ContentPredictionOut,
    ContentPredictionUpdate,
    PredictionAttachLog,
)

router = APIRouter(prefix="/predictions", tags=["predictions"])

RATE_FIELDS = {
    "predicted_save_rate": "predicted_favorites",
    "predicted_like_rate": "predicted_likes",
    "predicted_comment_rate": "predicted_comments",
    "predicted_follow_conversion_rate": "predicted_new_followers",
}


@router.get("", response_model=list[ContentPredictionOut])
def list_predictions(
    draft_id: int | None = Query(None),
    publish_log_id: int | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    q = db.query(ContentPrediction)
    if draft_id:
        q = q.filter(ContentPrediction.draft_id == draft_id)
    if publish_log_id:
        q = q.filter(ContentPrediction.publish_log_id == publish_log_id)
    items = q.order_by(ContentPrediction.created_at.desc(), ContentPrediction.id.desc()).limit(limit).all()
    return [ContentPredictionOut.model_validate(item) for item in items]


@router.post("", response_model=ContentPredictionOut, status_code=201)
def create_prediction(body: ContentPredictionCreate, db: Session = Depends(get_db)):
    _require_draft(db, body.draft_id)
    if body.publish_log_id:
        _require_matching_log(db, body.publish_log_id, body.draft_id, body.platform)
    data = body.model_dump()
    prediction = ContentPrediction(**data)
    _fill_rates(prediction, _explicit_rate_fields(data))
    db.add(prediction)
    db.commit()
    db.refresh(prediction)
    return ContentPredictionOut.model_validate(prediction)


@router.get("/{prediction_id}", response_model=ContentPredictionOut)
def get_prediction(prediction_id: int, db: Session = Depends(get_db)):
    prediction = _require_prediction(db, prediction_id)
    return ContentPredictionOut.model_validate(prediction)


@router.put("/{prediction_id}", response_model=ContentPredictionOut)
def update_prediction(prediction_id: int, body: ContentPredictionUpdate, db: Session = Depends(get_db)):
    prediction = _require_prediction(db, prediction_id)
    data = body.model_dump(exclude_unset=True)
    if data.get("publish_log_id"):
        platform = data.get("platform") or prediction.platform
        _require_matching_log(db, data["publish_log_id"], prediction.draft_id, platform)
    elif data.get("platform") and prediction.publish_log_id:
        _require_matching_log(db, prediction.publish_log_id, prediction.draft_id, data["platform"])
    for key, value in data.items():
        setattr(prediction, key, value)
    _fill_rates(prediction, _explicit_rate_fields(data))
    db.commit()
    db.refresh(prediction)
    return ContentPredictionOut.model_validate(prediction)


@router.post("/{prediction_id}/attach-log", response_model=ContentPredictionOut)
def attach_prediction_to_log(prediction_id: int, body: PredictionAttachLog, db: Session = Depends(get_db)):
    prediction = _require_prediction(db, prediction_id)
    _require_matching_log(db, body.publish_log_id, prediction.draft_id, prediction.platform)
    prediction.publish_log_id = body.publish_log_id
    prediction.status = "locked" if prediction.status == "draft" else prediction.status
    db.commit()
    db.refresh(prediction)
    return ContentPredictionOut.model_validate(prediction)


def _require_draft(db: Session, draft_id: int) -> Draft:
    draft = db.query(Draft).filter(Draft.id == draft_id).first()
    if not draft:
        raise HTTPException(404, "发布包不存在")
    return draft


def _require_matching_log(db: Session, publish_log_id: int, draft_id: int, platform: str) -> PublishLog:
    log = db.query(PublishLog).filter(PublishLog.id == publish_log_id).first()
    if not log:
        raise HTTPException(404, "发布记录不存在")
    if log.draft_id != draft_id:
        raise HTTPException(422, "发布记录与预测发布包不一致")
    if log.platform != platform:
        raise HTTPException(422, "发布记录与预测平台不一致")
    return log


def _require_prediction(db: Session, prediction_id: int) -> ContentPrediction:
    prediction = db.query(ContentPrediction).filter(ContentPrediction.id == prediction_id).first()
    if not prediction:
        raise HTTPException(404, "预测记录不存在")
    return prediction


def _explicit_rate_fields(data: dict) -> set[str]:
    return {field for field in RATE_FIELDS if data.get(field) is not None}


def _fill_rates(prediction: ContentPrediction, preserve_rates: set[str] | None = None) -> None:
    preserve_rates = preserve_rates or set()
    views = int(prediction.predicted_views or 0)
    if views <= 0:
        for field in RATE_FIELDS:
            if field not in preserve_rates:
                setattr(prediction, field, None)
        return
    for rate_field, count_field in RATE_FIELDS.items():
        if rate_field in preserve_rates:
            continue
        count = int(getattr(prediction, count_field) or 0)
        setattr(prediction, rate_field, round(count / views, 4))
