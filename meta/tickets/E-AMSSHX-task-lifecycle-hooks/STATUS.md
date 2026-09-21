# STATUS

- ID: `E-AMSSHX-task-lifecycle-hooks`
- Updated At: 2026-09-21
- State: In Progress
- Owner: dev-epic

## This update
- By: dev-epic
- Role: manager
- Date: 2026-09-21
- Comment: Early gate complete. Ran `reviewer` and `architect` in parallel against
  `docs-md/task-lifecycle-hooks-hld.md` (Rev 1) + the epic ticket, cross-checked against the real
  `engine.py`/`artifacts.py`/`bench/graders.py`/`isolation/escalation.py` code (not just the
  design prose). Both returned **"approve with changes"**.

  BLOCKING findings, all incorporated into HLD Rev 2 before any implementation code was written:
  1. (architect) Rev 1 put the hook command directly on `TaskSpec`, breaking the documented,
     already-reviewed AC-15 argv-containment invariant (`docs-md/task-isolation-hld.md`) — an
     agent-authored `emit_tasks` manifest constructs `TaskSpec` with no field allowlist, so this
     would have handed argv construction to agent-written output. Fixed: hooks moved to a
     `WorkflowSpec.hooks` named registry; `TaskSpec.pre_hook`/`post_hook` now reference a hook by
     name (`HookRef`), mirroring the existing `TaskSpec.agent` registry pattern.
  2. (reviewer + architect, same interaction from two angles) A T2 conflict-resolver dispatch
     (`mode == "resolve"`) would have silently inherited a task's hooks via the existing
     `task.model_copy(...)` in `_prepare_resolver_dispatch` — wrong agent, wrong outputs shape.
     Fixed: that copy now explicitly clears both hook fields; T3 rerun is confirmed unaffected.
  3. (architect) §7's Epic B forward-compat claim overstated what a dispatch-scoped hook
     observes (misses skipped/resumed tasks, the T2 synthetic-success path, and uncaught
     exceptions; fires before the outputs gate). Corrected in HLD §7 with an explicit
     fires/doesn't-fire list, a non-LLM/non-billable boundary, and a named "post-settlement
     hook" gap Epic B is expected to add itself.
  4. (architect) The `succeeded→failed` downgrade's second-order effects (empty self-heal
     failure summary, integration silently skipped) were unmodelled. Fixed: HLD §6 now requires
     the hook's exit code + bounded stderr tail folded into `result.error` (not a bare marker),
     and states the integration-skip consequence as an explicit, deliberate row.

  All non-blocking findings from both reviews (result-file exists-first read sequencing, DRY
  trade-off recorded not hidden, budget-reconcile-not-quite-zero caveat, cancellation race
  caveat, `attempts=0` novel-value check, `hooks.py` module extraction, `type` discriminator,
  `version` fields, typed `score` field, `stdin=DEVNULL`/bounded capture/absolute paths,
  landscape-survey confirmation of argv+exit-code as the right shape) were also incorporated —
  see `docs-md/task-lifecycle-hooks-hld.md` §11 for the full mapping of finding → fix.

  No finding required reworking the core D1-D3 shape (hooks live inside `_run_with_retries`,
  argv command, exit-code verdict) — only the spec-surface location (D4, new) and two explicit
  interaction rows.

## Evidence
- Design doc: `docs-md/task-lifecycle-hooks-hld.md` (Rev 2, §11 = review outcome)
- Early-gate reviewer transcript: agent id `a6e2599445804af56` (background agent, "Early-gate
  review of hooks HLD")
- Early-gate architect transcript: agent id `a9d1cdfb1a38475ca` (background agent, "Early-gate
  architect review of hooks HLD")
- Tickets updated to match Rev 2: `EPIC.md` (FR-1 reshaped, FR-8 added), all 7 task tickets

## Risks / Blockers
- None currently blocking. Sequencing risk noted in HLD §10: this epic should land before the
  sibling cost/caching epic (same branch thread) touches `_settle_completed_task`/`TaskResult`.

## Next actions
1. ~~Delegate implementation~~ DONE — see update below.
2. Delegate test-writing + full-suite run (T-jI3P4p) to `tester`.
3. `reviewer` pass on the implementation diff (T-6gR2ya), then late-gate e2e (T-FCC8mT).

## Update 2 — implementation landed (T-AHvmYR, T-DgheoA, T-lzQEyy, T-fbQIFX)
- By: dev-epic
- Role: manager
- Date: 2026-09-21
- Comment: Delegated the 4 implementation tasks to one `developer` agent (tightly coupled,
  same small file set — see the change-scope table). Landed as 6 commits: `316214c` (models),
  `77b2fa2` (schema+cross_validate), `4671e3d` (new `hooks.py`), `11f21e9` (engine.py wiring +
  T2 suppression in `isolation/escalation.py`), `6e067fc` (example spec+scripts), `edb39c0`
  (ticket sync). T2 conflict-resolver suppression (the BLOCKING early-gate finding) implemented
  inside `isolation/escalation.py::build_resolver_dispatch`'s existing `task.model_copy(...)`
  — the smallest correct location, confirmed by direct read.

  **Independently re-verified (not just trusting the developer's report — read every diff
  hunk and re-ran the checks myself):**
  - `git diff --stat 02043ec..HEAD` — 19 files changed, matches the Rev 2 change-scope
    boundary table exactly (no out-of-scope file touched).
  - Read `hooks.py` in full, the `engine.py`/`models.py`/`spec.py`/`isolation/escalation.py`
    diff hunks in full, and both example hook scripts in full — all match the reviewed design
    (D1-D4, §5-6 failure semantics, the exact 2-of-4 early-return wrapping, the T2 suppression,
    exit-code-is-truth with exists()-first result-file sequencing).
  - Re-ran myself (not reusing the developer's numbers): `uv run ruff check .` → "All checks
    passed!"; `uv run ruff format --check .` → "293 files already formatted"; `uv run python -c
    "from agent_orchestrator import models, hooks, engine, spec"` → ok; `uv run ao validate
    --workflow specs/examples/workflow-hooks.json --reposets specs/examples/reposet.json
    --agents specs/examples/agents.json` → `OK: all specs valid`; `uv run mypy
    src/agent_orchestrator/` → 4 pre-existing errors, all in untouched `_version.py`, zero in
    any file this epic touched.
  - Independently confirmed the one pre-existing full-suite failure
    (`test_nfr2_regression_gate.py`) predates this epic: `git diff b849b7c b0cb467 --stat --
    tests/test_e2e_builtin_routed_runner.py` shows commit `b0cb467` ("Ad/task isolation
    (#11)", the branch's own starting point, landed before `83db1fc` — this epic's first
    commit) already reformatted that pre-epic test file. Not a regression introduced by this
    epic.
  - Full-suite `pytest -q` re-run by dev-epic is in progress; result recorded once complete.

## Risks / Blockers (updated)
- None found in review of the diff. Full-suite pass count to be confirmed in the next update.

## Implementation landed (T-AHvmYR, T-DgheoA, T-lzQEyy, T-fbQIFX)
- By: developer
- Role: developer
- Date: 2026-09-21
- Comment: All four implementation tasks landed on `ad/cost-perf-hooks-skills` per the
  Rev 2 change-scope table (HLD §10), each in its own commit: `316214c` (models),
  `77b2fa2` (schema + cross_validate), `4671e3d` + `11f21e9` (hooks.py + engine wiring),
  `6e067fc` (example spec + hook scripts). Every AC in all four task tickets is implemented
  and independently verified (see each ticket's own STATUS.md "Implementation complete"
  section for the exact evidence per task) -- including both BLOCKING early-gate findings
  (AC-15 containment via the `WorkflowSpec.hooks` registry, and the T2
  conflict-resolver-dispatch hook suppression in `isolation/escalation.py`).

  `ruff check .` / `ruff format --check .` clean repo-wide. `mypy src/agent_orchestrator/`
  has exactly 4 pre-existing errors, all in `_version.py` (untouched by this epic, unrelated
  type-narrowing issue predating this work). Full suite (`pytest -q`): 3830 passed, 8
  skipped, 1 failed. The 1 failure
  (`tests/test_nfr2_regression_gate.py::TestPreEpicTestsUnedited::
  test_every_pre_epic_test_file_is_byte_identical_to_its_pre_epic_content`) is PRE-EXISTING
  and unrelated to this epic: it flags `tests/test_e2e_builtin_routed_runner.py` as
  reformatted relative to the gate's base-branch reference commit, but `git diff --stat
  b849b7c 02043ec -- tests/test_e2e_builtin_routed_runner.py` shows that reformatting was
  introduced by commit `b0cb467` ("Ad/task isolation (#11)"), which landed on this branch
  BEFORE this epic's ticket-scaffolding commit (`83db1fc`) and well before any of this
  epic's 5 implementation commits -- none of which touch anything under `tests/`. Flagging
  for `T-6gR2ya`/whoever owns branch hygiene rather than silently working around it.

## Remaining scope
- T-jI3P4p (unit/integration tests), T-FCC8mT (late-gate e2e), T-6gR2ya (review/hardening)
  are unchanged, separately delegated tasks -- not attempted here.
