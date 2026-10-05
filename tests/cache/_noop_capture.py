r"""Golden capture for the I-2 byte-identical snapshot (HLD 8.7.5, NFR-1).

ONE script produces both sides of the comparison: it was run against a worktree of base
`bb6d8a0` to create `tests/fixtures/result_cache/golden/`, and `test_noop_proof.py` runs the very
same file against the current tree. It drives the real CLI (`ao run` through typer's
`CliRunner`) on a three-task fake-executor workflow (chain `a -> b`, independent `c`) and writes
the two artefacts byte for byte:

    <out-dir>/status.json   the run's `.orchestrator/runs/<run_id>/status.json`
    <out-dir>/stdout.txt    what `ao run` printed on stdout

Determinism: `agent_orchestrator.runstate._utc_now` is pinned to `FIXED_NOW` (so the run id and
every `updated_at` are fixed); every `AO_*` variable is dropped and `HOME` is sandboxed. The only
normalisation is replacing the absolute workspace path with `<WS>` (the capture workspace is a
fresh temp dir, and `output_artifact_path` in status.json is absolute). Nothing else is rewritten.

Usage, from the repo root (`-m` keeps `tests` importable; PYTHONPATH picks WHICH
`agent_orchestrator` is exercised -- see the ticket HANDOFF for the exact base-tree command):

    PYTHONPATH=<tree>/src python -m tests.cache._noop_capture --variant serial --out-dir DIR
    PYTHONPATH=<tree>/src python -m tests.cache._noop_capture --variant parallel --out-dir DIR
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

FIXED_NOW = datetime(2026, 1, 1, tzinfo=UTC)
WORKFLOW_ID = "golden-fixture"
WORKSPACE_PLACEHOLDER = "<WS>"
STATUS_FILE = "status.json"
STDOUT_FILE = "stdout.txt"
# variant -> extra `ao run` arguments. Serial is the engine default (no flag at all).
VARIANTS: dict[str, list[str]] = {
    "serial": [],
    "parallel": ["--max-parallel", "3"],
}
_INSTRUCTION = "specs/instructions/stub.md"
_ENV_PREFIX = "AO_"


def _task(task_id: str, depends_on: list[str], inputs: list[str]) -> dict[str, object]:
    return {
        "id": task_id,
        "agent": "ag",
        "instruction": _INSTRUCTION,
        "depends_on": depends_on,
        "inputs": inputs,
        "outputs": [f"output/{task_id}.txt"],
    }


def write_fixture(ws: Path) -> tuple[Path, Path, Path]:
    """Write the fixture workflow, reposets and agents; return their paths."""
    (ws / _INSTRUCTION).parent.mkdir(parents=True)
    (ws / _INSTRUCTION).write_text("# stub instruction\n")
    workflow = {
        "version": "1.0",
        "id": WORKFLOW_ID,
        "repo_set": "rs",
        "tasks": [
            _task("a", [], []),
            _task("b", ["a"], ["output/a.txt"]),
            _task("c", [], []),
        ],
    }
    reposets = {
        "version": "1.0",
        "repo_sets": {
            "rs": {
                "workspace_root": ".",
                "repos": [{"id": "core", "path": ".", "role": "primary"}],
            }
        },
    }
    agents = {"version": "1.0", "agents": {"ag": {"executor": "fake"}}}
    paths = (ws / "workflow.json", ws / "reposets.json", ws / "agents.json")
    for path, doc in zip(paths, (workflow, reposets, agents), strict=True):
        path.write_text(json.dumps(doc, indent=2))
    return paths


def normalise(text: str, ws: Path) -> str:
    """Replace the absolute workspace path (raw and symlink-resolved) with `<WS>`."""
    for form in sorted({str(ws), str(ws.resolve())}, key=len, reverse=True):
        text = text.replace(form, WORKSPACE_PLACEHOLDER)
    return text


def capture(variant: str, out_dir: Path) -> int:
    # Imports are deferred so PYTHONPATH (base worktree vs current tree) decides which code runs.
    from typer.testing import CliRunner

    import agent_orchestrator.runstate as runstate
    from agent_orchestrator.cli import app

    for var in [v for v in os.environ if v.startswith(_ENV_PREFIX)]:
        del os.environ[var]
    with tempfile.TemporaryDirectory(prefix="noop-golden-") as tmp:
        ws = Path(tmp) / "ws"
        ws.mkdir()
        os.environ["HOME"] = str(Path(tmp) / "home")
        os.environ["AO_WORKSPACE_ROOT"] = str(ws)
        os.chdir(ws)
        workflow, reposets, agents = write_fixture(ws)
        runstate._utc_now = lambda: FIXED_NOW
        argv = [
            "run",
            "--workflow",
            str(workflow),
            "--reposets",
            str(reposets),
            "--agents",
            str(agents),
            *VARIANTS[variant],
        ]
        result = CliRunner().invoke(app, argv)
        if result.exit_code != 0:
            sys.stderr.write(f"ao run failed ({result.exit_code}):\n{result.output}\n")
            return 1
        snapshots = sorted(ws.glob(".orchestrator/runs/*/status.json"))
        if len(snapshots) != 1:
            sys.stderr.write(f"expected exactly one status.json, found {snapshots}\n")
            return 1
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / STATUS_FILE).write_text(normalise(snapshots[0].read_text(), ws))
        (out_dir / STDOUT_FILE).write_text(normalise(result.stdout, ws))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--variant", choices=sorted(VARIANTS), required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    return capture(args.variant, args.out_dir.resolve())


if __name__ == "__main__":
    raise SystemExit(main())
