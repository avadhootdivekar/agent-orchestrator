.PHONY: test test-fast test-playground test-real-llm lint format typecheck validate-sum-of-array \
        bench-validate bench-smoke bench-run bench-report bench-medium bench-large bench-xlarge

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

# Per-tier `ao-bench campaign` recipes (E-Bt4Xk9 T-Cm9Tb4). Cost-safety guard: NONE of
# these ever spend money on a bare `make bench-medium`/`bench-large` -- each recipe only
# PRINTS the EPIC's own PLAN cost envelope + the exact command it would run, then
# refuses (exit 1) unless AO_BENCH_CONFIRM=1 is set on the same invocation. `--max-
# parallel` is deliberately left UNSET here so it resolves from each suite's OWN tier
# default (medium=4, large=3 in benchmarks/tiers.json) via T-Cm9Tb4's CLI wiring --
# already exactly the PLAN's own per-tier parallelism ask, so there is nothing to
# override.
#   make bench-medium AO_BENCH_CONFIRM=1
#   make bench-large  AO_BENCH_CONFIRM=1
#   make bench-xlarge FORCE_XLARGE=1 AO_BENCH_CONFIRM=1   # demonstrates the refusal path
BENCH_MEDIUM_SUITE ?= benchmarks/suites/dev-medium/suite.json
BENCH_MEDIUM_SUBJECTS ?= benchmarks/subjects/claude-sonnet.json benchmarks/subjects/claude-opus.json benchmarks/subjects/ao-epic-sonnet.json benchmarks/subjects/ao-epic-plus-sonnet.json
BENCH_LARGE_SUITE ?= benchmarks/suites/swe-verified-mini/suite.json
BENCH_LARGE_SUBJECTS ?= benchmarks/subjects/claude-sonnet.json benchmarks/subjects/claude-opus.json benchmarks/subjects/ao-epic-sonnet.json
# No xlarge suite is committed (EPIC: xlarge is defined-only, disk-infeasible) -- this
# path exists solely to exercise the --enable-xlarge override plumbing end to end.
BENCH_XLARGE_SUITE ?= benchmarks/suites/xlarge/suite.json
AO_BENCH_CONFIRM ?=
FORCE_XLARGE ?=

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

# --- Per-tier campaign recipes (E-Bt4Xk9 T-Cm9Tb4 / EPIC.md PLAN) ----------------------
# Medium tier (EPIC.md PLAN Run 1): dev-medium x {claude-sonnet, claude-opus,
# ao-epic-sonnet, ao-epic-plus-sonnet}. Estimated ~$75 (range $48-141); tier run cap
# $150 (benchmarks/tiers.json, harness-enforced regardless of this estimate).
bench-medium:
	@echo "=== bench-medium: dev-medium campaign x 4 subjects ==="
	@echo "Estimated cost: ~\$$75 (range \$$48-141); harness-enforced whole-run cap: \$$150 (benchmarks/tiers.json medium tier, EPIC.md PLAN Run 1)."
	@echo "Command: uv run ao-bench campaign --suite $(BENCH_MEDIUM_SUITE) $(foreach s,$(BENCH_MEDIUM_SUBJECTS),--subject $(s)) --out-dir $(RESULTS)"
	@if [ "$(AO_BENCH_CONFIRM)" != "1" ]; then \
		echo "Refusing to launch: this spends real money. Set AO_BENCH_CONFIRM=1 to actually run."; \
		exit 1; \
	fi
	uv run ao-bench campaign --suite $(BENCH_MEDIUM_SUITE) $(foreach s,$(BENCH_MEDIUM_SUBJECTS),--subject $(s)) --out-dir $(RESULTS)

# Large tier (EPIC.md PLAN Run 2): swe-verified-mini x {claude-sonnet, claude-opus,
# ao-epic-sonnet}. Estimated ~$155 (range $95-270); tier run cap $800. Grading is Docker
# (no LLM cost). AO_BENCH_SWEBENCH_KEEP_IMAGES=1 is exported for the whole campaign:
# keeps the ~10GB of pulled per-instance images on disk across all 3 subjects instead of
# re-pulling each ~1GB image up to 3x (T-Sg6Jf2's own forward note) -- well within the
# ~37GB disk budget the large tier is sized for; cleaned up manually after the run.
bench-large:
	@echo "=== bench-large: swe-verified-mini campaign x 3 subjects ==="
	@echo "Estimated cost: ~\$$155 (range \$$95-270); harness-enforced whole-run cap: \$$800 (benchmarks/tiers.json large tier, EPIC.md PLAN Run 2)."
	@echo "AO_BENCH_SWEBENCH_KEEP_IMAGES=1 will be exported: keeps ~10GB of Docker images resident for the campaign's duration to avoid re-pulling them per subject."
	@echo "Command: AO_BENCH_SWEBENCH_KEEP_IMAGES=1 uv run ao-bench campaign --suite $(BENCH_LARGE_SUITE) $(foreach s,$(BENCH_LARGE_SUBJECTS),--subject $(s)) --out-dir $(RESULTS)"
	@if [ "$(AO_BENCH_CONFIRM)" != "1" ]; then \
		echo "Refusing to launch: this spends real money and pulls Docker images. Set AO_BENCH_CONFIRM=1 to actually run."; \
		exit 1; \
	fi
	AO_BENCH_SWEBENCH_KEEP_IMAGES=1 uv run ao-bench campaign --suite $(BENCH_LARGE_SUITE) $(foreach s,$(BENCH_LARGE_SUBJECTS),--subject $(s)) --out-dir $(RESULTS)

# xlarge tier is disabled by design (EPIC.md: 500-instance SWE-bench Verified is
# infeasible on ~37GB of disk here) -- refuses unconditionally unless FORCE_XLARGE=1,
# which only demonstrates the --enable-xlarge override plumbing (no xlarge suite is
# committed, so the underlying `ao-bench campaign` call will still fail to load one).
bench-xlarge:
	@echo "xlarge is disabled by design (disk); see benchmarks/tiers.json + ADR-0009"
	@if [ "$(FORCE_XLARGE)" != "1" ]; then exit 1; fi
	@echo "FORCE_XLARGE=1: this only demonstrates the --enable-xlarge override path -- no xlarge suite is committed, so this will still fail to load one."
	@echo "Command: uv run ao-bench campaign --suite $(BENCH_XLARGE_SUITE) $(foreach s,$(BENCH_LARGE_SUBJECTS),--subject $(s)) --out-dir $(RESULTS) --enable-xlarge"
	@if [ "$(AO_BENCH_CONFIRM)" != "1" ]; then \
		echo "Refusing to launch: set AO_BENCH_CONFIRM=1 to actually run."; \
		exit 1; \
	fi
	uv run ao-bench campaign --suite $(BENCH_XLARGE_SUITE) $(foreach s,$(BENCH_LARGE_SUBJECTS),--subject $(s)) --out-dir $(RESULTS) --enable-xlarge
