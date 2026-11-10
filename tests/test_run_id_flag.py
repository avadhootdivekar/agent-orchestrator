"""T-Ri7Dz2: ``ao run --run-id`` and ``ProcessSupervisor.launch_run(run_id=)``.

Everything runs in tmp workspaces with the fake executor / a stub ``ao`` command; clocks are
fixed so minted ids are deterministic.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agent_orchestrator import run_options
from agent_orchestrator.artifacts import LocalFsArtifactStore
from agent_orchestrator.cli import EXIT_RUN_EXISTS, EXIT_USAGE, app
from agent_orchestrator.errors import OrchestratorError
from agent_orchestrator.models import WorkflowSpec
from agent_orchestrator.runstate import (
    MAX_RUN_ID_CHARS,
    RunAlreadyExistsError,
    RunStateStore,
    validate_run_id,
)
from agent_orchestrator.ui.processes import (
    STATUS_FAILED_TO_START,
    STATUS_STARTED,
    STATUS_STARTING,
    LaunchError,
    LaunchRecord,
    ProcessSupervisor,
    _render_options,
    derive_launch_status,
)
from tests.test_cli import _write_agents_fake, _write_reposets, _write_workflow

runner = CliRunner()
T0 = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


def _setup(tmp_path: Path) -> list[str]:
    instr = tmp_path / "specs" / "examples" / "instructions"
    instr.mkdir(parents=True)
    (instr / "design.md").write_text("design instruction")
    wf, rs, ag = tmp_path / "wf.json", tmp_path / "rs.json", tmp_path / "ag.json"
    _write_workflow(wf)
    _write_reposets(rs, str(tmp_path))
    _write_agents_fake(ag)
    return ["run", "--workflow", str(wf), "--reposets", str(rs), "--agents", str(ag)]


def _invoke(tmp_path: Path, base: list[str], *extra: str):
    return runner.invoke(app, [*base, *extra], env={"AO_WORKSPACE_ROOT": str(tmp_path)})


def _runs(tmp_path: Path) -> list[str]:
    d = tmp_path / ".orchestrator" / "runs"
    return sorted(p.name for p in d.iterdir()) if d.exists() else []


# -- registry ------------------------------------------------------------------------------


def test_run_id_is_registry_option_without_env_config_or_ui() -> None:
    opt = run_options.get_option("run_id")
    assert opt.flag == "--run-id"
    assert (opt.env, opt.config, opt.ui_group, opt.resumable) == (None, None, None, False)
    assert "run_id" not in run_options.allowed_options()
    assert "run_id" not in run_options.options_schema()["json_schema"]["properties"]


def test_run_help_lists_run_id() -> None:
    out = runner.invoke(app, ["run", "--help"], env={"COLUMNS": "400", "TERM": "dumb"}).output
    assert "--run-id" in out


def test_run_id_option_dropped_from_options_dict() -> None:
    assert _render_options({"run_id": "x"}) == []


# -- validation / store --------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad", ["../escape", "a/b", "", "x" * (MAX_RUN_ID_CHARS + 1), ".hid", ".."]
)
def test_validate_rejects(bad: str) -> None:
    with pytest.raises(OrchestratorError):
        validate_run_id(bad)


def test_validate_accepts_boundary() -> None:
    assert validate_run_id("a" * MAX_RUN_ID_CHARS) == "a" * MAX_RUN_ID_CHARS
    assert validate_run_id("my-run_1.2") == "my-run_1.2"


def test_new_run_default_id_unchanged_and_supplied_verbatim(tmp_path: Path) -> None:
    store = RunStateStore(str(tmp_path), LocalFsArtifactStore(str(tmp_path)), clock=lambda: T0)
    wf = WorkflowSpec.model_validate(
        {"version": "1.0", "id": "wf", "tasks": [{"id": "t", "agent": "a", "instruction": "i"}]}
    )
    assert store.new_run(wf).run_id == "wf-20260102T030405Z"
    assert store.new_run(wf, run_id="custom-1").run_id == "custom-1"
    with pytest.raises(OrchestratorError):
        store.new_run(wf, run_id="../x")


def test_reserve_is_atomic_under_concurrency(tmp_path: Path) -> None:
    store = RunStateStore(str(tmp_path), LocalFsArtifactStore(str(tmp_path)))
    results: list[str] = []
    barrier = threading.Barrier(8)

    def worker() -> None:
        barrier.wait()
        try:
            store.reserve_run_dir("X")
            results.append("ok")
        except RunAlreadyExistsError:
            results.append("exists")

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count("ok") == 1 and results.count("exists") == 7


# -- CLI e2e -------------------------------------------------------------------------------


def test_cli_run_id_verbatim_and_duplicate_refused(tmp_path: Path) -> None:
    base = _setup(tmp_path)
    res = _invoke(tmp_path, base, "--run-id", "my-run-1")
    assert res.exit_code == 0, res.output
    assert _runs(tmp_path) == ["my-run-1"]
    state_file = tmp_path / ".orchestrator" / "runs" / "my-run-1" / "state.json"
    state = json.loads(state_file.read_text())
    assert state["run_id"] == "my-run-1"
    # fresh run (not recorded as a resume), and the id is not a resolved setting
    assert state["effective_settings_history"][0]["kind"] == "start"
    assert "run_id" not in state["effective_settings"]
    before = state_file.read_bytes()

    again = _invoke(tmp_path, base, "--run-id", "my-run-1")
    assert again.exit_code == EXIT_RUN_EXISTS
    assert "ao resume --run-id my-run-1" in again.output
    assert state_file.read_bytes() == before


@pytest.mark.parametrize("bad", ["../escape", "a/b", "", "x" * (MAX_RUN_ID_CHARS + 1)])
def test_cli_rejects_bad_ids_without_creating_anything(tmp_path: Path, bad: str) -> None:
    base = _setup(tmp_path)
    res = _invoke(tmp_path, base, "--run-id", bad)
    assert res.exit_code == EXIT_USAGE
    assert "must match" in res.output
    assert _runs(tmp_path) == []
    assert not (tmp_path / "escape").exists()


def test_cli_without_flag_still_mints_timestamp_id(tmp_path: Path) -> None:
    base = _setup(tmp_path)
    assert _invoke(tmp_path, base).exit_code == 0
    (only,) = _runs(tmp_path)
    assert only.startswith("test-wf-")


# -- ProcessSupervisor ---------------------------------------------------------------------

NOOP = [sys.executable, "-c", "pass"]


def test_launch_run_with_run_id_skips_discovery(tmp_path: Path) -> None:
    sup = ProcessSupervisor(str(tmp_path), ao_command=NOOP, run_id_discovery_timeout=30.0)

    def boom(_record: LaunchRecord) -> str | None:
        raise AssertionError("discovery must not run when run_id is supplied")

    sup._discover_run_id = boom  # type: ignore[method-assign]
    rec = sup.launch_run(workflow_path="wf.json", run_id="x")
    assert rec.run_id == "x" and rec.run_id_supplied
    assert rec.argv.count("--run-id") == 1
    assert rec.argv[rec.argv.index("--run-id") + 1] == "x"
    assert sup.load_records()[0].run_id == "x"


def test_launch_run_options_run_id_still_dropped(tmp_path: Path) -> None:
    sup = ProcessSupervisor(str(tmp_path), ao_command=NOOP, run_id_discovery_timeout=0.0)
    rec = sup.launch_run(options={"run_id": "x"})
    assert "--run-id" not in rec.argv
    assert rec.run_id is None


@pytest.mark.parametrize("bad", ["../e", "a/b", "", "x" * 129])
def test_launch_run_rejects_bad_run_id(tmp_path: Path, bad: str) -> None:
    sup = ProcessSupervisor(str(tmp_path), ao_command=NOOP, run_id_discovery_timeout=0.0)
    with pytest.raises(LaunchError):
        sup.launch_run(run_id=bad)
    assert sup.load_records() == []


def _rec(**kw: object) -> LaunchRecord:
    base: dict = {
        "launch_id": "launch-20260102T030405000000Z",
        "kind": "run",
        "pid": 1,
        "argv": ["ao"],
        "started_at": T0.isoformat(),
        "log_path": "x",
        "run_id": "x",
        "run_id_supplied": True,
    }
    base.update(kw)
    return LaunchRecord(**base)


def test_supplied_id_without_run_dir_classifies_failed_to_start() -> None:
    assert derive_launch_status(_rec(), False, T0, 5.0, run_dir_exists=False) == (
        STATUS_FAILED_TO_START
    )
    assert derive_launch_status(_rec(), True, T0, 5.0, run_dir_exists=False) == STATUS_STARTING
    assert derive_launch_status(_rec(), False, T0, 5.0, run_dir_exists=True) == STATUS_STARTED
    # a discovered (not supplied) id is never second-guessed
    discovered = _rec(run_id_supplied=False)
    assert derive_launch_status(discovered, False, T0, 5.0, run_dir_exists=False) == STATUS_STARTED


def test_describe_supplied_id_whose_dir_never_appears_is_failed_to_start(tmp_path: Path) -> None:
    sup = ProcessSupervisor(str(tmp_path), ao_command=NOOP, run_id_discovery_timeout=0.0)
    rec = sup.launch_run(run_id="never-made")
    sup._children[rec.pid].wait(timeout=10)
    assert sup.describe(rec)["status"] == STATUS_FAILED_TO_START
    run_dir = tmp_path / ".orchestrator" / "runs" / "never-made"
    os.makedirs(run_dir)
    assert sup.describe(rec)["status"] == STATUS_STARTED
