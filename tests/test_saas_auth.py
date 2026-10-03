"""SaaS account, session and organization security contracts on temporary SQLite."""
from contextlib import ExitStack
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
from types import SimpleNamespace
from threading import Barrier
import sys

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
import pytest


BASE = "/api/saas"
AUTH = f"{BASE}/auth"
PASSWORD = "Synthetic-Account-Only-90210"


@pytest.fixture
def saas(monkeypatch, tmp_path):
    monkeypatch.setenv("SAAS_MODE", "true")
    monkeypatch.setenv("SAAS_DATA_DIR", str(tmp_path / "saas-control"))
    monkeypatch.setenv("SAAS_SECURE_COOKIE", "false")
    monkeypatch.setenv("SAAS_ALLOW_REGISTRATION", "true")
    from app.saas import auth, models, routes, store

    production_scrypt_n = auth.SCRYPT_N
    monkeypatch.setattr(auth, "SCRYPT_N", 2 ** 12)
    store.close_control_db()
    auth.reset_rate_limits()
    store.init_control_db()
    application = FastAPI()
    application.include_router(routes.router)
    with ExitStack() as clients:
        def new_client():
            return clients.enter_context(TestClient(application))

        yield SimpleNamespace(
            app=application, client=new_client(), new_client=new_client,
            auth=auth, models=models, store=store, production_scrypt_n=production_scrypt_n,
        )
    auth.reset_rate_limits()
    store.close_control_db()


def registration_body(email="owner@example.com", **changes):
    body = {
        "name": "合成账户",
        "email": email,
        "password": PASSWORD,
        "organization_name": "合成测试组织",
    }
    body.update(changes)
    return body


def register(client, email="owner@example.com", **changes):
    result = client.post(f"{AUTH}/register", json=registration_body(email, **changes))
    assert result.status_code == 201, result.text
    return result.json()


def session(client):
    result = client.get(f"{BASE}/session")
    assert result.status_code == 200, result.text
    return result.json()


def csrf_headers(state, **headers):
    return {"X-CSRF-Token": state["csrf_token"], **headers}


def login(client, email="owner@example.com", password=PASSWORD):
    return client.post(f"{AUTH}/login", json={"email": email, "password": password})


def test_anonymous_session_does_not_expose_identity_or_csrf(saas):
    state = session(saas.client)
    assert state["mode"] == "saas"
    assert state["authenticated"] is False
    assert state["user"] is None
    assert not state["csrf_token"]
    assert state["organizations"] == []
    assert state["active_organization_id"] is None


def test_passwords_are_salted_and_never_returned_or_stored_as_plaintext(saas):
    assert saas.production_scrypt_n >= 2 ** 17
    first = register(saas.client)
    second = register(saas.new_client(), "second@example.com")
    with saas.store.session_factory() as db:
        users = db.query(saas.models.SaaSUser).order_by(saas.models.SaaSUser.id).all()
        assert len(users) == 2
        hashes = [user.password_hash for user in users]
        assert hashes[0] != hashes[1]
        assert all(PASSWORD not in encoded and len(encoded) > 40 for encoded in hashes)
    for state in (first, second):
        assert state["authenticated"] is True
        assert PASSWORD not in str(state)
        assert "password_hash" not in str(state)


@pytest.mark.parametrize("password", ["", "a" * 11])
def test_registration_rejects_short_passwords(saas, password):
    result = saas.client.post(f"{AUTH}/register", json=registration_body(password=password))
    assert result.status_code == 422, result.text
    with saas.store.session_factory() as db:
        assert db.query(saas.models.SaaSUser).count() == 0


def test_unknown_user_and_wrong_password_have_identical_login_errors(saas):
    register(saas.client)
    client = saas.new_client()
    wrong = login(client, password="Wrong-Synthetic-Password-123")
    unknown = login(client, email="missing@example.com", password="Wrong-Synthetic-Password-123")
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()
    assert not client.cookies.get("zhiyuan_session")


def test_login_session_uses_http_only_cookie_and_hashed_storage(saas):
    register(saas.client)
    client = saas.new_client()
    response = login(client)
    assert response.status_code == 200, response.text
    cookie_header = response.headers["set-cookie"].lower()
    assert "httponly" in cookie_header
    assert "samesite=lax" in cookie_header
    assert "path=/" in cookie_header
    assert "secure" not in cookie_header
    token = client.cookies.get("zhiyuan_session")
    assert token and token != response.json()["csrf_token"]
    with saas.store.session_factory() as db:
        records = db.query(saas.models.SaaSSession).all()
        assert records and all(row.token_hash != token for row in records)
    current = session(client)
    assert current["authenticated"] is True
    assert current["user"]["email"] == "owner@example.com"
    assert current["csrf_token"] == response.json()["csrf_token"]


def test_expired_session_is_unauthenticated(saas):
    register(saas.client)
    with saas.store.session_factory() as db:
        for record in db.query(saas.models.SaaSSession).all():
            record.expires_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=1)
        db.commit()
    assert session(saas.client)["authenticated"] is False


def test_logout_revokes_session_even_if_cookie_is_replayed(saas):
    state = register(saas.client)
    stolen_cookie = saas.client.cookies.get("zhiyuan_session")
    response = saas.client.post(f"{AUTH}/logout", headers=csrf_headers(state))
    assert response.status_code == 200, response.text
    assert session(saas.client)["authenticated"] is False
    replay = saas.new_client()
    replay.cookies.set("zhiyuan_session", stolen_cookie)
    assert session(replay)["authenticated"] is False
    with saas.store.session_factory() as db:
        assert all(row.revoked_at is not None for row in db.query(saas.models.SaaSSession).all())


@pytest.mark.parametrize("csrf", [None, "wrong-synthetic-csrf"])
def test_logout_requires_valid_csrf_and_keeps_session_on_rejection(saas, csrf):
    register(saas.client)
    response = saas.client.post(f"{AUTH}/logout", headers={} if csrf is None else {"X-CSRF-Token": csrf})
    assert response.status_code == 403, response.text
    assert session(saas.client)["authenticated"] is True


def test_csrf_token_from_another_session_cannot_authorize_logout(saas):
    register(saas.client)
    another = register(saas.new_client(), "second@example.com")
    response = saas.client.post(f"{AUTH}/logout", headers=csrf_headers(another))
    assert response.status_code == 403, response.text
    assert session(saas.client)["authenticated"] is True


def invite(client, state, email="invited@example.com", role="editor", **headers):
    response = client.post(f"{BASE}/invites", json={"email": email, "role": role},
                           headers=csrf_headers(state, **headers))
    assert response.status_code in {200, 201}, response.text
    result = response.json()
    assert result["token"] and result["invite_url"]
    return result


def accept(client, state, token):
    return client.post(f"{BASE}/invites/accept", json={"token": token}, headers=csrf_headers(state))


def membership_rows(saas, email):
    with saas.store.session_factory() as db:
        user = db.query(saas.models.SaaSUser).filter_by(email=email).one()
        return db.query(saas.models.SaaSMembership).filter_by(user_id=user.id).all()


def test_invites_cannot_directly_grant_owner(saas):
    state = register(saas.client)
    response = saas.client.post(f"{BASE}/invites", json={"email": "invited@example.com", "role": "owner"},
                                headers=csrf_headers(state))
    assert response.status_code == 422, response.text
    with saas.store.session_factory() as db:
        assert db.query(saas.models.SaaSInvite).count() == 0


def test_invite_tokens_are_hashed_and_anonymous_acceptance_is_blocked(saas):
    state = register(saas.client)
    created = invite(saas.client, state)
    with saas.store.session_factory() as db:
        record = db.query(saas.models.SaaSInvite).one()
        assert record.token_hash != created["token"]
        assert record.accepted_at is None
    anonymous = saas.new_client().post(f"{BASE}/invites/accept", json={"token": created["token"]})
    assert anonymous.status_code == 401, anonymous.text


def test_invite_email_mismatch_cannot_join_or_consume_token(saas):
    owner = register(saas.client)
    created = invite(saas.client, owner)
    wrong_client = saas.new_client()
    wrong_user = register(wrong_client, "different@example.com")
    before = len(membership_rows(saas, "different@example.com"))
    response = accept(wrong_client, wrong_user, created["token"])
    assert response.status_code in {400, 403, 409}, response.text
    assert len(membership_rows(saas, "different@example.com")) == before
    with saas.store.session_factory() as db:
        assert db.query(saas.models.SaaSInvite).one().accepted_at is None


def test_expired_invite_cannot_create_membership(saas):
    owner = register(saas.client)
    created = invite(saas.client, owner)
    invited_client = saas.new_client()
    invited = register(invited_client, "invited@example.com")
    with saas.store.session_factory() as db:
        record = db.query(saas.models.SaaSInvite).one()
        record.expires_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=1)
        db.commit()
    before = len(membership_rows(saas, "invited@example.com"))
    response = accept(invited_client, invited, created["token"])
    assert response.status_code in {400, 403, 409, 410}, response.text
    assert len(membership_rows(saas, "invited@example.com")) == before


def test_invite_acceptance_is_single_use_and_preserves_assigned_role(saas):
    owner = register(saas.client)
    created = invite(saas.client, owner, role="reviewer")
    invited_client = saas.new_client()
    invited = register(invited_client, "invited@example.com")
    before = len(membership_rows(saas, "invited@example.com"))
    accepted = accept(invited_client, invited, created["token"])
    assert accepted.status_code == 200, accepted.text
    members = membership_rows(saas, "invited@example.com")
    assert len(members) == before + 1
    assert any(member.role == "reviewer" for member in members)
    repeated = accept(invited_client, session(invited_client), created["token"])
    assert repeated.status_code in {400, 403, 409, 410}, repeated.text
    assert len(membership_rows(saas, "invited@example.com")) == before + 1


def test_invited_registration_works_when_public_registration_is_disabled(saas, monkeypatch):
    owner = register(saas.client)
    created = invite(saas.client, owner, role="viewer")
    monkeypatch.setenv("SAAS_ALLOW_REGISTRATION", "false")
    client = saas.new_client()
    public = client.post(f"{AUTH}/register", json=registration_body("public@example.com"))
    assert public.status_code == 403, public.text
    invited = register(client, "invited@example.com", invite_token=created["token"])
    assert invited["authenticated"] is True
    members = membership_rows(saas, "invited@example.com")
    assert len(members) == 1 and members[0].role == "viewer"
    with saas.store.session_factory() as db:
        assert db.query(saas.models.SaaSInvite).one().accepted_at is not None
        assert db.query(saas.models.SaaSOrganization).count() == 1


def test_login_rate_limit_has_retry_after_and_reset_restores_requests(saas, monkeypatch):
    monkeypatch.setattr(saas.auth, "AUTH_ACCOUNT_LIMIT", 2)
    for _ in range(2):
        assert login(saas.client, "missing@example.com").status_code == 401
    limited = login(saas.client, "missing@example.com")
    assert limited.status_code == 429, limited.text
    assert int(limited.headers["retry-after"]) > 0
    saas.auth.reset_rate_limits()
    assert login(saas.client, "missing@example.com").status_code == 401


def test_rate_limit_buckets_stay_bounded_on_rejected_requests(saas, monkeypatch):
    monkeypatch.setattr(saas.auth, "AUTH_ACCOUNT_LIMIT", 1)
    monkeypatch.setattr(saas.auth, "MAX_RATE_BUCKETS", 8)

    def request(index):
        return Request({"type": "http", "method": "POST", "path": "/login", "headers": [],
                        "client": (f"192.0.2.{index}", 10000 + index)})

    saas.auth.check_auth_rate_limit(request(1), "same-synthetic@example.com")
    for index in range(2, 20):
        with pytest.raises(HTTPException) as denied:
            saas.auth.check_auth_rate_limit(request(index), "same-synthetic@example.com")
        assert denied.value.status_code == 429
        assert len(saas.auth._rate_buckets) <= 8


def joined_member(saas, role="editor"):
    owner = register(saas.client)
    organization_id = owner["active_organization_id"]
    client = saas.new_client()
    member_state = register(client, "member@example.com")
    created = invite(saas.client, owner, "member@example.com", role=role)
    accepted = accept(client, member_state, created["token"])
    assert accepted.status_code == 200, accepted.text
    membership = next(item for item in membership_rows(saas, "member@example.com")
                      if item.organization_id == organization_id)
    return owner, client, session(client), membership


def test_another_organization_header_cannot_read_members_or_audit(saas):
    first = register(saas.client)
    second_client = saas.new_client()
    second = register(second_client, "second@example.com")
    assert second["active_organization_id"] != first["active_organization_id"]
    foreign = {"X-Organization-ID": first["active_organization_id"]}
    for path in ("members", "audit"):
        response = second_client.get(f"{BASE}/{path}", headers=foreign)
        assert response.status_code == 403, response.text
    visible = session(second_client)["organizations"]
    assert {org["id"] for org in visible} == {second["active_organization_id"]}


@pytest.mark.parametrize("role", ["editor", "reviewer", "viewer"])
def test_nonowners_cannot_invite_self_promote_or_read_audit(saas, role):
    owner, client, state, membership = joined_member(saas, role)
    scope = {"X-Organization-ID": owner["active_organization_id"]}
    assert client.get(f"{BASE}/members", headers=scope).status_code == 200
    invitations = client.post(f"{BASE}/invites", json={"email": "extra@example.com", "role": "viewer"},
                               headers=csrf_headers(state, **scope))
    assert invitations.status_code == 403, invitations.text
    promotion = client.patch(f"{BASE}/members/{membership.id}", json={"role": "owner"},
                              headers=csrf_headers(state, **scope))
    assert promotion.status_code == 403, promotion.text
    assert client.get(f"{BASE}/audit", headers=scope).status_code == 403
    with saas.store.session_factory() as db:
        assert db.get(saas.models.SaaSMembership, membership.id).role == role


@pytest.mark.parametrize("changes", [{"role": "editor"}, {"is_active": False}])
def test_last_owner_cannot_be_demoted_or_disabled(saas, changes):
    owner = register(saas.client)
    membership = membership_rows(saas, "owner@example.com")[0]
    response = saas.client.patch(f"{BASE}/members/{membership.id}", json=changes, headers=csrf_headers(owner))
    assert response.status_code == 409, response.text
    with saas.store.session_factory() as db:
        current = db.get(saas.models.SaaSMembership, membership.id)
        assert current.is_active and current.role == "owner"


def test_owner_can_transfer_ownership_before_demoting_self(saas):
    owner, _, _, member = joined_member(saas)
    grant = saas.client.patch(f"{BASE}/members/{member.id}", json={"role": "owner"},
                             headers=csrf_headers(owner))
    assert grant.status_code == 200, grant.text
    previous_owner = membership_rows(saas, "owner@example.com")[0]
    demote = saas.client.patch(f"{BASE}/members/{previous_owner.id}", json={"role": "editor"},
                              headers=csrf_headers(owner))
    assert demote.status_code == 200, demote.text
    # The same cookie must immediately reflect the reduced role.
    assert saas.client.get(f"{BASE}/audit").status_code == 403
    with saas.store.session_factory() as db:
        assert db.get(saas.models.SaaSMembership, member.id).role == "owner"


def test_disabled_membership_cannot_reuse_issued_session_to_access_org(saas):
    owner, client, _, member = joined_member(saas)
    scope = {"X-Organization-ID": owner["active_organization_id"]}
    assert client.get(f"{BASE}/members", headers=scope).status_code == 200
    response = saas.client.patch(f"{BASE}/members/{member.id}", json={"is_active": False},
                                 headers=csrf_headers(owner))
    assert response.status_code == 200, response.text
    denied = client.get(f"{BASE}/members", headers=scope)
    assert denied.status_code == 403, denied.text
    assert owner["active_organization_id"] not in {org["id"] for org in session(client)["organizations"]}


def test_owner_cannot_update_membership_id_from_another_organization(saas):
    owner = register(saas.client)
    register(saas.new_client(), "second@example.com")
    foreign_member = membership_rows(saas, "second@example.com")[0]
    response = saas.client.patch(f"{BASE}/members/{foreign_member.id}", json={"role": "viewer"},
                                 headers=csrf_headers(owner))
    assert response.status_code in {403, 404}, response.text
    with saas.store.session_factory() as db:
        assert db.get(saas.models.SaaSMembership, foreign_member.id).role == "owner"


@pytest.mark.parametrize("operation", ["organization", "invite", "member", "accept"])
def test_account_write_routes_check_csrf_without_app_middleware(saas, operation):
    register(saas.client)
    membership = membership_rows(saas, "owner@example.com")[0]
    writes = {
        "organization": ("POST", "/organizations", {"name": "未经授权的新组织"}),
        "invite": ("POST", "/invites", {"email": "extra@example.com", "role": "viewer"}),
        "member": ("PATCH", f"/members/{membership.id}", {"role": "owner"}),
        "accept": ("POST", "/invites/accept", {"token": "t" * 43}),
    }
    method, path, payload = writes[operation]
    response = saas.client.request(method, BASE + path, json=payload)
    assert response.status_code == 403, response.text
    with saas.store.session_factory() as db:
        assert db.query(saas.models.SaaSOrganization).count() == 1
        assert db.query(saas.models.SaaSInvite).count() == 0


def test_new_organization_belongs_to_authenticated_user_and_can_be_selected(saas):
    original = register(saas.client)
    created = saas.client.post(f"{BASE}/organizations", json={"name": "第二个合成组织"},
                               headers=csrf_headers(original))
    assert created.status_code == 201, created.text
    organization = created.json()
    assert organization["role"] == "owner"
    assert organization["id"] != original["active_organization_id"]
    listed = saas.client.get(f"{BASE}/organizations")
    assert listed.status_code == 200, listed.text
    assert {item["id"] for item in listed.json()["items"]} == {
        organization["id"], original["active_organization_id"],
    }
    assert session(saas.client)["active_organization_id"] == organization["id"]
    switched = saas.client.get(f"{BASE}/session", headers={"X-Organization-ID": original["active_organization_id"]})
    assert switched.json()["active_organization_id"] == original["active_organization_id"]


def test_disabled_user_loses_existing_session_and_cannot_login(saas):
    register(saas.client)
    with saas.store.session_factory() as db:
        db.query(saas.models.SaaSUser).one().is_active = False
        db.commit()
    assert session(saas.client)["authenticated"] is False
    disabled = login(saas.new_client())
    unknown = login(saas.new_client(), "missing@example.com")
    assert disabled.status_code == unknown.status_code == 401
    assert disabled.json() == unknown.json()


def test_secure_cookie_configuration_and_session_no_store(saas, monkeypatch):
    monkeypatch.setenv("SAAS_SECURE_COOKIE", "true")
    response = saas.client.post(f"{AUTH}/register", json=registration_body())
    assert response.status_code == 201, response.text
    assert "; secure" in response.headers["set-cookie"].lower()
    assert response.headers["cache-control"] == "no-store"
    assert saas.client.get(f"{BASE}/session").headers["cache-control"] == "no-store"


def test_owner_audit_is_scoped_and_redacts_sensitive_details(saas):
    from app.saas.context import TenantContext

    first = register(saas.client)
    second = register(saas.new_client(), "second@example.com")
    context = TenantContext(organization_id=first["active_organization_id"],
                            user_id=first["user"]["id"], role="owner")
    saas.auth.record_audit(context, "synthetic.audit", "synthetic", {
        "password": PASSWORD, "api_key": "synthetic-secret-api-key",
        "nested": {"token": "synthetic-invite-token"}, "safe_count": 2,
    })
    response = saas.client.get(f"{BASE}/audit")
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert items and all(item["actor_user_id"] == first["user"]["id"] for item in items)
    assert second["user"]["id"] not in response.text
    assert PASSWORD not in response.text
    assert "synthetic-secret-api-key" not in response.text
    assert "synthetic-invite-token" not in response.text
    synthetic = next(item for item in items if item["action"] == "synthetic.audit")
    assert synthetic["details"]["safe_count"] == 2


def test_non_ascii_csrf_is_denied_without_server_error(saas):
    register(saas.client)
    raw = saas.client.cookies.get("zhiyuan_session")
    request = Request({
        "type": "http", "method": "POST", "path": f"{AUTH}/logout",
        "headers": [(b"cookie", f"zhiyuan_session={raw}".encode("ascii")),
                    (b"x-csrf-token", b"\xe9")],
    })
    with pytest.raises(HTTPException) as denied:
        saas.auth.validate_csrf(request)
    assert denied.value.status_code == 403


def test_logout_still_works_with_stale_org_header_after_membership_revocation(saas):
    owner, member_client, member_state, membership = joined_member(saas)
    raw = member_client.cookies.get("zhiyuan_session")
    disabled = saas.client.patch(f"{BASE}/members/{membership.id}", json={"is_active": False},
                                 headers=csrf_headers(owner))
    assert disabled.status_code == 200, disabled.text
    response = member_client.post(f"{AUTH}/logout", headers=csrf_headers(
        member_state, **{"X-Organization-ID": owner["active_organization_id"]},
    ))
    assert response.status_code == 200, response.text
    replay = saas.new_client()
    replay.cookies.set("zhiyuan_session", raw)
    assert session(replay)["authenticated"] is False


def test_session_bootstrap_recovers_from_revoked_org_but_business_identity_stays_strict(saas):
    owner, member_client, _, membership = joined_member(saas)
    revoked_id = owner["active_organization_id"]
    stale_header = {"X-Organization-ID": revoked_id}
    disabled = saas.client.patch(f"{BASE}/members/{membership.id}", json={"is_active": False},
                                 headers=csrf_headers(owner))
    assert disabled.status_code == 200, disabled.text
    recovered = member_client.get(f"{BASE}/session", headers=stale_header)
    assert recovered.status_code == 200, recovered.text
    state = recovered.json()
    assert state["authenticated"] is True
    remaining = {organization["id"] for organization in state["organizations"]}
    assert revoked_id not in remaining
    assert state["active_organization_id"] in remaining
    assert member_client.get(f"{BASE}/members", headers=stale_header).status_code == 403

    raw = member_client.cookies.get("zhiyuan_session")
    request = Request({
        "type": "http", "method": "GET", "path": f"{BASE}/members",
        "headers": [(b"cookie", f"zhiyuan_session={raw}".encode("ascii")),
                    (b"x-organization-id", revoked_id.encode("ascii"))],
    })
    with pytest.raises(HTTPException) as denied:
        saas.auth.resolve_identity(request)
    assert denied.value.status_code == 403


def test_concurrent_invite_acceptance_cannot_overfill_last_member_slot(saas):
    from app.saas.commerce import get_plan_limits

    owner, _, _, _ = joined_member(saas)
    organization_id = owner["active_organization_id"]
    assert get_plan_limits(organization_id)["members"] == 3
    candidates = []
    for number in range(2):
        email = f"candidate-{number}@example.com"
        client = saas.new_client()
        state = register(client, email)
        created = invite(saas.client, owner, email, role="viewer")
        candidates.append((client, state, created["token"]))
    barrier = Barrier(2)

    def join(candidate):
        barrier.wait(timeout=5)
        return accept(*candidate)

    with ThreadPoolExecutor(max_workers=2) as workers:
        responses = list(workers.map(join, candidates))
    assert sorted(response.status_code for response in responses) == [200, 409]
    with saas.store.session_factory() as db:
        assert db.query(saas.models.SaaSMembership).filter_by(
            organization_id=organization_id, is_active=True,
        ).count() == 3
        invitations = db.query(saas.models.SaaSInvite).filter(
            saas.models.SaaSInvite.email.in_(["candidate-0@example.com", "candidate-1@example.com"]),
        ).all()
        assert sum(record.accepted_at is not None for record in invitations) == 1
