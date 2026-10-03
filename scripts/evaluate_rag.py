"""Upload explicitly synthetic demo documents and evaluate the running local API.

Run: .venv/bin/python scripts/evaluate_rag.py --base-url http://127.0.0.1:8000
The script adds demo documents (content-hash deduplicated), retains them for UI
inspection, and prints measured results. It does not call a generation model.
"""
import argparse
import json
from pathlib import Path
import httpx


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--dataset", type=Path, default=Path(__file__).parent / "fixtures" / "rag_demo.json")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--knowledge-base-id", type=int)
    parser.add_argument("--workspace-id", type=int)
    parser.add_argument("--top-k", type=int, default=3)
    args = parser.parse_args()
    dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    scope = {key: value for key, value in {"knowledge_base_id": args.knowledge_base_id,
             "workspace_id": args.workspace_id}.items() if value is not None}
    with httpx.Client(base_url=args.base_url.rstrip("/"), timeout=300) as client:
        ids = {}
        for document in dataset["documents"]:
            response = client.post("/api/knowledge/documents", json={**scope,
                **{key: value for key, value in document.items() if key != "key"}})
            response.raise_for_status()
            ids[document["key"]] = response.json()["document"]["id"]
        cases = [{"question": case["question"], "should_refuse": case["should_refuse"],
                  "expected_document_ids": [ids[key] for key in case["expected_document_keys"]]} for case in dataset["cases"]]
        response = client.post("/api/knowledge/evaluate", json={**scope, "cases": cases,
                               "top_k": args.top_k, "dataset_label": dataset["label"]})
        response.raise_for_status()
        report = {"dataset_id": dataset["dataset_id"], "document_ids": ids, **response.json()}
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
