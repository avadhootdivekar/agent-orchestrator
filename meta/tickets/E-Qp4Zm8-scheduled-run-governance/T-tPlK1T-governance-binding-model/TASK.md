# TASK: T-tPlK1T-governance-binding-model

## Metadata
- Task ID: `T-tPlK1T-governance-binding-model`
- Epic ID: `E-Qp4Zm8-scheduled-run-governance`
- Owner: developer agent
- Created: 2026-11-10
- Last Updated: 2026-11-10
- Status: Draft
- Estimate: 2 days

## Requirements Mapping
- Requirement IDs: FR-1, FR-2, FR-4, NFR-2 (see `../EPIC.md`); owner decision D-2

## Description
The governance data model. It adds `LimitsSpec` and its parts to the E-Sc9Rt4 binding model, adds `prompt_sha256` and
`week_start`, makes D-2 ("every schedule declares at least one limit") a load-time error, and extends
`specs/schedules.schema.json` to match.

Read HLD §6 and ADR-0025 D1/D7. Read the **merged** code of E-Sc9Rt4 `T-Sd1Kq7` (`schedules/models.py`, `store.py`,
`specs/schedules.schema.json`). This task edits those files additively after they land.

Files you own:
- `src/agent_orchestrator/schedules/governance.py` (new): `QuotaSpec`, `PeriodCostCap`, `CostLimits`, `LimitsSpec`,
  constants (`MAX_QUOTA_RUNS`, `MAX_USD`, `MIN_RESERVATION_USD`, `DEFAULT_PAUSE_TTL_SECONDS`,
  `MAX_PAUSE_TTL_SECONDS`, `MAX_RESUME_FACTOR`, `MAX_PROMPT_BYTES`, `LIMITS_REQUIRED`), and
  `limits_digest(limits) -> str` (sha256 of canonical JSON).
- `src/agent_orchestrator/schedules/models.py` (edit, additive): `ScheduleBinding.limits`, `.prompt_sha256`,
  `.week_start`; `ScheduleDefaults.limits`, `.week_start`; an `effective_limits()` / `effective_week_start()` accessor;
  the `ScheduleFile` D-2 validator; `RunArgs` rejects `cost_cap_usd`.
- `specs/schedules.schema.json` (edit, additive): `$defs` per HLD §6.3 and the binding/defaults properties.
- `tests/schedules/test_governance_model.py` (new), `tests/schedules/_fixtures.py` (new: `minimal_limits()`,
  `governed_binding(**kw)`)
- **E-Sc9Rt4 test fixtures** under `tests/schedules/` and `tests/service/` that construct bindings: edit them only to
  add `limits=minimal_limits()` (or the YAML equivalent). Use no other change, and list every touched file in STATUS.

Do NOT touch: `schedules/store.py` (unless the validator needs a hook. If so, keep it to one call and say why),
`scheduler.py`, `service/`, `engine.py`, `models.py`, `ui/`.

## Acceptance Criteria
1. Models match HLD §6.2 exactly: field names, types, defaults, `extra="forbid"`, bounds (`max_runs` 1..10 000;
   `max_usd`/`per_run_usd` in (0, 100 000] with no NaN/inf; `pause_ttl_seconds` 0..30 d), and `on_cap` defaults to
   `"pause"`.
2. Validators, each with a named error message: duplicate quota period; duplicate cost period; `per_run_usd >
   min(periods.max_usd)`; `prompt_sha256` not 64 lowercase hex; `prompt_sha256` set without `prompt_file`;
   `cost_cap_usd` inside `run_args` (message points to `limits.cost.per_run_usd`).
3. **D-2:** a `ScheduleFile` with any binding whose effective limits (`binding.limits` else `defaults.limits`) do not
   `declares_any_limit()` fails to load. The error message is `LIMITS_REQUIRED` formatted with the schedule id. This is
   covered for: no limits at all; `limits: {}`; `quotas: []` only; `cost: {on_cap: pause}` only; a binding with only
   `until.max_runs` (must still fail, Q-2 default).
4. Inheritance: binding `limits` replaces `defaults.limits` wholesale, with no deep merge. This is asserted with a
   defaults quota + binding cost-only ⇒ effective = cost-only.
5. `limits_digest` is stable across key order and float formatting (`2` vs `2.0`), and differs when any limit value
   changes.
6. JSON Schema parity: a shared corpus of ≥ 15 YAML documents (valid + invalid shapes) is validated by both the schema
   (`jsonschema`) and pydantic. They agree on every *shape* case. The cross-field cases (D-2 with defaults,
   uniqueness, per_run vs periods) are pydantic-only, and the test labels them so.
7. Round-trip through E-Sc9Rt4's `ScheduleStore.save()/load()` preserves `limits`, `prompt_sha256` and `week_start`
   byte-for-byte in YAML.
8. All E-Sc9Rt4 tests still pass after the fixture update. Report the count of fixture files touched.
9. Coverage ≥ 90 % on `schedules/governance.py`; ruff/format/mypy clean.
10. **(review amendment 2)** Ship `tests/schedules/_fixtures.py::minimal_limits()` **from day one** (or land immediately
    after T-Sd1Kq7), so E-Sc9Rt4 fixtures are never written limit-less and then rewritten.
11. **(RunArgs)** `RunArgs` explicitly excludes `cost_cap_usd`, even if `RunArgs` is derived from `allowed_options()`
    (the new Option would otherwise be auto-admitted). Test: a binding with `run_args: {cost_cap_usd: 5}` fails
    validation. T-PpE5lf asserts the same at the launch boundary.

## Risks
- D-2 is a breaking change for E-Sc9Rt4's fixtures (HLD §17). Do it in **one** PR with the fixture helper, so the
  tree is never red between merges.
- Do not let `RunArgs` accept `cost_cap_usd` just because the run-options registry now allow-lists it (T-et7Edf). A
  schedule's cap has exactly one source.

## Dependencies
- Upstream: E-Sc9Rt4 `T-Sd1Kq7` (merged). `T-lJu9oZ` is **not** required (types only: import `Period`/`WeekStart` from
  `schedules/windows.py` if merged, else define the `Literal`s here and have `windows.py` import them. Coordinate in
  STATUS).
- Downstream: `T-Uz0Uol`, `T-PpE5lf`, `T-rwj4nV`, `T-8knqVe`.

## Pseudocode / Algorithm
```text
class ScheduleFile:
  @model_validator(mode="after")
  def _limits_required(self):
      for b in self.schedules:
          eff = b.limits if b.limits is not None else self.defaults.limits
          if eff is None or not eff.declares_any_limit():
              raise ValueError(LIMITS_REQUIRED.format(id=b.id))
      return self
```

## Schemas / Interface Notes
- Interface: `schedules.governance.*`; `ScheduleBinding.effective_limits(defaults) -> LimitsSpec`.
- Spec / data schema: HLD §6.1 YAML, §6.3 JSON Schema.
- Triggers / events: N/A
- Artifacts: `<workspace>/.ao/schedules.yaml`, `specs/schedules.schema.json`.

## Handoff Boundary
- Upstream: E-Sc9Rt4 T-Sd1Kq7 merged code; HLD §6.
- Downstream: `effective_limits()` and `limits_digest()` are the contract the ledger/gate use.

## Artifacts
- Docs/comments: `meta/tickets/E-Qp4Zm8-scheduled-run-governance/T-tPlK1T-governance-binding-model/`
- Large outputs: N/A
