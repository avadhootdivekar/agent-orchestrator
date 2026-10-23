"""Shared builders for the stats-track tests (comparison over repeats, staged campaigns,
calibrate): real `run_suite` runs of fake subjects over cheap `file_assertion` tasks.

A "solvable" task ships `.bench-solution/solved.txt` in its fixture and is graded by
"does `solved.txt` exist" -- so `scripted_effect: copy-solution` solves it, `noop`/`fail`
do not, with no subprocess and no network. `install_scripted_subject` swaps the registered
`fake` subject for one whose per-(subject, task) outcome is scripted call by call, which
is what makes a *repeat* differ from the first run (a plain FakeSubject is deterministic).
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from agent_orchestrator.bench import runner
from agent_orchestrator.bench.registries import SUBJECT_REGISTRY
from agent_orchestrator.bench.runner import BenchRunRecord
from agent_orchestrator.bench.spec import BenchTask
from agent_orchestrator.bench.subjects import FakeSubject, RunContext, SubjectResult

FIXED_NOW = datetime(2026, 10, 23, 12, 0, 0, tzinfo=UTC)

SOLUTION_FILE = "solved.txt"


def fixed_clock() -> datetime:
    return FIXED_NOW


def uniq() -> str:
    return uuid.uuid4().hex[:8]


def solvable_task(
    task_id: str,
    *,
    solvable: bool = True,
    family: str | None = None,
    language: str | None = None,
) -> dict[str, Any]:
    """A task spec dict; `solvable` decides whether its fixture ships a solution overlay."""
    task: dict[str, Any] = {
        "id": task_id,
        "category": "bugfix",
        "instruction": f"tasks/{task_id}/instruction.md",
        "fixture": f"tasks/{task_id}/fixture",
        "grader": {
            "type": "file_assertion",
            "assertions": [{"type": "exists", "path": SOLUTION_FILE}],
        },
        "timeout_seconds": 60,
        "tags": [],
        "_solvable": solvable,  # stripped by `write_suite`
    }
    if family is not None:
        task["family"] = family
    if language is not None:
        task["language"] = language
    return task


def write_suite(base: Path, suite_id: str, tasks: list[dict[str, Any]], **extra: Any) -> Path:
    """Materialise the tasks' instruction/fixture files under *base* and write suite.json."""
    clean: list[dict[str, Any]] = []
    for task in tasks:
        spec = {k: v for k, v in task.items() if k != "_solvable"}
        instruction = base / spec["instruction"]
        instruction.parent.mkdir(parents=True, exist_ok=True)
        instruction.write_text("# instructions\n")
        fixture = base / spec["fixture"]
        fixture.mkdir(parents=True, exist_ok=True)
        (fixture / "placeholder.txt").write_text("fixture content\n")
        if task.get("_solvable", True):
            solution = fixture / ".bench-solution"
            solution.mkdir(exist_ok=True)
            (solution / SOLUTION_FILE).write_text("done\n")
        clean.append(spec)
    data: dict[str, Any] = {
        "version": "1.0",
        "id": suite_id,
        "domain": "software",
        "description": "stats-track test suite",
        "tasks": clean,
    }
    data.update(extra)
    path = base / f"{suite_id}-suite.json"
    path.write_text(json.dumps(data, indent=2))
    return path


def write_subject(base: Path, subject_id: str, **extra: Any) -> Path:
    data: dict[str, Any] = {"version": "1.0", "id": subject_id, "type": "fake"}
    data.update(extra)
    path = base / f"{subject_id}-subject.json"
    path.write_text(json.dumps(data, indent=2))
    return path


def run_repeat(
    suite_path: Path,
    subject_path: Path,
    out_dir: Path,
    repeat: int = 1,
    **kwargs: Any,
) -> BenchRunRecord:
    return runner.run_suite(
        suite_path, subject_path, out_dir=out_dir, clock=fixed_clock, repeat=repeat, **kwargs
    )


def run_dir_of(out_dir: Path, record: BenchRunRecord) -> Path:
    return out_dir / record.bench_run_id


def install_scripted_subject(
    monkeypatch: pytest.MonkeyPatch, plan: dict[tuple[str, str], list[str]]
) -> dict[tuple[str, str], int]:
    """Replace the registered `fake` subject with one that follows *plan*.

    `plan[(subject_id, task_id)]` is the list of `scripted_effect`s applied on that
    subject's 1st, 2nd, ... run of that task (the last one repeats); a (subject, task)
    absent from the plan behaves like the plain FakeSubject. Returns the per-key call
    counters so a test can assert which (subject, task) pairs were actually run.
    """
    counters: dict[tuple[str, str], int] = {}

    class _ScriptedSubject(FakeSubject):
        def run(self, task: BenchTask, ctx: RunContext) -> SubjectResult:
            key = (self.spec.id, task.id)
            counters[key] = counters.get(key, 0) + 1
            effects = plan.get(key)
            if effects is None:
                return super().run(task, ctx)
            effect = effects[min(counters[key], len(effects)) - 1]
            return FakeSubject(self.spec.model_copy(update={"scripted_effect": effect})).run(
                task, ctx
            )

    monkeypatch.setitem(SUBJECT_REGISTRY, "fake", _ScriptedSubject)
    return counters


def write_parity_bundle(root: Path, *, md: str = "# shared\n") -> Path:
    bundle = root / "parity"
    (bundle / "claude").mkdir(parents=True, exist_ok=True)
    (bundle / "claude" / "settings.json").write_text("{}\n")
    (bundle / "CLAUDE.md").write_text(md)
    (bundle / "parity.json").write_text(
        json.dumps({"claude_dir": "claude", "claude_md": "CLAUDE.md", "max_turns": 7})
    )
    return bundle
