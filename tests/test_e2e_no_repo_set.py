"""CLI-boundary end-to-end tests for no-repo-set mode (ADR-0022, HLD §8 U6 / A2.4).

Everything runs through `CliRunner` against a temp workspace with the fake executor, a workflow
with no `repo_set` and *no reposets file anywhere*: `ao new` -> `validate` -> `run` -> (failure)
-> `resume` -> `status`, then the dashboard's run-path read of the same workspace, and path-safety
guards (A2.3). Engine/prompt/cache internals are covered in test_no_repo_set_engine.py and the
CLI resolution table in test_cli_no_repo_set.py; this file does not re-assert those.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agent_orchestrator.cli import app
from agent_orchestrator.ui.service import DashboardService

runner = CliRunner()

BUILTIN_AGENTS = [
    "architect",
    "git-operator",
    "developer",
    "tester",
    "reviewer",
    "market-surveyor",
    "architect-opus",
    "reviewer-opus",
    "full-tester",
    "manager",
]


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("AO_WORKSPACE_ROOT", "AO_REPOSETS", "AO_WORKFLOW", "AO_AGENTS"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def ws(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Workspace nested in a sandbox dir so 'nothing escaped' can be asserted on the parent."""
    sandbox = tmp_path / "sandbox"
    root = sandbox / "ws"
    root.mkdir(parents=True)
    (sandbox / ".git").mkdir()  # stop find_project_config's upward walk at the sandbox
    (root / "instr.md").write_text("do the work")
    monkeypatch.chdir(root)
    return root


def _agents(ws: Path, names: list[str] | None = None) -> Path:
    path = ws / "agents.json"
    path.write_text(
        json.dumps(
            {
                "version": "1.0",
                "agents": {n: {"executor": "fake"} for n in names or ["worker"]},
            }
        )
    )
    return path


def _workflow(ws: Path, tasks: list[dict]) -> Path:
    path = ws / "workflow.json"
    path.write_text(json.dumps({"version": "1.0", "id": "e2e-norepo", "tasks": tasks}))
    return path


def _task(tid: str, **kw: object) -> dict:
    return {"id": tid, "agent": "worker", "instruction": "instr.md", **kw}


def _args(ws: Path, wf: Path, ag: Path) -> list[str]:
    return ["--workflow", str(wf), "--agents", str(ag), "--workspace", str(ws)]


def _only_run(ws: Path) -> Path:
    runs = list((ws / ".orchestrator" / "runs").iterdir())
    assert len(runs) == 1
    return runs[0]


def _tree(root: Path) -> set[str]:
    return {str(p.relative_to(root)) for p in root.rglob("*")}


def test_no_reposets_file_exists_in_fixture(ws: Path) -> None:
    # Guard the premise of every test below.
    assert not list(ws.glob("reposets*"))
    assert not list(ws.parent.glob("**/reposets*"))


class TestLifecycle:
    def test_validate_run_status_chain_without_repo_set(self, ws: Path) -> None:
        wf = _workflow(
            ws,
            [
                _task("a", outputs=["out/a.txt"]),
                _task("b", depends_on=["a"], inputs=["out/a.txt"], outputs=["out/b.txt"]),
                _task("c", depends_on=["b"], inputs=["out/b.txt"], outputs=["out/c.txt"]),
            ],
        )
        ag = _agents(ws)
        args = _args(ws, wf, ag)

        r = runner.invoke(app, ["validate", "--workflow", str(wf), "--agents", str(ag)])
        assert r.exit_code == 0, r.output

        r = runner.invoke(app, ["run", *args])
        assert r.exit_code == 0, r.output

        run_dir = _only_run(ws)
        state = json.loads((run_dir / "state.json").read_text())
        assert state["status"] == "succeeded"
        assert state["repo_set"] is None
        assert {t: v["status"] for t, v in state["tasks"].items()} == {
            "a": "succeeded",
            "b": "succeeded",
            "c": "succeeded",
        }
        for name in ("a", "b", "c"):
            assert (ws / "out" / f"{name}.txt").is_file()

        r = runner.invoke(app, ["status", "--run-id", run_dir.name, "--workspace", str(ws)])
        assert r.exit_code == 0, r.output
        assert run_dir.name in r.output
        assert "succeeded" in r.output

    def test_run_writes_nothing_outside_workspace(self, ws: Path) -> None:
        wf = _workflow(ws, [_task("a", outputs=["out/a.txt"])])
        ag = _agents(ws)
        before = _tree(ws.parent)
        r = runner.invoke(app, ["run", *_args(ws, wf, ag)])
        assert r.exit_code == 0, r.output
        added = _tree(ws.parent) - before
        assert added  # something was produced...
        assert all(p.startswith("ws/") for p in added), added  # ...only under the workspace

    def test_failed_run_then_resume_completes(self, ws: Path) -> None:
        # `b` needs a workspace input that does not exist yet -> the run fails; the user
        # supplies it and `ao resume` finishes the run without redoing `a`.
        wf = _workflow(
            ws,
            [
                _task("a", outputs=["out/a.txt"]),
                _task(
                    "b",
                    depends_on=["a"],
                    inputs=["data/in.txt"],
                    outputs=["out/b.txt"],
                ),
            ],
        )
        ag = _agents(ws)
        args = _args(ws, wf, ag)

        r = runner.invoke(app, ["run", *args])
        assert r.exit_code != 0, r.output
        run_dir = _only_run(ws)
        failed = json.loads((run_dir / "state.json").read_text())
        assert failed["status"] != "succeeded"
        assert failed["repo_set"] is None
        assert failed["tasks"]["a"]["status"] == "succeeded"
        assert failed["tasks"]["b"]["status"] != "succeeded"

        r = runner.invoke(app, ["status", "--run-id", run_dir.name, "--workspace", str(ws)])
        assert r.exit_code == 0, r.output

        (ws / "data").mkdir()
        (ws / "data" / "in.txt").write_text("input")
        r = runner.invoke(app, ["resume", "--run-id", run_dir.name, *args])
        assert r.exit_code == 0, r.output

        done = json.loads((run_dir / "state.json").read_text())
        assert done["status"] == "succeeded"
        assert done["tasks"]["b"]["status"] == "succeeded"
        assert (ws / "out" / "b.txt").is_file()
        assert len(list((ws / ".orchestrator" / "runs").iterdir())) == 1  # resumed in place


class TestScaffoldFromBuiltinTemplate:
    def test_new_without_repo_set_param_validates_and_notes(self, ws: Path) -> None:
        ag = _agents(ws, BUILTIN_AGENTS)
        r = runner.invoke(
            app,
            [
                "new",
                "routed-runner",
                "norepo",
                "--workspace",
                str(ws),
                "--agents",
                str(ag),
                "--validate-only",
            ],
        )
        assert r.exit_code == 0, r.output
        assert "repo_set" in r.output  # the NOTE about no-repo mode
        rendered = list(ws.glob("workflows/routed-runner/runs/*/workflow.json"))
        assert len(rendered) == 1
        spec = json.loads(rendered[0].read_text())
        assert not spec.get("repo_set")  # "" or absent -> normalised to None by the model
        assert not list(ws.glob("**/reposets*"))

    def test_new_template_blank_repo_set_runs_in_workspace(self, ws: Path) -> None:
        # Scaffold then execute the rendered workflow with `ao validate`/`ao run --workspace`.
        ag = _agents(ws, BUILTIN_AGENTS)
        r = runner.invoke(
            app,
            ["new", "routed-runner", "norepo", "--workspace", str(ws), "--agents", str(ag)],
        )
        assert r.exit_code == 0, r.output
        wf = next(ws.glob("workflows/routed-runner/runs/*/workflow.json"))
        r = runner.invoke(app, ["validate", "--workflow", str(wf), "--agents", str(ag)])
        assert r.exit_code == 0, r.output


class TestDashboardRunRead:
    def test_dashboard_lists_and_details_no_repo_run(self, ws: Path) -> None:
        wf = _workflow(ws, [_task("a", outputs=["out/a.txt"])])
        ag = _agents(ws)
        r = runner.invoke(app, ["run", *_args(ws, wf, ag)])
        assert r.exit_code == 0, r.output
        run_id = _only_run(ws).name

        class _NoProcs:
            def reconcile(self) -> None: ...
            def is_running(self, _run_id: str) -> bool:
                return False

            def record_for_run(self, _run_id: str) -> None:
                return None

            def describe(self, *_a: object, **_k: object) -> None:
                return None

        svc = DashboardService(str(ws), supervisor=_NoProcs())  # type: ignore[arg-type]
        rows = svc.list_runs()
        assert [row["run_id"] for row in rows] == [run_id]
        assert rows[0]["status"] == "succeeded"
        assert rows[0].get("repo_set") in (None, "")
        detail = svc.run_detail(run_id)
        assert detail["summary"]["status"] == "succeeded"
        assert svc.run_graph(run_id) is not None


class TestPathSafety:
    @pytest.mark.parametrize(
        "field,value",
        [
            ("outputs", "../escape.txt"),
            ("inputs", "../escape.txt"),
            ("outputs", "/tmp/ao-escape-abs.txt"),
        ],
    )
    def test_escape_does_not_succeed_or_write_outside(
        self, ws: Path, field: str, value: str
    ) -> None:
        wf = _workflow(ws, [_task("a", **{field: [value]})])
        ag = _agents(ws)
        before = _tree(ws.parent)
        r = runner.invoke(app, ["run", *_args(ws, wf, ag)])
        assert r.exit_code != 0, r.output
        if field == "outputs":
            assert "Path escapes workspace root" in r.output
        assert not (ws.parent / "escape.txt").exists()
        assert not Path("/tmp/ao-escape-abs.txt").exists()
        added = _tree(ws.parent) - before
        assert all(p.startswith("ws/") for p in added), added

    def test_symlink_escape_is_rejected(self, ws: Path, tmp_path: Path) -> None:
        outside = tmp_path / "outside"
        outside.mkdir()
        (ws / "link").symlink_to(outside, target_is_directory=True)
        wf = _workflow(ws, [_task("a", outputs=["link/leak.txt"])])
        ag = _agents(ws)
        r = runner.invoke(app, ["run", *_args(ws, wf, ag)])
        assert r.exit_code != 0, r.output
        assert "Path escapes workspace root" in r.output
        assert not (outside / "leak.txt").exists()
