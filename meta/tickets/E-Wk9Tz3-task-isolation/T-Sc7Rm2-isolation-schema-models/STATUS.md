# STATUS

- ID: `T-Sc7Rm2-isolation-schema-models`
- Updated At: 2026-09-07
- State: In Review
- Owner: developer-agent

## This update
- **2026-09-07 — Implemented, all gates green (By: developer-agent · Role: developer).** AC-1..AC-21
  delivered in `models.py`, `specs/workflow.schema.json`, `spec.py`, `runstate.py`. Two new test
  files: `tests/test_isolation_models.py` (78 tests), `tests/test_isolation_spec_validation.py`
  (37 tests) — 115 new tests, all passing.
  - **One deliberate design correction versus the HLD's literal V1-V12 table**, recorded here since
    it changes how the rules actually run: `IntegrationSpec.ladder`'s own default already includes
    `"llm"` (HLD §10.2), so evaluating V1/V2/V3/V7/V8/V10/V11 unconditionally would fatal on an unset
    `resolver_agent` for **every** workflow that isolates nothing — this was caught immediately by
    the pre-existing suite (118/128 tests in the dynamic-injection/loop/fixture-tier/validate-run-
    control/task-settings-override files failed before the fix). Those seven rules are now gated on
    "at least one task resolves to `isolation='worktree'`"; V4/V6/V9/V12 (task-level/glob-hygiene,
    independent of whether integration ever engages) run unconditionally; V5 alone warns when
    `integration` is configured but inactive. This is the single place the ticket's own risk note
    ("every rule must exist in Python, not only JSON Schema") could have silently regressed
    NFR-2/NFR-5 for the entire pre-epic spec corpus, so it is called out explicitly for review.
  - Warning-severity rules (V4/V5/V7/V10) use `logger.warning` (matching `dag.build_dag`'s existing
    non-fatal-notice convention), not a returned list — `cross_validate`'s signature is unchanged
    (still returns `None`), so `cli.py` needed no edit (out of this task's scope) for warnings to be
    visible via the standard logging pipeline.
  - V8 (foreign, pre-existing on-disk worktree collision) is a small, **self-contained** `git
    rev-parse` probe local to `spec.py` — it does **not** import `isolation/git.py` (`T-Gt4Pw8`'s
    concurrently-developed module), per this task's explicit boundary. Fatal only when two reposet
    members share a `--git-common-dir` via different toplevels (a plain nested subdirectory of the
    same repo, e.g. HLD §7.1's `docs`-under-`core` example, is explicitly NOT a violation). Gated
    behind "isolation active" so a non-isolated `ao validate` never spawns a git subprocess.
  - `runstate.write_status` emits the `integration` block (`active`/`branch`/`heads`/`tier_counts`/
    `integrated`/`conflict`/`failed`) and per-task `integration_status`/`tier_reached`/
    `conflicted_count`/`dispatch_cycle` — exact key sets pinned by a test (AC-18).
    `prepare_resume` explicitly preserves `dispatch_cycle` across its existing reset-to-pending path
    (which otherwise replaces `TaskRunState` wholesale) and normalizes any `task_integration[tid]`
    stuck at `"integrating"` back to `"pending"` while keeping `mode` (AC-17) — **confirmed**:
    `dispatch_cycle` survives `prepare_resume` exactly as `T-En8Hd4` needs.
  - AC-20 confirmed by test, no `engine.py` edit made: `_clone_body`'s `model_copy(deep=True,
    update={...})` already carries `isolation`/`touches` forward.
- Prior update — ticket created by the architect as part of the `E-Wk9Tz3-task-isolation` design
  package. Not started; no code written.
- **2026-09-07 — Phase-1 review amendment applied.** This ticket now carries **every schema/model
  field the later review fixes need**, so the schema lands once and is not reopened mid-epic:
  S-2 (`resolver_disallowed_tools`, `resolver_deny_push`, rules V10/V11), S-3 (`auto_commit`,
  `commit_denylist`, `on_denylisted_path`), S-5 (`tier_counts`), S-6
  (`regenerate[].timeout_seconds`), R-21 (`TaskRunState.dispatch_cycle`), R-1b
  (`BudgetCounters.reconciled_cycles`), R-4 (`workspace_lock`, reserved without semantics), R-5
  (`resolve_overlap_preference` as the single derived-default site), R-14 (V8 wording). New AC-15
  structurally forbids any command/argv field from ever living on `TaskSpec`. AC-20 downgraded to
  "pin with a test" — review confirmed `_clone_body`'s `model_copy` already carries the new fields,
  so no `engine.py` edit is required. AC count 10 -> 21; validation rules V1-V9 -> V1-V12; estimate
  unchanged at 2 days.
- Both gates state explicitly that **nothing must resolve before this task starts**.

## Evidence
- Design: [`docs-md/task-isolation-hld.md`](../../../../docs-md/task-isolation-hld.md) (see the
  module section named in `TASK.md`) and
  [`ADR-0013`](../../../../docs-md/adr/ADR-0013-per-task-git-isolation-and-rebase-integration.md).

## Risks / Blockers
- See `TASK.md` > Risks. Blocked only by the dependencies listed in `TASK.md` > Dependencies.

## Next actions
1. Read `TASK.md`, then the HLD section it names, then the **merged code of every dependency task**
   (do not re-derive an interface from the design doc alone).
2. Implement, run `uv run pytest -q` plus `ruff check` / `ruff format --check` / `uv run mypy src`,
   and record before/after counts in this file.
3. Update `TASK.md` Status and this file together, with By/Role/Date attribution.

---
- By: reviewer-agent · Role: reviewer · Date: 2026-09-07 · Comment: Code review complete —
  **APPROVE WITH CHANGES**. Full verification re-run and confirmed: `ruff check`/`format --check`
  clean, `mypy src` at the expected 4 pre-existing `_version.py` errors, full suite **2211 passed / 7
  skipped / 0 failed** (matches the claimed count), and `ao validate` against every
  `specs/examples/workflow*.json` is byte-identical to pre-change behavior (the one failure,
  `workflow-budget.json`'s `Unknown repo_set: 'main'`, reproduces identically with this diff stashed
  out — pre-existing, unrelated). Full findings in
  [`REVIEW.md`](REVIEW.md).
  - **Blocking (must fix before commit): C-1.** `_is_structural_task`'s loop-gate branch does a bare
    `loop.gate_task_id == task.id` match with no `__iter<N>`-suffix stripping, unlike the pre-existing
    `engine.py::_loop_for_gate` it was supposed to agree with. Empirically verified: a loop-gate task
    correctly resolves to `isolation="none"` on iteration 1, then **silently resolves to `"worktree"`
    from iteration 2 onward** under `defaults.isolation="worktree"` — a direct violation of the locked
    D6/ADR-0007 D4 invariant that structural/barrier tasks are never isolated. `T-En8Hd4` is scoped to
    build on `resolve_task_isolation` without re-deriving it, so it will silently inherit this bug.
    Fix: mirror `_loop_for_gate`'s suffix-stripping in `_is_structural_task`; add a regression test for
    an `__iter2`-suffixed gate clone.
  - **Major: C-2.** `_cross_validate_isolation`'s `any_task_isolated` gate (correctly added to avoid
    fataling every non-isolated workflow on the ladder's default `"llm"` entry — this part matches HLD
    §20 decision #4) is computed only from static `workflow.tasks`, so V1/V2/V3/V7/V8/V10/V11 can be
    silently skipped when isolation only actually takes effect via `defaults.isolation="worktree"` with
    an all-structural static graph, or via `emit_tasks`-injected children (confirmed `cross_validate`
    has exactly one call site, pre-run only, never re-run when manifests inject tasks at runtime).
  - **Major: C-3.** V4/V5/V7/V10 use bare `logger.warning`, which — confirmed empirically — prints
    with **zero** "WARNING" label during `ao validate` (no handler attached for that command; only
    `ao run`/`resume` attach one), unlike the same command's own `validate_run_control` warnings
    (`typer.echo(f"WARNING: {warning}", err=True)`). No downstream ticket currently owns wiring this.
  - **Major: C-4.** V8's `_git_rev_parse` probe is a second, weaker git-invocation choke point next to
    `isolation/git.py`'s S-1-hardened one (no `GIT_TERMINAL_PROMPT=0`/hook suppression) — the decision
    not to import `isolation/git.py` is reasonable given the concurrent-development boundary, but the
    gap should be closed with a one-line `GIT_TERMINAL_PROMPT=0` env override rather than left silent.
  - Minor: C-5 (numeric fields with a JSON-Schema `minimum` have no matching pydantic `Field(ge=...)`,
    so an installed wheel silently accepts negative values), C-6 (`_is_unsafe_relative_glob`'s
    `os.path.isabs` is host-platform-dependent, not POSIX-only as documented).
  - Everything else verified clean: AC-1..AC-21 each implemented with a corresponding test; V9's
    lowercasing is correct and necessary (schema pattern not packaged into the wheel, pydantic has no
    matching constraint); `TaskContext.env` is correctly inert/staged per this ticket's own stated
    intent; `prepare_resume`/`write_status` match HLD §10.3/§10.4 and are well-tested; all
    downstream-ticket field names (`tier_counts`, `reconciled_cycles`, `commit_denylist`,
    `on_denylisted_path`, `resolver_disallowed_tools`, `resolver_deny_push`, `workspace_lock`,
    `regenerate[].timeout_seconds`, `auto_commit`, `dispatch_cycle`, `resolve_overlap_preference`)
    grepped and confirmed present with matching names in the tickets that consume them.

- **2026-09-07 — Review findings addressed, all gates re-verified green.** Disposition of each C-id:
  - **C-1 (Blocking): FIXED.** Extracted `models.strip_iter_suffix` (new, single implementation) and
    call it from both `_is_structural_task`'s loop-gate branch AND `engine.py::_loop_for_gate` (a
    one-line body replacement, authorized for DRY — no other `engine.py` edit made). A loop-gate
    clone (`dev__iter2`, `dev__iter3`) with `defaults.isolation="worktree"` now correctly resolves to
    `"none"` via `resolve_task_isolation`. Tests: `TestResolveTaskIsolationLoopGateClone` (4 tests,
    `tests/test_isolation_models.py`), including one that goes through the REAL
    `Orchestrator._clone_body` (not a hand-built clone) per the review's own testing note.
  - **C-2 (Major): FIXED (gap 1) + documented limitation (gap 2).** `_any_task_isolated(workflow,
    tasks)` now checks `workflow.defaults.isolation == "worktree" OR any(resolve_task_isolation(t,
    workflow) == "worktree" for t in tasks)` — closes gap 1 (an all-structural static graph under
    `defaults.isolation="worktree"`) for free, since `defaults.isolation` is always known statically.
    Extracted the registry-independent rules (V1/V2/V4/V5/V6/V7/V9/V11) into a new public, pure
    function `validate_isolation(workflow, tasks) -> list[str]`; `_cross_validate_isolation` calls it
    first, then adds the three registry-dependent rules (V3/V8/V10). **Hook point for `T-En8Hd4`**
    (per instruction, documented here): call
    `validate_isolation(workflow, workflow.tasks + newly_injected_tasks)` at `emit_tasks` injection
    time to close gap 2 (a manifest-declared `isolation: "worktree"` independent of
    `defaults.isolation`, which a single pre-run `ao validate` pass can never see) — this ticket does
    not itself call it there (no `engine.py` wiring beyond the C-1 one-liner, per this ticket's
    boundary), so gap 2 remains open until `T-En8Hd4` actually uses this hook. Tests: 2 new cases in
    `TestV2LlmRequiresResolverAgent` + 1 in `TestV5IntegrationConfiguredButUnused`
    (`tests/test_isolation_spec_validation.py`), covering both the new gate branch and its control
    (defaults.isolation="none" stays inert).
  - **C-3 (Major): FIXED.** `cross_validate`/`validate_isolation`/`_cross_validate_isolation` now
    RETURN their warnings (V4/V5/V7/V10) instead of calling `logger.warning`. `cli.py::_load_all`
    threads them through the exact same `for warning in ...: typer.echo(f"WARNING: {warning}",
    err=True)` convention already used for `validate_run_control`'s warnings — a 4-line, minimal,
    authorized edit (no other `cli.py` change). Test:
    `TestValidateCliBoundary::test_ao_validate_prints_a_v5_warning_with_the_established_label`
    (CliRunner, asserts the literal `"WARNING: workflow.integration is configured"` text in
    `result.output`).
  - **C-4 (Major): FIXED.** `_git_rev_parse` now passes `env={**os.environ, "GIT_TERMINAL_PROMPT":
    "0", "LC_ALL": "C"}` to `subprocess.run`. Docstring expanded to name `isolation/git.py`'s
    `GitRepo._run` as the epic's single S-1-hardened choke point and explain why this probe stays
    decoupled from it (avoids a hard dependency on a module under concurrent, independent
    development) while replicating the cheap, dependency-free part of that hardening locally; hook
    suppression (needs `isolation/paths.py`'s computed empty-hooks-dir) is explicitly left for a
    later consolidation once `isolation/git.py` stabilizes.
  - **C-5 (Minor): FIXED.** Added `Field(ge=1)`/`Field(ge=0)` to `IntegrationSpec.
    verify_timeout_seconds`/`lock_timeout_seconds` (ge=1), `max_resolver_attempts`/
    `max_reruns_per_task` (ge=0), and `RegenerateRule.timeout_seconds` (ge=1) — matching
    `specs/workflow.schema.json`'s `minimum` for each. Tests:
    `TestNumericFieldConstraintsMatchSchemaMinimums` (6 tests).
  - **C-6 (Minor): FIXED.** `_is_unsafe_relative_glob` now uses `posixpath.isabs` instead of
    `os.path.isabs` (platform-independent absolute-path detection, matching the function's own
    documented "always posix-style" contract). Test:
    `TestV6TouchesUnsafeGlob::test_leading_slash_flagged_regardless_of_host_platform`.
  - **Re-verified gates**: `uv run ruff check .` / `ruff format --check .` clean; `uv run mypy src`
    unchanged at 4 pre-existing `_version.py` errors; `uv run pytest -q -p no:cacheprovider` full
    suite **2250 passed / 7 skipped / 0 failed** (>= the review's 2211 baseline; includes further
    concurrently-landed `T-Gt4Pw8` fixes/tests); coverage `models.py` 99%, `runstate.py` 99%,
    `spec.py` 94%, `budget.py` 100% (untouched), TOTAL 94% (unchanged). New/changed test files:
    `tests/test_isolation_models.py` (92 tests, was 78), `tests/test_isolation_spec_validation.py`
    (42 tests, was 37) — 19 new tests this round, 4 rewritten (V4/V5/V7/V10 warning assertions moved
    from `caplog` to the returned list per C-3). Files touched beyond the original set:
    `src/agent_orchestrator/engine.py` (the single authorized C-1 one-liner in `_loop_for_gate` + one
    import), `src/agent_orchestrator/cli.py` (the single authorized C-3 4-line edit in `_load_all`).
  — By: developer-agent · Role: developer · Date: 2026-09-07
