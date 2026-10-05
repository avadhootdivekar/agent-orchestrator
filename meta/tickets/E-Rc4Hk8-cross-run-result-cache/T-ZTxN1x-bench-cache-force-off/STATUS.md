# STATUS

- ID: `T-ZTxN1x-bench-cache-force-off`
- Updated At: `2026-10-05`
- State: `Done`
- Owner: `developer` (Dev B)

## This update
Implemented in commit `15a659d`: `src/agent_orchestrator/bench/subjects.py` (+9 lines, additive) and
`tests/bench/test_bench_cache_forced_off.py` (7 tests): `AoWorkflowSubject` appends
`_AO_NO_CACHE_FLAG = "--no-cache"` to its `ao run` argv and sets `env[ENV_CACHE] = "0"`
(`ENV_CACHE` from `agent_orchestrator.cache.constants`). `tests/bench/conftest.py` is not edited.

## Acceptance criteria
| AC | Result | Evidence |
|----|--------|----------|
| 1 E-8a | PASS | `_run_with_timeout` monkeypatched to capture `(argv, env)`: argv starts `uv run ao run`, contains `--no-cache`; `env["AO_CACHE"] == "0"` (with `AO_CACHE` unset in the outer environment) |
| 2 E-8b | PASS | parametrized over outer `AO_CACHE` = `1`, `shadow`, `on`, `true`: argv still has `--no-cache` and `env["AO_CACHE"] == "0"` |
| 3 E-8c | PASS | `CliRunner` `ao run --help` lists `--no-cache`, so a real bench run never fails with "no such option" |
| 4 No regressions | PASS | all of `tests/bench` passes (417 passed, 4 skipped; the two failures noted in the ticket did not occur in this tree); ruff and mypy clean |

## Evidence
- `.venv/bin/python -m pytest -q -p no:cacheprovider tests/bench/test_bench_cache_forced_off.py` -> **7 passed**.
- `.venv/bin/python -m pytest -q -p no:cacheprovider tests/bench` -> **417 passed, 4 skipped**.
- Full suite `.venv/bin/python -m pytest -q -p no:cacheprovider` (after T-eyn5UG, T-o95l1M and this task) -> **6333 passed, 10 skipped, 0 failed** (655 s); last known 6249 passed / 10 skipped / 1 failed (the load-dependent `test_wave_scheduler.py` flake did not recur).
- `.venv/bin/ruff check src tests`, `.venv/bin/ruff format --check src tests` and `.venv/bin/mypy src tests/cache` -> clean (only the 4 pre-existing `_version.py` mypy errors; the generated `_build_info.py` is the only format exception).

## Deviations from the HLD block (reason)
1. **`_AO_CACHE_OFF_VALUE = "0"`** is a second named constant next to `_AO_NO_CACHE_FLAG`, so the env value is not a bare literal (NFR-7); `ENV_CACHE` is the HLD's constant.
2. **The test stubs a run that produces no run directory** and suppresses the resulting `SubjectError` (raised after the spawn): only the captured spawn arguments matter here, and the existing `test_subjects.py` tests already cover the success path.

## Risks / Blockers
- None. A stale global `ao` without the flag is not a risk: the bench invokes `uv run ao` from the repo (ASSUMPTION A2 in `subjects.py`).

## Next actions
1. T-JCOAsq Part 3 counts this test in the final suite.
2. T-bdQZW4 mentions the forced-off behaviour in the benchmarking docs.

## Comments
- By: architect · Role: architect · Date: 2026-10-04 · Comment: Status initialized (Draft).
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2 re-plan after the Phase-4
  consultation. State stays `Draft` (Sprint 1, Wave 2); this file, `TASK.md`, `HANDOFF.md` (when
  present) and the epic `STATUS.md` rollup agree.
- By: developer · Role: developer · Date: 2026-10-05 · Comment: State -> Done (commit `15a659d`). All acceptance
  criteria pass; deviations 1-2 above are small and documented. Epic `EPIC.md` / `STATUS.md`
  rollup updated to match.
