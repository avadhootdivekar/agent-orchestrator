# STATUS

- ID: `T-28J9oR-cache-spec-config-surface`
- Updated At: `2026-10-05`
- State: `Draft`
- Owner: `developer` (Dev B)

## This update
- Rev 2 ticket: double opt-in policy, operator modes, `StrictBool`, config bounds and clamp, record
  binding fields; the CLI helper's construction half moved to T-o95l1M. Estimate unchanged (16 h).

## Evidence
- None yet (not started). Design evidence: HLD Rev 2 and ADR-0019 Rev 2.

## Risks / Blockers
- Blocked only until T-FJH6LI commit 1 lands (≤ 3 h into Sprint 1).
- Risk: concurrent `TaskSpec` edits by E-Ag7Pw3 (merge-time; HLD §24.2).

## Next actions
1. Wait for T-FJH6LI commit 1 (`constants.py`).
2. `models.py` + schema + `project_config.py`, then `settings.py`, then the CLI option, helper and group.
3. Run ruff, mypy, pytest and the NFR-2 gate.

## Comments
- By: architect · Role: architect · Date: 2026-10-04 · Comment: Status initialized (Draft).
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2 re-plan after the Phase-4
  consultation. State stays `Draft` (Sprint 1, Wave 1); this file, `TASK.md`, `HANDOFF.md` (when present)
  and the epic `STATUS.md` rollup agree.
