"""T-28J9oR AC-11: `--cache/--no-cache`, the `ao cache` group and the resolution half of
`_build_result_cache`, driven through `CliRunner` (the outermost boundary)."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

import pytest
import typer
import typer.main
from typer.testing import CliRunner

from agent_orchestrator import cli as cli_mod
from agent_orchestrator.cli import app

runner = CliRunner()

# The run-id timestamp and the log `ts` fields differ between two otherwise identical runs.
_RUN_ID_RE = re.compile(r"\d{8}t\d{6}z", re.IGNORECASE)
_TS_RE = re.compile(r'"ts": "[^"]*"')


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for var in ("AO_CACHE", "AO_WORKSPACE_ROOT"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)  # no `.ao/config.yaml` above tmp_path's .git-less tree
    monkeypatch.setenv("AO_WORKSPACE_ROOT", str(tmp_path))


def _fixture(tmp_path: Path) -> list[str]:
    """A one-task workflow on the fake executor; returns the shared CLI args."""
    instr = tmp_path / "instructions"
    instr.mkdir()
    (instr / "t.md").write_text("do it", encoding="utf-8")
    (tmp_path / "wf.json").write_text(
        json.dumps(
            {
                "version": "1.0",
                "id": "flags-wf",
                "repo_set": "rs",
                "tasks": [
                    {
                        "id": "t",
                        "agent": "ag",
                        "instruction": "instructions/t.md",
                        "outputs": ["out/t.md"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "rs.json").write_text(
        json.dumps(
            {
                "version": "1.0",
                "repo_sets": {
                    "rs": {
                        "workspace_root": str(tmp_path),
                        "repos": [{"id": "core", "path": ".", "role": "primary"}],
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "ag.json").write_text(
        json.dumps({"version": "1.0", "agents": {"ag": {"executor": "fake"}}}), encoding="utf-8"
    )
    return [
        "--workflow",
        str(tmp_path / "wf.json"),
        "--reposets",
        str(tmp_path / "rs.json"),
        "--agents",
        str(tmp_path / "ag.json"),
    ]


def _normalise(text: str, tmp_path: Path) -> str:
    return _TS_RE.sub('"ts": "<TS>"', _RUN_ID_RE.sub("<ID>", text.replace(str(tmp_path), "<WS>")))


class TestHelp:
    @pytest.mark.parametrize("cmd", ["run", "resume"])
    def test_flag_listed(self, cmd: str) -> None:
        res = runner.invoke(app, [cmd, "--help"])
        assert res.exit_code == 0
        assert "--cache" in res.output
        assert "--no-cache" in res.output

    def test_cache_flag_is_the_last_parameter(self) -> None:
        for name in ("run", "resume"):
            cmd = typer.main.get_command(app).commands[name]  # type: ignore[attr-defined]
            params = [p.name for p in cmd.params]
            assert params[-1] == "cache", (name, params[-3:])

    def test_cache_group_help(self) -> None:
        res = runner.invoke(app, ["cache", "--help"])
        assert res.exit_code == 0
        assert "RESULT cache" in res.output
        assert "prompt caching" in res.output

    def test_group_is_registered_on_the_top_level_help(self) -> None:
        res = runner.invoke(app, ["--help"])
        assert re.search(r"^\W*cache\b", res.output, re.MULTILINE)


class TestRunWarnings:
    def test_unknown_env_prints_exactly_one_warning_and_run_succeeds(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        args = _fixture(tmp_path)
        monkeypatch.setenv("AO_CACHE", "maybe")
        res = runner.invoke(app, ["run", *args])
        assert res.exit_code == 0, res.output
        warnings = [ln for ln in res.stderr.splitlines() if ln.startswith("WARNING:")]
        assert warnings == [
            "WARNING: AO_CACHE='maybe' not recognised (use 1|0|shadow); result cache OFF"
        ]
        assert "succeeded" in res.stdout

    def test_refresh_is_off_with_one_warning(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        args = _fixture(tmp_path)
        monkeypatch.setenv("AO_CACHE", "refresh")
        res = runner.invoke(app, ["run", *args])
        assert res.exit_code == 0, res.output
        assert res.stderr.count("WARNING: AO_CACHE='refresh' not recognised") == 1

    def test_resume_also_resolves_and_warns(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        args = _fixture(tmp_path)
        first = runner.invoke(app, ["run", *args])
        assert first.exit_code == 0, first.output
        run_dirs = list((tmp_path / ".orchestrator" / "runs").iterdir())
        assert len(run_dirs) == 1
        monkeypatch.setenv("AO_CACHE", "maybe")
        res = runner.invoke(app, ["resume", "--run-id", run_dirs[0].name, *args])
        assert res.exit_code == 0, res.output
        assert res.stderr.count("WARNING: AO_CACHE='maybe' not recognised") == 1


class TestNoCacheNoChange:
    """With no flag, env or config the cache layer is invisible and builds nothing."""

    def test_default_run_prints_nothing_cache_related(self, tmp_path: Path) -> None:
        res = runner.invoke(app, ["run", *_fixture(tmp_path)])
        assert res.exit_code == 0, res.output
        assert "result cache" not in (res.stdout + res.stderr).lower()
        assert "WARNING" not in res.stderr
        assert not (tmp_path / ".orchestrator" / "cache").exists()

    @pytest.mark.parametrize(("flag", "env"), [("--no-cache", None), (None, "0")])
    def test_every_off_mode_is_output_identical(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        flag: str | None,
        env: str | None,
    ) -> None:
        """An explicitly-off mode builds no cache (T-o95l1M builds one only for on/shadow, see
        test_cli_result_cache_wiring.py), so the run output is byte-identical to the default
        run apart from run-specific ids."""
        args = _fixture(tmp_path)
        base = runner.invoke(app, ["run", *args])
        base_text = _normalise(base.stdout + "|" + base.stderr, tmp_path)
        (tmp_path / ".orchestrator").rename(tmp_path / "base-orchestrator")
        shutil.rmtree(tmp_path / "out")  # otherwise the second run resumes-skips the task
        if env is not None:
            monkeypatch.setenv("AO_CACHE", env)
        res = runner.invoke(app, ["run", *args, *([flag] if flag else [])])
        assert res.exit_code == 0, res.output
        assert _normalise(res.stdout + "|" + res.stderr, tmp_path) == base_text
        assert not (tmp_path / ".orchestrator" / "cache").exists()


class TestBuildHelper:
    """`_build_result_cache` never reads the workflow unless the mode is on or shadow."""

    @staticmethod
    def _build(flag: bool | None, workspace: Path) -> object:
        wf: Any = object()
        return cli_mod._build_result_cache(flag, str(workspace), wf)

    def test_returns_none_for_every_off_mode(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for flag in (None, False):
            assert self._build(flag, tmp_path) is None
        monkeypatch.setenv("AO_CACHE", "0")
        assert self._build(None, tmp_path) is None
        monkeypatch.setenv("AO_CACHE", "1")
        assert self._build(False, tmp_path) is None  # --no-cache wins over the env

    def test_warnings_are_echoed_to_stderr(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setenv("AO_CACHE", "maybe")
        self._build(None, tmp_path)
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err.count("WARNING: AO_CACHE='maybe'") == 1

    def test_malformed_project_config_exits_1(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        (tmp_path / ".ao").mkdir()
        (tmp_path / ".ao" / "config.yaml").write_text("cache:\n  ttl_days: 0\n", encoding="utf-8")
        with pytest.raises(typer.Exit) as exc:
            self._build(None, tmp_path)
        assert exc.value.exit_code == 1
        assert "ttl_days" in capsys.readouterr().err
