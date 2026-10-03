"""Pilot data is bound to current server membership, including equal run IDs."""
import pytest

from app.models.pilot_record import PilotRecord
from test_saas_isolation import application, teams, invited_member, business_db, seed_legacy


BODY = {"version": 0, "cohort": "synthetic", "outcome": "abandoned", "note": "ALPHA-PRIVATE-REVIEW"}


def test_reports_and_records_require_identity_and_keep_organizations_separate(application, teams):
    a, b = teams
    ids_a, ids_b = seed_legacy(a, "ALPHA"), seed_legacy(b, "BETA")
    assert ids_a["run"] == ids_b["run"]
    route = f"/api/pilot/records/{ids_a['run']}"
    assert application.anonymous.get(route).status_code == 401
    assert application.anonymous.put(route, json=BODY).status_code == 401
    assert application.anonymous.get("/api/pilot/report", params={"start_date": "2000-01-01", "end_date": "9999-12-31"}).status_code == 401
    response = a.client.put(route, json=BODY)
    assert response.status_code == 200, response.text
    assert b.client.get(route).json()["record"] is None
    assert a.client.get(route, headers={"X-Organization-ID": b.org}).status_code == 403
    for identity, expected, excluded in ((a, "ALPHA", "BETA"), (b, "BETA", "ALPHA")):
        result = identity.client.get("/api/pilot/report", params={"start_date": "2000-01-01", "end_date": "9999-12-31"})
        assert result.status_code == 200, result.text
        assert expected in result.text and excluded not in result.text
    with business_db(a) as db:
        assert db.get(PilotRecord, ids_a["run"]).created_by == a.user


@pytest.mark.parametrize("role", ["editor", "reviewer", "viewer"])
def test_owner_and_editor_write_other_members_only_read(application, teams, role):
    owner, _ = teams
    ids = seed_legacy(owner, "ALPHA")
    route = f"/api/pilot/records/{ids['run']}"
    member = invited_member(application, owner, role)
    assert member.client.get(route).status_code == 200
    response = member.client.put(route, json=BODY)
    assert response.status_code == (200 if role == "editor" else 403), response.text
    with business_db(owner) as db:
        record = db.get(PilotRecord, ids["run"])
        if role == "editor":
            assert record.created_by == member.user
        else:
            assert record is None


def test_mutation_checks_csrf_origin_and_current_membership(application, teams):
    owner, _ = teams
    ids = seed_legacy(owner, "ALPHA")
    route = f"/api/pilot/records/{ids['run']}"
    assert owner.client.put(route, json=BODY, headers={"X-CSRF-Token": ""}).status_code == 403
    assert owner.client.put(route, json=BODY, headers={"Origin": "https://other.example.invalid"}).status_code == 403
    member = invited_member(application, owner, "editor")
    changed = owner.client.patch(f"/api/saas/members/{member.membership}", json={"role": "viewer"})
    assert changed.status_code == 200, changed.text
    assert member.client.put(route, json=BODY).status_code == 403
    with business_db(owner) as db:
        assert db.get(PilotRecord, ids["run"]) is None
