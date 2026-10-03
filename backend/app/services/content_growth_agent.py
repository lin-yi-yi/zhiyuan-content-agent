"""内容增长 Agent 总调度器。"""
import json
from app.saas.context import current_tenant, is_saas_mode
from datetime import UTC, datetime
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from sqlalchemy.orm import Session

from app.agent_core.boundaries import get_knowledge_base_or_default, workspace_context
from app.agent_core.embeddings import RetrievalError
from app.agent_core.rag_service import assess_required_facts, missing_fact_labels, search_knowledge, select_evidence
from app.db.session import SessionLocal
from app.models.agent_run import AgentRun, AgentStep
from app.models.card import Card
from app.models.draft import Draft
from app.models.source import Source
from app.models.topic import Topic
from app.llm.openai_compatible import ModelCallError
from app.llm.tracing import model_trace_context
from app.schemas.agent_run import AgentRunCreate, AgentRunResult, AgentReviewCreate
from app.schemas.card import CardOut
from app.schemas.draft import DraftOut
from app.schemas.topic import TopicOut
from app.services.card_generator import generate_cards
from app.services.evidence_cards import build_evidence_cards
from app.services.compliance_checker import check_compliance
from app.services.custom_topic_creator import CustomTopicIdea, ResearchReference, generate_custom_topic_ideas
from app.services.draft_generator import generate_draft
from app.services.package_evaluator import evaluate_draft
from app.services.topic_scorer import score_topic
from app.services.workflow_support import (
    AtomicStepSession, CitationError, EvidenceError, WorkflowCancelled, WorkflowConflict,
    complete_sentence_excerpt, generate_evidence_draft, validate_citations,
)
from app.services.review_lifecycle import archive_review, draft_snapshot_hash, validate_review_evidence


REVISION_THRESHOLD = 75


def _utcnow() -> datetime:
    """Database timestamps use naive UTC for compatibility with existing tables."""
    return datetime.now(UTC).replace(tzinfo=None)


def _safe_failure(exc: Exception) -> tuple[str, str, str]:
    """Persist controlled domain messages, never arbitrary SQL/SDK exception text."""
    safe_codes = {
        EvidenceError: "INSUFFICIENT_EVIDENCE",
        CitationError: "INVALID_CITATIONS",
        WorkflowConflict: "WORKFLOW_CONFLICT",
        RetrievalError: "RETRIEVAL_FAILED",
        ModelCallError: "MODEL_CALL_FAILED",
    }
    error_type = type(exc).__name__[:80]
    if type(exc) in safe_codes:
        return safe_codes[type(exc)], str(exc)[:1000], error_type
    return "STEP_FAILED", f"步骤执行失败，请检查配置、依赖和数据后重试。错误类型：{error_type}", error_type

STEP_PLAN = [
    ("retrieve_context", "知识库检索上下文"),
    ("topic_ideas", "生成 5 个选题建议"),
    ("create_topic", "推荐最佳选题并入库"),
    ("score_topic", "选题评分"),
    ("generate_draft", "生成发布包"),
    ("generate_cards", "生成图文卡片"),
    ("compliance_check", "合规和事实核验提示"),
    ("evaluate_package", "发布包质量评分"),
    ("revise_package", "自动轻量改稿"),
    ("reevaluate_package", "改稿后再次评分"),
    ("agent_decision", "生成执行决策和下一步建议"),
    ("human_review", "人工审核来源和草稿"),
]


def create_agent_run(req: AgentRunCreate, db: Session) -> AgentRunResult:
    """创建 Agent Run 和步骤，后台任务会继续执行。"""
    from app.services.business_brief import resolve_business_brief
    brief = resolve_business_brief(req, db)
    if brief:
        profile = brief.get("profile") or {}
        req = req.model_copy(update={
            "knowledge_base_id": brief["knowledge_base_id"],
            "workspace_id": brief["workspace_id"],
            "target_audience": req.target_audience or profile.get("audience", ""),
        })
    provider = req.provider or "local"
    run = AgentRun(
        goal=req.goal.strip(),
        mode=req.mode if req.mode in {"research", "inspiration"} else "inspiration",
        provider=provider,
        model_name=req.model or None,
        status="pending",
        current_step="queued",
        result_json={"_request": req.model_dump(), "brief": brief, "workflow": {
            "engine": "langgraph", "version": "content-v1", "attempt": 1,
            "persistence": "atomic_sql_steps", "scope": "organization" if is_saas_mode() else "local_single_user",
            "generation_mode": "local_rules" if provider == "local" else "llm",
        }},
    )
    db.add(run)
    db.flush()
    _create_steps(run.id, db)
    return get_agent_run(run.id, db)  # type: ignore[return-value]


async def execute_agent_run(run_id: int) -> None:
    """Only the caller that atomically claims a pending run executes its graph."""
    db = SessionLocal()
    try:
        claimed = db.query(AgentRun).filter(AgentRun.id == run_id, AgentRun.status == "pending").update(
            {"status": "running", "error_message": None}, synchronize_session=False,
        )
        db.commit()
        if not claimed:
            return
        run = db.query(AgentRun).filter(AgentRun.id == run_id).first()
        if not run:
            return
        try:
            req = _request_from_run(run)
        except (ValueError, TypeError):
            req = None
        if not req:
            run.status = "failed"
            run.error_message = "缺少 Agent 请求参数，无法继续执行"
            db.commit()
            return
        await _execute_steps(run, req, db)
    finally:
        db.close()


def prepare_retry_agent_run(run_id: int, db: Session) -> AgentRunResult | None:
    """Explicit retry; compare-and-set rejects concurrent or repeated retries."""
    run = db.query(AgentRun).filter(AgentRun.id == run_id).first()
    if not run:
        return None
    claimed = db.query(AgentRun).filter(AgentRun.id == run_id, AgentRun.status == "failed").update(
        {"status": "pending"}, synchronize_session=False,
    )
    if not claimed:
        db.rollback()
        raise WorkflowConflict("只有失败任务可以重试；运行中、已取消或已审核任务不能重复执行。")
    failed_step = (
        db.query(AgentStep)
        .filter(AgentStep.run_id == run_id, AgentStep.status == "failed")
        .order_by(AgentStep.step_index)
        .first()
    )
    if not failed_step:
        failed_step = db.query(AgentStep).filter(
            AgentStep.run_id == run_id, AgentStep.status.notin_(["completed", "skipped"]),
        ).order_by(AgentStep.step_index).first()
    if not failed_step:
        db.rollback()
        raise WorkflowConflict("该任务没有可恢复的失败步骤，请新建任务。")

    previous_attempt = {
        "attempt": int((run.result_json or {}).get("workflow", {}).get("attempt") or 1),
        "failure": (run.result_json or {}).get("failure") or {"message": run.error_message},
        "failed_step": failed_step.key,
        "duration_ms": failed_step.duration_ms,
    }

    downstream = (
        db.query(AgentStep)
        .filter(AgentStep.run_id == run_id, AgentStep.step_index >= failed_step.step_index)
        .order_by(AgentStep.step_index)
        .all()
    )
    for step in downstream:
        step.status = "pending"
        step.error_message = None
        step.started_at = None
        step.completed_at = None
        step.input_json = None
        step.output_json = None
    run.status = "pending"
    run.current_step = "queued"
    run.error_message = None
    data = _prune_results_for_retry(run.result_json or {}, failed_step.key)
    workflow = dict(data.get("workflow") or {})
    workflow["history"] = [*(workflow.get("history") or []), previous_attempt][-20:]
    workflow["attempt"] = int(workflow.get("attempt") or 1) + 1
    workflow["last_retry_at"] = _utcnow().isoformat()
    data["workflow"] = workflow
    run.result_json = data
    db.commit()
    return get_agent_run(run_id, db)


def cancel_agent_run(run_id: int, db: Session) -> AgentRunResult | None:
    run = db.get(AgentRun, run_id)
    if not run:
        return None
    if run.status == "cancelled":
        return get_agent_run(run_id, db)
    changed = db.query(AgentRun).filter(
        AgentRun.id == run_id, AgentRun.status.in_(["pending", "running", "awaiting_review"]),
    ).update({"status": "cancelled", "current_step": "cancelled"}, synchronize_session=False)
    if not changed:
        db.rollback()
        raise WorkflowConflict("该任务已结束，不能取消。")
    db.expire(run)
    data = dict(run.result_json or {})
    data["cancellation"] = {"at": _utcnow().isoformat(), "scope": "stop_at_node_boundary"}
    run.result_json = data
    db.query(AgentStep).filter(AgentStep.run_id == run_id, AgentStep.status.in_(["pending", "running", "awaiting_review"])).update(
        {"status": "cancelled", "completed_at": _utcnow()}, synchronize_session=False,
    )
    db.commit()
    db.expire_all()
    return get_agent_run(run_id, db)


def review_agent_run(run_id: int, req: AgentReviewCreate, db: Session) -> AgentRunResult | None:
    run = db.get(AgentRun, run_id)
    if not run:
        return None
    status = "approved" if req.decision == "approve" else "rejected"
    changed = db.query(AgentRun).filter(AgentRun.id == run_id, AgentRun.status == "awaiting_review").update(
        {"status": status, "current_step": status}, synchronize_session=False,
    )
    if not changed:
        db.rollback()
        raise WorkflowConflict("只有待人工审核任务可以提交审核，已提交的审核不能覆盖。")
    db.expire(run)
    draft = db.get(Draft, run.draft_id) if run.draft_id else None
    if not draft:
        db.rollback()
        raise WorkflowConflict("草稿不存在，不能审核。")
    if req.decision == "approve":
        try:
            validate_review_evidence(run, draft, db)
        except WorkflowConflict:
            db.rollback()
            raise
    review = {"decision": req.decision, "note": req.note.strip(), "at": _utcnow().isoformat(),
              "actor": (current_tenant.get().user_id if current_tenant.get() else "local_user"), "publishes_content": False,
              "content_hash": draft_snapshot_hash(draft, db)}
    data = dict(run.result_json or {})
    data.pop("review_invalidated", None)
    run.result_json = {**data, "review": review}
    draft.status = status
    step = _steps_by_key(run_id, AtomicStepSession(db))["human_review"]
    step.status = "completed"
    step.completed_at = _utcnow()
    step.output_json = review
    db.commit()
    return get_agent_run(run_id, db)


def submit_agent_run_review(run_id: int, db: Session) -> AgentRunResult | None:
    run = db.get(AgentRun, run_id)
    if run is None:
        return None
    changed = db.query(AgentRun).filter(AgentRun.id == run_id, AgentRun.status == "rejected").update(
        {"status": "awaiting_review", "current_step": "human_review"}, synchronize_session=False,
    )
    if not changed:
        db.rollback()
        raise WorkflowConflict("只有已退回的任务可以重新提交审核。")
    db.expire(run)
    draft = db.get(Draft, run.draft_id) if run.draft_id else None
    if draft is None:
        db.rollback()
        raise WorkflowConflict("草稿不存在，不能重新提交审核。")
    try:
        validate_review_evidence(run, draft, db)
    except WorkflowConflict:
        db.rollback()
        raise
    data = archive_review(run.result_json or {}, "重新提交人工审核")
    data["last_submitted_at"] = _utcnow().isoformat()
    run.result_json = data
    draft.status = "awaiting_review"
    step = _steps_by_key(run_id, AtomicStepSession(db))["human_review"]
    step.status, step.started_at, step.completed_at = "awaiting_review", _utcnow(), None
    step.output_json = None
    db.commit()
    return get_agent_run(run_id, db)


def recover_interrupted_agent_runs(db: Session) -> int:
    """Call once at startup, after schema initialization, in a single-process app.

    Interrupted work is made visible and never auto-replayed (API calls can cost).
    Completed SQL nodes and their artifacts survive; the unfinished node is retried.
    """
    runs = db.query(AgentRun).filter(AgentRun.status.in_(["pending", "running"])).all()
    for run in runs:
        step = db.query(AgentStep).filter(
            AgentStep.run_id == run.id, AgentStep.status.notin_(["completed", "skipped"]),
        ).order_by(AgentStep.step_index).first()
        message = "服务在任务完成前中断。已保留完成步骤，请显式重试继续；未完成的模型调用可能再次计费。"
        if step:
            step.status = "failed"
            step.error_message = message
            step.completed_at = _utcnow()
            run.current_step = step.key
        run.status = "failed"
        run.error_message = message
        run.result_json = {**(run.result_json or {}), "failure": {
            "code": "INTERRUPTED", "message": message, "recoverable": True,
            "at": _utcnow().isoformat(),
        }}
    db.commit()
    return len(runs)


def get_agent_run(run_id: int, db: Session) -> AgentRunResult | None:
    run = db.query(AgentRun).filter(AgentRun.id == run_id).first()
    if not run:
        return None
    topic = db.query(Topic).filter(Topic.id == run.selected_topic_id).first() if run.selected_topic_id else None
    draft = db.query(Draft).filter(Draft.id == run.draft_id).first() if run.draft_id else None
    cards = db.query(Card).filter(Card.draft_id == run.draft_id).order_by(Card.page_index).all() if run.draft_id else []
    evaluation = (run.result_json or {}).get("reevaluation") or (run.result_json or {}).get("evaluation")
    return _run_result(run, db, topic=topic, draft=draft, cards=cards, evaluation=evaluation)


async def run_content_growth_agent(req: AgentRunCreate, db: Session) -> AgentRunResult:
    """兼容旧调用：同步执行并返回最终结果。"""
    result = create_agent_run(req, db)
    await execute_agent_run(result.id)
    db.expire_all()
    refreshed = get_agent_run(result.id, db)
    if not refreshed:
        raise RuntimeError("Agent Run 创建后未找到")
    return refreshed


class ContentWorkflowState(TypedDict, total=False):
    run_id: int
    topic_id: int
    draft_id: int
    selected_idea: dict
    evaluation: dict


def build_content_graph(run: AgentRun, req: AgentRunCreate, db: Session):
    """A bounded graph with a quality branch and a persisted human-review gate.

    The graph re-enters at START on explicit retry. Completed nodes return their
    SQL checkpoints, so only failed and downstream nodes execute side effects.
    """
    steps = _steps_by_key(run.id, db)
    builder = StateGraph(ContentWorkflowState)

    def topic():
        value = db.get(Topic, run.selected_topic_id) if run.selected_topic_id else None
        if not value:
            raise ValueError("选题检查点不存在")
        return value

    def draft():
        value = db.get(Draft, run.draft_id) if run.draft_id else None
        if not value:
            raise ValueError("草稿检查点不存在")
        return value

    def cards():
        return db.query(Card).filter(Card.draft_id == run.draft_id).order_by(Card.page_index).all()

    async def dispatch(key, state, node_db):
        step = steps[key]
        if key == "retrieve_context":
            await _step_retrieve_context(run, req, step, node_db)
        elif key == "topic_ideas":
            idea = await _step_topic_ideas(run, req, step, node_db)
            return {"selected_idea": _idea_payload(idea)}
        elif key == "create_topic":
            value = await _step_create_topic(run, _idea_from_payload(state["selected_idea"]), step, node_db)
            return {"topic_id": value.id}
        elif key == "score_topic":
            await _step_score_topic(run, req, topic(), step, node_db)
        elif key == "generate_draft":
            value = await _step_generate_draft(run, req, topic(), step, node_db)
            return {"draft_id": value.id}
        elif key == "generate_cards":
            await _step_generate_cards(run, req, draft(), step, node_db)
        elif key == "compliance_check":
            await _step_compliance(run, req, draft(), step, node_db)
        elif key in {"evaluate_package", "reevaluate_package"}:
            result_key = "evaluation" if key == "evaluate_package" else "reevaluation"
            value = await _step_evaluate(run, req, draft(), cards(), step, node_db, result_key)
            return {"evaluation": value}
        elif key == "revise_package":
            await _step_revise(run, draft(), cards(), state["evaluation"], step, node_db)
        elif key == "agent_decision":
            await _step_agent_decision(run, req, topic(), draft(), cards(), state["evaluation"], step, node_db)
        return {}

    def node_for(key):
        async def node(state):
            # End the read transaction before consulting the current cancellation state.
            db.rollback()
            db.refresh(run)
            if run.status == "cancelled":
                raise WorkflowCancelled()
            if run.status != "running":
                raise WorkflowConflict("任务已离开执行状态")
            step = steps[key]
            db.refresh(step)
            if step.status not in {"completed", "skipped"} or (key == "retrieve_context" and req.required_facts):
                _start_step(run, step, {"run_id": run.id, "engine": "langgraph"}, db)
            attempt = int(((run.result_json or {}).get("workflow") or {}).get("attempt") or 1)
            # ContextVars follow this node's async execution and reset even when
            # the node fails. Model logs commit independently of node artifacts.
            with model_trace_context(run.id, step.id, key, attempt):
                output = await dispatch(key, state, AtomicStepSession(db))
            # Cancellation is cooperative: a running remote call cannot be recalled.
            with SessionLocal() as observer:
                status = observer.query(AgentRun.status).filter(AgentRun.id == run.id).scalar()
            if status == "cancelled":
                db.rollback()
                raise WorkflowCancelled()
            db.commit()
            return output
        return node

    keys = [key for key, _ in STEP_PLAN if key != "human_review"]
    for key in keys:
        builder.add_node(key, node_for(key))

    async def skip_revision(state):
        _skip_step(steps["revise_package"], {"reason": "质量评分达到阈值，未触发规则修订"}, AtomicStepSession(db))
        _skip_step(steps["reevaluate_package"], {"reason": "未触发规则修订"}, AtomicStepSession(db))
        db.commit()
        return {}

    async def await_review(state):
        db.rollback()
        db.refresh(run)
        if run.status == "cancelled":
            raise WorkflowCancelled()
        if req.use_rag:
            validate_citations(draft().body_text or "", (run.result_json or {}).get("citations") or [])
        changed = db.query(AgentRun).filter(AgentRun.id == run.id, AgentRun.status == "running").update(
            {"status": "awaiting_review", "current_step": "human_review"}, synchronize_session=False,
        )
        if not changed:
            db.rollback()
            raise WorkflowCancelled()
        topic().status = "generated"
        draft().status = "awaiting_review"
        step = steps["human_review"]
        step.status = "awaiting_review"
        step.started_at = _utcnow()
        step.input_json = {"draft_id": run.draft_id, "requires_manual_source_check": True}
        db.commit()
        db.refresh(run)
        return {}

    builder.add_node("skip_revision", skip_revision)
    builder.add_node("await_review", await_review)
    builder.add_edge(START, "retrieve_context")
    for previous, following in zip(keys[:7], keys[1:8]):
        builder.add_edge(previous, following)
    builder.add_conditional_edges(
        "evaluate_package",
        lambda state: "revise_package" if int(state["evaluation"].get("overall_score") or 0) < REVISION_THRESHOLD else "skip_revision",
        {"revise_package": "revise_package", "skip_revision": "skip_revision"},
    )
    builder.add_edge("revise_package", "reevaluate_package")
    builder.add_edge("reevaluate_package", "agent_decision")
    builder.add_edge("skip_revision", "agent_decision")
    builder.add_edge("agent_decision", "await_review")
    builder.add_edge("await_review", END)
    return builder.compile()


async def _execute_steps(run: AgentRun, req: AgentRunCreate, db: Session) -> None:
    try:
        graph = build_content_graph(run, req, db)
        await graph.ainvoke({"run_id": run.id})
    except WorkflowCancelled:
        db.rollback()
    except Exception as exc:
        db.rollback()
        db.refresh(run)
        if run.status == "cancelled":
            return
        code, message, error_type = _safe_failure(exc)
        changed = db.query(AgentRun).filter(AgentRun.id == run.id, AgentRun.status == "running").update(
            {"status": "failed", "error_message": message}, synchronize_session=False,
        )
        if not changed:
            db.rollback()
            return
        _fail_running_step(run.id, message, AtomicStepSession(db))
        run.result_json = {**(run.result_json or {}), "failure": {
            "code": code, "message": message, "error_type": error_type,
            "step": run.current_step, "recoverable": True,
            "at": _utcnow().isoformat(),
        }}
        if isinstance(exc, EvidenceError) and hasattr(exc, "retrieval"):
            run.result_json = {**run.result_json, "rag_context": exc.retrieval}
        db.commit()


async def _step_retrieve_context(run: AgentRun, req: AgentRunCreate, step: AgentStep, db: Session) -> dict:
    existing = _result(run, "rag_context")
    # Explicit requirements must be rechecked against live evidence on retry;
    # a completed retrieval checkpoint cannot preserve a revoked/expired fact.
    if step.status in {"completed", "skipped"} and existing and not req.required_facts:
        return existing
    if not req.use_rag:
        payload = {"enabled": False, "evidence_status": "disabled", "hits": [], "reason": "本次 Agent Run 未启用知识库检索。",
                   **assess_required_facts([], [])}
        _set_result(run, "rag_context", payload, db)
        _skip_step(step, payload, db)
        return payload

    _start_step(run, step, {
        "goal": req.goal,
        "workspace_id": req.workspace_id,
        "knowledge_base_id": req.knowledge_base_id,
        "top_k": req.rag_top_k,
        "min_score": req.rag_min_score,
        "required_facts": [item.model_dump() for item in req.required_facts],
    }, db)
    context = workspace_context(db, req.workspace_id)
    knowledge_base = get_knowledge_base_or_default(db, context, req.knowledge_base_id)
    query = " ".join(part for part in [
        req.goal,
        req.target_audience,
        req.viewpoint,
        req.personal_case[:240] if req.personal_case else "",
    ] if part)
    hits = search_knowledge(
        query=query,
        db=db,
        context=context,
        knowledge_base_id=knowledge_base.id,
        top_k=req.rag_top_k,
        min_score=req.rag_min_score,
    )
    candidates = hits
    hits = [hit for hit in select_evidence(candidates) if hit.content]
    facts = assess_required_facts(hits, req.required_facts)
    payload = {
        **facts,
        "enabled": True,
        "workspace_id": context.workspace_id,
        "workspace_slug": context.workspace_slug,
        "knowledge_base_id": knowledge_base.id,
        "knowledge_base_name": knowledge_base.name,
        "query": query,
        "coverage": _rag_coverage(hits),
        "candidate_count": len(candidates),
        "discarded_below_threshold": len(candidates) - len(hits),
        "retrieval_candidates": [hit.to_dict() for hit in candidates],
        "evidence_status": "sufficient" if _rag_has_sufficient_evidence(hits) else "weak_or_empty",
        "hits": [hit.to_dict() for hit in hits],
        "boundary": "本地单用户资料分组；检索按 workspace_id + knowledge_base_id 筛选，不提供身份认证或多租户安全隔离。",
    }
    if facts["missing_facts"]:
        payload["evidence_status"] = "missing_structured_facts"
        payload["refusal_reason"] = "missing_structured_facts"
        error = EvidenceError(f"缺少必需产品参数的已核验证据：{missing_fact_labels(facts['missing_facts'])}。任务已停止生成，请补充带版本及原文定位的资料后重试。")
        error.retrieval = payload
        raise error
    if payload["evidence_status"] != "sufficient":
        error = EvidenceError("检索证据不足，任务已停止生成。请导入相关资料或调整任务后重试。")
        error.retrieval = payload
        raise error
    _set_result(run, "rag_context", payload, db)
    _finish_step(step, "completed", payload, db)
    return payload


async def _step_topic_ideas(run: AgentRun, req: AgentRunCreate, step: AgentStep, db: Session) -> CustomTopicIdea:
    existing = _result(run, "topic_ideas")
    if step.status == "completed" and existing:
        return _idea_from_payload(_selected_idea_payload(existing))
    rag_note = _rag_note_for_prompt(run)
    brief = _result(run, "brief")
    brief_note = ("任务要求（只约束写法，不是事实来源）：" + json.dumps(brief, ensure_ascii=False)) if brief else ""
    _start_step(run, step, {"goal": req.goal, "mode": run.mode, "provider": req.provider or "local", "rag_enabled": bool(rag_note)}, db)
    ideas_result = await generate_custom_topic_ideas(
        mode=run.mode,
        research_depth=req.research_depth,
        theme=req.goal,
        target_audience=req.target_audience,
        viewpoint=_join_text(req.viewpoint, brief_note),
        personal_case=_join_text(req.personal_case, rag_note),
        content_type=req.content_type,
        source_urls=req.source_urls,
        provider=req.provider or "local",
        model=req.model,
    )
    ideas = sorted(ideas_result.ideas, key=lambda item: item.score, reverse=True)
    selected_idea = ideas[0]
    _enrich_idea_with_rag(selected_idea, run)
    payload = {
        "research_status": ideas_result.research_status,
        "keywords": ideas_result.keywords,
        "ideas": [_idea_payload(item) for item in ideas],
        "recommended_title": selected_idea.title,
        "rag_context_used": _rag_context_brief(run),
    }
    _set_result(run, "topic_ideas", payload, db)
    _finish_step(step, "completed", payload, db)
    return selected_idea


async def _step_create_topic(run: AgentRun, selected_idea: CustomTopicIdea, step: AgentStep, db: Session) -> Topic:
    if run.selected_topic_id:
        topic = db.query(Topic).filter(Topic.id == run.selected_topic_id).first()
        if topic:
            if step.status != "completed":
                _finish_step(step, "completed", {"topic_id": topic.id, "reused_checkpoint": True}, db)
            return topic
    _start_step(run, step, {"recommended_title": selected_idea.title}, db)
    topic = _create_topic_from_idea(selected_idea, db)
    run.selected_topic_id = topic.id
    payload = {
        "topic_id": topic.id,
        "source_id": topic.source_id,
        "title": topic.title,
        "score": topic.score,
        "verification_status": selected_idea.verification_status,
    }
    _set_result(run, "topic", payload, db)
    _finish_step(step, "completed", payload, db)
    return topic


async def _step_score_topic(run: AgentRun, req: AgentRunCreate, topic: Topic, step: AgentStep, db: Session) -> Topic:
    if step.status == "completed":
        db.refresh(topic)
        return topic
    _start_step(run, step, {"topic_id": topic.id, "auto_score": req.auto_score}, db)
    if req.auto_score:
        score_result = await score_topic(topic.id, db, provider=req.provider or "local", model=req.model or None)
        db.refresh(topic)
    else:
        score_result = {"score": topic.score, "reason": topic.score_reason, "skipped": True}
    payload = {
        "topic_id": topic.id,
        "score": topic.score,
        "risk_level": topic.risk_level,
        "result": _compact(score_result),
    }
    _set_result(run, "score", payload, db)
    _finish_step(step, "completed", payload, db)
    return topic


async def _step_generate_draft(run: AgentRun, req: AgentRunCreate, topic: Topic, step: AgentStep, db: Session) -> Draft:
    if run.draft_id:
        draft = db.query(Draft).filter(Draft.id == run.draft_id).first()
        if draft:
            if step.status != "completed":
                _finish_step(step, "completed", {"draft_id": draft.id, "reused_checkpoint": True}, db)
            return draft
    _start_step(run, step, {"topic_id": topic.id}, db)
    if req.use_rag:
        hits = (_result(run, "rag_context") or {}).get("hits") or []
        draft, citations = await generate_evidence_draft(topic, req, hits, db, business_brief=_result(run, "brief"))
        _set_result(run, "citations", citations, db)
    else:
        draft = await generate_draft(topic, db, provider=req.provider or "local", model=req.model or None)
    run.draft_id = draft.id
    payload = {
        "draft_id": draft.id,
        "title_options": draft.title_options or [],
        "cover_text_options": draft.cover_text_options or [],
    }
    _set_result(run, "draft", payload, db)
    _finish_step(step, "completed", payload, db)
    return draft


async def _step_generate_cards(run: AgentRun, req: AgentRunCreate, draft: Draft, step: AgentStep, db: Session) -> list[Card]:
    existing_cards = db.query(Card).filter(Card.draft_id == draft.id).order_by(Card.page_index).all()
    if step.status == "completed" and existing_cards:
        return existing_cards
    _start_step(run, step, {"draft_id": draft.id}, db)
    if existing_cards:
        for card in existing_cards:
            db.delete(card)
        db.commit()
    if req.use_rag:
        # Source cards remain extractive, so quoted evidence cannot silently turn
        # into unsourced claims through the generic card-generation prompt.
        citations = _result(run, "citations") or []
        cover_title = str((draft.title_options or [run.goal])[0])
        cards = [Card(draft_id=draft.id, **spec)
                 for spec in build_evidence_cards(cover_title, citations)]
        db.add_all(cards)
        db.flush()
    else:
        cards = await generate_cards(draft, db, provider=req.provider or "local", model=req.model or None)
    draft.max_card_count = len(cards)
    payload = {
        "draft_id": draft.id,
        "card_count": len(cards),
        "cards": [{"id": card.id, "page_index": card.page_index, "title": card.title} for card in cards],
    }
    _set_result(run, "cards", payload, db)
    _finish_step(step, "completed", payload, db)
    return cards


async def _step_compliance(run: AgentRun, req: AgentRunCreate, draft: Draft, step: AgentStep, db: Session) -> None:
    if step.status == "completed":
        return
    _start_step(run, step, {"draft_id": draft.id}, db)
    compliance = await check_compliance(draft, db, provider=req.provider or "local", model=req.model or None)
    db.refresh(draft)
    payload = _compact(compliance)
    _set_result(run, "compliance", payload, db)
    _finish_step(step, "completed", payload, db)


async def _step_evaluate(
    run: AgentRun,
    req: AgentRunCreate,
    draft: Draft,
    cards: list[Card],
    step: AgentStep,
    db: Session,
    result_key: str,
) -> dict:
    if step.status == "completed":
        return _result(run, result_key) or {}
    _start_step(run, step, {"draft_id": draft.id, "card_count": len(cards)}, db)
    evaluation = await evaluate_draft(draft, cards, db, provider=req.provider or "local", model=req.model or "")
    run.evaluation_score = int(evaluation.get("overall_score") or 0)
    payload = _compact(evaluation)
    _set_result(run, result_key, payload, db)
    _finish_step(step, "completed", payload, db)
    return evaluation


async def _step_revise(run: AgentRun, draft: Draft, cards: list[Card], evaluation: dict, step: AgentStep, db: Session) -> None:
    if step.status == "completed":
        return
    _start_step(run, step, {"draft_id": draft.id, "evaluation_score": evaluation.get("overall_score")}, db)
    payload = _revise_draft_and_cards(draft, cards, evaluation, db)
    _set_result(run, "revision", payload, db)
    _finish_step(step, "completed", payload, db)


async def _step_agent_decision(
    run: AgentRun,
    req: AgentRunCreate,
    topic: Topic,
    draft: Draft,
    cards: list[Card],
    evaluation: dict,
    step: AgentStep,
    db: Session,
) -> None:
    if step.status == "completed":
        return
    _start_step(run, step, {"draft_id": draft.id, "topic_id": topic.id, "evaluation_score": run.evaluation_score}, db)
    payload = _build_agent_decision(run, req, topic, draft, cards, evaluation)
    _set_result(run, "agent_decision", payload, db)
    _finish_step(step, "completed", payload, db)


def _build_agent_decision(
    run: AgentRun,
    req: AgentRunCreate,
    topic: Topic,
    draft: Draft,
    cards: list[Card],
    evaluation: dict,
) -> dict:
    data = run.result_json or {}
    topic_ideas = data.get("topic_ideas") if isinstance(data.get("topic_ideas"), dict) else {}
    selected_idea: dict[str, Any] = {}
    if topic_ideas:
        try:
            selected_idea = _selected_idea_payload(topic_ideas)
        except ValueError:
            selected_idea = {}

    revision = data.get("revision") if isinstance(data.get("revision"), dict) else {}
    score = int(evaluation.get("overall_score") or run.evaluation_score or 0)
    readiness = str(evaluation.get("publish_readiness") or "needs_review")
    verification_status = str(selected_idea.get("verification_status") or "未核验")
    confidence = _decision_confidence(score, verification_status, readiness)
    issues = evaluation.get("issues") if isinstance(evaluation.get("issues"), list) else []
    strengths = evaluation.get("strengths") if isinstance(evaluation.get("strengths"), list) else []
    references = selected_idea.get("references") if isinstance(selected_idea.get("references"), list) else []
    rag_context = data.get("rag_context") if isinstance(data.get("rag_context"), dict) else {}

    decision_status = "ready_for_review"
    if readiness == "not_ready" or score < 60:
        decision_status = "needs_major_revision"
    elif readiness != "ready" or score < REVISION_THRESHOLD:
        decision_status = "needs_review"

    why_this_topic = [
        item for item in [
            selected_idea.get("reason"),
            _rag_why_this_topic(rag_context),
            f"选题分数 {topic.score}/100，角度是「{topic.content_angle or '未填写'}」。",
            f"目标人群：{topic.target_audience or req.target_audience or 'AI 新手 / 职场人'}。",
        ] if item
    ]

    next_actions = _build_next_actions(score, readiness, bool(revision), issues, references)
    manual_review_focus = _manual_review_focus(draft, issues, verification_status)
    _apply_rag_decision_guidance(rag_context, next_actions, manual_review_focus)
    summary = (
        f"Agent 已围绕「{run.goal[:60]}」选择「{topic.title}」，"
        f"生成 {len(cards)} 页图文发布包，当前质量评分 {score}/100。"
    )

    return {
        "summary": summary,
        "decision_status": decision_status,
        "confidence": confidence,
        "selected_topic": {
            "id": topic.id,
            "title": topic.title,
            "score": topic.score,
            "content_angle": topic.content_angle,
            "verification_status": verification_status,
            "reason": selected_idea.get("reason") or topic.score_reason,
        },
        "quality_gate": {
            "score": score,
            "threshold": REVISION_THRESHOLD,
            "publish_readiness": readiness,
            "passed": score >= REVISION_THRESHOLD and readiness != "not_ready",
            "strengths": [str(item) for item in strengths[:4]],
        },
        "revision": {
            "triggered": bool(revision),
            "changes": revision.get("changes") if revision else [],
            "reason": revision.get("reason") if revision else "质量评分达到阈值，未触发自动改稿。",
        },
        "why_this_topic": [str(item) for item in why_this_topic[:4]],
        "manual_review_focus": manual_review_focus,
        "next_actions": next_actions,
        "source_trace": {
            "research_status": topic_ideas.get("research_status") if isinstance(topic_ideas, dict) else None,
            "keywords": topic_ideas.get("keywords") if isinstance(topic_ideas, dict) else [],
            "references": references[:5],
            "rag_context": _rag_context_brief(run),
        },
    }


def _rag_coverage(hits: list) -> dict:
    if not hits:
        return {"top_score": 0, "evidence_count": 0, "distinct_documents": 0, "status": "insufficient"}
    top_score = max(float(hit.score) for hit in hits)
    distinct_documents = len({hit.document_id for hit in hits})
    return {
        "top_score": round(top_score, 4),
        "evidence_count": len(hits),
        "distinct_documents": distinct_documents,
        "status": "sufficient" if _rag_has_sufficient_evidence(hits) else "weak",
    }


def _rag_has_sufficient_evidence(hits: list) -> bool:
    # RRF ranks candidates; evidence eligibility is a separate, mode-aware gate.
    return bool(select_evidence(hits))


def _rag_note_for_prompt(run: AgentRun) -> str:
    rag_context = _result(run, "rag_context")
    if not isinstance(rag_context, dict) or not rag_context.get("enabled"):
        return ""
    hits = rag_context.get("hits") if isinstance(rag_context.get("hits"), list) else []
    if not hits:
        return "知识库检索结果：没有找到足够相关的证据。请把事实性内容标记为待核验，不要编造来源。"
    lines = [
        "知识库检索证据：",
        "只能把下面 chunk 当作事实依据；没有覆盖到的内容必须标记为待核验。",
    ]
    for hit in hits[:5]:
        if not isinstance(hit, dict):
            continue
        chunk_id = hit.get("chunk_id")
        title = hit.get("title") or "未命名来源"
        content = str(hit.get("content") or "").replace("\n", " ")[:420]
        lines.append(f"- [chunk:{chunk_id}] {title}：{content}")
    return "\n".join(lines)


def _rag_context_brief(run: AgentRun) -> dict:
    rag_context = _result(run, "rag_context")
    if not isinstance(rag_context, dict):
        return {"enabled": False, "evidence_status": "disabled", "hits": []}
    hits = rag_context.get("hits") if isinstance(rag_context.get("hits"), list) else []
    return {
        "enabled": bool(rag_context.get("enabled")),
        "workspace_id": rag_context.get("workspace_id"),
        "knowledge_base_id": rag_context.get("knowledge_base_id"),
        "knowledge_base_name": rag_context.get("knowledge_base_name"),
        "evidence_status": rag_context.get("evidence_status"),
        "coverage": rag_context.get("coverage") or {},
        "hits": [
            {
                "chunk_id": hit.get("chunk_id"),
                "title": hit.get("title"),
                "score": hit.get("score"),
                "source_uri": hit.get("source_uri"),
            }
            for hit in hits[:5]
            if isinstance(hit, dict)
        ],
    }


def _enrich_idea_with_rag(idea: CustomTopicIdea, run: AgentRun) -> None:
    rag_context = _result(run, "rag_context")
    if not isinstance(rag_context, dict) or not rag_context.get("enabled"):
        return
    hits = rag_context.get("hits") if isinstance(rag_context.get("hits"), list) else []
    if not hits:
        idea.risk_tip = _join_text(idea.risk_tip, "知识库未检索到足够证据，发布前必须补来源或标记为观点创作。")
        return

    references = list(idea.references or [])
    for hit in hits[:4]:
        if not isinstance(hit, dict):
            continue
        references.append(ResearchReference(
            title=f"知识库证据：{hit.get('title') or '未命名来源'} / chunk {hit.get('chunk_id')}",
            url=str(hit.get("source_uri") or ""),
            summary=str(hit.get("content") or "")[:520],
            source_type="knowledge_base",
            status="ok",
        ))
    idea.references = references
    idea.verification_status = "有知识库证据待人工核验"
    idea.summary = _join_text(idea.summary, _rag_summary_for_idea(hits))
    idea.reason = _join_text(idea.reason, "已优先结合知识库检索证据，适合做成带来源复查的内容。")


def _rag_summary_for_idea(hits: list) -> str:
    excerpts = []
    for hit in hits[:2]:
        if isinstance(hit, dict) and hit.get("content"):
            excerpts.append(str(hit["content"]).replace("\n", " ")[:180])
    return "知识库证据摘要：" + " / ".join(excerpts) if excerpts else ""


def _rag_why_this_topic(rag_context: dict) -> str:
    if not rag_context.get("enabled"):
        return ""
    coverage = rag_context.get("coverage") if isinstance(rag_context.get("coverage"), dict) else {}
    evidence_count = coverage.get("evidence_count") or 0
    if rag_context.get("evidence_status") == "sufficient":
        return f"知识库检索命中 {evidence_count} 条证据，可作为内容事实依据。"
    return "知识库证据不足，本次更适合作为观点/方案草稿，发布前需要补充来源。"


def _apply_rag_decision_guidance(rag_context: dict, actions: list[dict[str, str]], review_focus: list[str]) -> None:
    if not rag_context.get("enabled"):
        return
    if rag_context.get("evidence_status") == "sufficient":
        review_focus.insert(0, "逐条核对知识库 chunk 引用，确认内容没有超出证据范围。")
        return
    review_focus.insert(0, "RAG 没有找到足够证据，事实性表达必须补来源或改成观点/方案。")
    actions.insert(0, {
        "priority": "high",
        "label": "先补充或索引相关素材",
        "reason": "本次启用了知识库检索，但证据不足；继续发布前应先补来源。",
        "target": "sources",
    })


def _join_text(*parts: str) -> str:
    return "\n\n".join(str(part).strip() for part in parts if str(part or "").strip())


def _decision_confidence(score: int, verification_status: str, readiness: str) -> str:
    if "未" in verification_status or readiness == "not_ready" or score < 60:
        return "low"
    if score >= 80 and readiness == "ready":
        return "high"
    return "medium"


def _build_next_actions(score: int, readiness: str, revised: bool, issues: list, references: list) -> list[dict[str, str]]:
    actions: list[dict[str, str]] = []
    if score < 60 or readiness == "not_ready":
        actions.append({
            "priority": "high",
            "label": "先补事实来源或个人案例",
            "reason": "当前发布包还不适合直接进入发布前审核，需要增强可信度和例子。",
            "target": "topic",
        })
    elif score < REVISION_THRESHOLD:
        actions.append({
            "priority": "high",
            "label": "优先改封面和前两页卡片",
            "reason": "质量评分未达到自动通过线，先处理钩子、密度和收藏价值。",
            "target": "cards",
        })
    else:
        actions.append({
            "priority": "high",
            "label": "进入人工审核清单",
            "reason": "内容质量已过基础线，下一步应人工核验事实、风险表达和 AIGC 标识。",
            "target": "review_checklist",
        })

    if revised:
        actions.append({
            "priority": "medium",
            "label": "复查自动改稿处",
            "reason": "Agent 已做过一次轻量修订，建议确认标题和卡片压缩后是否仍然自然。",
            "target": "draft",
        })
    if not references:
        actions.append({
            "priority": "medium",
            "label": "补一个可追溯来源",
            "reason": "本次主要依赖主题灵感，发布前最好补充工具官网、项目 README 或真实操作截图。",
            "target": "source",
        })
    if issues:
        actions.append({
            "priority": "medium",
            "label": "逐条处理质量问题",
            "reason": "质量评分器已经列出具体问题，处理后可以重新评分。",
            "target": "evaluation",
        })
    actions.append({
        "priority": "low",
        "label": "发布后录入数据",
        "reason": "手动发布后记录阅读、点赞、收藏和评论，后续复盘才能反推选题质量。",
        "target": "metrics",
    })
    return actions[:5]


def _manual_review_focus(draft: Draft, issues: list, verification_status: str) -> list[str]:
    focus = []
    if "未" in verification_status:
        focus.append("核验来源和关键事实，避免把观点创作写成事实结论。")
    for issue in issues[:3]:
        if isinstance(issue, dict) and issue.get("message"):
            page = f"第 {issue.get('card_page')} 页：" if issue.get("card_page") else ""
            focus.append(f"{page}{issue.get('message')}")
    if draft.risk_tips:
        focus.append(str(draft.risk_tips[0]))
    if draft.aigc_notice:
        focus.append("保留 AIGC 标识建议，发布前按平台要求处理。")
    return list(dict.fromkeys(focus))[:5]


def _revise_draft_and_cards(draft: Draft, cards: list[Card], evaluation: dict, db: Session) -> dict:
    changes: list[str] = []
    title_options = list(draft.title_options or [])
    evidence_based = "[chunk:" in (draft.body_text or "")
    if title_options and not evidence_based:
        original = str(title_options[0])
        if not any(word in original for word in ["别", "先", "步骤", "清单"]):
            title_options[0] = f"先别急着全自动：{original[:22]}"
            changes.append("强化第一个标题的行动钩子")
    cover_options = list(draft.cover_text_options or [])
    if cover_options and not evidence_based:
        cover_options[0] = str(cover_options[0])[:18] or "先跑通这套流程"
        if "流程" not in cover_options[0]:
            cover_options[0] = f"{cover_options[0]}流程"
        changes.append("收紧封面文案")
    if draft.body_text and len(draft.body_text) > 900 and "[chunk:" not in draft.body_text:
        draft.body_text = draft.body_text[:880] + "\n\n最后发布前我会人工核验事实和边界。"
        changes.append("压缩正文长度并补充人工核验提醒")
    draft.title_options = title_options
    draft.cover_text_options = cover_options
    risks = list(draft.risk_tips or [])
    risk = "发布前确认工具能力、数据来源和个人案例是否真实，不写成保证收益。"
    if risk not in risks:
        risks.append(risk)
        draft.risk_tips = risks
        changes.append("补充风险提示")

    for card in cards:
        if card.body and len(card.body) > 150:
            shortened = complete_sentence_excerpt(card.body, 138)
            if shortened != card.body:
                card.body = shortened
                changes.append(f"按完整句精简第 {card.page_index} 页正文")
        if card.page_index == 1 and title_options:
            card.title = str(title_options[0])[:255]
            changes.append("同步封面卡标题")

    db.commit()
    db.refresh(draft)
    for card in cards:
        db.refresh(card)
    return {
        "changed": bool(changes),
        "changes": list(dict.fromkeys(changes)),
        "reason": "质量评分低于阈值，已进行一次本地规则轻量修订。",
    }


def _create_steps(run_id: int, db: Session) -> dict[str, AgentStep]:
    steps: dict[str, AgentStep] = {}
    for index, (key, label) in enumerate(STEP_PLAN, start=1):
        step = AgentStep(run_id=run_id, step_index=index, key=key, label=label, status="pending")
        db.add(step)
        steps[key] = step
    db.commit()
    for step in steps.values():
        db.refresh(step)
    return steps


def _steps_by_key(run_id: int, db: Session) -> dict[str, AgentStep]:
    existing = db.query(AgentStep).filter(AgentStep.run_id == run_id).order_by(AgentStep.step_index).all()
    if not existing:
        return _create_steps(run_id, db)
    by_key = {step.key: step for step in existing}
    changed = False
    for index, (key, label) in enumerate(STEP_PLAN, start=1):
        if key not in by_key:
            db.add(AgentStep(run_id=run_id, step_index=index, key=key, label=label, status="pending"))
            changed = True
        elif by_key[key].step_index != index:
            by_key[key].step_index = index
            changed = True
    if changed:
        db.flush()
        db.commit()
        existing = db.query(AgentStep).filter(AgentStep.run_id == run_id).order_by(AgentStep.step_index).all()
    return {step.key: step for step in existing}


def _start_step(run: AgentRun, step: AgentStep, input_json: dict[str, Any], db: Session) -> None:
    run.current_step = step.key
    step.status = "running"
    step.started_at = _utcnow()
    step.completed_at = None
    step.error_message = None
    step.input_json = _compact(input_json)
    db.commit()


def _finish_step(step: AgentStep, status: str, output_json: dict[str, Any], db: Session) -> None:
    step.status = status
    step.output_json = _compact(output_json)
    step.completed_at = _utcnow()
    db.commit()


def _skip_step(step: AgentStep, output_json: dict[str, Any], db: Session) -> None:
    if step.status in {"completed", "skipped"}:
        return
    step.status = "skipped"
    step.output_json = _compact(output_json)
    step.completed_at = _utcnow()
    db.commit()


def _fail_running_step(run_id: int, message: str, db: Session) -> None:
    step = (
        db.query(AgentStep)
        .filter(AgentStep.run_id == run_id, AgentStep.status == "running")
        .order_by(AgentStep.step_index.desc())
        .first()
    )
    if step:
        step.status = "failed"
        step.error_message = message[:1000]
        step.completed_at = _utcnow()
        db.commit()


def _create_topic_from_idea(idea: CustomTopicIdea, db: Session) -> Topic:
    first_url = next((ref.url for ref in idea.references if ref.url and ref.status == "ok"), None)
    source = Source(
        source_type=idea.source_type or "custom_idea",
        title=idea.title[:255],
        url=first_url,
        raw_content=_source_raw(idea)[:20000],
        summary=idea.summary,
    )
    db.add(source)
    db.flush()
    topic = Topic(
        source_id=source.id,
        title=idea.title[:255],
        url=first_url,
        source_type=idea.source_type or "custom_idea",
        raw_summary=idea.summary,
        concise_summary=idea.summary[:255],
        target_audience=idea.target_audience[:100] if idea.target_audience else None,
        content_angle=idea.content_angle[:100] if idea.content_angle else None,
        recommended_platform=idea.recommended_platform or "小红书图文",
        score=max(0, min(100, int(idea.score or 0))),
        score_reason=_score_reason(idea),
        status="pending",
        risk_level="low",
    )
    db.add(topic)
    db.commit()
    db.refresh(topic)
    return topic


def _source_raw(idea: CustomTopicIdea) -> str:
    refs = "\n".join(
        f"- {ref.title} | {ref.url or '无链接'} | {ref.status} | {ref.summary}"
        for ref in idea.references
    )
    return (
        f"Agent 自动选题：{idea.title}\n"
        f"角度：{idea.content_angle}\n"
        f"目标人群：{idea.target_audience}\n"
        f"核验状态：{idea.verification_status}\n"
        f"关键词：{'、'.join(idea.keywords or [])}\n\n"
        f"摘要：{idea.summary}\n\n"
        f"推荐理由：{idea.reason}\n"
        f"风险提醒：{idea.risk_tip}\n\n"
        f"参考来源：\n{refs or '无'}"
    )


def _score_reason(idea: CustomTopicIdea) -> str:
    return "\n".join(
        part for part in [
            idea.reason,
            f"核验状态：{idea.verification_status}" if idea.verification_status else "",
            f"风险提醒：{idea.risk_tip}" if idea.risk_tip else "",
        ] if part
    )


def _idea_payload(idea: CustomTopicIdea) -> dict[str, Any]:
    return {
        "title": idea.title,
        "content_angle": idea.content_angle,
        "target_audience": idea.target_audience,
        "summary": idea.summary,
        "reason": idea.reason,
        "risk_tip": idea.risk_tip,
        "recommended_platform": idea.recommended_platform,
        "source_type": idea.source_type,
        "score": idea.score,
        "keywords": idea.keywords,
        "verification_status": idea.verification_status,
        "references": [_reference_payload(ref) for ref in idea.references],
    }


def _idea_from_payload(payload: dict[str, Any]) -> CustomTopicIdea:
    return CustomTopicIdea(
        title=str(payload.get("title") or "AI 工作流选题"),
        content_angle=str(payload.get("content_angle") or "教程"),
        target_audience=str(payload.get("target_audience") or "AI 新手 / 职场人"),
        summary=str(payload.get("summary") or ""),
        reason=str(payload.get("reason") or "适合转成小红书图文。"),
        risk_tip=str(payload.get("risk_tip") or "需要人工核验事实和边界。"),
        recommended_platform=str(payload.get("recommended_platform") or "小红书图文"),
        source_type=str(payload.get("source_type") or "custom_idea"),
        score=int(payload.get("score") or 65),
        keywords=[str(item) for item in (payload.get("keywords") or [])],
        references=[_reference_from_payload(item) for item in (payload.get("references") or []) if isinstance(item, dict)],
        verification_status=str(payload.get("verification_status") or "未核验"),
    )


def _selected_idea_payload(payload: dict[str, Any]) -> dict[str, Any]:
    ideas = payload.get("ideas") if isinstance(payload.get("ideas"), list) else []
    if not ideas:
        raise ValueError("缺少选题建议，无法继续执行")
    recommended = payload.get("recommended_title")
    for item in ideas:
        if isinstance(item, dict) and item.get("title") == recommended:
            return item
    first = ideas[0]
    if not isinstance(first, dict):
        raise ValueError("选题建议格式错误")
    return first


def _reference_payload(ref: ResearchReference) -> dict[str, Any]:
    return {
        "title": ref.title,
        "url": ref.url,
        "summary": ref.summary,
        "source_type": ref.source_type,
        "status": ref.status,
    }


def _reference_from_payload(payload: dict[str, Any]) -> ResearchReference:
    return ResearchReference(
        title=str(payload.get("title") or ""),
        url=str(payload.get("url") or ""),
        summary=str(payload.get("summary") or ""),
        source_type=str(payload.get("source_type") or "manual"),
        status=str(payload.get("status") or "ok"),
    )


def _request_from_run(run: AgentRun) -> AgentRunCreate | None:
    data = (run.result_json or {}).get("_request")
    if not isinstance(data, dict):
        return None
    return AgentRunCreate(**data)


def _result(run: AgentRun, key: str) -> Any:
    return (run.result_json or {}).get(key)


def _set_result(run: AgentRun, key: str, value: Any, db: Session) -> None:
    data = dict(run.result_json or {})
    data[key] = _compact(value)
    run.result_json = data
    db.commit()


def _prune_results_for_retry(result_json: dict[str, Any], failed_key: str) -> dict[str, Any]:
    keep = {"_request", "workflow"}
    order = [key for key, _ in STEP_PLAN]
    result_key_by_step = {
        "retrieve_context": "rag_context",
        "topic_ideas": "topic_ideas",
        "create_topic": "topic",
        "score_topic": "score",
        "generate_draft": "draft",
        "generate_cards": "cards",
        "compliance_check": "compliance",
        "evaluate_package": "evaluation",
        "revise_package": "revision",
        "reevaluate_package": "reevaluation",
        "agent_decision": "agent_decision",
    }
    for key in order:
        if key == failed_key:
            break
        result_key = result_key_by_step.get(key)
        if result_key:
            keep.add(result_key)
        if key == "generate_draft":
            keep.add("citations")
    return {key: value for key, value in result_json.items() if key in keep}


def _compact(value: Any, max_text: int = 1200) -> Any:
    if isinstance(value, dict):
        return {str(k): _compact(v, max_text=max_text) for k, v in value.items()}
    if isinstance(value, list):
        return [_compact(item, max_text=max_text) for item in value[:20]]
    if isinstance(value, str):
        return value[:max_text]
    return value


def _run_result(
    run: AgentRun,
    db: Session,
    topic: Topic | None = None,
    draft: Draft | None = None,
    cards: list[Card] | None = None,
    evaluation: dict | None = None,
) -> AgentRunResult:
    steps = db.query(AgentStep).filter(AgentStep.run_id == run.id).order_by(AgentStep.step_index).all()
    return AgentRunResult(
        **{
            "id": run.id,
            "goal": run.goal,
            "mode": run.mode,
            "provider": run.provider,
            "model_name": run.model_name,
            "status": run.status,
            "current_step": run.current_step,
            "selected_topic_id": run.selected_topic_id,
            "draft_id": run.draft_id,
            "evaluation_score": run.evaluation_score,
            "result_json": run.result_json,
            "error_message": run.error_message,
            "created_at": run.created_at,
            "updated_at": run.updated_at,
            "steps": steps,
            "topic": TopicOut.model_validate(topic) if topic else None,
            "draft": DraftOut.model_validate(draft) if draft else None,
            "cards": [CardOut.model_validate(card) for card in (cards or [])],
            "evaluation": evaluation,
        }
    )
