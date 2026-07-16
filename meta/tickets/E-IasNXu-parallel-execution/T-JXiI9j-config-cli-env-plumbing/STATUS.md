# STATUS

- ID: `T-JXiI9j-config-cli-env-plumbing`
- Updated At: 2026-07-15
- State: Done
- Owner: developer agent

## This update
- By: Claude · Role: developer · Date: 2026-07-15
- Comment: Implemented `max_parallel` end-to-end through the exact `quota_max_wait_seconds`
  three-layer precedence chain (CLI `--max-parallel` > env `AO_MAX_PARALLEL` > `.ao/config.yaml:
  max_parallel` > `DEFAULT_MAX_PARALLEL=1`). Plumbing only — `Orchestrator.__init__` now stores
  `self._max_parallel` (defensively clamped to >= 1) but `run()` is byte-for-byte unchanged and
  never reads it; the engine remains unconditionally serial until `T-j8YLGd`'s wave/barrier
  scheduler consumes it.
- Changes (`src/agent_orchestrator/`):
  1. `models.py`: added `DEFAULT_MAX_PARALLEL = 1`, co-located with `DEFAULT_QUOTA_*` (both
     "invocation-scoped runtime default" constants).
  2. `project_config.py`: added `ProjectConfig.max_parallel: int | None = None` (docstring
     mirrors the quota fields); added a commented `max_parallel:` line to `_INIT_TEMPLATE` under
     "Runtime execution settings", column-aligned with the existing `max_attempts`/`max_turns`
     lines.
  3. `cli.py::_resolve_run_settings`: added `max_parallel: int | None` param, resolved with the
     identical `_int_env` + `or`-chain as quota, appended as a 7th tuple element (return-type
     annotation + docstring updated); added the `< 1` validation guard
     (`typer.echo(..., err=True); raise typer.Exit(1)`).
  4. `cli.py::run` and `cli.py::resume`: added `--max-parallel` `typer.Option`; both unpack sites
     updated for the new 7-tuple arity; `max_parallel=eff_max_parallel` passed into **both**
     `Orchestrator(...)` constructions.
  5. `engine.py::Orchestrator.__init__`: added `max_parallel: int = DEFAULT_MAX_PARALLEL`,
     stores `self._max_parallel = max(1, int(max_parallel))`. `run()` untouched (grep confirms
     no diff inside `run()`'s body).

## Evidence
- Files changed: `src/agent_orchestrator/{models,project_config,cli,engine}.py`,
  `tests/{test_project_config,test_cli,test_engine}.py`. Zero changes under `specs/`
  (`git status --short -- specs` is empty — confirmed, no `*.schema.json` touched, per
  ADR-0007 D5 / NFR-3).
- Tests added (16 new, all passing):
  - `tests/test_project_config.py`: `TestProjectConfigSchema.test_max_parallel_round_trips`,
    `test_max_parallel_absent_defaults_to_none` (AC-4); `TestScaffoldInit.
    test_file_contains_max_parallel_comment` (AC-6).
  - `tests/test_cli.py::TestResolveRunSettingsMaxParallel` (8 tests): none-everywhere->1,
    config-only->4, env-over-config->6, cli-over-both->8 (AC-1); empty-env-is-unset (AC-3);
    CLI `0` falls through to default (no error — see Deviation below); negative CLI/env ->
    `typer.Exit(1)` with the "must be >= 1" message asserted via `capsys` (AC-2).
  - `tests/test_engine.py::TestMaxParallelPlumbing` (5 tests): ctor stores 4 / defaults to 1 /
    clamps 0 and -5 to 1 (AC-5 first half); `test_run_byte_identical_regardless_of_max_parallel`
    — a 4-task diamond DAG run twice (`max_parallel=1` vs `4`) against the same workspace, with
    both `RunStateStore`'s clock and `engine.py`'s direct `datetime.now(UTC)` calls (used for
    `TaskRunState.started_at/ended_at`, not the injectable clock — pre-existing, out of scope)
    frozen via `monkeypatch.setattr(engine_module, "datetime", ...)` (same technique as
    `test_monitoring_self_heal.py`/`test_run_active_seconds_breaker.py`), asserts
    `state_serial.model_dump() == state_parallel.model_dump()` — a true byte-identical
    regression proving `run()` genuinely ignores `self._max_parallel` this task (AC-5).
- Test counts: baseline `uv run pytest -q` = 799 passed, 3 skipped. After this change = **815
  passed, 3 skipped** (+16, zero regressions).
- `uv run ruff check .` — 2 pre-existing errors in `tests/test_e2e_cli.py` (untouched by this
  ticket, unrelated file). Scoped to the 7 files this ticket touched: **all checks passed**.
- `uv run ruff format --check .` — all 7 touched files formatted clean.
- `uv run mypy src` — 4 pre-existing errors in `src/agent_orchestrator/_version.py` (untouched,
  last modified in an unrelated prior commit). Scoped to the 4 touched `src` files: **no issues
  found**.

## Deviations from the ticket pseudocode (flagged, not silently guessed)
1. **`Orchestrator.__init__`'s `max_parallel` default uses `DEFAULT_MAX_PARALLEL` (imported
   constant), not the literal `1`** shown in the ticket's pseudocode line
   (`max_parallel: int = 1`). Every sibling default in that same constructor
   (`quota_max_wait_seconds`, `max_extensions_per_breaker`, etc.) already uses its imported
   `DEFAULT_*` constant rather than a literal — CLAUDE.md's "named constants, not magic
   literals" rule plus "match the existing pattern" both point the same way. Behaviorally
   identical (`DEFAULT_MAX_PARALLEL == 1`); flagged only because it deviates from the literal
   pseudocode text.
2. **`--max-parallel 0` does NOT raise `typer.Exit(1)`** — it silently resolves to
   `DEFAULT_MAX_PARALLEL` (1). TASK.md's AC-2 prose groups `0` with "negative" under "exits 1",
   but the ticket's own executable pseudocode (repeated identically in TASK.md, the HLD §4, and
   this ticket's assignment prompt) is a plain `or`-chain — `0 or DEFAULT_MAX_PARALLEL` is
   mathematically `DEFAULT_MAX_PARALLEL`, so the `< 1` guard can never see a `0`. The
   assignment prompt's own "Hard constraints" section independently confirms this reading
   ("the `or`-chain treats `0` as unset... the explicit `< 1` guard is what rejects a *negative*
   value"). This also matches this codebase's pre-existing precedent: `--quota-max-wait 0` /
   `--max-attempts 0` already silently fall through to their defaults via the identical pattern
   — `max_parallel` is not special-cased. Implemented and tested per the executable pseudocode +
   explicit clarification; flagging the AC-2 prose/pseudocode inconsistency for whoever
   authors/refines `T-TNleFt` (its Handoff Boundary line says CliRunner e2e covers "the
   `--max-parallel 0` error" — that line should be corrected to the negative-value case, or the
   validation semantics revisited, before that ticket is written).

## Risks / Blockers
- None for this task's own scope. Forward risk (unchanged from EPIC.md R1): `T-j8YLGd` must wire
  `self._max_parallel` into an actual wave scheduler without disturbing the `N=1` byte-identical
  guarantee this task's regression test now locks in at the plumbing layer.

## Next actions
1. Handed off to `T-j8YLGd-wave-barrier-scheduler` (consumes `self._max_parallel`; no further
   work needed on this task).
2. Recommend the `T-TNleFt` ticket text be reconciled with the `--max-parallel 0` behavior
   documented above before that ticket starts (see Deviations #2).
