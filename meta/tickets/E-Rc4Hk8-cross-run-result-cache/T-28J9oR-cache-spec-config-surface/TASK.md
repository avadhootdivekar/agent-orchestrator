# TASK: T-28J9oR-cache-spec-config-surface

## Metadata
- Task ID: `T-28J9oR-cache-spec-config-surface`
- Epic ID: `E-Rc4Hk8-cross-run-result-cache`
- Owner: `developer` (Dev B)
- Created: `2026-10-04`
- Last Updated: `2026-10-05` (Rev 2)
- Status: `Draft`
- Estimate: `16 focus hours (2 days)` · Sprint 1, Wave 1 (starts when T-FJH6LI commit 1 lands)

## Requirements Mapping
- Requirement IDs: FR-1, FR-2, FR-15, NFR-6, NFR-7
- HLD: §8.1.1–§8.1.4, §8.1.6, §8.1.7 (resolution half only), §8.9 (group help only), §13.1, §13.2
- ADR-0019: D1, D14, D22, D26

## Description
Add the declarative and operator surface of the result cache. Nothing here constructs a cache or
touches the engine.

1. **`models.py`.** Exactly HLD §8.1.1:
   - `StrictBool` added to the pydantic import;
   - the `RESULT_CACHE_*` constants;
   - `TaskSpec.cache: StrictBool | None = None`, after `verdict_path`, with the comment block;
   - `WorkflowDefaults.cache`, after `model`;
   - `class ResultCacheRecord`, with bounds;
   - `RunState.result_cache: dict[str, ResultCacheRecord] = {}`, after `record_git_heads`;
   - `is_current_result_cache_record(rec, ts)`, including the hit `ended_at` binding.
2. **`specs/workflow.schema.json`.** A `cache` boolean property in `defaults` and in
   `$defs.task`, with the description from §8.1.2.
3. **`project_config.py`.**
   - `class CacheConfig` (§8.1.4): bounds, the `_entry_fits_total` validator and
     `effective_max_entry_bytes`.
   - `ProjectConfig.cache`.
   - The `_INIT_TEMPLATE` block, after the isolation block.
4. **`cache/settings.py`** (§8.1.6): `ResultCacheSettings`, `resolve_result_cache_settings`,
   `task_cache_policy` and `opted_in_count`.
5. **`cli.py`, resolution half only.**
   - Add the `--cache/--no-cache` option as the **last** parameter of both `run` and `resume`,
     with the §8.1.7 help text.
   - Add `_build_result_cache(cache_flag, workspace, wf)`. It resolves the settings, echoes each
     warning to stderr as `WARNING: <text>`, and **returns `None`**. Construction is T-o95l1M's
     job.
   - Call it from both `run` and `resume`; its return value is not yet passed to `Orchestrator`.
   - Add `app.add_typer(cache_app, name="cache")`.
6. **`cache/cli.py` skeleton.** `cache_app = typer.Typer(...)`, plus a `@cache_app.callback()`
   whose docstring is the §8.9 group help. With no subcommands yet, `ao cache --help` still
   renders. T-6tRKml adds the commands.

## File scope (exclusive; shared files are additive hunks only)
- `src/agent_orchestrator/models.py` (shared; T-bdQZW4 later adds only a comment)
- `specs/workflow.schema.json`
- `src/agent_orchestrator/project_config.py`
- `src/agent_orchestrator/cli.py`: option, resolution half and `add_typer` only. T-o95l1M adds
  the construction half later.
- `src/agent_orchestrator/cache/settings.py` (new)
- `src/agent_orchestrator/cache/cli.py` (new, skeleton)
- `tests/cache/test_settings.py`, `test_models_result_cache.py`, `test_project_config_cache.py`,
  `test_cli_cache_flags.py` (new)

## Inputs / Outputs
- **Inputs:** T-FJH6LI commit 1 (`constants.py`); HLD §8.1.
- **Outputs:**
  - the spec and config fields;
  - `ResultCacheRecord` and the currency predicate;
  - mode and policy resolution;
  - the CLI flag;
  - the `ao cache` group.

## Acceptance Criteria
1. **U-M1 (spec fields).**
   - `TaskSpec(cache="yes")` and `TaskSpec(cache=1)` raise `ValidationError`.
   - `True`, `False` and `None` are accepted.
   - The same holds for `WorkflowDefaults.cache`.
   - The JSON schema accepts `cache: true` at both levels and rejects `"yes"`. The check is a
     `jsonschema` validation of a minimal spec.
2. **U-M2 (backward compatibility).** A `state.json` produced before this epic loads with
   `result_cache == {}`. The fixture is built by dumping a `RunState` and deleting the key.
3. **U-M3 (currency truth table).** `is_current_result_cache_record`:
   - returns False when `ts` is `None`;
   - returns False on a cycle mismatch;
   - returns True for a current miss, would_hit or ineligible record;
   - for a hit, returns True only when `status == "succeeded"` **and** `rec.ended_at` equals
     `ts.ended_at`;
   - for a hit, returns False when `ended_at` is `None`, when it differs, or when the status is
     `failed`.
4. **U-M4 (bounds and downgrade).**
   - `ResultCacheRecord(saved_cost_usd=float("inf"))` and a 300-character `reason_detail` are
     rejected.
   - A stand-in model containing only the pre-epic required `RunState` fields, with default
     pydantic config, validates a new `state.json` dump that includes `result_cache` (so an older
     `ao` can still load it).
   - `RunState.model_config` has no `extra` override.
5. **U-C1..C4 (`CacheConfig`).**
   - The defaults equal the `constants.DEFAULT_CACHE_*` values.
   - Each of these raises a `ConfigError` or `ValidationError`: `max_bytes: 0`,
     `max_bytes: 2**51`, `ttl_days: 0`, `ttl_days: 40000`, `mode: "sometimes"`, `enabled: "yes"`.
   - `max_bytes: 1000000` alone is valid, and `effective_max_entry_bytes == 1000000`.
   - An explicit `max_entry_bytes` above `max_bytes` is rejected, with the §8.1.4 message.
   - The `ao init` template, with the cache block uncommented, parses into a valid
     `ProjectConfig`.
6. **U-S1 (mode matrix).**
   - Every row of the §8.1.3 precedence table and every §8.1.7 edge case gives the expected
     `(mode, source)`:
     - CLI `None`, `True` or `False`;
     - `AO_CACHE` unset, `""`, `"1"`, `" ON "`, `"0"`, `"off"`, `"shadow"`, `"Refresh"` or
       `"maybe"`;
     - config `None`, `enabled: true` with each mode, `enabled: false`, or
       `mode: shadow` without `enabled`.
   - Exactly one warning is returned, for `"maybe"` only.
7. **U-S2 (author policy).** `task_cache_policy` over task `False`/`True`/`None` × defaults
   `False`/`True`/`None` × `injected`:
   - unset/unset gives **False**;
   - an injected task's `True` with defaults `False` or `None` gives False;
   - an injected task's `False` gives False.
8. **U-S3.** `opted_in_count` returns `(n, m)` over static tasks.
9. **CLI.**
   - `ao run --help` and `ao resume --help` list `--cache / --no-cache`.
   - `ao cache --help` exits 0 and contains "RESULT cache".
   - With `AO_CACHE=maybe`, `ao run` on a fake-executor fixture prints exactly one
     `WARNING: AO_CACHE='maybe' not recognised …` line on stderr, and the run result is
     unchanged.
   - With no flag, env or config, stdout and stderr equal those of the same run without this
     change. Use T-JCOAsq's I-2 golden once available; until then, compare against a capture
     taken before the edit.
10. **Hygiene.**
    - No pre-epic test file is edited, and `tests/test_nfr2_regression_gate.py` passes.
    - ruff and mypy are clean.
    - `pytest -q` has no new failures.

## Test requirements
- `tests/cache/test_models_result_cache.py`: AC-1..AC-4.
- `tests/cache/test_project_config_cache.py`: AC-5.
- `tests/cache/test_settings.py`: AC-6..AC-8.
- `tests/cache/test_cli_cache_flags.py`: AC-9, via `CliRunner`. Use `monkeypatch.chdir(tmp_path)`
  so the repository's own `.ao/config.yaml` is never read.

## Risks
- **E-Ag7Pw3 adds `TaskSpec` or `WorkflowSpec` fields in parallel.** These are additive hunks;
  §24.2 of the HLD has the merge rules.
- **`ao init` template drift.** Mitigation: the template-parses test.
- **A Typer group with no commands.** Mitigation: the callback renders help.

## Dependencies
- T-FJH6LI commit 1 (`constants.py`).

## Pseudocode / Algorithm
```text
See HLD §8.1.6 (resolve_result_cache_settings / task_cache_policy / opted_in_count) verbatim.
cli._build_result_cache (resolution half):
    cfg = _load_project_config_or_exit()
    settings, warnings = resolve_result_cache_settings(cache_flag, os.environ, cfg.cache if cfg else None)
    for w in warnings: typer.echo(f"WARNING: {w}", err=True)
    return None          # T-o95l1M replaces this line with the construction half (§8.1.7)
```

## Schemas / Interface Notes
- **Spec / data schema:** HLD §8.1.2 and §13.1 (spec); §13.2 (config); §8.1.1 and §13.5 (record).
- **Interface:** `ResultCacheSettings`, `resolve_result_cache_settings(cli_flag, environ, cfg)`,
  `task_cache_policy(task, workflow, *, injected)` and `opted_in_count(workflow)`.
- **Triggers / events:** none. Service-triggered runs inherit `AO_CACHE` and the config.

## Handoff Boundary
- **Upstream:** T-FJH6LI.
- **Downstream:**
  - T-QgQy08: models and settings.
  - T-eyn5UG: record model and predicate.
  - T-ZTxN1x: flag.
  - T-6tRKml: `cache/cli.py` skeleton.
  - T-o95l1M: `_build_result_cache`.
  - T-gDNjN2: settings.

## Artifacts
- **Docs/comments:** `meta/tickets/E-Rc4Hk8-cross-run-result-cache/T-28J9oR-cache-spec-config-surface/`
- **Large outputs:** N/A

## Comments
- By: architect · Role: architect · Date: 2026-10-04 · Comment: Surface task: spec, config, flags.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2 changes:
  - **Double opt-in:** an unset author policy now means NOT opted in (reviewer R1).
  - **Modes** `on`, `shadow` and `refresh` in env and config (critic #1/#2).
  - **`StrictBool`** (developer #12).
  - **`CacheConfig`** bounds and clamp (developer #13).
  - **Record** `mode`, `mode_source`, `reason_detail` and `ended_at` binding (reviewer R4).
  - **Ownership.** `cache/__init__.py` moved to T-FJH6LI; the construction half of the CLI
    helper moved to the new T-o95l1M.
