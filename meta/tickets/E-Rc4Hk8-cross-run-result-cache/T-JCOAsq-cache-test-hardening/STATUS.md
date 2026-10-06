# STATUS

- ID: `T-JCOAsq-cache-test-hardening`
- Updated At: `2026-10-05`
- State: `Done` (Parts 1, 2 and 3 done; acceptance criteria 1-5 all pass, see below)
- Owner: `tester`

## This update (2026-10-05, Part 3 done; task Done)
By: developer · Role: developer · Date: 2026-10-05.

- **E-1..E-6** (commit `b7489d1`): `tests/test_e2e_cli_result_cache.py`, 55 tests, every one entering
  through `CliRunner` on the real `ao` app (`ao run`, `ao resume`, `ao status`, `ao report-usage`),
  `executor: fake`, `monkeypatch.chdir(tmp_path)`, workflows opting in with `defaults.cache: true`
  (task-level flags where opt-in itself is the subject), a dispatch-counting wrapper on
  `FakeExecutor.execute` that also reports cost/tokens and stamps each output with its dispatch
  ordinal (so a restored file is provably the stored bytes), and a ticking `runstate._utc_now`.
  - **E-1** `TestE1SecondRunIsAllHits` (4): `ao run --cache` twice with the outputs deleted: the
    second run makes 0 dispatches (counter unmoved), restored bytes equal run 1's, the stdout line
    matches `Result cache: hits=2 (saved ~$1.0000 est., ~2540 tokens, ~Ns) would_hits=0 misses=0
    stored=0 ineligible=0` after `Total cost:   $0.0000`; `status.json` has `attempts == 0`, the
    exact D35 view with `source_run_id` of run 1, `usage_totals.cost_usd == 0`; `report-usage
    --json` aggregates hits=2/lookups=4. **Companion negative:** the same sequence with the cache
    off dispatches 4 times in total (2 on the second run vs 0), prints nothing about the cache,
    creates no cache directory and no `result_cache` key. Control: a hand-edited leftover output
    changes the key (D6), so both tasks are dispatched again.
  - **E-2** `TestE2PlainRun` (4 tests incl. parametrization): a plain run (the workflow opts in; the
    operator did not enable the layer) leaves no cache directory, no "result cache" text in `run`,
    `status`, `report-usage` (text and JSON), no `result_cache` key in `status.json`; stdout and
    `status.json` equal the base-`bb6d8a0` I-2 goldens byte for byte (serial and `max_parallel=3`)
    from the SAME capture script run in a subprocess, and in that subprocess the loaded
    `agent_orchestrator.cache*` modules are a subset of {`cache`, `cache.constants`,
    `cache.settings`, `cache.cli`} (manager decision recorded in `HANDOFF.md`; non-vacuous: the
    settings and cli modules must be present). Positive control: a `--cache` run in a subprocess
    additionally loads `coordinator`, `store` and `keys`.
  - **E-3** `TestE3NoCacheWins` (7): `--no-cache` beats env on/shadow, config on/shadow and both:
    the second run dispatches again (4 total), no banner/warning, no cache directory, no records;
    positive controls: the same env/config without the flag hits (2 dispatches total).
  - **E-4** `TestE4PrecedenceMatrix` (33 rows + a coverage guard): flag > env > config > off, the
    env spellings (`1/true/yes/ ON /0/false/no/off/shadow/ Shadow `, empty and blank), `refresh`
    and `maybe` giving off plus exactly one warning (also over an enabling config), config
    `enabled`/`mode`, and the flag short-circuiting the env (no warning). The expectation per row
    is literal (not computed by the resolver) and checked on the banner (mode, source,
    "2 of 2 static task(s) opted in"), the warning lines, the cache directory and `status.json`
    (lookups 2/misses 2/stored 2 and the per-task `mode`, or no `result_cache` at all).
  - **E-5** `TestE5DoubleOptIn` (3): operator on but no task opts in: the explanatory banner,
    no records, no entries, 4 dispatches; `defaults.cache: false` plus one task `cache: true`
    (and one explicit `false`): only that task is cached (one lookup, restored from run 1; the
    others dispatch again, banner "1 of 3"); a task-level `false` beats `defaults.cache: true`.
  - **E-6** `TestE6Resume` (3): run 1 `--cache` stores a, b, c; run 2 (cache OFF, outputs deleted)
    fails at c; `ao resume --cache` serves c from run 1's entry with 0 new dispatches of any
    task (run `succeeded`, c's record is a hit, a/b have none), while `ao resume --no-cache`
    re-dispatches only c (no banner, no `result_cache` anywhere); and a restored task is not
    re-dispatched on resume with its record still current (hits stay 2, `attempts == 0`).
- **Mutation sanity** (temporary one-edit source mutations, run against the e2e file, each restored
  byte for byte; every one FAILED the intended tests): `store.get_entry` returning `None` (E-1:
  4 dispatches instead of 2); `cli._build_result_cache` importing the coordinator before the
  `MODE_OFF` return (E-2 module check, `[serial]`); `resolve_result_cache_settings` letting the
  env override `--cache` (E-4 row `--cache|env='0'`); `DEFAULT_TASK_CACHE_POLICY = True` (E-5
  "no task opts in"); `ao resume` ignoring its `--cache` flag (E-6 resume-with-cache);
  `--no-cache` no longer a kill switch (E-3 `env-on`); the unknown-env warning silenced (E-4 row
  `refresh`); and, for the fixed Part 2 tripwire, `get_entry` touching the filesystem before key
  validation (ADV-2, run with `--cov`).
- **Defect found (test, not production): ADV-2's filesystem tripwire broke under `--cov`.**
  The CI step runs with `--cov`; coverage.py's tracer calls `os.path.realpath` -> `os.lstat` for
  each source file it first sees, on the stack of whatever is running, and that tripped the blanket
  `os.lstat/stat/open/unlink/...` spy of `test_adv2_every_store_operation_rejects_a_spliced_key...`
  (the spy then stayed installed through teardown and failed the whole session). It passed without
  `--cov`, which is why Part 2 did not see it. Fixed in `tests/cache/test_adversarial.py`
  (commit `b7489d1`): the spy delegates to the real function when the `coverage` package is on the
  call stack; the tripwire is still live (mutation above). No production code changed.
- **No production defect found in Part 3.** `src/` untouched in this task's Part 3
  (`git diff 07f9b22..HEAD -- src` is empty). Engine budget unchanged: net +105 / 12 added lines
  inside existing functions vs `ed8b8c3` (135 added, 30 removed; budget +110 / 12).
- **CI step** (commit `a947986`): one additive step "Result cache tests + coverage (E-Rc4Hk8)" in
  `.github/workflows/ci.yml`, after the bench step and before the dashboard step, text exactly as
  HLD 18. `yaml.safe_load` parses the workflow (job `test`, 9 steps with the new one).

### Acceptance criteria
1. **Part 1** PASS: goldens captured from base `bb6d8a0` (command in `HANDOFF.md`), I-2 serial and
   parallel pass, I-1 passes (37 tests in `test_noop_proof.py`, unedited and green in the full run).
2. **Part 2** PASS: 147 tests green (full run).
3. **Part 3** PASS: E-1..E-6, 55 tests.
4. **Coverage (hard gate)** PASS: package 98.71% (>= 85), `keys.py` 100%, `store.py` 96%,
   `restore.py` 97%, `coordinator.py` 100% (each >= 90); no extra tests were needed.
5. **Full suite** PASS: 6805 passed, 10 skipped, 0 failed (the 2 known bench failures did not
   occur; no regression against the baseline 5041 / 8 / 2). `ruff check src tests` and `ruff format
   --check src tests` clean; `mypy src` only the 4 pre-existing `_version.py` errors; NFR-2 gate
   green; `tests/conftest.py` and `tests/test_nfr2_regression_gate.py` unedited.

### Evidence for Part 3 (commands run in the task worktree, 2026-10-05)
- `.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_e2e_cli_result_cache.py`: `55 passed`
  (about 5 s).
- CI step, run locally exactly as in `ci.yml` (venv on `PATH`; script in the scratchpad):
  `pytest tests/cache tests/test_e2e_cli_result_cache.py tests/test_e2e_cli_result_cache_admin.py -q
  --cov=agent_orchestrator.cache --cov-report=term --cov-fail-under=85` -> `1620 passed in 48.65s`,
  `Required test coverage of 85% reached. Total coverage: 98.71%` (2644 statements, 34 missed).
  Per module: `keys.py` 92/0 100%, `store.py` 620/25 96%, `restore.py` 150/4 97%, `coordinator.py`
  260/0 100% (the four `coverage report --include="*/agent_orchestrator/cache/$m.py"
  --fail-under=90` calls exit 0); `safeio.py` 97%, every other cache module 100%.
- Full suite `.venv/bin/python -m pytest -q -p no:cacheprovider` (run after the tests and CI
  commits, tree clean): `6805 passed, 10 skipped, 1 warning in 711.65s (0:11:51)`. Baseline for
  comparison: 6572 passed / 10 skipped at T-6tRKml (before Parts 2/3 and T-bLpoze/T-nPMuz4 tests).
- `ruff check src tests`: `All checks passed!`. `ruff format --check src tests`: only the generated,
  git-ignored `src/agent_orchestrator/_build_info.py` is flagged (only when it exists). `ruff check .`
  and `ruff format --check .`: the ONLY finding is `output/E-YAAGhk-overseer-runner-template/
  repro_emit_lost_on_breaker_trip.py` (import order / format), a file from the merged overseer PR
  #13 (`191da69`), untouched by this epic (`git diff 0b980e3..HEAD -- output/E-YAAGhk-...` empty),
  so it is pre-existing and excluded. `mypy src tests/test_e2e_cli_result_cache.py`: only the 4
  pre-existing `_version.py` errors. No line over 100 columns.
- Dashboard: `cd ui && npx tsc -b --noEmit` exit 0; `npx vitest run`: `36 files, 419 tests passed`.
  The dashboard CI step `pytest tests/ui tests/test_general_instructions.py
  tests/test_e2e_cli_prompt_and_instructions.py -q --cov=agent_orchestrator.ui --cov-report=term
  --cov-fail-under=80`: `659 passed, 2 skipped`, `Total coverage: 94.42%`.
- I-2 goldens unedited: `tests/fixtures/result_cache/golden/{serial,parallel}/` last changed in
  `8972149` (Part 1); `test_i2_matches_base_golden[serial|parallel]` pass in the full run.
- `tests/conftest.py`, `tests/test_nfr2_regression_gate.py`: `git diff 0b980e3..HEAD` empty for both.
  No `.origin ==`/`!=`/`in` under `src/` (grep empty).

## Earlier update (2026-10-05, Part 2 done)
- **Part 2 done** (commit `4d11a69`, tests only, no `src/` change): 147 new tests, all passing
  deterministically (3 consecutive runs). The task stayed `In Progress` until Part 3.
  - `tests/cache/test_integration_hardening.py` (27 tests): I-5b (both halves: 4 independent
    opted-in tasks at `max_parallel=3` all store, then all hit with 0 dispatches, plus two
    concurrent runs storing the SAME key with a barrier inside `put_entry` proving the overlap),
    I-9, I-10 (key_changed_during_run, repo_head_moved with and without `include_repo_heads`,
    repo_worktree_changed; each with a positive control), I-11 (prior-output rule; in-place update
    task), I-12 (isolation on a real repo, cache on vs off: same tree/subject history), I-13, I-14,
    I-15 (best-effort smoke, two spawned interpreters), I-16, I-17 (stepping clock: inside TTL,
    expired, `ttl_days: None`), I-19 (real commit by a concurrent sibling), I-20 (quota requeue then
    hit; usage site A keeps the real spend), I-23 (shadow end to end), I-25 (unexpected lookup
    error, unexpected store error, strict re-raise, expected-error contrast), I-26 (a prune inside
    the store window: grace keeps the blob; past grace costs a miss, never wrong bytes; plus a
    best-effort prune loop beside a parallel run).
  - `tests/cache/test_adversarial.py` (120 tests): ADV-1 (7 manifest path forgeries), ADV-2
    (spliced keys at every store op with a filesystem tripwire, `key_mismatch`, traversal in hex
    fields, `ao cache rm/show` arguments via `CliRunner` with a positive control, junk entry
    names), ADV-3 (4 blob damages + missing blob), ADV-4 (symlinked root, blob, blob shard, entry
    file), ADV-4b (planted symlinked entry shard and `entries/v1`: miss `unsafe_path`, victim tree
    byte/mtime identical, entry not evicted, link survives, WARNING logged), ADV-5 (symlink in an
    input dir, output swapped for a link, link planted between key build and restore, link
    directory out of the workspace), ADV-6a/b/c (FIFO input, FIFO in an input dir, FIFO as entry
    file and as blob, all under a 5 s watchdog), ADV-7 (4 lying sizes, a sparse oversized entry
    file with a read-size spy, an oversized blob), ADV-8a/b, ADV-9 (harness covers every file of
    `tests/fixtures/result_cache/corpus` listed in MANIFEST; 28 files through `get_entry` AND
    through a full lookup with a state.json reload), ADV-10 (5 sensitive outputs, an output
    symlinked into `.git/hooks`, restore-time re-assertion with a planted entry).
  - `tests/cache/_hardening_rig.py`: shared rig (new helper module, like the Part 1 helpers).
- **Defects found in production code: none.** Observations that are engine behaviour, not cache
  defects (recorded so nobody "fixes" them in the cache): the engine itself raises
  `ArtifactPathError` at dispatch for an output whose directory is a symlink out of the workspace
  (`engine.py` `_run_with_retries`), and the engine resolves a declared output through a symlink,
  so an agent's write follows the link. A quota-exhausted attempt's usage is not accumulated into
  `cumulative_*` (the quota branch of `_settle_completed_task` returns before
  `_accumulate_actuals`); I-20 therefore obtains its real spend from a prior paid run carried over
  `prepare_resume`.
- Carry-overs from the gates are closed: G1b rev S-2 (I-5b, I-25) and the committed
  store-at-`max_parallel>1` test.

## Evidence for Part 2 (commands run in the task worktree, 2026-10-05)
- `.venv/bin/python -m pytest -q -p no:cacheprovider tests/cache/test_adversarial.py tests/cache/test_integration_hardening.py`
  run 3 times: `147 passed` each time (about 3 s). No skips on this host (symlink, FIFO and 2-CPU
  markers all satisfied).
- `.venv/bin/python -m pytest -q -p no:cacheprovider tests/cache`: `1427 passed` (3 runs, about 19 s;
  1280 before Part 2); `tests/cache tests/test_nfr2_regression_gate.py tests/test_spawn_provenance.py`:
  `1448 passed`. Goldens (I-1/I-2) unedited and green.
- `ruff check src tests`: clean. `ruff format --check src tests`: clean (the generated
  `_build_info.py` is only flagged when it exists). `mypy src tests/cache`: only the 4 pre-existing
  `src/agent_orchestrator/_version.py` errors. No line over 100 columns; no blanket `# noqa`.
- Full suite not run: no `src/` file was edited (the mutation runs restored every file; `git status`
  clean for `src/`).
- **Mutation sanity** (temporary source edit, relevant tests run, edit restored; each FAILED as
  intended): store `get_entry` without component checks (ADV-4/4b); coordinator evicting on
  `unsafe_path` plus store `delete_entry` without checks (ADV-4b); restore taking destinations from
  the manifest (ADV-1, I-14); no blob hash/size check (ADV-3, ADV-7, I-13); `RESTORED_MODE_MASK`
  0o777 (ADV-8b); `STORED_MODE_MASK` 0o7777 (ADV-8a); no sensitive check in `keys.py` (ADV-10) and
  none in `restore.py` (ADV-10 restore-time); preseed dropped at settle (I-11); guard 2, guard 3 and
  guard 1 each removed (I-10, I-19); unexpected error re-raised from `lookup` (I-25); parse boundary
  narrowed to `ValueError` (ADV-9 `deep_nesting_100000`, both harness halves); `O_NONBLOCK` removed
  from `O_SAFE_READ` (ADV-6 hangs, the watchdog fires after 5 s); shadow mode restoring (I-23);
  `BLOB_SWEEP_GRACE_SECONDS = 0` (I-26); TTL never expiring (I-17); injected `true` honoured
  (I-16); usage site A dropping carried spend (I-20); stale records treated as current (I-9; the
  `dispatch_cycle` check alone is covered by the `ended_at` binding, so I-9 only fails when both
  are removed, which is the D14 design); isolation no longer ineligible (I-12). The I-5b
  concurrency proof was checked by running it with `max_parallel=1`: the barrier breaks and the
  test fails. **Not mutation-checked:** I-15 (best-effort by design), the prune-loop smoke of I-26,
  ADV-2's CLI half beyond its positive control, ADV-5's capture-time `output_not_regular_file`
  (guard 1 fires first at integration level; the capture check is pinned by the unit tests).

## Earlier update (2026-10-05, Rev 3: Part 1 reworked)
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
2. ✓ Part 2 done: integration and adversarial tests (see the Part 2 update above)
3. ✓ Part 3 done: e2e suite, CI step, coverage gate, full suite (see the Part 3 update above).
4. Gate G2 (T-fXWbqg) is ready to start; then T-bdQZW4. Parent, at merge time: the HLD 24.2
   table in `HANDOFF.md`.

## Comments
- By: architect · Role: architect · Date: 2026-10-04 · Comment: Status initialized (Draft).
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2 re-plan after the Phase-4 consultation.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 3 re-plan after the early-gate review and the manager's scope decisions (HLD §23.5). State stays `Draft` (Part 1 any time before T-XpF1pF; Parts 2–3 in the hardening phase); this file, `TASK.md`, `HANDOFF.md` (when present) and the epic `STATUS.md` rollup agree.
- By: tester · Role: tester · Date: 2026-10-05 · Comment: Part 1 first delivery (commits 12b85d1, 7f7fdf5) was REJECTED: vacuous poison, one workflow, hand-made goldens. Its "complete" claims are withdrawn.
- By: developer · Role: developer · Date: 2026-10-05 · Comment: Part 1 reworked (commit 8972149): working `find_spec` poison with self-tests and negative controls, ten cache-off workflows in I-1, real base-captured goldens in I-2 (serial and `max_parallel=3`). Task remains In Progress until Part 3.
- By: developer · Role: developer · Date: 2026-10-05 · Comment: G1b carry-over: Part 2 must add I-5b (miss -> pending -> store at max_parallel>1) and I-25 (engine-level unexpected coordinator exception, run completes) (rev S-2). See `T-fXWbqg-cache-review-gates/STATUS.md` (G1b remediation).
- By: developer · Role: developer · Date: 2026-10-05 · Comment: Part 2 done (commit `4d11a69`): I-5b, I-9..I-17, I-19, I-20, I-23, I-25, I-26 and ADV-1..ADV-10 (+ ADV-4b, ADV-9 harness) as 147 passing tests in `tests/cache/test_integration_hardening.py`, `tests/cache/test_adversarial.py` and the helper `tests/cache/_hardening_rig.py`; mutation-checked against the key defences; no production defect found. Task stays In Progress until Part 3.
- By: developer · Role: developer · Date: 2026-10-05 · Comment: Part 3 done and task `Done` (commits `b7489d1` E-1..E-6 + ADV-2 tripwire fix, `a947986` CI step, then docs). 55 e2e tests through `CliRunner`; coverage 98.71% package, keys 100 / store 96 / restore 97 / coordinator 100; full suite 6805 passed, 10 skipped, 0 failed; mutation-checked (see above); the only defect found was a Part 2 test tripwire that broke under `--cov` (fixed in the test; no production change). Epic rollup and the G2 gate row updated to match.
