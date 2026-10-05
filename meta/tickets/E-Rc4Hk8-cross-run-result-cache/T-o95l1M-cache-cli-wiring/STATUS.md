# STATUS

- ID: `T-o95l1M-cache-cli-wiring`
- Updated At: `2026-10-05`
- State: `Done`
- Owner: `developer` (Dev B)

## This update
`cli.py` (additive, +59/-11): `_build_result_cache` construction half (lazy `ResultCache.from_settings`,
banner, `rc.warnings`), `result_cache=` on BOTH `Orchestrator(...)` constructions, the summary line
in `_print_state` / `_print_status_snapshot` through one helper `_echo_result_cache_line`, and the
two `report-usage` text lines. 27 new tests in `tests/cache/test_cli_result_cache_wiring.py`
(`CliRunner`, `executor: fake`). Two superseded T-28J9oR tests were narrowed (see Deviations).

## Acceptance criteria
| AC | Result | Evidence |
|----|--------|----------|
| 1 E-11 banner | PASS | `--cache`: exactly one stderr banner `Result cache: on (source=cli), 1 of 1 static task(s) opted in, at <ws>/.orchestrator/cache`; `AO_CACHE=shadow` -> `shadow (source=env)`; config `cache.enabled` -> `source=config`; no opted-in task -> the "but no task opts in" variant; a `.git` above the workspace -> one `WARNING: workspace ... is inside the git repository at ...` printed AFTER the banner; mode off (default, `--no-cache`, `AO_CACHE=0`) -> no banner, no `.orchestrator/cache` directory. Precedence: `--no-cache` beats env and config, `--cache` beats `AO_CACHE=shadow`, env beats config |
| 2 E-9 summary | PASS | two real `ao run --cache` runs (cost wrapper on `FakeExecutor.execute`: 0.75 USD, 1200/340 tokens): run 1 prints `hits=0 ... misses=1 stored=1`; run 2 prints `Result cache: hits=1 (saved ~$0.7500 est., ~1540 tokens, ~Ns) would_hits=0 misses=0 stored=0 ineligible=0` after `Total cost:`; `ao status --run-id` prints the identical line; a cache-off run prints none; `status.json` carries the brief fields (`hit`, `key`, `saved_cost_usd`, `saved_tokens`) and `usage_totals.cost_usd == 0.0` for the hit |
| 3 E-10 report-usage | PASS | hit runs: the hits line only (`1 hit(s) across scanned runs, ~$0.7500 avoided ...`); two `AO_CACHE=shadow` runs: the shadow line only (`1 would-hit(s) of 2 lookup(s), ~$0.7500 avoidable (est.)`); `--json` includes the 13.6 object (validated against the schema) and omits `result_cache` when no scanned run has records |
| 4 Both entry points | PASS | spy on `cli._build_result_cache` and `Orchestrator.__init__` kwargs: `run` and `resume` each pass the helper's own result (`is`-identical `ResultCache`); with the cache off both pass `None`; `resume --cache` also prints the banner |
| 5 U-LZ2 (CLI part) | PASS | fresh interpreter: `_print_state` / `_print_status_snapshot` with `result_cache == {}` leave `agent_orchestrator.cache.report` unloaded; with records it is loaded (positive control) |
| 6 No-op when off | PASS | `tests/cache/test_noop_proof.py` (I-1, I-2 goldens; unedited) green; `TestNoCacheNoChange` byte-compares `--no-cache` / `AO_CACHE=0` runs with the default run |
| 7 Hygiene | PASS | `CliRunner`, `monkeypatch.chdir(tmp_path)`, `executor: fake`, a ticking `runstate._utc_now` so second-granular run ids never collide; ruff and mypy clean; see Evidence |

Also added (decision recorded in HANDOFF): an E-2-style module check for a cache-off CLI process with
allow-list `{cache, cache.constants, cache.settings, cache.cli}`, and an AST guard that
`cache/cli.py` keeps module-level imports to typer plus constants/settings.

## Evidence
- `.venv/bin/python -m pytest -q -p no:cacheprovider tests/cache/test_cli_result_cache_wiring.py` -> **27 passed**.
- `.venv/bin/python -m pytest -q -p no:cacheprovider tests/cache tests/test_spawn_provenance.py tests/test_nfr2_regression_gate.py` -> **1190 passed** (I-1, I-2 goldens, spawn-provenance grep and NFR-2 gate included, all unedited).
- CLI/e2e modules (`tests/test_cli*.py`, `tests/test_e2e*.py`, `tests/test_status*.py`, `tests/test_resume_extend_breaker_cli.py`, `tests/test_claude_cli_argv_builder.py`): **417 passed** (521 s), unedited.
- `.venv/bin/ruff check src tests` -> All checks passed; `.venv/bin/ruff format --check src tests` -> clean (only generated `_build_info.py` is ever flagged); `.venv/bin/mypy src tests/cache` -> only the 4 pre-existing `_version.py` errors.
- `grep` of `.origin ==` / `!=` / `in` under `src/`: none added.

## Deviations from the HLD blocks (reason)
1. **`cache.cli` is allowed on the CLI path** (manager decision): the HLD E-2 allow-list (`cache`, `constants`, `settings`) is extended with `cache.cli`; `cache/cli.py` was already import-light (typer only) and an AST test now pins it. HANDOFF records it for T-bdQZW4.
2. **`_print_status_snapshot` formats the snapshot's own run block** (`snap["result_cache"]`, present only for a current record) instead of re-deriving it from a `RunState` (the function receives the parsed `status.json`, no state). The lazy import still happens only when the block exists.
3. **T-28J9oR tests narrowed (not deleted).** `test_cli_cache_flags.py::TestNoCacheNoChange::test_every_mode_is_output_identical_until_construction_lands` and `TestBuildHelper::test_returns_none_for_every_mode` asserted "no mode builds a cache until T-o95l1M"; they now cover the explicitly-off modes only (`--no-cache`, `AO_CACHE=0`, flag beating env), and the on/shadow behaviour is tested in `test_cli_result_cache_wiring.py`.
4. **`_echo_result_cache_line(block)`** is a small shared helper (both print sites; NFR-7 DRY).

## Risks / Blockers
- Run ids are second-granular (`<workflow>-<UTC seconds>`): two `ao run`s inside one wall-clock second share a run directory. Pre-existing; the tests pin the clock. Not changed here.
- `ResultCache.from_settings` does no store I/O (T-gDNjN2), so the banner cannot fail on an unwritable cache root; such a problem surfaces at the first lookup through the coordinator's own `store_unavailable` handling, never as a CLI error.

## Next actions
1. T-ZTxN1x (bench), then gate G1b.
2. T-6tRKml adds the `ao cache` commands and must keep `cache/cli.py` import-light.
3. T-bdQZW4 amends HLD 8.7.5 / E-2 for the `cache.cli` allowance.

## Comments
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Status initialized (Draft, Rev 2).
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 3 re-plan after the
  early-gate review and the manager's scope decisions (HLD §23.5). State stays `Draft`
  (engine set); this file, `TASK.md`, `HANDOFF.md` (when present) and the epic `STATUS.md` rollup
  agree.
- By: developer · Role: developer · Date: 2026-10-05 · Comment: State -> Done. Every acceptance
  criterion passes; deviations 1-4 above are small and documented. Epic `EPIC.md` / `STATUS.md`
  rollup updated to match.
