# HANDOFF: T-JCOAsq-cache-test-hardening

- Task: `T-JCOAsq-cache-test-hardening`
- State: `In Progress` (Parts 1 and 2 done; Part 3 pending)
- From: `tester`
- To: T-XpF1pF (Part 1 is its prerequisite), T-fXWbqg (gate evidence), T-bdQZW4, the parent

## Part 2 summary (2026-10-05, commit `4d11a69`)

Expected: `.venv/bin/python -m pytest -q -p no:cacheprovider tests/cache/test_adversarial.py tests/cache/test_integration_hardening.py` -> `147 passed`.

| File | Role |
|------|------|
| `tests/cache/test_integration_hardening.py` | 27 tests: I-5b, I-9..I-17, I-19, I-20, I-23, I-25, I-26 |
| `tests/cache/test_adversarial.py` | 120 tests: ADV-1..ADV-10 and ADV-4b |
| `tests/cache/_hardening_rig.py` | shared rig (new helper module; the same file-scope extension as the Part 1 `_noop_*` helpers): `Rig` (real `Orchestrator` + `ResultCache` + `LocalFsCacheStore`, fake or REAL git state), `HookedExecutor` (counting, cost-reporting, per-task before/after hooks), `SteppingClock`, `git()` |

### Test ids -> tests
- **I-5b**: `TestStoreAtMaxParallel` (4 tasks, `max_parallel=3`: all store, all then hit with 0 dispatches, a barrier proves two tasks were in flight together; two concurrent runs storing the same key, a barrier inside `put_entry`).
- **I-9** `test_i9_*`; **I-10** `TestPurityGuards` (4); **I-11** `TestPriorOutputRule` (2); **I-12** `test_i12_*` (real repo, cache on vs off); **I-13**; **I-14**; **I-15** (best-effort smoke, spawn; skipped with a reason on < 2 CPUs or Windows); **I-16** `TestEmitTasksChildren` (2); **I-17** `TestTtl` (2); **I-19**; **I-20**; **I-23** `TestShadowEndToEnd`; **I-25** `TestCoordinatorFailureBoundary` (4); **I-26** `TestPruneRacesStore` (3, the third best-effort).
- **ADV-1** `TestAdv1PathTraversalOnRestore` (7); **ADV-2** `TestAdv2KeySplicing`; **ADV-3** `TestAdv3CorruptBlob`; **ADV-4** `TestAdv4SymlinkedCacheComponents`; **ADV-4b** `TestAdv4bPlantedLinkDuringARealLookup`; **ADV-5** `TestAdv5LinksAtInputsAndOutputs`; **ADV-6a/b/c** `TestAdv6Fifo` (FIFO tests skip only without `os.mkfifo`); **ADV-7** `TestAdv7ResourceExhaustion`; **ADV-8a/b** `TestAdv8ModeBits`; **ADV-9** `TestAdv9HostileEntryCorpus` (coverage test + 28 `get_entry` cases + 28 full-lookup cases); **ADV-10** `TestAdv10SensitiveOutputs`. Symlink tests skip only on Windows.
- Threat ids (M-1 .. M-14) are in each class docstring.

### Interpretations (HLD 18.1 intent not derivable literally)
1. **I-20 "quota requeue followed by a hit"**: the quota branch returns before `_accumulate_actuals`, so a quota-failed attempt itself leaves `cumulative_*` at 0. The test therefore (a) pays for a real run (cost, tokens), (b) hides the entry and resumes (`prepare_resume` carries `cumulative_*`), (c) lets the first pass miss and dispatch into a quota wall, (d) restores the entry file from the injected `sleeper` (another process "stored it" during the wait), so the second pass is a hit. Asserts: one real dispatch, `dispatch_cycle == 3`, record current (`ended_at` bound), `cumulative_*` unchanged, and `aggregate_usage` site A counts the carried spend but not the hit as a task.
2. **I-16 "an injected task can only narrow"**: two workflows. With `defaults.cache` unset, an injected `cache: true` child is NOT cached while a static `cache: true` sibling is. With `defaults.cache: true`, an injected `cache: false` child is not cached and an injected child that says nothing is (author default applies). `emit_tasks` mutates the spec in place, so each run builds a fresh `WorkflowSpec`.
3. **I-26 "concurrent prune and store race is benign"**: the dangerous window is blobs written, entry not yet (orphan blobs). A `put_entry` wrapper runs a prune from another thread in exactly that window: inside the grace period the blob survives; with `now` past the grace (derived from the blob's real mtime, no wall clock) the blob is swept, the entry lands dangling, and the next run is a `blob_missing` miss with correct bytes and then self-heals. A third, best-effort test runs a destructive prune loop (`ttl 0`, late `now`) beside a parallel run.
4. **I-12 "integration commits identical"**: two workspaces with identical fixed clocks (so identical run ids and branch names), cache on vs off; the integration branch's per-commit (subject, tree) history and per-task integration statuses are equal. The ineligible reason is accepted as `run_integration_active` or `isolation_worktree` (rule order is not the test's business).
5. **ADV-2 "CLI arguments"**: driven through the real `ao cache show|rm` with `CliRunner`; every spliced argument exits non-zero and neither the cache tree nor a victim directory changes; a legitimate prefix works (positive control). The broader `ao cache` suite is E-7 (T-6tRKml).
6. **ADV-5 output-symlink cases**: the engine rejects an output whose resolved path leaves the workspace and follows a link inside it, so the cases that need a finished run keep the link target inside the workspace; the out-of-workspace directory-link case asserts the cache's outcome (ineligible `path_rejected`, nothing written) and that the ENGINE raises `ArtifactPathError`. At integration level the settle-time key recompute (guard 1) refuses a swapped-in link before capture's `output_not_regular_file` check; both reasons are accepted and capture is pinned by the unit tests.
7. **ADV-9 full lookup**: the planted corpus bytes replace the real entry of a primed run; the expected reason is whatever the store's own `get_entry` says for that exact file under the real key (`corrupt_entry`, or `key_mismatch` for `key_mismatch.json`), so a corpus entry whose content is valid but keyed elsewhere is also covered.
8. **ADV-10 restore-time half**: not reachable through a lookup (the key build refuses first), so it is a direct `restore_outputs` call with a real store and a planted entry.

## Part 1 summary (reworked 2026-10-05)

Files (all under the ticket's test scope; the `_noop_*.py` helpers are new and are used only by
`test_noop_proof.py`):

| File | Role |
|------|------|
| `tests/cache/test_noop_proof.py` | the tests (I-1, poison self-tests, negative controls, I-2) |
| `tests/cache/_noop_poison.py` | `PoisonedFinder` (`find_spec`), allowed set, loaded-module probe; imports nothing from `agent_orchestrator` |
| `tests/cache/_noop_scenarios.py` | the ten cache-off workflows, built from the existing engine tests' shapes |
| `tests/cache/_noop_subprocess.py` | I-1 entry point (`python -m tests.cache._noop_subprocess`), prints a JSON report |
| `tests/cache/_noop_capture.py` | the ONE capture script: produces the goldens on base and the actual output in I-2 |
| `tests/fixtures/result_cache/golden/{serial,parallel}/{status.json,stdout.txt}` | real base-captured goldens |

### Test ids
- I-1 self-tests: `TestPoisonedFinder::*`, `TestNegativeControls::*` (poison fires; a mid-run cache
  import fails the run, with the poison on and, separately, with it off).
- I-1: `TestI1PoisonedImports::test_i1_scenario_completes_cleanly[<scenario>]` for
  `serial, parallel, emit, loop, router_join_any, budget_wait, breakers_quiet, breakers_trip,
  hooks, isolation`, plus `test_i1_runs_every_required_workflow`, `test_i1_poison_was_active`,
  `test_i1_engine_cache_hooks_never_called`, `test_i1_loaded_cache_modules_subset_of_allowed`,
  `test_i1_overall_pass`.
- I-2: `TestI2GoldenSnapshot::test_i2_matches_base_golden[serial|parallel]`,
  `test_golden_is_real_captured_output[...]`, `test_comparison_detects_a_perturbed_golden[...]`.
- Expected: `37 passed` for `.venv/bin/python -m pytest -q -p no:cacheprovider tests/cache/test_noop_proof.py`.

### How I-1 stays valid after T-XpF1pF
`Orchestrator._result_cache_lookup`/`_result_cache_store` are replaced with raisers whether or not
they exist. `result_cache=None` is passed to `Orchestrator(...)` only when its signature has that
parameter (`_noop_scenarios.build_orchestrator`), so no edit is needed when the engine gains it.
After T-XpF1pF the engine may legitimately load `cache.constants`/`cache` (the allowed set); any
other `agent_orchestrator.cache.*` import by a cache-off run fails I-1.

### Golden capture (base commit `bb6d8a0`, full sha `bb6d8a0ef5a10b56d2795d6d67a9071de6588879`)
Run from the repo root of a tree that contains the capture script. `PYTHONPATH` selects which
`agent_orchestrator` is exercised (base worktree `src`), so the same script serves base and
current tree. Fixture: three fake-executor tasks (`a`, `b` depends on `a`, independent `c`),
`agent_orchestrator.runstate._utc_now` pinned to `2026-01-01T00:00:00Z` (fixed run id
`golden-fixture-20260101T000000Z`), every `AO_*` variable dropped, `HOME` sandboxed, the absolute
workspace path replaced with `<WS>` (the only normalisation).

```bash
BASE=<scratchpad>/noop-base-bb6d8a0
git worktree add --detach "$BASE" bb6d8a0
PYTHONPATH="$BASE/src" .venv/bin/python -m tests.cache._noop_capture \
    --variant serial   --out-dir tests/fixtures/result_cache/golden/serial
PYTHONPATH="$BASE/src" .venv/bin/python -m tests.cache._noop_capture \
    --variant parallel --out-dir tests/fixtures/result_cache/golden/parallel   # ao run --max-parallel 3
git worktree remove --force "$BASE"
```

(`PYTHONPATH` must be a literal path in the agent sandbox; the shell tool refuses a variable.)
The same script against the current tree is what `test_i2_matches_base_golden` runs:
`PYTHONPATH=<this tree>/src .venv/bin/python -m tests.cache._noop_capture --variant serial --out-dir <tmp>`.

Notes: serial and `max_parallel=3` currently produce byte-identical files (task order in
`status.json` and stdout follows spec order, not completion order), so the two golden directories
have equal content; both are kept because the ticket requires both variants and a future
scheduler change would make them diverge. Recapture only when a sibling epic changes
`status.json`/`ao run` output on purpose (HLD §24.2).

### Corrections to the first Part 1 handoff
The earlier handoff said the goldens were captured from base and compared byte for byte, and that
I-1 verified no cache import. Neither was true (hand-made goldens; a poison finder that Python 3.12
never consulted). This document replaces it.

### Finding for the parent (not fixed, out of the ticket's scope)
`src/agent_orchestrator/cli.py:44` imports `agent_orchestrator.cache.cli` eagerly, so a CLI
process (not the engine) loads a cache submodule outside HLD 8.7.5's CLI allowance (`cache`,
`constants`, `settings`). Decide: amend the HLD to include `cache.cli`, or register the sub-app
lazily. Part 3's E-2 module check (allowed set incl. `cache.settings`) will hit this on the CLI path.

## What will be handed over (Part 3)
- `tests/test_e2e_cli_result_cache.py` (Part 3: E-1…6)
- CI step in `.github/workflows/ci.yml` (Part 3)
- Coverage gate and full-suite baseline

## Frozen names / contracts
- Test file: `tests/cache/test_noop_proof.py`; capture script `tests/cache/_noop_capture.py`
- Golden dir: `tests/fixtures/result_cache/golden/{serial,parallel}/`
- NFR-1 evidence (HLD §8.7.5): I-1 / I-2 proof of no-op behavior
- Coverage thresholds: package ≥85%, core modules ≥90%
- `tests/conftest.py` unedited; the NFR-2 gate unedited.

## Comments
- By: architect · Role: architect · Date: 2026-10-04 · Comment: Handoff stub created.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2 contents.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 3: re-split parts, CI step.
- By: developer · Role: developer · Date: 2026-10-05 · Comment: Part 1 reworked (commit 8972149) after the first delivery was rejected; command, base sha, test ids and the CLI-import finding recorded above.
- By: developer · Role: developer · Date: 2026-10-05 · Comment: Part 2 done (commit `4d11a69`): test ids, files and interpretations recorded in "Part 2 summary" above.
