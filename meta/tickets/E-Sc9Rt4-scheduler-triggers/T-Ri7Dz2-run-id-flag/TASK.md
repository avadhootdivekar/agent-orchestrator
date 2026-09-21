# TASK: T-Ri7Dz2-run-id-flag

## Metadata
- Task ID: `T-Ri7Dz2-run-id-flag`
- Epic ID: `E-Sc9Rt4-scheduler-triggers`
- Owner: developer agent
- Created: 2026-09-06
- Last Updated: 2026-09-06
- Status: Draft
- Estimate: 1 day

## Requirements Mapping
- Requirement IDs: FR-8 (see `../EPIC.md`)

## Description
Give `ao run` a `--run-id` so a caller can name the run it is starting, instead of the engine
minting one and the caller inferring it afterwards by diffing the runs directory.

Today `ui/processes.py::_discover_run_id` polls the runs directory for up to
`RUN_ID_DISCOVERY_TIMEOUT_SECONDS = 10.0`, taking the oldest unclaimed fresh id. `meta/ROADMAP.md`
§4 already records the resulting mis-attribution risk ("Concurrent launches in the same instant
could mis-attribute … A `--run-id` flag on `ao run` would remove the guesswork"). For a daemon
this is worse than a caveat: ten blocking seconds per launch inside a 2-second monitor loop is
not acceptable, and an *inferred* fire→run link makes overlap detection and `ao schedule history`
unreliable at exactly the moment they matter.

Small task, deliberately scoped to the flag and its guard. It is independent of every other task
except `T-Lp4Wt6`, which consumes it.

Files you own (create/edit freely):
- `src/agent_orchestrator/cli.py` (edit: the `run` command only — add the option and thread it
  into the run-id resolution)
- `src/agent_orchestrator/runstate.py` and/or `engine.py` (edit: **only** the minimal change
  needed to accept a caller-supplied run id and to refuse a duplicate)
- `src/agent_orchestrator/ui/processes.py` (edit: **only** to pass `run_id` through
  `ALLOWED_OPTIONS` and to short-circuit discovery when it was supplied)
- `tests/test_run_id_flag.py` (new)

Do NOT touch: `schedules/`, `service/`, `ui/app.py`, `ui/service.py`, `ui/src/`, `dag.py`,
`executors/`, `spec.py`, `models.py`.

## Acceptance Criteria
1. `ao run --run-id <id>` uses `<id>` verbatim as the run id. Without the flag, behavior is
   byte-identical to today (the engine mints `<workflow_id>-<UTC timestamp>`).
2. `<id>` is validated against a named pattern constant (`^[A-Za-z0-9][A-Za-z0-9._-]*$`, max 128
   chars) so it can never contain a path separator or a `..` segment — a run id becomes a
   directory name, so this is a path-traversal guard, not cosmetics. A rejected id exits non-zero
   with a message naming the pattern.
3. **A `--run-id` whose run directory already exists is refused** with a distinct non-zero exit
   and a message telling the user to `ao resume --run-id <id>` instead. Silently reusing the
   directory would let a caller corrupt an existing run's state — this AC is the reason the flag
   is safe to add at all.
4. `ProcessSupervisor.launch_run(options={"run_id": ...})` renders `--run-id <id>` via the
   existing `ALLOWED_OPTIONS` mechanism (add the one entry; do not add a bespoke code path), and
   when a `run_id` was supplied, `_discover_run_id` is skipped entirely: the `LaunchRecord` is
   stamped with the supplied id immediately, with **zero** polling delay.
5. `ProcessSupervisor(root, run_id_discovery_timeout=0.0)` remains valid and means "do not
   block; let `reconcile()` backfill" — the fallback the scheduler uses if a launch ever happens
   without an id.
6. `ao resume --run-id` is unchanged. `ao run --run-id` and `ao resume` must not become two ways
   to start the same run: AC3's guard is what keeps them distinct.

### Tests
7. `CliRunner` e2e with the `fake` executor: `ao run --run-id my-run-1 …` produces a run
   directory named exactly `my-run-1` with a valid `state.json`; a second invocation with the
   same id exits non-zero and leaves the first run's `state.json` **byte-identical**.
8. Rejection tests for `../escape`, `a/b`, an empty string, a 129-character id — each asserting
   the exit code and that no run directory was created.
9. A `ProcessSupervisor` test asserting that with `options={"run_id": "x"}` the returned
   `LaunchRecord.run_id == "x"` and that discovery polling did not run (spy on the poll helper or
   assert elapsed time under a threshold with a stub `ao` command).
10. Regression: the entire existing `tests/ui/test_processes.py` and the run/resume e2e suites
    pass unmodified — no behavior change without the flag.
11. `uv run pytest -q` green with the delta reported; ruff + format clean; mypy whole-tree count
    reported.

## Risks
- Touching `engine.py`/`runstate.py` run-id minting is the one place this small task can break
  something large. Keep the change to "use the supplied id if present, else mint as before" and
  confirm with `git diff --stat` that nothing else moved.
- AC3's guard must check the run **directory**, not just the in-memory registry, or a stale
  process could still collide.
- `ROADMAP.md` §4 mentions this gap. Do **not** edit `meta/ROADMAP.md` in this task — the epic's
  `T-Dc6Zr2` owns the roadmap sync at close.

## Dependencies
- None. Can start any time in Sprint 1; `T-Lp4Wt6` needs it before that task's AC on zero-latency
  attribution can be met.

## Pseudocode / Algorithm
```text
FUNCTION resolve_run_id(supplied, workflow_id, clock, store):
  IF supplied IS NULL:
      RETURN f"{workflow_id}-{clock().strftime('%Y%m%dT%H%M%SZ')}"     # unchanged
  IF NOT RUN_ID_PATTERN.fullmatch(supplied) OR len(supplied) > RUN_ID_MAX_LEN:
      RAISE OrchestratorError(f"--run-id must match {RUN_ID_PATTERN.pattern} (max {RUN_ID_MAX_LEN})")
  IF store.run_dir(supplied).exists():
      RAISE OrchestratorError(f"run {supplied!r} already exists; use `ao resume --run-id {supplied}`")
  RETURN supplied
```

## Schemas / Interface Notes
- Interface / CLI: `ao run --run-id <id>`; `ProcessSupervisor.launch_run(options={"run_id": ...})`
  via the existing `ALLOWED_OPTIONS` map.
- Spec / data schema: none — this is a runtime invocation option, not a spec field (consistent
  with ADR-0003 §3's "invocation-scoped settings ride the CLI/env/config chain, never a workflow
  field").
- Triggers / events: N/A.
- Artifacts: determines the name of `<workspace>/.orchestrator/runs/<run_id>/`.

## Handoff Boundary
- Upstream: `meta/ROADMAP.md` §4 (the recorded gap); ADR-0014 D6.
- Downstream: `T-Lp4Wt6` (supplies `run_id` on every scheduled launch), `T-Ev3Qm5` (the exact
  fire→run link its overlap detection depends on), `T-Te3Qw8` (asserts the id end-to-end).

## Artifacts
- Docs/comments: `meta/tickets/E-Sc9Rt4-scheduler-triggers/T-Ri7Dz2-run-id-flag/`
- Large outputs: N/A
