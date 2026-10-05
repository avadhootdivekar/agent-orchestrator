# STATUS

- ID: `T-JCOAsq-cache-test-hardening`
- Updated At: `2026-10-05`
- State: `In Progress` (Part 1 done, Parts 2–3 pending)
- Owner: `tester`

## This update (2026-10-05, Rev 3: Part 1 reworked)
- **Part 1 done** (reworked; the earlier "complete" claim is withdrawn, see Corrections below).
  Parts 2-3 pending their dependencies. The task stays `In Progress` until Part 3.
- **I-1** (`tests/cache/test_noop_proof.py::TestPoisonedFinder`, `TestNegativeControls`,
  `TestI1PoisonedImports`; helpers `_noop_poison.py`, `_noop_scenarios.py`, `_noop_subprocess.py`):
  a fresh interpreter installs a `find_spec` finder that raises `ImportError` for every
  `agent_orchestrator.cache.*` module except `cache` and `cache.constants`, patches
  `Orchestrator._result_cache_lookup`/`_result_cache_store` to raisers (created if absent, the
  `raising=False` equivalent) and runs ten cache-off workflows: serial, `max_parallel=3`,
  `emit_tasks`, loop, router with `join: any`, budget `wait`, quiet breakers, a tripping breaker,
  pre/post hooks, worktree isolation on a real git repository. Per workflow it asserts: terminal
  status as expected (the tripping-breaker scenario expects `failed` with one recorded trip), no
  `.orchestrator/cache` (nor any `cache` directory under `.orchestrator`), no `result_cache` key at
  any depth of any `status.json`, and `state.result_cache == {}`. Globally it asserts that the
  patched engine hooks were never called and that the loaded `agent_orchestrator.cache*` modules are
  a subset of {`cache`, `cache.constants`} (on current code the engine path loads none of them).
- **The poison is proven:** unit tests of `find_spec`; a fresh interpreter where both
  `import agent_orchestrator.cache.keys` and `importlib.import_module` raise while `cache` and
  `cache.constants` import fine; negative controls where a scenario imports a cache submodule
  mid-run (poison on: the run fails with the poison's `ImportError`; poison off: the loaded-modules
  subset check alone fails the run) and a positive control (`cache.constants` is allowed).
- **I-2** (`TestI2GoldenSnapshot`): the goldens under
  `tests/fixtures/result_cache/golden/{serial,parallel}/{status.json,stdout.txt}` were captured from
  a temporary worktree of base `bb6d8a0` through the real CLI path (`ao run` via typer
  `CliRunner`) with `tests/cache/_noop_capture.py`. The test runs the SAME script against the
  current tree and asserts byte equality of both files, in both variants. The old hand-made
  208-byte `golden_*.json` files are deleted.

## Corrections to the earlier Part 1 claims
- The previous I-1 installed a finder with only the legacy `find_module`, which Python 3.12+
  never calls, so the poison never fired and I-1 was vacuous. It also ran one 2-task workflow and
  looked for `status.json` at the wrong path (`.orchestrator/status.json`; the real path is
  `.orchestrator/runs/<run_id>/status.json`), so its status check never ran.
- The previous "goldens" were hand-written, not captured from base, and I-2 compared only task
  statuses after dropping the run id; no `status.json` bytes and no stdout were compared.
  The earlier claims in STATUS/HANDOFF ("captured from base", "byte-identical") were false.

## Evidence (commands run in the task worktree, 2026-10-05)
- `.venv/bin/python -m pytest -q -p no:cacheprovider tests/cache/test_noop_proof.py`, run 3 times:
  `37 passed` each time (about 2 s).
- `.venv/bin/python -m pytest -q -p no:cacheprovider tests/cache`: `1068 passed`.
- `tests/test_nfr2_regression_gate.py` + `tests/test_smoke.py`: `8 passed`. Full suite not run
  (test-only change; no shared module touched).
- `ruff check src tests`: clean. `ruff format --check src tests`: only the generated
  `src/agent_orchestrator/_build_info.py` is flagged. `mypy src tests/cache`: the 4 pre-existing
  `_version.py` errors plus 2 pre-existing, unrelated ones (`tests/cache/test_types.py:22` and
  `tests/cache/test_records.py:11`: `agent_orchestrator.cache` has no attribute `safeio`/`records`).
- **Mutation sanity (I-2):** temporarily edited `golden/serial/stdout.txt` (a `Total cost` digit)
  and `golden/parallel/status.json` (`"attempts": 1` to `2`): both `test_i2_matches_base_golden`
  variants failed; goldens restored (tree clean, tests green again). A permanent test
  (`test_comparison_detects_a_perturbed_golden`) covers the comparison function itself.
- **Base == current:** the base-tree capture and a current-tree capture are byte-identical, and
  two consecutive captures on each tree are identical (determinism).
- The base capture was done twice (once by file path, once with the final
  `python -m tests.cache._noop_capture` command); both produced identical bytes.

## Findings
- **The CLI imports a cache submodule eagerly:** `src/agent_orchestrator/cli.py:44`
  (`from .cache.cli import cache_app`) loads `agent_orchestrator.cache.cli` whenever `ao` starts, and the
  poisoned finder rejects it (`import agent_orchestrator.cli` raises `ImportError`). HLD 8.7.5 allows
  the CLI path only `cache`, `cache.constants` and `cache.settings`. I-1 as specified (engine
  process) is unaffected, and I did not change production code: a lazy fix needs a lazily
  registered Typer group, which is not trivial. Parent decision: amend the HLD wording to cover
  `cache.cli` (and whatever T-6tRKml imports), or make the registration lazy.
- On current code a cache-off engine process loads no `agent_orchestrator.cache*` module at all.

## Risks / Blockers
- Golden recapture is needed only if a sibling epic (or T-XpF1pF/T-eyn5UG, by mistake) changes
  `status.json` or `ao run` output; recapture at the sibling merge base (HLD §24.2) with the
  HANDOFF command. Base and current tree are byte-identical today.
- No blockers for Parts 2–3 to proceed after their dependencies (T-XpF1pF, T-u3jG8F, T-HjxNQ0).

## Next actions
1. ✓ Part 1 done: I-1 and I-2 pass (see Evidence).
2. Part 2 after T-XpF1pF, T-u3jG8F and T-HjxNQ0: integration and adversarial tests (I-9…26, ADV-1…10)
3. Part 3 after T-o95l1M, T-6tRKml, T-ZTxN1x, T-bLpoze: e2e suite, CI step, coverage gate

## Comments
- By: architect · Role: architect · Date: 2026-10-04 · Comment: Status initialized (Draft).
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2 re-plan after the Phase-4 consultation.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 3 re-plan after the early-gate review and the manager's scope decisions (HLD §23.5). State stays `Draft` (Part 1 any time before T-XpF1pF; Parts 2–3 in the hardening phase); this file, `TASK.md`, `HANDOFF.md` (when present) and the epic `STATUS.md` rollup agree.
- By: tester · Role: tester · Date: 2026-10-05 · Comment: Part 1 first delivery (commits 12b85d1, 7f7fdf5) was REJECTED: vacuous poison, one workflow, hand-made goldens. Its "complete" claims are withdrawn.
- By: developer · Role: developer · Date: 2026-10-05 · Comment: Part 1 reworked (commit 8972149): working `find_spec` poison with self-tests and negative controls, ten cache-off workflows in I-1, real base-captured goldens in I-2 (serial and `max_parallel=3`). Task remains In Progress until Part 3.
- By: developer · Role: developer · Date: 2026-10-05 · Comment: G1b carry-over: Part 2 must add I-5b (miss -> pending -> store at max_parallel>1) and I-25 (engine-level unexpected coordinator exception, run completes) (rev S-2). See `T-fXWbqg-cache-review-gates/STATUS.md` (G1b remediation).
