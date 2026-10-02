"""CLI end-to-end tests for `ao report-survival` and the engine's git-head recording."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agent_orchestrator.artifacts import LocalFsArtifactStore
from agent_orchestrator.cli import app
from agent_orchestrator.models import RunIntegrationState, RunState, TaskIntegrationState
from agent_orchestrator.runstate import RunStateStore
from tests.test_e2e_cli_isolation import _env, _git_repo, _patch_dispatch_executor, _write_specs
from tests.test_survival import KEY, commit, git, lines, make_repo

runner = CliRunner()


@pytest.fixture(autouse=True)
def _isolated_git_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("AO_STATE_DIR", str(tmp_path / "ao-state"))


def _save(ws: Path, state: RunState) -> None:
    RunStateStore(str(ws), LocalFsArtifactStore(str(ws))).save(state)


def _state(repo: Path, sha: str) -> RunState:
    return RunState(
        run_id="run-1",
        workflow_id="wf",
        repo_set="rs",
        started_at="2026-01-01T12:00:00+00:00",
        updated_at="2026-01-01T13:00:00+00:00",
        git_repos={KEY: str(repo)},
        integration=RunIntegrationState(active=True),
        task_integration={
            "t1": TaskIntegrationState(isolation="worktree", squash_commits={KEY: sha})
        },
    )


def _ws(tmp_path: Path) -> tuple[Path, str]:
    repo = make_repo(tmp_path, "ws")
    sha = commit(repo, {"a.py": lines("alpha", 12)})
    _save(repo, _state(repo, sha))
    return repo, sha


class TestReportSurvivalCli:
    def test_table_and_json(self, tmp_path: Path) -> None:
        repo, sha = _ws(tmp_path)
        commit(repo, {"a.py": lines("beta", 12)})
        res = runner.invoke(app, ["report-survival", "--workspace", str(repo)])
        assert res.exit_code == 0, res.output
        assert "t1" in res.output and "isolation" in res.output
        assert "likely_worthless" in res.output and "0%" in res.output

        res = runner.invoke(app, ["report-survival", "--workspace", str(repo), "--json"])
        data = json.loads(res.output)
        task = data["runs"][0]["tasks"][0]
        assert task["task_id"] == "t1" and task["lines_added"] == 12
        assert task["flags"] == ["likely_worthless"]
        assert data["runs"][0]["total"]["lines_survived"] == 0

    def test_ref_option_changes_the_measurement(self, tmp_path: Path) -> None:
        repo, sha = _ws(tmp_path)
        commit(repo, {"a.py": None})
        res = runner.invoke(
            app,
            [
                "report-survival",
                "--workspace",
                str(repo),
                "--ref",
                sha,
                "--json",
                "--run-id",
                "run-1",
            ],
        )
        assert res.exit_code == 0, res.output
        assert json.loads(res.output)["runs"][0]["tasks"][0]["survival_rate"] == 1.0

    @pytest.mark.parametrize("bad", ["-x", "--output=/tmp/evil", "a b"])
    def test_option_like_ref_rejected(self, tmp_path: Path, bad: str) -> None:
        repo, _ = _ws(tmp_path)
        res = runner.invoke(app, ["report-survival", "--workspace", str(repo), "--ref", bad])
        assert res.exit_code == 2 and "invalid --ref" in res.output

    def test_unresolvable_ref_fails(self, tmp_path: Path) -> None:
        repo, _ = _ws(tmp_path)
        res = runner.invoke(app, ["report-survival", "--workspace", str(repo), "--ref", "nope"])
        assert res.exit_code == 1 and "does not resolve" in res.output

    def test_no_git_workspace_degrades(self, tmp_path: Path) -> None:
        ws = tmp_path / "plain"
        ws.mkdir()
        _save(ws, _state(ws, "deadbeef"))
        res = runner.invoke(app, ["report-survival", "--workspace", str(ws)])
        assert res.exit_code == 0, res.output
        assert "unavailable" in res.output

    def test_empty_workspace(self, tmp_path: Path) -> None:
        res = runner.invoke(app, ["report-survival", "--workspace", str(tmp_path)])
        assert res.exit_code == 0 and "no runs" in res.output


def _invoke_run(tmp_path: Path, extra: list[str]):
    return runner.invoke(
        app,
        [
            "run",
            "--workflow", str(tmp_path / "workflow.json"),
            "--reposets", str(tmp_path / "reposets.json"),
            "--agents", str(tmp_path / "agents.json"),
            *extra,
        ],
        env=_env(tmp_path),
    )  # fmt: skip


class TestEngineRecording:
    def test_serial_run_records_start_and_end_heads(self, tmp_path: Path) -> None:
        repo = _git_repo(tmp_path)
        _write_specs(tmp_path)
        wf = json.loads((tmp_path / "workflow.json").read_text())
        wf["defaults"] = {}  # no isolation
        wf.pop("integration", None)
        (tmp_path / "workflow.json").write_text(json.dumps(wf))
        res = _invoke_run(tmp_path, ["--max-parallel", "1"])
        assert res.exit_code == 0, res.output

        run_dir = next((tmp_path / ".orchestrator" / "runs").iterdir())
        state = json.loads((run_dir / "state.json").read_text())
        head = git(repo, "rev-parse", "HEAD")
        assert list(state["git_start_heads"].values()) == [head]
        assert list(state["git_repos"].values()) == [str(repo)]
        for t in state["tasks"].values():
            assert list(t["end_heads"].values()) == [head]
            assert list(t["start_heads"].values()) == [head]

        res = runner.invoke(app, ["report-survival", "--json"], env=_env(tmp_path))
        assert res.exit_code == 0, res.output
        rows = json.loads(res.output)["runs"][0]["tasks"]
        assert {r["attribution"] for r in rows} == {"none"}  # no commits => n/a

    def test_isolated_run_records_landed_ranges_and_is_attributed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _git_repo(tmp_path)
        _write_specs(tmp_path)
        _patch_dispatch_executor(
            monkeypatch,
            {
                "task_a": {"core": {"a_code.txt": lines("alpha", 12)}},
                "task_b": {"core": {"b_code.txt": lines("beta", 12)}},
                "task_c": {"core": {"c_code.txt": lines("gamma", 12)}},
            },
        )
        res = _invoke_run(tmp_path, ["--max-parallel", "3"])
        assert res.exit_code == 0, res.output
        run_dir = next((tmp_path / ".orchestrator" / "runs").iterdir())
        state = json.loads((run_dir / "state.json").read_text())
        for tid in ("task_a", "task_b", "task_c"):
            ranges = state["task_integration"][tid]["landed_ranges"]
            assert len(next(iter(ranges.values()))) == 1

        rep = runner.invoke(
            app, ["report-survival", "--json", "--ref", "ao/" + run_dir.name + "/integration"],
            env=_env(tmp_path),
        )  # fmt: skip
        assert rep.exit_code == 0, rep.output
        tasks = {t["task_id"]: t for t in json.loads(rep.output)["runs"][0]["tasks"]}
        for tid in ("task_a", "task_b", "task_c"):
            assert tasks[tid]["attribution"] == "isolation"
            assert tasks[tid]["lines_added"] == 12 and tasks[tid]["survival_rate"] == 1.0
