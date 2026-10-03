"""User-authored evidence notes and their explicit review/index lifecycle."""
from datetime import datetime, timezone
import hashlib
import unicodedata

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, JSON, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


def utc_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def fact_text(value: str) -> str:
    """Compare explicit labels/values only; this is not semantic fact checking."""
    # Unit case is meaningful (mA and MA must remain different values).
    return "".join(unicodedata.normalize("NFKC", value).split())


def structured_fact_errors(content: str, version_label: str, citations: list) -> list[str]:
    errors = []
    for index, citation in enumerate(citations or [], 1):
        if not isinstance(citation, dict):
            errors.append(f"引用 {index} 格式无效")
            continue
        fields = [citation.get(key, "") for key in ("product_model", "parameter", "value")]
        if not any(fields):
            continue
        if not all(isinstance(value, str) and value.strip() for value in fields):
            errors.append(f"引用 {index} 的品牌及产品型号、参数、参数值需一起填写")
            continue
        if not version_label.strip() or not str(citation.get("locator") or "").strip():
            errors.append(f"引用 {index} 的结构化事实需要资料版本及原文定位")
        excerpt = citation.get("excerpt", "")
        if not isinstance(excerpt, str) or not excerpt.strip():
            errors.append(f"引用 {index} 缺少结构化事实摘录")
        elif fact_text(excerpt) not in fact_text(content) or fact_text(fields[2]) not in fact_text(excerpt):
            errors.append(f"引用 {index} 的参数值必须出现在摘录中，摘录必须保留在笔记正文中")
    return errors


def structured_fact_conflicts(notes, now=None, candidate_id=None) -> dict[int, list[int]]:
    """Conflicting explicit values in one material scope fail closed.

    Pending material does not override reviewed material. At verification time
    candidate_id opts only that proposed note into the comparison. Product labels
    include the brand/model; no cross-brand synonym or unit inference is made.
    """
    now = utc_datetime(now or datetime.now(timezone.utc))
    groups = {}
    for note in notes:
        if note.id != candidate_id and (note.review_status != "verified" or note.verified_at is None):
            continue
        if note.expires_at and utc_datetime(note.expires_at) <= now:
            continue
        for citation in note.citations or []:
            if not isinstance(citation, dict):
                continue
            values = [citation.get(key, "") for key in ("product_model", "parameter", "value")]
            if not all(isinstance(value, str) and value.strip() for value in values):
                continue
            product, parameter, value = map(fact_text, values)
            key = (note.workspace_id, note.knowledge_base_id, product, parameter)
            groups.setdefault(key, []).append((note.id, value))
    conflicts = {}
    for entries in groups.values():
        if len({value for _, value in entries}) > 1:
            ids = {note_id for note_id, _ in entries}
            for note_id in ids:
                conflicts.setdefault(note_id, set()).update(ids)
    return {note_id: sorted(ids) for note_id, ids in conflicts.items()}


class EvidenceNote(Base):
    __tablename__ = "evidence_notes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    workspace_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    knowledge_base_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    provider: Mapped[str] = mapped_column(String(30), nullable=False)
    version_label: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    citations: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    rights_basis: Mapped[str] = mapped_column(String(30), nullable=False)
    rights_note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    own_note_confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    review_status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending")
    review_note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    review_history: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    source_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("sources.id", ondelete="SET NULL"), nullable=True, unique=True)
    document_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("knowledge_documents.id", ondelete="SET NULL"), nullable=True)
    index_state: Mapped[str] = mapped_column(String(30), nullable=False, default="not_indexed")
    index_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


def evidence_note_is_current(note: EvidenceNote, document_id: int | None = None,
                             now: datetime | None = None) -> bool:
    """SQL is authoritative; metadata snapshots never grant review approval."""
    now = utc_datetime(now or datetime.now(timezone.utc))
    return bool(
        note.review_status == "verified" and note.verified_at is not None
        and note.index_state == "indexed" and note.document_id is not None
        and (document_id is None or note.document_id == document_id)
        and note.rights_basis in {"own", "permission"}
        and (note.rights_basis != "permission" or note.rights_note.strip())
        and note.citations
        and hashlib.sha256(note.content.encode()).hexdigest() == note.content_hash
        and not structured_fact_errors(note.content, note.version_label, note.citations)
        and (note.provider != "aihot" or note.own_note_confirmed)
        and (note.expires_at is None or utc_datetime(note.expires_at) > now)
    )
