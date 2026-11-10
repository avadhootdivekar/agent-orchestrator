# TASK: T-Sd1Kq7-schedule-binding-model

## Metadata
- Task ID: `T-Sd1Kq7-schedule-binding-model`
- Epic ID: `E-Sc9Rt4-scheduler-triggers`
- Owner: developer agent
- Created: 2026-09-06
- Last Updated: 2026-11-10 (Opus design review w01-06: F3/F4/F6/F12/F20/F21 applied)
- Status: Done (2026-11-10)
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
2. `RunArgs` is a closed model built from a **named `SCHEDULE_RUN_ARG_NAMES` tuple** in
   `schedules/models.py` (owner decision F3, 2026-11-10, recorded in `../EPIC.md`), **not** from
   "exactly `ALLOWED_OPTIONS` ∪ `ALLOWED_BOOL_OPTIONS`": the live allow-list now holds 17 entries
   (14 value + 3 bool) including `sentinel.llm.powers` (`act` lets the LLM lane kill and inject
   tasks), `sentinel.llm.budget_usd` and `sentinel.max_parallel_ceiling`, so mirroring it would let
   an untrusted file grant LLM powers and raise spend. The MVP subset is: `model, turn_budget,
   effort, thinking_effort, autocompact, max_attempts, max_turns, max_parallel, budget_total,
   quota_max_wait, quota_poll_interval, self_heal, sentinel.enabled` — and **not** `sentinel.llm.*`
   or `sentinel.max_parallel_ceiling`. An import-time check asserts every name is in
   `run_options.allowed_options() ∪ allowed_bool_options()` (source of truth is `run_options`;
   `schedules/` must **not** import from the `ui/` package). Types and choices come from the
   registry `Option` via `pydantic.create_model`, with aliases for dotted keys (`sentinel.enabled`
   is not a valid field name). Note the registry's `effort` choices are `low/medium/high`; `xhigh`
   belongs to `turn_budget` — do not hard-type `effort` with `xhigh`. `extra="forbid"` makes an
   unknown key a **named** load error rather than the silent drop `_render_options` performs;
   `cost_cap_usd` (governance epic, a registry option) and `run_id` must be **rejected** by
   `RunArgs` (neither is a `RunArgs` key). `RunArgs.as_options() -> dict[str, object]` returns
   exactly what `launch_run(options=...)` expects.
3. Cross-field validation, each producing a `ScheduleFileError` naming the schedule id and the
   offending field: `kind=cron` requires `schedule`; `kind=interval` requires
   `interval_seconds >= MIN_INTERVAL_SECONDS (60)`; `kind=file_watch` requires `watch`;
   `kind=webhook` requires `webhook`; exactly one of `workflow` / `template` is set; `params` /
   `instance_id` only with `template`; `id` matches `^[a-z0-9][a-z0-9-_]*$` **and is at most 64
   characters** (the minted `ao-<id>-<16 chars>` run id must fit the 128-char run-id limit; F12);
   `template` is a bare template **name** (pattern with no `/`, no `.`, no `~`) — F4: `templates.
   load_template` accepts a *directory path* before name lookup (`templates/__init__.py:474-476`),
   so a path in an untrusted `schedules.yaml` could load any directory on the machine; ids are unique
   within the file; `timezone` parses via `ZoneInfo`; `schedule` parses via `croniter`;
   `jitter_seconds` in `[0, 3600]`; a `webhook.secret` literal key is rejected with a message
   naming `secret_env` / `secret_file` as the alternatives.
4. Every path-bearing field (`workflow`, `template` (name-only, AC3), `prompt_file`, `until.artifact_exists`,
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
10a. **Binding → `Trigger` adapter (F6).** `ScheduleBinding.to_trigger()` is owned here and covers
    `cron` and `interval` only. `Trigger.type` (`models.py:706`) has no `file_watch`/`webhook`
    members and this task adds only `interval`; event kinds do **not** go through `Trigger` — the
    engine's `TriggerSource` protocol (`observe`/`commit`, see `T-Ev3Qm5` AC1a and HLD §7.1)
    serves them. Document that in `to_trigger()`'s docstring; calling it for an event kind raises.
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
    every owned path. `uv run mypy src` reported as a whole-tree count (baseline: **0** errors — commit
    `991391b` made `mypy src` clean; F20). Coverage ≥80 % on `schedules/` and `service/statefile.py`.

## Risks
- `models.py` and `specs/workflow.schema.json` are files other epics have historically fenced
  off. Keep the diff to `Trigger` / `$defs.trigger` and confirm with `git diff --stat` before
  handing off; a wider diff will be rejected at review.
- Retyping option names/types/choices instead of deriving them from the registry is the most
  likely way this task introduces a silent divergence six months from now. AC2 requires the
  import-time membership check and registry-derived types.
- `schedules/` sits next to two existing scheduling modules: `scheduling/` (task co-scheduling,
  `scheduling/overlap.py`) and `scheduler.py` (trigger ABC). Keep the name, but the package
  `__init__` docstring must say how it differs from `scheduling/` (F21).
- Defaults inheritance (AC1) is easy to implement as "merge at read time in every consumer",
  which spreads the rule across four modules. Resolve it once, in `ScheduleFile.bindings`.

## Dependencies
- None. This is the epic's first task and can start immediately, in parallel with `T-Fr2Nx8`.
- Reads (read-only): `run_options.py` (`allowed_options()`, `allowed_bool_options()`, `Option`s),
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

## Implementation notes (2026-11-10, developer)
All 15 ACs implemented. Deviations / decisions:
- `ScheduleFile.from_data(data, source=)` is the validating entry point; it folds pydantic's aggregate
  into one `ScheduleFileError` naming file, schedule id and field. `ScheduleStore.load` uses it.
- Kind-specific fields of the *wrong* kind (e.g. `schedule` with `kind=interval`) are rejected, not
  ignored; `webhook` requires exactly one of `secret_env` / `secret_file`; `instance_id` may only use
  the `{date}` / `{rand6}` tokens and must expand to an id-shaped string (no path separators);
  `version` major must be `1`.
- `ResolvedBinding` is a type alias of `ScheduleBinding` (inheritable fields filled, typed Optional).
- `specs/schedules.schema.json` is generated from the model; a test guards drift (enum order normalised).
- `Trigger.interval_seconds` carries no `>= 60` check (that floor is the binding's, AC3).
- Coverage: schedules/ 99-100 % (store.py 90 %: OSError branches), statefile.py 100 %.
Unfinished: none. Follow-up: migrate `registry.py`, `boot_resume.py` and the supervisor snapshot to
`service/statefile.py` (out of scope here, AC8).

## Review fixes (2026-11-11, developer; Opus review of 47babc1, w04-01)
- W1 `ScheduleStore.save` round-trips `from_data(to_data())` before writing (invalid model -> `ScheduleFileError`, file untouched).
- W2 `to_trigger()` raises `ValueError` on a cron binding with unresolved timezone (use `ScheduleFile.bindings`).
- W3 numeric `run_args` bounded via `SCHEDULE_RUN_ARG_BOUNDS`, strict ints/floats/bools (no `true`/`"4"` coercion).
- W4 cron: only 5 fields; min gap over 8 fixed-anchor fires must be >= `MIN_INTERVAL_SECONDS`.
- S1 max_length 64 on `template`/`instance_id`; S2 tz must be in `available_timezones()` minus host-dependent names; S3 `interval_seconds` <= 1 year.
- Not done here: S3 root `Trigger.interval_seconds ge=1` (touches `models.py`, owned by in-flight units), W5 (statefile, belongs to T-Fr2Nx8), S4-S8.

## Binding Low fixes (2026-11-11, developer; w07-02, re-review w06-01 N1-N3)
- N1 root `Trigger.interval_seconds` is `Field(ge=1, le=MAX_INTERVAL_SECONDS)`; the constant moved to `models.py` (re-imported by `schedules/models.py`); `specs/workflow.schema.json` (the hand-written schema that carries `Trigger`) gained `maximum: 31536000`. `specs/schedules.schema.json` is generated from `ScheduleFile` (no root Trigger) and is unchanged.
- N2 `ScheduleStore.save` uses the new shared `statefile.atomic_write_bytes` (mkstemp temp name, existing-mode preserved). Side effect: a newly created `schedules.yaml` is now 0600 (was umask default), as with N4.
- N3 `statefile._fsync_dir` logs a WARNING when the directory fsync raises `OSError` (file is already in place, so no raise).
- Tests: `test_root_trigger_interval_bounds_*`, `test_save_uses_unique_tmp_names`, `test_concurrent_unlocked_saves_do_not_collide`, `test_durable_dir_fsync_failure_is_logged`.
- Not done (out of brief): N4-N7 (N7 belongs to `fire_store.py`'s own review).
