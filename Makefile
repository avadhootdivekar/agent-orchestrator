.PHONY: test test-fast test-playground test-real-llm lint format typecheck validate-sum-of-array \
        bench-validate bench-smoke bench-run bench-report

# Configurable knobs for the real-LLM tier (override on the command line):
#   make test-real-llm MAX_ATTEMPTS=3 BUDGET_TOTAL=200000 MAX_TURNS=40
# MAX_ATTEMPTS — max total attempts per task including first run (default: 1 = run once, no retry; set to N for N-1 retries)
# BUDGET_TOTAL — total token budget for the run (default: unset = unlimited)
# MAX_TURNS    — max turns per claude agent invocation (default: unset = use each agent's effort-derived value)
MAX_ATTEMPTS ?=
BUDGET_TOTAL ?=
MAX_TURNS ?=

# Configurable knobs for the ao-bench targets (override on the command line):
#   make bench-run SUITE=benchmarks/suites/dev-core/suite.json SUBJECT=benchmarks/subjects/claude-opus.json
#   make bench-report RESULTS=benchmarks/results SUITE=dev-core
# SUITE   — path to a suite.json (bench-run) or a committed suite id (bench-report --suite)
# SUBJECT — path to a subject.json (bench-run)
# RESULTS — results root ao-bench report auto-discovers the latest run per subject under
BENCH_SUITE_PATH ?= benchmarks/suites/dev-core/suite.json
BENCH_FAKE_SUBJECT_PATH ?= benchmarks/subjects/fake-pass.json
BENCH_HAIKU_SUBJECT_PATH ?= benchmarks/subjects/claude-haiku.json
BENCH_AO_EPIC_HAIKU_SUBJECT_PATH ?= benchmarks/subjects/ao-epic-haiku.json
BENCH_SMOKE_SUITE_ID ?= dev-core
# Cheap/bounded for the haiku smoke tier (design doc §7's own bench-smoke sketch);
# `bench-run`'s real (sonnet/opus) invocations leave --max-turns unset, deferring to
# each committed subject.json's own default.
BENCH_SMOKE_MAX_TURNS ?= 20
SUITE ?=
SUBJECT ?=
RESULTS ?= benchmarks/results

test:
	uv run pytest -q

test-fast:
	uv run pytest -q -m "not real_llm"

test-playground:
	uv run pytest tests/playground -q

test-real-llm:
	AO_E2E_REAL_LLM=1 AO_MAX_ATTEMPTS=$(MAX_ATTEMPTS) AO_BUDGET_TOTAL=$(BUDGET_TOTAL) AO_MAX_TURNS=$(MAX_TURNS) \
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

# ao-bench targets (E-9Qk4Zt): standalone `ao-bench` console script, never `ao` itself
# (design doc §7, SI-1). `benchmarks/suites/dev-core/` + `benchmarks/subjects/` are the
# committed MVP fixtures (T-Fx6Dp0).
bench-validate:
	uv run ao-bench validate --suite $(BENCH_SUITE_PATH)

# All THREE subject kinds over dev-core at haiku (TASK.md T-Fx6Dp0 AC3): fake (no LLM,
# proves the harness/grader plumbing), bare claude_cli, and the ao_workflow ao-epic
# template — then a comparison report across all three. `-` on each `run` line: one
# subject hitting a transient real-LLM failure (subject_status
# failed/timed_out/error, `ao-bench run` exit 2) must not abort the whole target before
# the OTHER subjects get a chance to run and `report` still summarizes whatever DID
# complete — this is a manual dev convenience target, never a CI gate.
bench-smoke:
	uv run ao-bench run --suite $(BENCH_SUITE_PATH) --subject $(BENCH_FAKE_SUBJECT_PATH)
	-uv run ao-bench run --suite $(BENCH_SUITE_PATH) --subject $(BENCH_HAIKU_SUBJECT_PATH) --max-turns $(BENCH_SMOKE_MAX_TURNS)
	-uv run ao-bench run --suite $(BENCH_SUITE_PATH) --subject $(BENCH_AO_EPIC_HAIKU_SUBJECT_PATH) --max-turns $(BENCH_SMOKE_MAX_TURNS)
	uv run ao-bench report --results-root $(RESULTS) --suite $(BENCH_SMOKE_SUITE_ID)

bench-run:
	uv run ao-bench run --suite $(SUITE) --subject $(SUBJECT)

bench-report:
	uv run ao-bench report --results-root $(RESULTS) --suite $(SUITE)
