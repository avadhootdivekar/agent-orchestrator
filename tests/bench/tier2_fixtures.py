"""Shared builders for the Tier-2 tests (`repo_mutation`, `pr_mined`, `pytest_targeted`).

Everything is hermetic: a real, tiny git repo is built under `tmp_path` and addressed by a
`file://` URL, so the providers' real clone/checkout/snapshot code and the grader's real
pytest subprocess run end to end without network or LLM.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from agent_orchestrator.bench import (
    mutation_provider,  # noqa: F401 -- importing registers the provider
    pr_mined_provider,  # noqa: F401
)
from agent_orchestrator.bench.graders import GradeResult
from agent_orchestrator.bench.pytest_targeted import PytestTargetedGrader
from agent_orchestrator.bench.spec import BenchTask, GraderConfig, Source
from agent_orchestrator.bench.workspace import RunContext, materialize_workspace

CORE_PY = (
    "def add(a, b):\n    return a + b\n\n\ndef clamp(x, lo, hi):\n    return max(lo, min(x, hi))\n"
)
TEXT_PY = (
    "def slugify(s):\n"
    '    return "-".join(s.lower().split())\n'
    "\n\n"
    "def shout(s):\n"
    "    return s.upper() + '!'\n"
)
TEST_CORE = (
    "from minipkg.core import add, clamp\n\n\n"
    "def test_add():\n    assert add(1, 2) == 3\n\n\n"
    "def test_clamp_inside():\n    assert clamp(5, 0, 10) == 5\n"
)
TEST_TEXT = (
    "from minipkg.text import shout, slugify\n\n\n"
    "def test_slugify():\n    assert slugify('Hello World') == 'hello-world'\n\n\n"
    "def test_shout():\n    assert shout('a') == 'A!'\n"
)
HIDDEN_CLAMP = (
    "from minipkg.core import clamp\n\n\n"
    "def test_clamp_upper_bound():\n    assert clamp(99, 0, 10) == 10\n\n\n"
    "def test_clamp_lower_bound():\n    assert clamp(-5, 0, 10) == 0\n"
)
CLAMP_BUG = {
    "file": "minipkg/core.py",
    "find": "return max(lo, min(x, hi))",
    "replace": "return max(lo, min(x, hi - 1))",
}
INJECT_F2P = ["tests/test_clamp_hidden.py::test_clamp_upper_bound"]
INJECT_P2P = ["tests/test_core.py::test_add", "tests/test_core.py::test_clamp_inside"]


def git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=T", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"git {args} failed: {result.stderr}"
    return result.stdout.strip()


def write_files(root: Path, files: dict[str, str]) -> None:
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)


def commit_all(repo: Path, message: str) -> str:
    git(repo, "add", "--all")
    git(repo, "commit", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def make_origin(tmp_path: Path, name: str = "origin") -> tuple[Path, str]:
    """A small package + tests repo with one commit. Returns (path, commit sha)."""
    origin = tmp_path / name
    origin.mkdir()
    git(origin, "init", "-b", "main")
    write_files(
        origin,
        {
            "pytest.ini": "[pytest]\n",  # pin rootdir/ini to the repo, not an ancestor's
            "conftest.py": "",
            "minipkg/__init__.py": "",
            "minipkg/core.py": CORE_PY,
            "minipkg/text.py": TEXT_PY,
            "tests/test_core.py": TEST_CORE,
            "tests/test_text.py": TEST_TEXT,
            "docs/feature.md": "slugify docs\n",
        },
    )
    return origin, commit_all(origin, "initial")


def write_suite(
    tmp_path: Path, recipe: dict[str, Any], hidden: dict[str, str] | None = None
) -> Path:
    """Suite dir with `tasks/t1/{recipe.json,instruction.md}` and `hidden/<name>` sources."""
    suite = tmp_path / "suite"
    (suite / "tasks" / "t1").mkdir(parents=True, exist_ok=True)
    (suite / "tasks" / "t1" / "recipe.json").write_text(json.dumps(recipe))
    (suite / "tasks" / "t1" / "instruction.md").write_text("Fix the reported bug.\n")
    for name, content in (hidden or {}).items():
        write_files(suite / "hidden", {name: content})
    return suite


def mutation_task(
    origin: Path, sha: str, grader_extra: dict[str, Any] | None = None, task_id: str = "t1"
) -> BenchTask:
    return BenchTask(
        id=task_id,
        category="bugfix",
        instruction="tasks/t1/instruction.md",
        source=Source(
            type="repo_mutation",
            repo=origin.as_uri(),
            base_commit=sha,
            recipe="tasks/t1/recipe.json",
        ),
        grader=GraderConfig(type="pytest_targeted", **(grader_extra or {})),
    )


def materialize(task: BenchTask, suite: Path, bench_ws: Path) -> RunContext:
    return materialize_workspace(
        task,
        bench_ws / "ws",
        suite_base_dir=suite,
        timeout_seconds=60,
        grading_dir=bench_ws / "grading",
    )


def grade(task: BenchTask, ctx: RunContext) -> GradeResult:
    return PytestTargetedGrader().grade(task.grader, ctx)
