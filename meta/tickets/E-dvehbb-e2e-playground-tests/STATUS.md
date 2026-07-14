# STATUS

- ID: `E-dvehbb-e2e-playground-tests`
- Updated At: 2026-07-02
- State: MVP complete (Phase 1) — Phase 2 pending
- Owner: architect

## Update (2026-07-02 — real-LLM tier hardened green; framework agent-cwd added)
- Drove the gated real-LLM tier (`T-g7rjh0`) from flaky to reliably green against the real
  `claude`. Three distinct, order-revealed failures were RCA'd from captured stdout/stderr
  (`subtype`/`num_turns`/`permission_denials`/`terminal_reason`) + workspace file checks:
  1. `architect-design` `error_max_turns` — `effort:medium`→`--max-turns 5` too tight (NOT
     permissions; `permission_denials:[]`). Fix: `EFFORT_MAX_TURNS` → `{15,30,60}` (turns are a
     loop-breaker, token budget is the real cost guard) + explicit `AgentSpec.max_turns` override
     + `ao run --max-turns` flag + `MAX_TURNS`/`AO_MAX_TURNS` plumbing.
  2. `taskreview-t1` missing `review.md` — reviewer on `acceptEdits` silently denies the Bash
     `pytest` its instruction requires. Fix: all playground agents → `bypassPermissions`.
  3. `architect-breakdown` manifest "not found" — `subprocess.run` had no `cwd`, so `claude`
     inherited repo-root cwd and the relative manifest write escaped the workspace. Fix: **new
     framework capability** — `AgentSpec.working_dir` → engine resolves under `workspace_root`
     (path-guarded) → `TaskContext.cwd` → executor `subprocess.run(cwd=…)`; default = workspace root.
- **NFR-1 note:** fix #3 deliberately touches `src/` (`models.py`, `engine.py`,
  `executors/claude_cli.py`, `cli.py`), crossing this epic's original "no production `src/` changes"
  boundary — per user direction the working-directory contract belongs in the framework (config-driven,
  honored by `ao` commands), not patched at the test level.
- Validation: real-LLM suite `3 passed in 578s` (`PYTEST_EXIT=0`, no pipe-masking); manifest now
  lands in the workspace; fast suite `387 passed` (+4 cwd/max-turns unit+integration tests); `mypy`
  clean; zero new lint. Playground `agents.claude.json` pinned to `bypassPermissions` for all 5 agents.
- Docs: `docs-md/lld-agent-orchestrator.md` (AgentSpec `model`/`effort`/`max_turns`/`working_dir`,
  TaskContext `cwd`) + `docs-md/e2e-playground-testing.md` §12.5 (RCA table).

## Update (2026-07-01 — real-LLM tier made runnable against real `claude`)
- Fixed a directory-access blocker in `T-g7rjh0`: real-LLM tests ran in pytest `tmp_path`
  (system `/tmp`), outside the spawned `claude` subprocess's sandbox allow-list (the repo
  working dir), so agents could not read instructions / write outputs. Moved the real-tier
  workspace into a repo-local, gitignored dir (`playground/.tmp/`, new `real_llm_workspace`
  fixture) and added `--permission-mode acceptEdits` to `agents.claude.json`. No `src/` change.
- Verified against the REAL `claude` interface (`AO_E2E_REAL_LLM=1`): all 3 real-LLM tests pass
  (smoke `status==succeeded`; spine outputs + control files well-formed). Default suite unchanged:
  377 passed, 3 skipped. See `T-g7rjh0/STATUS.md` for RCA + evidence.

## This update (2026-07-01 — MVP delivered & verified)
- Phase-1 MVP complete: {T-7592ux, T-1vuzyi, T-ee8hzo, T-r21p4y, T-g7rjh0} all Done.
- Delivered: `playground/sum-of-array/` (workflow spine + emit_tasks fan-out + LoopSpec review round, instructions, both agents files, control-file + expected-structure fixtures); `tests/playground/` scaffold, gating conftest (marker `real_llm` + `AO_E2E_REAL_LLM`), harness (`copy_example`/`run_cli`/`seed_control_files`/`agents_for`/`load_expected`/`assert_tree`); three tiers — fixture (19 tests), deterministic 6-area (20 tests), gated real-LLM (3 tests, skipped by default).
- Independently verified (orchestrator, not just subagent self-report): `ao validate` OK; statically-expanded DAG acyclic; full e2e smoke run through the CLI succeeds (1-round 10 tasks; 2-round 12 tasks incl. `__iter2` loop clones); `expected_paths` present; `task.start` order matches; 10/10 (12/12) stdout+stderr capture dirs; `origin` fields static/injected/loop; token accounting deterministic (`consumed_tokens=15200`, identical across runs).
- Fixture defect found & fixed during verification: `expected_events.json` claimed `loop.iterate` on the single-round path, but the engine emits `loop.iterate` only when the gate says *continue* (`engine.py`); split into verified `one_round`/`two_round` scenarios.
- Test results: `uv run pytest -q tests/playground` → 39 passed, 3 skipped; full suite → 377 passed, 3 skipped; `ruff check .` adds zero new errors; `src/` untouched (NFR-1 honored).

## Prior update (2026-07-01 — epic drafted)
- Architecture package authored: `EPIC.md` (requirements, design facts, tiers, area matrix, ADR summary, sprint plan) + 9 task tickets + design doc `docs-md/e2e-playground-testing.md`.
- Central design pivot recorded: the CLI's `DispatchExecutor` builds a payload-less `FakeExecutor`, so the deterministic tier **pre-seeds control files** (task manifest / loop gate verdicts) as fixtures; the same spec is generated live by real agents in the gated tier (ADR-002).
- Per-example workflow shape locked: static spine + `emit_tasks` fan-out (unique paths) + `LoopSpec` review round, using only already-tested engine paths (ADR-001).
- MVP boundary defined: {T-7592ux, T-1vuzyi, T-ee8hzo, T-r21p4y, T-g7rjh0} — independently shippable, always green, zero default token burn.
- No production `src/` changes in this epic (NFR-1); any engine gap is a separate ticket.

## Evidence
- `meta/tickets/E-dvehbb-e2e-playground-tests/EPIC.md`
- `meta/tickets/E-dvehbb-e2e-playground-tests/T-*/TASK.md` (9 tasks) + per-task `STATUS.md`
- `docs-md/e2e-playground-testing.md`

## Regression baseline
- Pre-MVP full suite: 338 passed. Post-MVP: 377 passed, 3 skipped (+39 new playground tests, +3 gated-skipped). Zero pre-existing test flips — additive only.
- Pre-existing (NOT this epic): `tests/test_e2e_cli.py` carries 6 `ruff` errors + 1 format issue committed in `cd0641e`; unchanged by this work. Flagged for a separate cleanup.

## Risks / Blockers
- R1: CLI cannot drive FakeExecutor payloads → mitigated by control-file pre-seeding (ADR-002); escalate to a separate engine ticket only if pre-seeding is insufficient.
- R2: shared artifact paths → spurious DAG cycles → mitigated by unique per-task paths + a fixture-tier acyclicity assertion.
- R3: real-LLM flake / burn → double gate (marker + env), content never asserted.

## Next actions (Phase 2 — non-MVP)
1. `T-92o31p-perf-correctness-tier` — performance envelope (Fake, always) + gated correctness spot-check (real).
2. `T-n477z9-sorting-example` and `T-94tepb-student-data-example` — replicate the proven `sum-of-array` template + parametrize the deterministic harness across examples.
3. `T-5bh03j-docs-refresh` — reconcile `docs-md/e2e-playground-testing.md` + `playground/README.md` against shipped behavior (note the `expected_events.json` one_round/two_round split and the `loop.iterate`-only-on-continue fact).
