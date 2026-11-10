# TASK: T-Ri7Dz2-run-id-flag

## Metadata
- Task ID: `T-Ri7Dz2-run-id-flag`
- Epic ID: `E-Sc9Rt4-scheduler-triggers`
- Owner: developer agent
- Created: 2026-09-06
- Last Updated: 2026-11-10 (Opus design review w01-06: F1/F22/F24 applied; see `../EPIC.md` "Late-gate corrections")
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
- `src/agent_orchestrator/runstate.py` (edit: the shared run-id pattern constant, an atomic
  directory reservation, and `RunStateStore.new_run(workflow, run_id=None)`)
- `src/agent_orchestrator/engine.py` (edit: **the one sanctioned `engine.py` edit of this epic** —
  an `Orchestrator.run(..., run_id=None)` keyword forwarded to `new_run`; `run_state is None`
  semantics stay untouched, see AC3a)
- `src/agent_orchestrator/run_options.py` (edit: one registry `Option`, see AC4)
- `src/agent_orchestrator/feedback.py` (edit: import the hoisted run-id constant instead of its
  own copy)
- `src/agent_orchestrator/ui/processes.py` (edit: **only** the explicit `launch_run(run_id=)`
  keyword, discovery short-circuit and `derive_launch_status`)
- `tests/test_run_id_flag.py` (new); `tests/test_run_options.py` (edit: the `run --help` flag
  assertion, AC9a); `tests/fixtures/run_options_schema.json` (regenerate, F24)

Do NOT touch: `schedules/`, `service/`, `ui/app.py`, `ui/service.py`, `ui/src/`, `dag.py`,
`executors/`, `spec.py`, `models.py`.

## Acceptance Criteria
1. `ao run --run-id <id>` uses `<id>` verbatim as the run id. Without the flag, behavior is
   byte-identical to today (the engine mints `<workflow_id>-<UTC timestamp>`).
2. `<id>` is validated against **one** shared run-id pattern constant hoisted into
   `runstate.py` (`^[A-Za-z0-9][A-Za-z0-9._-]*$`, max 128 chars) and used from both `runstate.py`
   and `feedback.py` (which already has `MAX_RUN_ID_CHARS`, `RUN_ID_RE`, `_DOT_RUN_IDS` — do not
   add a near-duplicate; CLAUDE.md: no duplicated logic). It can never contain a path separator
   or a `..` segment — a run id becomes a directory name, so this is a path-traversal guard, not
   cosmetics. A rejected id exits non-zero with a message naming the pattern.
3. **A `--run-id` whose run directory already exists is refused** with a distinct non-zero exit
   and a message telling the user to `ao resume --run-id <id>` instead. Silently reusing the
   directory would let a caller corrupt an existing run's state — this AC is the reason the flag
   is safe to add at all.
3a. **The guard is an atomic reservation, not check-then-run.** The run directory is only created
   by the first `save()` (`runstate.py:356`, `mkdir(..., exist_ok=True)`), so an `exists()`-then-run
   guard is a TOCTOU race: two concurrent `ao run --run-id X` both pass and share one state file.
   Reserve the id with `os.mkdir(run_dir)` (**no** `exist_ok`), treat `FileExistsError` as the
   refusal, and do it before `orch.run`. The supplied id reaches `RunStateStore.new_run(workflow,
   run_id=...)` through an `Orchestrator.run(..., run_id=None)` keyword. The engine treats
   `run_state is None` as "fresh run" (`engine.py:771` `run_state or new_run(...)`, `:791` store
   prompt, `:799` `"start"` vs `"resume"` settings record, `:887` git-start HEADs), so the CLI
   **must not** pre-build a `RunState` with the supplied id and pass it in — the run would be
   recorded as a resume, lose its prompt and skip `record_git_start`. This is the one sanctioned
   `engine.py` edit of the epic (EPIC Scope-Out updated to say so).
4. `run_id` is a run-options **registry `Option`** in `run_options._BASE_OPTIONS`:
   `Option("run_id", "str", flag="--run-id", help=..., resumable=False)` with **no `env`, no
   `config`, no `ui_group`**. It is not a dashboard-form field and not a resolved setting:
   exclude it from `_effective_settings_snapshot` (`cli.py:876-893` iterates every registry
   option). No `env` because `hooks.py:151` and `isolation/integrator.py:147` export `AO_RUN_ID`
   into hook/agent subprocesses; were `AO_RUN_ID` the option's env var, any `ao run` started
   inside a hook or agent would inherit the parent's id. **Leave `allowed_options()` /
   `ALLOWED_OPTIONS` unchanged**; `test_allow_list_unchanged_and_registry_derived`
   (`tests/test_run_options.py:196-219`, a deliberate security pin) must pass unmodified, so
   `options={"run_id": ...}` keeps being silently dropped by `_render_options`. (Giving the
   Option a `ui_group` is rejected: it adds a "Run id" dashboard field and a
   `GET /api/schema/run-options` `json_schema` property, and would make `launch_resume` — which
   already renders `--run-id <id>` itself at `ui/processes.py:466` — emit a second `--run-id`.)
   `ProcessSupervisor.launch_run` gains an **explicit keyword** `run_id: str | None = None`. When
   set it is validated with the shared pattern, rendered as `[get_option("run_id").flag,
   run_id]` (the flag comes from the registry, not a literal), the `LaunchRecord` is stamped
   immediately, and `_discover_run_id` is skipped (zero polling). `derive_launch_status`
   (`ui/processes.py:124-125`, which returns `STATUS_STARTED` whenever `record.run_id` is set)
   classifies a supplied-id record by whether its run directory exists, so a child that dies on
   a validation error before creating anything shows as `failed_to_start`, not "started"
   forever. `launch_resume` is unchanged.
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
9. A `ProcessSupervisor` test asserting that with `launch_run(..., run_id="x")` the returned
   `LaunchRecord.run_id == "x"`, the argv contains `--run-id x` exactly once, and discovery
   polling did not run (spy on the poll helper or assert elapsed time under a threshold with a
   stub `ao` command). Plus: `options={"run_id": "x"}` is still dropped (allow-list unchanged);
   a supplied-id record whose run directory never appears classifies as `failed_to_start`.
9a. `tests/test_run_options.py::test_every_registry_flag_is_a_real_cli_flag` passes **without**
    any `ao run --run-id` (because `ao resume --run-id` exists, `cli.py:1734`), so add an
    assertion that `--run-id` appears on `run --help` specifically. Regenerate
    `tests/fixtures/run_options_schema.json` (`test_schema_matches_snapshot`): `options_schema()
    ["options"]` gains `run_id`, `json_schema.properties` is unchanged because it is not a UI
    option (F24). A concurrency test: two simultaneous `--run-id X` ⇒ exactly one succeeds (AC3a).
10. Regression: the entire existing `tests/ui/test_processes.py` and the run/resume e2e suites
    pass unmodified — no behavior change without the flag.
11. `uv run pytest -q` green with the delta reported; ruff + format clean; mypy whole-tree count
    reported.

## Risks
- Touching `engine.py`/`runstate.py` run-id minting is the one place this small task can break
  something large. Keep the change to "use the supplied id if present, else mint as before" and
  confirm with `git diff --stat` that nothing else moved.
- Minted ids (no flag) have no duplicate guard: two un-flagged `ao run`s of the same workflow in
  the same second mint the same `<wf>-<seconds>` id (`runstate.py:203`). AC1 keeps that
  byte-identical; the gap is recorded for `T-Dc6Zr2`'s ROADMAP §4 note (F22), not fixed here.
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
  IF NOT RUN_ID_RE.fullmatch(supplied) OR len(supplied) > MAX_RUN_ID_CHARS:   # shared constants
      RAISE OrchestratorError(f"--run-id must match {RUN_ID_RE.pattern} (max {MAX_RUN_ID_CHARS})")
  TRY os.mkdir(store.run_dir(supplied))          # atomic reservation, NO exist_ok  (AC3a)
  EXCEPT FileExistsError:
      RAISE OrchestratorError(f"run {supplied!r} already exists; use `ao resume --run-id {supplied}`")
  RETURN supplied      # then Orchestrator.run(..., run_id=supplied) -> RunStateStore.new_run(run_id=)
```

## Schemas / Interface Notes
- Interface / CLI: `ao run --run-id <id>`; `ProcessSupervisor.launch_run(..., run_id=<id>)` — an
  explicit keyword, **not** an `ALLOWED_OPTIONS` entry (AC4). Governance epic E-Qp4Zm8's
  `cost_cap_usd` hits the same trap and should use the same explicit-kwarg pattern.
- Spec / data schema: none — this is a runtime invocation option, not a spec field (consistent
  with ADR-0003 §3's "invocation-scoped settings ride the CLI/env/config chain, never a workflow
  field").
- Triggers / events: N/A.
- Artifacts: determines the name of `<workspace>/.orchestrator/runs/<run_id>/`.

## Handoff Boundary
- Upstream: `meta/ROADMAP.md` §4 (the recorded gap); ADR-0014 D6.
- Downstream: `T-Lp4Wt6` (supplies `run_id=` on every scheduled launch), `T-Ev3Qm5` (the exact
  fire→run link its overlap detection depends on), `T-Te3Qw8` (asserts the id end-to-end).

## Artifacts
- Docs/comments: `meta/tickets/E-Sc9Rt4-scheduler-triggers/T-Ri7Dz2-run-id-flag/`
- Large outputs: N/A
