# TASK: T-o95l1M-cache-cli-wiring

## Metadata
- Task ID: `T-o95l1M-cache-cli-wiring`
- Epic ID: `E-Rc4Hk8-cross-run-result-cache`
- Owner: `developer` (Dev B)
- Created: `2026-10-05`
- Last Updated: `2026-10-05`
- Status: `Draft`
- Estimate: `8 focus hours (1 day)` · Sprint 2 (after T-XpF1pF)

## Requirements Mapping
- Requirement IDs: FR-1 (banner), FR-11 (summary line, `report-usage` text line), NFR-1
- HLD: §8.1.7 (construction half), §8.8.3, §8.8.4 (text line)
- ADR-0019: D1, D15, D22

## Description
Complete the operator-facing wiring in `cli.py`:

1. **Construction half of `_build_result_cache`** (HLD §8.1.7):
   - when the mode is not `off`, construct `ResultCache.from_settings(workspace_root=...,
     settings=...)`, with a lazy import;
   - print the stderr banner
     `Result cache: <mode> (source=<source>), <note>, at <root>`. The note is either
     `N of M static task(s) opted in` or
     `but no task opts in (set defaults.cache: true or tasks[].cache: true)`;
   - return the cache.
2. **Pass `result_cache=_build_result_cache(cache, workspace, wf)`** to **both**
   `Orchestrator(...)` constructions, in `run` and in `resume`.
3. **Summary line.** In `_print_state` and `_print_status_snapshot`, after `Total cost:`, print
   `format_summary_line(run_block(state))` when it is not None (HLD §8.8.3).
4. **`ao report-usage` text output.** Add the one-line `Result cache: N hit(s) across scanned
   runs, ~$X avoided (est.; source-run cost incl. retries; not in costs below)` after the header,
   only when hits are greater than 0 (HLD §8.8.4).

## File scope (exclusive)
- `src/agent_orchestrator/cli.py`: the construction half, the `Orchestrator(result_cache=)`
  arguments, the summary lines and the `report-usage` text line. It runs after T-28J9oR's
  resolution half.
- `tests/cache/test_cli_result_cache_wiring.py` (new)

## Inputs / Outputs
- **Inputs:** T-XpF1pF (the engine keyword), T-gDNjN2 (`ResultCache.from_settings`), T-eyn5UG
  (`run_block`, `format_summary_line`, `UsageReport` fields), T-28J9oR (the helper's resolution
  half).
- **Outputs:** the end-to-end usable feature (`ao run --cache`), which is the prerequisite for
  G0 (T-nPMuz4).

## Acceptance Criteria
1. **E-11 (banner).** With `--cache` and an opted-in fake workflow, stderr contains exactly one
   banner line with mode `on`, `source=cli`, `1 of 1 static task(s) opted in` and the cache root.
   - With `AO_CACHE=shadow`, the banner says `shadow (source=env)`.
   - A workflow with no opted-in task gets the "no task opts in" variant.
   - With the mode off, there is no banner.
2. **E-9 (summary line).** After a run with hits, stdout contains the exact §8.8.3 line, and
   `ao status <run>` prints the same line. A run without current records prints no such line.
3. **E-10 (`report-usage`).**
   - The text output includes the `Result cache:` line only when hits are greater than 0.
   - `--json` omits `result_cache_hits` and `result_cache_saved_cost_usd` when they are 0, and
     includes them when hits are greater than 0.
4. **Both entry points.** `ao run` and `ao resume` pass the same helper's result. Spy on
   `Orchestrator.__init__` kwargs in both paths.
5. **No-op when off.** I-2 (T-JCOAsq) still passes: stdout and `status.json` are byte-identical
   to the base golden.
6. **Hygiene.**
   - E2E tests use `CliRunner`, `monkeypatch.chdir(tmp_path)` and `executor: fake`.
   - `ruff` and `mypy` are clean.
   - `pytest -q` has no new failures.

## Test requirements
- `tests/cache/test_cli_result_cache_wiring.py`: AC-1..AC-4.

## Risks
- **Conflicts in `cli.py` with sibling epics.** Hunks are additive; see HLD §24.2.
- **Banner noise.** It is printed only when the mode is not `off`.

## Dependencies
- T-XpF1pF, T-gDNjN2, T-eyn5UG, T-28J9oR.

## Pseudocode / Algorithm
```text
HLD §8.1.7 `_build_result_cache` (full version) verbatim; §8.8.3 / §8.8.4 print sites.
```

## Schemas / Interface Notes
- **CLI:** `ao run|resume [--cache|--no-cache]` (HLD §14.2).
- **Text formats:** HLD §8.8.3 and §8.8.4.

## Handoff Boundary
- **Upstream:** T-XpF1pF, T-gDNjN2, T-eyn5UG, T-28J9oR.
- **Downstream:** T-JCOAsq (E-1…E-6), T-nPMuz4 (G0), T-bdQZW4 (docs).

## Artifacts
- **Docs/comments:** `meta/tickets/E-Rc4Hk8-cross-run-result-cache/T-o95l1M-cache-cli-wiring/`
- **Large outputs:** N/A

## Comments
- By: architect · Role: architect · Date: 2026-10-05 · Comment: New in Rev 2. Split out of
  T-XpF1pF so that the engine task stays within 3 days and `cli.py` has one owner per phase
  (developer #10).
