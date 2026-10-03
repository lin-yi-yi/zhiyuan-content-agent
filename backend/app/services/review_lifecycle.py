"""Bind local human approval to a specific editable draft/card snapshot."""
from copy import deepcopy
from datetime import UTC, datetime
import hashlib
import json
import re

from sqlalchemy.orm import Session

from app.agent_core.evidence_policy import document_evidence_states
from app.models.agent_run import AgentRun, AgentStep
from app.models.card import Card
from app.models.draft import Draft
from app.models.knowledge_base import KnowledgeChunk, KnowledgeDocument
from app.models.review_checklist import ReviewChecklist
from app.services.workflow_support import CitationError, WorkflowConflict, validate_citations


def _now():
    return datetime.now(UTC).replace(tzinfo=None)


def draft_content_snapshot(draft: Draft, db: Session, *, refresh_existing: bool = False) -> dict:
    """Only editable content belongs in history; never model/request credentials."""
    if refresh_existing:
        # Edit routes may have loaded rows before waiting for the run write lock.
        # Capture the committed content under that lock, not a stale identity map.
        db.refresh(draft)
    draft_fields = ["title_options", "cover_text_options", "body_text", "hashtags", "comment_guide",
                    "fact_checks", "risk_tips", "aigc_notice", "selected_title", "selected_cover_text",
                    "body_variant_key", "body_variants", "content_type", "template_key", "theme_key",
                    "variant_name", "max_card_count", "generated_reason"]
    card_fields = ["id", "page_index", "card_type", "title", "subtitle", "body", "highlight", "footer",
                   "layout_key", "theme_key", "style_json"]
    card_query = db.query(Card).filter(Card.draft_id == draft.id).order_by(Card.page_index, Card.id)
    cards = (card_query.populate_existing() if refresh_existing else card_query).all()
    return deepcopy({"draft": {name: getattr(draft, name) for name in draft_fields},
                     "cards": [{name: getattr(card, name) for name in card_fields} for card in cards]})


def _snapshot_hash(snapshot: dict) -> str:
    return hashlib.sha256(json.dumps(snapshot, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def draft_snapshot_hash(draft: Draft, db: Session) -> str:
    return _snapshot_hash(draft_content_snapshot(draft, db))


def draft_publication_texts(draft: Draft, db: Session) -> list[str]:
    """Review citations in titles and other copy as well as the body/cards."""
    def strings(value):
        if isinstance(value, str):
            return [value]
        if isinstance(value, (list, dict)):
            children = value.values() if isinstance(value, dict) else value
            return [text for child in children for text in strings(child)]
        return []

    fields = [draft.selected_title, draft.selected_cover_text, draft.comment_guide, draft.aigc_notice,
              draft.title_options, draft.cover_text_options, draft.hashtags]
    for card in db.query(Card).filter(Card.draft_id == draft.id).all():
        fields.extend(getattr(card, name) for name in ("title", "subtitle", "body", "highlight", "footer"))
    # Keep the body first even when empty: the review gate always requires its
    # citation independently. Historical JSON may hold dicts instead of lists.
    return [str(draft.body_text or ""), *strings(fields)]


def validate_review_evidence(run: AgentRun, draft: Draft, db: Session) -> None:
    data = run.result_json or {}
    profile = ((data.get("brief") or {}).get("profile") or {})
    prohibited = [item.strip() for item in str(profile.get("prohibited_claims") or "").splitlines() if item.strip()]
    publication_texts = draft_publication_texts(draft, db)
    if prohibited:
        text = "\n".join(publication_texts).casefold()
        hits = [item for item in prohibited if item.casefold() in text]
        if hits:
            raise WorkflowConflict("内容包含品牌禁用表述，请修改正文、标题或卡片后重新审核：" + "、".join(hits[:8]))
    if not (data.get("_request") or {}).get("use_rag"):
        return
    citations = data.get("citations") or []
    try:
        used = validate_citations(draft.body_text or "", citations)
        for text in publication_texts[1:]:
            if re.search(r"\[chunk:", text):
                used.extend(validate_citations(text, citations))
    except CitationError as exc:
        raise WorkflowConflict(str(exc)) from exc
    context = data.get("rag_context") or {}
    for item in used:
        chunk = db.get(KnowledgeChunk, item["chunk_id"])
        if (chunk is None or chunk.document_id != item.get("document_id")
                or chunk.workspace_id != context.get("workspace_id")
                or chunk.knowledge_base_id != context.get("knowledge_base_id")
                or chunk.content[:1200] != item.get("excerpt")):
            raise WorkflowConflict("引用资料已更新或删除，当前引用已失效。请重新生成任务取得最新证据后审核。")
    # Old chunks may intentionally remain after review is revoked. Their presence
    # and unchanged text do not grant approval to the document they came from.
    document_ids = {item["document_id"] for item in used}
    documents = db.query(KnowledgeDocument).filter(
        KnowledgeDocument.id.in_(document_ids),
    ).populate_existing().all()
    states = document_evidence_states(db, documents)
    if any(not states.get(document_id, {}).get("eligible") for document_id in document_ids):
        raise WorkflowConflict("引用资料已过期、撤销核验、存在冲突或不可用，请重新生成任务取得有效证据后审核。")


def archive_review(data: dict, reason: str) -> dict:
    result = dict(data)
    previous = result.pop("review", None)
    if previous:
        history = list(result.get("review_history") or [])
        history.append({**previous, "archived_at": _now().isoformat(), "reason": reason})
        result["review_history"] = history
    return result


def _archive_content(data: dict, snapshot: dict, reason: str) -> dict:
    from app.saas.context import current_tenant

    result = dict(data)
    history = list(result.get("content_history") or [])
    content_hash = _snapshot_hash(snapshot)
    # Some callers may invalidate repeatedly in one transaction. Keep one copy
    # of an unchanged pre-edit snapshot, while still retaining real A -> B -> A edits.
    if not history or history[-1].get("content_hash") != content_hash:
        tenant = current_tenant.get()
        history.append({"revision": int(result.get("content_revision") or 1),
                        "content_hash": content_hash, "snapshot": snapshot,
                        "at": _now().isoformat(), "reason": reason,
                        "actor": tenant.user_id if tenant else "local_user"})
    result["content_history"] = history
    return result


def _locked_runs(draft_id: int, db: Session) -> list[AgentRun]:
    # The write lock serializes editing and the run's review compare-and-set.
    db.query(AgentRun).filter(AgentRun.draft_id == draft_id).update(
        {"updated_at": _now()}, synchronize_session=False,
    )
    runs = db.query(AgentRun).filter(AgentRun.draft_id == draft_id).populate_existing().all()
    if any(run.status in {"pending", "running"} for run in runs):
        raise WorkflowConflict("任务仍在执行，请等待完成或取消后再编辑内容。")
    return runs


def has_workflow(draft_id: int, db: Session) -> bool:
    return db.query(AgentRun.id).filter(AgentRun.draft_id == draft_id).first() is not None


def invalidate_review_for_edit(draft: Draft, db: Session, reason: str) -> None:
    """Caller commits content changes and invalidation in the same transaction.

    Approved content returns to review immediately. Rejected content stays rejected
    until the user explicitly resubmits. All old decisions remain in review_history.
    """
    runs = _locked_runs(draft.id, db)
    snapshot = draft_content_snapshot(draft, db, refresh_existing=True) if runs else None
    changed_statuses = []
    for run in runs:
        data = archive_review(_archive_content(run.result_json or {}, snapshot, reason), reason)
        data["content_revision"] = int(data.get("content_revision") or 1) + 1
        data["review_invalidated"] = {"at": _now().isoformat(), "reason": reason,
                                      "requires_new_decision": True}
        data["evaluation_stale"] = True
        for key in ("evaluation", "reevaluation", "agent_decision"):
            data.pop(key, None)
        run.evaluation_score = None
        if run.status in {"approved", "awaiting_review", "completed"}:
            run.status = "awaiting_review"
            run.current_step = "human_review"
        if run.status in {"awaiting_review", "rejected"}:
            changed_statuses.append(run.status)
            step = db.query(AgentStep).filter(AgentStep.run_id == run.id, AgentStep.key == "human_review").first()
            if step:
                step.status = "awaiting_review" if run.status == "awaiting_review" else "pending"
                step.started_at = _now() if run.status == "awaiting_review" else None
                step.completed_at = None
                step.output_json = None
        run.result_json = data
    if changed_statuses:
        draft.status = "awaiting_review" if "awaiting_review" in changed_statuses else "rejected"
    elif draft.status == "approved":
        draft.status = "draft"
    # Existing checklist ticks describe the previous content, not the edited one.
    db.query(ReviewChecklist).filter(ReviewChecklist.draft_id == draft.id).update(
        {"checked": False}, synchronize_session=False,
    )


def invalidate_review_for_delete(draft: Draft, db: Session) -> None:
    runs = _locked_runs(draft.id, db)
    snapshot = draft_content_snapshot(draft, db, refresh_existing=True) if runs else None
    for run in runs:
        data = archive_review(_archive_content(run.result_json or {}, snapshot, "关联草稿已删除"), "关联草稿已删除")
        data["review_invalidated"] = {"at": _now().isoformat(), "reason": "关联草稿已删除"}
        run.result_json = data
        run.status, run.current_step, run.draft_id = "cancelled", "draft_deleted", None
        db.query(AgentStep).filter(AgentStep.run_id == run.id, AgentStep.key == "human_review").update(
            {"status": "cancelled", "completed_at": _now()}, synchronize_session=False,
        )
