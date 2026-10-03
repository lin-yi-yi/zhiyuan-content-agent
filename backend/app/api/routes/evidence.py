"""Local, explicitly reviewed evidence notes; this is not authenticated SaaS."""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services import evidence_notes as service
from app.services.evidence_notes import EvidenceError, NoteCreate, NoteReview, NoteScope


router = APIRouter(prefix="/evidence/notes", tags=["evidence"])


def _call(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except EvidenceError as exc:
        raise HTTPException(exc.status_code, str(exc)) from None


@router.post("", status_code=201)
def create(body: NoteCreate, db: Session = Depends(get_db)):
    return _call(service.create_note, body, db)


@router.get("")
def listing(workspace_id: int | None = Query(None, ge=1), knowledge_base_id: int | None = Query(None, ge=1),
            status: Literal["pending", "verified", "rejected", "revoked"] | None = None,
            limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0), db: Session = Depends(get_db)):
    return _call(service.list_notes, db, NoteScope(workspace_id=workspace_id, knowledge_base_id=knowledge_base_id), status, limit, offset)


@router.get("/{note_id}")
def detail(note_id: int, workspace_id: int | None = Query(None, ge=1), knowledge_base_id: int | None = Query(None, ge=1),
           db: Session = Depends(get_db)):
    return _call(service.get_note, note_id, NoteScope(workspace_id=workspace_id, knowledge_base_id=knowledge_base_id), db)


@router.post("/{note_id}/review")
def review(note_id: int, body: NoteReview, db: Session = Depends(get_db)):
    return _call(service.review_note, note_id, body, db)


@router.post("/{note_id}/index")
def index(note_id: int, body: NoteScope = NoteScope(), db: Session = Depends(get_db)):
    return _call(service.index_note, note_id, body, db)
