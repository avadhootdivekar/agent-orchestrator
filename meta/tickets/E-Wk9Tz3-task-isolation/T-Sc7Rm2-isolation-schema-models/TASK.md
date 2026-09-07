# TASK: T-Sc7Rm2-isolation-schema-models

## Metadata
- Task ID: `T-Sc7Rm2-isolation-schema-models`
- Epic ID: `E-Wk9Tz3-task-isolation`
- Owner: developer-agent
- Created: 2026-09-06
- Last Updated: 2026-09-07
- Status: In Review
- Estimate: 2 days

## Requirements Mapping
- Requirement IDs: FR-1, FR-7 (config surface), FR-10 (field), FR-15 (state), NFR-5 · Design: HLD §10,
  §11 M2
- Review findings folded in: **S-2**, **S-3**, **S-5**, **S-6** (security fields), **R-1b**
  (`BudgetCounters` cycle keying), **R-21** (`TaskRunState.dispatch_cycle`), **R-5**
  (`resolve_overlap_preference`), **R-3** (state the `should_skip` gate must read), **R-4**
  (`workspace_lock` reserved), **R-14** (V8 wording). This task carries **every schema/model field the
  later fixes need**, so the schema is written once and not reopened mid-epic.

## Description
Add every new field this epic needs to the pydantic models, the JSON Schema, the run state and the
cross-validator — **inert**: nothing consumes them yet. Landing this first unblocks every other task
and keeps the schema change in one reviewable diff.

The review pass added a second purpose: several later fixes (cost accounting across a requeue,
transcript capture across a requeue, the resolver's tool policy, the auto-commit secret screen,
rerere-tier visibility, the multi-run policy) each need a field. They are all specified here so the
schema lands **once**.

Files you own:
- `src/agent_orchestrator/models.py` (edit — new Literals and constants, `TaskSpec.isolation`/`touches`,
  `WorkflowDefaults.isolation`, `IntegrationSpec`, `ResolverConfig`, `RegenerateRule`,
  `SchedulingSpec`, `WorkflowSpec.integration`/`scheduling`, `TaskContext.env`,
  `TaskRunState.dispatch_cycle`, `BudgetCounters.reconciled_cycles`, `TaskIntegrationState`,
  `RunIntegrationState`, `RunState.integration`/`task_integration`, `resolve_task_isolation`,
  `resolve_overlap_preference`)
- `specs/workflow.schema.json` (edit)
- `src/agent_orchestrator/spec.py` (edit — cross-validation rules V1-V12 only)
- `src/agent_orchestrator/runstate.py` (edit — `prepare_resume` preservation + `write_status`
  additions only; `should_skip` stays with `T-En8Hd4`)
- `tests/test_isolation_models.py`, `tests/test_isolation_spec_validation.py` (new)

Do NOT touch: `engine.py`, `budget.py` (the `reconcile` **logic** change is `T-En8Hd4`'s; you add only
the model field it will use), `artifacts.py`, `cli.py`, `executors/`, `templates/`, or any `isolation/`
module.

## Acceptance Criteria

### Models and constants
1. Named constants, no magic literals: `DEFAULT_VERIFY_TIMEOUT_SECONDS = 1800`,
   `DEFAULT_INTEGRATION_LOCK_TIMEOUT_SECONDS = 1800`, `DEFAULT_REGENERATE_TIMEOUT_SECONDS = 120`,
   `DEFAULT_HOTSPOTS_PATH = ".ao/hotspots.json"`,
   `DEFAULT_RESOLVER_DISALLOWED_TOOLS = ["WebFetch", "WebSearch"]`, and `DEFAULT_COMMIT_DENYLIST`
   (`.env`, `.env.*`, `*.pem`, `*.key`, `*.p12`, `id_rsa*`, `id_dsa*`, `id_ecdsa*`, `id_ed25519*`,
   `*credentials*.json`, `*.kdbx`).
2. Models exactly as HLD §10.2. Defaults: `TaskSpec.isolation = "inherit"`,
   `WorkflowDefaults.isolation = "none"`, `IntegrationSpec.ladder =
   ["auto","mechanical","llm","rerun"]`, `auto_commit = True`, `on_denylisted_path = "fail"`,
   `resolver_deny_push = True`, `workspace_lock = "require"`.
3. `TaskContext.env: dict[str, str] = {}` — the executor overlay `T-En8Hd4` needs (today
   `ClaudeCliExecutor` passes no `env=` at all, so there is no injection point without this).

### S-2 / S-3 / S-5 / S-6 security fields
4. **S-2:** `IntegrationSpec.resolver_disallowed_tools: list[str] = DEFAULT_RESOLVER_DISALLOWED_TOOLS`
   and `resolver_deny_push: bool = True`. Both are documented in the model docstring **and** the JSON
   Schema description as *force-injected at dispatch, UNIONed with the named agent's own
   `disallowed_tools`* — closed by construction, not by prompt text (cf. ADR-0005). A test asserts the
   default is non-empty (an empty default would silently reopen the hole V11 exists to close).
5. **S-3:** `IntegrationSpec.auto_commit: bool = True`, `commit_denylist: list[str] =
   DEFAULT_COMMIT_DENYLIST`, `on_denylisted_path: Literal["fail","warn","allow"] = "fail"`. Docstring
   states the screen applies to paths that are **untracked at auto-commit time** only — an
   already-tracked file is the repo author's decision, not the engine's.
6. **S-6:** `RegenerateRule.timeout_seconds: int = DEFAULT_REGENERATE_TIMEOUT_SECONDS`, with a
   docstring naming why it exists (the command runs while the per-repo integration lock is held, so
   without its own bound a hung command stalls every other task on that repo until
   `lock_timeout_seconds`).
7. **S-5:** `RunIntegrationState.tier_counts: dict[str, int] = {}` (keys `auto`/`mechanical`/`llm`/
   `rerun`) so a run's free-tier — specifically **rerere** — resolution volume is visible without a new
   field later. `write_status` surfaces it (AC-13).

### R-1b / R-21 — the two fields the requeue fixes require
8. **R-21:** `TaskRunState.dispatch_cycle: int = 0`, documented as *incremented by the main thread
   once per dispatch, monotonic across requeues and resumes*. The docstring must state plainly why
   `TaskRunState.attempts` cannot serve: it is **assigned** from `result.attempts` (the per-call loop
   count), not accumulated, so it does not increase monotonically across calls.
9. **R-1b:** `BudgetCounters.reconciled_cycles: list[str] = []`, holding `"<task_id>#<dispatch_cycle>"`.
   Docstring records the defect it exists to fix: `DefaultBudgetManager.reconcile()` latches one-shot
   per `task_id` and runs at the **first** settle of a completed dispatch — before the conflict outcome
   is known — so every later T2/T3 reconcile for the same task is silently a no-op, and
   `reverse_estimate()` does not un-latch it. Existing `reconciled_tasks` is left in place untouched
   (backward compat); the logic switch is `T-En8Hd4`'s.

### R-4 / R-5 — reserved policy field and the derived-default resolver
10. **R-4:** `IntegrationSpec.workspace_lock: Literal["require","skip_sync","off"] = "require"`, with a
    docstring stating the semantics are fixed by `T-En8Hd4` and that this coordinates with
    `E-Sc9Rt4`'s per-workspace concurrency cap. `RunIntegrationState.workspace_lock_held: bool = False`.
    The field is reserved here **only** so the schema is not reopened; this ticket implements no
    behaviour for it.
11. **R-5:** `resolve_overlap_preference(workflow) -> Literal["off","soft"]` exists in `models.py` and
    is the **single** place §9.1's derived default is computed: an explicit
    `scheduling.overlap_preference` wins; otherwise `"soft"` iff any task resolves to
    `isolation == "worktree"`, else `"off"`. Table-driven test including the case that matters for
    NFR-2: a workflow with no isolation anywhere resolves to `"off"`.
12. `resolve_task_isolation(task, workflow)` implements HLD §11 M2: `emit_tasks`, a router task, or a
    loop-gate task always resolves to `"none"` (with a single warning when the task explicitly asked
    for `"worktree"`); `"inherit"` takes `workflow.defaults.isolation`; otherwise the task's own value.
    Table-driven test over all 3 x 4 combinations.

### Schema, validation, state
13. `specs/workflow.schema.json` accepts every new field and **rejects** unknown ones
    (`additionalProperties: false` preserved at every level). One test loads a spec using **all** new
    fields; another asserts an unknown field is rejected.
14. Cross-validation rules **V1-V12** from HLD §10.4 are implemented in `spec.cross_validate` with
    fatal vs warning exactly as tabled, each with its own test:
    V1 `strategy: "merge"` → fatal (reserved); V2 `"llm"` in ladder without `resolver_agent` → fatal;
    V3 unknown `resolver_agent` → fatal; V4 structural task asking for `worktree` → warning;
    V5 `integration` configured but nothing isolated → warning; V6 `touches` absolute or containing
    `..` → fatal; V7 `max_resolver_attempts > 0` with `"llm"` absent → warning;
    V8 a `RepoRef` path inside a **pre-existing, on-disk** git worktree of another member → fatal
    (probed at validate time — ao's own worktrees do not exist yet, so this rule can only ever be about
    foreign checkouts; R-14, and the wording in code/tests must say so);
    V9 a task id sanitizing to the reserved component `integration` → fatal;
    **V10** `"llm"` in the ladder while the named `resolver_agent`'s own `disallowed_tools` does not
    already contain every entry of `resolver_disallowed_tools` → **warning** (the dispatch force-injects
    the union anyway, so this is a "your spec is misleading" notice, not a hole);
    **V11** `resolver_disallowed_tools == []` while `"llm"` is in the ladder → **fatal**;
    **V12** any `commit_denylist` / `touches` / `resolvers.union` glob that is absolute or contains
    `..` → fatal.
15. **Command/argv containment test (S-2 adjacent, confirmed by the security gate).** Assert that
    `verify_command`, `resolvers.regenerate[].command`, `commit_denylist` and every `resolver_*` field
    exist **only** on `WorkflowSpec.integration` and **not** on `TaskSpec` — so an agent-authored
    `emit_tasks` manifest (which `read_task_manifest` parses into `TaskSpec` alone) can contribute a
    mode enum and advisory globs but nothing that executes. Implement as an explicit field-set
    assertion over `TaskSpec.model_fields`, so a future edit that moves a command field onto `TaskSpec`
    fails this test.
16. **NFR-5 both directions**, each with an explicit test: (a) a `state.json` captured before this
    change loads and yields `RunState.integration.active is False`, `task_integration == {}`,
    `dispatch_cycle == 0`, `reconciled_cycles == []`; (b) a new `RunState` round-trips identically;
    (c) a workflow JSON with no new keys produces the documented defaults.
17. `prepare_resume` **preserves** `state.integration` and `state.task_integration` verbatim, and
    normalizes any `task_integration[tid].status == "integrating"` to `"pending"` while keeping `mode`
    and `dispatch_cycle`. Test: build a state with one `integrating` task, resume, assert
    status/mode/cycle.
18. `write_status` emits the top-level `integration` block (`active`, `branch`, `heads`,
    `tier_counts`, and counts of `integrated`/`conflict`/`failed`) and per-task `integration_status`,
    `tier_reached`, `conflicted_count`, `dispatch_cycle`. A test asserts the **exact** key set so a
    later dashboard change cannot drift.
19. An `emit_tasks` manifest entry carrying `isolation` and `touches` survives
    `artifacts.read_task_manifest`'s `TaskSpec(**t)` construction unchanged (test with a real manifest
    file).
20. A loop-body clone (`engine._clone_body`, `__iter` ids) carries `isolation`/`touches` forward.
    Review confirmed this already works structurally — `_clone_body` uses
    `base.model_copy(deep=True, update={...})` and pydantic carries every non-overridden field — so
    **no `engine.py` edit is needed**. Add a test that **pins** the behaviour (so a future refactor of
    `_clone_body` cannot silently drop the fields) and state in your handoff that no engine change was
    required.
21. `uv run pytest -q` fully green with a recorded before/after count; `ruff` clean; `uv run mypy src`
    zero new errors. **No behaviour change**: the pre-existing engine suite passes unedited.

## Risks
- `additionalProperties: false` means a partial schema edit silently breaks every spec that uses a new
  field. Mitigation: AC-13's accept/reject pair.
- `specs/*.schema.json` is **not packaged into the wheel** and `config._validate_against_schema`
  silently no-ops when the file is absent — so for an installed `ao`, pydantic and `cross_validate` are
  the only gates. Every rule must exist in Python, not only in JSON Schema. State this explicitly in
  your handoff.
- `prepare_resume` currently replaces non-terminal `TaskRunState` objects wholesale
  (`runstate.py:212`); putting integration data on `TaskRunState` would silently lose it. That is why
  it lives in `RunState.task_integration`. Do not "simplify" it back. `dispatch_cycle` **is** on
  `TaskRunState` and would therefore be reset — AC-17 must preserve it explicitly.
- Reserving `workspace_lock` without semantics risks it shipping as a dead field if Phase 2 changes
  approach. Accepted deliberately: a reserved enum is far cheaper than a second schema migration, and
  `T-En8Hd4` owns making it real.

## Dependencies
- Upstream: none (parallel with `T-Gt4Pw8`). Both review gates confirm this task is unblocked.
- Downstream: every other task in the epic. `T-En8Hd4` (dispatch_cycle, reconciled_cycles,
  workspace_lock, resolve_overlap_preference), `T-Ib5Qy9` (auto_commit/commit_denylist/
  on_denylisted_path), `T-Lr6Ka3` (resolver_*), `T-Rm2Lx7` (regenerate timeout), `T-Ov9Bt5`
  (overlap_preference), `T-Cx4Jf1` (tier_counts, status.json keys).

## Pseudocode / Algorithm
```text
HLD §10.1 (JSON Schema), §10.2 (models + constants + resolve_overlap_preference), §10.3 (run state,
incl. the dispatch_cycle and reconciled_cycles rationale), §10.4 (validation V1-V12), §11 M2.
Those are the implementation contract, field for field.
```

## Schemas / Interface Notes
- Interface / API (**locked**): `resolve_task_isolation(task, workflow) -> Literal["none","worktree"]`,
  `resolve_overlap_preference(workflow) -> Literal["off","soft"]`.
- Spec / data schema: `specs/workflow.schema.json` `$defs/integration`, `$defs/scheduling`, plus
  `isolation`/`touches` on `$defs/task` and `isolation` on `defaults` — HLD §10.1 verbatim.
- Triggers / events: none.
- Artifacts: `status.json` key additions (AC-18).

## Handoff Boundary
- Upstream: none.
- Downstream: publish the final model field names, defaults and the two resolver-function signatures in
  `STATUS.md`. `T-En8Hd4`, `T-Ib5Qy9`, `T-Lr6Ka3`, `T-Rm2Lx7`, `T-Ov9Bt5` and `T-Cx4Jf1` all read the
  merged `models.py`, not this ticket. Explicitly confirm whether `dispatch_cycle` survived
  `prepare_resume` as specified, since `T-En8Hd4`'s transcript-capture and budget-cycle fixes depend on
  it.

## Artifacts
- Docs/comments: `meta/tickets/E-Wk9Tz3-task-isolation/T-Sc7Rm2-isolation-schema-models/`
- Large outputs: none

---
- By: architect · Role: architect · Date: 2026-09-07 · Comment: Phase-1 amendment after the design +
  security gates. Added every schema/model field the later fixes need so the schema lands once:
  S-2 (`resolver_disallowed_tools`/`resolver_deny_push` + V10/V11), S-3 (`auto_commit`/
  `commit_denylist`/`on_denylisted_path`), S-5 (`tier_counts`), S-6 (`regenerate[].timeout_seconds`),
  R-21 (`TaskRunState.dispatch_cycle`), R-1b (`BudgetCounters.reconciled_cycles`), R-4
  (`workspace_lock`, reserved only), R-5 (`resolve_overlap_preference` as the single derived-default
  site), R-14 (V8 wording corrected to "pre-existing, on-disk" worktrees). Added AC-15, a structural
  test that no command/argv field can ever live on `TaskSpec`, since that is what keeps an
  agent-authored `emit_tasks` manifest non-executing. AC-20 downgraded from "verify and maybe fix" to
  "pin with a test" — review confirmed `_clone_body`'s `model_copy` already carries the new fields, so
  no `engine.py` edit is needed. Estimate unchanged at 2 days: all additions are declarative fields
  plus table-driven tests; no new logic beyond two pure resolver functions.
- By: developer-agent · Role: developer · Date: 2026-09-07 · Comment: Implemented AC-1..AC-21.
  `models.py`: named constants + `IsolationMode`/`WorkflowIsolation`/`ResolverTier`/
  `OverlapPreference`; `TaskSpec.isolation`/`touches`; `WorkflowDefaults.isolation`; `RegenerateRule`,
  `ResolverConfig`, `IntegrationSpec`, `SchedulingSpec`; `WorkflowSpec.integration`/`scheduling`;
  `TaskContext.env`; `TaskIntegrationState`, `RunIntegrationState`; `TaskRunState.dispatch_cycle`;
  `BudgetCounters.reconciled_cycles`; `RunState.integration`/`task_integration`;
  `resolve_task_isolation`/`resolve_overlap_preference` (+ shared private helpers
  `_is_structural_task`/`_declared_isolation` so cross_validate's V4 can't independently drift from
  the dispatch-time resolver). `specs/workflow.schema.json`: task `isolation`/`touches`, defaults
  `isolation`, root `integration`/`scheduling` + their `$defs`, all `additionalProperties: false`.
  `spec.py`: `cross_validate` now also calls `_cross_validate_isolation` implementing V1-V12 —
  **one deliberate design correction versus the HLD's literal table**: since
  `IntegrationSpec.ladder`'s own default already includes `"llm"`, evaluating V1/V2/V3/V7/V8/V10/V11
  unconditionally would fatal on `resolver_agent` for every workflow that isolates nothing (breaking
  NFR-2/NFR-5 for the entire existing corpus — caught by the full suite going from 118/128 to 0
  failures once fixed). Those seven rules are now gated on "at least one task resolves to
  isolation='worktree'"; V4/V6/V9/V12 (task-level/glob-hygiene) run unconditionally; V5 alone warns
  when `integration` is configured but inactive. Warning-severity rules (V4/V5/V7/V10) use
  `logger.warning` (matching `dag.build_dag`'s existing non-fatal-notice convention) rather than a
  returned list, since this task does not touch `cli.py`. `runstate.py`: `write_status` emits the
  `integration` block + per-task `integration_status`/`tier_reached`/`conflicted_count`/
  `dispatch_cycle` (exact key set pinned by a test); `prepare_resume` preserves
  `dispatch_cycle` explicitly across its reset-to-pending path and normalizes
  `task_integration[*].status == "integrating"` to `"pending"` while keeping `mode`.
  V8 (foreign-worktree collision) implemented as a small, self-contained `git rev-parse` probe
  local to `spec.py` (never imports `isolation/git.py`, per this task's explicit boundary against
  `T-Gt4Pw8`'s concurrent work) — fatal only when two reposet members share a `--git-common-dir` via
  different toplevels, gated behind "isolation active" so it never probes the filesystem for a
  non-isolated `ao validate`. V9 lowercases the task id before comparing against the reserved
  `"integration"` component (schema's lowercase pattern isn't enforced when `specs/*.schema.json` is
  absent from an installed wheel). New tests: `tests/test_isolation_models.py` (78 tests) +
  `tests/test_isolation_spec_validation.py` (37 tests) = 115 new, all passing; full suite
  2008 -> 2211 passed (includes `T-Gt4Pw8`'s concurrently-landed tests), 7 skipped (unchanged), 0
  failed. `ruff check`/`format --check` clean; `mypy src` unchanged at 4 pre-existing `_version.py`
  errors. Coverage: `models.py` 99%, `runstate.py` 99%, `spec.py` 94%, `budget.py` 100% (untouched, as
  required), TOTAL 94% (baseline). No engine.py/budget.py/artifacts.py/cli.py/executors/templates/
  isolation edits, per this task's boundary. Full evidence + exact commands in `STATUS.md`.
