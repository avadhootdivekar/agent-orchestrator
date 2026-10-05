"""A one-task fake-executor workflow on disk, for the CLI wiring tests (T-o95l1M).

Imported by the test module AND by the child interpreters of the lazy-import checks, so it
depends only on the standard library.
"""

from __future__ import annotations

import json
from pathlib import Path

TASK_OUTPUT = "out/t.md"


def write_fixture(root: Path, *, workspace: Path | None = None, opted_in: bool = True) -> list[str]:
    """Write the spec files under *root*; the repo set points at *workspace* (default *root*).

    Returns the shared `--workflow/--reposets/--agents` CLI arguments. The task opts in with
    `cache: true` when *opted_in*; the agent uses the `fake` executor (cacheable).
    """
    ws = workspace or root
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "instructions").mkdir(exist_ok=True)
    (ws / "instructions" / "t.md").write_text("do it", encoding="utf-8")
    task: dict[str, object] = {
        "id": "t",
        "agent": "ag",
        "instruction": "instructions/t.md",
        "outputs": [TASK_OUTPUT],
        "skip_if_outputs_exist": False,  # a hit is only reachable past should_skip
    }
    if opted_in:
        task["cache"] = True
    (root / "wf.json").write_text(
        json.dumps({"version": "1.0", "id": "wiring-wf", "repo_set": "rs", "tasks": [task]}),
        encoding="utf-8",
    )
    (root / "rs.json").write_text(
        json.dumps(
            {
                "version": "1.0",
                "repo_sets": {
                    "rs": {
                        "workspace_root": str(ws),
                        "repos": [{"id": "core", "path": ".", "role": "primary"}],
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    (root / "ag.json").write_text(
        json.dumps({"version": "1.0", "agents": {"ag": {"executor": "fake"}}}), encoding="utf-8"
    )
    return [
        "--workflow",
        str(root / "wf.json"),
        "--reposets",
        str(root / "rs.json"),
        "--agents",
        str(root / "ag.json"),
    ]
