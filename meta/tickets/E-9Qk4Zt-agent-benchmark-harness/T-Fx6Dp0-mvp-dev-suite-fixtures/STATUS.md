# STATUS

- ID: `T-Fx6Dp0-mvp-dev-suite-fixtures`
- Updated At: 2026-07-22
- State: Done — MVP finish line reached (epic `E-9Qk4Zt` all 7/9 tasks now delivered)
- Owner: developer agent (tester review)

## This update
- By: Claude · Role: developer · Date: 2026-07-22 · Comment: Implementation complete.
  Curated `benchmarks/suites/dev-core/suite.json` (6 tasks: 2 bugfix, 2 feature, 1
  refactor, 1 test-writing — exactly TASK.md's band), all six committed
  `benchmarks/subjects/*.json` (`fake-pass`, `claude-{haiku,sonnet,opus}`,
  `ao-epic-{haiku,sonnet}`), and the `ao-epic` workflow template (`workflow.json` +
  `reposet.json` + `agents.json` + `instructions/{implement,verify}.md`). Activated
  `make bench-smoke` (fake-pass → claude-haiku → ao-epic-haiku → `ao-bench report`,
  `-` on the two real-subject lines so one flaky task never blocks the rest). Added
  `tests/bench/test_dev_core_suite.py` (22 deterministic tests). Real haiku smoke run
  for real: **claude-haiku 6/6 solved, $0.3732, 166.37s**; **ao-epic-haiku 6/6 solved,
  $0.6617, 343.05s**; fake-pass 6/6 solved (proof tier). Total real spend ≈ $1.04,
  well under the $5 cap. No suite curation fixes were needed mid-flight — every task
  solved cleanly on the first attempt for both real subjects.

  **Q1 (ao-epic workflow shape) resolved:** minimal 2-task `implement` (agent
  `developer`) → `verify` (agent `tester`) pipeline, per the orchestrator's resolution.
  **Design-gap found and worked around (documented in
  `benchmarks/subjects/ao-epic/instructions/implement.md`'s own docstring):** a
  workflow task's `instruction` path resolves against `AO_WORKSPACE_ROOT`
  (`bench/subjects.py`'s `AoWorkflowSubject` sets this to the bench task's ephemeral
  per-run workspace, and `bench/subjects.py` never copies a generic instructions/
  directory into it), so `workflow.json`'s `implement`/`verify` tasks cannot reference
  the committed `instructions/{implement,verify}.md` files directly — those would
  resolve outside the workspace root and be rejected by `LocalFsArtifactStore`'s path
  guard. Fix: both tasks' `instruction` field is `repo/INSTRUCTION.md` (the bench
  task's own instruction, already materialized into the workspace by
  `bench/workspace.py` before the subject runs); the committed `instructions/*.md`
  files serve as human-readable role documentation, mirrored verbatim into
  `agents.json`'s `prompt_template` strings (the one string the engine actually
  renders) — kept in sync, noted in both files. Each agent also sets
  `"working_dir": "repo"` so its cwd is the fixture root (where `uv run pytest`/
  `INSTRUCTION.md` actually live), not the outer ephemeral workspace dir.
  **Q2 (model ids) resolved:** full pinned ids per the orchestrator's resolution —
  `claude-opus-4-8` / `claude-sonnet-5` / `claude-haiku-4-5-20251001` — no bare
  aliases in any committed subject config.
  **Model-clobber safety (learnings "model override clobbers per-agent"):** neither
  `developer` nor `tester` in `ao-epic/agents.json` sets its own `model` field — the
  subject's `model` flows through `AO_MODEL` env → core `cli.py`'s
  `for spec in agent_map.values(): spec.model = eff_model` uniformly, so there is
  nothing to silently clobber (verified by `test_ao_epic_subjects_pin_model_via_subject_not_per_agent`).

## Evidence
- Design: `docs-md/benchmarking-framework-hld.md` §9, §11, §18.
- Suite + fixtures: `benchmarks/suites/dev-core/{suite.json,tasks/**}` (6 tasks, each with
  a committed `.bench-solution/` reference overlay; refactor/test-writing tasks carry
  their own `check.py`/`check_tests.py` `command`-grader scripts).
- Subjects: `benchmarks/subjects/{fake-pass,claude-haiku,claude-sonnet,claude-opus,
  ao-epic-haiku,ao-epic-sonnet}.json` + `benchmarks/subjects/ao-epic/{workflow,reposet,
  agents}.json` + `instructions/{implement,verify}.md`.
- Committed results (real, produced by this task — leave in place, do not clean):
  `benchmarks/results/2026-07-22-dev-core-{fake-pass,claude-haiku,ao-epic-haiku}/
  {run.json,summary.md}` + `benchmarks/results/2026-07-22-dev-core-compare/
  comparison.{json,md}`.
- Tests: `tests/bench/test_dev_core_suite.py` (22 new, deterministic, no LLM).
- Verification commands run (actual output, not claimed):
  - `uv run ao-bench validate --suite benchmarks/suites/dev-core/suite.json` → `suite
    'dev-core': 6 task(s)` / `OK`.
  - `uv run ao-bench validate --subject benchmarks/subjects/<each of the 6>.json` → `OK`
    for all six (claude_cli ones also print the probed `claude CLI: 2.1.217`).
  - `uv run ao validate --workflow benchmarks/subjects/ao-epic/workflow.json --reposets
    benchmarks/subjects/ao-epic/reposet.json --agents benchmarks/subjects/ao-epic/
    agents.json` → `OK: all specs valid`.
  - `uv run ao-bench run --suite benchmarks/suites/dev-core/suite.json --subject
    benchmarks/subjects/fake-pass.json` → 6/6 solved, $0.0000, committed to
    `benchmarks/results/2026-07-22-dev-core-fake-pass/`.
  - `uv run ao-bench run ... --subject benchmarks/subjects/claude-haiku.json --task
    <each of 6> --max-turns 20` (chunked per-task, generous timeouts) → 6/6 solved,
    **$0.3732 total, 166.37s total wall-clock**, cost/solved $0.0622.
  - `uv run ao-bench run ... --subject benchmarks/subjects/ao-epic-haiku.json --task
    <each of 6> --max-turns 20` (chunked) → 6/6 solved, **$0.6617 total, 343.05s total
    wall-clock**, cost/solved $0.1103.
  - `uv run ao-bench report --results-root benchmarks/results --suite dev-core` →
    `benchmarks/results/2026-07-22-dev-core-compare/comparison.{json,md}` written;
    winners: highest solve rate ao-epic-haiku (tied 100% with the others), lowest
    cost/fastest fake-pass (as expected, it's free/instant).
  - `make bench-smoke` (re-run after the above, all tasks already recorded) → all
    three subjects report `bench.task.skip` for every task (resume path proven), exits
    0, regenerates `comparison.md`.
  - `uv run pytest tests/bench -q` → 190 passed, 1 skipped.
  - `uv run pytest -q -m "not real_llm"` → **1047 passed, 4 deselected** (baseline
    1025 passed/4 deselected + 22 new = 1047, zero regressions). One unrelated,
    pre-existing timing flake (`test_wave_scheduler.py::TestParallelDispatchProof::
    test_two_independent_tasks_overlap_at_max_parallel_two`) was observed once under
    full-suite load and passed both standalone and on an immediate full-suite re-run —
    not a regression from this task, not touched by it.
  - `uv run ruff check .` → 2 errors, both the pre-existing, untouched
    `tests/test_e2e_cli.py` findings (unrelated to this task).
  - `uv run ruff format --check .` → all files formatted.
  - `uv run mypy src/agent_orchestrator/bench` → `Success: no issues found in 11
    source files`.

## Risks / Blockers
- None outstanding. R4 (real-LLM cost/flakiness) did not materialize — both real
  subjects solved every task on the first pass at haiku.

## Next actions
1. None for this task. Downstream: `T-Tst4Ln` wraps a `[real_llm]` smoke test around
   this suite; `T-Dcs2Rk` documents it in `benchmarks/README.md` and reconciles the
   HLD/ADR to as-built (including this task's `AoWorkflowSubject`
   instruction-path-resolution design-gap finding, which the docs-refresh should fold
   into design doc §4.2's edge cases).
