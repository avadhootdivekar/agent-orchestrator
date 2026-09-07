# TASK: T-Sd1Kq7-schedule-binding-model

## Metadata
- Task ID: `T-Sd1Kq7-schedule-binding-model`
- Epic ID: `E-Sc9Rt4-scheduler-triggers`
- Owner: developer agent
- Created: 2026-09-06
- Last Updated: 2026-09-06
- Status: Draft
- Estimate: 2 days

## Requirements Mapping
- Requirement IDs: FR-1, FR-3, NFR-3 (see `../EPIC.md`)

## Description
The foundation layer: the workspace-level schedule binding file, its pydantic model, its JSON
Schema, and an atomic file-locked store. Plus the two small additive changes that let
`interval` schedules ride the existing `Scheduler` ABC, and the shared atomic-JSON-state helper
the next three tasks all need.

This task defines the vocabulary every later task consumes, so its names are load-bearing. Read
HLD §5.1, §5.1.1, §7.1, §7.3 and §13.3 before starting.

Files you own (create/edit freely):
- `src/agent_orchestrator/schedules/__init__.py`, `models.py`, `store.py` (new package)
- `src/agent_orchestrator/service/statefile.py` (new)
- `specs/schedules.schema.json` (new)
- `src/agent_orchestrator/scheduler.py` (edit: add `IntervalScheduler`; leave `Scheduler`,
  `ManualScheduler`, `CronScheduler` behaviorally unchanged)
- `src/agent_orchestrator/models.py` (edit: **only** `Trigger`)
- `specs/workflow.schema.json` (edit: **only** `$defs.trigger`)
- `tests/schedules/test_models.py`, `tests/schedules/test_store.py`,
  `tests/schedules/test_interval_scheduler.py`, `tests/service/test_statefile.py` (new)

Do NOT touch: anything under `service/` other than the new `statefile.py`; `ui/`; `engine.py`;
`spec.py`; `dag.py`; `cli.py`; `docs-md/task-isolation-hld.md` or `ADR-0013` (owned by a
concurrent epic).

## Acceptance Criteria

### Binding model (`schedules/models.py`)
1. `ScheduleBinding` (pydantic, `extra="forbid"`) carries every field in HLD §5.1's table with
   the stated types and defaults, and `ScheduleFile` carries `version: str` plus
   `defaults: ScheduleDefaults` and `schedules: list[ScheduleBinding]`. Field-level defaults are
   `None`; `ScheduleFile.bindings` resolves each `None` against `defaults` so a consumer never
   has to know which layer a value came from.
2. `RunArgs` is a closed model whose fields are **exactly** `ui/processes.py`'s
   `ALLOWED_OPTIONS` ∪ `ALLOWED_BOOL_OPTIONS`, typed (`model: str`, `effort:
   Literal["low","medium","high","xhigh"]`, `max_attempts/max_turns/max_parallel/budget_total/
   quota_max_wait/quota_poll_interval: int`, `self_heal: bool`), with `extra="forbid"` so an
   unknown key is a **named** load error rather than the silent drop `_render_options` performs.
   `RunArgs.as_options() -> dict[str, object]` returns exactly what `launch_run(options=...)`
   expects. Import the allow-list names from `ui/processes.py` — do not retype the list, so a
   future addition there cannot silently diverge.
3. Cross-field validation, each producing a `ScheduleFileError` naming the schedule id and the
   offending field: `kind=cron` requires `schedule`; `kind=interval` requires
   `interval_seconds >= MIN_INTERVAL_SECONDS (60)`; `kind=file_watch` requires `watch`;
   `kind=webhook` requires `webhook`; exactly one of `workflow` / `template` is set; `params` /
   `instance_id` only with `template`; `id` matches `^[a-z0-9][a-z0-9-_]*$`; ids are unique
   within the file; `timezone` parses via `ZoneInfo`; `schedule` parses via `croniter`;
   `jitter_seconds` in `[0, 3600]`; a `webhook.secret` literal key is rejected with a message
   naming `secret_env` / `secret_file` as the alternatives.
4. Every path-bearing field (`workflow`, `prompt_file`, `until.artifact_exists`,
   `until.gate_file`, `watch.paths[]`) is validated as workspace-relative: an absolute path or
   any `..` segment is rejected at load time with a named error. Resolution itself uses
   `LocalFsArtifactStore`'s existing workspace-root guard — do not write a second one.

### Store (`schedules/store.py`)
5. `ScheduleStore(workspace_root)` exposes `path` (`<root>/.ao/schedules.yaml`), `load()`,
   `save(file)` and `mutate(fn)`. `mutate` acquires an exclusive `flock` on a **dedicated
   companion `.lock` file** (never the payload, whose inode is replaced on every save),
   re-reads the current on-disk file under the lock, applies `fn`, saves, releases — the exact
   pattern and rationale in `service/registry.py`'s module docstring.
6. `load()` on a missing file returns an empty `ScheduleFile`, not an error. `load()` on an
   unparseable or schema-invalid file raises `ScheduleFileError` whose message names the file,
   the schedule id where determinable, and the field.
7. `save()` writes atomically (tmp + `os.replace`) and round-trips: `load(save(x)) == x` for
   every field, including a binding that sets none of the optional fields.

### Shared state helper (`service/statefile.py`)
8. `read_json_model(path, model_cls, *, default_factory)` and
   `write_json_model_atomic(path, model)` implement the write-then-rename + corrupt-file-warn-
   and-start-clean idiom currently duplicated in `service/registry.py`,
   `service/boot_resume.py` and `Supervisor`'s snapshot code. This task **adds** the helper and
   uses it in its own new code only; refactoring the three existing call sites is explicitly out
   of scope (it would cross this task's ownership boundary and risk the service package while
   the epic is mid-flight) — record it as a follow-up in `STATUS.md`.

### Interval trigger (`scheduler.py`, `models.py`, `specs/workflow.schema.json`)
9. `Trigger` gains `interval_seconds: int | None = None` and `"interval"` in its `type` Literal.
   `specs/workflow.schema.json`'s `$defs.trigger` gains the enum member, the property, and an
   `if type==interval then required:[interval_seconds]` clause alongside the two that already
   exist. Both changes are strictly additive: `tests/test_scheduler.py` and every existing spec
   fixture must pass **unmodified**, and a spec with no `triggers` key must still default to
   `[{type: "manual"}]`.
10. `IntervalScheduler(Scheduler).next_fire(trigger, now)` returns `now +
    timedelta(seconds=trigger.interval_seconds)`, or `None` when `interval_seconds` is unset.
    It holds no state — the *anchor* is the caller's `now`, which is what lets the engine pass
    `last_scheduled_for` and get a stable series.
11. The `Scheduler` ABC docstring is amended to state the contract the engine relies on: *a
    returned value at or before the caller's current time means "fire now"*. No signature change.

### Tests
12. `tests/schedules/test_models.py`: one test per rejection case in AC3/AC4 asserting the error
    **message** names the schedule id and field (not merely that it raised); plus a positive
    round-trip covering defaults inheritance.
13. `tests/schedules/test_store.py`: round-trip; missing file ⇒ empty; corrupt file ⇒ named
    error; and a real concurrency test — two processes (or two `flock`ing handles) calling
    `mutate` where a bare `load()`+`save()` would lose one writer's change, asserting both
    survive. Mirror `tests/service/test_registry.py`'s existing approach.
14. `tests/schedules/test_interval_scheduler.py`: fixed clock; `interval_seconds` unset ⇒
    `None`; a series of three `next_fire` calls anchored on the prior result produces evenly
    spaced instants.
15. `uv run pytest -q tests/schedules tests/service tests/test_scheduler.py` green with the
    count delta vs. the epic baseline reported. `ruff check` + `ruff format --check` clean on
    every owned path. `uv run mypy src` reported as a whole-tree count (baseline: 4 pre-existing
    `_version.py` errors). Coverage ≥80 % on `schedules/` and `service/statefile.py`.

## Risks
- `models.py` and `specs/workflow.schema.json` are files other epics have historically fenced
  off. Keep the diff to `Trigger` / `$defs.trigger` and confirm with `git diff --stat` before
  handing off; a wider diff will be rejected at review.
- Retyping the `ALLOWED_OPTIONS` list instead of importing it is the most likely way this task
  introduces a silent divergence six months from now. AC2 requires the import.
- Defaults inheritance (AC1) is easy to implement as "merge at read time in every consumer",
  which spreads the rule across four modules. Resolve it once, in `ScheduleFile.bindings`.

## Dependencies
- None. This is the epic's first task and can start immediately, in parallel with `T-Fr2Nx8`.
- Reads (read-only): `ui/processes.py` (`ALLOWED_OPTIONS`, `ALLOWED_BOOL_OPTIONS`),
  `service/registry.py` (the `mutate()` pattern to mirror), `artifacts.py`
  (`LocalFsArtifactStore`'s root guard), `project_config.py` (relative-path anchoring idiom).

## Pseudocode / Algorithm
```text
FUNCTION ScheduleStore.mutate(fn):
  path.parent.mkdir(parents=True, exist_ok=True)
  WITH open(path.name + ".lock", "a+") AS lockfile:      # dedicated file: payload inode is replaced
      flock(lockfile, LOCK_EX)
      TRY:
          current = self.load()                          # ALWAYS re-read under the lock
          updated = fn(current)                           # fn must be pure w.r.t. any earlier snapshot
          self.save(updated)
          RETURN updated
      FINALLY:
          flock(lockfile, LOCK_UN)

FUNCTION ScheduleFile.bindings() -> list[ResolvedBinding]:
  FOR b IN self.schedules:
      YIELD b.model_copy(update={
          f: getattr(self.defaults, f) FOR f IN INHERITABLE_FIELDS IF getattr(b, f) IS None
      })
      # INHERITABLE_FIELDS = timezone, overlap, catch_up, catch_up_window_seconds,
      #                      jitter_seconds, grace_seconds, max_concurrent
```

## Schemas / Interface Notes
- Interface: `schedules.ScheduleStore` (`load`/`save`/`mutate`/`path`),
  `schedules.models.{ScheduleFile, ScheduleBinding, ScheduleDefaults, RunArgs, UntilSpec,
  WatchSpec, WebhookSpec}`, `schedules.errors.{ScheduleFileError, ScheduleNotFoundError,
  DuplicateScheduleIdError}`, `service.statefile.{read_json_model, write_json_model_atomic}`,
  `scheduler.IntervalScheduler`.
- Spec / data schema: `specs/schedules.schema.json` (new, mirrors HLD §5.1);
  `specs/workflow.schema.json` `$defs.trigger` (additive `interval`).
- Triggers / events: defines the `cron` / `interval` / `file_watch` / `webhook` binding
  vocabulary; `IntervalScheduler` is the only trigger implementation landed here.
- Artifacts: reads/writes `<workspace>/.ao/schedules.yaml` + its `.lock` companion.

## Handoff Boundary
- Upstream: HLD §5.1/§5.1.1/§7.1/§7.3/§13.3; ADR-0014 D2, D7, D9.
- Downstream: `T-Ev3Qm5` (consumes `ScheduleFile.bindings()` and `IntervalScheduler`),
  `T-Fr2Nx8` (uses `service/statefile.py`), `T-Lp4Wt6` (uses `RunArgs.as_options()`),
  `T-Cl6Jn9` (uses `ScheduleStore.mutate` for `add`/`remove`/`enable`/`disable`).

## Artifacts
- Docs/comments: `meta/tickets/E-Sc9Rt4-scheduler-triggers/T-Sd1Kq7-schedule-binding-model/`
- Large outputs: N/A
