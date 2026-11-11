# Final whole-branch review: `ad/enhancements-10nov` vs `origin/main`

- By: reviewer (Opus, unit `w35-04-all-final-branch-review`) · Role: reviewer · Date: 2026-11-11
- Scope: the whole branch, `git diff origin/main...HEAD` at `ce8f24d` (139 commits ahead, 0 behind; 210 files, 44 under `src/`). It covers three asks:
  - A1, scheduler triggers (`E-Sc9Rt4`)
  - A2, scheduled-run governance (`E-Qp4Zm8`), including the G-12..G-17 fixes
  - A3, stuck-kill retry loop and run stranding (`T-Rk4Wn7`, filed under `E-Sn7tL2`)
- Mandatory cycle 4 (charter). Read-only on source: this file is the only artefact. Each finding is a follow-up, not a fix.
- Method: I reviewed the scheduler core, governance and ticket/doc sync myself. Four Opus sub-reviews ran in parallel: ingress security, schedule engine core, governance, and A3 plus test quality. I re-read the cited lines for every finding kept here; anything not reproduced or confirmed in code was dropped. Earlier REVIEW files (`T-Se4Bk5`, `E-Qp4Zm8/REVIEW.md`, `T-Un0Lm6`, `T-Ap1Xs3`) were read first, and their closed items are not repeated.

## Gates (run on this tree, 2026-11-11)

| Gate | Result |
|---|---|
| `pytest -q --ignore=tests/bench` | **11010 passed, 10 skipped**, 0 failed (847 s). Excludes `tests/bench`, which unit w34-02 is editing in the shared tree. The tree also had two untracked test files from other units (`tests/schedules/test_run_now_engine_lock_f9.py`, `tests/service/test_schedule_interleave_ac6_ac8a.py`), and they were collected. |
| `ruff check .` | clean |
| `ruff format --check src tests` | clean (677 files) |
| `mypy src` | clean (233 files) |
| UI bundle vs `ui/src` | **stale** (see M-5) |

## Verdict per ask

- **A1, scheduler triggers.** No blocker for the usable bar. Binding model, engine core, fire store, launcher and CLI work end to end with tests, and the branch is green. The unfinished tasks (`T-Te3Qw8`, `T-Se4Bk5`, `T-Dc6Zr2`) are marked NOT Done in their tickets and in STATUS. Before calling the scheduler production-ready, fix M-1 (the tick budget starves once fire records accumulate) and M-2 (the daemon can start for good without its scheduler). Neither is a merge blocker for this charter's bar.
- **A2, governance.** The usable bar is met: the quota gate (`quota_exhausted` skip and record) and the per-run cost cap (pause/abort, sticky) work end to end with tests. G-1..G-17 are verified fixed. **One High remains, H-1:** a resumed capped run can be finalised while its child is still starting. It sits in the pause → resume → re-pause path, so it is outside the stated bar but inside A2.4 ("sticky on resume"). I recommend fixing it before capped resume is called done. M-5 means the dashboard Resume control that actually ships lacks the G-8 and G-13 fixes.
- **A3, stuck-kill.** No blocker. D1, D2 (as specified), D3, D4 (in code) and CTL-SCHEMA are implemented, and the tests discriminate: 66 targeted tests would fail on the old code. Two items are still open:
  - The D3 owner decision on a `fail_fast` opt-out. Continue-on-failure is now the unconditional default.
  - D4 has not been run against a real `claude` binary.

  M-6 and M-7 are design consequences worth a follow-up.

## Critical
None.

## High

**H-1. A resumed capped run is finalised while its child is still starting.** (A2)
- **Where:** `src/agent_orchestrator/service/schedule_engine.py:913-914`, `_is_live`: `if running is False: return False`.
- **What happens:**
  - After `drain_resume_capped` spawns the child and marks the key active, `state.json` still reads `failed` from the pause until the child's first save.
  - `_live_runs()` therefore retires the key and `_finalize_governance_once` sets `final=True`.
- **Effects:**
  - The top-up reservation is released while the run is spending, so the period cap can over-admit.
  - The overlap and concurrency slot is freed.
  - A later cap trip never re-pauses the run, because the record is already final.
- **Reproduction:** the sub-reviewer reproduced it with the `test_capped_resume` Rig and a launcher that does not rewrite `state.json`: `final=True`, `active_fire_keys=[]`, and `reserved_usd` went from 1.5 to 0.0. The existing test launcher writes `running` at spawn, which hides the bug. I checked the code path.
- **When it triggers:** in the same tick when overlap is `allow` and a concurrency check runs; in any configuration when the child's first save takes more than one eval interval.
- **Fix:** when the record has `cap_state == "resumed"`, is not final, and `state.updated_at <= resumes[-1].at`, fall through to `process_supervisor.is_running(run_id)`, the same "starting" treatment as a missing `state.json`. Add a regression test with a launcher that leaves `state.json` as `failed`.

## Medium

**M-1. The tick budget is spent before any schedule is checked, and fire records are never pruned.** (A1)
- **Where:**
  - `schedule_engine.py` `tick()` sets `deadline` *before* `_evaluate_all`.
  - `_evaluate_all` first runs `drain_resume_requests`, `drain_restart_requests` and `_sweep_pause_expiry`. The sweep calls `fires.list(status="launched")`, which parses every record on every tick.
  - Only then does it check the deadline, before the first schedule.
  - `FireIntentStore.prune` (`service/fire_store.py:382`) has no production caller.
- **What happens:** once setup takes more than 0.5 s (about 20k records: 10k measured at 0.245 s), every tick emits `tick_budget_exceeded` and nothing fires again. The sub-reviewer reproduced this with a fixed-clock harness: 5 ticks, 0 fires.
- **Fix:**
  - Start the budget at the schedule loop and always evaluate at least one schedule per tick.
  - Prune from the engine on a slow cadence, keeping records still inside a governance window. Count-based `max_records` alone would undercount monthly caps.
  - Index paused records for the expiry sweep.

**M-2. The daemon can start without its scheduler for good after a transient engine-lock collision.** (A1)
- **Where:** `src/agent_orchestrator/service/cli.py:822-831`. Any `build_schedule_engine` failure, including `EngineLockHeldError`, sets `engine = None` with no retry.
- **Brief lock holders:**
  - `probe_engine_lock`, run by every `ao schedule list`.
  - `run-now --local`, for a whole launch.
  - `restart_completed_schedule`.
- **Why it matters:** an `ao.service` (re)start inside that window silently disables every schedule until the next manual restart.
- **Fix:** on `EngineLockHeldError`, keep a pending engine and retry from `monitor_tick` with backoff. Degrade for good only when the holder is a long-lived `ao schedule daemon`.

**M-3. The governance ledger ignores the engine's injected clock.** (A1/A2, determinism)
- **Where:**
  - `schedule_engine.py:301`: the default `usage_ledger_factory=FireRecordLedger` gets no clock (used at `:330`).
  - `usage_ledger.py:83,227` falls back to `datetime.now(UTC)` and `time.time_ns`.
  - Same pattern in `schedule_launch.py:181`.
- **Why it matters:** `usage()` decides whether a window is open from the real clock, and open windows over-charge partial runs. Fixed-clock governed tests can change verdict with the real date (project rule: fixed clocks).
- **Fix:** default the factory to `lambda fires: FireRecordLedger(fires, clock=clock)` in both places.

**M-4. Watch globs with many `**` segments can freeze the service process (ReDoS).** (A1, security)
- **Where:** `src/agent_orchestrator/service/watch.py:155-166` (`_glob_to_regex`). Each `**` becomes `(?:[^/]+/)*`, and `schedules/models.py:278-280` sets no limit on `**` count or pattern length.
- **Reproduction:** 12 × `**` against a 22-level non-matching path took **21 s** for one `fullmatch`. It holds the GIL, so the engine, dashboard and webhook listener all stall on every poll.
- **Fix:** collapse consecutive `**`, cap at about 2 per pattern plus a length limit at load, or match per segment via the existing `_segments`.

**M-5. The committed dashboard bundle is stale relative to `ui/src`.** (A2, ships the wrong UI)
- **Where:** `src/agent_orchestrator/ui/static/assets/index-DXRstR0K.js`, last rebuilt in `4f6bbc1`.
- **What changed since:** `55878de`, `a50a3ce` and `ce8f24d` edited `ui/src/components/PausedRuns.tsx` (G-8 null `max_add_usd`, overshoot display, "bounded by period headroom") and `types.ts`. The bundle has no `overshoot` string and still compiles `f<=t.max_add_usd`.
- **Why it matters:** with `max_add_usd: null` (no `per_run_usd`), `amount <= null` is false in JS. The Resume button the bundle actually ships is permanently disabled ("amount must be > 0 and ≤ —"), and overshoot is never shown. Vitest passes because it runs against `ui/src`.
- **Mitigation:** the human install flow rebuilds the UI before `install.sh`. A checkout served as-is still serves the stale bundle.
- **Fix:** `make ui-build` and commit the regenerated assets. Consider a CI check that the bundle hash matches `ui/src`.

**M-6. Under default settings the stuck-duration kill can never fire.** (A3, design consequence)
- **Where:** `src/agent_orchestrator/sentinel_tick.py:599-625`. The veto window is `stuck_veto_minutes or idle_kill_minutes`. Output inside the window vetoes the kill; silence past it is an idle kill instead. `test_default_spec_stuck_factor_never_fires` pins this.
- **Why it matters:** a runaway task that keeps printing is now bounded only by its timeout or cost cap. This matches the ticket's literal D2 text, but it removes the stuck kill as a guard.
- **Fix:** default `stuck_veto_minutes` well below `idle_kill_minutes`, or add a hard ceiling (for example 2 × factor × baseline) that ignores the veto. Record the owner decision in `T-Rk4Wn7`.

**M-7. One incidental tool denial can pause the LLM lane and blame permissions.** (A3)
- **Where:** `sentinel_driver.py` `detect_permission_denial`; the alarm is raised at `sentinel_gen.py:939-946`. The default allow-list permits only `Bash(mv:*)`, so a single model `ls`/`cat` counts as a denial. If the generation then ends ungracefully for another reason (time, budget), `llm_permission_denied` fires and the lane pauses.
- **Fix:** alarm only when a Write/Edit/`mv` was denied, or when the required outputs are actually missing.

**M-8. A ledger test depends on the real wall clock.** (test quality)
- **Where:** `tests/service/test_governance_gate.py:271-282`. It asserts a rescan because the directory mtime is within `FS_SETTLE_NS` (2 s) of real time, so it fails if more than 2 s pass on a loaded host.
- **Fix:** pass the ledger's injectable `fs_now_ns`.

## Low

- **L-1.** `schedule_engine.py:536, 810-857`: resume requests are drained before the opt-in filter, and neither the opt-in nor `binding.enabled` is checked, so a paused run can be relaunched after schedules were disabled. Refuse with `schedule_disabled`, or document that this is intended.
- **L-2.** `schedules/models.py:278-280`: an invalid glob (`docs/[z-a].md`) passes `ao schedule validate` and only fails at scan time (it fails closed, then gets quarantined). Compile the glob in the validator.
- **L-3.** `service/webhook.py:120-125, 229-234`: `secret_env` uses only a denylist, so a predictable variable (for example `AO_UI_ALLOWED_HOSTS`) can serve as the secret. Require a `^AO_WEBHOOK_SECRET[A-Z0-9_]*$` prefix.
- **L-4.** `service/webhook.py:248-249, 258-259`: an unreadable or non-regular `secret_file` returns 503 with no log reason. Emit `reason=secret_unreadable`.
- **L-5.** `engine.py:424` (`if cost < cap`) and the retry gate: raw float sums (0.1 + 0.7 < 0.8) admit one more task at an exact cap, while the governance gate compares in micro-dollars. Compare `round(...,6)`.
- **L-6.** `service/usage_ledger.py:353-359` (`_raise_mark`): the ledger overwrites the conservative `cost_confidence` persisted at finalisation (`unknown` → `actual`) while the charge stays conservative, so the UI and CLI drop "(cost partial)". This undoes part of G-5/G-12. Keep the worse of stored and derived via `_CONF_RANK` once `final`.
- **L-7.** `schedule_launch.py:255/271` and `schedule_engine.py:1544`: if a crash hits between the `launched` write and `record_fire`, a live ungoverned run is never re-tracked, so overlap/concurrency can start a second concurrent run. This is not a double fire. Re-track live ungoverned `launched` records in the start sweep.
- **L-8.** `engine.py:2816` (`_has_remaining_work`): dependents of a failed task stay `pending`, so the final settle can record a spurious `builtin.cost_cap` trip. Exclude descendants of `ctx.failed_tasks`.
- **L-9.** `sentinel_driver.py` `lane_permission_warnings` only matches argv[0] == `claude`, so `npx`/`env`-wrapped agents get no warning.
- **L-10.** The D1 regression test is unit-level only; no engine-tier kill → retry → no-second-kill test. Add one with a stepping clock.
- **L-11.** `tests/service/test_webhook_listener.py:85,106,127,201`: real-socket tests with a 0.6 s deadline and 0.4-1.5 s margins. Mark them slow and widen the margins.
- **Note.** `service/systemd.py:114-123` renders the webhook host and port unquoted into the unit file, at operator trust (same as `hub_host`). Validating them as host/IP is optional.

## Ticket / STATUS / HLD / ADR sync

- **S-1 (stale ticket).** `T-Se4Bk5` `STATUS.md:5,217` and `REVIEW.md:596-664` still list **R-2c as open and blocking**. It is fixed: `ui/service.py:1514` `request_capped_resume` uses `readonly_fire_store` (`55878de`, G-1; `ReadOnlyFireStore` refuses every mutator, `d88df5f`), and the engine sub-review confirmed that every non-engine caller is read-only. Record R-2c as closed by `55878de`/`d88df5f` in `T-Se4Bk5` and in the `E-Sc9Rt4` rollup. The other `T-Se4Bk5` blockers still apply: a clean-tree whole gate and FU-8..11.
- **S-2.** `docs-md/overseer-runner-hld.md:688` still says a failed unit gate "halts the run again". That contradicts D3 continue-on-failure. Re-word it, or record the owner's `fail_fast` decision.
- **S-3.** `E-Qp4Zm8` `STATUS.md` rollup table is dated w24-07 (`1d5c6f9`). Later fixes (G-1..G-17, `T-8knqVe` w26-03) appear only as appended comments. The table's states still match the task headers, but the "authoritative" table predates them. Re-stamp it at closeout.
- **Checked consistent:**
  - `E-Sc9Rt4` STATUS/EPIC: 11 Done / 3 NOT Done, matching the task headers.
  - T-Ri7Dz2 AC4: `run_id` is a registry `Option` in `run_options.py:120` with no `ui_group`, so it is absent from `allowed_options()` and the dashboard argv allow-list.
  - ADR-0025: "Proposed — implemented except §26.4", consistent with `T-lTW88y` Partial.
  - `guide-sentinel.md` documents D2.

## Checked and found sound
- **Webhook:**
  - Webhook HMAC: SHA-256 over the raw bytes, `compare_digest`, scheme pinned per binding, and the signed timestamp checked against a two-sided 300 s window.
  - Replay key: a hash of the signed material.
  - Request limits: 64 KiB body cap (covers chunked), read timeout, concurrency limit.
  - Unknown-schedule and bad-signature responses are identical, and a rate limit applies.
  - Secrets never appear in logs.
  - `secret_file` is opened with `O_NOFOLLOW` and must be mode 0600.
  - The listener binds to loopback by default.
- **Watch paths:** `..`/absolute/`~`/NUL are refused, directory symlinks are not followed, scans are bounded (except M-4), and ignore lists prevent self-trigger loops.
- **Fire store:** a unique key per UTC instant, durable atomic writes (mkstemp, fsync, replace, dir fsync), `.tmp` files are ignored, and a corrupt record still blocks a fire.
- **Queues:** queue rehydration is correct, and run-now/natural-instant dedup holds.
- **Cron and jitter:** DST spring-forward fires once (fall-back double fire is the documented ADR-0014 limitation), and jitter is deterministic per key.
- **Governance:**
  - Window math is correct at midnight, DST 23 h/25 h days, Sunday week start and Dec→Jan rollover.
  - The quota gate records a suppressed fire.
  - Reservation happens under a flock and is released on crash or orphaned fires.
  - Resume is idempotent and the cap stays sticky.
  - Every read-only path uses `ReadOnlyFireStore`.
- **Tests:** fixed clocks throughout, DST/odd-zone/leap/rollover boundaries plus a seeded property test, and CLI-e2e (CliRunner) coverage for schedules, governance, cost cap, webhook and watch.

## Pre-submit checklist
- [x] Scope: whole-branch diff `origin/main...ce8f24d` (stated above).
- [x] Three alignment levels: project goals (determinism M-3, safe-by-default M-4/M-6, resume safety H-1); epic/task ACs (charter A1-A3, tickets read); code intent (`_is_live` docstring vs resume path; bundle vs `ui/src`).
- [x] Dimensions walked:
  - SOLID/KISS: OK.
  - DRY: OK, G-16 resolved.
  - Magic literals: none new found.
  - Pluggability: the ledger clock seam is not wired (M-3).
  - Spec/DAG: D3 OK.
  - Determinism/resume: H-1, M-3, L-7.
  - Errors/logging: M-2, L-4.
  - Testability: M-8, L-10.
  - Concurrency: H-1, M-1, M-2.
- [x] Each finding has location, observation, impact and next step.
- [x] Severity: none Critical. H-1 is High because it breaks period-cap and slot accounting in a shipped path.
- [x] Testing notes: inline (H-1 launcher fixture, M-8 clock, L-10 engine tier, L-11 timing).
- [x] No source edits; review only.
