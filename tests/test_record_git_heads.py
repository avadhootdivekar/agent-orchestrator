"""FR-12: `record_git_heads` opt-out -- precedence, engine gating, survival reason, CLI e2e."""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest
from typer.testing import CliRunner

from agent_orchestrator import engine as engine_mod
from agent_orchestrator.cli import _resolve_record_git_heads, app
from agent_orchestrator.models import RunState
from agent_orchestrator.project_config import ProjectConfig, scaffold_init
from agent_orchestrator.survival import REASON_HEADS_ABSENT, REASON_HEADS_OPTED_OUT
from tests.test_e2e_cli_isolation import _env, _git_repo, _patch_dispatch_executor, _write_specs
from tests.test_e2e_cli_survival import _invoke_run
from tests.test_survival import lines

runner = CliRunner()


@pytest.fixture(autouse=True)
def _git_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("AO_STATE_DIR", str(tmp_path / "ao-state"))
    monkeypatch.delenv("AO_RECORD_GIT_HEADS", raising=False)


class TestPrecedence:
    def _cfg(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, text: str | None) -> None:
        monkeypatch.chdir(tmp_path)
        if text is not None:
            (tmp_path / ".ao").mkdir()
            (tmp_path / ".ao" / "config.yaml").write_text(text)

    def test_default_on(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self._cfg(tmp_path, monkeypatch, None)
        assert _resolve_record_git_heads(None) is True

    def test_config_off(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self._cfg(tmp_path, monkeypatch, "record_git_heads: false\n")
        assert _resolve_record_git_heads(None) is False

    def test_env_beats_config(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self._cfg(tmp_path, monkeypatch, "record_git_heads: false\n")
        monkeypatch.setenv("AO_RECORD_GIT_HEADS", "1")
        assert _resolve_record_git_heads(None) is True
        monkeypatch.setenv("AO_RECORD_GIT_HEADS", "0")
        self._cfg(tmp_path, monkeypatch, None)
        assert _resolve_record_git_heads(None) is False

    def test_cli_beats_env_and_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._cfg(tmp_path, monkeypatch, "record_git_heads: true\n")
        monkeypatch.setenv("AO_RECORD_GIT_HEADS", "true")
        assert _resolve_record_git_heads(False) is False
        monkeypatch.setenv("AO_RECORD_GIT_HEADS", "0")
        assert _resolve_record_git_heads(True) is True

    def test_schema_and_template(self, tmp_path: Path) -> None:
        assert ProjectConfig().record_git_heads is None
        path = scaffold_init(tmp_path)
        text = path.read_text()
        assert "record_git_heads" in text and "AO_RECORD_GIT_HEADS" in text

    def test_flag_in_help_of_run_and_resume(self) -> None:
        for cmd in ("run", "resume"):
            out = runner.invoke(app, [cmd, "--help"], env={"COLUMNS": "200"}).output
            assert "--no-record-git-heads" in out and "AO_RECORD_GIT_HEADS" in out

    def test_readme_documents_setting(self) -> None:
        readme = (Path(__file__).parent.parent / "README.md").read_text()
        assert "AO_RECORD_GIT_HEADS" in readme and "record_git_heads" in readme


class TestSurvivalReason:
    def test_reason_by_state(self) -> None:
        from agent_orchestrator.survival import _attribution_reason

        def st(heads: bool | None = None, start: dict[str, str] | None = None) -> RunState:
            return RunState(
                run_id="r",
                workflow_id="w",
                repo_set="s",
                started_at="t",
                updated_at="t",
                record_git_heads=heads,
                git_start_heads=start or {},
            )

        assert _attribution_reason(st(False)) == REASON_HEADS_OPTED_OUT
        assert _attribution_reason(st()) == REASON_HEADS_ABSENT
        assert _attribution_reason(st(start={"k": "a"})) is None
        assert _attribution_reason(st(True, {"k": "a"})) is None


class TestEngineGating:
    def _workflow(self, tmp_path: Path) -> None:
        _git_repo(tmp_path)
        _write_specs(tmp_path)
        wf = json.loads((tmp_path / "workflow.json").read_text())
        wf["defaults"] = {}
        wf.pop("integration", None)
        (tmp_path / "workflow.json").write_text(json.dumps(wf))

    def _state(self, tmp_path: Path) -> dict:
        run_dir = next((tmp_path / ".orchestrator" / "runs").iterdir())
        return json.loads((run_dir / "state.json").read_text())

    def test_flag_off_makes_no_head_recording_calls(self, tmp_path: Path) -> None:
        self._workflow(tmp_path)
        with (
            mock.patch.object(engine_mod, "record_git_start") as rgs,
            mock.patch.object(engine_mod, "current_heads") as ch,
        ):
            res = _invoke_run(tmp_path, ["--no-record-git-heads"])
            assert res.exit_code == 0, res.output
            rgs.assert_not_called()
            ch.assert_not_called()
        state = self._state(tmp_path)
        assert state["record_git_heads"] is False
        assert state["git_repos"] == {} and state["git_start_heads"] == {}
        for t in state["tasks"].values():
            assert t["start_heads"] == {} and t["end_heads"] == {}

    def test_env_off_and_flag_on_default(self, tmp_path: Path) -> None:
        self._workflow(tmp_path)
        with mock.patch.object(engine_mod, "record_git_start") as rgs:
            env = dict(_env(tmp_path), AO_RECORD_GIT_HEADS="0")
            res = runner.invoke(
                app,
                [
                    "run",
                    "--workflow", str(tmp_path / "workflow.json"),
                    "--reposets", str(tmp_path / "reposets.json"),
                    "--agents", str(tmp_path / "agents.json"),
                ],
                env=env,
            )  # fmt: skip
            assert res.exit_code == 0, res.output
            rgs.assert_not_called()

    def test_default_on_records(self, tmp_path: Path) -> None:
        self._workflow(tmp_path)
        res = _invoke_run(tmp_path, [])
        assert res.exit_code == 0, res.output
        state = self._state(tmp_path)
        assert state["record_git_heads"] is True and state["git_start_heads"]


class TestE2E:
    def test_serial_flag_off_report_explains_reason(self, tmp_path: Path) -> None:
        _git_repo(tmp_path)
        _write_specs(tmp_path)
        wf = json.loads((tmp_path / "workflow.json").read_text())
        wf["defaults"] = {}
        wf.pop("integration", None)
        (tmp_path / "workflow.json").write_text(json.dumps(wf))
        assert _invoke_run(tmp_path, ["--no-record-git-heads"]).exit_code == 0

        rep = runner.invoke(app, ["report-survival"], env=_env(tmp_path))
        assert rep.exit_code == 0, rep.output
        assert "intentionally not recorded" in rep.output

        rep = runner.invoke(app, ["report-survival", "--json"], env=_env(tmp_path))
        assert rep.exit_code == 0, rep.output
        run = json.loads(rep.output)["runs"][0]
        assert run["attribution_reason"] == REASON_HEADS_OPTED_OUT
        assert run["total"]["attribution"] in ("time-window", "none")

    def test_isolated_flag_off_keeps_landed_ranges_and_attribution(
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
        res = _invoke_run(tmp_path, ["--max-parallel", "3", "--no-record-git-heads"])
        assert res.exit_code == 0, res.output
        run_dir = next((tmp_path / ".orchestrator" / "runs").iterdir())
        state = json.loads((run_dir / "state.json").read_text())
        assert state["git_start_heads"] == {}
        for tid in ("task_a", "task_b", "task_c"):
            assert state["task_integration"][tid]["landed_ranges"]
            assert state["tasks"][tid]["start_heads"] == {}

        rep = runner.invoke(
            app,
            ["report-survival", "--json", "--ref", "ao/" + run_dir.name + "/integration"],
            env=_env(tmp_path),
        )
        assert rep.exit_code == 0, rep.output
        run = json.loads(rep.output)["runs"][0]
        assert run["attribution_reason"] == REASON_HEADS_OPTED_OUT
        tasks = {t["task_id"]: t for t in run["tasks"]}
        for tid in ("task_a", "task_b", "task_c"):
            assert tasks[tid]["attribution"] == "isolation"
            assert tasks[tid]["lines_added"] == 12

        # report-usage keeps working on a heads-less run
        usage = runner.invoke(app, ["report-usage", "--json"], env=_env(tmp_path))
        assert usage.exit_code == 0, usage.output
