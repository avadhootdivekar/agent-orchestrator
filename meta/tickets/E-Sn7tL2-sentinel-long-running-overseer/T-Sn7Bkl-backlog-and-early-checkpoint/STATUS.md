# STATUS

- ID: `T-Sn7Bkl-backlog-and-early-checkpoint`
- Updated At: `2026-11-09`
- State: `Done`
- Owner: `developer (claude-sonnet-5-5)`

## This update
- By: claude-opus-5-5 · Role: architect · Date: 2026-11-09 · Comment: Task created from epic breakdown (owner-approved design).
- By: claude-sonnet-5-5 · Role: developer · Date: 2026-11-09 · Comment: **Implemented.** `sentinel_backlog.py` + engine step
  `_sentinel_bandwidth` (L1 release, L2 early checkpoint, FR-19 cap/breaker), `graph_check.plan_early_checkpoint`, overseer tool
  (`outputs/backlog.json` checker `OV-BL1..7`, publication to `control/backlog.json`, `digest.carry_over[]`/`soft_barrier`,
  `OV-R17`, `barrier: soft` rule, late-unit ledger ingest), instructions/contract/README. As-built: `docs-md/sentinel-hld.md` §6.9.
  AC1 NFR-13: idle-slot-seconds-with-work-available off=3.5 vs on=0.01 (P=6, seeded lognormal, real engine; >99% less).
  AC2-4 met by `tests/test_sentinel_backlog.py` (14) + `tests/test_overseer_tool_backlog.py` (35).

## Evidence
- Gates: `ruff check .` + `ruff format --check .` clean; `mypy src`: 10 errors, all pre-existing (`_version.py`, `overseer_tool.py`
  limit_events) — none in changed lines; `mypy .` fails earlier on a duplicate `conftest` module in `benchmarks/` (pre-existing).
  Full `pytest`: 10983 passed / 6 failed on the shared tree mid-run; after fixes 3 of the 6 (other unit's WIP) pass, my own
  (`test_spawn_provenance`: breaker must not read `.origin`) fixed via `SentinelState.injected_ids`; the 2 bench
  `test_fake_subject_full_suite_run_*` failures reproduce on clean HEAD (pre-existing). Affected subset re-run: 777 passed.
  No UI changes => `npm test`/`build` not run.

## Risks / Blockers
- Carry-over stragglers are not predecessors of the next checkpoint and could still run when a tail starts (documented).
- No live smoke (T-SnXAcc). The template workflow declares no `sentinel_injected_task_count` breaker; the engine cap is the guard.

## Next actions
1. T-SnXAcc: e2e matrix + live smoke (idle-slot scenario with backlog + early checkpoint).
