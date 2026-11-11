# STATUS

- ID: `T-Hq3vXd-docs-refresh`
- Updated At: 2026-11-11
- State: Partial — docs synced to landed code; epic NOT closeable
- Owner: TBD (assigned at sprint start)

## Update 2026-11-11 (w30-02) — By: developer · Role: developer · docs only
- Done: I-6 blind-executor note added beside HLD §9.3 (`scheduled-run-governance-hld.md`); `meta/ROADMAP.md` §2d-i table with all 11 E-Qp4Zm8 tickets; AC1 name grep over the governance HLD, ADR-0025, run-options guide, guide-scheduled-runs, guide-sentinel and scheduler-triggers HLD: every `ao schedule <subcommand>` (list, next, history, validate, add, set-limits, pin-prompt, usage, resume, remove, enable, disable, run-now, daemon) and every flag was matched against `uv run ao ... --help` and `src/`.
- Mismatch fixed: `run-options-and-limits-guide.md` named `--per-run`; the real flag is `--cost-per-run` (alias `--per-run-usd`). Not mismatches: `ao schedule save` (HLD D9) is the Python `save` function, not a command; `--usage--cap-hit...` is a heading anchor.
- Task stays Partial: epic not closeable while the other tickets are open (see ROADMAP §2d-i).

## Reconciliation 2026-11-11 (By: developer · Role: developer · unit `w24-07-a1-task-status-sync` · docs only)
- Checked against branch `ad/enhancements-10nov` at HEAD `1d5c6f9`. Any older line below that says "not started", "uncommitted" or "in the working tree" is superseded by this block where they disagree.
- State: **Partial — docs synced to landed code; epic NOT closeable**
- Done: HLD §26 as-built, ADR-0025 implementation outcome, guides, ticket sync by this unit (w24-07).
- **Unmet / open**: `scheduler-triggers-hld.md` cross-links (file dirty from another unit); AC3 "Recurring jobs with limits" walkthrough with captured output (the CLI is now committed, so it can be written); AC1 full name grep; HLD §26 still lists pause/resume, the CLI and `--cost-cap-usd` wiring as unfinished/uncommitted although they landed (`d0378d7`, `3e2870b`) - needs one more pass after T-8knqVe; I-6 blind-executor note beside HLD §9.3 not written.

## Update 2026-11-11 (w29-03) — By: developer · Role: developer · Date: 2026-11-11
- AC3 done: end-to-end walkthrough (schedule with quota + cost cap -> usage -> cap hit -> pause -> resume -> set-limits/disable) added to `docs-md/run-options-and-limits-guide.md`, every command run against the clone's CLI (`uv run ao schedule ...`) in a temp workspace/state dir; output captured. Caveat stated in the doc: the cap-hit/paused state was **seeded** (no daemon, no agent run), and `resume` ran with `--queue`; the installed `ao` snapshot lacks `schedule`. Observed: duplicate `resume --queue` for the same run is accepted twice (documented, not changed - docs only).
- Cross-links added: `scheduler-triggers-hld.md` Related line <-> `scheduled-run-governance-hld.md` Related line, plus `guide-scheduled-runs.md` intro.
- Still open: I-6 blind-executor note beside HLD §9.3; AC1 exhaustive name grep; `meta/ROADMAP.md` rows. State stays Partial.

## Update 2026-11-11 (w28-05)
- HLD §26 re-pass done (pause/resume, CLI, `--cost-cap-usd`, resume-capped route, AC17 now described as landed); ADR-0025 outcome table and `run-options-and-limits-guide.md` synced. Still open: AC3 walkthrough with captured output, `scheduler-triggers-hld.md`/`guide-scheduled-runs.md` cross-links (dirty from other units), I-6 note beside HLD §9.3. Task stays Partial.

## This update
- 2026-11-11 — By: developer (unit `w20-08-a2-docs-adr-sync`) · Role: developer · Date: 2026-11-11 · Comment: PARTIAL, honest. Done: HLD status line + §4 line refs refreshed + new §26 (As built, deviations, unfinished); ADR-0025 status + "Implementation outcome" (kept Proposed, not Accepted); epic narrative evidence log; `run-options-and-limits-guide.md` (cost cap section, marked flag-not-committed); `guide-sentinel.md` (cap interaction, kill under-count); all task STATUS + epic rollup.
  NOT done (with reason): (a) `scheduler-triggers-hld.md` cross-links - file has another unit's uncommitted edits, not touched; (b) `meta/ROADMAP.md` rows - outside this unit's brief; (c) AC3 "Recurring jobs with limits" walkthrough with captured output - the CLI is uncommitted, writing it would describe unlanded behaviour; (d) AC1 full grep of every name - done for events, constants, flags and routes cited in §26 (greps over `src/`), not exhaustively over HLD §1-§24, which remains design text (§26 supersedes it); (e) `record_trip` docstring - needs `src/`, out of scope. Re-run this task after the carry-over units commit.
- Ticket created by the `architect` agent as part of the E-Qp4Zm8 design package. Not started.
- Scope: Post-implementation reconciliation of docs-md, ADR-0025, E-Sc9Rt4 HLD cross-links, run-options guide, sentinel guide, ROADMAP.

## Evidence
- None yet. No implementation has begun. Design basis:
  `docs-md/scheduled-run-governance-hld.md` and `docs-md/adr/ADR-0025-scheduled-run-governance-quotas-and-cost-caps.md`.

## Risks / Blockers
- Blocked until all other tasks (incl. `T-lTW88y` final gate) are done.

## Next actions
1. Read the upstream tasks' **merged code** (including E-Sc9Rt4's), not only the HLD, before starting.
2. Implement against the acceptance criteria in `TASK.md`, in AC order; report the pytest delta, ruff/format, mypy, coverage.
3. Grep every documented public name against `src/` before marking done.
