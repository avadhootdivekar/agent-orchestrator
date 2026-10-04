# TASK: T-JCOAsq-cache-test-hardening

## Metadata
- Task ID: `T-JCOAsq-cache-test-hardening`
- Epic ID: `E-Rc4Hk8-cross-run-result-cache`
- Owner: `tester`
- Created: `2026-10-04`
- Last Updated: `2026-10-05` (Rev 2)
- Status: `Draft`
- Estimate: `24 focus hours (3 days)`: Sprint 1 6 h / Sprint 2 10 h / Sprint 3 8 h

## Requirements Mapping
- Requirement IDs: NFR-1, NFR-5, NFR-9, NFR-10, NFR-11, FR-1, FR-2, FR-5, FR-6, FR-8, FR-10, FR-16
- HLD: §18 (strategy, catalogue), §8.7.5 (no-op evidence), §19 (AC matrix)

## Description
Own the cross-cutting test layers in three parts.

### Part 1 (Sprint 1, 6 h): before any engine-touching task merges
1. **Base golden.** Capture, at base `bb6d8a0`, the golden `status.json` and the `ao run` stdout
   for a fixture workflow:
   - three fake-executor tasks: a chain plus one independent;
   - a fixed clock and a fixed run id;
   - the absolute workspace path normalized to `<WS>`.

   Store them under `tests/fixtures/result_cache/golden/`. Document the regeneration command in
   `HANDOFF.md`: a temporary worktree of `bb6d8a0` with `PYTHONPATH` pointing at its `src`.
2. **I-2 test code.** It is active immediately: the cache-off path must stay byte-identical
   through every task.
3. **I-1 test code.** Guard it with
   `pytest.importorskip("agent_orchestrator.cache.coordinator")`. It patches
   `Orchestrator._result_cache_lookup` and `Orchestrator._result_cache_store` (with
   `raising=False`) and every public `ResultCache` method to raise. It runs workflows with
   `result_cache=None`:
   - serial;
   - `max_parallel=3`;
   - emit;
   - loop;
   - a router with `join: any`;
   - a budget wait;
   - breakers;
   - hooks;
   - isolation on a real repository.

   It asserts:
   - no `.orchestrator/cache` directory;
   - no `result_cache` key in `status.json`;
   - `state.result_cache == {}`;
   - a **subprocess** check that `sys.modules` gains no `agent_orchestrator.cache.coordinator`.
4. **Hostile-entry corpus.** Coordinate with T-FJH6LI, which owns the corpus files. Here, write
   the ADV-9 harness that runs every corpus file through `get_entry` and a full lookup.

### Part 2 (Sprint 2, 10 h): integration hardening as the modules land
- **I-9:** a cache-off resume after a deleted-output hit leaves a stale record that is omitted
  everywhere.
- **I-10:** purity guards. A mutated input gives `key_changed_during_run`. A commit gives
  `repo_head_moved`, also with `include_repo_heads: false`. An undeclared tracked edit gives
  `repo_worktree_changed`.
- **I-11:** the prior-output rule (D6).
- **I-12:** an isolation workflow is entirely ineligible, and the integration commits are
  identical with the cache on and off.
- **I-13:** a corrupt blob gives a miss on run 2 (not storable) and is re-stored on run 3.
- **I-14:** a `../escape` tamper writes no file outside the workspace.
- **I-15:** two processes on the same workspace both succeed, and `verify` is ok.
- **I-16:** `emit_tasks` children; an injected task can only narrow.
- **I-17:** TTL with a stepping clock.
- **I-19:** a committing sibling at `max_parallel=2` means the concurrent task is not stored.
- **I-20:** a quota requeue then a hit preserves real spend in `cumulative_*`.
- **I-23 / I-24:** shadow and refresh end to end.
- **I-25:** an injected unexpected exception gives `cache.disabled`, the run completes, and strict
  mode re-raises.
- **I-26:** a concurrent `prune` and store race is benign.
- **ADV-1..10:** the integration-level variants.

### Part 3 (Sprint 3, 8 h): end to end, coverage and suite
- **E-1:** `ao run --cache` twice on an opted-in workflow, with outputs deleted in between. The
  second run makes **0** dispatches and prints `Result cache: hits=2 …`. A companion negative,
  with the cache off, counts 2 dispatches.
- **E-2:** a plain run creates no cache directory and prints no "Result cache" text; stdout
  equals the golden.
- **E-3 / E-4:** `--no-cache` beats env and config, and the full precedence matrix, including
  `AO_CACHE=shadow|refresh|maybe`.
- **E-5:** double opt-in. Operator on but no opt-in gives the banner "no task opts in" and no
  records. `defaults.cache: false` with one task set `true` caches only that task.
- **E-6:** `ao resume --cache` and `--no-cache` after a failure.
- **Coverage report.** `agent_orchestrator.cache` must be at least 85% overall and at least 90%
  for keys, store, restore and coordinator. If `.github/workflows/*.yml` already runs
  `pytest --cov`, add a per-package threshold. Otherwise record the numbers in `STATUS.md` and
  propose the CI change.
- **Full suite.** Run `pytest -q`, `ruff` and `mypy`, and paste the numbers into `STATUS.md`.

**E2E conventions.**
- Use `CliRunner` and `monkeypatch.chdir(tmp_path)`.
- Write workflows that opt in through `"defaults": {"cache": true}`.
- Count dispatches by monkeypatching
  `agent_orchestrator.executors.fake.FakeExecutor.execute` with a counting wrapper.
- Monkeypatch `agent_orchestrator.runstate._utc_now` to an advancing clock: run ids have
  1-second granularity.
- **Never edit `tests/conftest.py`.** Each module controls `AO_CACHE` itself.

## File scope (exclusive)
- `tests/cache/test_noop_proof.py` (I-1, I-2)
- `tests/cache/test_adversarial.py` (ADV harness, including ADV-9)
- `tests/cache/test_integration_hardening.py` (Part 2)
- `tests/test_e2e_cli_result_cache.py` (E-1…E-6)
- `tests/fixtures/result_cache/golden/**`

## Inputs / Outputs
- **Inputs:** every implementation task, as it lands.
- **Outputs:** NFR-1 evidence, the adversarial and integration suites, e2e coverage, and the
  coverage report.

## Acceptance Criteria
1. **Part 1.**
   - The golden is captured from base code. `HANDOFF.md` records the exact command and the base
     sha.
   - I-2 passes on every branch state from Sprint 1 onward.
   - I-1 is present and activates when the coordinator lands.
2. **Part 2.** Each of I-9…I-17, I-19, I-20 and I-23…I-26 passes deterministically:
   - fixed or stepping clocks;
   - no `sleep`;
   - FIFO and multiprocess cases joined with timeouts.
3. **ADV-1…ADV-10.** Every adversarial case passes, with the threat-model ids (M-n) referenced in
   the test docstrings.
4. **Part 3.**
   - E-1…E-6 pass. E-1 includes the companion negative control.
   - Coverage meets the targets above, or a shortfall is listed explicitly in `STATUS.md` with
     the uncovered lines.
5. **Full suite.**
   - `pytest -q` shows no new failures against the baseline (5041 passed / 8 skipped / 2 known
     bench failures), plus the new tests.
   - The NFR-2 gate passes.
   - `tests/conftest.py` is unedited.

## Test requirements
- As listed above. All are deterministic and replayable.

## Risks
- **Golden fragility.** Absolute paths and timestamps can leak into the golden. Mitigation:
  normalization plus a fixed clock and run id.
- **Flaky multiprocess tests.** Mitigation: bounded iterations and timeouts; the ≥ 2 CPU
  assumption is documented.

## Dependencies
- **Part 1:** none (base code), plus T-FJH6LI for the corpus.
- **Part 2:** the modules as they land (T-gDNjN2, T-XpF1pF, T-u3jG8F, T-HjxNQ0).
- **Part 3:** T-o95l1M, T-6tRKml, T-ZTxN1x.

## Pseudocode / Algorithm
```text
HLD §18.1 catalogue rows owned by T-JCOAsq; §8.7.5 I-1/I-2 definitions.
```

## Schemas / Interface Notes
- **`status.json` shapes:** HLD §13.5.
- **CLI JSON:** HLD §13.4.

## Handoff Boundary
- **Upstream:** all implementation tasks.
- **Downstream:** T-fXWbqg (G1b/G2 evidence), T-nPMuz4 (G0 uses the e2e patterns), T-bdQZW4.

## Artifacts
- **Docs/comments:** `meta/tickets/E-Rc4Hk8-cross-run-result-cache/T-JCOAsq-cache-test-hardening/`
- **Large outputs:** coverage HTML, if any, under `output/E-Rc4Hk8-cross-run-result-cache/coverage/`

## Comments
- By: architect · Role: architect · Date: 2026-10-04 · Comment: Cross-cutting test ownership.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2 changes:
  - golden determinism (`<WS>` normalization, fixed run id; tester T1);
  - I-1 patches the engine's private methods and `ResultCache` (tester T2);
  - E-1 negative control (tester T3);
  - new I-19…I-26 and the hostile corpus (security S1);
  - run-id clock patching (developer #15);
  - **no conftest edit** (developer #2).
