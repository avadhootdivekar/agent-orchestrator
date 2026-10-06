# HANDOFF: T-o95l1M-cache-cli-wiring

- Task: `T-o95l1M-cache-cli-wiring`
- State: `Done (handoff available)`
- From: `developer` (Dev B)
- To: T-JCOAsq (e2e), T-nPMuz4 (G0 protocol smoke), T-6tRKml (`ao cache` commands), T-bdQZW4 (docs)

## What was delivered
- `ao run --cache` and `ao resume --cache` end to end: `_build_result_cache` builds
  `ResultCache.from_settings(...)` for mode `on` / `shadow`, prints the one-line stderr banner and
  the factory's warnings, and returns the cache; BOTH `Orchestrator(...)` constructions receive
  `result_cache=` (the same helper's result).
- Summary line after `Total cost:` in `_print_state` (formats `run_block(state)`) and
  `_print_status_snapshot` (formats the snapshot's own `result_cache` run block), via one helper
  `_echo_result_cache_line(block)`; `cache.report` is imported lazily and only for a run with
  records.
- `ao report-usage` text: the hits line when hits > 0 and the shadow line when would_hits > 0.
- Tests: `tests/cache/test_cli_result_cache_wiring.py` (27) and the fixture helper
  `tests/cache/_wiring_fixture.py`.

## Frozen names / contracts
- Banner: `Result cache: <mode> (source=<source>), <n> of <m> static task(s) opted in, at <root>`
  (or `..., but no task opts in (set defaults.cache: true or tasks[].cache: true), at <root>`), on
  stderr, followed by one `WARNING: <text>` per `rc.warnings` entry.
- Summary line (stdout): `Result cache: hits=N (saved ~$X.XXXX est., ~T tokens, ~Ss) would_hits=N
  misses=N stored=N ineligible=N`; `report-usage` lines as HLD 8.8.4.
- `_build_result_cache(cache_flag, workspace, wf)` is the ONE helper for run and resume.

## HLD deviation for the T-bdQZW4 docs refresh (decision: manager)
- **HLD E-2 / 8.7.5 says the CLI path loads only `cache`, `cache.constants`, `cache.settings`.**
  `cli.py` imports `agent_orchestrator.cache.cli` at module scope (the `ao cache` sub-app), so
  every `ao` start also loads `cache.cli`. Decision: keep `cache/cli.py` import-light (module level:
  `typer` only, later `cache.constants` / `cache.settings`; EVERY other cache import, `store`,
  `coordinator`, `report`, ..., lazy inside the command bodies) and treat
  `agent_orchestrator.cache.cli` as allowed on the CLI path. The allow-list for E-2-style module
  checks is `{cache, cache.constants, cache.settings, cache.cli}`
  (`test_cli_result_cache_wiring.py::TestLazyImports` asserts it for a cache-off `ao run`).
  **T-6tRKml must keep that rule** when it adds the commands. T-bdQZW4: amend HLD 8.7.5 / E-2 and
  ADR-0019 accordingly.
- Banner text, nested-repository warning and report lines are unchanged from the HLD.

## Verification the receiver should run
- `pytest -q tests/cache/test_cli_result_cache_wiring.py tests/cache/test_noop_proof.py tests/cache/test_cli_cache_flags.py`

## Comments
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Handoff stub created (Rev 2).
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 3: warnings and lazy
  imports. State `Draft` mirrors `TASK.md` and `STATUS.md`.
- By: developer · Role: developer · Date: 2026-10-05 · Comment: State -> Done; handoff available.
  The `cache.cli` CLI-path allowance is recorded above for the docs refresh.
