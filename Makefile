.PHONY: setup run check evaluate
setup:
	./scripts/setup.sh
run:
	./scripts/start.sh
check:
	./scripts/check.sh
evaluate:
	.venv/bin/python scripts/evaluate_rag.py --base-url http://127.0.0.1:8765 --output .data/rag-evaluation.json
