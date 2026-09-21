# REWORK NOTES B — gate/guard half of T-Ee3Mn8

- By: developer-agent (rework pass B) · Role: developer · Date: 2026-09-07
- Ticket: `T-Ee3Mn8-e2e-and-review` (epic `E-Wk9Tz3-task-isolation`), branch `ad/task-isolation`
- Answers REVIEW.md findings **M-1, M-3, M-4, W-4, W-6**
- Files owned by this pass (the only files edited):
  - `tests/test_nfr2_regression_gate.py`
  - `tests/isolation/test_security_guards.py`
- **No `src/` change.** `git diff -- src/` from this pass is empty; every mutation experiment
  below ran against a throwaway copy of `src/agent_orchestrator/` in a scratch directory, not
  against the working tree (four other agents were live in `src/` during this pass).
- Everything below that belongs in `STATUS.md` / `TASK.md` is recorded here instead, per the
  brief — a concurrent agent owns those two files plus `tests/test_e2e_isolation.py`, and this
  pass did not touch any of the three.

---

## 1. M-1 — AC-1 re-added as a real gate (blocking)

### What changed

`tests/test_nfr2_regression_gate.py` now holds **two** gates, each named for what it measures:

| Gate | Test | Measures |
|---|---|---|
| 1 (AC-1) | `TestPreEpicTestsUnedited` (3 tests) | The pre-epic test suite is **unedited**. |
| 2 | `test_isolation_subsystem_untouched_on_the_non_isolated_path` | The isolation subsystem is never constructed or observed on the non-isolated path. |

Gate 2 is the former `test_nfr2_golden_compare_identical_state_events_and_artifacts`, kept
verbatim (the reviewer proved all three of its comparison arms fire) and **renamed only**, so it
no longer reads as though it discharges AC-1. Its docstring now says so explicitly.

### What the AC-1 gate asserts

`test_every_pre_epic_test_file_is_byte_identical_to_its_pre_epic_content`:

1. Resolves the pre-epic base **in-test**, never as a hardcoded sha:
   `git merge-base HEAD ad/multi-workspace-service` (constant `_PRE_EPIC_BASE_BRANCH`, with an
   `origin/…` fallback). Verified equivalent to the other derivation the brief allowed — the
   epic's first commit is `5ff3cc0`, and `5ff3cc0^ == e193ead == merge-base`.
2. Enumerates **every** path that existed under `tests/` at that base
   (`git ls-tree -r -z --name-only <base> -- tests/` → 109 paths, of which 104 are `.py`), and
   asserts each is byte-identical to `git cat-file blob <base>:<path>` — raw bytes, no `text=`
   decoding, so a non-UTF-8 fixture blob is compared correctly too.
3. Reports **deletions** as well as edits — removing a pre-existing test fails the gate the same
   way editing one does.
4. Has a floor (`_MIN_PRE_EPIC_TEST_FILES = 50`) so a broken enumeration cannot make it vacuous.
5. Skips (does not pass) only when the question is unanswerable in the checkout — no git binary,
   not a work tree, base branch absent (installed sdist, shallow CI clone).

Two companions, deliberately separate tests so a stale-list problem cannot mask a real edit:

- `test_the_gate_reports_a_tampered_pre_epic_file` — non-vacuity, expressed as a **delta**
  against the untampered baseline so it stays valid even on a tree where the blocking gate is
  legitimately red.
- `test_no_stale_exception_entries` — every declared exception must still name a real pre-epic
  path that really does still differ.

### The named exceptions it allows (and why)

Recorded here as the brief requires. All five are pre-epic files this epic modified in *committed*
work by other tasks; each is declared in `_EPIC_MODIFIED_PRE_EPIC_TESTS` with the same reason.

| Pre-epic test file | Owning task | Diff vs base | Justification |
|---|---|---|---|
| `tests/test_budget.py` | T-Ac6Vd9 | +117 / −5 | R-1b re-keyed `BudgetCounters.charged_estimate` from the bare task id to `cycle_key(task_id, dispatch_cycle)` so a ladder redispatch of the same task is independently chargeable. These tests assert the **key literal**, which is part of the changed contract. Every numeric total they assert is unchanged — NFR-2's promise is about the numbers, not the dict key shape. |
| `tests/test_engine_budget.py` | T-Ac6Vd9 | +25 / −8 | Same cycle-keyed ledger: the resume double-charge guard now looks up `cycle_key(task, cycle−1)`, so the fixture must seed a `TaskRunState` with `dispatch_cycle` and go through `prepare_resume`. |
| `tests/test_runstate.py` | T-Ac6Vd9 | +76 / −0 | **Additive only** — two new tests that `cumulative_*` usage survives `prepare_resume` for a requeued task. No pre-existing assertion touched. |
| `tests/test_project_config.py` | T-Cx4Jf1 | +101 / −0 | **Additive only** — `IsolationConfig` schema tests for the new `.ao/config.yaml` `isolation:` block. |
| `tests/test_builtin_routed_runner_assets.py` | T-Tp7Zs2 / T-Lr6Ka3 | +254 / −0 | The builtin routed-runner template gained a `merge-resolver` agent, and this file's required-agents-match-DAG test is an **exact set equality**, so it had to learn the new agent. The rest of the change is additive. |

Three of the five are strictly additive; only the two T-Ac6Vd9 files change an existing
assertion, and both changes are the deliberate R-1b contract change.

Not exceptions, for the record:
- `tests/isolation/*`, `tests/test_e2e_cli_prune_worktrees.py`, `tests/test_isolation_events.py`,
  `tests/test_prune_worktrees.py` — **created by this epic**, so they were never pre-epic and need
  no exception (this covers W-8's `tests/isolation/test_resolvers.py`; see §5).

### Before/after pass counts (AC-1's "documented, reproducible")

Recorded 2026-09-07. Reproduction command is in the module docstring of
`tests/test_nfr2_regression_gate.py`; the base worktree it creates was removed afterwards
(`git worktree list` shows only the main checkout).

| | Result |
|---|---|
| **BEFORE** — base `e193ead`, its own code (`PYTHONPATH=<base-wt>/src`), the 104 pre-epic `.py` test files | **2006 passed, 7 skipped, 2 failed** |
| **AFTER** — this branch's code, the same 104 files | **2045 passed, 7 skipped, 0 failed** |

The delta reconciles exactly, with nothing unexplained — `2006 + 37 + 2 = 2045`:

- **+37** — the five exception files collect 96 tests at base and 133 today. All additive; no
  pre-epic test was removed, skipped or weakened.
- **+2** — the two BEFORE failures are artifacts of the throwaway base worktree, **not** of base
  code. Both are `tests/bench/test_dev_{core,medium}_suite.py::test_fake_subject_full_suite_run_
  produces_valid_run_json_and_summary`, whose pytest grader shells out to `uv run pytest` and
  returns `error: Failed to spawn: 'pytest' — No such file or directory` because that scratch
  checkout has no venv with pytest in it (captured from the run record's `grader_raw_tail`).
  `git diff <base> HEAD -- src/agent_orchestrator/bench/` is **empty**, so base and HEAD run
  byte-identical grader code and neither result is attributable to this epic.

### The gate was proved against real edits, not just fixtures

| Live mutation (applied, run, restored; sha256 re-verified identical) | Result |
|---|---|
| Append one comment line to `tests/test_dag.py` (a pre-epic file) | **caught** — `edited ['tests/test_dag.py']`, exactly one test red |
| Move `tests/test_dag.py` out of the tree | **caught** — `deleted ['tests/test_dag.py']` |
| Clean tree | 7 passed |

Both experiments left `tests/test_dag.py` byte-identical (`git status --porcelain` clean).

### Coordination note for whoever merges this

The gate covers **all** pre-epic files under `tests/`, including `tests/ui/*` (13 files) and
`tests/test_engine.py`. Concurrent agents are working in `tests/ui/` for T-Cx4Jf1 Part B. If that
work edits a pre-epic `tests/ui` file, this gate will go red **by design** — the fix is either to
revert the edit or to add a justified entry to `_EPIC_MODIFIED_PRE_EPIC_TESTS`, not to weaken the
gate. That is exactly the AC-1 behaviour the review asked to restore.

---

## 2. M-4 + W-6 — the subprocess-git guard, repaired

### What changed in `tests/isolation/test_security_guards.py`

Two complementary guards replace the single defeatable one, and the module docstring now states
each one's real scope (the old docstring claimed `src/`-wide while the sweep was `isolation/`-only).

**Guard 1 — alias-aware call-site scan, now genuinely `src/`-wide.**
`_find_subprocess_git_argv` resolves:
- import bindings — `import subprocess`, `import subprocess as _sp`, `from subprocess import run`,
  `from subprocess import run as _r` (`_subprocess_bindings`);
- argv held in a variable — any name ever assigned a `["git", …]` literal (`_git_argv_variable_names`,
  deliberately flow-insensitive: over-approximating can only make the guard stricter);
- the `shell=True` string form and f-string form, and the `args=` keyword spelling of argv.

Scope is now all of `src/agent_orchestrator/` with a **named, per-file, justified allowlist**
(`_GIT_CALL_ALLOWED`) rather than a silent `isolation/`-only narrowing — so the reviewer's
mutation #8 (a git shell-out in `engine.py`) is no longer out of scope. The six allowlisted files
are `isolation/git.py` (`_SubprocessRunner`, the sanctioned runner) plus five pre-epic,
outside-the-isolation-boundary callers: `spec.py` (reposet V8 probe), `_version.py` (version
stamping), `bench/runner.py`, `bench/swebench_provider.py`, `bench/swebench_grader.py`.
`test_every_allowlisted_file_still_calls_git` keeps that list from going stale.

**Guard 2 — alias-proof importer-set equality, `isolation/`-only.**
The exact set of modules under `isolation/` that import `subprocess` in *any* form must equal
`{git.py, resolvers.py, integrator.py}`. Because it is an import-binding fact rather than a
call-site pattern, no spelling of a call can evade it.

**W-6 — the non-vacuity proof now proves the guard, not the visitor.**
`test_the_guard_catches_every_known_bypass_spelling` writes six fixture modules — one per bypass
spelling, including the three the reviewer used to defeat the old guard — plus a **negative
control** (`_clean.py`: `subprocess.run(rule.command)` and `subprocess.run([tool, '--version'])`),
and runs the *same* `_scan_for_subprocess_git_argv` the real gate calls. It asserts set equality of
the flagged files, so a guard that flagged everything would fail it too.
`test_the_allowlist_is_honoured_but_not_a_blanket_exemption` proves the allowlist suppresses
exactly its own entries. Guard 2 has the matching detector proof
(`test_importer_detection_sees_every_import_spelling`).

### Mutation results for the repaired guards

Method: `src/agent_orchestrator/` copied to a scratch directory; each snippet appended to the
named module **in the copy**; both guards run against the copy; the copy restored and the baseline
re-verified clean before and after. The working tree's `src/` was never modified.

| # | Mutation | Old guard | Repaired guards |
|---|---|---|---|
| 1 | `import subprocess` + `subprocess.run(["git","status"])` → `isolation/escalation.py` | caught | **caught** (guard 1 + guard 2) |
| 2 | `import subprocess as _sp` + `_sp.run([...])` → `isolation/escalation.py` | **passed** ❌ | **caught** (guard 1 + guard 2) |
| 3 | `from subprocess import run as _r` + `_r([...])` → `isolation/escalation.py` | **passed** ❌ | **caught** (guard 1 + guard 2) |
| 4 | `_argv = ["git","push","--force"]` + `subprocess.run(_argv)` → `isolation/escalation.py` | **passed** ❌ | **caught** (guard 1 + guard 2) |
| 5 | `subprocess.run(["git","status"])` → **`engine.py`** (was out of scope) | **passed** ❌ | **caught** (guard 1) |
| 6 | alias variant inside `isolation/integrator.py`, a module that **already** imports `subprocess` (guard 2 cannot help) | n/a | **caught** (guard 1) |
| 7 | variable-argv variant inside `isolation/integrator.py` | n/a | **caught** (guard 1) |
| 8 | `subprocess.run('git push --force', shell=True)` → `isolation/locks.py` | n/a | **caught** (guard 1 + guard 2) |

All five reviewer cases plus three harder ones are caught; nothing is left documented as
out-of-scope. Baseline (unmutated `src/`) is clean under both guards.

Residual, honestly stated limits (in the module docstring): the allowlist is per-file, so a *new*
git call added inside one of the six allowlisted files is not flagged; and a fully dynamic argv
built at runtime (`[prog] + args` where `prog` comes from config) is not statically decidable.
Guard 2 covers the isolation package against the second case by refusing new `subprocess`
importers there at all.

---

## 3. M-3 — AC-19 (R-12): the real collision measurement

**This is a measurement, not a test.** The tautological assertion in
`tests/test_e2e_isolation.py` is being removed by the concurrent agent; nothing was added here to
replace it. Record the block below in `STATUS.md` as a named first-adoption risk.

### Method

1. The consumer is `../ao-runner-finplan`. That directory is **not itself a git repo**; the repo
   an isolated run would integrate into is its primary reposet repo,
   `../ao-runner-finplan/fin_plan` (branch `epic/e-yqqn8h-multiple-binary-split`, HEAD `bd5de534`,
   2026-08-08).
2. Dirty-file snapshot: `git status --porcelain` in that repo on **2026-09-07** → **4106 entries**
   (matching AC-19's "~4106"; snapshot saved outside the repo, not committed). Composition:
   **all 4106 are `D` (deleted-not-staged)** — 4090 under `pipeline/`, 16 under `old_assets/`.
   It is one bulk directory deletion, not scattered edits.
3. "Path set a representative isolated run would integrate": the consumer's real ao epic runs, one
   per epic branch. For each of the **16** `epic/*` branches,
   `git diff --name-only $(git merge-base master <branch>) <branch>` — 34–140 paths per run,
   **873 unique paths** across all 16.
4. Collision = `dirty ∩ integrated`, the same intersection `_sync_checkout` computes before it
   refuses a sync. Also computed at task granularity: the 52 individual commits on the most recent
   epic branch.

### Result

| Measure | Observed |
|---|---|
| Dirty paths in the snapshot | **4106** |
| Unique integrated paths across 16 real runs | **873** |
| Dirty ∩ integrated | **0** |
| **Observed collision rate (dirty paths hit)** | **0 / 4106 = 0.00 %** |
| **Runs that would have been refused a checkout sync** | **0 / 16 = 0 %** |
| Task-level commits colliding (52 commits, latest epic) | **0 / 52 = 0 %** |

### Named first-adoption risk (for `STATUS.md`)

> **R-12 first-adoption risk — dirty-checkout collisions (measured, weak evidence).**
> Measured 2026-09-07 against `ao-runner-finplan/fin_plan` @ `bd5de534`: **0 collisions**
> (0.00 %) between a real 4106-entry `git status --porcelain` dirty set and the 873 paths that
> 16 real ao epic runs integrated; 0 of 16 runs, and 0 of the latest epic's 52 commits, would
> have had a checkout sync refused. **This does not show that dirty checkouts are generally
> safe.** The snapshot is a single, degenerate shape: one bulk deletion of `pipeline/` (4090) and
> `old_assets/` (16), directories ao work does not live in — its integrated paths are 641 under
> `src/`, 110 under `ad/`, 51 `scripts/`, 25 `tests/`, 23 `docs-md/`. The disjointness is
> circumstantial, not structural: one epic run *did* touch `pipeline/local-test/
> docker-compose.test.yml`, the same top-level directory as 4090 of the 4106 dirty entries, and it
> missed only because that specific file was not among the deleted ones. An operator with
> uncommitted work under `src/` would see a very different number. The design's decision not to
> stash the operator's uncommitted work is **not** contradicted by this measurement, but neither
> is it validated by it; re-measure against a second consumer, or against a snapshot taken
> mid-feature-work, before treating 0 % as the expected rate.

---

## 4. W-4 — `tests/isolation/test_conflict_fixtures.py` docstring correction (record only)

That file belongs to neither agent in this rework, so nothing was edited. The corrections to apply:

- **Module docstring is factually wrong.** It claims the tests are *"deterministic unit tests (no
  real git subprocess, everything pre-staged), so they're fast"*. Every test in the file shells out
  to real `git` (`git checkout`, `git rebase`, `git merge-base`, `git rev-list`). Suggested
  replacement wording: *"These tests are deterministic **integration** tests: they drive real
  `git` against tiny throwaway repos under `tmp_path` with a fixed clock and a scrubbed git
  environment. Everything is pre-staged, so they are fast (~Xs) despite the real subprocesses."*
- **`TestConflictFixtureDeterminism::test_conflict_repo_fixture_is_idempotent` does not test
  idempotency** — it counts the commits on `ours`/`theirs`. Rename to
  `test_each_branch_has_exactly_one_commit`, or add a genuine idempotency assertion (build the
  fixture twice and compare tree hashes).

---

## 5. Items for the merged `STATUS.md` / `TASK.md`

Recorded here because this pass may not touch those files.

1. **W-8 declaration.** `tests/isolation/conftest.py` gained `"lock"` to `CONFLICT_KINDS`, which
   forced an edit to `tests/isolation/test_resolvers.py` (its `test_all_conflict_kinds_are_covered`
   guard and the `test_kind_outcome` parametrisation). Both files were **created by this epic**, so
   AC-1's "pre-existing test" language does not bite and the new gate does not flag them — but the
   ownership-boundary crossing still belongs in `STATUS.md`, with the reviewer's suggestion that a
   separate `MECHANICAL_KINDS` constant would have avoided the coupling.
2. **AC-1 is now discharged by an automated gate**, not by a shell-out: cite
   `tests/test_nfr2_regression_gate.py::TestPreEpicTestsUnedited`, the before/after counts in §1,
   and the five declared exceptions.
3. **AC-19 is now discharged by the measurement in §3**, which is the number to paste into
   `STATUS.md`. The e2e file's tautological assertion is being deleted by the other agent.
4. **N-2 correction:** this pass's two files contribute **18 tests** (7 in
   `test_nfr2_regression_gate.py`, 11 in `test_security_guards.py`), all passing, 0 xfailed. Ruff
   check and ruff format are clean on both.

---

## 6. Gates run for this pass

- `ruff check` + `ruff format --check` on both owned files — **clean**.
- Both owned files, **7 runs** across 4 configurations (three plain runs, one `-p no:randomly`, one
  per-file-in-its-own-process, one with the two files in reversed order): **18 passed** every time,
  no order dependence. Note: `pytest-randomly` is **not installed** in this venv
  (`find_spec("pytest_randomly") is None`), so "default order" is collection order and
  `-p no:randomly` is a no-op here; order variation was obtained by reordering the file arguments
  and by running each file in a separate process instead.
- `mypy` on the two files is outside the project's configured scope (CLAUDE.md's gate is
  `mypy src`); run directly it emits only `import-untyped` notes for `agent_orchestrator.*` (no
  `py.typed` marker) plus pre-existing `unused-ignore` notes on the untouched monkeypatch block —
  no new error introduced by this pass.
- **Full suite: `98 failed, 3616 passed, 7 skipped`** (142 s) against the stated baseline of
  3678 passed / 7 skipped / 0 failed. **None of the 98 is in either owned file, and none is caused
  by this pass.** Attribution, proved rather than asserted — the identical suite re-run with
  `--ignore` on both owned files gives **`98 failed, 3654 passed, 7 skipped`**: the same 98
  failures, in the same 11 files, in the same per-file distribution:

  | File (all owned by concurrent agents) | Failures |
  |---|---|
  | `tests/test_engine_isolation.py` | 27 |
  | `tests/test_e2e_isolation.py` | 24 |
  | `tests/test_engine_workspace_lock_sync.py` | 14 |
  | `tests/test_engine_conflict_escalation.py` | 13 |
  | `tests/test_engine_isolation_accounting.py` | 8 |
  | `tests/test_cli_isolation_flags.py` | 3 |
  | `tests/test_e2e_cli_isolation.py`, `tests/isolation/test_ladder_e2e.py`, `tests/isolation/test_integrator.py`, `tests/isolation/test_escalation.py` | 2 each |
  | `tests/test_conflict_instructions.py` | 1 |

  Root cause of 69 of the 98: a **new, uncommitted** `ConfigError` in
  `src/agent_orchestrator/isolation/paths.py:238` — *"worktree root … (from `$AO_STATE_DIR`) lies
  inside the workspace root … Task worktrees must live entirely outside the workspace"* — which
  rejects the fixture shape those suites use. That is live T-Cx4Jf1 Part B / security-hardening
  work in `src/`, which this pass is forbidden to touch and did not touch. The remaining 29 are
  `AssertionError`s in the same in-flight isolation suites. The two full-suite runs also differ in
  pass count (3616 vs 3654) purely because other agents added and fixed tests between them.

  Note for the owning agents: once `paths.py:238` settles, re-run the suite — this pass's two files
  are order-independent and read-only, so they cannot be implicated.
- No commit created. This pass made **no `src/` edit at all**; the uncommitted changes present in
  `src/agent_orchestrator/{cli,engine}.py` on this tree belong to the concurrent T-Cx4Jf1 Part B
  agents and predate this pass.
