# STATUS

- ID: `T-tPlK1T-governance-binding-model`
- Updated At: 2026-11-11
- State: Done (pending review/merge)
- Owner: TBD (assigned at sprint start)

## This update
- **2026-11-11 (developer, unit `w08-01-a2-gov-binding-model`):** implemented. New `schedules/governance.py`
  (`QuotaSpec`, `PeriodCostCap`, `CostLimits`, `LimitsSpec`, constants, `limits_digest`; `Period`/`WeekStart` imported
  from `windows.py`); `models.py`: `limits`/`week_start`/`prompt_sha256`, `effective_limits()`/`effective_week_start()`,
  D-2 `ScheduleFile` validator, `RunArgs` rejects `cost_cap_usd`; `week_start`/`limits` added to `INHERITABLE_FIELDS`;
  schema regenerated. Numeric limit fields are `strict` (a quoted `"3"` is rejected, matching `run_args`).
  Tests: `tests/schedules/test_governance_model.py` (+ `_fixtures.py`). Fixture files touched: **2**
  (`tests/schedules/test_models.py`, `tests/schedules/test_store.py`); `tests/service/` needed no edits.
  Notes: the schema is pydantic-generated, so HLD §6.3 `anyOf` "at least one limit" is not emitted; D-2 stays
  pydantic-only (as the HLD states). `store.py` untouched.
- Ticket created by the `architect` agent as part of the E-Qp4Zm8 design package. Not started.
- Scope: `LimitsSpec` & co., binding/defaults deltas, D-2 validator, JSON Schema delta, shared fixture helper + E-Sc9Rt4 fixture updates.

## Evidence
- None yet. No implementation has begun. Design basis:
  `docs-md/scheduled-run-governance-hld.md` and `docs-md/adr/ADR-0025-scheduled-run-governance-quotas-and-cost-caps.md`.

## Risks / Blockers
- Blocked until E-Sc9Rt4 `T-Sd1Kq7` has merged.

## Next actions
1. Read the upstream tasks' **merged code** (including E-Sc9Rt4's), not only the HLD, before starting.
2. Implement against the acceptance criteria in `TASK.md`, in AC order; report the pytest delta, ruff/format, mypy, coverage.
3. Hand `effective_limits()`/`limits_digest()` to `T-Uz0Uol`/`T-PpE5lf`.
