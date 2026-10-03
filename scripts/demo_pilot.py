"""Exercise pilot APIs with isolated synthetic records, optionally serve the UI.

No generation API or customer data is used. The temporary database is discarded
on exit. This verifies pilot accounting and review gates, not an LLM/RAG pipeline
or a customer's actual productivity. Run from the repository's .venv.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve", action="store_true", help="keep the synthetic preview open on localhost")
    parser.add_argument("--port", type=int, default=8771)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("port must be 1024–65535")
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "backend"))
    with TemporaryDirectory(prefix="content-pilot-demo-") as folder:
        os.environ.update({
            "PYTHON_DOTENV_DISABLED": "1",
            "SAAS_MODE": "false", "DATABASE_URL": f"sqlite:///{Path(folder) / 'demo.db'}",
            "RAG_RETRIEVAL_MODE": "lexical", "RAG_VECTOR_PATH": str(Path(folder) / "vectors"),
            "DEFAULT_LLM_PROVIDER": "local", "AIHOT_ENABLED": "false", "GITHUB_ENABLED": "false",
        })
        for provider in ("DEEPSEEK", "QWEN", "DOUBAO", "KIMI"):
            os.environ[f"{provider}_API_KEY"] = ""
        for task in ("DEFAULT_LLM", "TOPIC_SCORE", "DRAFT_GENERATION", "CARD_GENERATION", "COMPLIANCE_CHECK"):
            os.environ[f"{task}_PROVIDER"] = "local"
            os.environ[f"{task}_MODEL"] = "local-rule-based-v0"
        from fastapi.testclient import TestClient
        from app.main import app
        from app.db.session import SessionLocal, engine
        from app.models.agent_run import AgentRun, AgentStep
        from app.models.draft import Draft
        from app.models.topic import Topic
        from app.schemas.agent_run import AgentReviewCreate
        from app.services.content_growth_agent import review_agent_run
        from app.llm.router import router
        assert all(not cfg["api_key"] for name, cfg in router.PROVIDERS.items() if name != "local")

        def seed(label, approve=False, failed=False):
            with SessionLocal() as db:
                topic = Topic(title=label)
                db.add(topic); db.flush()
                draft = Draft(topic_id=topic.id, selected_title=label,
                              body_text="合成演示内容，不包含客户资料，不用于实际发布。", status="awaiting_review")
                db.add(draft); db.flush()
                run = AgentRun(goal=label, draft_id=draft.id, status="failed" if failed else "awaiting_review",
                               current_step="human_review", result_json={"_request": {"use_rag": False}})
                db.add(run); db.flush()
                db.add(AgentStep(run_id=run.id, step_index=1, key="human_review", label="人工审核", status="awaiting_review"))
                db.commit()
                if approve:
                    review_agent_run(run.id, AgentReviewCreate(decision="approve", note="合成内部审核"), db)
                return run.id, draft.id

        try:
            with TestClient(app) as client:
                fast, _ = seed("合成演示 A · 工业产品 FAQ", approve=True)
                slow, _ = seed("合成演示 B · 返工后耗时增加", approve=True)
                stale, stale_draft = seed("合成演示 C · 验收后内容变化", approve=True)
                abandoned, _ = seed("合成演示 D · 资料不足放弃", failed=True)
                missing, _ = seed("合成演示 E · 尚未采集工时")
                for run_id, baseline, actual, support in ((fast, 60, 25, 5), (slow, 40, 50, 10), (stale, 50, 25, 5)):
                    response = client.put(f"/api/pilot/records/{run_id}", json={
                        "version": 0, "cohort": "合成演示，不是真实客户", "outcome": "accepted",
                        "baseline_minutes": baseline, "actual_work_minutes": actual, "support_minutes": support,
                        "baseline_reference": "合成同类任务对照，未测量真实效率",
                        "acceptance_reference": "合成确认记录，无真实客户验收", "note": "数据仅用于验证计算与界面",
                    })
                    assert response.status_code == 200, response.text
                response = client.put(f"/api/pilot/records/{abandoned}", json={
                    "version": 0, "cohort": "合成演示，不是真实客户", "outcome": "abandoned",
                    "actual_work_minutes": 10, "support_minutes": 0, "note": "合成资料不足，保留失败投入",
                })
                assert response.status_code == 200, response.text
                with SessionLocal() as db:
                    db.get(Draft, stale_draft).body_text += " 未经重新审核的合成修改"
                    db.commit()
                blocked = client.put(f"/api/pilot/records/{missing}", json={
                    "version": 0, "cohort": "合成演示", "outcome": "accepted", "acceptance_reference": "不能绕过内部审核",
                })
                assert blocked.status_code == 409, blocked.text
                conflict = client.put(f"/api/pilot/records/{fast}", json={"version": 0, "cohort": "合成演示", "outcome": "pending"})
                assert conflict.status_code == 409, conflict.text
                today = datetime.now(timezone.utc).date().isoformat()
                response = client.get("/api/pilot/report", params={"start_date": today, "end_date": today})
                assert response.status_code == 200, response.text
                report = response.json()
                for key, expected in {"total_runs": 5, "accepted_runs": 2, "stale_acceptances": 1,
                                      "abandoned_runs": 1, "missing_records": 1, "comparable_runs": 2,
                                      "baseline_minutes_total": 100, "actual_minutes_total": 90,
                                      "saved_minutes_total": 10, "savings_rate": .1}.items():
                    assert report[key] == expected, (key, report[key], expected)
                print(json.dumps({"verified": True, "data": "synthetic only", "checks": [
                    "save via HTTP", "approval gate 409", "version conflict 409", "stale acceptance",
                    "missing remains unknown", "negative case included"],
                    "summary": {key: value for key, value in report.items() if key != "items"}}, ensure_ascii=False, indent=2), flush=True)
            if args.serve:
                import uvicorn
                print(f"合成数据预览：http://127.0.0.1:{args.port}/#pilot（Ctrl+C 退出后清除演示数据）", flush=True)
                uvicorn.run(app, host="127.0.0.1", port=args.port)
        finally:
            engine.dispose()


if __name__ == "__main__":
    main()
