"""Build a manual handoff from a still-approved content snapshot.

This is a read-only projection, not a publishing connector or a delivery receipt.
Only reviewed draft content and a small business-field allowlist leave the service.
"""
from copy import deepcopy
from datetime import datetime, timezone
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy.orm import Session

from app.agent_core.evidence_policy import document_evidence_states
from app.models.agent_run import AgentRun
from app.models.draft import Draft
from app.models.knowledge_base import KnowledgeChunk, KnowledgeDocument
from app.services.review_lifecycle import draft_publication_texts, draft_snapshot_hash, validate_review_evidence
from app.services.workflow_support import WorkflowConflict


DEFAULT_AIGC_NOTICE = "本文含 AI 辅助生成内容，已完成人工审核；发布时请使用目标平台的 AI 内容标识功能。"
PUBLISH_CHECKLIST = [
    "核对品牌、目标渠道、标题、正文与图片是否为本次已审核版本。",
    "核对引用事实、资料时效、素材使用权限和不允许的宣传承诺。",
    "在目标平台主动声明 AI 使用情况并使用相应标识功能。",
    "由有权限的人员进入官方后台完成发布；本系统不会自动发布。",
    "发布后记录实际链接、发布时间和平台结果；下载交付包不等于发布成功。",
]
_PROFILE_FIELDS = ("id", "name", "audience", "tone", "prohibited_claims", "call_to_action", "version")
_WORKFLOW_FIELDS = ("key", "name", "description", "required_materials")
_POLICY_FIELDS = ("facts_from_knowledge_only", "human_review_required", "automatic_publish")
_CITATION = re.compile(r"\[chunk:(\d+)\]")
_PUBLIC_QUERY_FIELDS = {"id", "p", "page", "post", "article", "article_id", "doc", "doc_id", "version", "v",
                        "__biz", "mid", "idx", "sn", "chksm"}


def _scalar_fields(value: object, fields: tuple[str, ...]) -> dict:
    if not isinstance(value, dict):
        return {}
    # No recursive unknown objects, account data or provider configuration.
    return {key: value[key] for key in fields
            if key in value and (value[key] is None or isinstance(value[key], (str, int, float, bool)))}


def _business_brief(raw: object) -> dict | None:
    if not isinstance(raw, dict):
        return None
    workflow = _scalar_fields(raw.get("workflow"), _WORKFLOW_FIELDS)
    materials = (raw.get("workflow") or {}).get("required_materials") if isinstance(raw.get("workflow"), dict) else None
    if isinstance(materials, list):
        workflow["required_materials"] = [item for item in materials if isinstance(item, str)]
    policy = raw.get("policy")
    return {
        "profile": _scalar_fields(raw.get("profile"), _PROFILE_FIELDS) if isinstance(raw.get("profile"), dict) else None,
        "workflow": workflow,
        "delivery": raw.get("delivery") if isinstance(raw.get("delivery"), str) else "图文交付包",
        "policy": {key: policy[key] for key in _POLICY_FIELDS
                   if isinstance(policy, dict) and isinstance(policy.get(key), bool)},
    }


def _public_source_uri(raw: object) -> str:
    """Keep source attribution without credentials, query tokens or local paths."""
    if not isinstance(raw, str):
        return ""
    try:
        parsed = urlsplit(raw.strip())
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username is not None or parsed.password is not None):
            return ""
        if any(character in raw for character in "\r\n\t"):
            return ""
        _ = parsed.port  # Validate malformed ports without requesting the URL.
        # Article identity may live in query parameters (including WeChat).
        # Preserve a narrow public-identifier allowlist, never auth/tracking data.
        query = urlencode([(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True)
                           if key.casefold() in _PUBLIC_QUERY_FIELDS and "@" not in value
                           and not any(character in value for character in "\r\n\t")])
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, query, ""))
    except ValueError:
        return ""


def _public_locator(raw: object) -> str:
    """Export a human page/section locator, not a local path or credential URL."""
    if not isinstance(raw, str):
        return ""
    value = raw.strip()
    if (not value or any(ord(character) < 32 for character in value)
            or re.search(r"[a-z][a-z0-9+.-]*://|www\.", value, flags=re.I)
            or re.search(r"(?:^|[\s(（:：])(?:~?/|\.\.?[\\/]|[a-z]:[\\/]|\\\\)", value, flags=re.I)
            or re.search(r"/(?:Users|home|private|Volumes|etc)/", value)
            or re.search(r"\S+@\S+", value)
            or re.search(r"(?:token|api[-_ ]?key|secret|password|authorization|cookie)\s*[:=]", value, flags=re.I)):
        return ""
    return value


def _hashtags(raw: object) -> list[str]:
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, str)]
    if isinstance(raw, dict):
        values = raw.get("items") or raw.get("hashtags") or []
        return [item for item in values if isinstance(item, str)] if isinstance(values, list) else []
    return []


def _draft_title(draft: Draft) -> str:
    if draft.selected_title:
        return draft.selected_title
    options = draft.title_options
    if isinstance(options, list):
        for option in options:
            if isinstance(option, str) and option.strip():
                return option
            if isinstance(option, dict) and isinstance(option.get("title"), str):
                return option["title"]
    elif isinstance(options, dict):
        for value in options.values():
            if isinstance(value, str) and value.strip():
                return value
    return "内容交付稿"


def _markdown(payload: dict) -> str:
    brief = payload["brand_brief"] or {}
    profile = brief.get("profile") or {}
    workflow = brief.get("workflow") or {}
    lines = ["# 已审核内容交付单", "", "交付方式：下载后人工发布；本文件不表示内容已在平台发布。", "",
             "## 任务与品牌快照", "", f"- 任务编号：{payload['run_id']}", f"- 草稿编号：{payload['draft_id']}",
             f"- 品牌：{profile.get('name') or '未绑定品牌'}", f"- 品牌快照版本：{profile.get('version') or '未记录'}",
             f"- 工作流模板：{workflow.get('name') or workflow.get('key') or '通用内容流程'}", "",
             "## 标题", "", payload["title"], "", "## 正文", "", payload["body_text"], "",
             "## 话题", "", " ".join(payload["hashtags"]) or "未设置", "", "## 引用来源", ""]
    lines += [f"资料元数据核验时间（UTC）：{payload['evidence_checked_at']}。以下版本、核验状态和有效期来自交付时的当前资料记录，并非生成时元数据快照。", ""]
    if payload["citations"]:
        for item in payload["citations"]:
            lines.append(f"- {item['marker']} {item['title']}：{item['source_uri'] or '内部资料（来源地址未对外导出）'}")
            lines.append(f"  - 资料版本：{item['version_label'] or '未登记'}；文档编号：{item['document_id']}；片段编号：{item['chunk_id']}")
            lines.append(f"  - 资料内容 SHA-256：{item['document_content_hash'] or '未记录'}")
            state = "人工核验有效" if item["verification_status"] == "verified" else "未登记人工核验"
            lines.append(f"  - 核验状态：{state}；核验时间（UTC）：{item['verified_at'] or '未登记'}；有效期至（UTC）：{item['expires_at'] or '未登记'}")
            if item["locators"]:
                lines.append("  - 原文定位：" + "；".join(item["locators"]))
    else:
        lines.append("此稿未包含可导出的知识库引用标记，请按内容和使用场景核对事实及来源。")
    lines += ["", "## AI 辅助说明", "", payload["aigc_notice"], "", "## 审核记录", "",
              f"- 审核时间（UTC）：{payload['review']['at']}",
              f"- 内容快照 SHA-256：{payload['review']['content_hash']}",
              "- 审核状态：生成此交付单时，已审核版本仍匹配，引用有效性检查通过。", "",
              "## 人工发布检查", ""]
    lines.extend(f"- [ ] {item}" for item in payload["publish_checklist"])
    return "\n".join(lines) + "\n"


def build_delivery(run_id: int, db: Session) -> dict | None:
    run = db.get(AgentRun, run_id)
    if run is None:
        return None
    if run.status != "approved":
        raise WorkflowConflict("任务尚未通过人工审核，暂不能生成正式交付单。")
    draft = db.get(Draft, run.draft_id) if run.draft_id else None
    if draft is None:
        raise WorkflowConflict("已审核草稿不存在，请恢复草稿并重新审核后交付。")
    data = run.result_json or {}
    review = data.get("review") or {}
    if (not isinstance(review, dict) or review.get("decision") != "approve" or not review.get("at")
            or not review.get("content_hash") or review["content_hash"] != draft_snapshot_hash(draft, db)):
        raise WorkflowConflict("草稿或卡片已变更，当前内容与已审核版本不一致，请重新审核后交付。")
    validate_review_evidence(run, draft, db)

    cited_text = "\n".join(draft_publication_texts(draft, db))
    used_ids = {int(value) for value in _CITATION.findall(cited_text)}
    chunks = {item.id: item for item in db.query(KnowledgeChunk).filter(KnowledgeChunk.id.in_(used_ids)).all()}
    document_ids = {item.document_id for item in chunks.values()}
    documents = {item.id: item for item in db.query(KnowledgeDocument).filter(KnowledgeDocument.id.in_(document_ids)).all()}
    states = document_evidence_states(db, documents.values())
    citations, seen = [], set()
    for item in data.get("citations") or []:
        if not isinstance(item, dict) or item.get("chunk_id") not in used_ids or item.get("chunk_id") in seen:
            continue
        seen.add(item["chunk_id"])
        chunk = chunks.get(item["chunk_id"])
        document = documents.get(chunk.document_id) if chunk else None
        evidence = (states.get(document.id, {}).get("evidence") or {}) if document else {}
        locators = list(dict.fromkeys(locator for source in evidence.get("citations") or []
                                     if isinstance(source, dict) and isinstance(source.get("excerpt"), str)
                                     and source["excerpt"] and chunk and source["excerpt"] in chunk.content
                                     if (locator := _public_locator(source.get("locator")))))
        citations.append({"title": item.get("title") if isinstance(item.get("title"), str) else "引用资料",
                          "source_uri": _public_source_uri(item.get("source_uri")),
                          "marker": f"[chunk:{item['chunk_id']}]", "chunk_id": item["chunk_id"],
                          "chunk_index": chunk.chunk_index if chunk else None,
                          "document_id": document.id if document else item.get("document_id"),
                          "document_content_hash": document.content_hash if document else None,
                          "version_label": evidence.get("version_label") or None,
                          "verification_status": evidence.get("status") or "untracked",
                          "verified_at": evidence.get("verified_at"), "expires_at": evidence.get("expires_at"),
                          "locators": locators})
    payload = {
        "run_id": run.id, "draft_id": draft.id, "title": _draft_title(draft),
        "body_text": draft.body_text or "", "hashtags": _hashtags(draft.hashtags),
        "aigc_notice": draft.aigc_notice or DEFAULT_AIGC_NOTICE,
        "brand_brief": _business_brief(deepcopy(data.get("brief"))),
        "review": {"at": review["at"], "content_hash": review["content_hash"]},
        "evidence_checked_at": datetime.now(timezone.utc).isoformat(),
        "evidence_metadata_basis": "current_at_delivery",
        "citations": citations, "delivery_mode": "manual", "publish_checklist": list(PUBLISH_CHECKLIST),
    }
    payload["markdown"] = _markdown(payload)
    return payload
