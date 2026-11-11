# STATUS

- ID: `T-Dc6Zr2-docs-refresh`
- Updated At: 2026-11-11
- State: Partial — docs reconciled with landed code; epic cannot close
- Owner: developer agent (overseer unit w20-07-a1-docs-refresh)

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
