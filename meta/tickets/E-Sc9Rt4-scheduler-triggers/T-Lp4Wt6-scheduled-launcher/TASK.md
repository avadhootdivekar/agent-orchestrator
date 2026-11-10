# TASK: T-Lp4Wt6-scheduled-launcher

## Metadata
- Task ID: `T-Lp4Wt6-scheduled-launcher`
- Epic ID: `E-Sc9Rt4-scheduler-triggers`
- Owner: developer agent
- Created: 2026-09-06
- Last Updated: 2026-11-10 (Opus design review w01-06: F1/F2/F4/F10/F11/F20 applied)
- Status: Draft
- Estimate: 2 days

## Requirements Mapping
- Requirement IDs: FR-7, FR-8, NFR-3 (see `../EPIC.md`)

## Description
The mechanism half of a fire: turn a `FireDecision` into a real, detached `ao run` through the
**existing** `ProcessSupervisor.launch_run` path, with the two-phase intent record that makes it
at-most-once, and with template instantiation for bindings that want a fresh instance per fire.

This task adds **zero new argv surface**. Everything a schedule can pass to `ao run` goes through
`ui/processes.py`'s existing `ALLOWED_OPTIONS` / `ALLOWED_BOOL_OPTIONS` allow-list via
`RunArgs.as_options()` (the curated `SCHEDULE_RUN_ARG_NAMES` subset, F3), plus the one explicit
`launch_run(run_id=)` keyword from `T-Ri7Dz2` (F1: **not** an `options` entry — `options={"run_id":
...}` is silently dropped). That constraint is a security requirement (ADR-0014 D9), not a
convenience: `.ao/schedules.yaml` is untrusted workspace content.

Read HLD §6.6 and §13.3, and ADR-0014 D5/D6/D9. Read the merged code of `T-Ev3Qm5` (the
`ScheduledLauncher` Protocol you implement), `T-Fr2Nx8` (the store), `T-Sd1Kq7` (`RunArgs`) and
`T-Ri7Dz2` (the `run_id` option).

Files you own (create/edit freely):
- `src/agent_orchestrator/service/schedule_launch.py` (new)
- `tests/service/test_schedule_launch.py` (new)

Do NOT touch: `service/schedule_engine.py`, `service/fire_store.py`, `schedules/`,
`ui/processes.py`, `templates/`, `project_config.py`, `cli.py` — all read-only imports.

## Acceptance Criteria
1. `ScheduledLauncher(state_dir, *, process_supervisor_factory=..., project_config_loader=...,
   template_loader=..., clock=...)` — **F2: it receives the engine's long-lived per-workspace
   `ProcessSupervisor` instance** (the one `T-Ev3Qm5` AC1 caches and reconciles each tick), not a
   private one; a launcher-private supervisor would never reap its children and every finished
   scheduled run would be left a zombie that `is_running` reports alive — implements `T-Ev3Qm5`'s Protocol:
   `launch(decision: FireDecision) -> FireIntentRecord`. Every collaborator is injectable so
   tests spawn nothing.
2. **Two-phase, in this order, no exceptions**: write the `intended` record (fsync-durable, via
   `T-Fr2Nx8`'s store) → resolve the workflow → launch → update the record to `launched` with
   `launch_id`, `pid` and `run_id`. If anything between phases raises, the record is updated to
   `launch_failed` with the reason and `ScheduleStateStore.record_failure` is called; the
   exception is **not** re-raised (a failed fire must not kill the engine tick).
3. The run id supplied to `ao run` is `ao-<schedule_id>-<scheduled_for as %Y%m%dT%H%M%SZ>`,
   built by a named module-level function so `T-Te3Qw8` can assert the exact string.
   **`scheduled_for` is converted to UTC (`astimezone(UTC)`) before formatting** and before the
   literal `Z` is appended (F11). It is passed through `launch_run(run_id=...)` — `T-Ri7Dz2`'s
   explicit keyword (F1) — never through `options` (dropped) and never as raw argv. The schedule
   id is capped at 64 chars in `T-Sd1Kq7`, so the id fits the 128-char run-id limit (F12).
4. The shared `ProcessSupervisor` (AC1) is constructed — by the engine — with
   `run_id_discovery_timeout=0.0`. Blocking the engine tick for up to 10 s per launch is a defect,
   not a tuning choice; a test must assert the constructed timeout. Because `run_id` is supplied,
   `_discover_run_id` is skipped entirely anyway (`T-Ri7Dz2` AC4); the zero timeout is the
   fallback.
5. Workflow resolution:
   - `binding.workflow` → resolved through `LocalFsArtifactStore`'s workspace-root guard; a path
     that escapes the root, or that does not exist, is a `launch_failed` with a named reason (not
     a crash, and not a silent skip).
   - `binding.template` → **F4: resolved by name through `templates.discover_templates` only,
     never through `load_template`'s directory-path branch** (`templates/__init__.py:474-476`
     accepts a path before it tries name lookup, so a path in an untrusted file could load any
     directory on the machine; `T-Sd1Kq7` already rejects a non-name at load, this is the
     second wall). Then, with the discovered template,
     `templates.instantiate(tmpl, root, slug_or_id=<rendered instance_id>, params=binding.params,
     prompt_text=<prompt_file contents or None>)`, using the returned `InstantiateResult.
     workflow_path`. `instance_id` tokens `{date}` (UTC `%Y%m%d`) and `{rand6}` are rendered by a
     named helper; an omitted `instance_id` falls back to the template's own `id_pattern`.
6. **F10: pass neither `reposets` nor `agents`.** The earlier premise (boot-resume reads them from
   `.ao/config.yaml`) was wrong: boot-resume recovers them from the prior launch's **argv**
   (`service/boot_resume.py:108-109, 178-186`). `ao run` already falls back to `cfg.reposets` /
   `AO_REPOSETS` (`cli.py:183, 199`) and the child's cwd is the workspace root
   (`processes.py:372`), so the launcher needs no second resolution path. Pass only
   `workspace=<root>` for a no-repo-set workflow (ADR-0022, matching `_decide_and_act`'s
   `extra`). They are deliberately **not** schedule fields (ADR-0014 D9); a binding that tries to
   set them fails at load time in `T-Sd1Kq7`, and this module must not accept them either.
7. The prompt is passed as `prompt=<text>`, read from `binding.prompt_file` through the root
   guard. It is never passed as `--prompt` on the command line — `ProcessSupervisor` already
   writes it to a file, because argv is world-readable in `ps` and subject to `ARG_MAX`.
8. `RunArgs.as_options()` output is handed to `launch_run(options=...)` verbatim and `run_id` is
   passed as the separate explicit keyword (F1). **No `options` key is added that is not in
   `SCHEDULE_RUN_ARG_NAMES` ⊆ `ALLOWED_OPTIONS` ∪ `ALLOWED_BOOL_OPTIONS`** — assert this in a test
   that reads the allow-list at runtime rather than hardcoding the expected keys, so a future
   divergence fails loudly; and assert `"run_id" not in options`.

### Tests
9. Happy path with `tests/ui/conftest.py`'s `StubSupervisor` (reuse it; hoist the fixture if the
   new test module cannot import it): assert `launch_calls[0]` carries the exact
   `workflow_path`, `prompt`, `workspace` and `options`, the constructed `run_id` string as the
   explicit `run_id` kwarg, and **no** `reposets`/`agents` (F10) — and that the fire record went `intended` → `launched` with the pid and run
   id stamped. No subprocess is spawned anywhere in this test module.
10. Ordering: monkeypatch the supervisor to raise, and assert the `intended` record was already
    on disk **before** the raise (read it in the raising stub), then that it ends `launch_failed`
    and `launch()` returned normally rather than propagating.
11. Template path: a temp workspace with a minimal template; assert a fresh instance directory
    per fire for `instance_id: "nightly-{date}"` across two different fixed dates, and that the
    rendered `workflow.json` is what gets launched.
12. Security: a binding whose `workflow` is `../../etc/passwd` or an absolute path produces
    `launch_failed` with a traversal reason and **no** `launch_run` call; a `RunArgs` carrying an
    unknown key never reaches this module (it failed at load in `T-Sd1Kq7`) — assert that too, as
    a characterization test, so a future relaxation there fails here. A binding whose
    `template` is a path (`/tmp/x`, `../x`, `~/x`) never reaches `load_template` (F4).
13. `uv run pytest -q tests/service` green with the full-suite delta reported; ruff + format
    clean; mypy whole-tree count reported (baseline 0 errors; F20); coverage ≥80 % on `service/schedule_launch.py`.

## Risks
- The two-phase ordering (AC2) is the entire at-most-once guarantee. AC10's test must read the
  record from **inside** the raising stub, not merely assert the final state — otherwise a future
  refactor that writes the record after the launch would still pass.
- `templates.instantiate` is idempotent on an existing id: a binding whose `instance_id` has no
  `{date}`/`{rand6}` token will re-use the same instance every fire. That is usually what the
  user wants for "keep pushing this epic", and surprising for "nightly benchmark". Document the
  behavior in the module docstring; do not try to guess.
- `_discover_run_id` costing 10 s (AC4) is easy to reintroduce by constructing
  `ProcessSupervisor(root)` with defaults somewhere in a later refactor.

## Dependencies
- `T-Ev3Qm5` (the `ScheduledLauncher` Protocol and `FireDecision`), `T-Fr2Nx8` (the stores),
  `T-Sd1Kq7` (`RunArgs`), `T-Ri7Dz2` (the `run_id` option). Read all four's merged code.
- Reads (read-only): `ui/processes.py`, `templates/__init__.py`, `project_config.py`,
  `artifacts.py`.

## Pseudocode / Algorithm
```text
FUNCTION ScheduledLauncher.launch(d) -> FireIntentRecord:
  fires.write(FireIntentRecord(fire_key=d.fire_key, status="intended",
                               workspace_root=d.workspace_root, schedule_id=d.schedule_id,
                               scheduled_for=d.scheduled_for, effective_due=d.effective_due,
                               created_at=clock()))                        # fsync BEFORE anything else
  TRY:
      IF d.binding.template IS NOT NULL:
          tmpl   = discover_templates_by_name(d.binding.template, d.workspace_root)   # NAME only (F4)
          result = templates.instantiate(tmpl, d.workspace_root,
                                         slug_or_id=render_instance_id(d.binding, d.scheduled_for),
                                         params=d.binding.params,
                                         prompt_text=read_prompt(d) )
          workflow_path = result.workflow_path
      ELSE:
          workflow_path = safe_join(d.workspace_root, d.binding.workflow)      # root-guarded
          IF NOT exists(workflow_path): RAISE LaunchResolutionError("workflow not found: ...")

      run_id  = scheduled_run_id(d.schedule_id, d.scheduled_for)               # ao-<id>-<UTC>, UTC-normalised
      options = d.binding.run_args.as_options()                                # curated subset; no run_id
      ps      = process_supervisor_factory(d.workspace_root)                   # the engine's SHARED instance (F2)
      rec     = ps.launch_run(workflow_path=workflow_path, prompt=read_prompt(d),
                              options=options, run_id=run_id,                  # explicit kwarg (F1)
                              workspace=workspace_arg_for(workflow_path))      # no reposets/agents (F10)

      fires.update(d.fire_key, status="launched", launch_id=rec.launch_id, pid=rec.pid,
                   run_id=run_id, argv_digest=sha256(" ".join(rec.argv)))
      state.record_fire(d.workspace_root, d.schedule_id, scheduled_for=d.scheduled_for,
                        run_id=run_id, fire_key=d.fire_key)
      events.emit("schedule.fired", "INFO", run_id=run_id, fire_key=d.fire_key,
                  lateness_seconds=(clock() - d.scheduled_for).total_seconds())
  EXCEPT Exception AS e:
      fires.update(d.fire_key, status="launch_failed", reason=str(e))
      state.record_failure(d.workspace_root, d.schedule_id, str(e))
      events.emit("schedule.fire_failed", "ERROR", error=str(e))               # NOT re-raised
  RETURN fires.get(d.fire_key)
```

## Schemas / Interface Notes
- Interface / API: `service.schedule_launch.{ScheduledLauncher, scheduled_run_id,
  render_instance_id, LaunchResolutionError}`; implements `T-Ev3Qm5`'s `ScheduledLauncher`
  Protocol; consumes `ProcessSupervisor.launch_run(workflow_path, prompt, reposets, agents,
  options)`.
- Spec / data schema: consumes `ScheduleBinding` / `RunArgs`; writes `FireIntentRecord`.
- Triggers / events: emits `schedule.fired` / `schedule.fire_failed`.
- Artifacts: creates `<workspace>/.orchestrator/runs/ao-<schedule_id>-<ts>/` (via the engine) and
  `<workspace>/.orchestrator/ui/launches/<launch_id>.json` (via `ProcessSupervisor`); may create
  a template instance directory per fire.

## Handoff Boundary
- Upstream: `T-Ev3Qm5`, `T-Fr2Nx8`, `T-Sd1Kq7`, `T-Ri7Dz2`; HLD §6.6/§13.3; ADR-0014 D5/D6/D9.
- Downstream: `T-Sv5Hb3` (constructs the launcher and injects it into the engine), `T-Cl6Jn9`
  (`ao schedule run-now --local` reuses this exact class so the two paths cannot diverge),
  `T-Te3Qw8` (asserts the launched run end-to-end).

## Artifacts
- Docs/comments: `meta/tickets/E-Sc9Rt4-scheduler-triggers/T-Lp4Wt6-scheduled-launcher/`
- Large outputs: N/A
