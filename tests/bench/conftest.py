"""Shared fixtures for tests/bench: tiny valid suite/subject spec builders on tmp_path.

Every builder materializes real files under tmp_path (instruction.md, fixture/, golden
files) so `load_suite`'s path-existence checks pass by default -- individual tests
override fields (or skip materialization) to exercise the reject paths. All fixtures
here are fake-tier only (no network, no real LLM) per CLAUDE.md's determinism rules.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest


def _default_task(task_id: str = "bugfix-off-by-one") -> dict[str, Any]:
    return {
        "id": task_id,
        "category": "bugfix",
        "instruction": f"tasks/{task_id}/instruction.md",
        "fixture": f"tasks/{task_id}/fixture",
        "grader": {"type": "pytest", "command": "true", "cwd": "."},
        "timeout_seconds": 60,
        "tags": ["python", "easy"],
    }


def _materialize_task(base: Path, task: dict[str, Any], materialize_goldens: bool = True) -> None:
    """Write real instruction.md / fixture/ for one task dict, plus (unless
    `materialize_goldens=False`) any `equals_file` golden files it references --
    kept independently toggle-able so a test can leave a golden missing on disk while
    still having a real instruction/fixture (see test_spec.py's golden-missing case).
    """
    instruction = base / task["instruction"]
    instruction.parent.mkdir(parents=True, exist_ok=True)
    instruction.write_text("# Fix the off-by-one bug in add().\n")

    fixture = base / task["fixture"]
    fixture.mkdir(parents=True, exist_ok=True)
    (fixture / "placeholder.txt").write_text("fixture content\n")

    if not materialize_goldens:
        return
    for assertion in task.get("grader", {}).get("assertions", []):
        if assertion.get("type") == "equals_file" and assertion.get("golden"):
            golden = base / assertion["golden"]
            golden.parent.mkdir(parents=True, exist_ok=True)
            if not golden.exists():
                golden.write_text("golden content\n")


SuiteFactory = Callable[..., Path]
SubjectFactory = Callable[..., Path]


@pytest.fixture()
def suite_factory(tmp_path: Path) -> SuiteFactory:
    """Returns a callable that writes a suite.json (+ materialized task files) under
    tmp_path and returns its Path. kwargs override top-level suite fields; pass
    `tasks=[...]` to override the task list; pass `materialize=False` to skip writing
    instruction/fixture/golden files (for missing-path negative tests); pass
    `extra_top_level={"unknown": 1}` to inject an additionalProperties violation.
    """

    def _make(
        *,
        suite_id: str = "dev-core",
        version: str = "1.0",
        domain: str = "software",
        tasks: list[dict[str, Any]] | None = None,
        materialize: bool = True,
        materialize_goldens: bool = True,
        dest_name: str = "suite.json",
        extra_top_level: dict[str, Any] | None = None,
        omit_version: bool = False,
    ) -> Path:
        if tasks is None:
            tasks = [_default_task()]
        data: dict[str, Any] = {
            "version": version,
            "id": suite_id,
            "domain": domain,
            "description": "test suite",
            "tasks": tasks,
        }
        if omit_version:
            del data["version"]
        if extra_top_level:
            data.update(extra_top_level)

        if materialize:
            for t in tasks:
                _materialize_task(tmp_path, t, materialize_goldens=materialize_goldens)

        suite_path = tmp_path / dest_name
        suite_path.write_text(json.dumps(data, indent=2))
        return suite_path

    return _make


@pytest.fixture()
def subject_factory(tmp_path: Path) -> SubjectFactory:
    """Returns a callable that writes a subject.json under tmp_path and returns its Path."""

    def _make(
        *,
        subject_id: str = "fake-pass",
        version: str = "1.0",
        type_: str = "fake",
        dest_name: str = "subject.json",
        extra: dict[str, Any] | None = None,
        omit_version: bool = False,
    ) -> Path:
        data: dict[str, Any] = {"version": version, "id": subject_id, "type": type_}
        if omit_version:
            del data["version"]
        if extra:
            data.update(extra)
        subject_path = tmp_path / dest_name
        subject_path.write_text(json.dumps(data, indent=2))
        return subject_path

    return _make
