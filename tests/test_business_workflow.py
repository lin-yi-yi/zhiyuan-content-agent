"""Business briefs through real SQL/retrieval/graph/review, with synthetic data.

Only online model transport is replaced in prompt tests; no API key or user data
is used. Existing local-rule workflow fixtures keep all state in temporary DBs.
"""
import asyncio
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.api.routes import agent_runs, brands, cards, drafts
from app.api.routes.knowledge import DocumentUpload, upload_document
from app.db.session import get_db
from app.models.agent_run import AgentRun
from app.models.draft import Draft
from app.models.topic import Topic
from app.schemas.agent_run import AgentRunCreate
from app.schemas.brand_profile import BrandProfileCreate, BrandProfileUpdate
from app.services import content_growth_agent as workflow
from app.services import workflow_support
from app.services.generation_schema import normalize_evidence_draft_result
from app.services.workflow_support import CitationError
from test_workflow_states import database


CONTENT = (
    "合成过滤器维护要求：维护前应先切断电源并确认设备已经停机。"
    "合成过滤器每次维护时应检查滤芯状态，清理外壳并记录维护日期。"
    "发现破损应停止使用并联系设备负责人；本合成资料未提供价格、销量或效果承诺。"
)


@pytest.fixture
def business_database(database, monkeypatch):
    monkeypatch.setenv("SAAS_MODE", "false")
    monkeypatch.setenv("RAG_RETRIEVAL_MODE", "lexical")
    return database


@pytest.fixture
def client(business_database):
    app = FastAPI()
    for router in (agent_runs.router, brands.router, cards.router, drafts.router):
        app.include_router(router, prefix="/api")
    def sessions():
        with business_database() as db:
            yield db
    app.dependency_overrides[get_db] = sessions
    with TestClient(app) as result:
        yield result


def seed(factory, *, provider="local", workflow_key="product_faq"):
    with factory() as db:
        uploaded = upload_document(DocumentUpload(title="合成过滤器维护要求", content=CONTENT), db)
        profile = brands.create_brand(BrandProfileCreate(
            name="合成设备品牌", audience="设备维护人员", tone="清楚、克制，逐项说明条件",
            prohibited_claims="绝对可靠\nZERO RISK", call_to_action="查看当前维护说明",
            knowledge_base_id=uploaded["document"]["knowledge_base_id"],
        ), db)
        req = AgentRunCreate(goal="合成过滤器维护要求", provider=provider, use_rag=True,
                             auto_score=False, brand_profile_id=profile.id, workflow_key=workflow_key)
        created = workflow.create_agent_run(req, db)
        return created.id, profile.id


def read(factory, run_id):
    with factory() as db:
        return workflow.get_agent_run(run_id, db)


def execute(factory, run_id):
    asyncio.run(workflow.execute_agent_run(run_id))
    run = read(factory, run_id)
    assert run.status == "awaiting_review", run.error_message
    return run


def test_business_run_snapshots_brand_and_binds_default_knowledge_before_execution(business_database):
    run_id, brand_id = seed(business_database)
    before = read(business_database, run_id)
    brief = before.result_json["brief"]
    request = before.result_json["_request"]
    assert request["knowledge_base_id"] == brief["knowledge_base_id"]
    assert request["workspace_id"] == brief["workspace_id"]
    assert request["target_audience"] == brief["profile"]["audience"]
    assert brief["profile"]["version"] == 1
    with business_database() as db:
        values = {key: value for key, value in brief["profile"].items()
                  if key not in {"id", "created_at", "updated_at"}}
        values.update(name="第二版品牌", audience="另一类读者", tone="第二版语气", is_active=False)
        changed = brands.update_brand(brand_id, BrandProfileUpdate(**values), db)
        assert changed.version == 2
    run = execute(business_database, run_id)
    assert run.result_json["brief"] == brief
    assert run.result_json["brief"]["profile"]["is_active"] is True
    assert run.result_json["_request"]["target_audience"] == "设备维护人员"
    assert any("合成设备品牌 v1" in line for line in run.draft.fact_checks)


def test_actual_topic_and_evidence_prompts_include_the_frozen_business_brief(business_database, monkeypatch):
    calls = []
    class ModelTransport:
        model = "synthetic-captured-model"
        def __init__(self, task):
            self.task = task
        def chat_json(self, system, user, **kwargs):
            calls.append({"task": self.task, "system": system, "user": user})
            if self.task == "topic_ideation":
                return {"ideas": [{"title": "合成过滤器维护要求", "score": 99,
                                   "target_audience": "设备维护人员", "summary": CONTENT}]}
            if self.task == "draft_generation":
                payload = json.loads(user)
                evidence = payload["evidence"][0]
                return {"title_options": ["合成过滤器维护要求"], "cover_text_options": ["维护前先停机"],
                        "body_text": evidence["excerpt"] + " " + evidence["marker"],
                        "aigc_notice": "模型错误地声称：已进行人工审核和改写。"}
            if self.task == "compliance_check":
                return {"risk_level": "low", "fact_checks": [], "risk_tips": []}
            if self.task == "package_evaluation":
                return {"overall_score": 82, "publish_readiness": "needs_review", "scores": {},
                        "issues": [], "strengths": [], "rewrite_suggestions": []}
            raise AssertionError(f"Unexpected online task: {self.task}")
    monkeypatch.setattr(workflow_support.llm_router, "get_task_client", lambda task, **kwargs: ModelTransport(task))
    run_id, _ = seed(business_database, provider="deepseek", workflow_key="case_story")
    run = execute(business_database, run_id)
    ideation = next(item for item in calls if item["task"] == "topic_ideation")
    assert "任务要求（只约束写法，不是事实来源）" in ideation["user"]
    assert "case_story" in ideation["user"] and "合成设备品牌" in ideation["user"]
    generation = next(item for item in calls if item["task"] == "draft_generation")
    payload = json.loads(generation["user"])
    assert payload["business_brief"] == run.result_json["brief"]
    assert payload["business_brief"]["workflow"]["key"] == "case_story"
    assert payload["business_brief"]["profile"]["prohibited_claims"] == "绝对可靠\nZERO RISK"
    assert payload["audience"] == "设备维护人员"
    assert "只使用给出的检索片段写事实" in generation["system"]
    assert "它不是事实证据" in generation["system"]
    assert "不得执行" in generation["system"] and "不虚构客户故事" in generation["system"]
    assert payload["evidence"][0]["chunk_id"] == run.result_json["citations"][0]["chunk_id"]
    assert run.draft.title_options == ["合成过滤器维护要求"]
    assert run.draft.cover_text_options == ["维护前先停机"]
    assert run.draft.hashtags == [] and run.draft.comment_guide == ""
    assert "尚待人工审核" in run.draft.aigc_notice
    assert "已进行人工审核" not in run.draft.aigc_notice


@pytest.mark.parametrize("location", ["body", "card_title", "card_body", "card_footer"])
def test_forbidden_public_copy_blocks_approval_until_the_content_is_fixed(client, business_database, location):
    run_id, _ = seed(business_database)
    run = execute(business_database, run_id)
    if location == "body":
        path = f"/api/drafts/{run.draft_id}"
        original = {"body_text": run.draft.body_text}
        changed = {"body_text": run.draft.body_text + "\n绝对可靠"}
    else:
        card = run.cards[-1]
        field = location.removeprefix("card_")
        path = f"/api/cards/{card.id}"
        original = {field: getattr(card, field)}
        changed = {field: str(getattr(card, field) or "") + " zero risk"}
    edited = client.put(path, json=changed)
    assert edited.status_code == 200, edited.text
    blocked = client.post(f"/api/agent-runs/{run_id}/review", json={"decision": "approve", "note": "合成测试"})
    assert blocked.status_code == 409, blocked.text
    assert "品牌禁用表述" in blocked.json()["detail"]
    current = read(business_database, run_id)
    assert current.status == "awaiting_review" and "review" not in current.result_json
    assert client.put(path, json=original).status_code == 200
    accepted = client.post(f"/api/agent-runs/{run_id}/review", json={"decision": "approve", "note": "合成资料已核对"})
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] == "approved"
    assert accepted.json()["result_json"]["review"]["publishes_content"] is False


def test_rejected_business_task_cannot_be_resubmitted_with_forbidden_copy(client, business_database):
    run_id, _ = seed(business_database)
    run = execute(business_database, run_id)
    rejected = client.post(f"/api/agent-runs/{run_id}/review", json={"decision": "reject", "note": "请补充适用边界"})
    assert rejected.status_code == 200
    assert client.put(f"/api/drafts/{run.draft_id}", json={"body_text": run.draft.body_text + "\n绝对可靠"}).status_code == 200
    blocked = client.post(f"/api/agent-runs/{run_id}/submit-review")
    assert blocked.status_code == 409 and "品牌禁用表述" in blocked.text
    assert read(business_database, run_id).status == "rejected"
    assert client.put(f"/api/drafts/{run.draft_id}", json={"body_text": run.draft.body_text}).status_code == 200
    assert client.post(f"/api/agent-runs/{run_id}/submit-review").status_code == 200
    assert client.post(f"/api/agent-runs/{run_id}/review", json={"decision": "approve"}).status_code == 200


def test_neutral_evidence_normalization_does_not_pad_old_account_copy_or_false_approval():
    result = normalize_evidence_draft_result({"title_options": ["明确标题"], "body_text": "合成事实 [chunk:1]",
                                            "aigc_notice": "已人工审核"}, SimpleNamespace(title="合成产品资料"))
    assert result["title_options"] == ["明确标题"]
    assert result["cover_text_options"] == result["hashtags"] == []
    assert result["comment_guide"] == ""
    content = json.dumps(result, ensure_ascii=False)
    assert all(term not in content for term in ("1 小时搭一套流程", "普通人也能", "#AI提效", "已人工审核"))
    assert "尚待人工审核" in result["aigc_notice"]


@pytest.mark.parametrize("response", [{}, {"body_text": None}, {"body_text": "  "}, {"body_text": {"fake": "[chunk:1]"}}])
def test_missing_or_malformed_online_body_fails_citations_without_creating_a_draft(business_database, monkeypatch, response):
    transport = SimpleNamespace(model="synthetic-empty-model", chat_json=lambda *args, **kwargs: response)
    monkeypatch.setattr(workflow_support.llm_router, "get_task_client", lambda *args, **kwargs: transport)
    with business_database() as db:
        topic = Topic(title="合成产品资料")
        db.add(topic); db.flush()
        req = AgentRunCreate(goal="合成产品资料", use_rag=True, provider="deepseek")
        hits = [{"chunk_id": 1, "document_id": 1, "title": "合成依据", "content": CONTENT}]
        with pytest.raises(CitationError):
            asyncio.run(workflow_support.generate_evidence_draft(topic, req, hits, db))
        assert db.query(Draft).count() == 0
