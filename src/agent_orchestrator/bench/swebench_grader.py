"""`SweBenchGrader` (E-Bt4Xk9 T-Sg6Jf2, FR-6, ADR-0009 D2/D4).

Grades a SWE-bench instance by extracting the subject's patch from the mutated
workspace (`git diff` against the pinned `base_commit`), writing it to a predictions
file, invoking the **official** `swebench.harness.run_evaluation` in Docker for that
ONE instance (subprocess, `sys.executable -m swebench.harness.run_evaluation` -- never
importing harness internals, so a minor `swebench` version bump can't break this
module's import graph, per TASK.md's "Harness API drift" risk), and parsing the
official report -> `resolved` -> `solved`. `swebench` is lazy-imported (module scope
never touches it -- SI-1/NFR-1: `agent_orchestrator.bench.cli` stays importable
without the optional extra); `docker`/the extra missing degrade to a graceful
`GradeResult(solved=False, ...)`, never a crash (AC5).

Docker-eval serialization (ADR-0009 D4, R1 disk safety): `_DOCKER_EVAL_LOCK` is a
module-global lock held for the harness subprocess call + the post-eval image
cleanup, so agent runs can parallelize (T-Pl3Rx7) while at most one Docker eval (and
one instance image) is ever in flight. After each eval the instance image is removed
(`docker rmi -f`) and the build cache pruned, unless `keep_images` says otherwise (see
below) -- `docker images left on disk` is always reported in `GradeResult.detail`.

Two contract notes, both forced by this task's file-ownership boundary (`runner.py`,
`workspace.py`, `spec.py` are read-only/forbidden here -- see TASK.md/dispatch):

1. **Instance identity.** `Grader.grade(cfg, ctx)` (`bench/graders.py`) hands this
   grader a `GraderConfig` + a `GraderContext` Protocol that today declares only
   `repo_dir` (the real `RunContext` also has `capture_dir`, which this grader needs
   too -- read defensively via `getattr` below, rather than overriding `_grade`'s `ctx`
   parameter with a narrower Protocol type, which would violate Liskov substitution
   against the base class). Neither `GraderConfig` nor `GraderContext` carries the
   task's `source.instance_id` -- widening `RunContext`/`runner.py`'s call site to
   pass `task`/`source` through is the "right" fix (TASK.md's own noted contract gap)
   but touches two files this task does not own. Instead, this grader derives the
   instance id from an invariant `runner.py`/`workspace.py` ALREADY guarantee on their
   own: `ws_path = subject_ws_root / task.id` (`runner.py::_run_one`) and
   `repo_dir = ws_path / "repo"` (`workspace.py::materialize_workspace`) -- so
   `Path(repo_dir).parent.name` is always exactly `task.id`. For the committed
   `swe-verified-mini` suite, `task.id == source.instance_id` by construction
   (`swebench_import.py` always sets the task id to the instance id -- verified
   against the actual committed `suite.json`). Flagged as a forward note for a future
   contract-widening task rather than edited here.
2. **`model_name_or_path`.** Neither `cfg` nor `ctx` carries a subject id today (the
   real `RunContext` has no such field). Rather than lean on the same kind of
   path-introspection trick for a value that is purely a predictions-file/report label
   (never affects `resolved`), this uses the stable constant `_MODEL_NAME_OR_PATH`
   ("ao-bench") -- explicitly sanctioned by this task's dispatch note ("if the subject
   id isn't available, use a stable constant... and document"). Each grade() call still
   gets its own deterministic, instance-scoped `run_id` and its own per-task `cwd`
   (`ctx.capture_dir`), so two subjects grading the same instance never collide on
   report paths even though the label is shared.

`keep_images` (whether to leave the instance's Docker image on disk after grading, so
a later grade of the SAME instance for a DIFFERENT subject skips a ~1GB/4min re-pull)
is, for the identical file-ownership reason, NOT threaded through `GraderConfig` --
`GraderConfig` (`bench/spec.py`) has no `extra="allow"` (confirmed empirically: an
unmodeled key in a suite's `grader:{...}` block is silently dropped by pydantic before
this grader ever sees it), and widening it is a `spec.py` edit this task may not make.
Resolution order: an explicit `SweBenchGrader(keep_images=...)` constructor arg (tests
only -- `runner.py` always instantiates `GRADER_REGISTRY[type]()` with no args) beats
the `AO_BENCH_SWEBENCH_KEEP_IMAGES` env var (config/env, not a suite edit --
CLAUDE.md's "no hardcoded ... env values -- use config/env" sanctions env as a config
channel) beats the default `False` (clean). See STATUS.md forward notes for
T-Cm9Tb4/the PLAN run.

Patch extraction uses `git -C repo_dir diff HEAD` -- a deliberate, documented
deviation from T-Sw5Hd9's forward note (which suggested a bare `git diff`). `HEAD` is
pinned/detached at exactly `base_commit` (T-Sw5Hd9 AC3), so `git diff HEAD` reports
BOTH staged and unstaged changes against that immutable baseline -- matching, literally,
what every task's own `instruction.md` promises the agent ("your changes are graded by
diffing the working tree against the original checkout"). A bare `git diff` only shows
UNSTAGED changes and would silently drop a fix the agent happened to `git add` (staging
is not itself prohibited by the instruction, only committing/resetting is). Both forms
equally exclude the untracked `INSTRUCTION.md` (a plain 2-tree `git diff` never reports
untracked files, regardless of which ref is given), so T-Sw5Hd9's exclusion guarantee
is preserved either way.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .graders import Grader, GraderContext, GradeResult
from .registries import register_grader
from .spec import GraderConfig

GRADER_TYPE = "swebench"

# ---------------------------------------------------------------------------
# Named constants (CLAUDE.md: no magic literals)
# ---------------------------------------------------------------------------

# Mirrors `swebench_import.py`'s own `DEFAULT_DATASET` -- a DELIBERATE duplicate (that
# module is a read-only sibling, not imported here to avoid any coupling beyond a
# single pinned string; mirrors that module's own precedent of tolerating a documented
# duplicate constant, e.g. its `_SUITE_DEFAULT_TIMEOUT_SECONDS` comment). Keep in sync
# if the pinned dataset ever changes.
_SWEBENCH_DATASET_NAME = "princeton-nlp/SWE-bench_Verified"

# Matches `swebench_provider.py`'s `SWEBENCH_PROVIDER_TYPE` value and the committed
# suite's prebuilt image namespace (ADR-0009 ground-truth probe).
_SWEBENCH_NAMESPACE = "swebench"

# Grading one instance at a time -- never let the harness itself fan out workers
# (that would defeat the module-global Docker-eval lock's "one eval in flight" intent
# and multiply peak disk usage, R1).
_HARNESS_MAX_WORKERS = 1

# Harness's own `--timeout` default (`run_evaluation --help`) -- used when neither the
# suite's grader config nor a caller override supplies one.
_DEFAULT_EVAL_TIMEOUT_SECONDS = 1800

# Headroom ABOVE `--timeout` for the subprocess's own hard bound: `--timeout` only
# bounds the in-container TEST run; image pull (first time, ~1GB/4min+ per ADR-0009
# ground truth) + container start + report-writing happen outside that window.
_HARNESS_TIMEOUT_PADDING_SECONDS = 900

# Bounded control-plane docker calls (image inspect/rmi/builder prune) -- NOT the eval
# itself, just housekeeping, so a small fixed bound is appropriate.
_DOCKER_CONTROL_TIMEOUT_SECONDS = 60

# `git -C repo_dir diff HEAD` is a fast, local, in-memory operation even for a large
# repo -- generous but small bound so a wedged git process can't hang a grade forever.
_GIT_DIFF_TIMEOUT_SECONDS = 30

# `GradeResult.raw_tail` cap (mirrors graders.py's own `_RAW_TAIL_MAX_CHARS`, sized up
# since the harness's own stdout/stderr is chattier than a pytest run).
_RAW_TAIL_MAX_CHARS = 4000

# Predictions-file field names the harness expects (swebench.harness.constants:
# KEY_INSTANCE_ID / KEY_MODEL / KEY_PREDICTION) -- duplicated as plain strings rather
# than imported (module-scope import of `swebench` would break the lazy-import
# contract/NFR-1); verified against the installed harness's `constants/__init__.py`.
_PRED_KEY_INSTANCE_ID = "instance_id"
_PRED_KEY_MODEL = "model_name_or_path"
_PRED_KEY_PATCH = "model_patch"

# Stable constant `model_name_or_path` label -- see module docstring note 2.
_MODEL_NAME_OR_PATH = "ao-bench"

# Working subdir under the task's own `capture_dir` (RunContext) where the
# predictions file + harness cwd (so its `report.json`/`logs/` land in per-task
# artifacts, never the repo working tree or a shared location) live.
_EVAL_SUBDIRNAME = "swebench"
_PREDICTIONS_FILENAME = "predictions.json"

# Harness report-file naming (verified against the installed `swebench` package:
# `harness/reporting.py::make_run_report` writes
# `<model_name_or_path>.<run_id>.json` to CWD, and per-instance
# `logs/run_evaluation/<run_id>/<model_name_or_path>/<instance_id>/report.json`,
# both CWD-relative regardless of any `--report_dir`).
_RUN_EVAL_LOG_DIRNAME = "run_evaluation"

# Image tag naming (ADR-0009 ground-truth probe, verified live against a pulled
# image): `swebench/sweb.eval.x86_64.<instance_id with "__" -> "_1776_">:latest`.
# Reimplementing this (rather than importing the harness's own naming helper) is a
# deliberate trade against "Harness API drift" (TASK.md risk) in the OTHER direction --
# accepted since it is read-only housekeeping (a wrong guess just means an image is
# left on disk, reported in `detail`, never a wrong grading verdict).
_INSTANCE_ID_MANGLE_FROM = "__"
_INSTANCE_ID_MANGLE_TO = "_1776_"
_INSTANCE_IMAGE_TAG_SUFFIX = "latest"

# `keep_images` resolution (module docstring): env var beats the `False` default,
# constructor arg (tests only) beats both.
_KEEP_IMAGES_ENV_VAR = "AO_BENCH_SWEBENCH_KEEP_IMAGES"
_KEEP_IMAGES_ENV_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_DEFAULT_KEEP_IMAGES = False


def _tail(text: str, limit: int = _RAW_TAIL_MAX_CHARS) -> str:
    """Small local duplicate of `graders.py::_tail` (private there, and that module is
    read-only for this task) -- same behavior: cap a possibly-huge capture to its last
    `limit` chars so `run.json` can't bloat from a runaway harness log.
    """
    return text[-limit:] if len(text) > limit else text


@dataclass
class _HarnessRun:
    """Internal result of invoking the harness subprocess -- never raises; failure
    modes are flags the caller turns into a graceful `GradeResult` (mirrors
    `graders.py::_CommandRun`'s convention).
    """

    returncode: int
    output: str
    timed_out: bool = False
    missing: bool = False


def _instance_id_from_repo_dir(repo_dir: str) -> str:
    """See module docstring note 1: `repo_dir`'s PARENT directory name is always
    exactly `task.id` (an invariant `runner.py`/`workspace.py` already guarantee), and
    for the committed `swe-verified-mini` suite `task.id == source.instance_id`.
    """
    return Path(repo_dir).parent.name


def _extract_patch(repo_dir: str) -> tuple[str, str | None]:
    """Stage everything, then `git -C repo_dir diff HEAD`. Staging first (`add -A`)
    is load-bearing: `git diff HEAD` never reports *untracked* files, so an agent fix
    that creates a new file would otherwise be silently absent from the graded patch
    (reviewer finding C1 -- a false negative indistinguishable from a wrong fix).
    `add -A` respects `.git/info/exclude`, so the provider-registered INSTRUCTION.md
    exclusion still holds. Returns `(patch_text, error)` -- `error` is `None` on
    success (including a genuinely empty diff -- an empty PATCH is not a git failure,
    the caller distinguishes the two). Never raises.
    """
    try:
        add_proc = subprocess.run(  # noqa: S603 -- fixed argv, no shell, bounded timeout
            ["git", "-C", repo_dir, "add", "-A"],
            capture_output=True,
            text=True,
            timeout=_GIT_DIFF_TIMEOUT_SECONDS,
            check=False,
        )
        if add_proc.returncode != 0:
            return "", f"git add -A exited {add_proc.returncode}: {add_proc.stderr[-500:]}"
        proc = subprocess.run(  # noqa: S603 -- fixed argv, no shell, bounded timeout
            ["git", "-C", repo_dir, "diff", "HEAD"],
            capture_output=True,
            text=True,
            timeout=_GIT_DIFF_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return "", str(exc)
    if proc.returncode != 0:
        return "", proc.stderr.strip() or f"git diff exited {proc.returncode}"
    return proc.stdout, None


def _write_predictions(path: Path, instance_id: str, patch: str) -> None:
    """One-row predictions file (harness's own expected shape, `KEY_*` constants
    above): a JSON LIST containing exactly one `{instance_id, model_name_or_path,
    model_patch}` object -- this grader only ever evaluates one instance per call.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [
        {
            _PRED_KEY_INSTANCE_ID: instance_id,
            _PRED_KEY_MODEL: _MODEL_NAME_OR_PATH,
            _PRED_KEY_PATCH: patch,
        }
    ]
    path.write_text(json.dumps(payload))


def _run_id_for(instance_id: str) -> str:
    """Deterministic, instance-scoped run id (no clock/RNG needed, CLAUDE.md
    determinism rule): uniqueness only matters WITHIN a single `cwd`, and `cwd` is
    already per-task-unique (`ctx.capture_dir`) and freshly wiped by
    `materialize_workspace` before every grade -- so a stable
    `f"aobench-{instance_id}"` can never collide with a still-live report from a prior
    call, and stays trivially greppable in Docker container/image names.
    """
    return f"aobench-{instance_id}"


def _build_harness_argv(
    *, instance_id: str, predictions_path: Path, run_id: str, timeout_seconds: int
) -> list[str]:
    """Argv for `sys.executable -m swebench.harness.run_evaluation` (subprocess, never
    an internal import -- TASK.md's "Harness API drift" risk mitigation). `sys.executable`
    is deliberately the CALLING interpreter's own path: NFR-1/module docstring already
    established that this grader only reaches this point after `import swebench`
    succeeded in-process, so the SAME interpreter necessarily has the extra installed.
    """
    return [
        sys.executable,
        "-m",
        "swebench.harness.run_evaluation",
        "--dataset_name",
        _SWEBENCH_DATASET_NAME,
        "--predictions_path",
        str(predictions_path),
        "--run_id",
        run_id,
        "--instance_ids",
        instance_id,
        "--max_workers",
        str(_HARNESS_MAX_WORKERS),
        "--namespace",
        _SWEBENCH_NAMESPACE,
        "--timeout",
        str(timeout_seconds),
    ]


def _run_harness(argv: list[str], cwd: Path, hard_timeout: int) -> _HarnessRun:
    """Spawn the harness, bounded by `hard_timeout` (module docstring:
    `timeout_seconds + _HARNESS_TIMEOUT_PADDING_SECONDS`). Never raises: a missing
    `sys.executable`/module (shouldn't happen -- defense-in-depth) or a timeout both
    degrade to a flagged `_HarnessRun`, mirroring `graders.py::_run_command`.
    """
    try:
        proc = subprocess.run(  # noqa: S603 -- fixed argv, no shell, bounded timeout
            argv,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=hard_timeout,
            check=False,
        )
        return _HarnessRun(
            returncode=proc.returncode, output=(proc.stdout or "") + (proc.stderr or "")
        )
    except FileNotFoundError as exc:
        return _HarnessRun(returncode=127, output=str(exc), missing=True)
    except subprocess.TimeoutExpired as exc:
        partial = (exc.stdout or "") + (exc.stderr or "") if isinstance(exc.stdout, str) else ""
        return _HarnessRun(returncode=124, output=partial, timed_out=True)


def _report_model_dirname(model_name_or_path: str) -> str:
    """Mirrors the harness's own `KEY_MODEL.replace("/", "__")` path-safety escaping
    (`reporting.py`) for both the top-level report filename and the per-instance
    report directory.
    """
    return model_name_or_path.replace("/", "__")


def _read_top_level_report(
    cwd: Path, model_name_or_path: str, run_id: str
) -> dict[str, Any] | None:
    """`<model>.<run_id>.json`, written to CWD regardless of `--report_dir` (verified
    against the installed `swebench.harness.reporting.make_run_report`). `None` if
    missing/unreadable/malformed -- the caller treats that as an eval failure.
    """
    path = cwd / f"{_report_model_dirname(model_name_or_path)}.{run_id}.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _instance_image_tag(instance_id: str) -> str:
    mangled = instance_id.replace(_INSTANCE_ID_MANGLE_FROM, _INSTANCE_ID_MANGLE_TO)
    return f"{_SWEBENCH_NAMESPACE}/sweb.eval.x86_64.{mangled}:{_INSTANCE_IMAGE_TAG_SUFFIX}"


def _docker_image_exists(tag: str) -> bool:
    try:
        proc = subprocess.run(  # noqa: S603 -- fixed argv, no shell, bounded timeout
            ["docker", "image", "inspect", tag],
            capture_output=True,
            text=True,
            timeout=_DOCKER_CONTROL_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


def _cleanup_docker_image(instance_id: str) -> dict[str, Any]:
    """Remove the instance's eval image (`docker rmi -f`) + best-effort prune the
    builder cache, and always report what is left on disk afterward (deliverable #3).
    Never raises: every docker subprocess here is best-effort housekeeping, not part
    of the grading verdict.
    """
    tag = _instance_image_tag(instance_id)
    detail: dict[str, Any] = {
        "image_tag": tag,
        "image_present_before_cleanup": _docker_image_exists(tag),
    }
    if detail["image_present_before_cleanup"]:
        try:
            proc = subprocess.run(  # noqa: S603 -- fixed argv, no shell, bounded timeout
                ["docker", "rmi", "-f", tag],
                capture_output=True,
                text=True,
                timeout=_DOCKER_CONTROL_TIMEOUT_SECONDS,
                check=False,
            )
            detail["docker_rmi_returncode"] = proc.returncode
            if proc.returncode != 0:
                detail["docker_rmi_stderr"] = _tail(proc.stderr or "", 500)
        except (OSError, subprocess.TimeoutExpired) as exc:
            detail["docker_rmi_error"] = str(exc)
    try:
        subprocess.run(  # noqa: S603 -- fixed argv, no shell, bounded timeout
            ["docker", "builder", "prune", "-f"],
            capture_output=True,
            text=True,
            timeout=_DOCKER_CONTROL_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass  # best-effort only -- never fails the grade over build-cache housekeeping
    detail["image_present_after_cleanup"] = _docker_image_exists(tag)
    return detail


# Module-global lock (ADR-0009 D4): held for the harness subprocess call + the
# post-eval image cleanup, so agent execution can parallelize (T-Pl3Rx7) while Docker
# evaluation -- and instance-image disk usage -- stays strictly one-at-a-time (R1).
_DOCKER_EVAL_LOCK = threading.Lock()


class SweBenchGrader(Grader):
    """Official-harness SWE-bench grader (TASK.md, ADR-0009 D2/D4). See module
    docstring for the `keep_images` resolution order and the two contract-gap
    deviations (instance-id derivation, `model_name_or_path`).
    """

    def __init__(self, *, keep_images: bool | None = None) -> None:
        # `runner.py` always instantiates `GRADER_REGISTRY[type]()` with no args
        # today, so `keep_images` is only ever set directly by a test; production
        # resolution goes through `_resolve_keep_images`'s env-var fallback.
        self._keep_images_override = keep_images

    def _resolve_keep_images(self) -> bool:
        if self._keep_images_override is not None:
            return self._keep_images_override
        env_value = os.environ.get(_KEEP_IMAGES_ENV_VAR)
        if env_value is None:
            return _DEFAULT_KEEP_IMAGES
        return env_value.strip().lower() in _KEEP_IMAGES_ENV_TRUE_VALUES

    def _grade(self, cfg: GraderConfig, ctx: GraderContext) -> GradeResult:
        if shutil.which("docker") is None:
            return GradeResult(
                solved=False,
                score=0.0,
                detail={"reason": "swebench extra/docker unavailable: docker not on PATH"},
            )
        try:
            # Presence check only -- the harness itself is invoked via subprocess below.
            import swebench  # noqa: F401
        except ImportError as exc:
            return GradeResult(
                solved=False,
                score=0.0,
                detail={
                    "reason": "swebench extra/docker unavailable: swebench package not installed",
                    "import_error": str(exc),
                },
            )

        # `GraderContext` (bench/graders.py) only guarantees `repo_dir`; the real
        # `RunContext` also has `capture_dir` (this grader's per-task scratch/output
        # location), read defensively rather than widening the override's own `ctx`
        # parameter type (module docstring note 1 -- would violate Liskov
        # substitution against the base `Grader.grade` signature).
        capture_dir = getattr(ctx, "capture_dir", None)
        if not isinstance(capture_dir, str) or not capture_dir:
            return GradeResult(
                solved=False,
                score=0.0,
                detail={"reason": "grader context missing capture_dir (needs a real RunContext)"},
            )

        instance_id = _instance_id_from_repo_dir(ctx.repo_dir)

        patch, diff_error = _extract_patch(ctx.repo_dir)
        if diff_error is not None:
            return GradeResult(
                solved=False,
                score=0.0,
                detail={
                    "reason": "git diff failed",
                    "instance_id": instance_id,
                    "git_error": diff_error,
                },
            )
        if not patch.strip():
            # AC2: an empty/no-op diff short-circuits BEFORE any Docker spawn.
            return GradeResult(
                solved=False,
                score=0.0,
                detail={"reason": "empty patch", "instance_id": instance_id},
            )

        eval_dir = Path(capture_dir) / _EVAL_SUBDIRNAME
        predictions_path = eval_dir / _PREDICTIONS_FILENAME
        _write_predictions(predictions_path, instance_id, patch)

        timeout_seconds = cfg.timeout_seconds or _DEFAULT_EVAL_TIMEOUT_SECONDS
        run_id = _run_id_for(instance_id)
        argv = _build_harness_argv(
            instance_id=instance_id,
            predictions_path=predictions_path,
            run_id=run_id,
            timeout_seconds=timeout_seconds,
        )
        hard_timeout = timeout_seconds + _HARNESS_TIMEOUT_PADDING_SECONDS
        keep_images = self._resolve_keep_images()

        # ADR-0009 D4: one Docker eval (and one instance image) in flight at a time,
        # across every concurrently-scheduled task-level worker.
        with _DOCKER_EVAL_LOCK:
            harness_run = _run_harness(argv, eval_dir, hard_timeout)
            report = (
                None
                if (harness_run.missing or harness_run.timed_out)
                else _read_top_level_report(eval_dir, _MODEL_NAME_OR_PATH, run_id)
            )
            images_detail = (
                {"kept": True, "image_tag": _instance_image_tag(instance_id)}
                if keep_images
                else _cleanup_docker_image(instance_id)
            )

        detail: dict[str, Any] = {
            "instance_id": instance_id,
            "patch_bytes": len(patch.encode("utf-8")),
            "run_id": run_id,
            "returncode": harness_run.returncode,
            "images": images_detail,
        }
        raw_tail = _tail(harness_run.output)
        if harness_run.timed_out:
            detail.update({"eval_error": "swebench harness timed out", "timed_out": True})
            return GradeResult(solved=False, score=0.0, detail=detail, raw_tail=raw_tail)
        if harness_run.missing:
            detail.update({"eval_error": "failed to invoke swebench harness subprocess"})
            return GradeResult(solved=False, score=0.0, detail=detail, raw_tail=raw_tail)
        if report is None:
            rc = harness_run.returncode
            detail.update({"eval_error": f"harness exited {rc} without a readable report"})
            return GradeResult(solved=False, score=0.0, detail=detail, raw_tail=raw_tail)

        resolved = instance_id in set(report.get("resolved_ids", []))
        detail.update(
            {
                "resolved": resolved,
                "error_ids": report.get("error_ids", []),
                "unresolved_ids": report.get("unresolved_ids", []),
                "empty_patch_ids": report.get("empty_patch_ids", []),
            }
        )
        return GradeResult(
            solved=resolved, score=1.0 if resolved else 0.0, detail=detail, raw_tail=raw_tail
        )


register_grader(GRADER_TYPE, SweBenchGrader)
