# TASK: T-ZTxN1x-bench-cache-force-off

## Metadata
- Task ID: `T-ZTxN1x-bench-cache-force-off`
- Epic ID: `E-Rc4Hk8-cross-run-result-cache`
- Owner: `developer` (Dev B)
- Created: `2026-10-04`
- Last Updated: `2026-10-05` (Rev 2)
- Status: `Done`
- Estimate: `4 focus hours (0.5 day)` · Sprint 1, Wave 2

## Requirements Mapping
- Requirement IDs: FR-14
- HLD: §8.11
- ADR-0019: D23

## Description
`ao-bench` must always measure real dispatch cost. In `bench/subjects.py`,
`AoWorkflowSubject` (the only subject that shells out to `ao run`) must:

- append the named constant `_AO_NO_CACHE_FLAG = "--no-cache"` to its `ao run` argv;
- set `env[ENV_CACHE] = "0"`, using `ENV_CACHE` from `agent_orchestrator.cache.constants`.

Both are required: the argv flag and the env var together.

## File scope (exclusive)
- `src/agent_orchestrator/bench/subjects.py`
- `tests/bench/test_bench_cache_forced_off.py` (new). `tests/bench/conftest.py` is **not**
  edited.

## Inputs / Outputs
- **Inputs:** T-28J9oR (the `--no-cache` flag exists on `ao run`); T-FJH6LI (`ENV_CACHE`).
- **Outputs:** bench runs that are immune to the result cache.

## Acceptance Criteria
1. **E-8a.** With `subjects._run_with_timeout` monkeypatched to capture `(argv, env)`, an
   `AoWorkflowSubject` run passes `"--no-cache"` in argv and `env["AO_CACHE"] == "0"`.
2. **E-8b.** The same holds when the outer environment sets `AO_CACHE=1` or `AO_CACHE=shadow`.
3. **E-8c.** `CliRunner` shows `ao run --help` lists `--no-cache`, so a real bench run never fails
   with "no such option".
4. **No regressions.** Existing bench tests pass, except the 2 known pre-existing failures. ruff
   and mypy are clean.

## Test requirements
- `tests/bench/test_bench_cache_forced_off.py`: AC-1..AC-3.

## Risks
- **A stale global `ao` without the flag.** The bench uses `uv run ao` from the repo
  (ASSUMPTION A2 in `subjects.py`), so the flag always exists.

## Dependencies
- T-28J9oR, T-FJH6LI (commit 1).

## Pseudocode / Algorithm
```text
HLD §8.11 verbatim.
```

## Schemas / Interface Notes
- N/A (argv and env only).

## Handoff Boundary
- **Upstream:** T-28J9oR.
- **Downstream:** T-JCOAsq, which counts it in the final suite.

## Artifacts
- **Docs/comments:** `meta/tickets/E-Rc4Hk8-cross-run-result-cache/T-ZTxN1x-bench-cache-force-off/`
- **Large outputs:** N/A

## Comments
- By: architect · Role: architect · Date: 2026-10-04 · Comment: Bench forced off.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2: AC-2 also covers
  `AO_CACHE=shadow`. The scope is unchanged.
- By: developer · Role: developer · Date: 2026-10-05 · Comment: State -> Done (commit `15a659d`). All acceptance criteria pass; evidence in `STATUS.md`.
