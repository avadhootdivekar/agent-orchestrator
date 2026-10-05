# HANDOFF: T-28J9oR-cache-spec-config-surface

- Task: `T-28J9oR-cache-spec-config-surface`
- State: `Done (handoff available)`
- From: `developer` (Dev B)
- To: T-QgQy08, T-eyn5UG, T-ZTxN1x, T-6tRKml, T-o95l1M, T-gDNjN2, T-JCOAsq
- Commit: `458c472` on branch `worktree-agent-a18ce2c08e42a3a5a`.

## What was delivered
- **`models`:** `TaskSpec.cache` and `WorkflowDefaults.cache` (`StrictBool | None`),
  `ResultCacheRecord` (derived `hit` and `saved_tokens`, recomputed by `_derive_brief_fields`),
  `RunState.result_cache` (default `{}`), `is_current_result_cache_record`, and the
  `RESULT_CACHE_*` constants. `models.py` imports nothing from `cache/`.
- **`specs/workflow.schema.json`:** `cache` boolean in `defaults` and `$defs.task`.
- **`project_config`:** `CacheConfig` (`mode` on/shadow; `effective_max_entry_bytes`),
  `ProjectConfig.cache`, and the `_INIT_TEMPLATE` block.
- **`cache.settings`:** `ResultCacheSettings`, `resolve_result_cache_settings`,
  `task_cache_policy` (uses `constants.DEFAULT_TASK_CACHE_POLICY`, read through the module so
  U-S4 can monkeypatch it) and `opted_in_count`. Imports `project_config.CacheConfig` only
  lazily/under `TYPE_CHECKING`.
- **`cli`:** `--cache/--no-cache` as the last parameter of `run` and `resume`;
  `_build_result_cache(cache_flag, workspace, wf)` (resolution half: resolves, echoes
  `WARNING: <text>` per warning, returns `None` for every mode); both commands call it as a bare
  statement (T-o95l1M must bind the result and pass `result_cache=` to both `Orchestrator(...)`
  constructions); `app.add_typer(cache_app, name="cache")`.
- **`cache.cli`:** `cache_app` with a group callback whose docstring is the §8.9 group help.
- `cache/types.py`: the `# type: ignore[attr-defined,unused-ignore]` on the `TYPE_CHECKING`
  `models` import is removed (T-FJH6LI's follow-up).

## Frozen names / contracts
- Every name above. `ResultCacheRecord` field names are written to `state.json`; renaming one is
  a state-format change.
- Mode resolution: CLI flag > `AO_CACHE` (1/true/yes/on, 0/false/no/off, shadow; empty = next
  layer; anything else = off + exactly one warning) > `cache.enabled`/`cache.mode` > off.
- T-o95l1M completes `_build_result_cache`: replace the final `return None` with
  `ResultCache.from_settings(...)`, the `opted_in_count` banner and the `rc.warnings` echo (HLD
  §8.1.7). Add `opted_in_count` to the lazy import then.

## Deviations from the HLD code blocks (reason)
1. **`CacheConfig.mode` default is the literal `"on"`**, not `constants.MODE_ON`: mypy cannot
   narrow a plain `str` constant to `Literal["on", "shadow"]` and `constants.py` may import only
   `re` (so it cannot be `Final`). `test_project_config_cache.py` pins `"on" == constants.MODE_ON`.
2. **`ao init` template line `mode: "on"` is quoted** (HLD §8.1.4 has `mode: on`). Under YAML 1.1
   a bare `on` loads as boolean `true`, so the uncommented template failed `CacheConfig`
   validation (`Input should be 'on' or 'shadow'`), which acceptance test U-C catches. A comment
   says why. A user who writes bare `mode: on` gets a loud `ConfigError` (fail-closed). The HLD
   template should be updated by T-bdQZW4.
3. **`cache/settings.py` uses `from . import constants` and `constants.X`** instead of name
   imports, so that U-S4's `monkeypatch.setattr(constants, "DEFAULT_TASK_CACHE_POLICY", True)`
   really flips the result (a name import would capture the old value).
4. The `ao init` template's `max_entry_bytes` comment is wrapped onto two lines to respect the
   100-column limit.

## Verification the receiver should run
- `pytest -q tests/cache/test_settings.py tests/cache/test_models_result_cache.py tests/cache/test_project_config_cache.py tests/cache/test_cli_cache_flags.py tests/test_nfr2_regression_gate.py`

## Comments
- By: architect · Role: architect · Date: 2026-10-04 · Comment: Handoff stub created.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2 contents.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 3: no `refresh`, named policy
  default, derived record fields.
- By: developer · Role: developer · Date: 2026-10-05 · Comment: State -> Done; handoff available
  (commit `458c472`). Deviations 1-4 above are small and documented; the frozen names are unchanged.
