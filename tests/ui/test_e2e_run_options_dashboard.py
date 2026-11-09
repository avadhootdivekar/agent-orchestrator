"""E2E for the dashboard run-options epic (E-aYG5vn, T-mQs7CQ).

Two boundaries, both against a **real** ``ProcessSupervisor`` spawning a **real** ``ao run`` /
``ao resume`` child (fake executor, temp workspace — never a live workspace):

* **API level** — ``POST /api/runs``, ``POST /api/templates/{name}/instances`` (``start``) and
  ``POST /api/runs/{id}/resume`` with thinking effort, turn budget, max turns and autocompact;
  verify the child argv, ``state.effective_settings`` (+ ``history``), per-task effective limits,
  and what ``GET /api/runs/{id}`` (run page "Limits & settings" + task panel) returns.
* **CliRunner level** — the same flags straight through ``ao run`` / ``ao resume``, plus the
  NFR-4 guarantee that every dashboard-visible registry option is a CLI flag too.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
import typer.main
from typer.testing import CliRunner

pytest.importorskip("fastapi", reason="dashboard needs the optional [ui] extra")
from fastapi.testclient import TestClient  # noqa: E402

import agent_orchestrator.templates as templates_mod  # noqa: E402
from agent_orchestrator.cli import app as cli_app  # noqa: E402
from agent_orchestrator.models import RunState  # noqa: E402
from agent_orchestrator.project_config import ProjectConfig  # noqa: E402
from agent_orchestrator.run_options import OPTIONS  # noqa: E402
from agent_orchestrator.ui.app import create_app  # noqa: E402
from agent_orchestrator.ui.processes import ProcessSupervisor  # noqa: E402
from agent_orchestrator.ui.service import DashboardService  # noqa: E402

from .conftest import write_template  # noqa: E402
from .test_e2e_ui import _write_workspace  # noqa: E402

runner = CliRunner()

RUN_TIMEOUT = 90.0
OPTS = {
    "thinking_effort": "high",
    "turn_budget": "xhigh",
    "max_turns": 77,
    "autocompact": "auto",
}


def _wait(pred: Callable[[], bool], timeout: float = RUN_TIMEOUT) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.2)
    return False


def _state_of(ws: Path, run_id: str) -> RunState:
    path = ws / ".orchestrator" / "runs" / run_id / "state.json"
    return RunState.model_validate_json(path.read_text())


@pytest.fixture()
def ws(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for name in ("AO_EFFORT", "AO_TURN_BUDGET", "AO_AUTOCOMPACT", "AO_THINKING_EFFORT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(templates_mod, "_BUILTIN_ROOT", tmp_path / "no-builtin")
    root = tmp_path / "ws"
    root.mkdir()
    _write_workspace(root)
    # extras for the template path: a `worker` fake agent, the template's repo set + instruction
    agents = json.loads((root / "agents.json").read_text())
    agents["agents"]["worker"] = {"executor": "fake"}
    (root / "agents.json").write_text(json.dumps(agents))
    reposets = json.loads((root / "reposets.json").read_text())
    reposets["repo_sets"]["default-set"] = reposets["repo_sets"]["main"]
    (root / "reposets.json").write_text(json.dumps(reposets))
    (root / "instructions" / "do-work.md").write_text("do the work", encoding="utf-8")
    (root / "prompts").mkdir(exist_ok=True)
    (root / "prompts" / "run.md").write_text("go", encoding="utf-8")
    write_template(root / "cfg-templates", dirname="mini-template", name="mini")
    return root


@pytest.fixture()
def client(ws: Path) -> Iterator[TestClient]:
    cfg = ProjectConfig(
        templates=[str(ws / "cfg-templates" / "mini-template")],
        reposets=str(ws / "reposets.json"),
        agents=str(ws / "agents.json"),
    )
    service = DashboardService(str(ws), supervisor=ProcessSupervisor(str(ws)), project_config=cfg)
    with TestClient(create_app(service)) as c:
        yield c


def _finished(client: TestClient, run_id: str) -> bool:
    detail = client.get(f"/api/runs/{run_id}")
    return (
        detail.status_code == 200
        and detail.json()["summary"]["status"] in ("succeeded", "failed")
        and not detail.json()["is_live"]
    )


def _argv_of(client: TestClient, launch_id: str) -> list[str]:
    return client.get(f"/api/launches/{launch_id}").json()["argv"]


def _assert_flags(argv: list[str]) -> None:
    for flag, value in (
        ("--thinking-effort", "high"),
        ("--turn-budget", "xhigh"),
        ("--max-turns", "77"),
        ("--autocompact", "auto"),
    ):
        assert argv[argv.index(flag) + 1] == value, argv


def _assert_run_page(client: TestClient, ws: Path, run_id: str) -> dict:
    """State + API assertions shared by New Run and Template Launch."""
    st = _state_of(ws, run_id)
    eff = st.effective_settings
    for key, val in (
        ("thinking_effort", "high"),
        ("turn_budget", "xhigh"),
        ("autocompact", "auto"),
    ):
        assert (eff[key].value, eff[key].source) == (val, "cli"), key
    assert (eff["max_turns"].value, eff["max_turns"].source) == (77, "cli")
    assert [h.kind for h in st.effective_settings_history] == ["start"]

    (task,) = st.tasks.values()
    lim = task.effective_limits
    assert lim is not None
    assert (lim.turn_budget, lim.max_turns, lim.autocompact) == ("xhigh", 77, "auto")

    detail = client.get(f"/api/runs/{run_id}").json()
    assert detail["settings_recorded"] is True
    rows = {row["key"]: row for row in detail["limits"]}
    assert rows, "run page 'Limits & settings' has rows"
    # task panel: per-task effective limits ride on the task rows
    tasks = detail["tasks"]
    assert len(tasks) == 1 and tasks[0]["limits"]["max_turns"] == 77
    assert tasks[0]["limits"]["turn_budget"] == "xhigh"
    return detail


# -- API level ----------------------------------------------------------------------------------


class TestNewRunLaunch:
    def test_new_run_options_reach_argv_state_and_run_page(
        self, client: TestClient, ws: Path
    ) -> None:
        resp = client.post(
            "/api/runs",
            json={
                "workflow_path": str(ws / "workflow.json"),
                "prompt": "Build it.",
                "options": OPTS,
            },
        )
        assert resp.status_code == 201, resp.text
        launch = resp.json()
        _assert_flags(_argv_of(client, launch["launch_id"]))
        run_id = launch["run_id"]
        assert run_id and _wait(lambda: _finished(client, run_id))
        assert client.get(f"/api/runs/{run_id}").json()["summary"]["status"] == "succeeded"
        _assert_run_page(client, ws, run_id)

    def test_non_allowlisted_option_is_dropped_not_forwarded(
        self, client: TestClient, ws: Path
    ) -> None:
        resp = client.post(
            "/api/runs",
            json={
                "workflow_path": str(ws / "workflow.json"),
                "prompt": "x",
                "options": {"max_turns": 5, "--evil": "1", "rm": "-rf"},
            },
        )
        assert resp.status_code == 201, resp.text
        argv = _argv_of(client, resp.json()["launch_id"])
        assert "--max-turns" in argv and "--evil" not in argv and "-rf" not in argv
        _wait(lambda: _finished(client, resp.json()["run_id"]))

    def test_invalid_option_value_makes_the_child_fail_not_the_server(
        self, client: TestClient, ws: Path
    ) -> None:
        resp = client.post(
            "/api/runs",
            json={
                "workflow_path": str(ws / "workflow.json"),
                "prompt": "x",
                "options": {"turn_budget": "bogus"},
            },
        )
        # accepted by the API (validation is the CLI's job) but the run never starts
        assert resp.status_code in (201, 400)
        if resp.status_code == 201:
            assert _wait(
                lambda: (
                    client.get(f"/api/launches/{resp.json()['launch_id']}").json()["status"]
                    == "failed_to_start"
                )
            )


class TestTemplateLaunch:
    def test_template_launch_forwards_options_and_records_them(
        self, client: TestClient, ws: Path
    ) -> None:
        resp = client.post(
            "/api/templates/mini/instances",
            json={"slug_or_id": "optsdemo", "start": True, "options": OPTS},
        )
        assert resp.status_code == 201, resp.text
        launch = resp.json()["launch"]
        _assert_flags(_argv_of(client, launch["launch_id"]))
        run_id = launch["run_id"]
        assert run_id and _wait(lambda: _finished(client, run_id))
        assert client.get(f"/api/runs/{run_id}").json()["summary"]["status"] == "succeeded"
        _assert_run_page(client, ws, run_id)


class TestResumeShowsHistory:
    def test_resume_with_changed_options_appends_history(
        self, client: TestClient, ws: Path
    ) -> None:
        first = client.post(
            "/api/runs",
            json={"workflow_path": str(ws / "workflow.json"), "prompt": "x", "options": OPTS},
        ).json()
        run_id = first["run_id"]
        assert _wait(lambda: _finished(client, run_id))

        resp = client.post(
            f"/api/runs/{run_id}/resume",
            json={"options": {"turn_budget": "low", "max_turns": 9, "thinking_effort": "max"}},
        )
        assert resp.status_code == 200, resp.text
        argv = _argv_of(client, resp.json()["launch_id"])
        assert argv[argv.index("--turn-budget") + 1] == "low"
        assert argv[argv.index("--max-turns") + 1] == "9"

        assert _wait(lambda: len(_state_of(ws, run_id).effective_settings_history) == 2)
        assert _wait(lambda: _finished(client, run_id))
        st = _state_of(ws, run_id)
        hist = st.effective_settings_history
        assert [h.kind for h in hist] == ["start", "resume"]
        assert hist[0].settings["turn_budget"].value == "xhigh"
        assert hist[0].settings["max_turns"].value == 77
        assert hist[1].settings["turn_budget"].value == "low"
        assert hist[1].settings["max_turns"].value == 9
        assert hist[1].settings["thinking_effort"].value == "max"
        # latest snapshot is what the run page shows as "current"
        assert st.effective_settings["turn_budget"].value == "low"
        detail = client.get(f"/api/runs/{run_id}").json()
        assert detail["settings_recorded"] is True


# -- CliRunner level ----------------------------------------------------------------------------


def _cli_base(ws: Path) -> list[str]:
    return [
        "--workflow",
        str(ws / "workflow.json"),
        "--reposets",
        str(ws / "reposets.json"),
        "--agents",
        str(ws / "agents.json"),
    ]


def _cli_flags() -> list[str]:
    return [
        "--thinking-effort",
        "high",
        "--turn-budget",
        "xhigh",
        "--max-turns",
        "77",
        "--autocompact",
        "auto",
    ]


class TestCliLevel:
    def test_run_and_resume_record_settings_and_history(
        self, ws: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(ws)
        env = {"AO_WORKSPACE_ROOT": str(ws)}
        r = runner.invoke(cli_app, ["run", *_cli_base(ws), *_cli_flags()], env=env)
        assert r.exit_code == 0, r.output
        (run_dir,) = list((ws / ".orchestrator" / "runs").iterdir())
        run_id = run_dir.name
        st = _state_of(ws, run_id)
        assert st.effective_settings["thinking_effort"].value == "high"
        (task,) = st.tasks.values()
        assert task.effective_limits is not None
        assert (task.effective_limits.turn_budget, task.effective_limits.max_turns) == ("xhigh", 77)

        r = runner.invoke(
            cli_app,
            ["resume", "--run-id", run_id, *_cli_base(ws), "--turn-budget", "low"],
            env=env,
        )
        assert r.exit_code == 0, r.output
        st = _state_of(ws, run_id)
        assert [h.kind for h in st.effective_settings_history] == ["start", "resume"]
        assert st.effective_settings["turn_budget"].value == "low"
        assert st.effective_settings_history[0].settings["turn_budget"].value == "xhigh"

    def test_legacy_effort_flag_warns_and_conflict_errors(
        self, ws: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(ws)
        env = {"AO_WORKSPACE_ROOT": str(ws)}
        r = runner.invoke(cli_app, ["run", *_cli_base(ws), "--effort", "low"], env=env)
        assert r.exit_code == 0 and "DEPRECATED" in r.output
        r = runner.invoke(
            cli_app, ["run", *_cli_base(ws), "--effort", "low", "--turn-budget", "high"], env=env
        )
        assert r.exit_code == 1 and "conflicting" in r.output

    def test_migrate_effort_keys_dry_run_then_apply(self, tmp_path: Path) -> None:
        agents = tmp_path / "specs" / "agents.json"
        agents.parent.mkdir()
        agents.write_text('{"agents": {"dev": {"executor": "fake", "effort": "high"}}}\n')
        before = agents.read_text()
        r = runner.invoke(cli_app, ["migrate", "effort-keys", "--workspace", str(tmp_path)])
        assert r.exit_code == 0, r.output
        assert agents.read_text() == before  # dry-run leaves files alone
        r = runner.invoke(
            cli_app, ["migrate", "effort-keys", "--workspace", str(tmp_path), "--apply"]
        )
        assert r.exit_code == 0, r.output
        migrated = json.loads(agents.read_text())["agents"]["dev"]
        assert migrated.get("turn_budget") == "high" and "effort" not in migrated


class TestEveryDashboardOptionIsACliFlag:
    """NFR-4 at the outermost boundary: the schema the forms render == flags `ao run` accepts."""

    def test_schema_options_are_cli_flags(self, client: TestClient) -> None:
        schema = client.get("/api/schema/run-options").json()
        schema_names = json.dumps(schema)
        run_cmd = typer.main.get_command(cli_app).commands["run"]  # type: ignore[attr-defined]
        run_flags = {flag for param in run_cmd.params for flag in getattr(param, "opts", [])}
        ui_options = [o for o in OPTIONS if o.ui]
        assert ui_options
        for opt in ui_options:
            assert opt.flag in run_flags, f"{opt.name}: no `ao run` flag {opt.flag}"
            assert f'"{opt.name}"' in schema_names, f"{opt.name} missing from the form schema"
