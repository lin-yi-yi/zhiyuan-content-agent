"""Live provenance checks. Human review is a recorded assertion, not fact checking."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, or_

from app.models.source import Source


def utc(value):
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def document_evidence_states(db, documents, now=None):
    # Imported here to keep model registration independent of retrieval services.
    from app.models.evidence_note import EvidenceNote, evidence_note_is_current, structured_fact_conflicts

    documents = list(documents)
    if not documents:
        return {}
    now = now or datetime.now(timezone.utc)
    ids = [doc.id for doc in documents]
    source_ids = [doc.source_id for doc in documents if doc.source_id]
    sources = {item.id: item for item in db.query(Source).filter(Source.id.in_(source_ids))}
    notes = db.query(EvidenceNote).filter(or_(EvidenceNote.document_id.in_(ids), EvidenceNote.source_id.in_(source_ids))).all()
    # Also check reviewed facts outside this candidate subset. A narrow top-k or
    # single-document approval check must not hide a contradictory active note.
    scopes = {(doc.workspace_id, doc.knowledge_base_id) for doc in documents}
    peers = db.query(EvidenceNote).filter(or_(*(and_(EvidenceNote.workspace_id == workspace_id,
        EvidenceNote.knowledge_base_id == kb_id) for workspace_id, kb_id in scopes))).all()
    conflicts = structured_fact_conflicts(peers, now=now)
    by_source = {note.source_id: note for note in notes if note.source_id}
    states = {}
    for doc in documents:
        source = sources.get(doc.source_id)
        note = by_source.get(doc.source_id)
        metadata = doc.metadata_json or {}
        governed = bool(note or metadata.get("evidence") or metadata.get("source_type") == "evidence_note"
                        or (source and source.source_type == "evidence_note"))
        if not governed:
            states[doc.id] = {"eligible": doc.status == "indexed", "reason": "untracked",
                              "evidence": None, "freshness": {"status": "untracked", "expires_at": None}}
            continue
        expires = utc(note.expires_at) if note else None
        expired = bool(expires and expires <= now)
        integrity = bool(note and note.workspace_id == doc.workspace_id and note.knowledge_base_id == doc.knowledge_base_id
                         and note.content_hash == doc.content_hash and metadata.get("evidence", {}).get("note_id") == note.id)
        conflict_ids = conflicts.get(note.id, []) if note else []
        eligible = bool(integrity and not conflict_ids and doc.status == "indexed" and evidence_note_is_current(note, document_id=doc.id, now=now))
        reason = "expired" if expired else ("conflict" if conflict_ids else ("current" if eligible else "unverified"))
        evidence = None
        if note:
            evidence = {"note_id": note.id, "status": note.review_status,
                        "verified_at": utc(note.verified_at).isoformat() if note.verified_at else None,
                        "expires_at": expires.isoformat() if expires else None,
                        "version_label": note.version_label, "citations": note.citations,
                        "source_url": note.source_url, "provider": note.provider,
                        "rights_basis": note.rights_basis, "content_hash": note.content_hash,
                        "fact_conflict_note_ids": conflict_ids,
                        "published_at": utc(note.published_at).isoformat() if note.published_at else None}
        states[doc.id] = {"eligible": eligible, "reason": reason, "evidence": evidence,
                          "freshness": {"status": "expired" if expired else (
                              "expires_soon" if expires and expires <= now + timedelta(days=7) else "current"),
                              "expires_at": expires.isoformat() if expires else None}}
    return states


def evidence_health(states):
    values = list(states.values())
    expired = sum(item["reason"] == "expired" for item in values)
    unverified = sum(item["reason"] == "unverified" for item in values)
    conflicting = sum(item["reason"] == "conflict" for item in values)
    untracked = sum(item["reason"] == "untracked" and item["eligible"] for item in values)
    soon = sum(item["freshness"]["status"] == "expires_soon" and item["eligible"] for item in values)
    warnings = []
    if expired:
        warnings.append(f"当前知识库有 {expired} 份核验资料已过期，已排除；请新建修订笔记并重新核验。")
    if unverified:
        warnings.append(f"当前知识库有 {unverified} 份资料核验已撤销、尚未完成入库或关联不完整，已排除。")
    if conflicting:
        warnings.append(f"当前知识库有 {conflicting} 份资料存在结构化事实冲突，已排除；请核对产品、参数及适用条件，撤销旧版后重新核验。")
    if soon:
        warnings.append(f"当前知识库有 {soon} 份核验资料将在 7 天内到期。")
    if untracked:
        warnings.append(f"当前知识库有 {untracked} 份普通上传资料未登记核验和时效；引用时仍需自行核对。")
    return {"excluded_count": sum(not item["eligible"] for item in values), "expired_count": expired,
            "unverified_count": unverified, "conflict_count": conflicting, "untracked_count": untracked, "expires_soon_count": soon,
            "warnings": warnings, "scope": "current_knowledge_base"}
