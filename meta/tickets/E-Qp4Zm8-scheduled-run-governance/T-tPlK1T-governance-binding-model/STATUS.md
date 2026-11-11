# STATUS

- ID: `T-tPlK1T-governance-binding-model`
- Updated At: 2026-11-11
- State: Done
- Owner: TBD (assigned at sprint start)

## Reconciliation 2026-11-11 (By: developer · Role: developer · unit `w24-07-a1-task-status-sync` · docs only)
- Checked against branch `ad/enhancements-10nov` at HEAD `1d5c6f9`. Any older line below that says "not started", "uncommitted" or "in the working tree" is superseded by this block where they disagree.
- State: **Done**
- All ACs met; reviewed (governance review fixes `e757305`: int/float parity, digest identity, HLD 6.3) and exercised by the schema-vs-pydantic parity corpus (w21-06). No unmet ACs. Merge to `main` via PR pending.

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
- **2026-11-11 (developer, unit `w12-04-a2-gov-review-fixes`):** fixed the w10-04 Opus review (commit 618b762):
  - **M1** schema/pydantic parity on integers: `max_runs`/`pause_ttl_seconds` accept an integral float (`3.0` -> `3`),
    reject `3.5`, `"3"`, `true`, `inf`, `nan` (JSON Schema "integer" semantics). Corpus cases added.
  - **M2** HLD §6.3 rewritten: schema is pydantic-generated, no `anyOf`, class-named `$defs`, deviations listed.
  - **L2** decided: digest identity = same *enforcement*; a cap-less `cost` block hashes like no `cost`. Test added.
  - **L3** stale sections below replaced.
  - **L4** won't-fix: Python-`re` `jsonschema` quirk (ECMA validators already reject); pydantic fail-closed. Pinned in
    the pydantic-only corpus and documented in HLD §6.3.
  - **L1** untouched (dead `_canonical` in `limits_digest`; harmless, not in this unit's list). Still open.
  - **H1** (committed HEAD unimportable: `engine.py` docstring/`run_id` mix-up, `models.py` `MAX_INTERVAL_SECONDS`
    defined after use) is *not* fixed here: the fixes exist only as other units' uncommitted edits to `engine.py` /
    `models.py` in the working tree. State at 2026-11-11: still uncommitted; branch HEAD 59743c5 is not
    importable from a clean checkout until those owners commit them.

## Evidence
- Review: `outputs/waves/w10/w10-04-a2-gov-binding-review.md` (565 passed in a patched temp worktree, governance.py
  100% coverage, 9/10 mutations killed). After this unit: see the unit report `w12-04-a2-gov-review-fixes.md`.
- Design basis: `docs-md/scheduled-run-governance-hld.md`, `docs-md/adr/ADR-0025-scheduled-run-governance-quotas-and-cost-caps.md`.

## Risks / Blockers
- H1 above: a clean checkout of the branch does not import until the `engine.py`/`models.py` fixes are committed.

## Next actions
1. Owners of the uncommitted `engine.py`/`models.py` edits commit them (H1); gate from a clean worktree.
2. `T-Uz0Uol` / `T-PpE5lf` consume `effective_limits()` / `limits_digest()` (digest = enforcement identity, see L2).
3. T-rwj4nV docs: note that a legacy limit-less schedules file fails to load (fail-closed, HLD §17).
