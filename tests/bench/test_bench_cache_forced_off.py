"""T-ZTxN1x E-8a..E-8c: `ao-bench` always runs `ao run` with the result cache forced off.

`AoWorkflowSubject` is the only subject that shells out to `ao run`. The argv flag and the env var
are both asserted, including when the operator's own environment tries to enable the cache.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from agent_orchestrator.bench import subjects
from agent_orchestrator.bench.errors import SubjectError
from agent_orchestrator.bench.spec import BenchTask, GraderConfig, SubjectSpec
from agent_orchestrator.bench.subjects import AoWorkflowSubject
from agent_orchestrator.bench.workspace import RunContext
from agent_orchestrator.cache.constants import ENV_CACHE
from agent_orchestrator.cli import app

NO_CACHE_FLAG = "--no-cache"


def _task() -> BenchTask:
    return BenchTask(
        id="t1",
        category="bugfix",
        instruction="tasks/t1/instruction.md",
        fixture="tasks/t1/fixture",
        grader=GraderConfig(type="fake"),
    )


def _ctx(base: Path) -> RunContext:
    (base / "repo").mkdir(exist_ok=True)
    (base / "capture").mkdir(exist_ok=True)
    return RunContext(
        workspace=str(base),
        repo_dir=str(base / "repo"),
        instruction_path=str(base / "repo" / "INSTRUCTION.md"),
        capture_dir=str(base / "capture"),
        timeout_seconds=30,
        max_turns=None,
        budget_total=None,
        subject_base_dir=str(base),
    )


def _spec(base: Path) -> SubjectSpec:
    (base / "workflow.json").write_text("{}")
    (base / "agents.json").write_text("{}")
    (base / "reposet.json").write_text(
        json.dumps(
            {
                "version": "1.0",
                "repo_sets": {
                    "default-set": {
                        "workspace_root": "PLACEHOLDER",
                        "repos": [{"id": "core", "path": "PLACEHOLDER", "role": "primary"}],
                    }
                },
            }
        )
    )
    return SubjectSpec(
        version="1.0",
        id="ao-epic",
        type="ao_workflow",
        workflow="workflow.json",
        reposets="reposet.json",
        agents="agents.json",
    )


@pytest.fixture
def spawned(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Callable[[], tuple[list[str], dict[str, str]]]:
    """Returns a function that runs an `AoWorkflowSubject` with `_run_with_timeout` stubbed and
    reports the (argv, env) it would have spawned `ao run` with."""
    monkeypatch.setattr(subjects.shutil, "which", lambda name: f"/usr/bin/{name}")
    seen: dict[str, Any] = {}

    def fake_run_with_timeout(
        argv: list[str],
        *,
        cwd: Path,
        stdout_path: Path,
        stderr_path: Path,
        timeout_seconds: int,
        env: dict[str, str] | None = None,
    ) -> tuple[int, bool]:
        seen["argv"], seen["env"] = list(argv), dict(env or {})
        stdout_path.write_text("")
        stderr_path.write_text("")
        return 1, False  # the outcome is irrelevant here; only the spawn arguments are

    monkeypatch.setattr(subjects, "_run_with_timeout", fake_run_with_timeout)

    def run() -> tuple[list[str], dict[str, str]]:
        # The stub produces no run directory, so the subject reports an error afterwards; the
        # spawn arguments were already captured, which is all these tests look at.
        with contextlib.suppress(SubjectError):
            AoWorkflowSubject(_spec(tmp_path)).run(_task(), _ctx(tmp_path))
        return seen["argv"], seen["env"]

    return run


def test_flag_and_env_are_set_on_a_plain_run(
    spawned: Callable[[], tuple[list[str], dict[str, str]]], monkeypatch: pytest.MonkeyPatch
) -> None:  # E-8a
    monkeypatch.delenv(ENV_CACHE, raising=False)
    argv, env = spawned()
    assert NO_CACHE_FLAG in argv
    assert argv[:4] == ["uv", "run", "ao", "run"]  # the flag belongs to `ao run`
    assert env["AO_CACHE"] == "0"


@pytest.mark.parametrize("outer", ["1", "shadow", "on", "true"])
def test_an_operator_enabling_the_cache_does_not_leak_into_the_bench(
    spawned: Callable[[], tuple[list[str], dict[str, str]]],
    monkeypatch: pytest.MonkeyPatch,
    outer: str,
) -> None:  # E-8b
    monkeypatch.setenv(ENV_CACHE, outer)
    argv, env = spawned()
    assert NO_CACHE_FLAG in argv
    assert env["AO_CACHE"] == "0"


def test_the_flag_is_the_named_constant() -> None:
    assert subjects._AO_NO_CACHE_FLAG == NO_CACHE_FLAG
    assert ENV_CACHE == "AO_CACHE"


def test_ao_run_help_lists_the_flag_so_a_real_bench_run_never_fails() -> None:  # E-8c
    res = CliRunner().invoke(app, ["run", "--help"])
    assert res.exit_code == 0
    assert NO_CACHE_FLAG in res.output
