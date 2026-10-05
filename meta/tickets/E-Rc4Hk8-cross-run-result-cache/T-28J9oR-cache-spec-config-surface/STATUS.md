# STATUS

- ID: `T-28J9oR-cache-spec-config-surface`
- Updated At: `2026-10-05`
- State: `Done`
- Owner: `developer` (Dev B)

## This update
Implemented in commit `458c472` (branch `worktree-agent-a18ce2c08e42a3a5a`): `models.py`,
`specs/workflow.schema.json`, `project_config.py`, `cache/settings.py`, `cache/cli.py`
(skeleton), the CLI option, `_build_result_cache` (resolution half) and `add_typer`, plus four new
test modules and removal of T-FJH6LI's `# type: ignore` in `cache/types.py`. Shared files got
additive hunks only (HLD §24.2); no pre-epic test file was edited; `tests/conftest.py` and
`tests/test_nfr2_regression_gate.py` are untouched. Cache stays OFF by default and the engine is
unchanged. T-QgQy08, T-eyn5UG, T-ZTxN1x, T-6tRKml, T-o95l1M, T-gDNjN2 and T-JCOAsq may start on
this dependency. Frozen names and four small deviations: `HANDOFF.md`.

## Acceptance criteria
| AC | Result | Evidence |
|----|--------|----------|
| 1 U-M1 | PASS | `test_models_result_cache.py::TestSpecFields`: `"yes"`, `1`, `0`, `"true"`, `[]` rejected at both levels; `True/False/None` accepted; `jsonschema` accepts `cache: true` at both levels and rejects `"yes"` at both |
| 2 U-M2 | PASS | `TestBackwardCompat`: dump a `RunState`, delete `result_cache`, reload -> `{}` |
| 3 U-M3 | PASS | `TestCurrency`: `ts None`, cycle mismatch -> False; miss / would_hit / ineligible current -> True; hit True only with `succeeded` and `rec.ended_at == ts.ended_at` (failed, other `ended_at`, `None` -> False) |
| 4 U-M4 | PASS | `TestBounds`: `inf`, NaN, 300-char `reason_detail`, bad key rejected; stand-in old `RunState` (5 required fields) validates a new dump; no `extra` in `RunState.model_config` |
| 5 U-M5 | PASS | `TestDerivedFields`: derived `hit`/`saved_tokens`; forged JSON (`hit: true`, `outcome: miss`, wrong `saved_tokens`) re-derived; `model_dump()` has both; state JSON round-trip |
| 6 U-C1..C4 | PASS | `test_project_config_cache.py`: defaults equal `DEFAULT_CACHE_*`; 12 rejected inputs (incl. `max_bytes: 0`/`2**51`, `ttl_days: 0`/`40000`, `mode: refresh`/`sometimes`, `enabled: "yes"`); `max_bytes: 1000000` alone valid with `effective_max_entry_bytes == 1000000`; entry > total rejected with the §8.1.4 message; uncommented `ao init` cache block parses into a valid `ProjectConfig`; whole template still loads; `mode: refresh` / `ttl_days: 0` in a file -> `ConfigError` |
| 7 U-S1 | PASS | `test_settings.py::TestModeMatrix`: CLI `None/True/False`; `AO_CACHE` unset, `""`, whitespace, `1`, `" ON "`, `0`, `off`, `shadow`, `" Shadow "`, `refresh`, `maybe`, `2` (exactly one warning each for the unknown values; they fail closed even with `enabled: true` config); config `None`, `enabled: true` with each mode, `enabled: false`, `mode: shadow` without `enabled`; limits from config/defaults |
| 8 U-S2 | PASS | all 9 task x defaults combinations; unset/unset -> False; injected `True` with defaults `False`/`None` -> False; injected `True` falls back to `defaults.cache: true`; injected `False` narrows |
| 9 U-S3 | PASS | `opted_in_count` -> `(n, m)` over static tasks |
| 10 U-S4 | PASS | `constants.DEFAULT_TASK_CACHE_POLICY is False` (message names ADR-0019 D1); unset levels return that constant; monkeypatching it to `True` flips `task_cache_policy` and `opted_in_count`; explicit `False` still wins |
| 11 CLI | PASS | `test_cli_cache_flags.py` via `CliRunner`: `run --help`/`resume --help` list `--cache`/`--no-cache`; `cache` is the last parameter of both; `ao cache --help` exits 0 and contains "RESULT cache"; `AO_CACHE=maybe` -> exactly one `WARNING: AO_CACHE='maybe' not recognised (use 1|0|shadow); result cache OFF` on stderr, run succeeds (also `refresh`, and on `resume`); with no flag/env/config the output has no cache line and `.orchestrator/cache` is not created; every mode (`--cache`, `--no-cache`, `AO_CACHE=1/shadow/0`) gives output identical to the default run. **Capture before the edit:** a script ran the same fixture against `git archive HEAD` (pre-change `src`) and the changed tree, normalising the workspace path, run-id timestamp and log `ts`: `diff` -> identical (exit code 0 in both) |
| 12 hygiene | PASS | see Evidence; no pre-epic test edited; NFR-2 gate passes; no line over 100 columns |

## Evidence
- `.venv/bin/python -m pytest -q tests/cache` -> **426 passed** (246 from T-FJH6LI, 123 for this
  task, 57 for T-QgQy08).
- `.venv/bin/python -m pytest -q tests/cache/test_models_result_cache.py tests/cache/test_project_config_cache.py tests/cache/test_settings.py tests/cache/test_cli_cache_flags.py tests/test_nfr2_regression_gate.py` -> all passed (NFR-2 gate: 7 passed).
- `.venv/bin/ruff check src tests` -> All checks passed.
- `.venv/bin/ruff format --check src tests` -> only the pre-existing generated
  `src/agent_orchestrator/_build_info.py` would be reformatted.
- `.venv/bin/mypy src tests/cache` -> only the 4 pre-existing `_version.py` errors.
- `awk 'length>100'` over `src/agent_orchestrator/cache/*.py tests/cache/*.py` -> 0 lines.
- **Full suite** (touches `models.py`, the schema, `project_config.py`, `cli.py`):
  `.venv/bin/python -m pytest -q -p no:cacheprovider` -> **first run: 1 failed, 5525 passed, 10 skipped**
  (`tests/test_spawn_provenance.py::TestNoBehaviorChange::test_origin_has_no_behavioral_reader_outside_ui_runs`:
  the T-FJH6LI comment `# state.tasks[tid].origin == "injected"` on `LookupRequest.injected` in
  `cache/types.py` matches that test's `.origin ==` grep over `src/`; a latent T-FJH6LI regression
  that its own run never saw). Comment reworded in commit `a6a6a70`. **Re-run after the fix:
  `.venv/bin/python -m pytest -q -rs -p no:cacheprovider` -> 5583 passed, 10 skipped, 0 failed**
  in 652 s (the 2 known bench failures did not occur in this run). The 5583 include the 57
  T-QgQy08 tests. Versus the delivery brief's baseline of 5159 passed / 8 skipped: +424 passed
  (this branch adds 246 + 123 + 57 = 426 `tests/cache` tests; the baseline's exact composition was
  not re-derived). The 10 skips are all environment gates (`real_llm` x4, `swebench` x3,
  `playwright` not installed x2, one isolation layout skip) and none is in `tests/cache`; the 2
  extra skips versus 8 are most likely the two `tests/ui/test_e2e_graph.py` playwright skips in
  this worktree's venv (not verified against a baseline run).

## Risks / Blockers
- No blockers.
- Risk: concurrent `TaskSpec` edits by E-Ag7Pw3 (merge-time; HLD §24.2). Additive hunks only.
- Heads-up for T-gDNjN2 / T-XpF1pF: `tests/test_spawn_provenance.py` forbids any `.origin ==`,
  `.origin !=` or `.origin in` text anywhere under `src/` outside `ui/runs.py` (comments
  included). Derive `LookupRequest.injected` without those spellings (e.g. a membership test on
  `state.spawned_by`, or `origin` read through a helper) or the engine seam fails that test.
- Risk: `model_copy(update=...)` skips validators; documented on `ResultCacheRecord` (the engine
  only updates `ended_at`, `stored` and `store_reason` through it).
- Note: the HLD §8.1.4 template shows `mode: on`, which YAML 1.1 reads as boolean `true`; the
  shipped template quotes it (HANDOFF deviation 2). T-bdQZW4 should correct the HLD text.

## Next actions
1. T-o95l1M completes `_build_result_cache` (construction half) and passes `result_cache=` to
   both `Orchestrator(...)` constructions.
2. T-6tRKml adds the `ao cache` commands to `cache_app`.

## Comments
- By: architect · Role: architect · Date: 2026-10-04 · Comment: Status initialized (Draft).
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2 re-plan after the Phase-4
  consultation.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 3 re-plan after the
  early-gate review and the manager's scope decisions (HLD §23.5).
- By: developer · Role: developer · Date: 2026-10-05 · Comment: Implemented as commit `458c472` (plus the one-line comment fix `a6a6a70`);
  State -> Done. All 12 acceptance criteria pass. Deviations (literal `"on"` default for the
  `Literal` field; quoted `mode: "on"` in the init template; `from . import constants` in
  `settings.py`; one wrapped template comment) are listed in `HANDOFF.md`. This file, `TASK.md`,
  `HANDOFF.md` and the epic rollup agree.
