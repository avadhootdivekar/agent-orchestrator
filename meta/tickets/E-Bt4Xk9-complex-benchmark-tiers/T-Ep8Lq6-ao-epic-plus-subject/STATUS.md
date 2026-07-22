# STATUS

- ID: `T-Ep8Lq6-ao-epic-plus-subject`
- Updated At: 2026-07-22
- State: **Done**
- Owner: developer agent

## This update
- By: developer agent
- Role: developer
- Date: 2026-07-22
- Comment: Implemented the `ao-epic-plus` 4-agent (plan→implement→review→fix)
  `ao_workflow` bench subject — all new files, no existing file touched except the new
  test file. Uniform-model (no per-agent `model`), matches the `ao-epic` reference
  shape (schema version fields, placeholder-rewrite convention, `working_dir`/
  `permission_mode` usage). Real one-task smoke run completed end-to-end on
  `dev-core`/`bugfix-off-by-one` with `ao-epic-plus-haiku`: solved, $0.2463, all 4
  workflow tasks (plan/implement/review/fix) succeeded.

## Files created (all NEW, exclusive ownership)
- `benchmarks/subjects/ao-epic-plus/workflow.json` — 4-task DAG.
- `benchmarks/subjects/ao-epic-plus/agents.json` — planner/developer/reviewer/fixer.
- `benchmarks/subjects/ao-epic-plus/reposet.json` — mirrors `ao-epic`'s reposet.
- `benchmarks/subjects/ao-epic-plus/instructions/{plan,implement,review,fix}.md`.
- `benchmarks/subjects/ao-epic-plus-sonnet.json` (model `claude-sonnet-5`).
- `benchmarks/subjects/ao-epic-plus-haiku.json` (model `claude-haiku-4-5-20251001`).
- `tests/bench/test_ao_epic_plus_subject.py` — 11 new deterministic tests.

## Evidence (AC-by-AC)
- **AC1** (`ao-bench validate --subject ...` → OK, type `ao_workflow`): verified via
  direct CLI invocation for both `ao-epic-plus-sonnet.json` and `-haiku.json` → `OK`
  / `type='ao_workflow'`; asserted in
  `test_validate_cli_accepts_ao_epic_plus_{sonnet,haiku}_subject`.
- **AC2** (4-task DAG `plan`→`implement`→`review`→`fix`, `repo/INSTRUCTION.md`
  instruction, artifact-wired inputs/outputs, `working_dir: repo`, bypassPermissions):
  verified via `spec.load_workflow` + `spec.cross_validate` against the real core
  loaders (same as `ao validate`) in
  `test_ao_epic_plus_workflow_assets_cross_validate`,
  `test_ao_epic_plus_workflow_task_agent_mapping_and_dependency_chain`,
  `test_ao_epic_plus_workflow_artifacts_wire_the_verify_repair_chain` — confirms
  `review` consumes `implement`'s `output/impl.md` and `fix` consumes `review`'s
  `output/review.md` (a real verify/repair chain, not 4 independent turns).
- **AC3** (uniform-model, no per-agent `model`, model flows via `AO_MODEL`): grep-style
  assertion over `agents.json` in `test_ao_epic_plus_agents_pin_no_per_agent_model`
  (asserts `"model" not in agent_spec` for all 4 agents) +
  `test_ao_epic_plus_subjects_load_via_load_subject` confirms `sonnet`/`haiku` subject
  configs carry `model` only at the subject level and both point at the identical
  workflow/agents/reposet templates. `bench/subjects.py`'s `AoWorkflowSubject` (existing,
  unmodified) is what threads `spec.model` → `AO_MODEL` env var — confirmed by reading
  its `run()` implementation; the real smoke run's `state.json` shows all 4 tasks ran
  under the one subject-level model with no per-agent override recorded.
- **AC4** (`agents.json` prompt_template / `instructions/*.md` kept in sync,
  documented): each `instructions/{plan,implement,review,fix}.md` carries an explicit
  note that `agents.json`'s `prompt_template` is authoritative (mirrors `ao-epic`'s
  `implement.md`/`verify.md` convention) — asserted in
  `test_ao_epic_plus_instructions_mirror_and_document_agents_json_authority`.
- **AC5** (opt-in real run, adapted — dev-medium doesn't exist yet): ran ONE `dev-core`
  task end-to-end via `uv run ao-bench run --suite benchmarks/suites/dev-core/suite.json
  --subject benchmarks/subjects/ao-epic-plus-haiku.json --task bugfix-off-by-one
  --out-dir <scratch>` (CLI's own `--task` filter used instead of a suite copy, per the
  ticket's own fallback instruction). Result: **exactly one run dir**
  (`ao-epic-plus-20260722T153655Z` under the ephemeral workspace's
  `.orchestrator/runs/`), `run.json` records real `cost_usd=$0.24626640000000002` and
  real token counts (input 232 / output 9397 / cache_creation 55649 / cache_read
  836384), **all 4 workflow tasks executed and succeeded**
  (plan $0.0620 / implement $0.0751 / review $0.0643 / fix $0.0449, 1 attempt each),
  task **solved=true** (pytest grader: 3/3 passed). Total real spend **$0.2463**, well
  under the $2 smoke cap. `benchmarks/results/` was never written to (explicit
  `--out-dir` to a scratch dir); the generated `playground/.tmp/bench/...` gitignored
  workspace copy was deleted after recording these numbers — repo left clean
  (`git status` confirmed no diff under `benchmarks/results/` or `playground/`).
- **AC6** (reviewer/fixer run visible tests, never modify grading/check scripts): both
  prompt_templates explicitly instruct re-running `pytest`/`uv run pytest -q` and
  explicitly forbid modifying `check.py`/`check_tests.py`/the instruction file —
  asserted in `test_reviewer_and_fixer_prompts_require_running_tests_and_forbid_grading_edits`
  and `test_code_touching_agents_forbid_modifying_instruction_or_grading_scripts`
  (developer/reviewer/fixer; `planner` is exempt by design — it makes no code changes).

## Verification run (all green, zero regressions)
- `uv run pytest tests/bench -q` → **267 passed, 1 skipped** (was 256 passed, 1 skipped
  before this task's 11 new tests).
- `uv run pytest -q -m "not real_llm"` → **1124 passed, 4 deselected** (0 regressions;
  deselected count matches the pre-existing `real_llm`-marked tier, unchanged).
- `ruff check` / `ruff format --check` on the new test file → clean.
- `mypy src tests/bench/test_ao_epic_plus_subject.py` → 0 new errors (the file adds
  none; the only errors present are 4 pre-existing, unrelated `src/agent_orchestrator/
  _version.py` errors from before this task, outside this task's scope — standalone
  `mypy tests/bench/test_ao_epic_plus_subject.py` alone spuriously reports
  "missing py.typed marker" for every `agent_orchestrator.*` import because the file
  isn't given the package's source root in that invocation; CI itself only runs
  `mypy src`, so this is a known mypy-single-file artifact, not a real issue).
- `git status` at completion shows only this task's exclusively-owned new files
  (plus one unrelated pre-existing untracked doc from before this session) — no
  existing file touched except the new test file, as required.

## Risks / Blockers
- None outstanding. Dependencies: none (Wave A; all new files; independent of the
  parallel `T-Tr1Km8` tier-model work, which landed as its own commit `c66ae68`
  during this task without touching any file this task owns.
- Forward note for `T-Md7Vc3`/PLAN Run 1: this subject is tuned to *not* saturate a
  trivial task cheaply (~$0.25/task on `dev-core`'s easiest bugfix at haiku, ~2x
  `ao-epic`'s 2-agent cost) — it is meant to earn back its overhead via solve rate on
  `dev-medium`'s harder, multi-file tasks (PLAN Run 1 uses `ao-epic-plus-sonnet`),
  not on `dev-core`.

## Next actions
None — task complete.
