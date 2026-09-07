# Epic: E-Sc9Rt4-scheduler-triggers

## Metadata
- Epic ID: `E-Sc9Rt4-scheduler-triggers`
- Title: Scheduler daemon — cron & event triggers owned by `ao service`
- Owner: architect agent (design) → dev-epic agent (delivery)
- Created: 2026-09-06
- Last Updated: 2026-09-06
- Status: Draft — design complete, implementation not started
- Origin ask: user request. `Trigger(type=manual|cron|event)` is modelled and validated but
  nothing executes a schedule (ROADMAP §1, §3.2). Decisions D1/D3/D4/D5/D8 in ADR-0014 were
  locked by the user before design work started and are not re-litigated.
- Mirror: [`meta/tickets/E-Sc9Rt4-scheduler-triggers/EPIC.md`](../../meta/tickets/E-Sc9Rt4-scheduler-triggers/EPIC.md)
  (task-level tracking and the sprint plan live there; this file is the running narrative +
  evidence log)

## Goal

Make schedules fire themselves, owned by the one process that is already always running.

`ao service run` gains a `ScheduleEngine` — a signal-free sibling of `Supervisor`, driven from the
same 2-second monitor loop — that evaluates every registered workspace's `.ao/schedules.yaml` and
materializes runs through the existing `ProcessSupervisor.launch_run` path. Concretely:

- **Cron and interval schedules** with per-schedule overlap (`skip` / `queue` / `allow`),
  catch-up (`once` / `skip`, bounded window, never backfilling), deterministic jitter, and
  per-workspace plus service-wide concurrency caps.
- **At-most-once fires**: an fsync-durable intent record written *before* the launch; an orphaned
  intent is reconciled but never relaunched; a recorded fire key suppresses re-firing, catch-up
  re-firing, and clock-rewind re-firing alike.
- **Event triggers behind the existing `Scheduler` ABC**: polled file-watch (whose `scheduled_for`
  is the newest changed mtime, making event fires replay-safe) and an authenticated,
  default-off HMAC webhook listener.
- **Loop-style `until`**: re-run on a schedule until an artifact appears, a gate JSON field flips,
  or `max_runs` is reached — the inter-run sibling of the engine's intra-run `LoopSpec`.
- **`ao schedule` CLI** (list / next / add / remove / enable / disable / run-now / history /
  validate / daemon) and a small dashboard panel.
- **`ao run --run-id`**, which makes the fire→run link exact and closes ROADMAP §4's
  attribution caveat for this path.

Full design: [`docs-md/scheduler-triggers-hld.md`](../scheduler-triggers-hld.md).
Decision record: [`docs-md/adr/ADR-0014-service-owned-scheduler-and-triggers.md`](../adr/ADR-0014-service-owned-scheduler-and-triggers.md).

## Acceptance Criteria (testable)

See [`EPIC.md`](../../meta/tickets/E-Sc9Rt4-scheduler-triggers/EPIC.md)'s FR-1..FR-14 /
NFR-1..NFR-6 for the full, traceable list with per-requirement verification. Summary:

1. A `cron` or `interval` binding in `.ao/schedules.yaml` fires a real run from `ao service run`,
   with the run id `ao-<schedule_id>-<scheduled_for>` and a fire record linking the two.
2. Overlap policy is honored: `skip` suppresses (and records) a fire while a run is active,
   `queue` buffers exactly one, `allow` proceeds within the concurrency caps. No `replace`.
3. Downtime spanning N fire instants produces **exactly one** catch-up fire when within the
   window, and zero when beyond it — never N.
4. A crash between the intent record and the process spawn produces a **missed** run, never a
   duplicate; the operator sees `schedule.fire_orphaned`.
5. A clock rewound by an hour, and a DST transition, produce no double fire (the fall-back
   repeated local hour is the documented exception, pinned by a test).
6. A file-watch binding fires once when a watched path changes and does not fire on the runs it
   itself produces.
7. A correctly-signed webhook delivery fires through the normal policy path; a replayed one is
   409; a wrong signature and an unknown schedule are indistinguishable 401s; no log line or
   response body ever contains secret or signature material.
8. An `until` schedule terminates on `max_runs` / `artifact_exists` / a gate field, shows
   `completed`, and is restartable with `ao schedule enable`.
9. A workspace with no `.ao/schedules.yaml` behaves exactly as before the epic — zero schedule
   events, an `ao service status` payload identical apart from the new `schedules` key.
10. One unparseable schedules file, or one schedule that raises, affects neither the daemon nor
    any other workspace.
11. Toolchain gate: `pytest` green with a reported delta, `ruff check` / `ruff format --check`
    clean, `mypy src` at or below its existing **4-error `_version.py` baseline** (those four are
    pre-existing and not this epic's to fix), `make ui-typecheck` / `make ui-test` green, and
    ≥80 % coverage on every new module (≥90 % on `service/webhook.py`).

## Design Decisions (locked before implementation — early-gate review target)

Recorded in full in [ADR-0014](../adr/ADR-0014-service-owned-scheduler-and-triggers.md); one line
each here.

- **D1** — The supervisor owns scheduling (it already has the singleton lock, the registry, the
  state dir, the loop and a headless launch path); a per-workspace scheduler process is rejected
  as the production topology and survives only as `ao schedule daemon` for dev/test.
- **D2** — Schedules are bound in a workspace-level `.ao/schedules.yaml` (a workflow file cannot
  say which params, prompt or run args a scheduled invocation should use); `WorkflowSpec.triggers`
  remains a declaration, adopted via `ao schedule add --from-workflow`.
- **D3** — Overlap is `skip` (default) | `queue` | `allow`, borrowed from Temporal and Argo;
  there is deliberately no `replace`, because killing an in-flight agent run destroys paid work.
- **D4** — Catch-up is systemd's "run once if missed, within a bounded window", not Airflow's
  backfill; non-backfill is structural (the backlog collapses before any policy check).
- **D5** — Fires are **at-most-once**: intent written and fsynced before the launch, an orphaned
  intent never relaunched, and any recorded status suppressing that fire key forever.
- **D6** — Scheduled runs go through `ProcessSupervisor.launch_run`, and `ao run` gains
  `--run-id` so the fire→run link is exact rather than inferred by a 10-second directory diff.
- **D7** — Event triggers reuse the existing `Scheduler.next_fire` ABC unchanged; file watching
  is polling, not inotify, with a content-derived `scheduled_for`.
- **D8** — The webhook is HMAC-authenticated on its own default-off port — not on the hub (which
  would expose the workspace inventory) and not on the dashboard (which is worse).
- **D9** — `.ao/schedules.yaml` is untrusted workspace content: no free-form argv (the existing
  `ALLOWED_OPTIONS` allow-list is reused verbatim), no path escapes, no inline secrets.
- **D10** — The dashboard and CLI *request* fires through a drop directory; the engine stays the
  only launch authority.

## Decomposition

**No two tasks create or edit the same new file.** Each ticket states its owned files and an
explicit "Do NOT touch" list, and each says to read the prior task's *merged code* rather than
re-deriving the interface from the HLD — the rule that prevented interface drift in E-GIytcL.

| Task | Owns | Requirements | Est. |
|---|---|---|---|
| `T-Sd1Kq7-schedule-binding-model` | `schedules/` package, `specs/schedules.schema.json`, `service/statefile.py`, `Trigger`+`IntervalScheduler` | FR-1, FR-3, NFR-3 | 2 d |
| `T-Fr2Nx8-fire-store-and-state` | `service/fire_store.py` | FR-7, FR-10 | 2 d |
| `T-Ev3Qm5-schedule-engine-core` | `service/schedule_engine.py` | FR-2, FR-4, FR-5, FR-6, NFR-2 | 3 d |
| `T-Ri7Dz2-run-id-flag` | `ao run --run-id` | FR-8 | 1 d |
| `T-Lp4Wt6-scheduled-launcher` | `service/schedule_launch.py` | FR-7, FR-8, NFR-3 | 2 d |
| `T-Sv5Hb3-service-integration` | `service/{registry,cli,systemd}.py` deltas | FR-2, FR-10, NFR-1, NFR-2 | 2 d |
| `T-Cl6Jn9-schedule-cli` | `schedules/cli.py` | FR-9, NFR-5 | 3 d |
| `T-Fw8Gp4-file-watch-trigger` | `service/watch.py`, `FileWatchScheduler` | FR-11 | 2 d |
| `T-Wh9Kv1-webhook-ingress` | `service/webhook.py`, `WebhookScheduler` | FR-12, NFR-3 | 3 d |
| `T-Un0Lm6-until-termination` | `until_satisfied` + lifecycle | FR-13 | 2 d |
| `T-Ap1Xs3-dashboard-schedules` | 5 UI routes + `Schedules.tsx` | FR-14 | 3 d |
| `T-Te3Qw8-integration-e2e-suite` | integration + e2e tiers | all, NFR-1/2/4 | 3 d |
| `T-Se4Bk5-security-and-review-gate` | `dev-security` + `reviewer` late gate | NFR-3, NFR-6 | 2 d |
| `T-Dc6Zr2-docs-refresh` | `docs-md/`, ROADMAP, ticket sync | all | 2 d |

Sequencing is dependency-driven, not calendar-driven. `T-Sd1Kq7` and `T-Fr2Nx8` are independent
and start together; `T-Ri7Dz2` is independent of everything but `T-Lp4Wt6`. After `T-Cl6Jn9`, the
four feature tasks (`T-Fw8Gp4`, `T-Wh9Kv1`, `T-Un0Lm6`, `T-Ap1Xs3`) are mutually independent.
**Sprint 1 alone is a shippable increment**: cron and interval schedules firing at-most-once with
the full CLI and hub status. The sprint assignment and the capacity arithmetic are in the epic's
[`STATUS.md`](../../meta/tickets/E-Sc9Rt4-scheduler-triggers/STATUS.md).

## Evidence Log

- 2026-09-06 — **Design package delivered** (architect agent): `docs-md/scheduler-triggers-hld.md`
  (HLD + LLD, five mermaid diagrams, §15 landscape survey, §16 decisions needed, §18 test plan,
  §19 non-goals); `docs-md/adr/ADR-0014-service-owned-scheduler-and-triggers.md` (D1-D10 +
  alternatives table + consequences summary); this page; and a 14-task ticket folder. **No `src/`
  file was edited and no commit was made** — design-only deliverable, per the task's constraints.
- 2026-09-06 — **Prior-art claims verified against shipped code, not assumed.**
  `src/agent_orchestrator/scheduler.py` is entirely orphaned (no importer anywhere in `src/`);
  `croniter>=2.0` is already a **core** dependency, not an extra; `ProcessSupervisor.launch_run`
  already carries an argv allow-list (`ALLOWED_OPTIONS` / `ALLOWED_BOOL_OPTIONS`) that silently
  drops unknown keys; `Supervisor._decide_and_act` already launches headlessly with no HTTP, which
  is the proof the launch path is reusable; `service/cli.py::run` owns the loop and the signal
  handlers while `Supervisor` is deliberately signal-free (its AC11); `build_status_provider` is
  the designated status-enrichment seam and `hub.py` must stay a thin adapter.
- 2026-09-06 — **Two findings that changed the design during discovery.** (1)
  `ui/processes.py::_discover_run_id` polls for up to `RUN_ID_DISCOVERY_TIMEOUT_SECONDS = 10.0`
  per launch — unacceptable inside a 2-second monitor loop, and an *inferred* fire→run link makes
  overlap detection unreliable. That turned `ao run --run-id` from a nice-to-have into a scoped
  task (`T-Ri7Dz2`, ADR-0014 D6). (2) `LaunchRecord` is a plain `@dataclass`, not a pydantic
  model — the codebase's convention is dataclasses for internal persisted records and pydantic for
  HTTP boundaries and daemon state files; the new state models follow the latter.
- 2026-09-06 — **Consumer reference read** (`../ao-runner-finplan`, read-only): the real pattern is
  `ao new epic-runner <slug> --param type=epic --prompt-file …` then `ao run --workflow <rendered>`,
  with `reposets`/`agents`/budgets/quota all coming from `.ao/config.yaml`. This is the direct
  evidence for ADR-0014 D2 (a workflow file alone cannot express a scheduled invocation) and for
  D9 (`reposets`/`agents` must come from config, not from a schedule binding).
- `TODO:` per-task evidence entries with hard numbers (suite counts and deltas, ruff/mypy status,
  coverage) are appended here at each task boundary during delivery.

### Final MVP requirement traceability

`TODO:` completed at epic close by `T-Dc6Zr2` — table of `| Requirement | Landed in | Test
coverage |` with per-file test counts, in the same shape as
[`E-GIytcL`](E-GIytcL-multi-workspace-service.md)'s.

## Risks & Blockers

- **Shared-process blast radius.** The engine lives in the supervisor process, so a scheduler bug
  can take every dashboard down with it. Bounded by per-schedule exception isolation with
  quarantine after five consecutive errors, a per-tick wall-clock budget so child monitoring is
  never starved, three levels of kill switch, and the unit's existing `Restart=on-failure` — but
  this is the epic's largest single risk and both `T-Ev3Qm5` and `T-Te3Qw8` carry ACs for it.
- **`.ao/schedules.yaml` is untrusted workspace content.** A `command:`/`env:` field, or a missed
  path guard, would make "clone a repo and register it" into clock-triggered arbitrary code
  execution with **no human in the loop** — materially worse than anything the dashboard offers
  today. NFR-3 and `T-Se4Bk5` exist for exactly this; no new argv surface may be added.
- **First authenticated endpoint in an unauthenticated project.** Getting `SecurityMiddleware`
  merely imported rather than *mounted* is a mistake already made once here (ADR-0012 early-gate
  correction #6), and a non-constant-time signature comparison is a real side channel.
- **Concurrent epic boundary.** Another architect owns per-task git isolation (`ADR-0013`,
  `docs-md/task-isolation-hld.md`); neither file was created or touched here. Until it lands,
  ROADMAP §4's parallel-write-conflict caveat applies to concurrently scheduled runs — which is
  why `max_concurrent` defaults to 1.
- **Carried limitations, chosen not overlooked**: a crash inside the fire window costs a missed
  run (at-most-once, D5); a DST fall-back repeated local hour fires twice (`timezone: UTC` is the
  mitigation, and a test pins the behavior so a change is deliberate); the webhook's nonce cache
  and rate bucket are in-memory and reset on restart; `launch_id` is microsecond-resolution, so a
  future *batched* launcher would need a collision-safe id.
- **Not blocked.** Every open decision in HLD §16 has a recommended default that the tickets are
  written against.

## Next actions

1. User confirms or overturns HLD §16's D-a … D-j. The three with real consequences are **D-b**
   (webhook gets its own default-off listener rather than riding the hub), **D-e** (at-most-once:
   a crash mid-fire costs a missed run, never a duplicate), and **D-h** (`ao run --run-id` is in
   scope here).
2. Run an early gate — `reviewer` + `architect` in parallel, **before any implementation lands**.
   E-GIytcL's history is the argument: that gate caught three blocking correctness defects
   (singleton lock, boot-resume idempotency re-check, orphan reclamation) that design review by the
   author alone had not surfaced. The analogous candidates here are the fire-store at-most-once
   semantics, the tick-budget interaction with child monitoring, and the webhook's middleware
   mounting. Fold findings into the HLD, an ADR-0014 "Early-gate corrections" section, and the
   affected tasks' acceptance criteria.
3. Start Sprint 1 by dispatching `T-Sd1Kq7-schedule-binding-model` and
   `T-Fr2Nx8-fire-store-and-state` in parallel.
