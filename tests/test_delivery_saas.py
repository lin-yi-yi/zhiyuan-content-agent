"""Full-app handoff authorization uses actual SaaS sessions and tenant databases."""
import pytest

from test_saas_isolation import application, teams, invited_member, business_db

from app.models.agent_run import AgentRun, AgentStep
from app.models.draft import Draft
from app.models.topic import Topic
from app.schemas.agent_run import AgentReviewCreate
from app.services.content_growth_agent import review_agent_run


def seed_approved(identity, label):
    with business_db(identity) as db:
        topic = Topic(title=label)
        db.add(topic)
        db.flush()
        draft = Draft(topic_id=topic.id, selected_title=label, body_text=label, status="awaiting_review")
        db.add(draft)
        db.flush()
        run = AgentRun(goal=label, draft_id=draft.id, status="awaiting_review", current_step="human_review",
                       result_json={"_request": {"use_rag": False}})
        db.add(run)
        db.flush()
        db.add(AgentStep(run_id=run.id, step_index=1, key="human_review", label="人工审核", status="awaiting_review"))
        db.commit()
        review_agent_run(run.id, AgentReviewCreate(decision="approve", note=identity.email), db)
        return run.id


@pytest.mark.parametrize("role", ["viewer", "editor", "reviewer"])
def test_handoff_allows_members_but_keeps_auth_and_tenant_isolation(application, teams, role):
    owner, other = teams
    first_id = seed_approved(owner, "TENANT-ALPHA-APPROVED")
    second_id = seed_approved(other, "TENANT-BETA-APPROVED")
    assert first_id == second_id
    member = invited_member(application, owner, role)
    route = f"/api/agent-runs/{first_id}/delivery"
    assert application.anonymous.get(route).status_code == 401
    for client in (owner.client, member.client):
        response = client.get(route)
        assert response.status_code == 200, response.text
        assert response.json()["title"] == "TENANT-ALPHA-APPROVED"
        assert "TENANT-BETA" not in response.text and owner.email not in response.text
    assert other.client.get(route).json()["title"] == "TENANT-BETA-APPROVED"
    assert member.client.get(route, headers={"X-Organization-ID": other.org}).status_code == 403
