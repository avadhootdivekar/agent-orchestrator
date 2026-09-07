"""CliRunner e2e tests for E-Wk9Tz3 task isolation (T-En8Hd4).

Outermost-boundary coverage (CLAUDE.md's e2e rule): drives `ao run`/`ao resume` through
`typer.testing.CliRunner`, never calling `Orchestrator` directly except to set up a
pre-crash RunState for the resume test (mirroring `tests/test_e2e_cli.py`'s own
`TestE2EResumeCLI` pattern).

`DispatchExecutor` is constructed fresh, with no config, by `cli.py`'s `run`/`resume`
commands -- so a plain `agents.json` with ``"executor": "fake"`` gives every task a
`FakeExecutor()` that writes declared `outputs` only, never a TRACKED file inside a repo.
To prove real git landing (not just an Empty integration), `agent_orchestrator.executors.
DispatchExecutor` is monkeypatched to a subclass that configures its inner `FakeExecutor`
with `repo_writes` -- `cli.py` does `from .executors import DispatchExecutor` as a LOCAL
import inside each command body, so this patch (applied to the `executors` package
attribute before `runner.invoke`) is picked up exactly like a real dependency override,
without touching `cli.py`'s source.

Declared task `outputs` are placed OUTSIDE the repo (the consumer's own convention, HLD
§7.2) -- an isolated task's own declared-output check runs on the shared checkout
immediately at settle, before any barrier sync, so an in-repo declared output would
(correctly, per R-2's disposition) never be found there yet.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

import agent_orchestrator.executors as executors_pkg
from agent_orchestrator.cli import app
from agent_orchestrator.engine import Orchestrator
from agent_orchestrator.executors.fake import FakeExecutor
from agent_orchestrator.runstate import RunStateStore

runner = CliRunner()


def _git(args: list[str], cwd: Path) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)
    assert result.returncode == 0, f"git {args} failed: {result.stderr}"
    return result.stdout


def _git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _git(["init", "-q", "-b", "main"], repo)
    _git(["config", "user.name", "ao-test"], repo)
    _git(["config", "user.email", "ao-test@example.invalid"], repo)
    (repo / "README.md").write_text("hi\n")
    _git(["add", "-A"], repo)
    _git(["commit", "-q", "-m", "base"], repo)
    return repo


def _write_specs(tmp_path: Path) -> None:
    (tmp_path / "specs" / "instructions").mkdir(parents=True, exist_ok=True)
    for name in ("task_a", "task_b", "task_c"):
        (tmp_path / "specs" / "instructions" / f"{name}.md").write_text(f"# {name}\n")

    workflow = {
        "version": "1.0",
        "id": "iso-e2e-wf",
        "repo_set": "rs",
        "defaults": {"isolation": "worktree"},
        # sync_checkout: never -- keeps the checked-out branch literally untouched, per
        # this ticket's own e2e AC ("the checked out branch is untouched").
        # sync_checkout: never (checked-out branch untouched, per this ticket's own e2e
        # AC); ladder drops "llm" so V2 (integration.resolver_agent required whenever
        # "llm" is in the ladder) doesn't fire -- this fixture never exercises T2/T3.
        "integration": {"sync_checkout": "never", "ladder": ["auto", "mechanical"]},
        "tasks": [
            {
                "id": "task_a",
                "agent": "ag",
                "instruction": "specs/instructions/task_a.md",
                "outputs": ["output/a.txt"],
            },
            {
                "id": "task_b",
                "agent": "ag",
                "instruction": "specs/instructions/task_b.md",
                "outputs": ["output/b.txt"],
            },
            {
                "id": "task_c",
                "agent": "ag",
                "instruction": "specs/instructions/task_c.md",
                "depends_on": ["task_a", "task_b"],
                "inputs": ["output/a.txt", "output/b.txt"],
                "outputs": ["output/c.txt"],
            },
        ],
    }
    (tmp_path / "workflow.json").write_text(json.dumps(workflow))

    reposets = {
        "version": "1.0",
        "repo_sets": {
            "rs": {
                "workspace_root": str(tmp_path),
                "repos": [{"id": "core", "path": "repo", "role": "primary"}],
            }
        },
    }
    (tmp_path / "reposets.json").write_text(json.dumps(reposets))

    agents = {"version": "1.0", "agents": {"ag": {"executor": "fake"}}}
    (tmp_path / "agents.json").write_text(json.dumps(agents))


def _patch_dispatch_executor(monkeypatch: pytest.MonkeyPatch, repo_writes: dict) -> None:
    """`cli.py`'s `run`/`resume` do ``from .executors import DispatchExecutor`` as a LOCAL
    import inside each command body -- patching the `executors` package's own attribute
    (read fresh at call time) lets a test swap in a `DispatchExecutor` whose inner
    `FakeExecutor` writes real tracked files into an isolated task's repo, without
    touching `cli.py`.
    """

    class _RepoWritingDispatchExecutor(executors_pkg.DispatchExecutor):
        def __init__(self) -> None:
            super().__init__()
            self._fake = FakeExecutor(repo_writes=repo_writes)

    monkeypatch.setattr(executors_pkg, "DispatchExecutor", _RepoWritingDispatchExecutor)


def _env(tmp_path: Path) -> dict[str, str]:
    return {
        "AO_WORKSPACE_ROOT": str(tmp_path),
        "HOME": str(tmp_path / "home"),
        "AO_STATE_DIR": str(tmp_path / "ao-state"),
    }


class TestIsolatedRunLands:
    def test_two_disjoint_tasks_plus_a_dependent_all_land(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _git_repo(tmp_path)
        _write_specs(tmp_path)
        _patch_dispatch_executor(
            monkeypatch,
            {
                "task_a": {"core": {"a_code.txt": "a code\n"}},
                "task_b": {"core": {"b_code.txt": "b code\n"}},
                "task_c": {"core": {"c_code.txt": "c code\n"}},
            },
        )

        result = runner.invoke(
            app,
            [
                "run",
                "--workflow",
                str(tmp_path / "workflow.json"),
                "--reposets",
                str(tmp_path / "reposets.json"),
                "--agents",
                str(tmp_path / "agents.json"),
                "--max-parallel",
                "3",
            ],
            env=_env(tmp_path),
        )
        assert result.exit_code == 0, f"CLI failed:\n{result.output}"
        assert "succeeded" in result.output.lower()

        # Declared outputs (outside the repo) exist in the shared workspace.
        assert (tmp_path / "output" / "a.txt").exists()
        assert (tmp_path / "output" / "b.txt").exists()
        assert (tmp_path / "output" / "c.txt").exists()

        # Read back the run's own state to find the integration branch.
        store_state = json.loads(
            next((tmp_path / ".orchestrator" / "runs").iterdir()).joinpath("state.json").read_text()
        )
        branch = store_state["integration"]["branch"]
        assert branch is not None

        # All three repo-tracked files are reachable from the integration ref.
        for fname, content in (
            ("a_code.txt", "a code\n"),
            ("b_code.txt", "b code\n"),
            ("c_code.txt", "c code\n"),
        ):
            shown = _git(["show", f"{branch}:{fname}"], repo)
            assert shown == content

        # The checked-out branch (main) is untouched (sync_checkout: never): none of the
        # three files exist there, and README.md is unchanged.
        for fname in ("a_code.txt", "b_code.txt", "c_code.txt"):
            assert not (repo / fname).exists()
        cp = subprocess.run(
            ["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True, check=False
        )
        assert cp.stdout.strip() == ""  # clean tree -- nothing landed into the checkout

        # rev-list --count base..branch == 1 PER task commit means 3 commits total ahead
        # of main (squash = one commit per task, HLD §8.2 AC-4-style invariant).
        ahead = _git(["rev-list", "--count", f"main..{branch}"], repo).strip()
        assert ahead == "3"


class TestResumeAfterCrash:
    def test_resume_completes_after_a_simulated_crash(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Mirrors `tests/test_e2e_cli.py::TestE2EResumeCLI`'s own pattern: an initial run
        (via the Python API, simulating the process that crashed) leaves one task failed
        mid-isolation; `ao resume` (via the CLI, the outer boundary) completes it.
        """
        from agent_orchestrator.artifacts import LocalFsArtifactStore

        repo = _git_repo(tmp_path)
        _write_specs(tmp_path)

        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        monkeypatch.setenv("AO_STATE_DIR", str(tmp_path / "ao-state"))

        from agent_orchestrator.spec import load_workflow

        wf = load_workflow(str(tmp_path / "workflow.json"))
        import agent_orchestrator.config as config_mod

        reposet_map = config_mod.load_reposets(str(tmp_path / "reposets.json"))
        agent_map = config_mod.load_agents(str(tmp_path / "agents.json"))

        store = LocalFsArtifactStore(str(tmp_path))
        rs_store = RunStateStore(str(tmp_path), store)

        # First "process": task_a fails outright (simulating the crash's last observed
        # state) -- the run halts with task_b/task_c never dispatched.
        orch1 = Orchestrator(FakeExecutor(behaviors={"task_a": "fail"}), store, rs_store)
        state1 = orch1.run(wf, reposet_map, agent_map)
        assert state1.status == "failed"
        run_id = state1.run_id

        # Resume via the CLI (outer boundary) with a fresh, succeeding executor patched in.
        _patch_dispatch_executor(
            monkeypatch,
            {
                "task_a": {"core": {"a_code.txt": "a code\n"}},
                "task_b": {"core": {"b_code.txt": "b code\n"}},
                "task_c": {"core": {"c_code.txt": "c code\n"}},
            },
        )
        result = runner.invoke(
            app,
            [
                "resume",
                "--run-id",
                run_id,
                "--workflow",
                str(tmp_path / "workflow.json"),
                "--reposets",
                str(tmp_path / "reposets.json"),
                "--agents",
                str(tmp_path / "agents.json"),
            ],
            env=_env(tmp_path),
        )
        assert result.exit_code == 0, f"CLI resume failed:\n{result.output}"
        assert "succeeded" in result.output.lower()
        assert (tmp_path / "output" / "a.txt").exists()
        assert (tmp_path / "output" / "b.txt").exists()
        assert (tmp_path / "output" / "c.txt").exists()

        final_state = json.loads(
            (tmp_path / ".orchestrator" / "runs" / run_id / "state.json").read_text()
        )
        assert final_state["status"] == "succeeded"
        branch = final_state["integration"]["branch"]
        assert branch is not None
        shown = _git(["show", f"{branch}:a_code.txt"], repo)
        assert shown == "a code\n"
