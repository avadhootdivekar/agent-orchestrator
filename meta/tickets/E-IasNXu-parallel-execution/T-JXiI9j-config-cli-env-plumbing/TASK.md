# TASK: T-JXiI9j-config-cli-env-plumbing

## Metadata
- Task ID: `T-JXiI9j-config-cli-env-plumbing`
- Epic ID: `E-IasNXu-parallel-execution`
- Owner: developer agent
- Created: 2026-07-15
- Last Updated: 2026-07-15
- Status: Done
- Estimate: 1.0 day

## Requirements Mapping
- FR-1, FR-2, NFR-3

## Description
Plumb a new run-scoped setting `max_parallel` end-to-end through the exact existing three-layer
precedence chain (ADR-0003 / ADR-0007 D5), identical in shape to `quota_max_wait_seconds`. This task
delivers only the plumbing; the engine remains **strictly serial** — `Orchestrator` stores
`max_parallel` but `run()` does not yet consume it (that is `T-j8YLGd`). This is a real, testable
seam: after this task, `--max-parallel 4` is accepted and validated but has no runtime effect yet,
which the acceptance criteria state explicitly.

Concretely:
1. `src/agent_orchestrator/project_config.py`: add `max_parallel: int | None = None` to
   `ProjectConfig` (with docstring noting it is invocation-scoped, mirrors quota fields at L112-116).
2. Add a module constant `DEFAULT_MAX_PARALLEL = 1` (co-locate with the other `DEFAULT_*` runtime
   constants; `cli.py` imports it like `DEFAULT_QUOTA_MAX_WAIT_SECONDS`).
3. `src/agent_orchestrator/cli.py::_resolve_run_settings` (L259): add a `max_parallel: int | None`
   parameter and resolve it with the same `_int_env` + `or`-chain used by quota (L289-300); return it
   as an additional tuple element. Validate `>= 1` with a loud `typer.Exit(1)` (mirror the `--effort`
   validation at L617-623). Update the return-type annotation and docstring.
4. `cli.py::run` (L501) and `cli.py::resume` (L683): add `--max-parallel` `typer.Option` (default
   None, help documents `AO_MAX_PARALLEL` + `max_parallel`), pass it into `_resolve_run_settings`,
   unpack the new tuple element, and pass `max_parallel=eff_max_parallel` into **both**
   `Orchestrator(...)` constructions (run ~L656, resume ~L904).
5. `src/agent_orchestrator/engine.py::Orchestrator.__init__` (L136): add `max_parallel: int = 1`,
   store `self._max_parallel = max(1, int(max_parallel))` (defensive clamp). Do **not** change `run()`.
6. `project_config.py::_INIT_TEMPLATE` (L239): add a commented `max_parallel:` line under the
   "Runtime execution settings" section (mirror the `max_attempts`/`max_turns` comment style).

## Acceptance Criteria
1. Given a `.ao/config.yaml` with `max_parallel: 4` and no CLI/env override, When `_resolve_run_settings`
   runs, Then it returns `max_parallel == 4`. Given `AO_MAX_PARALLEL=6` over that config, Then `6`.
   Given `--max-parallel 8` over both, Then `8`. Given none of the three, Then `1` (`DEFAULT_MAX_PARALLEL`).
2. Given `--max-parallel 0` (or a negative value, or `AO_MAX_PARALLEL=-1`), When `ao run`/`ao resume`
   is invoked, Then the CLI exits `1` with a clear "must be >= 1" error before any task dispatch.
3. Given `AO_MAX_PARALLEL=""` (empty), When resolving, Then it is treated as unset (falls through to
   config/default) — no crash (learning #23 empty-env-is-falsy pattern).
4. `ProjectConfig(max_parallel=3)` round-trips; a config file with no `max_parallel` key loads with
   `max_parallel is None` (byte-identical to pre-task behavior). `ao validate` is unaffected and **no
   `*.schema.json` file is modified** (grep the diff: zero changes under `specs/`).
5. `Orchestrator(..., max_parallel=4)` stores `self._max_parallel == 4`; `Orchestrator(...)` with no
   arg stores `1`. A full `orch.run(...)` with `max_parallel=4` produces a result **byte-identical**
   to `max_parallel=1` (engine still serial this task) — an explicit regression assertion.
6. `ao init` scaffolds a `.ao/config.yaml` whose template contains a commented `max_parallel:` line
   documenting `AO_MAX_PARALLEL` and the default.
7. `ruff check`, `ruff format --check`, and `mypy src` are clean for the changed files; `uv run pytest`
   green (existing suite unedited).

## Risks
- Low. Return-arity change to `_resolve_run_settings` touches two unpack sites (run + resume) — mirror
  the existing tuple exactly and update both. Keep `max_parallel` OUT of `wf.defaults` (it is
  invocation-scoped, ADR-0003 §3) — do not add it to the workflow spec/schema.

## Dependencies
- None (foundational). Downstream: `T-j8YLGd` consumes `self._max_parallel`.

## Pseudocode / Algorithm
```text
# project_config.py
DEFAULT_MAX_PARALLEL = 1                       # or import from models with the other DEFAULT_*
class ProjectConfig(BaseModel):
    ...
    max_parallel: int | None = None            # invocation-scoped (ADR-0007 D5); NOT a spec field

# cli.py::_resolve_run_settings(..., max_parallel: int | None)
resolved_max_parallel = int(
    max_parallel or _int_env("AO_MAX_PARALLEL") or (cfg.max_parallel if cfg else None)
    or DEFAULT_MAX_PARALLEL)
if resolved_max_parallel < 1:
    typer.echo("ERROR: --max-parallel must be >= 1", err=True); raise typer.Exit(1)
return (..., resolved_max_parallel)

# cli.py::run / ::resume
max_parallel: int | None = typer.Option(None, "--max-parallel",
    help="Max independent ready tasks to run at once (default 1 = serial). "
         "Env AO_MAX_PARALLEL; config max_parallel.")
...
(..., eff_max_parallel) = _resolve_run_settings(..., max_parallel)
orch = Orchestrator(..., max_parallel=eff_max_parallel)

# engine.py::Orchestrator.__init__
def __init__(self, ..., max_parallel: int = 1):
    ...
    self._max_parallel = max(1, int(max_parallel))     # run() ignores it until T-j8YLGd
```

## Schemas / Interface Notes
- Interface / API: `_resolve_run_settings` (return arity +1); `Orchestrator.__init__` (+kwarg);
  `--max-parallel` CLI option on `run`/`resume`.
- Spec / data schema (JSON/YAML): `.ao/config.yaml` `max_parallel:` (pydantic `ProjectConfig`, no
  JSON-schema file). **No `specs/*.schema.json` change** (ADR-0007 D5) — an explicit non-change.
- Triggers / events: N/A.
- Artifacts (inputs/outputs by path): N/A.

## Handoff Boundary
- Upstream: none.
- Downstream: `T-j8YLGd` (engine consumes `self._max_parallel`); `T-TNleFt` (CliRunner e2e for the
  three precedence sources + the `--max-parallel 0` error).

## Artifacts
- Docs/comments: `meta/tickets/E-IasNXu-parallel-execution/T-JXiI9j-config-cli-env-plumbing/`
- Large outputs: none.

## Comments

- By: Claude
- Role: developer
- Date: 2026-07-15
- Comment: Implemented per the Description/Pseudocode. Full detail + test evidence in
  `STATUS.md`. One flagged discrepancy: AC-2's prose lists `--max-parallel 0` alongside
  "negative" as cases that "exit 1", but the ACTUAL executable pseudocode directly below it in
  this file (`max_parallel or _int_env(...) or ... or DEFAULT_MAX_PARALLEL`, then `if
  resolved_max_parallel < 1`) makes `0` mathematically unable to reach the guard — `0` is falsy
  in Python, so it falls through the `or`-chain to `DEFAULT_MAX_PARALLEL` (=1) before the `< 1`
  check ever runs; only a truthy *negative* value survives the chain far enough to be rejected.
  This matches the identical pre-existing behavior of `--quota-max-wait 0` / `--max-attempts 0`
  elsewhere in `_resolve_run_settings` (0 there also silently becomes the default, not an
  error) — `max_parallel` was implemented consistently with that established pattern rather than
  special-cased. Implemented exactly as specified in the Pseudocode section (`0` -> default,
  silently; negative -> loud `typer.Exit(1)`); unit-tested both branches explicitly in
  `tests/test_cli.py::TestResolveRunSettingsMaxParallel`. Recommend correcting AC-2's wording
  (drop `0` from the "exits 1" list) and revisiting the `T-TNleFt` Handoff Boundary line ("the
  `--max-parallel 0` error") before that ticket is written, so its e2e tests target the actual
  implemented behavior instead of the prose's imprecise summary.
