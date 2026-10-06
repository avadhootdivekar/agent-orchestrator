# STATUS

- ID: `T-eyn5UG-cache-reporting`
- Updated At: `2026-10-05`
- State: `Done`
- Owner: `developer` (Dev B)

## This update
Implemented in commit `cbf9152` (branch `worktree-agent-a18ce2c08e42a3a5a`): `cache/report.py`
(the HLD 8.8.1 helpers), the `write_status`, `usage.py` (sites A and B, `UsageReport.result_cache`,
payload) and `outcomes.py` (`"cached"`) hooks, with five new test modules (52 tests). Every caller
imports `cache.report` lazily and only when `state.result_cache` is non-empty. The cache stays OFF
by default; the print sites (`_print_state`, `report-usage` text) belong to T-o95l1M.

## Acceptance criteria
| AC | Result | Evidence |
|----|--------|----------|
| 1 U-RP1..RP3 | PASS | `test_report.py::TestCurrency` (cycle and `ended_at` binding, hit on a no-longer-succeeded task, unknown task); `TestTaskView`: exactly the 15 documented keys, the first five in brief order, validates against the verbatim 13.5 `resultCacheTask` schema for hit / would_hit / probe-failed miss / ineligible; `TestRunBlock` validates against `resultCacheRun` |
| 2 U-RP4 | PASS | `saved_*` (incl. `saved_tokens`) sum current hits only; `avoidable_cost_usd` sums current would_hits; `lookups == hits + would_hits + misses`; `run_block` is None when nothing is current; floats rounded after summing (no `1.2345000000000002`) |
| 3 U-RP5 | PASS | exact `Result cache: hits=2 (saved ~$1.2345 est., ~54000 tokens, ~312s) would_hits=0 misses=1 stored=1 ineligible=1` from a state built to the 8.8.2 example; `format_summary_line(None) is None` |
| 4 U-RP6 | PASS | `usage_counters` returns the 13.6 contribution (with `miss_reasons` / `store_skip_reasons`) and validates against the 13.6 schema; None when nothing is current |
| 5 U-RP7 | PASS | AST check: `report.py` imports only `__future__`, `collections`, `typing`, `models` |
| 6 U-RP8 | PASS | one `mixed_state` (current hit, stale hit by cycle, stale hit by `ended_at`, current miss) gives one answer through the helpers (`test_report.py`), `write_status` (`test_status_result_cache.py`), `aggregate_usage` (`test_usage_result_cache.py`) and `_settle_reason` (`test_outcomes_result_cache.py`) |
| 7 U-RS1 | PASS | with an empty map (and with only stale records) `status.json` has exactly the literal pre-epic top-level (11) and per-task (14) key lists |
| 8 U-RS2 | PASS | a current hit adds exactly `tasks[tid].result_cache` and the top-level `result_cache`; both validate against 13.5; every other value is unchanged (compared with the same state without records; `updated_at` excluded, it is the store wall clock) |
| 9 U-US1..US4 | PASS | site A: first-pass hit not counted and adds 0 (a group of only first-pass hits does not appear); hit-after-spend (carried 0.5 USD, 300/70 tokens) adds its spend to the group but is not counted as a task; stale hits are counted normally. Site B: a current-hit producer gets no attribution, the other producer and a stale-hit producer still do. Cross-run object sums over runs, validates against 13.6; the payload key list equals the pre-epic literal when absent |
| 10 U-OC1 | PASS | `"cached"` for a current hit; `"dispatched"` for stale hits and a normal success; `_settle_reason(ts)` unchanged; `grade_run` rows carry `cached` for the hit only |
| 11 U-LZ1 | PASS | `test_lazy_read_sides.py`: in a fresh interpreter, `write_status` + `aggregate_usage` + `_settle_reason` with an empty map load NO `agent_orchestrator.cache*` module; with a record `cache.report` is loaded (positive control) |
| 12 Hygiene | PASS | existing `test_runstate.py`, `test_outcomes.py`, `test_usage_*.py`, `test_status_artifact.py`, `test_hardening_usage_signals.py`, `test_e2e_cli_usage_join.py` pass unedited (174 passed with the spawn-provenance and NFR-2 gate); ruff and mypy clean (see Evidence) |

## Evidence
- `.venv/bin/python -m pytest -q -p no:cacheprovider tests/cache/test_report.py tests/cache/test_status_result_cache.py tests/cache/test_usage_result_cache.py tests/cache/test_outcomes_result_cache.py tests/cache/test_lazy_read_sides.py` -> **52 passed**.
- `.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_runstate.py tests/test_outcomes.py tests/test_usage_report.py tests/test_usage_join.py tests/test_usage_verdicts.py tests/test_status_artifact.py tests/test_hardening_usage_signals.py tests/test_e2e_cli_usage_join.py tests/test_spawn_provenance.py tests/test_nfr2_regression_gate.py` -> **174 passed**.
- `tests/cache/test_noop_proof.py` (I-1, I-2 goldens, unedited) stays green with the `runstate` / `usage` / `outcomes` edits.
- `.venv/bin/ruff check src tests` -> All checks passed. `.venv/bin/ruff format --check src tests` -> clean (only the generated `_build_info.py` is ever flagged). `.venv/bin/mypy src tests/cache` -> only the 4 pre-existing `_version.py` errors.
- Shared-file diff: `runstate.py` +13/-1 (about 7 added lines in `write_status`), `outcomes.py` +12/-3, `usage.py` additive (sites A/B, `ResultCacheUsage`, helpers); `git grep` of `.origin ==`/`!=`/`in` under `src/`: none added.

## Deviations from the HLD blocks (reason)
1. **Rounding.** `run_block` and `usage_counters` round summed floats (`_USD_DIGITS = 6`, `_SECONDS_DIGITS = 3`) so `status.json` never shows binary-float noise; the HLD examples are already round numbers. Per-record values are unrounded.
2. **A current hit's verdict is still read.** Site A skips the hit for the group counters, feedback and survival joins, but the verdict-reading half of the loop is unchanged: a reviewer task served from the cache still describes this run's producers (same inputs by key), so its verdict counts in `reviews_seen` / `verdicts_found` and is attributed to producers (site B filters the producers only).
3. **Hit-after-spend with no other task in the group** creates a group row with `tasks == 0` and the real carried `cost_usd` / tokens (mean cost and retry rate are `None`, no division by zero). A first-pass hit never creates a group.
4. **`ResultCacheUsage`** lives in `usage.py` next to `UsageReport` (it is a report model); `report.usage_counters` returns plain dicts, so `report.py` stays dependent on `models` only (U-RP7).
5. **`report.UNKNOWN_REASON`** (`"unknown"`) is the bucket for a miss record without a reason; `report.BRIEF_FIELDS` names the five D35 fields in order. Both are module constants, not literals in the helpers.
6. **Test builders.** `tests/cache/_report_states.py` holds the shared states and the verbatim 13.5 / 13.6 schemas (reused by T-o95l1M's tests).

## Risks / Blockers
- None. A `UsageReport` consumer that iterates `model_fields` (none found) would see the new optional field; the JSON payload omits it when absent.
- T-bLpoze (dashboard) reuses `result_cache_status_fields` / `task_view`; keep the lazy-import rule there (U-LZ2).

## Next actions
1. T-o95l1M prints the summary and `report-usage` lines from these helpers.
2. T-bLpoze, T-nPMuz4 and T-JCOAsq consume `report.py` and `UsageReport.result_cache`.

## Comments
- By: architect · Role: architect · Date: 2026-10-04 · Comment: Status initialized (Draft).
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2 re-plan after the Phase-4
  consultation.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 3 re-plan after the
  early-gate review and the manager's scope decisions (HLD 23.5). State stays `Draft`
  (engine set); this file, `TASK.md`, `HANDOFF.md` (when present) and the epic `STATUS.md` rollup
  agree.
- By: developer · Role: developer · Date: 2026-10-05 · Comment: State -> Done (commit `cbf9152`).
  Every acceptance criterion passes; deviations 1-6 above are small and documented. Epic
  `EPIC.md` / `STATUS.md` rollup updated to match.
- By: developer · Role: developer · Date: 2026-10-05 · Comment: G1b remediation touched this task's code (sec S-2 (format_summary_line made total)) in commit `6ba90ba`; findings and regression tests are listed in `T-fXWbqg-cache-review-gates/STATUS.md` (G1b remediation). Task state stays Done.
