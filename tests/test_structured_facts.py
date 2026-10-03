"""Explicit industrial parameters: synthetic materials, lexical retrieval, no API models."""
from datetime import datetime, timedelta, timezone

import pytest

from app.agent_core import rag_service
from app.agent_core.boundaries import workspace_context
from app.agent_core.evidence_policy import document_evidence_states
from app.models.evidence_note import EvidenceNote
from app.models.knowledge_base import KnowledgeBase, KnowledgeDocument

from test_evidence_notes import BASE, client, database, note_payload, verify


def fact_payload(value="24 V", product="合成星桥 XP-24", **changes):
    excerpt = f"{product} 的额定电压为 {value}，仅适用于直流输入。"
    body = note_payload(
        title=f"{product} 合成规格 {value}",
        content=f"【项目自编合成资料，非真实产品】{excerpt} 资料不包含价格、认证或防护等级，不能推断这些参数。",
        citations=[{"claim": excerpt, "excerpt": excerpt,
                    "source_url": "https://example.com/synthetic-xp24#electrical",
                    "locator": "合成规格第 1 节：电气参数",
                    "product_model": product, "parameter": "额定电压 / 直流输入", "value": value}],
    )
    body.update(changes)
    return body


def create_fact(client, **changes):
    result = client.post(BASE, json=fact_payload(**changes))
    assert result.status_code == 201, result.text
    return result.json()


def index_fact(client, note):
    scope = {"workspace_id": note["workspace_id"], "knowledge_base_id": note["knowledge_base_id"]}
    verify(client, note, **scope)
    result = client.post(f"{BASE}/{note['id']}/index", json=scope)
    assert result.status_code == 200, result.text
    return result.json()


def answer(database):
    with database() as db:
        return rag_service.answer_question("合成星桥 XP-24 额定电压", db, workspace_context(db))


def test_structured_parameter_retains_original_locator_version_and_value(client, database):
    note = create_fact(client)
    indexed = index_fact(client, note)
    result = answer(database)
    assert not result["refused"]
    metadata = result["citations"][0]["metadata"]["evidence"]
    assert metadata["version_label"] == "synthetic-v1"
    assert metadata["citations"][0]["value"] == "24 V"
    assert metadata["citations"][0]["product_model"] == "合成星桥 XP-24"
    assert metadata["citations"][0]["locator"] == "合成规格第 1 节：电气参数"
    assert indexed["note"]["fact_conflict_note_ids"] == []


@pytest.mark.parametrize("field,value", [
    ("product_model", ""), ("parameter", ""), ("value", ""),
    ("locator", ""), ("value", "48 V"), ("excerpt", "额定电压是 24 V，但这是资料中不存在的句子。"),
])
def test_incomplete_or_unlocatable_structured_facts_are_rejected(client, field, value):
    payload = fact_payload()
    payload["citations"][0][field] = value
    result = client.post(BASE, json=payload)
    assert result.status_code == 422, result.text


def test_structured_facts_need_material_version_but_generic_notes_remain_compatible(client):
    result = client.post(BASE, json=fact_payload(version_label=""))
    assert result.status_code == 422, result.text
    generic = client.post(BASE, json=note_payload(version_label=""))
    assert generic.status_code == 201, generic.text
    verify(client, generic.json())


def test_conflicting_candidate_cannot_be_verified_or_displace_reviewed_version(client, database):
    original = create_fact(client)
    index_fact(client, original)
    proposal = create_fact(client, value="48 V", version_label="synthetic-v2")
    assert set(proposal["fact_conflict_note_ids"]) == {original["id"], proposal["id"]}
    blocked = client.post(f"{BASE}/{proposal['id']}/review", json={"decision": "verify", "confirmed_sources": True})
    assert blocked.status_code == 409 and "结构化事实冲突" in blocked.text
    assert client.get(f"{BASE}/{proposal['id']}").json()["review_status"] == "pending"
    assert not answer(database)["refused"]  # An unreviewed assertion cannot silently replace a reviewed one.
    client.post(f"{BASE}/{original['id']}/review", json={"decision": "revoke"})
    replacement = index_fact(client, proposal)
    current = answer(database)
    assert not current["refused"]
    assert {citation["document_id"] for citation in current["citations"]} == {replacement["document_id"]}
    assert all(citation["metadata"]["evidence"]["version_label"] == "synthetic-v2" for citation in current["citations"])


def test_internal_conflict_requires_revised_note_instead_of_choosing_one_value(client):
    payload = fact_payload()
    other = fact_payload(value="48 V")["citations"][0]
    payload["citations"].append(other)
    payload["content"] += other["excerpt"]
    created = client.post(BASE, json=payload)
    assert created.status_code == 201, created.text
    note = created.json()
    result = client.post(f"{BASE}/{note['id']}/review", json={"decision": "verify", "confirmed_sources": True})
    assert result.status_code == 409 and "结构化事实冲突" in result.text


def test_unit_case_does_not_silently_merge_different_values(client):
    original = create_fact(client, value="100 mA")
    index_fact(client, original)
    candidate = create_fact(client, value="100 MA")
    blocked = client.post(f"{BASE}/{candidate['id']}/review", json={"decision": "verify", "confirmed_sources": True})
    assert blocked.status_code == 409 and "结构化事实冲突" in blocked.text


@pytest.mark.parametrize("boundary", ["other_brand", "other_knowledge_base", "same_value", "expired_version"])
def test_fact_conflicts_respect_scope_brand_and_active_version(client, database, boundary):
    original = create_fact(client)
    index_fact(client, original)
    changes = {"value": "48 V", "version_label": "synthetic-v2"}
    if boundary == "other_brand":
        changes["product"] = "合成远山 XP-24"
    elif boundary == "same_value":
        changes["value"] = "24V"
    with database() as db:
        if boundary == "other_knowledge_base":
            kb = KnowledgeBase(workspace_id=original["workspace_id"], name="另一品牌资料范围", status="active")
            db.add(kb)
            db.commit()
            changes.update(workspace_id=original["workspace_id"], knowledge_base_id=kb.id)
        elif boundary == "expired_version":
            note = db.get(EvidenceNote, original["id"])
            note.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            db.commit()
    replacement = create_fact(client, **changes)
    assert index_fact(client, replacement)["note"]["index_status"] == "indexed"


def test_live_retrieval_excludes_conflicts_even_outside_candidate_documents(client, database):
    original = create_fact(client)
    indexed = index_fact(client, original)
    proposal = create_fact(client, value="48 V")
    with database() as db:
        # Simulate historical/imported bad state; the public review endpoint
        # rejects this transition. The second note has no indexed document.
        altered = db.get(EvidenceNote, proposal["id"])
        altered.review_status = "verified"
        altered.verified_at = datetime.now(timezone.utc)
        db.commit()
        document = db.get(KnowledgeDocument, indexed["document_id"])
        state = document_evidence_states(db, [document])[document.id]
        assert not state["eligible"] and state["reason"] == "conflict"
    result = answer(database)
    assert result["refused"] and result["citations"] == []
    assert result["evidence_health"]["conflict_count"] == 1
    assert any("结构化事实冲突" in warning for warning in result["evidence_health"]["warnings"])
    detail = client.get(f"{BASE}/{original['id']}").json()
    assert detail["index_status"] == "stale" and not detail["indexable"]
    blocked = client.post(f"{BASE}/{proposal['id']}/index", json={})
    assert blocked.status_code == 409, blocked.text


def test_content_tampering_cannot_retain_review_by_reusing_old_hash(client, database):
    note = create_fact(client)
    index_fact(client, note)
    with database() as db:
        altered = db.get(EvidenceNote, note["id"])
        altered.content = altered.content.replace("24 V", "48 V")
        db.commit()
    assert answer(database)["refused"]
