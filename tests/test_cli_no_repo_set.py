"""CLI workspace resolution for no-repo-set mode (ADR-0022 U2, HLD §8)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agent_orchestrator.cli import _resolve_workspace_root, app

runner = CliRunner()


def _write(tmp_path: Path, repo_set: str | None) -> tuple[Path, Path]:
    (tmp_path / "instr.md").write_text("do it")
    wf = {
        "version": "1.0",
        "id": "no-repo",
        "tasks": [
            {
                "id": "a",
                "agent": "worker",
                "instruction": "instr.md",
                "outputs": ["out/a.txt"],
            }
        ],
    }
    if repo_set is not None:
        wf["repo_set"] = repo_set
    wf_path = tmp_path / "workflow.json"
    wf_path.write_text(json.dumps(wf))
    ag = tmp_path / "agents.json"
    ag.write_text(json.dumps({"version": "1.0", "agents": {"worker": {"executor": "fake"}}}))
    return wf_path, ag


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # Run from a scratch dir so the repo's own .ao/config.yaml is never discovered.
    (tmp_path / ".git").mkdir()
    monkeypatch.chdir(tmp_path)
    for var in ("AO_WORKSPACE_ROOT", "AO_REPOSETS", "AO_WORKFLOW", "AO_AGENTS"):
        monkeypatch.delenv(var, raising=False)


def test_validate_no_repo_set_without_reposets(tmp_path: Path) -> None:
    wf, ag = _write(tmp_path, None)
    r = runner.invoke(app, ["validate", "--workflow", str(wf), "--agents", str(ag)])
    assert r.exit_code == 0, r.output


@pytest.mark.parametrize("blank", ["", "  "])
def test_validate_blank_repo_set_is_no_repo(tmp_path: Path, blank: str) -> None:
    wf, ag = _write(tmp_path, blank)
    r = runner.invoke(app, ["validate", "--workflow", str(wf), "--agents", str(ag)])
    assert r.exit_code == 0, r.output


def test_validate_repo_set_without_reposets_keeps_old_message(tmp_path: Path) -> None:
    wf, ag = _write(tmp_path, "rs")
    r = runner.invoke(app, ["validate", "--workflow", str(wf), "--agents", str(ag)])
    assert r.exit_code == 1
    assert "ERROR: --reposets or AO_REPOSETS required" in r.output


def test_validate_supplied_reposets_still_loaded(tmp_path: Path) -> None:
    wf, ag = _write(tmp_path, None)
    bad = tmp_path / "reposets.json"
    bad.write_text("{not json")
    r = runner.invoke(
        app, ["validate", "--workflow", str(wf), "--agents", str(ag), "--reposets", str(bad)]
    )
    assert r.exit_code != 0


def test_run_status_resume_in_workspace_flag(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    wf, ag = _write(ws, None)
    args = ["--workflow", str(wf), "--agents", str(ag), "--workspace", str(ws)]
    r = runner.invoke(app, ["run", *args])
    assert r.exit_code == 0, r.output
    runs = list((ws / ".orchestrator" / "runs").iterdir())
    assert len(runs) == 1
    run_id = runs[0].name
    assert json.loads((runs[0] / "state.json").read_text())["repo_set"] is None

    r = runner.invoke(app, ["status", "--run-id", run_id, "--workspace", str(ws)])
    assert r.exit_code == 0, r.output

    r = runner.invoke(app, ["resume", "--run-id", run_id, *args])
    assert r.exit_code == 0, r.output


def test_run_env_workspace_beats_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    wf, ag = _write(tmp_path, None)
    monkeypatch.setenv("AO_WORKSPACE_ROOT", str(ws))
    r = runner.invoke(app, ["run", "--workflow", str(wf), "--agents", str(ag)])
    assert r.exit_code == 0, r.output
    assert (ws / ".orchestrator" / "runs").is_dir()


def test_workspace_flag_beats_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    flag_ws, env_ws = tmp_path / "flag", tmp_path / "env"
    flag_ws.mkdir()
    env_ws.mkdir()
    wf, ag = _write(tmp_path, None)
    monkeypatch.setenv("AO_WORKSPACE_ROOT", str(env_ws))
    r = runner.invoke(
        app, ["run", "--workflow", str(wf), "--agents", str(ag), "--workspace", str(flag_ws)]
    )
    assert r.exit_code == 0, r.output
    assert (flag_ws / ".orchestrator").is_dir()
    assert not (env_ws / ".orchestrator").exists()


def test_project_config_workspace_root_then_ao_parent_then_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proj = tmp_path / "proj"
    (proj / ".ao").mkdir(parents=True)
    (proj / ".git").mkdir()  # stop find_project_config's upward walk here
    monkeypatch.chdir(proj)

    # .ao/ parent when config sets no workspace_root
    (proj / ".ao" / "config.yaml").write_text("{}\n")
    assert _resolve_workspace_root(None, None, None, None, no_repo_default=True) == str(
        proj.resolve()
    )

    # config workspace_root wins over the .ao parent
    other = tmp_path / "other"
    (proj / ".ao" / "config.yaml").write_text(f"workspace_root: {other}\n")
    assert _resolve_workspace_root(None, None, None, None, no_repo_default=True) == str(other)

    # no config anywhere: cwd + stderr NOTE
    bare = tmp_path / "bare"
    (bare / ".git").mkdir(parents=True)
    monkeypatch.chdir(bare)
    r = runner.invoke(app, ["status", "--run-id", "nope"])
    assert r.exit_code == 1
    assert "using current directory as workspace" in r.output


def test_repo_set_workspace_precedence_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent_orchestrator.cli import _resolve_workspace
    from agent_orchestrator.config import load_reposets
    from agent_orchestrator.spec import load_workflow

    wf_path, _ = _write(tmp_path, "rs")
    rs = tmp_path / "reposets.json"
    rs.write_text(
        json.dumps(
            {
                "version": "1.0",
                "repo_sets": {
                    "rs": {
                        "workspace_root": str(tmp_path / "from-reposet"),
                        "repos": [{"id": "core", "path": ".", "role": "primary"}],
                    }
                },
            }
        )
    )
    wf, rmap = load_workflow(str(wf_path)), load_reposets(str(rs))
    assert _resolve_workspace(wf, rmap) == str(tmp_path / "from-reposet")
    monkeypatch.setenv("AO_WORKSPACE_ROOT", "/env/ws")
    assert _resolve_workspace(wf, rmap) == "/env/ws"
    assert _resolve_workspace(wf, rmap, "/flag/ws") == "/flag/ws"


def test_cache_admin_still_refuses_unresolvable_workspace() -> None:
    # `ao cache` shares _resolve_workspace_root but must not adopt the no-repo cwd default.
    r = runner.invoke(app, ["cache", "stats"])
    assert r.exit_code == 1


def test_example_no_repo_workflow_validates() -> None:
    """specs/examples/workflow-no-repo-set.json declares no repo_set and passes `ao validate`."""
    root = Path(__file__).resolve().parents[1] / "specs" / "examples"
    result = CliRunner().invoke(
        app,
        [
            "validate",
            "--workflow",
            str(root / "workflow-no-repo-set.json"),
            "--agents",
            str(root / "agents.json"),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "repo_set" not in json.loads((root / "workflow-no-repo-set.json").read_text())


def test_rate_notes_config_root_fallback_but_status_does_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proj = tmp_path / "proj"
    (proj / ".ao").mkdir(parents=True)
    (proj / ".git").mkdir()
    (proj / ".ao" / "config.yaml").write_text("{}\n")
    monkeypatch.chdir(proj)

    r = runner.invoke(app, ["rate", "nope", "--show"])
    assert "using the project config's workspace" in r.output
    assert str(proj.resolve()) in r.output

    r = runner.invoke(app, ["status", "--run-id", "nope"])
    assert "NOTE:" not in r.output
