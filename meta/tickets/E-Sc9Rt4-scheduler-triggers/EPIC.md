# EPIC: E-Sc9Rt4-scheduler-triggers

## Metadata
- Epic ID: `E-Sc9Rt4-scheduler-triggers`
- Title: Scheduler daemon — cron & event triggers owned by `ao service`
- Owner: architect agent (design) → dev-epic agent (delivery)
- Created: 2026-09-06
- Last Updated: 2026-11-10
- Status: Draft (design amended 2026-11-10 by the Opus design review; implementation not started)
- Origin ask: user request — "`Trigger` is spec'd but nothing executes a schedule; `ao service` is the only always-on process, so it should own cron/event trigger evaluation." Decisions D1/D3/D4/D5/D8 in ADR-0014 were locked by the user before design started and are not re-litigated.

## Summary
- Goal: Make schedules fire themselves. `ao service run` gains a `ScheduleEngine` that evaluates
  every registered workspace's `.ao/schedules.yaml` on a fixed cadence and materializes runs
  through the existing `ProcessSupervisor.launch_run` path — with overlap, catch-up, jitter and
  concurrency policies, at-most-once crash-safe fires, cron/interval/file-watch/webhook triggers
  behind the existing `Scheduler` ABC, a `ao schedule` CLI, a dashboard panel, and structured
  observability. Closes `meta/ROADMAP.md` §3.2 ("Spec'd, not scheduled").
- Scope In: new `schedules/` package (binding model + locked store + JSON Schema); new
  `service/{statefile,schedule_engine,fire_store,schedule_launch,watch,webhook}.py`; additive
  changes to `scheduler.py` (`IntervalScheduler`, `FileWatchScheduler`, `WebhookScheduler`),
  `models.py` (`Trigger.interval_seconds` + `"interval"`), `specs/workflow.schema.json`,
  `service/{registry,cli,systemd}.py`, `cli.py` (`ao run --run-id`, `ao schedule` sub-app),
  `ui/{app,service}.py` + `ui/src/` (Schedules panel); tests at all three tiers; HLD + ADR-0014;
  `meta/ROADMAP.md` sync at close.
- Scope Out: a general authentication/authorization/audit framework (roadmap §3.1; dashboard/hub
  session auth has since landed as E-Da5Tn9 / ADR-0021 — the webhook HMAC here is one endpoint's
  minimum and is deliberately **not** behind that session auth); Airflow-style backfill;
  inotify/`watchdog`; distributed or multi-host scheduling; cross-run dependency triggers;
  notifications/alerting; replacing or cancelling an in-flight run from a schedule; per-task git
  isolation (concurrent epic, `ADR-0013` / `docs-md/task-isolation-hld.md` — do not touch those
  files); editing `executors/`, `dag.py`, `budget.py`, `breakers.py`, and editing `engine.py`
  **except** the single sanctioned `Orchestrator.run(..., run_id=None)` keyword forward owned by
  `T-Ri7Dz2` (F1). Per-task isolation has since landed (`b0cb467`) — see Risks.

## Requirements

### MVP (must-have)
- FR-1 (Functional): `<workspace>/.ao/schedules.yaml` is the authoritative schedule binding file
  — a pydantic `ScheduleFile`/`ScheduleBinding` model plus `specs/schedules.schema.json`, loaded
  and saved through an atomic, file-locked store mirroring `service/registry.py`'s
  `mutate()`/write-then-rename. A missing file is an empty schedule set, never an error.
  Verification: unit tests for round-trip, for `mutate()` under a concurrent writer, and for
  every rejection case (unknown `run_args` key, absolute/`..` path, bad cron, bad IANA timezone,
  duplicate id, `interval_seconds < 60`, inline `secret:`).
- FR-2 (Functional): `ScheduleEngine` — a signal-free `start()`/`tick(now)`/`shutdown()`/
  `status_snapshot()` class with an injectable clock and injectable collaborators — evaluates
  every registered workspace whose registry entry has `schedules: true` (**opt-in**, default
  `false`; F5), self-rate-limited to
  `eval_interval_seconds` (default 15) independent of the 2 s supervisor loop, and bounded by
  `SCHEDULE_TICK_BUDGET_SECONDS`. Verification: unit tests on a fixed clock; an integration test
  single-stepping `tick()` alongside `Supervisor.tick()`.
- FR-3 (Functional): `cron` and `interval` kinds sit behind the existing
  `Scheduler.next_fire(trigger, now)` ABC; `IntervalScheduler` is new, `CronScheduler` is
  unchanged, and `Trigger` gains an additive optional `interval_seconds` plus an `"interval"`
  enum member in both `models.py` and `specs/workflow.schema.json`. Verification: new unit tests
  including DST spring-forward and fall-back cases; `tests/test_scheduler.py` passes unmodified.
- FR-4 (Functional): per-binding overlap policy `skip` (default) | `queue` (depth 1, first-wins)
  | `allow`; no `replace`. Verification: a {policy} × {no active run, one active run, queue full}
  matrix of unit tests, plus an assertion that an overlap-skip burns the fire key.
- FR-5 (Functional): catch-up policy `once` (default) | `skip` with `catch_up_window_seconds`
  (default 86400); a backlog of missed instants collapses to at most one fire, never backfills.
  Verification: boundary tests at `grace_seconds ± 1` and `catch_up_window ± 1`; an N-missed-
  instants test asserting exactly one fire.
- FR-6 (Functional): deterministic jitter derived from the fire key, plus a per-workspace
  `max_concurrent` (default 1) and a service-wide `max_concurrent_global` (default 3).
  Verification: same fire key ⇒ same offset across processes, offset in `[0, jitter_seconds]`;
  cap tests asserting a concurrency skip does **not** burn the fire key while an overlap skip does.
- FR-7 (Functional): at-most-once fires — a `FireIntentRecord` is written atomically **before**
  the launch and updated after it; on `start()`, an `intended` record with no live pid and no run
  directory becomes `orphaned` and is never relaunched; a record in any status suppresses every
  future attempt at that `fire_key`, catch-up included. Verification: a crash-simulation test
  (write an `intended` record with a dead pid, `start()`, assert `orphaned` and zero launches);
  a clock-rewind test asserting no double fire.
- FR-8 (Functional): a fire launches through `ProcessSupervisor.launch_run(..., run_id=...)` — an
  **explicit keyword**, not an `options` entry (the registry-derived `ALLOWED_OPTIONS` would
  silently drop it; F1); `ao run` gains `--run-id` (a registry `Option` with no `ui_group`/`env`/
  `config`) which is used verbatim and refuses an id whose run directory already exists via an
  atomic `os.mkdir` reservation. Verification: `StubSupervisor` kwargs assertion (no subprocess); a
  `CliRunner` e2e asserting the run directory carries the supplied id and a duplicate id exits
  non-zero.
- FR-9 (Functional): `ao schedule list | next | add | remove | enable | disable | run-now |
  history | validate | daemon` as a Typer sub-app registered with the same one-line
  `app.add_typer` pattern `ao service` uses; `run-now` routes through the drop directory when a
  daemon is live and executes in-process when it is not; `daemon --once` performs one `tick()`
  and exits. Verification: `CliRunner` e2e for every subcommand, including the no-daemon path.
- FR-10 (Non-functional / observability): every evaluation outcome emits a structured event to
  `<state_dir>/schedules/events.jsonl` **and** the stdlib logger, every skip states its reason,
  and `GET /api/service/status` gains a `schedules` key merged in by `build_status_provider`
  (never by `hub.py`, which stays a thin adapter). Verification: event-emission unit tests per
  outcome; a hub payload-shape test; a grep-level assertion that no rejection log line contains
  secret or signature material.

### MVP-2 (this epic, later tasks — each behind an explicit switch, additive to MVP)
- FR-11 (Functional): `file_watch` kind — polled, workspace-relative globs, `(mtime_ns, size)`
  digest persisted per schedule, debounce window, `max_files` and per-tick scan budget, a default
  ignore list (`.git/`, `node_modules/`, `.venv/`, `__pycache__/`, `.orchestrator/`), and
  `scheduled_for` derived from the newest changed mtime so a fire is replay-safe. A truncated
  scan never fires. Verification: unit tests for digest/debounce/truncation; an e2e that touches
  a watched file, fires once, and does not fire again with no change.
- FR-12 (Functional): `webhook` kind — a dedicated, default-off FastAPI listener on
  `--webhook-port` (default 8771), loopback-bound unless told otherwise, mounting
  `ui.security.SecurityMiddleware` on the app, exposing only `POST /hooks/{ws}/{id}` and
  `GET /healthz`, requiring HMAC-SHA256 over the raw body (`X-AO-Signature` or GitHub's
  `X-Hub-Signature-256`) compared with `hmac.compare_digest`, plus a bounded-LRU delivery-id
  nonce, an optional ±300 s timestamp window, a 64 KiB body cap and a per-schedule token bucket.
  A verified delivery only enqueues; the fire happens on the next tick through the normal policy
  path. Unknown schedule and bad signature both return 401. Verification: unit tests for valid/
  wrong-secret/missing-header/GitHub-header/oversized/stale/duplicate/rate-limited/unknown-
  schedule/group-readable-secret; an e2e via `TestClient` + `daemon --once`.
- FR-13 (Functional): `until` — inter-run loop termination on `max_runs` (always honored, CLI
  supplies `DEFAULT_UNTIL_MAX_RUNS` when omitted), `artifact_exists`, or `gate_file`/`gate_field`
  /`gate_equals`; satisfaction sets the schedule `completed` (reversible via `ao schedule
  enable`), and an unreadable or malformed gate never terminates the loop. Verification: unit
  tests for all four conditions plus missing/malformed/wrong-type gate files.
- FR-14 (Functional): dashboard Schedules panel — `GET /api/schedules`,
  `GET /api/schedules/{id}/history`, `POST /api/schedules/{id}/{enable,disable,run-now}` behind a
  **typed** exception hierarchy (not the existing substring matching), behavior in
  `ui/service.py` and ≤5-line route bodies in `ui/app.py`; one new React panel following
  `ui/README.md` conventions, also surfacing the already-built-but-unused `GET /api/launches`.
  Verification: API integration tests via `TestClient`; vitest component tests; `make ui-build`
  output committed.

### Non-functional (all tasks)
- NFR-1 (Backward compatibility): a workspace with no `.ao/schedules.yaml`, a registry with no
  `schedules:` keys, and a workflow with the default `triggers: [{type: manual}]` behave exactly
  as before this epic. Verification: an e2e asserting zero schedule events and an
  `ao service status` payload identical apart from the new `schedules` key.
- NFR-2 (Reliability): an unparseable `.ao/schedules.yaml` in one workspace never stops the
  daemon or affects another workspace; a schedule that raises is isolated and quarantined after
  `MAX_CONSECUTIVE_EVAL_ERRORS = 5`; schedule work is bounded per tick so child monitoring is
  never starved. Verification: dedicated integration tests for each of the three.
- NFR-3 (Security): `.ao/schedules.yaml` is treated as untrusted workspace content — `run_args`
  is a closed model over the curated `SCHEDULE_RUN_ARG_NAMES` subset of the registry-derived
  allowlist with `extra="forbid"` (F3; no `sentinel.llm.*`, no `max_parallel_ceiling`); `template`
  is a bare name, never a path (F4); schedules are opt-in per workspace because workflow hooks and
  agent tools already execute arbitrary commands (F5); every path field resolves through the existing workspace-root guard; webhook
  secrets are referenced by env-var name or mode-checked file, never inlined; `MIN_INTERVAL_
  SECONDS = 60`, scan/body/rate bounds apply. Verification: `T-Se4Bk5`'s dedicated review plus
  per-case unit tests; a review-time grep confirming no new argv surface was introduced and `ALLOWED_OPTIONS` is unchanged.
- NFR-4 (Determinism / testability): every clock is injectable; no test touches a real `$HOME`
  (`AO_SERVICE_STATE_DIR` / `AO_SERVICE_CONFIG` throughout), the network, or a real agent CLI.
  Verification: review-time grep + the suite running unmodified in this sandbox.
- NFR-5 (Operability): `ao schedule list` warns when no live daemon is found; every suppressed
  fire is recorded with its reason so `ao schedule history` shows what did **not** happen.
  Verification: covered by FR-9/FR-10 tests.
- NFR-6 (Quality gate): `pytest` green with a reported delta vs. the epic baseline, `ruff check`
  / `ruff format --check` clean, `mypy src` at its **0-error** baseline (commit `991391b` made it clean; F20), and ≥80 % coverage on every new module. Verification: reported per task in each
  `STATUS.md`, re-verified in `T-Se4Bk5`.

## Task List
- [x] `T-Sd1Kq7-schedule-binding-model` — `schedules/` package (models, locked store, JSON
  Schema), `service/statefile.py`, `Trigger.interval_seconds` + `IntervalScheduler` (FR-1, FR-3,
  NFR-3) — 2 d
- [x] `T-Fr2Nx8-fire-store-and-state` — `service/fire_store.py`: `FireIntentStore`,
  `ScheduleStateStore`, `ScheduleEventLog` (FR-7, FR-10) — 2 d
- [x] `T-Ev3Qm5-schedule-engine-core` — `service/schedule_engine.py`: evaluate/tick, overlap,
  catch-up, jitter, concurrency, quarantine (FR-2, FR-4, FR-5, FR-6, NFR-2) — 3 d
- [x] `T-Ri7Dz2-run-id-flag` — `ao run --run-id` with duplicate-id refusal (FR-8) — 1 d
- [x] `T-Lp4Wt6-scheduled-launcher` — `service/schedule_launch.py`: two-phase launch, template
  instantiation per fire, `run_args` → existing options allow-list (FR-7, FR-8, NFR-3) — 2 d
- [x] `T-Sv5Hb3-service-integration` — registry additions, `ao service run` wiring,
  `--no-schedules`/`--webhook-*`, status merge, systemd unit delta (FR-2, FR-10, NFR-1, NFR-2) — 2 d
- [~] `T-Cl6Jn9-schedule-cli` — the `ao schedule` sub-app incl. `daemon --once` (FR-9, NFR-5) — 3 d — landed 2026-11-11 for cron/interval; event-kind flags/`observe` in `next` pending T-Fw8Gp4/T-Wh9Kv1
- [~] `T-Fw8Gp4-file-watch-trigger` — `service/watch.py` + `FileWatchScheduler` (FR-11) — 2 d — core landed 2026-11-11; CLI flags + e2e pending
- [~] `T-Wh9Kv1-webhook-ingress` (core landed; CLI flags + daemon e2e pending T-Cl6Jn9) — `service/webhook.py`, listener app, `WebhookScheduler` (FR-12,
  NFR-3) — 3 d
- [~] `T-Un0Lm6-until-termination` — `until` evaluation + `completed` lifecycle (FR-13) — 2 d — landed 2026-11-11 (AC13 done; coverage report unfinished)
- [~] `T-Ap1Xs3-dashboard-schedules` — 5 UI routes + typed errors + React panel (FR-14) — 3 d — implemented 2026-11-11, awaiting review
- [~] `T-Te3Qw8-integration-e2e-suite` (per-trigger e2e tier landed, 11 tests; rest open) — cross-module integration + `CliRunner` e2e tiers
  (all FRs, NFR-1, NFR-2, NFR-4) — 3 d
- [ ] `T-Se4Bk5-security-and-review-gate` — `dev-security` audit of the untrusted-file and
  webhook surfaces + `reviewer` architecture pass + NFR-6 re-verification (NFR-3, NFR-6) — 2 d
- [~] `T-Dc6Zr2-docs-refresh` (partial 2026-11-11: HLD §21-23, ADR, LLD, README, ROADMAP, ai-epics, guide done; epic close-out blocked on T-Se4Bk5) — reconcile `docs-md/` + ADR-0014 + ROADMAP §1/§3.2 with what was
  actually built, including deviations (all) — 2 d

**Total: 14 tasks (the overseer charter's "16 tasks" was wrong; F19), 32 person-days across 3 sprints. See `STATUS.md` for the capacity math and sprint
assignment.**

## Risks and Dependencies
- **A scheduler bug can take down every dashboard**, since the engine shares the supervisor
  process (ADR-0014 D1). Mitigated by per-schedule exception isolation + quarantine, a per-tick
  budget, `--no-schedules`, and the unit's existing `Restart=on-failure` — but this is the
  epic's single largest blast radius and `T-Ev3Qm5`/`T-Te3Qw8` both carry ACs for it.
- **`.ao/schedules.yaml` is untrusted workspace content.** A `command:`/`env:` field, or a
  missed path guard, would turn "clone a repo and register it" into clock-triggered arbitrary
  code execution with no human in the loop — strictly worse than anything the dashboard offers
  today. NFR-3 and `T-Se4Bk5` exist for this; no new argv surface may be introduced.
- **The webhook is the project's first HMAC-authenticated endpoint**, deliberately outside the
  optional session auth that ADR-0021 added to the hub/dashboards (F18). Getting `SecurityMiddleware` merely imported rather than
  **mounted** is a known, previously-made mistake (ADR-0012 early-gate correction #6).
- **Per-task isolation has landed (F15; supersedes the earlier "until that lands" caveat).**
  `ADR-0013` / `b0cb467` (`isolation/runlock.py`; `engine.py` takes a `WorkspaceRunLock` per run)
  is in the tree. A scheduled run alongside a manual run in a workspace whose workflow uses
  `integration.workspace_lock: require` now **degrades at startup**, and `max_concurrent` counts
  scheduled runs only, not manual ones. The default of 1 is kept. Do not edit those docs/files.
- **Zombie / liveness (F2).** A fresh `ProcessSupervisor` cannot reap children and reports zombies
  alive; the engine must own one long-lived instance per workspace. Boot-resume has the same latent
  leak (`supervisor.py:487`) — backlog, not this epic.
- **Two evaluators (F9).** `engine.lock` guards against service + `ao schedule daemon` (or a
  misjudged local `run-now`) double-firing.
- **`models.py` / `specs/workflow.schema.json` edits** (FR-3) are small and additive but touch
  files other epics have historically fenced off. `T-Sd1Kq7` owns them exclusively; every other
  task treats them as read-only.
- **`_discover_run_id` blocks up to 10 s per launch** in `ui/processes.py`. `T-Ri7Dz2` + a
  `run_id_discovery_timeout=0.0` construction are what keep that out of the monitor loop; if
  `T-Ri7Dz2` slips, `T-Lp4Wt6` inherits a real latency defect.
- **`launch_id` is microsecond-resolution.** Serial launching within a tick makes a collision
  unreachable today; a future batched launcher would need a collision-safe id (carried, not fixed).
- **Global `ao` installs are snapshots** — `ao schedule` will not exist until `install.sh` is
  re-run; the documented staleness symptom is a Typer unknown-command error.

## Owner decisions (2026-11-10)
Taken at the Opus design reviewer's recommended defaults; both are written into the tickets/HLD.
- **D-k / F5 — schedules are OPT-IN.** `WorkspaceEntry.schedules: bool = False`; enabled with
  `ao service add --schedules` / `ao service set --schedules`. (`T-Sv5Hb3` AC1/AC6, `T-Cl6Jn9` AC2,
  `T-Se4Bk5` AC7, HLD §5.2/§13.3.)
- **D-l / F3 — `RunArgs` = curated `SCHEDULE_RUN_ARG_NAMES`** drawn from the registry allowlist
  (`model, turn_budget, effort, thinking_effort, autocompact, max_attempts, max_turns,
  max_parallel, budget_total, quota_max_wait, quota_poll_interval, self_heal, sentinel.enabled`),
  excluding `sentinel.llm.*` and `sentinel.max_parallel_ceiling` (and `cost_cap_usd`, `run_id`).
  (`T-Sd1Kq7` AC2, HLD §3.3/§5.1.1.)

## Late-gate corrections — Opus design review (2026-11-10) — disposition of F1-F24
Source: overseer run `o-6w86nm-sched-governance`, report
`workflows/overseer-runner/runs/o-6w86nm-sched-governance/outputs/waves/w01/w01-06-opus-design-review-sc9rt4.md`.
All were **applied** (none rejected); ADR-0014 carries the matching "Late-gate corrections
(2026-11-10)" section.

| # | Sev | Applied in |
|---|---|---|
| F1 | blocker | `T-Ri7Dz2` AC2/3/3a/4/9/9a (explicit `launch_run(run_id=)`, registry `Option`, allow-list unchanged, atomic reservation, `new_run` seam, `derive_launch_status`); `T-Lp4Wt6` AC3/AC8; `T-Se4Bk5` AC2/AC6; FR-8 + Scope-Out here; HLD §3.3/§6.6/§7.2; ADR D6 |
| F2 | blocker | `T-Ev3Qm5` AC1/AC7/AC17a; `T-Lp4Wt6` AC1/AC4; `T-Te3Qw8` AC8a; HLD §6.1/§6.4/§7.2 |
| F3 | high | `T-Sd1Kq7` AC2; `T-Lp4Wt6` AC8; `T-Se4Bk5` AC1; NFR-3; HLD §3.3/§5.1.1; ADR D9 |
| F4 | high | `T-Sd1Kq7` AC3/AC4; `T-Lp4Wt6` AC5/AC12; `T-Se4Bk5` AC1/AC5; HLD §13.3 |
| F5 | high | `T-Sv5Hb3` AC1/AC6; `T-Cl6Jn9` AC2; `T-Se4Bk5` AC7; `T-Te3Qw8` AC11; NFR-3; HLD §5.2/§13.3 |
| F6 | high | `T-Ev3Qm5` AC1a; `T-Sd1Kq7` AC10a; `T-Fw8Gp4` AC4/AC8a; `T-Wh9Kv1` AC13; `T-Cl6Jn9` AC3; HLD §6.7/§7.1 |
| F7 | high | `T-Ev3Qm5` AC8/AC17a; `T-Te3Qw8` AC8a; HLD §6.3 |
| F8 | high | `T-Ev3Qm5` AC6/AC17a; `T-Te3Qw8` AC8a; HLD §6.3 |
| F9 | high | `T-Ev3Qm5` AC1b; `T-Cl6Jn9` AC2/AC10/AC11/AC15a; `T-Te3Qw8` AC8a; HLD §6.1/§6.9/§7.2/§9.2 |
| F10 | high | `T-Lp4Wt6` AC6/AC9; `T-Te3Qw8` AC4; HLD §6.6 |
| F11 | medium | `T-Fr2Nx8` AC1/AC14; `T-Lp4Wt6` AC3; HLD §6.3 |
| F12 | medium | `T-Sd1Kq7` AC3 (id ≤64 chars) |
| F13 | medium | `T-Ev3Qm5` AC8a/AC17a; HLD §6.3 |
| F14 | medium | `T-Wh9Kv1` AC12/AC13/AC15; `T-Te3Qw8` AC14; HLD §8.2 |
| F15 | medium | Risks here; `T-Dc6Zr2` AC6; HLD §6.4 |
| F16 | medium | `T-Cl6Jn9` AC6 + file list; `T-Un0Lm6`/`T-Fw8Gp4`/`T-Wh9Kv1` file lists; `T-Ev3Qm5` handoff (serial order `T-Ev3Qm5` → `T-Un0Lm6` → `T-Fw8Gp4` → `T-Wh9Kv1`) |
| F17 | medium | `T-Ap1Xs3` file list/AC9/AC12/AC13/AC14a/AC17 |
| F18 | medium | `T-Wh9Kv1` Description/AC1/AC3; `T-Sv5Hb3` pseudocode (`child_env`, `auth=launch.runtime`); `T-Se4Bk5` AC7a; Scope-Out/Risks here; HLD §8.1; ADR D8 |
| F19 | low | task count corrected to 14 (Task List total) |
| F20 | low | NFR-6 here and every task's mypy AC → 0-error baseline |
| F21 | low | `T-Sd1Kq7` Risks (`schedules/` docstring vs `scheduling/`); `T-Dc6Zr2` AC5a |
| F22 | low | `T-Ri7Dz2` Risks; `T-Dc6Zr2` AC6 (ROADMAP §4 note) |
| F23 | low | `T-Sv5Hb3` AC6a; HLD §6.1; `T-Dc6Zr2` AC6 |
| F24 | low | `T-Ri7Dz2` file list + AC9a (schema snapshot regenerated; `json_schema.properties` unchanged) |

## Links
- Design doc: [`docs-md/scheduler-triggers-hld.md`](../../../docs-md/scheduler-triggers-hld.md)
- ADR: [`docs-md/adr/ADR-0014-service-owned-scheduler-and-triggers.md`](../../../docs-md/adr/ADR-0014-service-owned-scheduler-and-triggers.md)
- Epic context (narrative + evidence log): [`docs-md/ai-epics/E-Sc9Rt4-scheduler-triggers.md`](../../../docs-md/ai-epics/E-Sc9Rt4-scheduler-triggers.md)
- Prior art this epic builds on: [`docs-md/multi-workspace-service-hld.md`](../../../docs-md/multi-workspace-service-hld.md) · [`ADR-0012`](../../../docs-md/adr/ADR-0012-multi-workspace-service-supervisor.md) · [`ADR-0010`](../../../docs-md/adr/ADR-0010-dashboard-architecture-and-general-instructions.md) · [`docs-md/workflow-templates-hld.md`](../../../docs-md/workflow-templates-hld.md)
- Sprint plan: `STATUS.md` (this folder)
- Output artifacts (if any): `output/E-Sc9Rt4-scheduler-triggers/`
