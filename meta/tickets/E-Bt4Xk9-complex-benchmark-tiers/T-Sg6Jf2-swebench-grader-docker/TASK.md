# TASK: T-Sg6Jf2-swebench-grader-docker

## Metadata
- Task ID: `T-Sg6Jf2-swebench-grader-docker`
- Epic ID: `E-Bt4Xk9-complex-benchmark-tiers`
- Owner: developer agent
- Created: 2026-07-22
- Last Updated: 2026-07-22
- Status: Done
- Estimate: 3.0 days

## Requirements Mapping
- FR-6 (SweBenchGrader: Docker eval on extracted patch; `resolved`=solved; per-instance timeout; disk cleanup; Docker-eval lock)

## Reconciliation note (as-built vs this doc's original plan)
- By: developer agent / Role: developer / Date: 2026-07-22
- Comment: the orchestrator's dispatch message (issued after this TASK.md was drafted,
  carrying verified ground-truth probes: the real harness CLI shape, report
  filenames/schema, image-tag naming) superseded some of this doc's own pseudocode.
  Deviations, all either explicitly authorized by that dispatch or forced by a real,
  verified contract gap:
  1. **Module path**: `src/agent_orchestrator/bench/swebench_grader.py` (not
     `graders_swebench.py`) — per the dispatch's explicit deliverable text.
  2. **Instance identity**: `GraderContext`/`RunContext` carry no `task`/`source`
     today (verified by reading `runner.py`/`workspace.py`, both read-only/forbidden
     for this task) — `task.source.instance_id` is NOT available to `Grader.grade`.
     Instead of widening those two files, the grader derives the instance id from an
     invariant they already guarantee: `repo_dir`'s PARENT directory name is always
     `task.id` (`runner.py`'s `ws_path = subject_ws_root / task.id`), and
     `task.id == source.instance_id` for the committed suite (verified against the
     real `suite.json`). Flagged as a forward note for a future contract-widening
     task rather than a `runner.py`/`workspace.py` edit here.
  3. **`keep_images` is NOT a `GraderConfig` field**: `GraderConfig` (`bench/spec.py`,
     forbidden to edit in this task) has no `extra="allow"` — an unmodeled key in a
     suite's `grader:{...}` block is silently DROPPED by pydantic (empirically
     verified) before this grader ever sees it, so a suite-level `keep_images` field
     could not work without a `spec.py` edit. Implemented instead as an
     `AO_BENCH_SWEBENCH_KEEP_IMAGES` env var (config/env, CLAUDE.md-sanctioned) +
     a `SweBenchGrader(keep_images=...)` constructor arg (tests only). No
     `benchmarks/suites/swe-verified-mini/suite.json` edit was made — `timeout_seconds`
     already works via the existing `GraderConfig.timeout_seconds` field, and
     `keep_images` genuinely cannot be threaded through the suite file today.
  4. **Patch extraction is `git diff HEAD`, not a bare `git diff`** (T-Sw5Hd9's own
     forward note suggested the latter). `HEAD` is pinned/detached at exactly
     `base_commit`, so `git diff HEAD` captures BOTH staged and unstaged changes
     against that immutable baseline — matching, literally, what every task's own
     `instruction.md` promises the agent ("your changes are graded by diffing the
     working tree against the original checkout"). A bare `git diff` only shows
     unstaged changes and would silently drop a fix the agent happened to `git add`.
     Both forms equally exclude the untracked `INSTRUCTION.md`.
  5. **`model_name_or_path` is the stable constant `"ao-bench"`**, not a subject id —
     `RunContext` has no subject-id field today; explicitly sanctioned by the
     dispatch note ("if the subject id isn't available, use a stable constant... and
     document").
  6. **One pre-existing, out-of-ownership test now fails**:
     `tests/bench/test_graders.py::test_grader_registry_has_all_mvp_types` asserts
     `set(GRADER_REGISTRY) == {"pytest","command","file_assertion","fake"}` — an
     exact-equality check that is now stale by design (this task correctly adds a
     5th type). `graders.py`/"all other test files" are forbidden for this task with
     no carve-out (unlike `cli.py`/`registries.py`), so this was NOT edited; flagged
     here and in the epic-level report instead. One-line fix needed (add
     `"swebench"` to the expected set, or switch to a superset/membership check
     mirroring `test_registries.py`'s/`test_workspace.py`'s own safer pattern for
     `WORKSPACE_PROVIDER_REGISTRY`).

## Description
Grade a SWE-bench instance with the **official** `swebench` evaluation harness. After the subject mutates `ws/repo`, extract the patch (`git diff` against `base_commit`), write a predictions file, run `swebench.harness.run_evaluation` in Docker for that single instance, parse the produced report → `resolved` → `solved`. Serialize the Docker-eval step behind a **module-global lock** (so agent runs can parallelize via T-Pl3Rx7 but only one Docker eval runs at a time — disk safety on ~37 GB), enforce a per-instance timeout, and **delete the instance image + prune build cache after each eval**. `swebench` is lazy-imported; a missing extra / Docker degrades to a graceful `GradeResult(solved=False, ...)` with a clear reason, never a crash.

## File ownership (exclusive)
- `src/agent_orchestrator/bench/graders_swebench.py` — NEW: `SweBenchGrader`. Registers `"swebench"` into `GRADER_REGISTRY`.
- `src/agent_orchestrator/bench/spec.py` — add `"swebench"` to `KNOWN_GRADER_TYPES` (one-line; sequenced AFTER T-Sw5Hd9's spec.py edit).
- `src/agent_orchestrator/bench/registries.py` — (registration is import-time in graders_swebench; only touch if a helper is needed — sequenced after T-Sw5Hd9).
- (read-only) `bench/graders.py` (`Grader` ABC, `GradeResult`), `bench/workspace.py` (`ws/repo`), `bench/spec.py` `Source`.

## Inputs / Outputs
- Inputs: mutated `ws/repo` (subject's edits), `task.source` (`instance_id`, `dataset`, `revision`, `base_commit`), per-instance timeout.
- Outputs: `GradeResult{solved = resolved, score = 1.0/0.0 (or FAIL_TO_PASS fraction), detail = {resolved, patch_size, eval_report_path, ...}, raw_tail}`.

## Acceptance Criteria
1. **Given** a `ws/repo` with a known-correct golden patch for a pinned instance **When** `SweBenchGrader.grade` runs (opt-in `swebench` marker, Docker present) **Then** `solved is True` and `detail["resolved"] is True`; **Given** an empty/no-op diff **Then** `solved is False`.
2. Patch extraction is `git -C ws/repo diff <base_commit>` → a non-empty unified diff written to the predictions file as `model_patch`; an empty diff short-circuits to `solved=False, detail={"reason":"empty patch"}` (no Docker spawn).
3. The eval runs the official harness for **exactly one** `instance_id` with a per-instance `--timeout`; a harness/Docker failure or timeout → `GradeResult(solved=False, detail={"eval_error":...})`, never an exception past the `Grader.grade` boundary.
4. **Disk safety:** the Docker-eval step is serialized behind a module-global lock; after each eval the instance image is removed and build cache pruned; an opt-in real single-instance test asserts `docker system df` returns to ~baseline afterward (peak bounded, does not exhaust the ~37 GB volume).
5. **Given** the `swebench` extra is not installed OR `docker` is not on PATH **When** grade runs **Then** `GradeResult(solved=False, detail={"reason":"swebench extra/docker unavailable"})` with a clear message (checked-up-front, no traceback) — so a non-swebench suite/CI is never affected.
6. `"swebench"` is in `KNOWN_GRADER_TYPES`; a `swe-verified-mini` suite validates. SI-1 preserved.

## Risks
- **Disk exhaustion (R1):** the central risk. Serialize eval + delete image + prune after every instance; never let two evals hold images at once. Peak ≈ 1 image (~1–2 GB) + build cache (~3–5 GB). Document and test.
- Predictions-file / report-path contract: the harness writes `evaluation_results/<run_id>/...` (or a report json keyed by `model_name_or_path`); parse the `resolved` flag from the official report structure, and pin the `run_id` per grade call to avoid collisions under (serialized) evals.
- Harness API drift: call `swebench.harness.run_evaluation` via subprocess (`python -m swebench.harness.run_evaluation ...`) rather than importing internals, so a minor `swebench` version bump does not break the import graph; parse the on-disk report. Pin `swebench` version in the extra.
- `git diff` must exclude untracked noise (`.orchestrator/`, workspace scaffolding) — diff only tracked files against base_commit, or add a targeted pathspec; ensure the agent's real code edits are captured and bench scaffolding is not.

## Pseudocode / Algorithm
```text
_DOCKER_EVAL_LOCK = threading.Lock()
class SweBenchGrader(Grader):
    def _grade(self, cfg, ctx):
        if shutil.which("docker") is None: return GradeResult(False, 0.0, {"reason":"docker unavailable"})
        try: import swebench  # noqa
        except ImportError: return GradeResult(False, 0.0, {"reason":"swebench extra not installed"})
        src = ctx.task.source                     # instance_id, dataset, revision, base_commit
        patch = run(["git","-C",ctx.repo_dir,"diff",src.base_commit]).stdout
        if not patch.strip(): return GradeResult(False, 0.0, {"reason":"empty patch"})
        run_id = f"aobench-{src.instance_id}-{short_uuid}"
        preds = write_json_tmp([{ "instance_id": src.instance_id,
                                  "model_name_or_path": "ao-bench",
                                  "model_patch": patch }])
        with _DOCKER_EVAL_LOCK:                    # disk safety: one Docker eval at a time
            rc = run_bounded(["python","-m","swebench.harness.run_evaluation",
                              "--dataset_name", src.dataset, "--split","test",
                              "--predictions_path", preds, "--run_id", run_id,
                              "--instance_ids", src.instance_id, "--max_workers","1",
                              "--timeout", str(cfg.timeout_seconds or DEFAULT)],
                             timeout=hard_timeout)
            report = _read_report(run_id, src.instance_id)   # official report json
            _cleanup_images(src.instance_id)                 # docker image rm + docker builder prune -f
        if rc.timed_out or report is None:
            return GradeResult(False, 0.0, {"eval_error": rc.summary, "timed_out": rc.timed_out}, tail(rc.output))
        resolved = bool(report.get("resolved"))
        return GradeResult(solved=resolved, score=1.0 if resolved else 0.0,
                           detail={"resolved":resolved,"patch_bytes":len(patch),"run_id":run_id}, raw_tail=tail(rc.output))
register_grader("swebench", SweBenchGrader)
```
Note: `GraderContext` currently exposes only `repo_dir`; this grader also needs `task.source`. Extend the grader call path minimally — either pass the task's `source` via `GraderConfig`/context, or widen the local `GraderContext` Protocol to expose the task (coordinate the tiny contract change; keep it additive and backward-compatible).

## Schemas / Interface Notes
- `grader.type = "swebench"` (config-driven; no per-task command needed — the instance drives it).
- Optional dep: reuses the `swebench` extra from T-Sw5Hd9 (pin `swebench==<X>`).
- Triggers/events: `bench.task.end` already carries solved/cost; add `resolved` into grader `detail`.
- Artifacts: predictions file + eval report under the gitignored workspace/capture (pointer in `run.json`, not committed).

## Handoff Boundary
- Upstream: T-Sw5Hd9 (provider, source shape, optional extra), T-Wp4Nz5 (grader/registry seam), T-Pl3Rx7 (task-level pool — this task adds the ORTHOGONAL Docker-eval lock).
- Downstream: T-Cm9Tb4 (large-tier campaign uses this grader), T-Ts0Xn5 (swebench-marked tests), the PLAN large-tier run.

## Artifacts
- Docs/comments: this folder. Large outputs: eval reports are gitignored workspace artifacts (not committed).
