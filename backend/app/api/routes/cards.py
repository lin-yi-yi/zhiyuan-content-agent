"""卡片 API"""
from copy import deepcopy

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.card import Card
from app.models.draft import Draft
from app.schemas.card import CardCreate, CardMove, CardOut, CardUpdate
from app.services.review_lifecycle import invalidate_review_for_edit
from app.services.workflow_support import WorkflowConflict


class BatchStyleRequest(BaseModel):
    layout_key: str = ""
    theme_key: str = ""


def _cards_for_draft(db: Session, draft_id: int) -> list[Card]:
    return db.query(Card).filter(Card.draft_id == draft_id).order_by(Card.page_index, Card.id).all()


def _invalidate_cards_review(db: Session, draft_id: int) -> None:
    draft = db.get(Draft, draft_id)
    if draft is None:
        raise HTTPException(404, "发布包不存在")
    try:
        invalidate_review_for_edit(draft, db, "卡片内容或排版已修改，旧审核结论失效")
    except WorkflowConflict as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from exc


def _renumber_cards(db: Session, draft_id: int) -> list[Card]:
    cards = _cards_for_draft(db, draft_id)
    for index, card in enumerate(cards, start=1):
        card.page_index = index
    return cards


def _clone_style_json(value: dict | None) -> dict | None:
    if value is None:
        return None
    return deepcopy(value)


def _split_body(body: str | None) -> tuple[str, str]:
    text = (body or "").strip()
    if not text:
        return "", ""

    paragraphs = [part.strip() for part in text.replace("\r", "").split("\n") if part.strip()]
    if len(paragraphs) >= 2:
        midpoint = max(1, len(paragraphs) // 2)
        return "\n".join(paragraphs[:midpoint]), "\n".join(paragraphs[midpoint:])

    sentence_breaks = []
    for index, char in enumerate(text):
        if char in "。！？!?；;":
            sentence_breaks.append(index + 1)
    if sentence_breaks:
        target = len(text) // 2
        split_at = min(sentence_breaks, key=lambda item: abs(item - target))
        return text[:split_at].strip(), text[split_at:].strip()

    midpoint = max(1, len(text) // 2)
    return text[:midpoint].strip(), text[midpoint:].strip()


router = APIRouter(prefix="/cards", tags=["cards"])


@router.get("/draft/{draft_id}", response_model=list[CardOut])
def list_cards_by_draft(draft_id: int, db: Session = Depends(get_db)):
    cards = db.query(Card).filter(Card.draft_id == draft_id).order_by(Card.page_index).all()
    return [CardOut.model_validate(c) for c in cards]


@router.post("/draft/{draft_id}", response_model=list[CardOut])
def create_card(draft_id: int, body: CardCreate, db: Session = Depends(get_db)):
    draft = db.query(Draft).filter(Draft.id == draft_id).first()
    if not draft:
        raise HTTPException(404, "发布包不存在")

    _invalidate_cards_review(db, draft_id)

    cards = _cards_for_draft(db, draft_id)
    target_index = body.page_index if body.page_index is not None else len(cards) + 1
    target_index = max(1, min(target_index, len(cards) + 1))
    for card in cards:
        if card.page_index >= target_index:
            card.page_index += 1

    new_card = Card(
        draft_id=draft_id,
        page_index=target_index,
        card_type=body.card_type,
        title=body.title,
        subtitle=body.subtitle,
        body=body.body,
        highlight=body.highlight,
        footer=body.footer,
        layout_key=body.layout_key,
        theme_key=body.theme_key,
        style_json=_clone_style_json(body.style_json),
    )
    db.add(new_card)
    db.flush()
    _renumber_cards(db, draft_id)
    db.commit()
    return [CardOut.model_validate(c) for c in _cards_for_draft(db, draft_id)]


@router.put("/{card_id}", response_model=CardOut)
def update_card(card_id: int, body: CardUpdate, db: Session = Depends(get_db)):
    c = db.query(Card).filter(Card.id == card_id).first()
    if not c:
        raise HTTPException(404, "卡片不存在")
    values = body.model_dump(exclude_unset=True)
    if any(getattr(c, key) != value for key, value in values.items()):
        _invalidate_cards_review(db, c.draft_id)
    for k, v in values.items():
        setattr(c, k, v)
    db.commit()
    db.refresh(c)
    return CardOut.model_validate(c)


@router.post("/{card_id}/duplicate", response_model=list[CardOut])
def duplicate_card(card_id: int, db: Session = Depends(get_db)):
    card = db.query(Card).filter(Card.id == card_id).first()
    if not card:
        raise HTTPException(404, "卡片不存在")
    _invalidate_cards_review(db, card.draft_id)
    cards = _cards_for_draft(db, card.draft_id)
    for item in cards:
        if item.page_index > card.page_index:
            item.page_index += 1
    copy = Card(
        draft_id=card.draft_id,
        page_index=card.page_index + 1,
        card_type=card.card_type,
        title=f"{card.title}（复制）",
        subtitle=card.subtitle,
        body=card.body,
        highlight=card.highlight,
        footer=card.footer,
        layout_key=card.layout_key,
        theme_key=card.theme_key,
        style_json=_clone_style_json(card.style_json),
    )
    db.add(copy)
    db.flush()
    _renumber_cards(db, card.draft_id)
    db.commit()
    return [CardOut.model_validate(c) for c in _cards_for_draft(db, card.draft_id)]


@router.post("/{card_id}/split", response_model=list[CardOut])
def split_card(card_id: int, db: Session = Depends(get_db)):
    card = db.query(Card).filter(Card.id == card_id).first()
    if not card:
        raise HTTPException(404, "卡片不存在")
    _invalidate_cards_review(db, card.draft_id)

    first_body, second_body = _split_body(card.body)
    if not second_body:
        second_body = "继续补充这一页后半段内容。"
    card.body = first_body or card.body

    cards = _cards_for_draft(db, card.draft_id)
    for item in cards:
        if item.page_index > card.page_index:
            item.page_index += 1
    next_card = Card(
        draft_id=card.draft_id,
        page_index=card.page_index + 1,
        card_type=card.card_type,
        title=f"{card.title}（续）",
        subtitle=card.subtitle,
        body=second_body,
        highlight=card.highlight,
        footer=card.footer,
        layout_key=card.layout_key,
        theme_key=card.theme_key,
        style_json=_clone_style_json(card.style_json),
    )
    db.add(next_card)
    db.flush()
    _renumber_cards(db, card.draft_id)
    db.commit()
    return [CardOut.model_validate(c) for c in _cards_for_draft(db, card.draft_id)]


@router.put("/{card_id}/move", response_model=list[CardOut])
def move_card(card_id: int, body: CardMove, db: Session = Depends(get_db)):
    card = db.query(Card).filter(Card.id == card_id).first()
    if not card:
        raise HTTPException(404, "卡片不存在")
    cards = _cards_for_draft(db, card.draft_id)
    current_index = next((index for index, item in enumerate(cards) if item.id == card.id), None)
    if current_index is None:
        raise HTTPException(404, "卡片不存在")
    target_index = current_index - 1 if body.direction == "up" else current_index + 1
    if target_index < 0 or target_index >= len(cards):
        return [CardOut.model_validate(c) for c in cards]
    _invalidate_cards_review(db, card.draft_id)
    moved = cards.pop(current_index)
    cards.insert(target_index, moved)
    for index, item in enumerate(cards, start=1):
        item.page_index = index
    db.commit()
    return [CardOut.model_validate(c) for c in _cards_for_draft(db, card.draft_id)]


@router.delete("/{card_id}", response_model=list[CardOut])
def delete_card(card_id: int, db: Session = Depends(get_db)):
    card = db.query(Card).filter(Card.id == card_id).first()
    if not card:
        raise HTTPException(404, "卡片不存在")
    draft_id = card.draft_id
    _invalidate_cards_review(db, draft_id)
    db.delete(card)
    db.flush()
    _renumber_cards(db, draft_id)
    db.commit()
    return [CardOut.model_validate(c) for c in _cards_for_draft(db, draft_id)]


@router.put("/draft/{draft_id}/batch-style", response_model=list[CardOut])
def batch_update_style(draft_id: int, body: BatchStyleRequest, db: Session = Depends(get_db)):
    """批量更新整套卡片的 layout 和/或 theme"""
    cards = db.query(Card).filter(Card.draft_id == draft_id).order_by(Card.page_index).all()
    if not cards:
        raise HTTPException(404, "该草稿没有卡片")
    if any((body.layout_key and c.layout_key != body.layout_key) or
           (body.theme_key and c.theme_key != body.theme_key) for c in cards):
        _invalidate_cards_review(db, draft_id)
    for c in cards:
        if body.layout_key:
            c.layout_key = body.layout_key
        if body.theme_key:
            c.theme_key = body.theme_key
    db.commit()
    for c in cards:
        db.refresh(c)
    return [CardOut.model_validate(c) for c in cards]
