# STATUS

- ID: `T-Sg6Jf2-swebench-grader-docker`
- Updated At: 2026-07-22
- State: Done
- Owner: developer agent

## This update
- By: developer agent / Role: developer / Date: 2026-07-22
- Comment: Shipped `SweBenchGrader` (`bench/swebench_grader.py`), registered into
  `GRADER_REGISTRY` at import time (mirrors `swebench_provider.py`'s exact mechanism;
  triggered via one new import line in `bench/cli.py`, same place the provider is
  triggered). `git diff HEAD` patch extraction → predictions file → official
  `swebench.harness.run_evaluation` via subprocess (`sys.executable`, never an
  internal import) → report parsing → `resolved`/`solved`. Module-global
  `_DOCKER_EVAL_LOCK` serializes the Docker-eval step (ADR-0009 D4/R1); post-eval
  `docker rmi -f` + `docker builder prune -f` unless `keep_images`. Deviations from
  this task's own TASK.md pseudocode are documented in TASK.md's "Reconciliation
  note" (module path, instance-id derivation, `keep_images` channel, `git diff HEAD`,
  `model_name_or_path`) — all forced by real, verified contract gaps in
  `GraderContext`/`RunContext`/`GraderConfig`, or explicitly authorized by the
  dispatch message.

## Evidence (AC-by-AC, TASK.md numbering)

1. **Golden patch → `solved is True`, `detail["resolved"] is True`; empty diff →
   `solved is False`.** Verified for REAL (opt-in, `AO_E2E_SWEBENCH=1`,
   `@pytest.mark.swebench`):
   `test_real_grade_gold_patch_resolves_smallest_pinned_instance` — a real,
   provider-checked-out `psf__requests-2931` workspace, the real gold patch (from the
   pinned SWE-bench Verified dataset row, fixture-committed at
   `tests/bench/data/psf__requests-2931.gold.patch`) applied via `git apply`, graded
   through a REAL Docker eval (pulled the real ~0.91GiB
   `swebench/sweb.eval.x86_64.requests_1776_requests-2931:latest` image). Result:
   **`solved=True`, `detail["resolved"]=True`**, all `FAIL_TO_PASS`/`PASS_TO_PASS`
   tests passed per the real per-instance `report.json`. Run once by hand, **98.3s**
   wall time (venv/collection + real GitHub clone via the provider's repo cache,
   which already existed from T-Sw5Hd9's own opt-in test + a real Docker pull +
   container run + grading + cleanup). Empty-diff short-circuit:
   `test_grade_empty_patch_short_circuits_before_any_subprocess` proves NO
   docker/harness subprocess is spawned (only the real `git diff HEAD` call is
   allowed through a fake that raises on anything else) and
   `detail={"reason":"empty patch",...}`.
2. **`git -C repo_dir diff <base_commit>` → predictions `model_patch`; empty diff →
   short-circuit, no Docker spawn.** Implemented as `git -C repo_dir diff HEAD` (see
   TASK.md Reconciliation note #4 for the deliberate deviation + rationale).
   `test_extract_patch_captures_unstaged_tracked_edit`,
   `test_extract_patch_captures_staged_edit_too` (proves the deviation's value: a
   bare `git diff` would have missed this),
   `test_extract_patch_excludes_untracked_instruction_md`,
   `test_extract_patch_no_changes_is_empty_not_an_error`,
   `test_extract_patch_git_failure_is_reported`,
   `test_write_predictions_shape` (exact JSON shape:
   `[{"instance_id","model_name_or_path","model_patch"}]`).
3. **Exactly one `instance_id`, per-instance `--timeout`; harness/Docker
   failure/timeout → `GradeResult(solved=False, detail={"eval_error":...})`, never an
   exception past `Grader.grade`.** `test_build_harness_argv_shape` (pure argv
   check), `test_grade_invokes_harness_with_expected_argv_and_cwd` (real git +
   mocked harness, asserts the exact argv AND that `cwd` is the task's own
   `capture_dir/swebench`), `test_grade_harness_nonzero_exit_without_report_is_eval_error`,
   `test_grade_harness_timeout_is_graceful_not_an_exception`. **Note**: the dispatch
   text's one-line summary said "timeout → GraderError"; the authoritative TASK.md
   AC3 ("never an exception past the `Grader.grade` boundary") and `graders.py`'s own
   established convention (every other grader returns a graceful `GradeResult` on a
   timeout/missing-command, never raises) both say a timeout must be a GRACEFUL
   `GradeResult`, so that is what is implemented and tested;
   `GraderError` remains reserved for a genuinely unexpected internal bug (via the
   inherited `Grader.grade` wrapper, never raised directly by this grader).
4. **Disk safety: Docker-eval lock; image + build-cache cleanup after each eval;
   opt-in test asserts disk returns to baseline.**
   `test_grade_serializes_docker_eval_across_threads` (two threads, a shared
   in-flight counter inside the mocked harness call — asserts `max_in_flight == 1`).
   `test_grade_default_keep_images_false_removes_image` /
   `test_grade_keep_images_true_skips_docker_cleanup` /
   `test_grade_keep_images_via_env_var` cover the keep/clean toggle and its three
   resolution channels. Real-world verification: BEFORE the opt-in real test,
   `docker images | grep swebench` showed only the pre-existing, unrelated
   `sympy_1776_sympy-20154` image (2.58GB, leftover from the orchestrator's own
   feasibility probe); AFTER, `docker images | grep swebench` shows the SAME single
   image and nothing else — the `requests_1776_requests-2931` image (freshly pulled,
   ~0.91GiB) was cleanly removed. `docker system df` Images size was unchanged
   (12.53GB before and after) and Build Cache dropped (63 objects/50MB →
   47 objects/0B, i.e. cleanup also reclaimed pre-existing cache); free disk
   unchanged (42G). `sg_mod._docker_image_exists(tag) is False` asserted directly in
   the real test too.
5. **`swebench` extra / docker missing → graceful `GradeResult`, checked up front, no
   traceback.** `test_grade_docker_missing_is_graceful`,
   `test_grade_missing_swebench_extra_is_graceful` (via the standard
   `sys.modules["swebench"] = None` trick, mirroring
   `test_swebench_import.py`'s identical use for `datasets`). Also verified LIVE: a
   throwaway venv built from base `pyproject.toml` (no `swebench` extra, confirmed
   absent via `importlib.util.find_spec`) still imports `agent_orchestrator.bench.cli`
   cleanly, the grader registers, and `ao-bench validate --suite
   benchmarks/suites/swe-verified-mini/suite.json` (via `CliRunner`) exits 0.
6. **`"swebench"` in `KNOWN_GRADER_TYPES`; `swe-verified-mini` validates.** Already
   satisfied by T-Sw5Hd9 (`spec.py` untouched by this task, confirmed by `git diff` —
   zero changes to `spec.py`). `test_swebench_grader_registered_at_import_time`
   confirms `GRADER_REGISTRY["swebench"] is SweBenchGrader`.

## Registration mechanism (as required by the dispatch)
- `register_grader("swebench", SweBenchGrader)` at the bottom of
  `bench/swebench_grader.py` (import-time side effect, identical pattern to every
  other grader in `graders.py` and to `swebench_provider.py`'s own
  `register_workspace_provider` call).
- Trigger: `bench/cli.py` already had `from . import swebench_provider  # noqa: F401`
  as the sole trigger for the (non-default) `swebench` workspace provider (nothing
  else imports that module). Mirrored EXACTLY: added
  `from . import swebench_grader  # noqa: F401 -- registers "swebench" grader
  (T-Sg6Jf2)` immediately above it (`ruff --fix` folded both into one parenthesized
  `from . import (...)` statement — cosmetic only, same two lines/intent).
  `graders.py`'s own 4 built-in graders register via a DIFFERENT path
  (`runner.py` imports `graders.py` directly for `GradeResult`) — `swebench_grader.py`
  needed its own explicit hook because nothing already imports it, exactly the same
  situation `swebench_provider.py` was in.

## Deviations
See TASK.md's "Reconciliation note" for full detail + rationale on each:
1. Module path `bench/swebench_grader.py` (dispatch-specified, not
   `graders_swebench.py` from this task's own original TASK.md).
2. Instance id derived from `repo_dir`'s parent dirname (`task.id`), not
   `task.source.instance_id` — `GraderContext`/`RunContext` do not carry `task`/
   `source` today; widening them touches `runner.py`/`workspace.py`, both forbidden
   for this task. Forward note below.
3. `keep_images` is an env var (`AO_BENCH_SWEBENCH_KEEP_IMAGES`) + test-only
   constructor arg, NOT a `GraderConfig`/suite.json field — `GraderConfig` silently
   drops unmodeled keys (no `extra="allow"`) and `spec.py` is forbidden for this task.
4. `git diff HEAD` (captures staged + unstaged changes against the pinned
   `base_commit`), not a bare `git diff` as T-Sw5Hd9's forward note suggested —
   matches the literal grading promise in every task's own `instruction.md`.
5. `model_name_or_path = "ao-bench"` (stable constant; no subject id on
   `RunContext` today) — explicitly sanctioned by the dispatch message.
6. `benchmarks/suites/swe-verified-mini/suite.json` was **NOT** edited — no field
   was genuinely blocked from working with defaults (`timeout_seconds` already works
   via the existing `GraderConfig` field; `keep_images` cannot be suite-driven at all
   without a `spec.py` edit, so a suite edit would not have helped either).
7. One pre-existing test now fails and was NOT fixed (out of file ownership, no
   carve-out granted): `tests/bench/test_graders.py::test_grader_registry_has_all_mvp_types`
   asserts an exact `set(GRADER_REGISTRY)` equality that is now stale by design.
   See "Test run" below and the Risks section.

## Test run (actual numbers)
- New: `tests/bench/test_swebench_grader.py` — **27 collected** (26 run by default +
  1 `swebench`-marked opt-in, skipped by default without `AO_E2E_SWEBENCH=1`).
  `uv run pytest tests/bench/test_swebench_grader.py -q` → **26 passed, 1 skipped**.
- Scoped bench run: `uv run pytest tests/bench -q --ignore=tests/bench/test_dev_medium_suite.py`
  → **365 passed, 4 skipped, 1 failed** (the one pre-existing, out-of-ownership
  `test_graders.py` closed-set assertion — see Deviations #7 / Risks).
- Full fast suite: `uv run pytest -q -m "not real_llm" --ignore=tests/bench/test_dev_medium_suite.py`
  → **1222 passed, 3 skipped, 4 deselected, 1 failed** vs the stated baseline of
  1197 passed/2 skipped/4 deselected — delta is exactly **+25 passed** (26 new tests
  minus the 1 gated opt-in), **+1 skipped** (that gated opt-in test itself), **zero
  unexplained regressions**, plus the one precisely-diagnosed pre-existing failure
  above.
- Opt-in real test: `AO_E2E_SWEBENCH=1 uv run pytest tests/bench/test_swebench_grader.py -q -m swebench`
  → **1 passed** in **98.3s**, run once by hand as required (full result/disk detail
  in AC1/AC4 above).
- `ruff check` / `ruff format --check` clean on all touched files
  (`swebench_grader.py`, `cli.py`, `test_swebench_grader.py`). `mypy src` (the actual
  CI gate) → the same 4 PRE-EXISTING, out-of-scope `_version.py` errors T-Sw5Hd9
  already documented (confirmed untouched by `git diff`), zero new errors. `mypy` run
  directly against the 3 touched files individually → clean (0 errors on the two
  `src/` files; the test file only surfaces the same pre-existing "missing py.typed
  marker" noise every other `tests/bench/*.py` file exhibits under that same direct
  invocation — not a regression, CI only ever gates `mypy src`).

## Disk footprint (NFR-2 / risk R1 audit)
- Docker: before the opt-in real test, `docker images | grep swebench` = 1 image
  (2.58GB, pre-existing/unrelated). After: still exactly 1 image, same tag/size — the
  freshly-pulled `psf__requests-2931` image (~0.91GiB) was fully cleaned up.
  `docker system df` Images size unchanged (12.53GB); Build Cache dropped from
  63/50MB to 47/0B (cleanup reclaimed pre-existing cache too); host free disk
  unchanged at 42G.
- Repo-local: no new committed binary content; `playground/.tmp/bench/tests/...`
  workspace from the opt-in test is intentionally preserved on disk (mirrors
  `test_workspace.py`/`test_swebench_provider.py`'s own "workspaces preserved for
  audit" convention, not cleaned up by this task).

## Risks / Blockers
- **Cross-file test staleness (not blocking this task's own scope, but blocks a
  fully-green full suite)**: `tests/bench/test_graders.py::test_grader_registry_has_all_mvp_types`
  needs a one-line update (add `"swebench"` to its expected set, or switch to a
  membership/superset check mirroring `test_registries.py`'s/`test_workspace.py`'s
  own safer pattern for `WORKSPACE_PROVIDER_REGISTRY`). This task's dispatch message
  explicitly forbids editing `graders.py`/"all other test files" with no carve-out
  (unlike `cli.py`/`registries.py`, which do have one), so it was deliberately left
  untouched and reported here instead of silently overstepping file ownership.
- **Forward risk for T-Cm9Tb4 (large-tier campaign, 3 subjects × 10 instances)**: see
  Next actions below for the `keep_images` tradeoff.
- Harness/host variance (R2): out of this task's control beyond the timeout + graceful
  degradation already implemented; the official `resolved` status remains
  authoritative per ADR-0009.

## Next actions (forward notes)
1. **For T-Cm9Tb4 (large-tier campaign recipes)**: a 3-subject run against the same
   10 pinned instances will re-pull each ~1GB image up to 3×  (once per subject) if
   `keep_images` stays at its default `False`. Setting
   `AO_BENCH_SWEBENCH_KEEP_IMAGES=1` for the whole campaign trades ~10GB of images
   held on disk for the run's duration (well within the ~37GB budget for 10
   instances at ~1GB each) against 2 re-pulls × 4min × 10 instances saved
   (~80min). Recommend the `make bench-large` recipe set this env var, OR
   pre-pull all 10 images once (`docker pull` in a setup step) before the campaign
   and leave `keep_images=False` so each subject's run still self-cleans (simpler,
   same disk profile, no code change needed either way since the pre-pulled image
   would just get removed and re-pulled by the FIRST subject, then removed and
   re-pulled again... actually pre-pulling does NOT help under `keep_images=False`;
   only the env var actually saves re-pulls across subjects). **Recommend the env
   var for the PLAN/campaign run specifically**, default `False` everywhere else.
2. **For the PLAN large-tier run**: set `AO_BENCH_SWEBENCH_KEEP_IMAGES=1` before
   invoking `ao-bench campaign`/`ao-bench run` against `swe-verified-mini` if the
   plan re-runs multiple subjects against the same suite; unset (or leave default)
   for a single-subject smoke run to keep disk fully clean per instance.
3. **Contract-widening (out of this task's scope)**: a future task should widen
   `RunContext`/`runner.py`'s call site to carry `task`/`task.source` through to
   `Grader.grade` (TASK.md's own originally-noted gap) — this would let a future
   grader avoid the `repo_dir`-parent-dirname derivation this task uses instead, and
   would also be the natural place to add a real `GraderConfig.extra="allow"` (or a
   dedicated `keep_images` field) so `keep_images` can become suite-driven.
4. **Test fix owed**: `tests/bench/test_graders.py::test_grader_registry_has_all_mvp_types`
   (one line, see Risks above).
