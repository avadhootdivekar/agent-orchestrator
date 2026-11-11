# STATUS

- ID: `T-Dc6Zr2-docs-refresh`
- Updated At: 2026-11-11
- State: Partial — docs reconciled with landed code; epic cannot close
- Owner: developer agent (overseer unit w20-07-a1-docs-refresh)

## Sync 2026-11-11 (w36-02, developer; docs only; AC10 spot-check)
- **Checked (static, file by file against `src/` and `ui/`):** HLD §5.3 state-dir layout: `service/paths.py::default_state_dir` (env > XDG > `~/.local/state/ao/service`); `supervisor.json`, `port_resolution.json`, `logs/` (`service/supervisor.py`), `boot_resume.json` (`boot_resume.py`); `schedules/state.json`, `events.jsonl` + `.1` single rollover, `fires/<slug>/<fire_key>.json` (`fire_store.py`); `schedules/engine.lock` (`schedule_engine.py`); `schedules/watch/<slug>/<id>.json` (`watch.py`); `schedules/requests/<uuid>.json` written by `schedules/cli.py::_queue_request`, `webhook.py`, `capped_resume.py`, `schedule_engine._defer_run_now`. Dashboard: the five routes exist in `ui/app.py:580-632`; panel files all exist (`ui/src/components/Schedules.tsx`, `tabs/model.ts`, `tabs/TabView.tsx`, `App.tsx` NAV entry, `api.ts` four methods, `types.ts` `Schedule`/`ScheduleEvent`, `test/schedules.test.tsx`, `tests/ui/test_schedules_api.py`).
- **Drift fixed in `docs-md/scheduler-triggers-hld.md`:** (1) §5.3 slug description said "readable basename plus hash"; code is the whole sanitized resolved root path + `-` + 8 hex of sha256; (2) `usage.lock` (governance lock, `governance_gate.GOVERNANCE_LOCK_NAME`) and `engine.lock` were missing from the layout; (3) `watch/` path and the four request `kind`s (run_now, webhook, restart_completed, resume_capped) and uuid file naming added; (4) lazy creation / atomic `.tmp` note and state-dir env precedence added; (5) §10 now notes the extra governance routes (`governance-events`, `usage`, `paused-runs`, `resume-capped`) that sit beside the five; (6) §21/§23 wording updated so they no longer say the layout was not re-derived.
- **Not checked:** runtime behaviour (no live service or browser run; the dashboard panel was not rendered); `pytest`/`vitest` not run (docs-only change, and pydantic is not installed in the system python used here); the §11 panel's described row behaviour (expandable 20-event history, polling) beyond file/symbol existence and `SCHEDULES_POLL_MS = 10000`; `ScheduleUsage.tsx`/`schedule-usage.test.tsx` exist but are governance-owned and not described in §11.
- **Unfinished (unchanged):** epic close-out (T-Se4Bk5, T-Te3Qw8, T-Un0Lm6, T-Ap1Xs3 state); until M1 / dashboard W-1 restart note. Epic not closed.

## Sync 2026-11-11 (w28-03, developer; docs only)
- Done (T-Wh9Kv1 review D-1): the optional, unsigned `X-AO-Timestamp` gate on `scheme: github` is now documented in `docs-md/scheduler-triggers-hld.md` §8.2 (pseudocode + design note) and `docs-md/guide-scheduled-runs.md` §6, checked against `service/webhook.py::verify_delivery` (malformed -> 401 `bad_timestamp` before HMAC; skew > 300 s -> 401 `stale` after HMAC; absent -> no check; not part of the signed material/replay key).
- Unfinished: unchanged from the w27-02 block below (epic close-out, AC10 runtime spot-checks, until M1 / dashboard W-1 restart note). `docs-md/scheduler-triggers-hld.md` also holds another unit's uncommitted §11 edit, which was not committed by this unit.

## Sync 2026-11-11 (w27-02, developer; docs only; HEAD `c4e3871`)
- Supersedes the w24-07 open list below for HLD items: w25-06 (`57c8de4`) re-synced the HLD, governance HLD §26 and ADR-0025 (`[wt]` markers and the `--watch`/`--from-workflow` "not shipped" wording are gone).
- **Unfinished:** epic close-out (blocked on T-Se4Bk5, T-Te3Qw8, T-Un0Lm6, T-Ap1Xs3); AC10 file-by-file spot-check of the §5.3 state-dir layout and of the dashboard panel at runtime; docs must be updated when the until M1 / dashboard W-1 restart fix lands (the guide currently does not say the service must be stopped to restart a completed schedule).

## Reconciliation 2026-11-11 (By: developer · Role: developer · unit `w24-07-a1-task-status-sync` · docs only)
- Checked against branch `ad/enhancements-10nov` at HEAD `1d5c6f9`. Any older line below that says "not started", "uncommitted" or "in the working tree" is superseded by this block where they disagree.
- State: **Partial — docs reconciled with landed code; epic cannot close**
- Met: HLD §21-23, ADR-0014 late-gate sections, LLD, README, ROADMAP, ai-epics, operator guide `docs-md/guide-scheduled-runs.md`; ticket-state sync redone by this unit (w24-07) for both epics.
- **Unmet / open**: epic close-out (blocked on T-Se4Bk5 and T-Te3Qw8, not Done); AC10 spot-check not done for the §5.3 state-dir layout file-by-file and for the dashboard panel at runtime; HLD still carries `[wt]` (working-tree-only) markers for webhook scheme pinning that is now committed - `docs-md/scheduler-triggers-hld.md` had another unit's uncommitted edit when this ran, so it was not touched; `ao schedule add --watch`/`--from-workflow` are marked NOT shipped in HLD §9.1 although both now exist.

## This update — 2026-11-11 (developer, unit `w20-07-a1-docs-refresh`)
- **Done:** HLD status header + §8.2 (scheme pinning, per-schedule nonce cache, extra statuses/reasons) + §9.1 (CLI flags actually shipped; `--watch`/`--from-workflow` marked NOT shipped) + §12 (full event table) + new §21 *Deviations* (11 entries), §22 *Delivery status*, §23 *Constants sweep*; ADR-0014 status + "Late-gate corrections (2026-11-11)"; LLD §7/§12 + `Trigger` snippet; README `ao schedule` section + `interval` trigger row; ROADMAP §1 row, §2d, §3.2, §4 (limitations a-j; §3.1 untouched); ai-epics evidence log, traceability table, close-out; new operator guide `docs-md/guide-scheduled-runs.md` (AC9: nightly / laptop off / overlap / stop / git push / why-not-fired); ticket sync (EPIC checkboxes, T-Ri7Dz2, T-Un0Lm6, T-Te3Qw8, T-Se4Bk5, this ticket).
- **Spot-checked against code, not reports (AC10):** all 11 constants and the default webhook port/rate/binding defaults by `grep` in `src/` (HLD §23); the five dashboard routes in `ui/app.py:566-594` and the UI files' existence; `ao schedule` subcommands and flag set by grepping `typer.Option` in `schedules/cli.py` (found `--watch`/`--from-workflow` absent); webhook verification path by reading `verify_delivery` and `make_webhook_app` (scheme pin, status codes, reasons); emitted event names by `grep` of `src` vs `EVENT_LEVELS`; `Trigger` model in `models.py:718`; test counts by `def test_` count per file; suite run: 1233 passed / 1 failed (`test_hld_event_table_is_complete`, stale vs uncommitted governance events), ruff clean on service+schedules, `mypy src` 0 errors.
- **Not checked:** the §5.3 state-dir layout file-by-file (only `engine.lock`, `events.jsonl`, `state.json`, `requests/`); the dashboard panel's runtime behaviour (no browser run); webhook scheme pinning exists only in the uncommitted working tree (`service/webhook.py`, `schedules/models.py`, `schedules/cli.py`) — docs describe it as landed code in the tree, flag **[wt]**.
- **Unfinished:** epic close (T-Se4Bk5 not started; T-Te3Qw8 partial); ROADMAP/README were not re-verified for unrelated rows; the stale `test_hld_event_table_is_complete` belongs to the governance carry-over unit, not touched here.
- Ticket created by the `architect` agent as part of the E-Sc9Rt4 design package. Not started.
- Scope: post-implementation reconciliation of `docs-md/`, ADR-0014, `lld-agent-orchestrator.md`
  §7/§12, the ai-epics page, `meta/ROADMAP.md` §1/§2/§3.2/§4, `README.md`, and the full ticket
  status sync — **including an explicit list of deviations from the original design**.

## Evidence
- None yet — no implementation has begun.

## Risks / Blockers
- Blocked until: `T-Se4Bk5` has closed and every implementation task is Done.
- Standing risk: this is the task a slipping sprint cuts. Sprint 3 is sized at 4 of 15 days so it
  cannot be squeezed; if it is being cut anyway, escalate rather than trimming acceptance criteria.

## Next actions
1. Run AC2's mechanical constants/paths sweep first — it finds the divergences a section-by-section
   read misses, and its results determine how much of AC1 is actually rework.
2. Write the "Deviations from the original design" subsection even if it says "none" (a silent
   absence reads as "not checked").
3. Sync `meta/ROADMAP.md` §1/§2-series/§3.2/§4 — and leave §3.1 (authentication, authorization on
   triggers, audit) untouched and still highest priority. One HMAC-protected webhook is not auth.
4. Reconcile ticket states across `EPIC.md`, the epic `STATUS.md` and every task folder so wording
   and counts match (tickets README rules 8 and 9), then close the epic.
