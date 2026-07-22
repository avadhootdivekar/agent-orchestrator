"""Tests for `agent_orchestrator.bench.swebench_grader` (E-Bt4Xk9 T-Sg6Jf2).

Network/Docker-free by default: `git diff` extraction runs against a REAL, local,
throwaway git repo (tmp_path) -- that part is exercised for real -- but the
`swebench` harness subprocess and every `docker` control-plane call are replaced by
`_make_fake_run` (a scripted `subprocess.run` stand-in matched on `argv[0]`, real git
passthrough via a captured reference to the ORIGINAL `subprocess.run` taken before any
monkeypatching, so patching `swebench_grader.subprocess.run` -- the same module object
`git diff` also calls through -- can't recurse into itself). The `swebench`/`docker`
presence pre-flight checks are stubbed independently (`_swebench_and_docker_available`)
so this file's default tests never depend on whether the real optional extra or a
real `docker` binary happen to be installed in whatever environment runs the suite.

The one opt-in real test (`swebench` marker, `AO_E2E_SWEBENCH=1`,
`tests/bench/conftest.py`'s gate) grades ONE pinned instance (`psf__requests-2931`,
the smallest committed image) with its real gold patch through a REAL Docker eval.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
import types
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

import agent_orchestrator.bench.swebench_grader as sg_mod

# noqa: F401 -- registers the "swebench" WorkspaceProvider (needed by the opt-in real
# test's materialize_workspace() call below), mirrors bench/cli.py's own import.
from agent_orchestrator.bench import swebench_provider  # noqa: F401
from agent_orchestrator.bench.graders import GradeResult
from agent_orchestrator.bench.registries import GRADER_REGISTRY
from agent_orchestrator.bench.spec import GraderConfig
from agent_orchestrator.bench.swebench_grader import (
    GRADER_TYPE,
    SweBenchGrader,
    _build_harness_argv,
    _extract_patch,
    _instance_id_from_repo_dir,
    _instance_image_tag,
    _read_top_level_report,
    _run_id_for,
    _write_predictions,
)

# The REAL `subprocess.run`, captured at import time -- BEFORE any test monkeypatches
# `sg_mod.subprocess.run` (the same module object `import subprocess` refers to
# process-wide). `_make_fake_run`'s git passthrough calls THIS, never `subprocess.run`
# directly, so it can't recurse into its own patched-in replacement.
_REAL_SUBPROCESS_RUN = subprocess.run

_DATA_DIR = Path(__file__).parent / "data"
_GOLD_REPORT_FIXTURE = _DATA_DIR / "gold.feasibility-gold.json"


# ---------------------------------------------------------------------------
# Small local helpers (mirrors test_swebench_provider.py's own `_git` helper)
# ---------------------------------------------------------------------------


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    result = _REAL_SUBPROCESS_RUN(
        ["git", "-c", "user.email=test@example.com", "-c", "user.name=Test", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"git {args} failed: {result.stderr}"
    return result


def _make_repo(
    tmp_path: Path,
    instance_id: str,
    *,
    baseline: str = "def add(a, b):\n    return a - b  # bug\n",
) -> tuple[Path, Path]:
    """A real, tiny git repo whose `ws/repo` PARENT directory is named *instance_id*
    (mirrors `runner.py`'s own `ws_path = subject_ws_root / task.id` invariant that
    `_instance_id_from_repo_dir` relies on) plus a sibling `capture/` dir.
    """
    task_dir = tmp_path / "subject" / instance_id
    repo_dir = task_dir / "repo"
    capture_dir = task_dir / "capture"
    repo_dir.mkdir(parents=True)
    capture_dir.mkdir(parents=True)
    _git(["init", "-b", "main"], cwd=repo_dir)
    (repo_dir / "app.py").write_text(baseline)
    _git(["add", "."], cwd=repo_dir)
    _git(["commit", "-m", "base"], cwd=repo_dir)
    return repo_dir, capture_dir


@dataclass
class _Ctx:
    """Minimal `RunContext` stand-in exposing exactly what this grader reads."""

    repo_dir: str
    capture_dir: str


@dataclass
class _RepoOnlyCtx:
    """A ctx exposing ONLY `repo_dir` (satisfies `graders.py::GraderContext`, but NOT
    the real `RunContext`'s `capture_dir`) -- for the defensive-`getattr` test.
    """

    repo_dir: str


def _fixed_docker_present(monkeypatch: pytest.MonkeyPatch, *, present: bool = True) -> None:
    fake_path = "/usr/bin/docker" if present else None
    monkeypatch.setattr(
        sg_mod.shutil, "which", lambda name: fake_path if name == "docker" else None
    )


def _fixed_swebench_importable(monkeypatch: pytest.MonkeyPatch, *, importable: bool = True) -> None:
    # `None` in `sys.modules` is the standard trick to force `import swebench` to
    # raise `ImportError` (mirrors test_swebench_import.py's identical use for
    # `datasets`); a real `ModuleType` stub makes it succeed without needing the
    # actual optional extra installed.
    stub = None if not importable else types.ModuleType("swebench")
    monkeypatch.setitem(sys.modules, "swebench", stub)


@pytest.fixture()
def _swebench_and_docker_available(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stubs BOTH `_grade` pre-flight gates as satisfied, independent of whatever is
    actually installed on the machine running this test file.
    """
    _fixed_docker_present(monkeypatch, present=True)
    _fixed_swebench_importable(monkeypatch, importable=True)


def _make_fake_run(
    *,
    resolved_ids: list[str] = (),  # type: ignore[assignment]
    unresolved_ids: list[str] = (),  # type: ignore[assignment]
    error_ids: list[str] = (),  # type: ignore[assignment]
    empty_patch_ids: list[str] = (),  # type: ignore[assignment]
    harness_returncode: int = 0,
    docker_image_present: bool = False,
    write_report: bool = True,
    raise_timeout_on_harness: bool = False,
) -> tuple[Callable[..., subprocess.CompletedProcess[str]], list[dict[str, Any]]]:
    """A scripted `subprocess.run` replacement dispatched on `argv[0]`:

    - `git ...`  -> REAL passthrough (`_REAL_SUBPROCESS_RUN`), so patch extraction is
      genuinely exercised even in a "mocked harness" test.
    - `<sys.executable> -m swebench.harness.run_evaluation ...` -> simulated: writes
      the harness's own top-level report shape (`<model>.<run_id>.json`, matching
      `reporting.py::make_run_report`'s real, verified filename convention) into the
      `cwd` the call was made with, then returns a scripted `CompletedProcess` (or
      raises `subprocess.TimeoutExpired` if `raise_timeout_on_harness`).
    - `docker image inspect ...` / `docker rmi ...` / `docker builder prune ...` ->
      simulated docker control-plane responses (`docker_image_present` scripts
      whether the "instance image" appears to exist before cleanup).

    Returns `(fake_run, calls)` -- `calls` records every invocation's argv/cwd/timeout
    for assertions (deliberately outside the callable so the test can inspect *calls*
    with no fixture teardown ordering issues).
    """
    calls: list[dict[str, Any]] = []
    # Mutable so a `docker rmi` call flips subsequent `image inspect` responses --
    # `_cleanup_docker_image` inspects BEFORE and AFTER removal and this grader's
    # `detail["images"]["image_present_after_cleanup"]` depends on the second call
    # reflecting the (simulated) removal.
    image_state = {"present": docker_image_present}

    def _fake_run(
        argv: list[str], *, cwd: Path | str | None = None, timeout: float | None = None, **_: Any
    ) -> subprocess.CompletedProcess[str]:
        calls.append({"argv": list(argv), "cwd": Path(cwd) if cwd else None, "timeout": timeout})

        if argv[0] == "git":
            return _REAL_SUBPROCESS_RUN(
                argv, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False
            )

        if argv[0] == "docker":
            if argv[1:3] == ["image", "inspect"]:
                rc = 0 if image_state["present"] else 1
                return subprocess.CompletedProcess(argv, rc, stdout="", stderr="")
            if argv[1] == "rmi":
                image_state["present"] = False
                return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
            if argv[1:3] == ["builder", "prune"]:
                return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
            raise AssertionError(f"unexpected docker argv: {argv}")

        if argv[0] == sys.executable:
            assert "swebench.harness.run_evaluation" in argv
            if raise_timeout_on_harness:
                raise subprocess.TimeoutExpired(cmd=argv, timeout=timeout or 0)
            if write_report:
                run_id = argv[argv.index("--run_id") + 1]
                report = {
                    "resolved_ids": list(resolved_ids),
                    "unresolved_ids": list(unresolved_ids),
                    "error_ids": list(error_ids),
                    "empty_patch_ids": list(empty_patch_ids),
                    "schema_version": 2,
                }
                assert cwd is not None
                report_path = Path(cwd) / f"{sg_mod._MODEL_NAME_OR_PATH}.{run_id}.json"
                report_path.write_text(json.dumps(report))
            return subprocess.CompletedProcess(
                argv, harness_returncode, stdout="Instances resolved: 1\n", stderr=""
            )

        raise AssertionError(f"unexpected subprocess call: {argv}")

    return _fake_run, calls


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_swebench_grader_registered_at_import_time() -> None:
    assert GRADER_REGISTRY[GRADER_TYPE] is SweBenchGrader


# ---------------------------------------------------------------------------
# Instance-id derivation (module docstring note 1)
# ---------------------------------------------------------------------------


def test_instance_id_from_repo_dir_is_parent_dirname(tmp_path: Path) -> None:
    repo_dir = tmp_path / "some-subject" / "django__django-11138" / "repo"
    assert _instance_id_from_repo_dir(str(repo_dir)) == "django__django-11138"


# ---------------------------------------------------------------------------
# Patch extraction (`git diff HEAD`)
# ---------------------------------------------------------------------------


def test_extract_patch_captures_unstaged_tracked_edit(tmp_path: Path) -> None:
    repo_dir, _ = _make_repo(tmp_path, "some__instance-1")
    (repo_dir / "app.py").write_text("def add(a, b):\n    return a + b  # fixed\n")

    patch, err = _extract_patch(str(repo_dir))

    assert err is None
    assert "app.py" in patch
    assert "+    return a + b  # fixed" in patch


def test_extract_patch_captures_staged_edit_too(tmp_path: Path) -> None:
    """Deviation from a bare `git diff` (module docstring): `git diff HEAD` also
    captures STAGED changes -- proves a `git add` before grading doesn't silently
    drop the agent's fix.
    """
    repo_dir, _ = _make_repo(tmp_path, "some__instance-2")
    (repo_dir / "app.py").write_text("def add(a, b):\n    return a + b  # fixed\n")
    _git(["add", "."], cwd=repo_dir)

    patch, err = _extract_patch(str(repo_dir))

    assert err is None
    assert "+    return a + b  # fixed" in patch


def test_extract_patch_excludes_untracked_instruction_md(tmp_path: Path) -> None:
    repo_dir, _ = _make_repo(tmp_path, "some__instance-3")
    (repo_dir / "app.py").write_text("def add(a, b):\n    return a + b  # fixed\n")
    (repo_dir / "INSTRUCTION.md").write_text("# Fix the bug\n")

    patch, err = _extract_patch(str(repo_dir))

    assert err is None
    assert "INSTRUCTION.md" not in patch


def test_extract_patch_no_changes_is_empty_not_an_error(tmp_path: Path) -> None:
    repo_dir, _ = _make_repo(tmp_path, "some__instance-4")

    patch, err = _extract_patch(str(repo_dir))

    assert err is None
    assert patch.strip() == ""


def test_extract_patch_git_failure_is_reported(tmp_path: Path) -> None:
    not_a_repo = tmp_path / "not-a-repo"
    not_a_repo.mkdir()

    patch, err = _extract_patch(str(not_a_repo))

    assert patch == ""
    assert err is not None


# ---------------------------------------------------------------------------
# Predictions-file shape
# ---------------------------------------------------------------------------


def test_write_predictions_shape(tmp_path: Path) -> None:
    path = tmp_path / "predictions.json"

    _write_predictions(path, "django__django-11138", "diff --git a/x b/x\n")

    payload = json.loads(path.read_text())
    assert payload == [
        {
            "instance_id": "django__django-11138",
            "model_name_or_path": sg_mod._MODEL_NAME_OR_PATH,
            "model_patch": "diff --git a/x b/x\n",
        }
    ]


# ---------------------------------------------------------------------------
# Harness-invocation argv construction
# ---------------------------------------------------------------------------


def test_build_harness_argv_shape() -> None:
    argv = _build_harness_argv(
        instance_id="django__django-11138",
        predictions_path=Path("/tmp/preds.json"),
        run_id="aobench-django__django-11138",
        timeout_seconds=900,
    )

    assert argv == [
        sys.executable,
        "-m",
        "swebench.harness.run_evaluation",
        "--dataset_name",
        sg_mod._SWEBENCH_DATASET_NAME,
        "--predictions_path",
        "/tmp/preds.json",
        "--run_id",
        "aobench-django__django-11138",
        "--instance_ids",
        "django__django-11138",
        "--max_workers",
        "1",
        "--namespace",
        "swebench",
        "--timeout",
        "900",
    ]


def test_grade_invokes_harness_with_expected_argv_and_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _swebench_and_docker_available: None
) -> None:
    instance_id = "some__instance-argv"
    repo_dir, capture_dir = _make_repo(tmp_path, instance_id)
    (repo_dir / "app.py").write_text("def add(a, b):\n    return a + b  # fixed\n")

    fake_run, calls = _make_fake_run(resolved_ids=[instance_id])
    monkeypatch.setattr(sg_mod.subprocess, "run", fake_run)

    result = SweBenchGrader().grade(
        GraderConfig(type="swebench", timeout_seconds=42), _Ctx(str(repo_dir), str(capture_dir))
    )

    assert result.solved is True
    harness_calls = [c for c in calls if c["argv"][0] == sys.executable]
    assert len(harness_calls) == 1
    argv = harness_calls[0]["argv"]
    assert "--instance_ids" in argv and instance_id in argv
    assert "--run_id" in argv and _run_id_for(instance_id) in argv
    assert "--timeout" in argv and "42" in argv
    assert "--namespace" in argv and "swebench" in argv
    assert "--max_workers" in argv and "1" in argv
    # cwd is the task's own capture dir (per-task artifacts), never the repo/shared dir.
    assert harness_calls[0]["cwd"] == capture_dir / "swebench"

    predictions_path = capture_dir / "swebench" / "predictions.json"
    payload = json.loads(predictions_path.read_text())
    assert payload[0]["instance_id"] == instance_id
    assert "a + b" in payload[0]["model_patch"]


# ---------------------------------------------------------------------------
# Empty-patch short-circuit: no docker/harness subprocess at all
# ---------------------------------------------------------------------------


def test_grade_empty_patch_short_circuits_before_any_subprocess(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _swebench_and_docker_available: None
) -> None:
    instance_id = "some__instance-empty"
    repo_dir, capture_dir = _make_repo(tmp_path, instance_id)

    def _fail_unless_git(
        argv: list[str], *, cwd: Path | str | None = None, timeout: float | None = None, **_: Any
    ) -> subprocess.CompletedProcess[str]:
        # `git diff HEAD` itself is a (real, passthrough) subprocess call -- only a
        # DOCKER/HARNESS call would mean AC2's "no Docker spawn" was violated.
        if argv[0] == "git":
            return _REAL_SUBPROCESS_RUN(
                argv, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False
            )
        raise AssertionError(f"no docker/harness subprocess expected for an empty patch: {argv}")

    monkeypatch.setattr(sg_mod.subprocess, "run", _fail_unless_git)

    result = SweBenchGrader().grade(
        GraderConfig(type="swebench"), _Ctx(str(repo_dir), str(capture_dir))
    )

    assert result.solved is False
    assert result.score == 0.0
    assert result.detail["reason"] == "empty patch"


# ---------------------------------------------------------------------------
# Report parsing -- real fixture (gold.feasibility-gold.json copied from the
# orchestrator's own verified feasibility probe)
# ---------------------------------------------------------------------------


def test_read_top_level_report_parses_real_gold_fixture() -> None:
    assert _GOLD_REPORT_FIXTURE.is_file(), "fixture must be committed under tests/bench/data/"

    report = _read_top_level_report(_DATA_DIR, "gold", "feasibility-gold")

    assert report is not None
    assert report["schema_version"] == 2
    assert report["resolved_instances"] == 1
    assert "sympy__sympy-20154" in report["resolved_ids"]
    assert report["error_ids"] == []
    assert report["unresolved_ids"] == []


def test_read_top_level_report_missing_file_returns_none(tmp_path: Path) -> None:
    assert _read_top_level_report(tmp_path, "ao-bench", "no-such-run") is None


def test_read_top_level_report_malformed_json_returns_none(tmp_path: Path) -> None:
    (tmp_path / "ao-bench.run-x.json").write_text("{not valid json")
    assert _read_top_level_report(tmp_path, "ao-bench", "run-x") is None


# ---------------------------------------------------------------------------
# resolved / unresolved / error mapping
# ---------------------------------------------------------------------------


def test_grade_maps_resolved_instance_to_solved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _swebench_and_docker_available: None
) -> None:
    instance_id = "some__instance-resolved"
    repo_dir, capture_dir = _make_repo(tmp_path, instance_id)
    (repo_dir / "app.py").write_text("fixed\n")
    fake_run, _ = _make_fake_run(resolved_ids=[instance_id])
    monkeypatch.setattr(sg_mod.subprocess, "run", fake_run)

    result = SweBenchGrader().grade(
        GraderConfig(type="swebench"), _Ctx(str(repo_dir), str(capture_dir))
    )

    assert result.solved is True
    assert result.score == 1.0
    assert result.detail["resolved"] is True


def test_grade_maps_unresolved_instance_to_not_solved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _swebench_and_docker_available: None
) -> None:
    instance_id = "some__instance-unresolved"
    repo_dir, capture_dir = _make_repo(tmp_path, instance_id)
    (repo_dir / "app.py").write_text("an attempted but wrong fix\n")
    fake_run, _ = _make_fake_run(unresolved_ids=[instance_id])
    monkeypatch.setattr(sg_mod.subprocess, "run", fake_run)

    result = SweBenchGrader().grade(
        GraderConfig(type="swebench"), _Ctx(str(repo_dir), str(capture_dir))
    )

    assert result.solved is False
    assert result.score == 0.0
    assert result.detail["resolved"] is False
    assert instance_id in result.detail["unresolved_ids"]


def test_grade_maps_error_instance_to_not_solved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _swebench_and_docker_available: None
) -> None:
    instance_id = "some__instance-error"
    repo_dir, capture_dir = _make_repo(tmp_path, instance_id)
    (repo_dir / "app.py").write_text("a patch that fails to apply cleanly upstream\n")
    fake_run, _ = _make_fake_run(error_ids=[instance_id])
    monkeypatch.setattr(sg_mod.subprocess, "run", fake_run)

    result = SweBenchGrader().grade(
        GraderConfig(type="swebench"), _Ctx(str(repo_dir), str(capture_dir))
    )

    assert result.solved is False
    assert instance_id in result.detail["error_ids"]


def test_grade_harness_nonzero_exit_without_report_is_eval_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _swebench_and_docker_available: None
) -> None:
    instance_id = "some__instance-no-report"
    repo_dir, capture_dir = _make_repo(tmp_path, instance_id)
    (repo_dir / "app.py").write_text("edit\n")
    fake_run, _ = _make_fake_run(harness_returncode=1, write_report=False)
    monkeypatch.setattr(sg_mod.subprocess, "run", fake_run)

    result = SweBenchGrader().grade(
        GraderConfig(type="swebench"), _Ctx(str(repo_dir), str(capture_dir))
    )

    assert result.solved is False
    assert "eval_error" in result.detail


# ---------------------------------------------------------------------------
# Timeout -> graceful GradeResult (AC3: "never an exception past the Grader.grade
# boundary" -- the authoritative TASK.md acceptance criterion, matching every other
# grader's own convention in graders.py; see swebench_grader.py module docstring).
# ---------------------------------------------------------------------------


def test_grade_harness_timeout_is_graceful_not_an_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _swebench_and_docker_available: None
) -> None:
    instance_id = "some__instance-timeout"
    repo_dir, capture_dir = _make_repo(tmp_path, instance_id)
    (repo_dir / "app.py").write_text("edit\n")
    fake_run, _ = _make_fake_run(raise_timeout_on_harness=True)
    monkeypatch.setattr(sg_mod.subprocess, "run", fake_run)

    result = SweBenchGrader().grade(
        GraderConfig(type="swebench", timeout_seconds=5), _Ctx(str(repo_dir), str(capture_dir))
    )

    assert result.solved is False
    assert result.detail["timed_out"] is True


# ---------------------------------------------------------------------------
# keep_images -> docker rmi called / not-called
# ---------------------------------------------------------------------------


def test_grade_default_keep_images_false_removes_image(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _swebench_and_docker_available: None
) -> None:
    instance_id = "some__instance-cleanup"
    repo_dir, capture_dir = _make_repo(tmp_path, instance_id)
    (repo_dir / "app.py").write_text("edit\n")
    fake_run, calls = _make_fake_run(resolved_ids=[instance_id], docker_image_present=True)
    monkeypatch.setattr(sg_mod.subprocess, "run", fake_run)

    result = SweBenchGrader().grade(
        GraderConfig(type="swebench"), _Ctx(str(repo_dir), str(capture_dir))
    )

    rmi_calls = [c for c in calls if c["argv"][:2] == ["docker", "rmi"]]
    assert len(rmi_calls) == 1
    assert _instance_image_tag(instance_id) in rmi_calls[0]["argv"]
    assert result.detail["images"]["image_present_after_cleanup"] is False


def test_grade_keep_images_true_skips_docker_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _swebench_and_docker_available: None
) -> None:
    instance_id = "some__instance-keep"
    repo_dir, capture_dir = _make_repo(tmp_path, instance_id)
    (repo_dir / "app.py").write_text("edit\n")
    fake_run, calls = _make_fake_run(resolved_ids=[instance_id], docker_image_present=True)
    monkeypatch.setattr(sg_mod.subprocess, "run", fake_run)

    result = SweBenchGrader(keep_images=True).grade(
        GraderConfig(type="swebench"), _Ctx(str(repo_dir), str(capture_dir))
    )

    docker_calls = [c for c in calls if c["argv"][0] == "docker"]
    assert docker_calls == []
    assert result.detail["images"] == {"kept": True, "image_tag": _instance_image_tag(instance_id)}


def test_grade_keep_images_via_env_var(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _swebench_and_docker_available: None
) -> None:
    instance_id = "some__instance-keep-env"
    repo_dir, capture_dir = _make_repo(tmp_path, instance_id)
    (repo_dir / "app.py").write_text("edit\n")
    fake_run, calls = _make_fake_run(resolved_ids=[instance_id])
    monkeypatch.setattr(sg_mod.subprocess, "run", fake_run)
    monkeypatch.setenv(sg_mod._KEEP_IMAGES_ENV_VAR, "1")

    SweBenchGrader().grade(GraderConfig(type="swebench"), _Ctx(str(repo_dir), str(capture_dir)))

    docker_calls = [c for c in calls if c["argv"][0] == "docker"]
    assert docker_calls == []


# ---------------------------------------------------------------------------
# Lock serialization: two threads, mocked subprocess with a shared in-flight
# counter -> assert Docker evals never overlap.
# ---------------------------------------------------------------------------


def test_grade_serializes_docker_eval_across_threads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _swebench_and_docker_available: None
) -> None:
    bookkeeping_lock = threading.Lock()
    state = {"in_flight": 0, "max_in_flight": 0}

    def _tracking_fake_run(
        argv: list[str], *, cwd: Path | str | None = None, timeout: float | None = None, **_: Any
    ) -> subprocess.CompletedProcess[str]:
        if argv[0] == "git":
            return _REAL_SUBPROCESS_RUN(
                argv, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False
            )
        if argv[0] == "docker":
            rc = 1 if argv[1:3] == ["image", "inspect"] else 0
            return subprocess.CompletedProcess(argv, rc, stdout="", stderr="")
        assert argv[0] == sys.executable
        with bookkeeping_lock:
            state["in_flight"] += 1
            state["max_in_flight"] = max(state["max_in_flight"], state["in_flight"])
        try:
            time.sleep(0.05)  # hold "inside the eval" briefly to expose any overlap
            instance_id = argv[argv.index("--instance_ids") + 1]
            run_id = argv[argv.index("--run_id") + 1]
            assert cwd is not None
            report_path = Path(cwd) / f"{sg_mod._MODEL_NAME_OR_PATH}.{run_id}.json"
            report_path.write_text(json.dumps({"resolved_ids": [instance_id]}))
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
        finally:
            with bookkeeping_lock:
                state["in_flight"] -= 1

    monkeypatch.setattr(sg_mod.subprocess, "run", _tracking_fake_run)

    repo_a, capture_a = _make_repo(tmp_path, "some__instance-lock-a")
    (repo_a / "app.py").write_text("edit a\n")
    repo_b, capture_b = _make_repo(tmp_path, "some__instance-lock-b")
    (repo_b / "app.py").write_text("edit b\n")

    results: dict[str, GradeResult] = {}

    def _run(name: str, repo_dir: Path, capture_dir: Path) -> None:
        results[name] = SweBenchGrader().grade(
            GraderConfig(type="swebench"), _Ctx(str(repo_dir), str(capture_dir))
        )

    t1 = threading.Thread(target=_run, args=("a", repo_a, capture_a))
    t2 = threading.Thread(target=_run, args=("b", repo_b, capture_b))
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    assert state["max_in_flight"] == 1, "Docker evals overlapped -- lock did not serialize"
    assert results["a"].solved is True
    assert results["b"].solved is True


# ---------------------------------------------------------------------------
# Pre-flight gates: docker missing / swebench extra missing
# ---------------------------------------------------------------------------


def test_grade_docker_missing_is_graceful(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _fixed_docker_present(monkeypatch, present=False)
    repo_dir, capture_dir = _make_repo(tmp_path, "some__instance-nodocker")

    result = SweBenchGrader().grade(
        GraderConfig(type="swebench"), _Ctx(str(repo_dir), str(capture_dir))
    )

    assert result.solved is False
    assert "docker" in result.detail["reason"]


def test_grade_missing_swebench_extra_is_graceful(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fixed_docker_present(monkeypatch, present=True)
    _fixed_swebench_importable(monkeypatch, importable=False)
    repo_dir, capture_dir = _make_repo(tmp_path, "some__instance-noextra")

    result = SweBenchGrader().grade(
        GraderConfig(type="swebench"), _Ctx(str(repo_dir), str(capture_dir))
    )

    assert result.solved is False
    assert "swebench" in result.detail["reason"]


def test_grade_missing_capture_dir_is_graceful(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _swebench_and_docker_available: None
) -> None:
    """`GraderContext` (graders.py) only guarantees `repo_dir` -- a ctx conforming
    ONLY to that minimal Protocol (no `capture_dir`) must degrade gracefully rather
    than raise an `AttributeError` (module docstring note 1).
    """
    repo_dir, _ = _make_repo(tmp_path, "some__instance-noctx")

    result = SweBenchGrader().grade(GraderConfig(type="swebench"), _RepoOnlyCtx(str(repo_dir)))

    assert result.solved is False
    assert "capture_dir" in result.detail["reason"]


# ---------------------------------------------------------------------------
# Opt-in real test (swebench marker): grade ONE pinned instance with its real gold
# patch through a REAL Docker eval.
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]
_COMMITTED_SUITE_DIR = _REPO_ROOT / "benchmarks" / "suites" / "swe-verified-mini"
_GOLD_PATCH_FIXTURE = _DATA_DIR / "psf__requests-2931.gold.patch"


@pytest.mark.swebench
@pytest.mark.skipif(
    not _COMMITTED_SUITE_DIR.is_dir(), reason="swe-verified-mini not generated in this checkout"
)
def test_real_grade_gold_patch_resolves_smallest_pinned_instance() -> None:
    """Opt-in (AO_E2E_SWEBENCH=1): real Docker eval of `psf__requests-2931` (the
    smallest committed image, ~0.91GiB) with its real gold patch (from the pinned
    SWE-bench Verified dataset row, fixture-committed) applied to a real,
    provider-checked-out workspace -- proves the production grading path end to end.
    `keep_images=False` (the default) so the pulled image is removed afterward.
    """
    from agent_orchestrator.bench.spec import load_suite
    from agent_orchestrator.bench.workspace import BENCH_WORKSPACE_ROOT, materialize_workspace

    assert _GOLD_PATCH_FIXTURE.is_file()
    suite = load_suite(_COMMITTED_SUITE_DIR / "suite.json")
    task = suite.task("psf__requests-2931")

    ws = BENCH_WORKSPACE_ROOT / "tests" / "swebench-grader-real-opt-in" / task.id
    ctx = materialize_workspace(
        task, ws, suite_base_dir=_COMMITTED_SUITE_DIR.resolve(), timeout_seconds=120
    )

    apply_result = _REAL_SUBPROCESS_RUN(
        ["git", "-C", ctx.repo_dir, "apply", str(_GOLD_PATCH_FIXTURE)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert apply_result.returncode == 0, f"gold patch failed to apply: {apply_result.stderr}"

    tag = _instance_image_tag("psf__requests-2931")
    result = SweBenchGrader(keep_images=False).grade(task.grader, ctx)

    assert result.solved is True, result.detail
    assert result.detail["resolved"] is True
    assert sg_mod._docker_image_exists(tag) is False  # cleaned up (disk safety, R1)
