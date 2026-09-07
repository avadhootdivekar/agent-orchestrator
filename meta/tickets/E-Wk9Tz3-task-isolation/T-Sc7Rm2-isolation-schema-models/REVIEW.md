# REVIEW: T-Sc7Rm2-isolation-schema-models

- Reviewer: reviewer-agent
- Date: 2026-09-07
- Scope: `src/agent_orchestrator/models.py`, `spec.py`, `runstate.py`, `specs/workflow.schema.json`,
  `tests/test_isolation_models.py`, `tests/test_isolation_spec_validation.py`, ticket docs. Explicitly
  excludes the concurrently-developed `T-Gt4Pw8` porcelain (`isolation/git.py`, `xdg.py`, `errors.py`
  additions, `tests/isolation/`, `tests/test_xdg.py`).
- Verdict: **APPROVE WITH CHANGES** — must fix C-1, C-2, C-3, C-4 before commit.

## Verification performed (not just claimed)

| Command | Result |
|---|---|
| `uv run ruff check .` | All checks passed |
| `uv run ruff format --check .` | 250 files already formatted |
| `uv run mypy src` | 4 errors, all pre-existing in `_version.py` (matches expectation) |
| `uv run pytest tests/test_isolation_models.py tests/test_isolation_spec_validation.py tests/test_cli.py tests/test_e2e_cli.py tests/test_dynamic_injection.py tests/test_loop_construct.py tests/test_validate_run_control.py tests/test_task_settings_override.py -q` | 305 passed (`tests/test_spec.py` named in the review brief does not exist in this repo — substituted the actual fixture-heavy files the developer's own STATUS.md names as the ones that caught the ladder-gating bug) |
| `uv run pytest -q` (full suite) | **2211 passed, 7 skipped, 0 failed** — matches the claimed count exactly |
| `uv run ao validate` against every `specs/examples/workflow*.json` | All `OK: all specs valid` except `workflow-budget.json` (`ERROR: Unknown repo_set: 'main'`) — confirmed via `git stash` on the four changed files that this error is **pre-existing**, unrelated to this diff. Byte-identical behavior confirmed. |

## Blocking

### C-1 — `_is_structural_task`'s loop-gate check misses `__iter<N>` clones; a loop-gate task can be isolated from iteration 2 onward, violating D6/ADR-0007 D4

- **Location**: `src/agent_orchestrator/models.py`, `_is_structural_task` (new function, ~line 248-263):
  ```python
  if any(loop.gate_task_id == task.id for loop in workflow.loops):
      return True
  ```
- **What's wrong**: `engine.py::_loop_for_gate` (pre-existing, line 2119) already handles this exact
  case — a loop's gate task is re-dispatched every iteration via `_clone_body`, which suffixes its id
  `__iter{N}` for N≥2 (`engine.py:2153`, `_clone_body`, confirmed: the gate task is a member of
  `loop.body` and gets cloned like every other body task). `_loop_for_gate` therefore strips the
  `__iter<N>` suffix before comparing against `gate_task_id`. `_is_structural_task`'s new loop-gate
  branch does a **bare equality check with no suffix-stripping**, so it only recognizes iteration 1's
  gate task. `resolve_task_isolation` — the ticket's own "ONE place per-task isolation is resolved,
  never re-derive it at a call site" — silently disagrees with `engine.py`'s existing barrier logic for
  every iteration after the first.
- **Why it matters**: this is a direct, confirmed violation of a **locked design decision** (HLD §5 D6:
  "Structural tasks (`emit_tasks`, router, loop-gate) default to `isolation: none`" / ADR-0007 D4:
  loop-gate tasks are serial barriers that must never run in flight with anything else). A workflow
  using `defaults.isolation: worktree` with a loop construct will have its gate task correctly forced
  to `"none"` on iteration 1, then **silently resolve to `"worktree"` on iteration 2+** — exactly the
  kind of drift the function's own docstring says it exists to prevent. `T-En8Hd4` is explicitly
  scoped to consume `resolve_task_isolation` as-is without re-deriving it, so it will inherit this bug
  unless it independently re-checks (against this ticket's own stated contract not to).
- **Evidence** (empirically verified against the actual merged code, not hypothesized):
  ```
  iteration-1 gate task id=dev, is_structural= True
  iteration-1 resolve_task_isolation= none
  iteration-2 clone id=dev__iter2, is_structural= False
  iteration-2 clone resolve_task_isolation= worktree
  ```
  The developer's own `TestCloneBodyPinsIsolationFields.test_clone_body_carries_isolation_and_touches_forward`
  (`tests/test_isolation_models.py:534`) sets up exactly this shape (`gate_task_id="dev"`, cloned to
  `dev__iter2`) and asserts `clones[0].isolation == "worktree"` as correct — but never calls
  `resolve_task_isolation`/`_is_structural_task` on the clone to check it's still forced to `"none"`.
  `TestResolveTaskIsolationStructural`'s loop_gate case (`tests/test_isolation_models.py:270-280`) only
  ever constructs the task with `id="t"` matching `gate_task_id="t"` directly — no test exercises an
  `__iter`-suffixed id. The router branch of `_is_structural_task` has the *same* bare-equality shape,
  but that is **not** a new bug — `engine.py::_router_for_task` also does a bare match with no
  suffix-handling, so `_is_structural_task`'s router branch is consistent with existing behavior; only
  the loop-gate branch drifted from its `engine.py` counterpart.
- **Fix**: mirror `_loop_for_gate`'s suffix-stripping in `_is_structural_task`'s loop-gate branch (or
  extract the comparison into one small shared helper both call, if that doesn't require touching
  `engine.py` — this ticket's own boundary allows editing `models.py` freely). Add a regression test:
  an `__iter2`-suffixed gate-task clone with `isolation="worktree"` and `defaults.isolation="worktree"`
  must still resolve to `"none"` via `resolve_task_isolation`.

## Major

### C-2 — The `any_task_isolated` gate is computed only from static `workflow.tasks`, so V1/V2/V3/V7/V8/V10/V11 can be silently skipped for isolation that only takes effect through `defaults.isolation` + an all-structural static graph, or through `emit_tasks`-injected children

- **Location**: `src/agent_orchestrator/spec.py::_cross_validate_isolation`, `any_task_isolated`
  computation (~line 247-249):
  ```python
  any_task_isolated = any(
      resolve_task_isolation(t, workflow) == ISOLATION_WORKTREE for t in workflow.tasks
  )
  ```
- **What's wrong**: this is a reasonable and necessary fix for the real problem the developer found
  (evaluating V1-V3/V7/V8/V10/V11 unconditionally would fatal on `resolver_agent` for every workflow
  that isolates nothing, since `IntegrationSpec.ladder`'s own default includes `"llm"` — confirmed
  correct against HLD §20 decision #4, which explicitly locks `"llm"` on by default). But the gate as
  written has two gaps:
  1. **Structural-only static graph.** If a workflow sets `defaults.isolation: "worktree"` and its
     only *static* tasks are structural (e.g. a single `emit_tasks` task), `resolve_task_isolation`
     forces that task to `"none"` (correctly), so `any_task_isolated` is `False` — even though every
     child it emits at runtime will default to `isolation="inherit"` → `workflow.defaults.isolation ==
     "worktree"` and actually isolate. V1-V3/V7/V8/V10/V11 never run for this workflow's real
     integration config.
  2. **`emit_tasks`-declared isolation.** `cross_validate` runs exactly once, at `ao validate`/pre-run
     time (`cli.py:163`), against the statically-declared `workflow.tasks` only. A manifest entry that
     sets `isolation: "worktree"` directly (independent of `workflow.defaults.isolation`) is parsed by
     `artifacts.read_task_manifest` and injected by `engine.py:1260` at runtime, with **no** second
     `cross_validate` pass — confirmed by grep: `cross_validate` has exactly one call site in the
     whole package (`cli.py:163`).
- **Why it matters**: an invalid integration config (e.g. no `resolver_agent` while the default ladder
  includes `"llm"`) passes `ao validate` silently and only surfaces when a dynamically-injected,
  isolated task actually needs the resolver — mid-run, after cost has already been spent, instead of a
  fast, actionable `ao validate` error naming the field (CLAUDE.md "safe by default", "actionable
  errors"). Given this epic's stated primary consumer pattern is fan-out via `emit_tasks` (per repo
  memory: heavy `emit_tasks`/fan-out usage), gap (1) especially is a realistic shape, not a corner case.
- **Fix**: at minimum close gap (1) cheaply — `any_task_isolated = workflow.defaults.isolation ==
  ISOLATION_WORKTREE or any(...)` — since `workflow.defaults.isolation` is always known statically.
  Gap (2) (fully dynamic manifest-declared isolation) can't be closed the same way; document it
  explicitly as an accepted, named limitation in this ticket's Risks section (it currently isn't
  mentioned anywhere), so a later ticket doesn't have to rediscover it.

### C-3 — V4/V5/V7/V10 warnings use bare `logger.warning`, which prints with **no** "WARNING" label during `ao validate` (no handler is attached for that command), inconsistent with the CLI's own established warning convention

- **Location**: `src/agent_orchestrator/spec.py::_cross_validate_isolation` (all `logger.warning(...)`
  calls); contrast with `cli.py:_load_all` (~line 170): `for warning in validate_run_control(wf,
  graph): typer.echo(f"WARNING: {warning}", err=True)`.
- **What's wrong**: `cli.py`'s logging setup only attaches a handler (`attach_run_handler`) for `ao
  run`/`ao resume` (confirmed: single call site at `cli.py:1083`). For `ao validate`, the package
  logger has no handler anywhere in its hierarchy, so Python's logging "handler of last resort"
  kicks in — empirically verified:
  ```
  $ python3 -c "import logging; logging.getLogger('agent_orchestrator.spec').warning('test message here')"
  test message here
  ```
  No `WARNING`, no logger name, no timestamp — just the bare text, printed to stderr. This is a
  materially different (and much easier to miss) presentation than the same command's own
  `validate_run_control` warnings, which are unambiguously tagged `WARNING: ...`.
- **Why it matters**: this directly affects observability for exactly the failure classes this ticket
  cares about most — V5 ("your `integration` block has no effect") and V10 ("your resolver agent's
  `disallowed_tools` doesn't already cover the force-injected set", a security-adjacent notice, S-2). A
  user could easily mistake the unlabeled line for `ao`'s own stray print output, or miss it entirely
  when piping/grepping stderr for `WARNING:`. No downstream ticket currently owns wiring this into
  `cli.py`'s established pattern — grepped every downstream `TASK.md` under
  `meta/tickets/E-Wk9Tz3-task-isolation/`; none mention surfacing `cross_validate`'s isolation
  warnings.
- **Fix**: the cheapest correct option that doesn't force an API break under this ticket's own
  "don't touch `cli.py`" boundary: keep `cross_validate`'s signature (`-> None`) but have `cli.py`
  configure a lightweight stderr handler with a `"WARNING: %(message)s"`-shaped formatter for the
  package logger whenever no run-handler is attached (i.e., in the `validate` command path specifically,
  or in `main()`'s callback), so all `logger.warning` calls anywhere in the package get the same visible
  treatment `ao validate` already promises elsewhere. Failing that, explicitly hand this off in
  `STATUS.md`/`TASK.md` as a named, owned follow-up (it currently is not named anywhere).

### C-4 — V8's `_git_rev_parse` probe bypasses the S-1 "single choke point" git-safety convention established (concurrently) in `isolation/git.py`

- **Location**: `src/agent_orchestrator/spec.py::_git_rev_parse` (~line 93-117).
- **What's wrong**: `isolation/git.py`'s `GitRepo._run` (developed concurrently under `T-Gt4Pw8`, out
  of this review's edit scope but read for context) sets `core.hooksPath` to an always-empty directory
  and `GIT_TERMINAL_PROMPT=0` on every invocation — explicitly the S-1 security-gate fix, whose
  disposition in the HLD (§24) says "SAFETY_ARGS on every invocation **at the single choke point**".
  `spec.py::_git_rev_parse` is a second, independent choke point: plain `subprocess.run(["git",
  "rev-parse", *args], cwd=path, ...)` with no `env=` override at all — no `GIT_TERMINAL_PROMPT=0`, no
  hook suppression.
- **Why it matters**: this is a DRY violation (two different "shell out to git safely" implementations
  that can now independently drift) and a consistency gap versus this repo's own recently-established
  security posture. Practically: the explicit `timeout=_GIT_PROBE_TIMEOUT_SECONDS` (5s, verified
  correctly gated behind `any_task_isolated` so it never runs for a non-isolated `ao validate`, and
  verified tested — `test_not_probed_when_no_task_isolated`, `test_reporef_path_does_not_exist_is_skipped_gracefully`)
  means `subprocess.run`'s own timeout will forcibly kill a stuck child regardless of what it's blocked
  on, so the docstring's "can never hang" claim holds even without `GIT_TERMINAL_PROMPT=0` — this is a
  defense-in-depth/consistency gap, not an exploitable hang.
  The developer's choice not to import `isolation/git.py` is reasonable and explicitly justified
  (avoiding a hard dependency on a module under concurrent, independent development) — but the
  resulting gap should still be closed rather than accepted silently.
- **Fix**: add `env={**os.environ, "GIT_TERMINAL_PROMPT": "0"}` to the `subprocess.run` call in
  `_git_rev_parse` — zero coupling to `isolation/git.py`, matches the one part of S-1's posture that's
  cheap to replicate locally (hook suppression needs `isolation/paths.py`'s computed empty-hooks-dir,
  which *would* require the coupling this ticket deliberately avoids — reasonable to leave that one
  for a later consolidation once `isolation/git.py` stabilizes, but say so explicitly in `STATUS.md`).

## Minor

### C-5 — Numeric fields with a JSON-Schema `minimum` have no matching pydantic-level constraint

- **Location**: `models.py::IntegrationSpec` / `RegenerateRule` — `verify_timeout_seconds`,
  `lock_timeout_seconds`, `max_resolver_attempts`, `max_reruns_per_task`,
  `RegenerateRule.timeout_seconds` are all plain `int = <default>`, while
  `specs/workflow.schema.json` declares `"minimum": 1` or `"minimum": 0` for each.
- **Why it matters**: the ticket's own "packaging note" establishes that pydantic is the *only* gate
  for an installed wheel (schema not packaged) — that principle applies as much to basic numeric
  bounds as to the V1-V12 rules. A spec author could set `max_resolver_attempts: -5` and pydantic would
  accept it silently; the packaged JSON Schema would reject it, so behavior differs between a
  dev checkout and an installed `ao`.
- **Fix**: add `Field(ge=1)`/`Field(ge=0)` to match the schema's `minimum`, or a small cross-validation
  check alongside V1-V12. Low priority — none of these fields are consumed yet (this ticket is
  schema-only), so nothing breaks today, but it's cheaper to close now than after `T-Lr6Ka3`/
  `T-Rm2Lx7`/`T-Ib5Qy9` start reading them.

### C-6 — `_is_unsafe_relative_glob`'s absolute-path check is host-platform-dependent, not POSIX-only as documented

- **Location**: `spec.py::_is_unsafe_relative_glob` (~line 77-90): `if os.path.isabs(value): return
  True`.
- **What's wrong**: the docstring says globs are "always posix-style, even when `ao` runs on Windows",
  implying platform-independent detection, but `os.path.isabs` uses the *host* platform's rules.
  Verified: on Linux, `os.path.isabs("C:\\Users\\x")` and `os.path.isabs("C:/Users/x")` both return
  `False` — a Windows-style absolute path glob authored into `touches`/`commit_denylist`/
  `resolvers.union` would not be flagged as unsafe when `ao validate` runs on Linux (the common case).
- **Why it matters**: low severity — this is an author-mistake safety net, not a runtime attacker
  surface, and the common Unix-absolute-path and `..`-traversal cases are correctly caught on every
  platform (the `..` check already uses `PurePosixPath`, which is platform-independent).
- **Fix**: either drop the "even when ao runs on Windows" claim from the docstring (accurate as-is:
  POSIX-style only), or replace `os.path.isabs` with an explicit POSIX-style absolute check
  (`value.startswith("/")`) plus a small Windows-drive-letter/UNC pattern check if cross-platform
  authoring is actually a goal.

## Suggestions

- A dedicated round-trip test over `tests/fixtures/**/*.json` and
  `templates/builtin/routed-runner/workflow.json.tmpl` (in addition to the existing
  `specs/examples/workflow*.json` parametrized test) would make the "every existing fixture loads
  byte-identically" guarantee explicit and self-documenting, rather than relying on the full suite's
  aggregate pass/fail. Not required now — the full suite (2211 passed) already exercises these paths
  indirectly and passed.
- Consider naming the "loop-gate clone must still resolve `none`" invariant explicitly next to
  `_is_structural_task`'s docstring once C-1 is fixed, so a future refactor of `_clone_body` (which
  this ticket's own AC-20 flags as a drift risk for `isolation`/`touches`) doesn't reintroduce the same
  class of bug for a different field.

## Alignment verdict

- **Project goals**: DAG-first/deterministic/safe-by-default mostly upheld; C-1 is a direct violation
  of "safe by default" + DAG barrier correctness that must be fixed before this lands, since
  `T-En8Hd4` is explicitly scoped to build on `resolve_task_isolation` without re-deriving it.
- **Epic/ticket goals**: AC-1 through AC-21 are each implemented and each has a corresponding test;
  the one deliberate, disclosed design correction (gating V1-V3/V7/V8/V10/V11 on `any_task_isolated`)
  is the *right* call and matches HLD §20 decision #4 (LLM resolver on by default) — but the gate's
  implementation has the gap in C-2. No scope creep observed; the explicit non-edits (`engine.py`,
  `budget.py`, `artifacts.py`, `cli.py`, `executors/`, `templates/`, `isolation/`) were honored.
- **Code-level intent**: `resolve_task_isolation`'s docstring promises to be "the ONE place... so a
  future change... cannot silently drift between call sites" — C-1 shows it has already silently
  drifted from `engine.py`'s pre-existing, more complete loop-gate matching logic.

## Dimensions walked (all checked)

SOLID/KISS: OK — two small pure resolver functions, no over-abstraction. DRY: C-1/C-4 are the two real
duplicate-logic risks; C-5 is a schema/pydantic parity gap. Magic literals: OK — every new constant is
named (`DEFAULT_*`, `TIER_*`, `ISOLATION_*`, `OVERLAP_*`, `ON_DENYLISTED_*`) per AC-1. Pluggable
architecture: N/A for this ticket (declarative schema/model layer only, no executor/backend seam
touched). Spec/DAG correctness: C-1/C-2 are the material findings. Determinism/resume safety:
`prepare_resume`/`write_status` verified correct and tested (AC-17/AC-18); no `random`/`time` used.
Errors/logging: C-3 is the material finding; fatal paths correctly raise `SpecValidationError` naming
task id/field. Testability: all new logic is pure/injectable; 115 new tests, well-structured,
table-driven where appropriate. Concurrency/rollout: NFR-5 backward-compat verified two ways (fixture
load test + full-suite green); additive schema fields only.

## Testing notes

- What to mock: nothing new needed — `resolve_task_isolation`/`resolve_overlap_preference` are pure;
  `_git_rev_parse` already degrades to `None` gracefully and is tested against real temp git repos
  (appropriately an integration-style test, not mocked).
- What to integration-test: the C-1 fix, once applied, needs a test at the `engine.Orchestrator._clone_body`
  boundary asserting the *resolved* isolation of a cloned gate task, not just that fields survive the
  clone.
- Coverage gaps: C-1's exact scenario (loop-gate clone + non-none isolation) was untested despite two
  adjacent tests (`TestResolveTaskIsolationStructural`, `TestCloneBodyPinsIsolationFields`) that were
  each one assertion away from catching it.
