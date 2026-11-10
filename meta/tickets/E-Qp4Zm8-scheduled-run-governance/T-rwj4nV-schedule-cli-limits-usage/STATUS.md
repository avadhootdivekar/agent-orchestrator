# STATUS

- ID: `T-rwj4nV-schedule-cli-limits-usage`
- Updated At: 2026-11-11
- State: Partial (CLI in working tree, uncommitted; NOT done)
- Owner: TBD (assigned at sprint start)

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
