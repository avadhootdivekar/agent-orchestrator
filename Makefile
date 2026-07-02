.PHONY: test test-fast test-playground test-real-llm lint format typecheck validate-sum-of-array

# Configurable knobs for the real-LLM tier (override on the command line):
#   make test-real-llm MAX_ATTEMPTS=3 BUDGET_TOTAL=200000
# MAX_ATTEMPTS — max retries per task (default: 1, i.e. no retry)
# BUDGET_TOTAL — total token budget for the run (default: unset = unlimited)
MAX_ATTEMPTS ?=
BUDGET_TOTAL ?=

test:
	uv run pytest -q

test-fast:
	uv run pytest -q -m "not real_llm"

test-playground:
	uv run pytest tests/playground -q

test-real-llm:
	AO_E2E_REAL_LLM=1 AO_MAX_ATTEMPTS=$(MAX_ATTEMPTS) AO_BUDGET_TOTAL=$(BUDGET_TOTAL) \
	  uv run pytest -m real_llm -v

lint:
	uv run ruff check .

format:
	uv run ruff format .

typecheck:
	uv run mypy .

validate-sum-of-array:
	uv run ao validate \
	  --workflow playground/sum-of-array/workflow.json \
	  --reposets playground/sum-of-array/reposet.json \
	  --agents   playground/sum-of-array/agents.fake.json
