"""Manual clarification directory: live SQL evidence, no intent/model inference."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json

import pytest

from app.agent_core import rag_service
from app.api.routes.knowledge import DocumentUpload, upload_document
from app.models.evidence_note import EvidenceNote, fact_text
from app.models.knowledge_base import KnowledgeBase, KnowledgeChunk, KnowledgeDocument
from app.models.workspace import Workspace

from test_required_facts import client, database, query, VOLTAGE, PRICE
from test_saas_isolation import application, teams
from test_structured_facts import create_fact, fact_payload, index_fact


PATH = "/api/v04/rag/fact-catalog"


def catalog(client, **params):
    response = client.get(PATH, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def custom_fact(client, product, parameter, **changes):
    payload = fact_payload(product=product)
    payload["citations"][0]["parameter"] = parameter
    payload.update(changes)
    response = client.post("/api/evidence/notes", json=payload)
    assert response.status_code == 201, response.text
    note = response.json()
    indexed = index_fact(client, note)
    return note, indexed


def test_catalog_empty_and_no_model_embedding_or_retrieval_config(client, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("catalog must not call a model, vector search or retrieval configuration")
    monkeypatch.setattr(rag_service.llm_router, "get_task_client", forbidden)
    monkeypatch.setattr(rag_service, "embed_texts", forbidden)
    monkeypatch.setattr(rag_service, "retrieval_config", forbidden)
    monkeypatch.setattr(rag_service.vector_store, "search_vectors", forbidden)
    result = catalog(client)
    assert result["items"] == [] and result["total"] == 0 and result["has_more"] is False
    assert result["offset"] == 0 and result["limit"] == 30
    assert result["requirements_complete"] is False
    assert result["suggestion_method"] == "reviewed_evidence_metadata"


def test_catalog_contains_labels_and_provenance_but_not_values_or_full_text(client, monkeypatch):
    note = create_fact(client)
    indexed = index_fact(client, note)
    monkeypatch.setenv("RAG_RETRIEVAL_MODE", "invalid-unavailable-embedding-config")
    monkeypatch.setattr(rag_service.llm_router, "get_task_client", lambda *a, **kw: pytest.fail("no model"))
    result = catalog(client)
    assert result["workspace_id"] == note["workspace_id"]
    assert result["knowledge_base_id"] == note["knowledge_base_id"]
    item = result["items"][0]
    assert set(item) == {"product_model", "parameter", "evidence"}
    assert {key: item[key] for key in VOLTAGE} == VOLTAGE
    source = item["evidence"][0]
    assert set(source) == {"document_id", "chunk_id", "source_url", "locator", "version_label"}
    assert source["document_id"] == indexed["document_id"] and source["chunk_id"] > 0
    assert source["source_url"] == note["citations"][0]["source_url"]
    assert source["version_label"] == "synthetic-v1"
    encoded = json.dumps(result, ensure_ascii=False)
    assert "24 V" not in encoded and note["content"] not in encoded and '"claim"' not in encoded


def test_stable_normalized_dedup_sort_and_pagination_preserve_all_sources(client):
    original, _ = custom_fact(client, "合成星桥 XP-24", "额定电压 / 直流输入")
    custom_fact(client, "合成星桥 ＸＰ－２４", "额定电压/直流输入", version_label="synthetic-v2")
    custom_fact(client, "合成 A-1", "工作电流 mA")
    custom_fact(client, "合成 A-1", "工作电流 MA")
    full = catalog(client)
    assert full["total"] == 3
    keys = [(fact_text(item["product_model"]), fact_text(item["parameter"])) for item in full["items"]]
    assert keys == sorted(keys) and keys[0] != keys[1]  # Unit/label case stays significant.
    duplicate = next(item for item in full["items"] if item["product_model"] == original["citations"][0]["product_model"])
    assert len(duplicate["evidence"]) == 2
    assert {source["version_label"] for source in duplicate["evidence"]} == {"synthetic-v1", "synthetic-v2"}
    assert catalog(client) == full
    pages = [catalog(client, offset=offset, limit=1) for offset in range(4)]
    assert [page["has_more"] for page in pages] == [True, True, False, False]
    assert all(page["total"] == 3 and page["limit"] == 1 for page in pages)
    assert [page["items"][0] for page in pages[:3]] == full["items"]
    assert pages[3]["items"] == []


@pytest.mark.parametrize("params", [
    {"offset": -1}, {"limit": 0}, {"limit": 51}, {"offset": "bad"},
    {"workspace_id": 0}, {"knowledge_base_id": -1},
])
def test_catalog_rejects_invalid_pagination_and_scope(client, params):
    assert client.get(PATH, params=params).status_code == 422


def test_workspace_and_knowledge_base_scope(client, database):
    first = create_fact(client)
    index_fact(client, first)
    with database() as db:
        second_kb = KnowledgeBase(workspace_id=first["workspace_id"], name="合成另一库", status="active")
        other_ws = Workspace(name="合成另一工作区", slug="synthetic-other", is_default=False)
        db.add_all([second_kb, other_ws])
        db.flush()
        other_kb = KnowledgeBase(workspace_id=other_ws.id, name="合成隔离库", status="active")
        db.add(other_kb)
        db.commit()
        second_kb_id, other_ws_id, other_kb_id = second_kb.id, other_ws.id, other_kb.id
    custom_fact(client, "合成第二库 S-2", "额定电压", workspace_id=first["workspace_id"], knowledge_base_id=second_kb_id)
    custom_fact(client, "合成隔离 W-3", "额定电压", workspace_id=other_ws_id, knowledge_base_id=other_kb_id)
    assert catalog(client)["items"][0]["product_model"] == VOLTAGE["product_model"]
    second = catalog(client, workspace_id=first["workspace_id"], knowledge_base_id=second_kb_id)
    assert second["total"] == 1 and second["items"][0]["product_model"] == "合成第二库 S-2"
    other = catalog(client, workspace_id=other_ws_id, knowledge_base_id=other_kb_id)
    assert other["total"] == 1 and other["items"][0]["product_model"] == "合成隔离 W-3"
    for params in ({"workspace_id": first["workspace_id"], "knowledge_base_id": other_kb_id},
                   {"workspace_id": 99999}, {"knowledge_base_id": 99999}):
        assert client.get(PATH, params=params).status_code == 422


@pytest.mark.parametrize("mutation", [
    "expired", "revoked", "pending", "not_indexed", "document_unindexed", "note_content_changed",
    "document_hash_changed", "chunk_content_changed", "chunk_deleted", "chunk_scope_changed",
    "document_metadata_list", "evidence_metadata_list", "wrong_note_link",
])
def test_live_catalog_removes_stale_or_unreviewed_material(client, database, mutation):
    note = create_fact(client)
    indexed = index_fact(client, note)
    assert catalog(client)["total"] == 1
    with database() as db:
        stored = db.get(EvidenceNote, note["id"])
        document = db.get(KnowledgeDocument, indexed["document_id"])
        chunk = db.query(KnowledgeChunk).filter_by(document_id=document.id).first()
        if mutation == "expired":
            stored.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        elif mutation == "revoked":
            stored.review_status = "revoked"
        elif mutation == "pending":
            stored.review_status = "pending"
        elif mutation == "not_indexed":
            stored.index_state = "not_indexed"
        elif mutation == "document_unindexed":
            document.status = "pending"
        elif mutation == "note_content_changed":
            stored.content += "正文已修改但没有重新核验。"
        elif mutation == "document_hash_changed":
            document.content_hash = "0" * 64
        elif mutation == "chunk_content_changed":
            chunk.content += "索引已改变但未重建。"
        elif mutation == "chunk_deleted":
            db.delete(chunk)
        elif mutation == "chunk_scope_changed":
            chunk.workspace_id += 1000
        elif mutation == "document_metadata_list":
            document.metadata_json = []
        elif mutation == "evidence_metadata_list":
            document.metadata_json = {**document.metadata_json, "evidence": ["bad"]}
        else:
            document.metadata_json = {**document.metadata_json, "evidence": {"note_id": stored.id + 1}}
        db.commit()
    assert catalog(client)["items"] == []


@pytest.mark.parametrize("mutation", ["bad_url", "missing_locator", "missing_claim", "non_string_parameter", "long_parameter", "wrong_value", "bad_citation"])
def test_bad_citation_metadata_never_produces_catalog_option(client, database, mutation):
    note = create_fact(client)
    index_fact(client, note)
    with database() as db:
        stored = db.get(EvidenceNote, note["id"])
        citations = deepcopy(stored.citations)
        if mutation == "bad_citation":
            citations = ["not a citation"]
        else:
            field, value = {"bad_url": ("source_url", "http://127.0.0.1/private"),
                            "missing_locator": ("locator", ""), "missing_claim": ("claim", ""),
                            "non_string_parameter": ("parameter", 42), "long_parameter": ("parameter", "参" * 201),
                            "wrong_value": ("value", "999 V")}[mutation]
            citations[0][field] = value
        stored.citations = citations
        db.commit()
    assert catalog(client)["total"] == 0


def test_unreadable_historical_citations_fail_closed_without_exposing_payload(client, database):
    note = create_fact(client)
    index_fact(client, note)
    with database() as db:
        db.get(EvidenceNote, note["id"]).citations = 42
        db.commit()
    response = client.get(PATH)
    assert response.status_code == 422
    assert response.json() == {"detail": "资料结构无效，暂时无法提供参数目录，请检查核验笔记。"}


def test_repeated_citation_is_one_source_and_stale_chunk_metadata_is_not_authority(client, database):
    note = create_fact(client)
    indexed = index_fact(client, note)
    with database() as db:
        stored = db.get(EvidenceNote, note["id"])
        stored.citations = [*stored.citations, deepcopy(stored.citations[0])]
        chunk = db.query(KnowledgeChunk).filter_by(document_id=indexed["document_id"]).first()
        chunk.metadata_json = {"evidence": {"status": "verified", "citations": [
            {**stored.citations[0], "product_model": "伪造跨型号", "parameter": "伪造参数"}]}}
        db.commit()
    result = catalog(client)
    assert result["total"] == 1 and result["items"][0]["product_model"] == VOLTAGE["product_model"]
    assert len(result["items"][0]["evidence"]) == 1


def test_same_chunk_keeps_distinct_locators_while_answer_uses_first_match(client):
    payload = fact_payload()
    first = payload["citations"][0]
    second = {**first, "locator": "合成规格附录 A：同一参数复述",
              "source_url": "https://example.com/synthetic-xp24#appendix-a"}
    payload["citations"] = [first, second]
    created = client.post("/api/evidence/notes", json=payload)
    assert created.status_code == 201, created.text
    index_fact(client, created.json())
    item = catalog(client)["items"][0]
    assert len(item["evidence"]) == 2
    assert item["evidence"][0]["chunk_id"] == item["evidence"][1]["chunk_id"]
    assert [source["locator"] for source in item["evidence"]] == [first["locator"], second["locator"]]
    assert query(client, [VOLTAGE])["matched_facts"][0]["locator"] == first["locator"]


def test_metadata_cannot_attribute_fact_to_chunk_without_excerpt(client, database):
    note = create_fact(client)
    indexed = index_fact(client, note)
    with database() as db:
        chunk = db.query(KnowledgeChunk).filter_by(document_id=indexed["document_id"]).first()
        chunk.content = "合成星桥 XP-24 的另一段资料没有提供任何电压参数。"
        chunk.embedding_hash = hashlib.sha256(chunk.content.encode()).hexdigest()
        db.commit()
    assert catalog(client)["total"] == 0


def test_conflicting_reviewed_peer_outside_index_is_excluded_live(client, database):
    original = create_fact(client)
    index_fact(client, original)
    conflict = create_fact(client, value="48 V")
    assert catalog(client)["total"] == 1  # A pending proposal has no authority.
    with database() as db:
        peer = db.get(EvidenceNote, conflict["id"])
        peer.review_status = "verified"
        peer.verified_at = datetime.now(timezone.utc)
        db.commit()
    assert catalog(client)["total"] == 0
    with database() as db:
        db.get(EvidenceNote, conflict["id"]).review_status = "revoked"
        db.commit()
    assert catalog(client)["total"] == 1


def test_plain_upload_and_unindexed_note_are_not_catalog_material(client, database):
    create_fact(client)
    with database() as db:
        upload_document(DocumentUpload(title="合成未核验材料", content=fact_payload()["content"]), db)
    assert catalog(client)["total"] == 0


def test_directory_does_not_confirm_intent_or_change_general_or_explicit_answers(client):
    index_fact(client, create_fact(client))
    assert catalog(client)["requirements_complete"] is False
    general = query(client, query="合成星桥 XP-24 额定电压和售价是多少？")
    assert not general["refused"] and general["answerability"] == "not_assessed"
    confirmed = query(client, [VOLTAGE, PRICE])
    assert confirmed["refused"] and confirmed["missing_facts"] == [PRICE]
    assert not query(client, [VOLTAGE])["refused"]


def test_catalog_uses_authenticated_tenant_store_with_colliding_numeric_ids(application, teams, monkeypatch):
    a, b = teams
    a_note = create_fact(a.client, product="合成组织 A")
    b_note = create_fact(b.client, product="合成组织 B")
    index_fact(a.client, a_note)
    index_fact(b.client, b_note)
    assert a_note["id"] == b_note["id"] and a_note["knowledge_base_id"] == b_note["knowledge_base_id"]
    monkeypatch.setattr(rag_service, "embed_texts", lambda *a, **kw: pytest.fail("no embeddings"))
    monkeypatch.setattr(rag_service.llm_router, "get_task_client", lambda *a, **kw: pytest.fail("no models"))
    assert catalog(a.client)["items"][0]["product_model"] == "合成组织 A"
    assert catalog(b.client)["items"][0]["product_model"] == "合成组织 B"
    assert a.client.get(PATH, headers={"X-Organization-ID": b.org}).status_code == 403
    assert application.anonymous.get(PATH).status_code == 401
