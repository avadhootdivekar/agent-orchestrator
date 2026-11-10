"""T-Rk4Wn7 L4: T4 integration conflict must not strand independent sibling work.

Through the CLI with real git worktrees: two tasks conflict on one file and the ladder is
``["auto"]`` so the loser fails at T4 (retained worktree, ``integration.failed``). With
``--max-parallel 2`` (fewer slots than ready tasks) the independent siblings c/d are still
queued when that failure settles; D3 requires they keep dispatching and land. Only the
dependent of the failed task stays unrun. Reverting the D3 engine fix (halt on task
failure) leaves c/d ``pending`` and fails this test.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from agent_orchestrator.cli import app
from tests.test_e2e_isolation import (  # noqa: F401
    _create_test_repo,
    _env,
    _events_named,
    _freeze_run_id,
    _git,
    _isolated_git_env,
    _patch_executor,
    _run_dir,
    _setup_workflow,
    _state,
    _workspace,
)

runner = CliRunner()


def test_t4_conflict_does_not_strand_independent_siblings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = _workspace(tmp_path)
    repo = _create_test_repo(ws, files={"README.md": "hi\n", "f.txt": "line1\nline2\n"})
    run_id = _freeze_run_id(monkeypatch)
    _setup_workflow(
        ws,
        [
            {"id": "task_a", "agent": "ag", "outputs": ["output/a.txt"]},
            {"id": "task_b", "agent": "ag", "outputs": ["output/b.txt"]},
            {"id": "task_c", "agent": "ag", "outputs": ["output/c.txt"]},
            {"id": "task_d", "agent": "ag", "outputs": ["output/d.txt"]},
            {
                "id": "task_dep",
                "agent": "ag",
                "depends_on": ["task_b"],
                "outputs": ["output/e.txt"],
            },
        ],
        integration_config={"sync_checkout": "never", "ladder": ["auto"]},
    )
    _patch_executor(
        monkeypatch,
        {
            "task_a": {"core": {"f.txt": "line1-A\nline2\n"}},
            "task_b": {"core": {"f.txt": "line1-B\nline2\n"}},
            "task_c": {"core": {"c.txt": "c\n"}},
            "task_d": {"core": {"d.txt": "d\n"}},
        },
    )

    result = runner.invoke(
        app,
        [
            "run",
            "--workflow",
            str(ws / "workflow.json"),
            "--reposets",
            str(ws / "reposets.json"),
            "--agents",
            str(ws / "agents.json"),
            "--max-parallel",
            "2",
        ],
        env=_env(ws),
    )
    assert result.exit_code == 1, result.output

    state = _state(ws, run_id)
    failed = _events_named(_run_dir(ws, run_id), "integration.failed")
    assert len(failed) == 1, failed
    loser = failed[0]["task_id"]
    assert loser in ("task_a", "task_b")
    assert state["status"] == "failed"
    assert state["tasks"][loser]["status"] == "failed"

    # Siblings queued behind the failure (2 slots, 4 ready) still ran and landed.
    for tid in ("task_c", "task_d"):
        assert state["tasks"][tid]["status"] == "succeeded", (tid, state["tasks"][tid])
        assert state["task_integration"][tid]["status"] == "integrated", tid
    branch = state["integration"]["branch"]
    assert _git(["show", f"{branch}:c.txt"], repo) == "c\n"
    assert _git(["show", f"{branch}:d.txt"], repo) == "d\n"

    # Only the failed task's dependent is left unrun (when the loser is task_b).
    if loser == "task_b":
        assert state["tasks"]["task_dep"]["status"] in ("pending", "not_taken")
