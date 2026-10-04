# HANDOFF: T-28J9oR-cache-spec-config-surface

- Task: `T-28J9oR-cache-spec-config-surface`
- State: `Draft` (handoff not yet available)
- From: `developer` (Dev B)
- To: T-QgQy08, T-eyn5UG, T-ZTxN1x, T-6tRKml, T-o95l1M, T-gDNjN2, T-JCOAsq

## What will be handed over
- **`models`:** `TaskSpec.cache`, `WorkflowDefaults.cache`, `ResultCacheRecord`,
  `RunState.result_cache`, `is_current_result_cache_record`, and the `RESULT_CACHE_*`
  constants.
- **`project_config`:** `CacheConfig` (`effective_max_entry_bytes`) and `ProjectConfig.cache`.
- **`cache.settings`:** `ResultCacheSettings`, `resolve_result_cache_settings`,
  `task_cache_policy` and `opted_in_count`.
- **`cli`:** the `--cache/--no-cache` option on `run`/`resume`, and `_build_result_cache`
  (resolution half; returns `None`).
- **`cache.cli`:** `cache_app` with its group callback.

## Frozen names / contracts
- Every name above.
- `ResultCacheRecord` field names, which are written to `state.json`. Renaming one later is a
  state-format change.

## Verification the receiver should run
- `pytest -q tests/cache/test_settings.py tests/cache/test_models_result_cache.py tests/cache/test_project_config_cache.py tests/cache/test_cli_cache_flags.py tests/test_nfr2_regression_gate.py`

## Comments
- By: architect · Role: architect · Date: 2026-10-04 · Comment: Handoff stub created.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2 contents. State `Draft`
  mirrors `TASK.md` and `STATUS.md`.
