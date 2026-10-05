# HANDOFF: T-JCOAsq-cache-test-hardening

- Task: `T-JCOAsq-cache-test-hardening`
- State: `In Progress` (Part 1 done; Parts 2–3 pending)
- From: `tester`
- To: T-XpF1pF (Part 1 is its prerequisite), T-fXWbqg (gate evidence), T-bdQZW4, the parent

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

## What will be handed over (Parts 2-3)
- `tests/cache/test_adversarial.py` (Part 2: ADV-1…10)
- `tests/cache/test_integration_hardening.py` (Part 2: I-9…26)
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
