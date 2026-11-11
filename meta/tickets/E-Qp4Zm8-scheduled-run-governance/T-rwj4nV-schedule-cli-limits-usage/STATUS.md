# STATUS

- ID: `T-rwj4nV-schedule-cli-limits-usage`
- Updated At: 2026-11-11
- State: Implemented — awaiting dedicated review (AC1-11 implemented, committed)
- Owner: TBD (assigned at sprint start)

## Reconciliation 2026-11-11 (By: developer · Role: developer · unit `w24-07-a1-task-status-sync` · docs only)
- Checked against branch `ad/enhancements-10nov` at HEAD `1d5c6f9`. Any older line below that says "not started", "uncommitted" or "in the working tree" is superseded by this block where they disagree.
- State: **Implemented — awaiting dedicated review (AC1-11 implemented, committed)**
- Committed `d0378d7`; the old "uncommitted, resume blocked on T-nI4DBf" state is superseded: `capped_resume.py` landed (`3e2870b`) and the `--local` drain path is now exercised by T-88wSLC (`test_resume_local_add_usd_completes_task_4_and_charges_the_same_fire`).
- **Why not Done**: no dedicated code review recorded for `schedules/usage_view.py`/the new CLI (the T-lTW88y gate covered the GET routes and resume path only); `usage_view.py` measured 78 % in the T-Se4Bk5 coverage run vs 97 % in T-88wSLC's (different test scopes; w24-04 owns the follow-up).

## This update
- **2026-11-11 (developer, unit `w20-01-a2-limits-usage-cli`):** implemented AC1-AC11 in `schedules/cli.py` + new `schedules/usage_view.py`
  (`build_usage_doc` / `render_usage` / `compact_limits`, shared with T-8knqVe) + `tests/schedules/test_cli_governance_e2e.py` (26 CliRunner
  tests, fixed clock, golden usage text, JSON-schema check, `--at`/`next` mtime-unchanged assertions). Flags: `--quota`, `--cost-per-run`
  (`--per-run-usd` kept as alias), `--cost-cap`, `--on-cap`, `--pause-ttl`, `--week-start`, `--prompt`, `--pin-prompt`; commands `set-limits`,
  `pin-prompt`, `usage`, `resume`; `list` LIMITS/TODAY/PAUSED, `history` reason+period + `governance` JSON, `next` "would skip", `validate` L4/L7 warnings.
  - **Gap (honest):** `resume` imports `service/capped_resume.py` (T-nI4DBf / w18-01), which was still UNCOMMITTED in the working tree when this
    landed; the import is lazy so the committed CLI imports cleanly, but `ao schedule resume` fails until that module is committed. Tested:
    `--queue` success (request file + new cap), refusals expired / no_headroom / not_paused / unknown run -> exit 3. NOT tested: the `--local`
    in-process drain path (needs the engine launcher; covered by T-nI4DBf's own drain tests).
  - Usage views use a read-only fire store (cost high-water-mark write-back is applied in memory only), so `usage`, `list` and `next` never write.
  - `validate` L4(a) reads `sentinel.llm.budget_usd` from the binding's workflow spec (it is not a schedule run arg).
  - Unrelated red test seen (carry-over, not this unit): `tests/service/test_fire_store.py::test_hld_event_table_is_complete` (new resume events missing from the HLD event table).
- 2026-11-11 — By: developer (unit `w20-08-a2-docs-adr-sync`) · Role: developer · Date: 2026-11-11 · Comment: UNFINISHED. `ao schedule set-limits/pin-prompt/usage/resume` and the limit flags exist in the uncommitted working tree (`schedules/cli.py`, untracked `schedules/usage_view.py`); the committed HEAD has none. Not reviewed. HLD §26.2.
- Ticket created by the `architect` agent as part of the E-Qp4Zm8 design package. Not started.
- Scope: `ao schedule add` limit/prompt flags, `set-limits`, `pin-prompt`, `usage`, `resume`; list/history/next columns; shared `usage_view` builder.

## Evidence
- None yet. No implementation has begun. Design basis:
  `docs-md/scheduled-run-governance-hld.md` and `docs-md/adr/ADR-0025-scheduled-run-governance-quotas-and-cost-caps.md`.

## Risks / Blockers
- Blocked until E-Sc9Rt4 `T-Cl6Jn9`, `T-Uz0Uol`, `T-PpE5lf` (and `T-nI4DBf` for `resume`) have merged.

## Next actions
1. Read the upstream tasks' **merged code** (including E-Sc9Rt4's), not only the HLD, before starting.
2. Implement against the acceptance criteria in `TASK.md`, in AC order; report the pytest delta, ruff/format, mypy, coverage.
3. Hand `build_usage_doc` to `T-8knqVe`.
