# TASK: T-28J9oR-cache-spec-config-surface

## Metadata
- Task ID: `T-28J9oR-cache-spec-config-surface`
- Epic ID: `E-Rc4Hk8-cross-run-result-cache`
- Owner: `developer` (Dev B)
- Created: `2026-10-04`
- Last Updated: `2026-10-05` (Rev 3)
- Status: `Draft`
- Estimate: `16 focus hours (2 days)` · Sprint 1 (starts when T-FJH6LI commit 1 lands)

## Requirements Mapping
- Requirement IDs: FR-1, FR-2, FR-11 (record fields), FR-15, NFR-6, NFR-7
- HLD: §8.1.1–§8.1.4, §8.1.6, §8.1.7 (resolution half only), §8.9 (group help only), §13.1, §13.2
- ADR-0019: D1, D14, D22, D26, D35

## Description
Add the declarative and operator surface of the result cache. Nothing here constructs a cache or
touches the engine.

1. **`models.py`.** Exactly HLD §8.1.1 (copy-ready):
   - `StrictBool` and `model_validator` added to the pydantic import;
   - the `RESULT_CACHE_*` constants;
   - `TaskSpec.cache: StrictBool | None = None` after `verdict_path`, with the comment block;
   - `WorkflowDefaults.cache` after `model`;
   - `class ResultCacheRecord` with bounds and the **derived fields** `hit` and `saved_tokens`
     (recomputed by `_derive_brief_fields`, never trusted from input; ADR-0019 D35);
   - `RunState.result_cache: dict[str, ResultCacheRecord] = {}` after `record_git_heads`;
   - `is_current_result_cache_record(rec, ts)` with the hit `ended_at` binding.

   `models.py` imports nothing from `cache/`.
2. **`specs/workflow.schema.json`.** A `cache` boolean property in `defaults` and in `$defs.task`.
3. **`project_config.py`.** `CacheConfig` (HLD §8.1.4, copy-ready; `mode: Literal["on",
   "shadow"]`), `ProjectConfig.cache`, and the `_INIT_TEMPLATE` block.
4. **`cache/settings.py`** (HLD §8.1.6): `ResultCacheSettings`, `resolve_result_cache_settings`
   (modes on/off/shadow; any other `AO_CACHE` value, including `refresh`, → off plus one
   warning), `task_cache_policy` (falls back to **`constants.DEFAULT_TASK_CACHE_POLICY`**, the
   single flip point; no literal `False`), and `opted_in_count`.
5. **`cli.py`, resolution half only.** The `--cache/--no-cache` option as the **last** parameter
   of `run` and `resume`; `_build_result_cache(cache_flag, workspace, wf)` that resolves the
   settings, echoes each warning as `WARNING: <text>` and **returns `None`** (T-o95l1M adds the
   construction half); both commands call it; `app.add_typer(cache_app, name="cache")`.
6. **`cache/cli.py` skeleton.** `cache_app = typer.Typer(...)` plus a `@cache_app.callback()`
   whose docstring is the §8.9 group help, so `ao cache --help` renders before T-6tRKml adds
   commands.

## File scope (exclusive; shared files are additive hunks only)
- `src/agent_orchestrator/models.py` (shared; T-bdQZW4 later adds only a comment)
- `specs/workflow.schema.json`
- `src/agent_orchestrator/project_config.py`
- `src/agent_orchestrator/cli.py`: option, resolution half and `add_typer` only
- `src/agent_orchestrator/cache/settings.py` (new), `src/agent_orchestrator/cache/cli.py` (new, skeleton)
- `tests/cache/test_settings.py`, `test_models_result_cache.py`, `test_project_config_cache.py`,
  `test_cli_cache_flags.py` (new)

## Inputs / Outputs
- **Inputs:** T-FJH6LI commit 1 (`constants.py`); HLD §8.1.
- **Outputs:** spec and config fields; `ResultCacheRecord` and the currency predicate; mode and
  policy resolution; the CLI flag; the `ao cache` group.

## Acceptance Criteria
1. **U-M1 (spec fields).** `TaskSpec(cache="yes")` and `TaskSpec(cache=1)` raise
   `ValidationError`; `True`, `False` and `None` are accepted; the same for
   `WorkflowDefaults.cache`. The JSON schema accepts `cache: true` at both levels and rejects
   `"yes"` (`jsonschema` validation of a minimal spec).
2. **U-M2 (backward compatibility).** A `state.json` produced before this epic (built by dumping a
   `RunState` and deleting the key) loads with `result_cache == {}`.
3. **U-M3 (currency truth table).** False for `ts is None` and for a cycle mismatch; True for a
   current miss, would_hit or ineligible record; for a hit, True only when
   `status == "succeeded"` **and** `rec.ended_at == ts.ended_at`.
4. **U-M4 (bounds and downgrade).** `saved_cost_usd=float("inf")` and a 300-character
   `reason_detail` are rejected. A stand-in model with only the pre-epic required `RunState`
   fields validates a new `state.json` dump; `RunState.model_config` has no `extra` override.
5. **U-M5 (derived fields, D35).** `hit == (outcome == "hit")` and
   `saved_tokens == saved_input_tokens + saved_output_tokens` for constructed records; a record
   loaded from JSON with `"hit": true, "outcome": "miss"` and a wrong `saved_tokens` comes back
   with the derived values; `model_dump()` contains `hit` and `saved_tokens`.
6. **U-C1..C4 (`CacheConfig`).** Defaults equal the `constants.DEFAULT_CACHE_*` values. Each of
   these is rejected: `max_bytes: 0`, `max_bytes: 2**51`, `ttl_days: 0`, `ttl_days: 40000`,
   `mode: "refresh"`, `mode: "sometimes"`, `enabled: "yes"`. `max_bytes: 1000000` alone is valid
   and `effective_max_entry_bytes == 1000000`. An explicit `max_entry_bytes` above `max_bytes` is
   rejected with the §8.1.4 message. The `ao init` template, with the cache block uncommented,
   parses into a valid `ProjectConfig`.
7. **U-S1 (mode matrix).** Every row of §8.1.3 and every §8.1.7 edge case: CLI `None`/`True`/
   `False`; `AO_CACHE` unset, `""`, `"1"`, `" ON "`, `"0"`, `"off"`, `"shadow"`, `" Shadow "`,
   `"refresh"`, `"maybe"`; config `None`, `enabled: true` with each mode, `enabled: false`,
   `mode: shadow` without `enabled`. Exactly one warning each for `"refresh"` and `"maybe"`.
8. **U-S2 (author policy).** Task `False`/`True`/`None` × defaults `False`/`True`/`None` ×
   `injected`: unset/unset → False; an injected task's `True` with defaults `False` or `None` →
   False.
9. **U-S3.** `opted_in_count` returns `(n, m)` over static tasks.
10. **U-S4 (flip-point pin).** `constants.DEFAULT_TASK_CACHE_POLICY is False`; with both levels
    unset, `task_cache_policy` returns that constant, and monkeypatching the constant to `True`
    flips the result. The failure message names ADR-0019 D1.
11. **CLI.** `ao run --help` and `ao resume --help` list `--cache / --no-cache`; `ao cache --help`
    exits 0 and contains "RESULT cache". With `AO_CACHE=maybe`, `ao run` on a fake-executor
    fixture prints exactly one `WARNING: AO_CACHE='maybe' not recognised …` line on stderr and
    the run result is unchanged. With no flag, env or config, stdout and stderr equal those of the
    same run without this change (T-JCOAsq's I-2 golden once available; until then a capture
    taken before the edit).
12. **Hygiene.** No pre-epic test file is edited; `tests/test_nfr2_regression_gate.py` passes;
    ruff (every line ≤ 100 columns) and mypy are clean; `pytest -q` has no new failures.

## Test requirements
- `tests/cache/test_models_result_cache.py`: AC-1..AC-5.
- `tests/cache/test_project_config_cache.py`: AC-6.
- `tests/cache/test_settings.py`: AC-7..AC-10.
- `tests/cache/test_cli_cache_flags.py`: AC-11, via `CliRunner` with `monkeypatch.chdir(tmp_path)`.

## Risks
- **E-Ag7Pw3 adds `TaskSpec`/`WorkflowSpec` fields in parallel.** Additive hunks; HLD §24.2.
- **`model_copy(update=...)` skips validators.** Documented on the model; the engine only updates
  `ended_at`, `stored` and `store_reason` through it.

## Dependencies
- T-FJH6LI commit 1 (`constants.py`).

## Pseudocode / Algorithm
```text
HLD §8.1.1 / §8.1.4 code blocks verbatim (copy-ready); §8.1.6 pseudocode;
cli._build_result_cache (resolution half): resolve -> echo warnings -> return None
```

## Schemas / Interface Notes
- **Spec / data schema:** HLD §8.1.2 and §13.1 (spec); §13.2 (config); §8.1.1 and §13.5 (record).
- **Interface:** `ResultCacheSettings`, `resolve_result_cache_settings(cli_flag, environ, cfg)`,
  `task_cache_policy(task, workflow, *, injected)`, `opted_in_count(workflow)`.

## Handoff Boundary
- **Upstream:** T-FJH6LI (commit 1).
- **Downstream:** T-QgQy08, T-eyn5UG, T-ZTxN1x, T-6tRKml, T-o95l1M, T-gDNjN2, T-JCOAsq.

## Artifacts
- **Docs/comments:** `meta/tickets/E-Rc4Hk8-cross-run-result-cache/T-28J9oR-cache-spec-config-surface/`
- **Large outputs:** N/A

## Comments
- By: architect · Role: architect · Date: 2026-10-04 · Comment: Surface task: spec, config, flags.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2: double opt-in, modes,
  `StrictBool`, config bounds, record binding fields.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 3 (manager B): `refresh`
  removed (unknown value → off + warning; config rejects it); `DEFAULT_TASK_CACHE_POLICY` is the
  only default and is pinned by U-S4; `ResultCacheRecord` gains the derived `hit` and
  `saved_tokens` fields (D35).
