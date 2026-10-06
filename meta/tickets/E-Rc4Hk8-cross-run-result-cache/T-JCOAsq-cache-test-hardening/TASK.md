# TASK: T-JCOAsq-cache-test-hardening

## Metadata
- Task ID: `T-JCOAsq-cache-test-hardening`
- Epic ID: `E-Rc4Hk8-cross-run-result-cache`
- Owner: `tester`
- Created: `2026-10-04`
- Last Updated: `2026-10-05` (Rev 3, Part 3 done)
- Status: `Done` (Parts 1, 2 and 3 complete; every acceptance criterion passes)
- Estimate: `24 focus hours (3 days)`: Part 1 6 h ✓ / Part 2 10 h ✓ / Part 3 8 h ✓

## Requirements Mapping
- Requirement IDs: NFR-1, NFR-5, NFR-9, NFR-10, NFR-11, FR-1, FR-2, FR-5, FR-6, FR-8, FR-10, FR-16
- HLD: §18 (strategy, catalogue, coverage gates), §8.7.5 (no-op evidence), §19 (AC matrix),
  §24.2 (golden recapture)

## Description
Own the cross-cutting test layers, in three parts. **Each part needs only code that exists by the
time it runs.**

### Part 1 — no-op proof authoring (6 h). Depends on nothing (base code only).
It is a prerequisite of T-XpF1pF and may run at any point before it.

1. **Base golden.** From a temporary worktree of base `bb6d8a0` (with `PYTHONPATH` pointing at
   its `src`), capture the golden `status.json` and `ao run` stdout for a fixture workflow (three
   fake-executor tasks: a chain plus one independent), with a fixed clock and run id and the
   workspace path normalized to `<WS>`, **twice: serial and `max_parallel=3`**. Store them under
   `tests/fixtures/result_cache/golden/` and record the exact command in `HANDOFF.md`.
2. **I-2.** Active immediately: the cache-off path must stay byte-identical, in both variants,
   through every later task.
3. **I-1 (poisoned imports).** In a **subprocess**: install a `sys.meta_path` finder that raises
   `ImportError` for every `agent_orchestrator.cache.*` module except `agent_orchestrator.cache`
   and `.constants`; patch `Orchestrator._result_cache_lookup` and `_result_cache_store` to raise
   (`raising=False`, so the test is valid before T-XpF1pF); run, with `result_cache=None`, the
   workflows serial, `max_parallel=3`, emit, loop, router with `join: any`, budget wait, breakers,
   hooks, and isolation on a real repository. Assert: all complete; no `.orchestrator/cache`;
   no `result_cache` key in `status.json`; `state.result_cache == {}`; loaded
   `agent_orchestrator.cache*` modules ⊆ {`agent_orchestrator.cache`,
   `agent_orchestrator.cache.constants`}. This needs no cache code to exist.

### Part 2 — integration and adversarial hardening (10 h). Depends on T-XpF1pF, T-u3jG8F, T-HjxNQ0.
- I-9, I-10, I-11, I-12, I-13, I-14, I-16, I-17, I-19, I-20 (including usage site A keeping the
  real spend), I-23 (shadow end to end), I-25, I-26. (I-24, the Rev 2 `refresh` test, is
  retired.)
- **I-15** is labelled **best-effort smoke** in its docstring; contention itself is proved by
  U-SM13 (T-HjxNQ0).
- **ADV-1…ADV-10** at integration level, with the threat ids (M-n) in the docstrings, including
  the **ADV-9 harness** (every corpus file from T-FJH6LI through `get_entry` and through a full
  lookup) and **ADV-4b** (a planted symlinked shard or `entries/v1` directory during a real
  lookup: miss `unsafe_path`, victim directory untouched, entry not evicted).

### Part 3 — end to end, coverage gate, full suite (8 h). Depends on Part 2, T-o95l1M, T-6tRKml, T-ZTxN1x, T-bLpoze.
- **E-1…E-6** (HLD §18.1). E-1 includes the companion negative (cache off → 2 dispatches). E-2
  includes a subprocess module check: loaded cache modules ⊆ {`cache`, `cache.constants`,
  `cache.settings`, `cache.cli`} (`cache.cli` added by the manager decision: the CLI registers
  the `ao cache` group eagerly; recorded in `HANDOFF.md`).
- **CI step.** Add one additive step to `.github/workflows/ci.yml`, exactly as in HLD §18
  ("Result cache tests + coverage (E-Rc4Hk8)"): `--cov=agent_orchestrator.cache
  --cov-fail-under=85`, then `coverage report --include=... --fail-under=90` for `keys`, `store`,
  `restore` and `coordinator`.
- **Full suite**: `pytest -q`, `ruff check .`, `ruff format --check .`, `mypy src`; paste the
  numbers into `STATUS.md`.

**Conventions.**
- `CliRunner`, `monkeypatch.chdir(tmp_path)`, workflows that opt in with
  `"defaults": {"cache": true}`.
- Count dispatches with a wrapper on `agent_orchestrator.executors.fake.FakeExecutor.execute`;
  the same wrapper sets `cost_usd` and tokens on successful results, because `FakeExecutor`
  reports no cost.
- Monkeypatch `agent_orchestrator.runstate._utc_now` to an advancing clock (1 s run-id
  granularity).
- Fixed or stepping clocks; no `sleep`; FIFO and multiprocess cases joined with timeouts; explicit
  skip markers; permission errors simulated.
- **Never edit `tests/conftest.py`.** Each module controls `AO_CACHE` itself.

## File scope (exclusive)
- `tests/cache/test_noop_proof.py` (Part 1: I-1, I-2) and its helpers
  `tests/cache/_noop_poison.py`, `_noop_scenarios.py`, `_noop_subprocess.py`, `_noop_capture.py`
- `tests/fixtures/result_cache/golden/**` (Part 1)
- `tests/cache/test_adversarial.py`, `tests/cache/test_integration_hardening.py` (Part 2) and its shared helper `tests/cache/_hardening_rig.py`
- `tests/test_e2e_cli_result_cache.py` (Part 3: E-1…E-6)
- `.github/workflows/ci.yml` (Part 3: one additive step)

## Inputs / Outputs
- **Inputs:** base code (Part 1); the implementation tasks as listed per part; the corpus from
  T-FJH6LI.
- **Outputs:** the NFR-1 evidence, the integration and adversarial suites, the e2e suite, the CI
  coverage gate and the full-suite numbers.

## Acceptance Criteria
1. **Part 1.** The golden is captured from base code (command and base sha in `HANDOFF.md`); I-2
   passes, serial and `max_parallel=3`; I-1 passes on base code and keeps passing after
   T-XpF1pF.
2. **Part 2.** I-9…I-17, I-19, I-20, I-23, I-25 and I-26 pass deterministically; I-15 is
   labelled best-effort; ADV-1…ADV-10 and ADV-4b pass.
3. **Part 3.** E-1…E-6 pass.
4. **Coverage (hard pass/fail).** The CI step passes: `agent_orchestrator.cache` ≥ **85%**, and
   `keys.py`, `store.py`, `restore.py` and `coordinator.py` each ≥ **90%**. A shortfall fails this
   task; it is not waived by listing it.
5. **Full suite.** `pytest -q` shows no new failures against the baseline (5041 passed / 8 skipped
   / 2 known bench failures), plus the new tests; `ruff check .`, `ruff format --check .` and
   `mypy src` are clean; the NFR-2 gate passes; `tests/conftest.py` is unedited.

## Test requirements
- As listed above. All deterministic and replayable.

## Risks
- **Golden fragility.** Mitigation: normalization plus a fixed clock and run id. After the
  sibling epics merge, the parent recaptures the goldens at the merge base if a sibling changed
  `status.json` or `ao run` output on purpose (HLD §24.2).
- **Flaky multiprocess tests.** Mitigation: bounded iterations and timeouts; I-15 best-effort.

## Dependencies
- **Part 1:** none (base code).
- **Part 2:** T-XpF1pF, T-u3jG8F, T-HjxNQ0 (plus the T-FJH6LI corpus).
- **Part 3:** Part 2, T-o95l1M, T-6tRKml, T-ZTxN1x, T-bLpoze.

## Pseudocode / Algorithm
```text
HLD §18.1 catalogue rows owned by T-JCOAsq; §8.7.5 I-1/I-2 definitions; §18 CI step.
```

## Schemas / Interface Notes
- **`status.json` shapes:** HLD §13.5. **CLI JSON:** HLD §13.4, §13.6.

## Handoff Boundary
- **Upstream:** base code (Part 1); the implementation tasks (Parts 2–3).
- **Downstream:** T-XpF1pF (Part 1 is its prerequisite), T-fXWbqg (gate evidence), T-bdQZW4.

## Artifacts
- **Docs/comments:** `meta/tickets/E-Rc4Hk8-cross-run-result-cache/T-JCOAsq-cache-test-hardening/`
- **Large outputs:** coverage HTML, if any, under `output/E-Rc4Hk8-cross-run-result-cache/coverage/`

## Comments
- By: architect · Role: architect · Date: 2026-10-04 · Comment: Cross-cutting test ownership.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2: golden determinism, I-1
  targets, E-1 negative control, I-19…I-26, corpus harness, no conftest edit.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 3 (early-gate A1b, A1e, C,
  D): parts re-split so each needs only existing code (Part 1 base-only; the ADV-9 harness moved
  to Part 2); I-1 uses a poisoned-import finder plus a module check; I-2 adds `max_parallel=3`;
  I-24 retired; ADV-4b added; the CI coverage step is in this task's file scope; coverage is a
  hard pass/fail AC.
- By: developer · Role: developer · Date: 2026-10-05 · Comment: Part 1 reworked (commit 8972149)
  after the first delivery was rejected (vacuous poison, one workflow, hand-made goldens). File
  scope extended to the `_noop_*.py` helper modules. Status stays `In Progress` until Part 3;
  details in `STATUS.md` and `HANDOFF.md`.
- By: developer · Role: developer · Date: 2026-10-05 · Comment: Part 2 delivered (commit `4d11a69`): 147 tests (I-5b, I-9..I-17, I-19, I-20, I-23, I-25, I-26, ADV-1..ADV-10, ADV-4b). File scope extended by the helper module `_hardening_rig.py`. Status stays `In Progress` until Part 3; details in `STATUS.md` and `HANDOFF.md`.
- By: developer · Role: developer · Date: 2026-10-05 · Comment: Part 3 done and the task is `Done` (commits `b7489d1` tests, `a947986` CI step, then the docs commit). `tests/test_e2e_cli_result_cache.py` holds E-1..E-6 (55 tests, through `CliRunner`); the CI step "Result cache tests + coverage (E-Rc4Hk8)" is in `.github/workflows/ci.yml`; coverage 98.71% for the package and 100/96/97/100% for keys/store/restore/coordinator; full suite 6805 passed, 10 skipped, 0 failed. E-2's module allow-list includes `cache.cli` (manager decision, recorded in `HANDOFF.md`). The Part 3 run found that a Part 2 tripwire broke under `--cov`; fixed in the test (no production change). Acceptance criteria 1-5 are checked in `STATUS.md`; the HLD 24.2 merge-notes table is in `HANDOFF.md`.
