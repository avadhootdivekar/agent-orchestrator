# STATUS

- ID: `E-IasNXu-parallel-execution`
- Updated At: 2026-07-15
- State: Done (5/5 tasks complete — `T-JXiI9j`, `T-j8YLGd`, `T-VSfAUN`, `T-TNleFt`, `T-EJKD6f` all Done)
- Owner: architect agent (design) → developer/tester agents (delivery)

## Epic closure (2026-07-15)
- By: Claude · Role: developer · Date: 2026-07-15
- Comment: `T-EJKD6f-docs-adr-reconcile` complete — the epic's final task. All documentation
  reconciled to the as-built implementation, verified against the merged `engine.py`/`cli.py`/
  `project_config.py` directly (not taken on faith from sibling STATUS.md summaries):
  `docs-md/parallel-execution-hld.md` Status flipped to "Implemented (as-built reconciled
  2026-07-15)" with a new §14 "As-built deviations from design" (all 6 items from the reconcile
  checklist, plus a 7th flagged-not-fixed finding); ADR-0007 Status flipped to
  "Accepted — Implemented" with a new "Implementation notes" addendum; the master HLD index
  (`hld-agent-orchestrator.md`) and the whole `docs-md/`/README/`.claude/` tree swept for stale
  "strictly serial" claims (learning #35) — 19 grep hits reviewed individually, every genuine hit
  fixed (the master HLD's own execution-flow section, the circuit-breaker HLD/LLD's evaluation-point
  notes and assumption log, and the dynamic-workflows logging HLD's "STANDING ASSUMPTION —
  single-threaded engine" block, which was the most directly falsified claim found), false positives
  left untouched. README gained a "Parallel execution" section + `AO_MAX_PARALLEL` env-var row +
  config sample line, matching the existing quota section's style. 4 durable learnings captured in
  `meta/learnings.md` (+ matching compact bullets). One genuine code-doc mismatch was found (two
  stale `max_parallel` docstrings in `engine.py`/`project_config.py` still claiming the engine
  "stays fully serial" post-T-JXiI9j) and correctly NOT fixed — flagged for a trivial code-only
  fast-follow, per this ticket's own no-production-code-edits charter. Docs-only: `uv run pytest -q`
  unchanged at 857 passed / 3 skipped; `git status --short -- src tests specs` is non-empty but
  entirely pre-existing sibling-ticket work (confirmed via session tool-call history — no
  edit/write ever touched those paths this task). Full detail: `T-EJKD6f-docs-adr-reconcile/STATUS.md`.
- **Epic outcome:** opt-in parallel task execution shipped behind `max_parallel` (default `1` =
  byte-identical serial). Final evidence: full suite **857 passed / 3 skipped**, coverage **93%**
  overall (`engine.py` 96%), `ruff check`/`ruff format --check` clean on every file any task
  touched (2 pre-existing, untouched `test_e2e_cli.py` errors throughout), `mypy src` clean on every
  touched file (4 pre-existing, untouched `_version.py` errors throughout), zero `*.schema.json`
  changes (ADR-0007 D5 held), and the `N=1` byte-identical regression gate (`tests/test_engine*.py`,
  58 tests) passed **unedited** at every single handoff from T-j8YLGd through T-TNleFt.

## Prior update (T-VSfAUN handoff)
- By: Claude · Role: developer · Date: 2026-07-15
- Comment: `T-VSfAUN-concurrency-failure-semantics` complete — closes both concrete gaps
  `T-j8YLGd` flagged (Deviations #1 and #5): the budget-wait `BLOCKED` path is now wired (drain
  while a sibling holds capacity; sleep only once nothing is in flight — R3 closed), and
  HALT/cancel now drain in-flight siblings at `N>1` (proven, not just structurally carried
  through). Verified — not rebuilt — that `_settle_completed_task`'s quota/429/self-heal requeue
  and `_drain_remaining` already matched the ticket's own pseudocode exactly as landed by
  `T-j8YLGd`; only the budget gate's internal retry loop needed an actual code change (FR-6).
  **`N=1` byte-identical gate re-asserted**: `tests/test_engine*.py` 58/58 UNEDITED; full suite
  845 passed / 3 skipped vs. the 837/3 handoff baseline (+8, zero regressions). New file
  `tests/test_wave_concurrency_semantics.py` (8 tests, re-run 13× with zero flakes) proves AC-1
  (budget cap holds at N=4, order-independent final `consumed_tokens`), AC-2 (no deadlock on a
  rolling window, proved via exact `run.log` event order), AC-3 (quota/429/self-heal requeue
  with a sibling genuinely in flight, estimate reversed exactly once), AC-4 (breaker halt drains
  the in-flight sibling, resumable without re-running it), AC-5 (cancel drains two in-flight
  siblings, resumable), and AC-6 (no thread leak, asserted per-scenario across all 4 new drain
  paths). Full detail + 4 flagged deviations: `T-VSfAUN-concurrency-failure-semantics/STATUS.md`.
- FR-6/FR-7/FR-8 and the NFR-4 half of the requirements table under concurrency are now fully
  evidence-backed. `T-TNleFt` inherits a closed R3 and a reusable `_ScriptedGatedExecutor`
  composition-wrapper pattern (gating + scripted per-call `TaskResult` outcomes) if its full
  interaction-matrix harness needs both together.

## Evidence
- Tickets: `meta/tickets/E-IasNXu-parallel-execution/{EPIC.md, T-*/TASK.md}`.
- Design: `docs-md/parallel-execution-hld.md`; ADR: `docs-md/adr/ADR-0007-parallel-task-execution.md`.
- `T-JXiI9j`: see prior update below — `max_parallel` plumbing, unchanged.
- `T-j8YLGd`: `src/agent_orchestrator/engine.py` (wave/barrier scheduler rewrite) +
  `tests/test_wave_scheduler.py` (new, 22 tests). `uv run pytest tests/test_engine*.py -q` =
  58 passed (AC-1 gate, zero test edits). `uv run pytest -q` = 837 passed / 3 skipped (+22,
  0 regressions vs. 815/3). `ruff check .`/`ruff format --check .` clean except the 2
  pre-existing `tests/test_e2e_cli.py` errors (untouched). `mypy src` clean except the 4
  pre-existing `_version.py` errors (untouched). `git status --short -- specs` empty.
- `T-VSfAUN`: `src/agent_orchestrator/engine.py` (`_prepare_and_maybe_dispatch` budget-gate
  block: `in_flight_nonempty` param + BLOCKED-drain-then-re-gate; no other engine changes
  needed) + `tests/test_wave_concurrency_semantics.py` (new, 8 tests). `uv run pytest
  tests/test_engine*.py -q` = 58 passed (N=1 gate, zero test edits, unchanged from `T-j8YLGd`).
  `uv run pytest -q` = 845 passed / 3 skipped (+8, 0 regressions vs. 837/3). New file re-run
  13× total with zero flakes. `ruff`/`ruff format` clean on touched files (same 2 pre-existing
  `test_e2e_cli.py` errors, untouched). `mypy src` clean except the same 4 pre-existing
  `_version.py` errors. `git status --short -- specs` empty; `git diff --numstat tests/` shows
  0 deletions on every existing test file.

## Risks / Blockers
- No blockers. Key risks tracked in EPIC.md (R1 N=1 regression, R2 executor thread-safety,
  R3 budget-wait deadlock, R4 breaker count nondeterminism at N>1). **R1 remains fully closed**
  (re-asserted this task: 58/58 `test_engine*.py` unedited, +8 net new tests, 0 regressions).
  R2 holds for `ClaudeCliExecutor` (unchanged) and is exercised via the shared `_GatedExecutor`
  double, reused unedited (never a competing implementation) plus a composition wrapper
  (`_ScriptedGatedExecutor`) adding scripted per-call outcomes for the requeue tests.
  **R3 (budget-wait deadlock under concurrency) is now closed** — `TestBudgetCapUnderConcurrency`
  / `TestNoBudgetDeadlockOnRollingWindow` construct and pass the exact scenario (BLOCKED drains
  instead of sleeping while a sibling holds capacity). **R4 (breaker count nondeterminism at
  N>1) remains open by design** — accepted/documented per EPIC.md and the task's own Risks
  section ("Do NOT try to force determinism by serializing failures — only barriers serialize");
  `N=1` stays deterministic.
- ASSUMPTION: `team_size = 2` for the capacity math (developer + tester). If staffing differs,
  re-run the capacity math in EPIC.md; the task decomposition is unaffected.

## Next actions
1. ~~Implement `T-JXiI9j-config-cli-env-plumbing`~~ — done 2026-07-15.
2. ~~Implement `T-j8YLGd-wave-barrier-scheduler`~~ — done 2026-07-15.
3. ~~Implement `T-VSfAUN-concurrency-failure-semantics`~~ — done 2026-07-15.
4. ~~`T-TNleFt-tests-parallel-matrix`~~ — done 2026-07-15. Gated-executor harness, `N=1` regression
   gate, interaction-matrix integration tests, CliRunner e2e all landed; `--max-parallel 0`
   handoff-line wording corrected to match shipped behavior (0 → exit 0/serial, negative → exit 1).
5. ~~`T-EJKD6f-docs-adr-reconcile`~~ — done 2026-07-15. Docs reconciled to as-built, ADR-0007
   finalized, whole-tree stale-claim sweep complete, README updated, learnings captured, epic
   closed. **Epic complete — no further action.** One small forward item recommended (not a
   blocker): a code-only fast-follow to fix two stale `max_parallel` docstrings in
   `engine.py`/`project_config.py` (flagged, not fixed, in `T-EJKD6f-docs-adr-reconcile/STATUS.md`).
