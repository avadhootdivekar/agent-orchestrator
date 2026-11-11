# STATUS

- ID: `T-nI4DBf-capped-run-pause-resume`
- Updated At: 2026-11-11
- State: Done (committed `3e2870b`; reviewed in T-lTW88y; limitation B-2 recorded)
- Owner: TBD (assigned at sprint start)

## Reconciliation 2026-11-11 (By: developer · Role: developer · unit `w24-07-a1-task-status-sync` · docs only)
- Checked against branch `ad/enhancements-10nov` at HEAD `1d5c6f9`. Any older line below that says "not started", "uncommitted" or "in the working tree" is superseded by this block where they disagree.
- State: **Done (committed `3e2870b`; reviewed in T-lTW88y; limitation B-2 recorded)**
- All ACs met: ACs 1-9, 11, 12 by `tests/service/test_capped_resume.py` (54 tests), AC10 coverage 95 % (>= 85 %), CLI tier `test_e2e_cli_cost_cap.py`; B-1 (engine shims dropped `launch_capped_resume`) fixed `b93b576`; `capped_resume.py` audited in the T-lTW88y gate (W-2 Info accepted). The older "uncommitted, unreviewed" state is superseded.
- Known limitation **B-2** (not an AC): a cap pause is settled only on the schedule's next due tick, so it is not visible right after the run exits; proposed fix (settle in the retire hook or a settle-only pass) not built.
- The HTTP resume route and Resume button are T-8knqVe's scope, not this task's.

## This update
- 2026-11-11 — By: developer (unit `w18-01-a2-capped-run-pause-resume`) · Role: developer · Date: 2026-11-11 · Comment: IMPLEMENTED (pending review/merge).
  **Done:** `service/capped_resume.py` (`request_capped_resume`, `check_resume` shared by the request and the drain re-check,
  `ResumeRefused` incl. a drain-only `launch_failed` reason, `max_add`/`default_add`, `drain_resume_capped`, `sweep_pause_expiry`,
  `is_governed_run`, `ResumeCappedRequest`); engine `drain_resume_requests` (single launch authority; the in-process path is the same
  method) + `_sweep_pause_expiry` once per tick before the evaluation loop; `ScheduledLauncher.launch_capped_resume` (original
  `LaunchRecord.workflow_path`, no re-render, pre- and post-spawn argv verification, fail closed); `ScheduleStateStore.mark_active`;
  three events. ACs 1-9, 11, 12 covered by `tests/service/test_capped_resume.py` (54 tests, fixed clocks, 95 % coverage of
  `capped_resume.py`, 11 mutations killed). CLI tier: `tests/test_e2e_cli_cost_cap.py` (7, CliRunner).
  **T-et7Edf wiring finished here** (a resume must render `--cost-cap-usd`): `cli.py` flag on `run`/`resume`, `COST_CAP_OPTION` in
  `OPTIONS`, snapshot fixture, allow-list and renderer tests. This touched `cli.py`/`run_options.py`, which this ticket's "do not
  touch" list excludes; the brief allowed it as the unfinished T-et7Edf wiring.
  **Deviations / unfinished (named):** (a) `ao schedule resume` / HTTP route belong to T-rwj4nV (already consuming
  `request_capped_resume`) / T-8knqVe (Resume button + generic-resume 409 via `is_governed_run`): not done here;
  (b) `capped_resume.REQUEST_TTL_SECONDS` duplicates the engine constant (import cycle), same value;
  (c) overlap is detected via `active_fire_keys` + liveness (not `_pending_schedules`);
  (d) without `per_run_usd`, `max_add` = remaining headroom (an over-large amount is `bad_amount`; remaining <= 0 is `no_headroom`);
  (e) a launch failure rolls the record back to `paused` and reports `launch_failed` (extra reason);
  (f) paused/aborted classification and `pause_expires_at` come from the existing retire hook, unchanged.
- 2026-11-11 — By: developer (unit `w20-08-a2-docs-adr-sync`) · Role: developer · Date: 2026-11-11 · Comment: UNFINISHED. `service/capped_resume.py` (request/drain/sweep) exists in the working tree but is untracked at doc-sync time; carry-over unit w18-01 owns committing it. Not reviewed, not claimed done. HLD §26.2/§26.4.
- Ticket created by the `architect` agent as part of the E-Qp4Zm8 design package. Not started.
- Scope: Pause/abort lifecycle: operator resume request (`resume_capped` drop request), engine drain as single launch authority, TTL expiry sweep.

## Evidence
- None yet. No implementation has begun. Design basis:
  `docs-md/scheduled-run-governance-hld.md` and `docs-md/adr/ADR-0025-scheduled-run-governance-quotas-and-cost-caps.md`.

## Risks / Blockers
- Not blocked: builds on committed T-PpE5lf / T-Cl6Jn9 / T-et7Edf on branch `ad/enhancements-10nov`.

## Next actions
1. Read the upstream tasks' **merged code** (including E-Sc9Rt4's), not only the HLD, before starting.
2. Implement against the acceptance criteria in `TASK.md`, in AC order; report the pytest delta, ruff/format, mypy, coverage.
3. Hand `request_capped_resume`/`ResumeRefused` to `T-rwj4nV` and `T-8knqVe`.

- 2026-11-11 — By: developer (unit `w22-01-a2-capped-resume-shim`) · Role: developer · Date: 2026-11-11 · Comment: B-1 FIXED: `_Deferred` (schedules/cli.py) and `_DeferredLauncher` (service/cli.py) now forward `launch_capped_resume`; regression tests added; strict xfail and `direct_launcher` workaround removed from tests/schedules/test_cost_cap_cli_e2e.py. B-2 KNOWN LIMITATION (not changed): a cap pause is settled only on the schedule's next due tick, so the pause/resume is not visible right after the run exits. Proposed fix (settle in the retire hook or a settle-only pass each tick) recorded in HLD §26.5; needs a design decision.
