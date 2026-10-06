# TASK: T-o95l1M-cache-cli-wiring

## Metadata
- Task ID: `T-o95l1M-cache-cli-wiring`
- Epic ID: `E-Rc4Hk8-cross-run-result-cache`
- Owner: `developer` (Dev B)
- Created: `2026-10-05`
- Last Updated: `2026-10-05` (Rev 3)
- Status: `Done`
- Estimate: `8 focus hours (1 day)` · Sprint 3 (after T-XpF1pF) · engine set

## Requirements Mapping
- Requirement IDs: FR-1 (banner), FR-11 (summary line, `report-usage` text), NFR-1
- HLD: §8.1.7 (construction half), §8.8.3, §8.8.4 (text lines)
- ADR-0019: D1, D9, D15, D22

## Description
Complete the operator-facing wiring in `cli.py`:

1. **Construction half of `_build_result_cache`** (HLD §8.1.7, copy-ready): when the mode is not
   `off`, `ResultCache.from_settings(...)` (lazy import); print the stderr banner
   `Result cache: <mode> (source=<source>), <note>, at <root>`; then print each entry of
   `rc.warnings` as `WARNING: <text>` (the nested-repository warning, A-12); return the cache.
2. **Pass `result_cache=_build_result_cache(cache, workspace, wf)`** to both `Orchestrator(...)`
   constructions (`run` and `resume`).
3. **Summary line.** In `_print_state` and `_print_status_snapshot`, after `Total cost:`: **only
   when `state.result_cache` is non-empty**, import `cache.report` lazily and print
   `format_summary_line(run_block(state))` when it is not None.
4. **`ao report-usage` text output.** When the cross-run `result_cache` object is present: the
   hits line, and, when `would_hits > 0`, the shadow line (HLD §8.8.4).

## File scope (exclusive)
- `src/agent_orchestrator/cli.py`: the construction half, the `Orchestrator(result_cache=)`
  arguments, the summary lines and the `report-usage` lines (after T-28J9oR's resolution half).
- `tests/cache/test_cli_result_cache_wiring.py` (new)

## Inputs / Outputs
- **Inputs:** T-XpF1pF (engine keyword), T-gDNjN2 (`ResultCache.from_settings`, `warnings`),
  T-eyn5UG (`run_block`, `format_summary_line`, `UsageReport.result_cache`), T-28J9oR.
- **Outputs:** the feature usable end to end (`ao run --cache`, `AO_CACHE=shadow`); the
  prerequisite of the G0 protocol smoke validation (T-nPMuz4).

## Acceptance Criteria
1. **E-11 (banner).** With `--cache` and an opted-in fake workflow, stderr has exactly one banner
   line (`on`, `source=cli`, `1 of 1 static task(s) opted in`, the root). `AO_CACHE=shadow` gives
   `shadow (source=env)`. A workflow with no opted-in task gets the "no task opts in" variant. A
   workspace below a directory holding `.git` gets the nested-repository WARNING. Mode off: no
   banner.
2. **E-9 (summary line).** After a run with hits, stdout contains the exact §8.8.3 line and
   `ao status <run>` prints the same; a run without current records prints no such line. The
   test's dispatch wrapper on `FakeExecutor.execute` sets `cost_usd` and tokens on successful
   results, because `FakeExecutor` reports no cost.
3. **E-10 (`report-usage`).** The text output includes the hits line only when hits > 0 and the
   shadow line only when would_hits > 0; `--json` omits `result_cache` when no scanned run has
   records and includes the §13.6 object otherwise.
4. **Both entry points.** `ao run` and `ao resume` pass the same helper's result (spy on
   `Orchestrator.__init__` kwargs in both paths).
5. **U-LZ2 (CLI part).** In a subprocess, `_print_state` / `_print_status_snapshot` on a state
   with `result_cache == {}` leave `agent_orchestrator.cache.report` out of `sys.modules`.
6. **No-op when off.** I-2 (T-JCOAsq) still passes: stdout and `status.json` are byte-identical
   to the base golden.
7. **Hygiene.** E2E tests use `CliRunner`, `monkeypatch.chdir(tmp_path)` and `executor: fake`;
   ruff (≤ 100 columns) and mypy are clean; `pytest -q` has no new failures.

## Test requirements
- `tests/cache/test_cli_result_cache_wiring.py`: AC-1..AC-5.

## Risks
- **Conflicts in `cli.py` with sibling epics.** Additive hunks; HLD §24.2.

## Dependencies
- T-XpF1pF, T-gDNjN2, T-eyn5UG, T-28J9oR.

## Pseudocode / Algorithm
```text
HLD §8.1.7 `_build_result_cache` (full version) verbatim; §8.8.3 / §8.8.4 print sites.
```

## Schemas / Interface Notes
- **CLI:** `ao run|resume [--cache|--no-cache]` (HLD §14.2). **Text formats:** HLD §8.8.3, §8.8.4.

## Handoff Boundary
- **Upstream:** T-XpF1pF, T-gDNjN2, T-eyn5UG, T-28J9oR.
- **Downstream:** T-JCOAsq (E-1…E-6), T-nPMuz4, T-bdQZW4.

## Artifacts
- **Docs/comments:** `meta/tickets/E-Rc4Hk8-cross-run-result-cache/T-o95l1M-cache-cli-wiring/`
- **Large outputs:** N/A

## Comments
- By: architect · Role: architect · Date: 2026-10-05 · Comment: New in Rev 2 (split out of
  T-XpF1pF; developer #10).
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 3 (early-gate C; manager B):
  banner prints the factory's nested-repository warnings; summary and usage lines import
  `cache.report` lazily (U-LZ2); `report-usage` prints a shadow line; the e2e wrapper supplies
  cost.
- By: developer · Role: developer · Date: 2026-10-05 · Comment: State -> Done (commit `e882b31`). All acceptance criteria pass; evidence, deviations and the `cache.cli` CLI-path decision in `STATUS.md` / `HANDOFF.md`.
