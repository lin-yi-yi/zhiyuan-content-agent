"""Retry preserves frozen brand/template input and its review/export constraints.

Synthetic SQLite and local rules only; no real customer records or model APIs.
"""
import asyncio
from copy import deepcopy

import pytest

from app.api.routes import brands
from app.models.agent_run import AgentRun
from app.models.card import Card
from app.models.draft import Draft
from app.schemas.agent_run import AgentReviewCreate
from app.schemas.brand_profile import BrandProfileUpdate
from app.services import content_growth_agent as workflow
from app.services.delivery import build_delivery
from app.services.workflow_support import WorkflowConflict
from test_business_workflow import business_database, read, seed
from test_workflow_states import database


@pytest.mark.parametrize("failed_step", ["topic_ideas", "generate_cards"])
def test_retry_keeps_original_brand_template_and_bans_through_review_and_delivery(business_database, monkeypatch, failed_step):
    run_id, brand_id = seed(business_database)
    initial = read(business_database, run_id)
    original_brief = deepcopy(initial.result_json["brief"])
    original_request = deepcopy(initial.result_json["_request"])
    original_handler = getattr(workflow, f"_step_{failed_step}")

    async def fail(*args, **kwargs):
        raise RuntimeError("synthetic failure while testing frozen retry input")

    monkeypatch.setattr(workflow, f"_step_{failed_step}", fail)
    asyncio.run(workflow.execute_agent_run(run_id))
    failed = read(business_database, run_id)
    assert failed.status == "failed"
    assert failed.result_json["failure"]["step"] == failed_step
    assert failed.result_json["brief"] == original_brief

    # Changing the live brand after failure must not silently rewrite the task.
    with business_database() as db:
        fields = {key: value for key, value in original_brief["profile"].items()
                  if key not in {"id", "created_at", "updated_at"}}
        fields.update(name="后来修改的品牌", tone="新的语气", prohibited_claims="另一个新禁用词")
        changed = brands.update_brand(brand_id, BrandProfileUpdate(**fields), db)
        assert changed.version == original_brief["profile"]["version"] + 1
        workflow.prepare_retry_agent_run(run_id, db)
    retried = read(business_database, run_id)
    assert retried.result_json["brief"] == original_brief
    assert retried.result_json["_request"] == original_request
    assert retried.result_json["brief"]["workflow"]["key"] == "product_faq"
    assert retried.result_json["workflow"]["attempt"] == 2

    monkeypatch.setattr(workflow, f"_step_{failed_step}", original_handler)
    asyncio.run(workflow.execute_agent_run(run_id))
    resumed = read(business_database, run_id)
    assert resumed.status == "awaiting_review", resumed.error_message
    assert resumed.result_json["brief"] == original_brief

    with business_database() as db:
        run = db.get(AgentRun, run_id)
        draft = db.get(Draft, run.draft_id)
        original_body = draft.body_text
        draft.body_text = original_body + "\n绝对可靠"
        db.commit()
        with pytest.raises(WorkflowConflict, match="品牌禁用表述"):
            workflow.review_agent_run(run_id, AgentReviewCreate(decision="approve"), db)
        db.rollback()
        db.refresh(draft)
        draft.body_text = original_body
        card = db.query(Card).filter(Card.draft_id == draft.id).order_by(Card.id).first()
        original_title = card.title
        card.title = "ZERO RISK"
        db.commit()
        with pytest.raises(WorkflowConflict, match="品牌禁用表述"):
            workflow.review_agent_run(run_id, AgentReviewCreate(decision="approve"), db)
        db.rollback()
        db.refresh(card)
        card.title = original_title
        db.commit()
        approved = workflow.review_agent_run(run_id, AgentReviewCreate(decision="approve"), db)
        assert approved.status == "approved"
        payload = build_delivery(run_id, db)
        assert payload["brand_brief"]["profile"]["name"] == original_brief["profile"]["name"]
        assert payload["brand_brief"]["profile"]["version"] == original_brief["profile"]["version"]
        assert payload["brand_brief"]["workflow"]["key"] == "product_faq"
        assert payload["brand_brief"]["policy"] == original_brief["policy"]
