"""Compare lexical, genuine semantic and hybrid retrieval in isolated temporary data.

Uses the configured FastEmbed cache, no generation API, no running app database.
First run may download the embedding model. Keep bad cases; no threshold fitting.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / ".data" / "hybrid-retrieval-evaluation.json")
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--cache-dir", type=Path, default=ROOT / ".data" / "models")
    args = parser.parse_args()
    if not 1 <= args.top_k <= 12:
        parser.error("top-k must be between 1 and 12")
    datasets = [json.loads((ROOT / "scripts" / "fixtures" / name).read_text())
                for name in ("rag_demo.json", "rag_hybrid_identifiers.json")]
    with TemporaryDirectory(prefix="content-hybrid-eval-") as directory:
        os.environ.update({"DATABASE_URL": "sqlite:///:memory:", "SAAS_MODE": "false",
            "RAG_RETRIEVAL_MODE": "semantic", "RAG_EMBEDDING_PROVIDER": "fastembed",
            "RAG_EMBEDDING_MODEL": "BAAI/bge-small-zh-v1.5", "RAG_VECTOR_PATH": str(Path(directory) / "vectors"),
            "RAG_EMBEDDING_CACHE_DIR": str(args.cache_dir)})
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        import app.models  # noqa: F401
        from app.db.session import Base
        from app.agent_core.vector_store import close_vector_stores
        from app.api.routes.knowledge import DocumentUpload, EvaluationRequest, upload_document, evaluate_knowledge
        engine = create_engine("sqlite://")
        Base.metadata.create_all(engine)
        try:
            with sessionmaker(bind=engine, expire_on_commit=False)() as db:
                ids = {}
                for dataset in datasets:
                    for document in dataset["documents"]:
                        body = {key: value for key, value in document.items() if key != "key"}
                        ids[document["key"]] = upload_document(DocumentUpload(**body), db)["document"]["id"]
                cases = [{"question": case["question"], "should_refuse": case["should_refuse"],
                          "expected_document_ids": [ids[key] for key in case["expected_document_keys"]]}
                         for dataset in datasets for case in dataset["cases"]]
                reports = {}
                for mode in ("lexical", "semantic", "hybrid"):
                    print(f"Evaluating {mode}...", flush=True)
                    reports[mode] = evaluate_knowledge(EvaluationRequest(cases=cases, top_k=args.top_k,
                        retrieval_mode=mode, dataset_label="人工中文小样本21题；真实BGE模型，不代表业务准确率"), db)
        finally:
            close_vector_stores()
            engine.dispose()
    result = {"evaluated_at": datetime.now(timezone.utc).isoformat(), "datasets": [d["dataset_id"] for d in datasets],
              "model": "BAAI/bge-small-zh-v1.5", "document_count": len(ids), "sample_count": len(cases),
              "top_k": args.top_k, "reports": reports,
              "limitations": ["合成语料与人工问题；不是独立真实业务测试集。", "阈值保持原值，记录误拒与错误接纳，不按结果调参。",
                              "引用存在校验不验证结论蕴含；未测生成质量。", "单机串行测量；模型启动与缓存会影响延迟。"]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"output": str(args.output), "samples": len(cases),
        "metrics": {mode: report["metrics"] for mode, report in reports.items()}}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
