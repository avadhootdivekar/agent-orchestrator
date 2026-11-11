"""AC6 helper: run an ungoverned ``ao run`` and reduce its ``state.json`` to a stable shape.

Only APIs that also exist in the pre-epic tree are used (``ao run``, ``FakeExecutor``,
``tests.test_e2e_cli_max_parallel`` writers), so the same function produced the committed golden
``ungoverned_state.pre_epic.json`` from the pre-governance commit (``af24202``) and now produces
the current shape for comparison. Regenerate only from a pre-epic checkout.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from tests.test_e2e_cli_max_parallel import (
    _write_instructions,
    _write_simple_agents,
    _write_simple_reposet,
    _write_small_workflow,
)
from typer.testing import CliRunner

import agent_orchestrator.executors as executors_pkg
from agent_orchestrator.cli import app
from agent_orchestrator.executors.fake import FakeExecutor

RUN_ID = "baseline-run"
TASKS = ("task_a", "task_b", "task_c")
_TS = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d")
# Wall-clock / host dependent scalars: value replaced by its type name, key kept.
_VOLATILE_KEYS = {"duration_s", "elapsed_s", "wall_s", "pid", "hostname"}


def normalize(node: Any, root: Path) -> Any:
    """Replace timestamps, durations and absolute paths so two runs compare equal."""
    if isinstance(node, dict):
        return {
            k: (type(v).__name__ if k in _VOLATILE_KEYS else normalize(v, root))
            for k, v in sorted(node.items())
        }
    if isinstance(node, list):
        return [normalize(v, root) for v in node]
    if isinstance(node, str):
        if _TS.match(node):
            return "<ts>"
        return node.replace(str(root), "<root>")
    return node


def run_ungoverned(tmp: Path, monkeypatch: Any) -> dict[str, Any]:
    """Ungoverned, cost-less ``ao run`` (no cap flag, no schedule); normalized state.json."""
    wf = _write_small_workflow(tmp)
    rs = _write_simple_reposet(tmp)
    ag = _write_simple_agents(tmp)
    _write_instructions(tmp)

    class _Costed(executors_pkg.DispatchExecutor):
        def __init__(self) -> None:
            super().__init__()
            self._fake = FakeExecutor()  # no cost_usd: the pre-epic tree cannot record one

    monkeypatch.setattr(executors_pkg, "DispatchExecutor", _Costed)
    monkeypatch.setenv("AO_WORKSPACE_ROOT", str(tmp))
    monkeypatch.setenv("HOME", str(tmp / "home"))
    res = CliRunner().invoke(
        app,
        ["run", "--workflow", str(wf), "--reposets", str(rs), "--agents", str(ag),
         "--run-id", RUN_ID],
    )  # fmt: skip
    assert res.exit_code == 0, res.output
    state = json.loads(next(tmp.rglob(f"{RUN_ID}/state.json")).read_text())
    return normalize(state, tmp)  # type: ignore[no-any-return]
