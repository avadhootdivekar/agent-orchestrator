# TASK: T-Uz0Uol-usage-ledger

## Metadata
- Task ID: `T-Uz0Uol-usage-ledger`
- Epic ID: `E-Qp4Zm8-scheduled-run-governance`
- Owner: developer agent
- Created: 2026-11-10
- Last Updated: 2026-11-10
- Status: Draft
- Estimate: 3 days

## Requirements Mapping
- Requirement IDs: FR-2, FR-4, NFR-1, NFR-2, NFR-5 (see `../EPIC.md`)

## Description
The usage ledger answers one question: for a schedule, in a set of windows, how many invocations and how much spend
(actual + reserved)? It stores almost nothing. It derives totals from E-Sc9Rt4 fire records plus each run's
`state.json` and summary meta, and keeps only a monotonic high-water mark of observed cost on the fire record.

Read HLD §10 (normative), §6.4 and ADR-0025 D3. Read the **merged** code of E-Sc9Rt4 `T-Fr2Nx8`
(`service/fire_store.py`, `FireIntentRecord`, `FireIntentStore`) and of `T-tPlK1T` and `T-lJu9oZ`.

Files you own:
- `src/agent_orchestrator/service/usage_ledger.py` (new): `FireGovernance` (pydantic, HLD §6.4 shape,
  `schema="ao.schedule.governance/v1"`), `Charge`, `WindowUsage`, `UsageLedger`, `probe_run()` with an mtime-keyed
  cache, `COST_REPORTING_EXECUTORS`, `STATE_PROBE_GRACE_SECONDS`.
- `src/agent_orchestrator/service/fire_store.py` (edit, additive): `FireIntentRecord.governance: FireGovernance |
  None = None` (import from `usage_ledger` or define the model in `fire_store.py` and re-export. Pick the direction
  with no import cycle and state it); `write_suppressed(..., governance=None)` kwarg; `find_by_run_id(ws, run_id)`;
  `iter_schedule(ws, schedule_id, since: datetime)`; `update_governance(fire_key, **fields)` (atomic
  write-then-rename, same idiom as the store's other writes).
- `tests/service/test_usage_ledger.py` (new); additive cases in `tests/service/test_fire_store.py`.

Do NOT touch: `service/schedule_engine.py`, `service/schedule_launch.py`, `schedules/` (read-only), `engine.py`,
`models.py` (read-only: `compute_run_usage_totals`, `RunState`), `ui/`, `summarizer.py` (read the meta file by its
named constants `SUMMARY_DIR_NAME`/`SUMMARY_META_FILE`).

## Acceptance Criteria
1. `UsageLedger(fires, *, run_state_loader, clock)` is constructor-injected (no monkeypatching in tests).
   `usage(workspace_root, schedule_id, windows: dict[Period, Window]) -> dict[Period, WindowUsage]`, where
   `WindowUsage = (runs: int, actual_usd: float, reserved_usd: float, confidence: worst-of)`.
2. `charge(rec)` implements **every row** of HLD §10.2, with one test per row: `suppressed`, `launch_failed`, `intended`
   with no `state.json` (reservation held), `orphaned` with no run dir ($0, runs=1), `orphaned` with a run dir
   (observed), running (observed + reserved_left), terminal actual, terminal partial, terminal unknown (`max(observed,
   reserved)`), and paused/aborted/expired (observed, no reservation).
3. Observed cost = `compute_run_usage_totals(state).cost_usd + summary_cost_usd` (0 when the meta file is
   absent/corrupt, without raising).
4. **High-water mark:** `cost_observed_usd` never decreases. Tamper test: rewrite a run's `state.json` with a lower
   cost after a probe. The charge must not drop. The mark is persisted via `update_governance` only when it rises by
   ≥ $0.01 or the run is terminal (assert the write count).
5. **No double count:** a window total is a sum over distinct `fire_key`s. A record updated N times (resumes) still
   contributes once (test with 3 resumes).
6. **Restart determinism:** build a ledger, compute usage; build a fresh ledger over the same directory, compute again.
   The results are identical (dataclass equality).
7. Membership by `scheduled_for` only. Timezone change test: the same records under Europe/Berlin vs UTC re-bucket a
   23:30Z fire across the day boundary as expected, and the totals across both days are conserved.
8. Index: `{(ws, schedule_id): sorted[(scheduled_for, fire_key)]}`, built at construction and updated by
   `note_written(rec)`. `usage()` scans only records with `scheduled_for >= min(window.start)`. A perf test with 50 000
   records across 100 schedules computes one schedule's month usage in < 50 ms (use `time.perf_counter`, generous CI
   margin documented).
9. `probe_run` caches by `(path, st_mtime_ns, st_size)`. A corrupt/missing `state.json` returns `None` without
   raising. After `STATE_PROBE_GRACE_SECONDS` since `launched` with no `state.json`, a single
   `schedule.governance.cost_unknown`-class warning is emitted via an injected event sink (no direct logging config).
10. `cost_confidence`: `actual` if every settled task has `actuals_available`, `unknown` if none do, else `partial`.
11. Coverage ≥ 90 % on `usage_ledger.py`; ruff/format/mypy clean; E-Sc9Rt4 fire-store tests unchanged and green.

## Risks
- The temptation to store per-window counters "for speed". Do not. AC6/AC7 fail by construction if you do.
- `state.json` reads are the hot path. Keep the mtime cache, and never parse a run that is outside every requested
  window.
- An import cycle between `fire_store.py` and `usage_ledger.py`. Decide the direction once.

## Dependencies
- Upstream: E-Sc9Rt4 `T-Fr2Nx8` (merged); `T-tPlK1T`; `T-lJu9oZ`.
- Downstream: `T-PpE5lf` (gate), `T-nI4DBf` (cap state updates), `T-rwj4nV` / `T-8knqVe` (display).

## Pseudocode / Algorithm
```text
HLD §10.2 charge() is normative. usage():
  out = {p: WindowUsage.zero() for p in windows}
  for (sf, key) in index[(ws, sid)] from bisect(min start):
      rec = fires.get(key); c = charge(rec)
      for p, w in windows.items():
          if w.start <= sf < w.end: out[p] += c
  return out
```

## Schemas / Interface Notes
- Interface: `service.usage_ledger.{UsageLedger, FireGovernance, Charge, WindowUsage, probe_run}`;
  `FireIntentStore.{find_by_run_id, iter_schedule, update_governance}`.
- Spec / data schema: HLD §6.4 `governance` sub-object.
- Triggers / events: emits through an injected sink only (the engine owns `events.jsonl`).
- Artifacts: reads `<state_dir>/schedules/fires/**`, `<ws>/.orchestrator/runs/<id>/state.json`, `.../summary/summary.json`.

## Handoff Boundary
- Upstream: E-Sc9Rt4 fire store, governance model, windows.
- Downstream: `usage()` and `charge()` signatures are the contract for the gate and both UIs.

## Artifacts
- Docs/comments: `meta/tickets/E-Qp4Zm8-scheduled-run-governance/T-Uz0Uol-usage-ledger/`
- Large outputs: N/A
