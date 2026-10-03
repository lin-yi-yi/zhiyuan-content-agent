"""Regression cases from the bounded independent SaaS security review."""
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import json

import pytest

from test_saas_isolation import application, teams, invited_member, seed_legacy, business_db, CONTENT, PASSWORD


def zero_quota_online_client(identity, monkeypatch):
    """Real model adapter and logging; only upstream SDK transport is synthetic."""
    from app.llm.router import ModelRouter, router
    from app.llm.openai_compatible import OpenAICompatibleClient
    from app.saas.commerce import provision_plan
    monkeypatch.setattr(ModelRouter, "PROVIDERS", {**ModelRouter.PROVIDERS, "deepseek": {
        "api_key": "synthetic-only", "base_url": "https://provider.invalid", "model": "security-test-model"}})
    calls = []
    def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({
            "score": 70, "score_reason": "Synthetic score", "concise_summary": "Synthetic summary"})))], usage=None)
    client = OpenAICompatibleClient.__new__(OpenAICompatibleClient)
    client.provider, client.model = "deepseek", "security-test-model"
    client.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    router._clients["deepseek:security-test-model"] = client
    response = identity.client.patch("/api/saas/connections/deepseek", json={"enabled": True})
    assert response.status_code == 200, response.text
    provision_plan(identity.org, code="team", limits={"ai_requests": 0, "documents": 10, "members": 3})
    return calls


@pytest.mark.parametrize("spelling", ["+{id}", "{id}.0"])
def test_alternate_integer_spelling_does_not_grant_editor_review(application, teams, spelling):
    owner, _ = teams
    editor = invited_member(application, owner, "editor")
    created = owner.client.post("/api/evidence/notes", json={"title": "Synthetic evidence", "content": CONTENT,
        "source_url": "https://example.com/synthetic", "rights_basis": "own", "citations": [
            {"claim": "Synthetic claim", "excerpt": "Synthetic excerpt", "source_url": "https://example.com/synthetic"}]})
    assert created.status_code == 201, created.text
    note_id = created.json()["id"]
    encoded = spelling.format(id=note_id)
    result = editor.client.post(f"/api/evidence/notes/{encoded}/review",
                                json={"decision": "verify", "confirmed_sources": True})
    assert result.status_code == 403, result.text
    assert owner.client.get(f"/api/evidence/notes/{note_id}").json()["review_status"] == "pending"


@pytest.mark.parametrize("spelling", ["+{id}", "{id}.0"])
def test_alternate_integer_spelling_still_reserves_model_quota(teams, monkeypatch, spelling):
    owner, _ = teams
    from app.models.topic import Topic
    with business_db(owner) as db:
        topic = Topic(title="Synthetic quota target")
        db.add(topic)
        db.commit()
        topic_id = topic.id
    calls = zero_quota_online_client(owner, monkeypatch)
    encoded = spelling.format(id=topic_id)
    response = owner.client.post(f"/api/topics/{encoded}/score", json={"provider": "deepseek"})
    assert response.status_code == 429, response.text
    assert calls == []
    assert owner.client.get("/api/models/runs").json()["runs"] == []


@pytest.mark.parametrize("value", [1, "true"])
@pytest.mark.parametrize("path", ["/api/topics/custom-ideas/confirm", "/api/topics/import-url/confirm",
                                   "/api/topics/import-url", "/api/sources/{id}/topic-ideas/confirm"])
def test_coerced_auto_score_cannot_bypass_quota(teams, monkeypatch, path, value):
    owner, _ = teams
    source_id = seed_legacy(owner, "AUTOSCORE")["source"]
    calls = zero_quota_online_client(owner, monkeypatch)
    response = owner.client.post(path.format(id=source_id), json={"title": "Synthetic topic",
        "content_angle": "Synthetic", "target_audience": "Synthetic", "summary": CONTENT,
        "reason": "Synthetic", "risk_tip": "Synthetic", "url": "https://example.com/synthetic",
        "source_type": "manual", "topic_title": "Synthetic", "auto_score": value, "provider": "deepseek"})
    assert response.status_code == 422, response.text
    assert calls == []
    assert owner.client.get("/api/models/runs").json()["runs"] == []


def test_editor_cannot_restore_old_approval_by_echoing_status(application, teams):
    owner, _ = teams
    draft_id = seed_legacy(owner, "OLD-APPROVAL")["draft"]
    editor = invited_member(application, owner, "editor")
    approved = owner.client.put(f"/api/drafts/{draft_id}", json={"status": "approved"})
    assert approved.status_code == 200, approved.text
    response = editor.client.put(f"/api/drafts/{draft_id}", json={
        "body_text": "Changed synthetic content that has never been approved", "status": "approved"})
    assert response.status_code in {200, 403}, response.text
    saved = owner.client.get(f"/api/drafts/{draft_id}").json()
    assert not (saved["body_text"].startswith("Changed synthetic") and saved["status"] == "approved"), saved


def test_owned_organization_last_slot_is_atomic(teams, monkeypatch):
    from app.saas.models import Membership, Organization
    from app.saas.store import session_factory
    owner, _ = teams
    monkeypatch.setenv("SAAS_MAX_OWNED_ORGANIZATIONS", "2")
    barrier = Barrier(2)
    def create(index):
        barrier.wait()
        return owner.client.post("/api/saas/organizations", json={"name": f"Synthetic concurrent {index}"})
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(create, range(2)))
    assert sorted(response.status_code for response in responses) == [201, 429]
    with session_factory() as db:
        assert db.query(Membership).filter_by(user_id=owner.user, role="owner", is_active=True).count() == 2
        assert db.query(Organization).count() == 3


def test_instance_last_slot_is_atomic_across_different_owners(teams, monkeypatch):
    from app.saas.models import Organization
    from app.saas.store import session_factory
    monkeypatch.setenv("SAAS_MAX_ORGANIZATIONS", "3")
    barrier = Barrier(2)
    def create(identity):
        barrier.wait()
        return identity.client.post("/api/saas/organizations", json={"name": "Synthetic final instance slot"})
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(create, teams))
    assert sorted(response.status_code for response in responses) == [201, 429]
    with session_factory() as db:
        assert db.query(Organization).count() == 3
    # Hitting admission capacity does not reject existing organizations' business access.
    for identity in teams:
        assert identity.client.get("/api/sources").status_code == 200


def test_registration_obeys_instance_last_slot_without_orphan_accounts(application, teams, monkeypatch):
    from app.saas.models import Organization, User
    from app.saas.store import session_factory
    monkeypatch.setenv("SAAS_MAX_ORGANIZATIONS", "3")
    clients = [application.new_client(), application.new_client()]
    barrier = Barrier(2)
    def register(index):
        barrier.wait()
        return clients[index].post("/api/saas/auth/register", json={"name": "Synthetic admission",
            "email": f"admission-{index}@example.invalid", "password": PASSWORD, "organization_name": "Synthetic organization"})
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(register, range(2)))
    assert sorted(response.status_code for response in responses) == [201, 429]
    with session_factory() as db:
        assert db.query(Organization).count() == 3
        assert db.query(User).count() == 3


def test_invitation_join_does_not_consume_owned_or_instance_organization_capacity(teams, monkeypatch):
    from app.saas.models import Membership, Organization
    from app.saas.store import session_factory
    owner, invited = teams
    monkeypatch.setenv("SAAS_MAX_ORGANIZATIONS", "2")
    monkeypatch.setenv("SAAS_MAX_OWNED_ORGANIZATIONS", "1")
    created = owner.client.post("/api/saas/invites", json={"email": invited.email, "role": "viewer"})
    assert created.status_code == 201, created.text
    result = invited.client.post("/api/saas/invites/accept", json={"token": created.json()["token"]})
    assert result.status_code == 200, result.text
    with session_factory() as db:
        assert db.query(Organization).count() == 2
        assert db.query(Membership).filter_by(user_id=invited.user, is_active=True).count() == 2
        assert db.query(Membership).filter_by(user_id=invited.user, role="owner", is_active=True).count() == 1
    members = owner.client.get("/api/saas/members").json()["items"]
    membership = next(row for row in members if row["user_id"] == invited.user)
    # An owner grant cannot circumvent the same per-user ownership limit.
    promotion = owner.client.patch(f"/api/saas/members/{membership['id']}", json={"role": "owner"})
    assert promotion.status_code == 429, promotion.text


def test_invited_registration_still_works_when_instance_is_full(application, teams, monkeypatch):
    from app.saas.models import Organization, User, Membership
    from app.saas.store import session_factory
    owner, _ = teams
    monkeypatch.setenv("SAAS_MAX_ORGANIZATIONS", "2")
    monkeypatch.setenv("SAAS_MAX_OWNED_ORGANIZATIONS", "1")
    monkeypatch.setenv("SAAS_ALLOW_REGISTRATION", "false")
    created = owner.client.post("/api/saas/invites", json={"email": "invited-new@example.invalid", "role": "reviewer"})
    assert created.status_code == 201, created.text
    result = application.new_client().post("/api/saas/auth/register", json={"name": "Synthetic invitee",
        "email": "invited-new@example.invalid", "password": PASSWORD, "invite_token": created.json()["token"]})
    assert result.status_code == 201, result.text
    assert result.json()["active_organization_id"] == owner.org
    with session_factory() as db:
        assert db.query(Organization).count() == 2
        user = db.query(User).filter_by(email="invited-new@example.invalid").one()
        assert db.query(Membership).filter_by(user_id=user.id, role="reviewer", is_active=True).count() == 1
        assert db.query(Membership).filter_by(user_id=user.id, role="owner").count() == 0


@pytest.mark.parametrize(("name", "value"), [("SAAS_MAX_ORGANIZATIONS", "101"),
    ("SAAS_MAX_ORGANIZATIONS", "0"), ("SAAS_MAX_ORGANIZATIONS", "not-a-number"),
    ("SAAS_MAX_OWNED_ORGANIZATIONS", "4"), ("SAAS_MAX_OWNED_ORGANIZATIONS", "-1")])
def test_invalid_admission_configuration_fails_closed_without_new_records(teams, monkeypatch, name, value):
    from app.saas.models import Organization
    from app.saas.store import session_factory
    owner, _ = teams
    monkeypatch.setenv(name, value)
    result = owner.client.post("/api/saas/organizations", json={"name": "Must not be created"})
    assert result.status_code == 503, result.text
    with session_factory() as db:
        assert db.query(Organization).count() == 2
