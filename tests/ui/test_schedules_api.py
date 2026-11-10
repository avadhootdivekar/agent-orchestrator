"""``/api/schedules`` routes + ``DashboardService`` schedule methods (T-Ap1Xs3).

Fixed clock throughout. The scheduler state dir is a per-test temp dir (never the user's XDG dir).
"""

from __future__ import annotations

import inspect
import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml

pytest.importorskip("fastapi", reason="dashboard API needs the optional [ui] extra")
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import agent_orchestrator.ui.service as ui_service  # noqa: E402
from agent_orchestrator.service.fire_store import (  # noqa: E402
    ScheduleEventLog,
    ScheduleStateStore,
)
from agent_orchestrator.ui.app import API_PREFIX, create_app  # noqa: E402
from agent_orchestrator.ui.errors import (  # noqa: E402
    ScheduleAlreadyActive,
    ScheduleApiError,
    ScheduleFileInvalid,
    ScheduleNotFound,
)
from agent_orchestrator.ui.service import DashboardService  # noqa: E402
from tests.schedules._fixtures import minimal_limits_data  # noqa: E402

from .conftest import StubSupervisor  # noqa: E402

NOW = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)
URL = f"{API_PREFIX}/schedules"
ORIGIN = {"Origin": "http://testserver"}
REQ_DIR = ("schedules", "requests")


def _binding(sid: str, **extra: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": sid,
        "kind": "cron",
        "schedule": "0 * * * *",
        "workflow": "w.json",
        "limits": minimal_limits_data(),
    }
    row.update(extra)
    return row


@pytest.fixture()
def state_dir(tmp_path: Path) -> Path:
    return tmp_path / "state"


@pytest.fixture()
def write_schedules(workspace: Path) -> Callable[..., Path]:
    def write(*rows: dict[str, Any]) -> Path:
        path = workspace / ".ao" / "schedules.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump({"version": "1.0", "schedules": list(rows)}))
        return path

    return write


@pytest.fixture()
def svc(workspace: Path, stub_supervisor: StubSupervisor, state_dir: Path) -> DashboardService:
    return DashboardService(
        str(workspace),
        supervisor=stub_supervisor,  # type: ignore[arg-type]
        clock=lambda: NOW,
        schedule_state_dir=state_dir,
    )


@pytest.fixture()
def client(svc: DashboardService) -> TestClient:
    return TestClient(create_app(svc))


def _requests(state_dir: Path) -> list[dict[str, Any]]:
    d = state_dir.joinpath(*REQ_DIR)
    return [json.loads(p.read_text()) for p in sorted(d.glob("*.json"))] if d.is_dir() else []


# -- list --------------------------------------------------------------------------------------


def test_list_without_state_dir_is_bindings_with_null_state(
    client: TestClient, write_schedules: Callable[..., Path]
) -> None:
    write_schedules(_binding("a"), _binding("b", enabled=False, until={"max_runs": 3}))
    r = client.get(URL)
    assert r.status_code == 200
    body = r.json()
    assert body["daemon_seen"] is False
    a, b = body["schedules"]
    assert (a["id"], a["kind"], a["enabled"], a["workflow"]) == ("a", "cron", True, "w.json")
    assert a["status"] is None and a["next_fire_at"] is None and a["last_run_id"] is None
    assert a["runs_count"] == 0 and a["max_runs"] is None
    assert b["enabled"] is False and b["max_runs"] == 3


def test_list_merges_state(
    svc: DashboardService,
    client: TestClient,
    state_dir: Path,
    write_schedules: Callable[..., Path],
) -> None:
    write_schedules(_binding("a", template="t", workflow=None))
    store = ScheduleStateStore(state_dir)
    store.record_failure(svc.workspace_root, "a", "boom")
    store.set_next_fire(svc.workspace_root, "a", NOW)
    body = client.get(URL).json()
    assert body["daemon_seen"] is True
    row = body["schedules"][0]
    assert row["template"] == "t" and row["workflow"] is None
    assert row["status"] == "active"
    assert row["next_fire_at"] == NOW.isoformat()
    assert row["consecutive_failures"] == 1


def test_list_no_file_is_empty(client: TestClient) -> None:
    assert client.get(URL).json() == {"daemon_seen": False, "schedules": []}


def test_invalid_file_is_400_with_parse_error(
    client: TestClient, workspace: Path, write_schedules: Callable[..., Path]
) -> None:
    path = write_schedules(_binding("a"))
    path.write_text("schedules: [unclosed")
    r = client.get(URL)
    assert r.status_code == 400
    assert "schedules.yaml" in r.json()["detail"]


# -- history -----------------------------------------------------------------------------------


def test_history_includes_suppressed_with_reason_and_respects_limit(
    svc: DashboardService,
    client: TestClient,
    state_dir: Path,
    write_schedules: Callable[..., Path],
) -> None:
    write_schedules(_binding("a"), _binding("b"))
    log = ScheduleEventLog(state_dir, clock=lambda: NOW)
    log.emit("schedule.fired", workspace=svc.workspace_root, schedule_id="a", run_id="r1")
    log.emit(
        "schedule.governance.skipped",
        workspace=svc.workspace_root,
        schedule_id="a",
        reason="quota_exhausted",
    )
    log.emit("schedule.fired", workspace=svc.workspace_root, schedule_id="b")
    log.emit("schedule.fired", workspace="/elsewhere", schedule_id="a")
    body = client.get(f"{URL}/a/history").json()
    assert body["schedule_id"] == "a"
    assert [e["event"] for e in body["events"]] == ["schedule.governance.skipped", "schedule.fired"]
    assert body["events"][0]["reason"] == "quota_exhausted"
    assert len(client.get(f"{URL}/a/history?limit=1").json()["events"]) == 1


def test_history_unknown_404_and_bad_limit_422(
    client: TestClient, write_schedules: Callable[..., Path]
) -> None:
    write_schedules(_binding("a"))
    assert client.get(f"{URL}/nope/history").status_code == 404
    assert client.get(f"{URL}/a/history?limit=0").status_code == 422
    assert client.get(f"{URL}/a/history?limit=201").status_code == 422


def test_history_clamps_limit_at_service_level(
    svc: DashboardService, write_schedules: Callable[..., Path]
) -> None:
    write_schedules(_binding("a"))
    assert svc.schedule_history("a", limit=10**6)["events"] == []


# -- enable / disable --------------------------------------------------------------------------


def test_disable_then_enable_round_trip(
    svc: DashboardService,
    client: TestClient,
    state_dir: Path,
    write_schedules: Callable[..., Path],
) -> None:
    path = write_schedules(_binding("a"))
    r = client.post(f"{URL}/a/disable", headers=ORIGIN)
    assert r.status_code == 200 and r.json()["enabled"] is False
    assert yaml.safe_load(path.read_text())["schedules"][0]["enabled"] is False
    r = client.post(f"{URL}/a/enable", headers=ORIGIN)
    assert r.json() == {"schedule_id": "a", "enabled": True, "restarted": False}
    events = ScheduleEventLog(state_dir).read(schedule_id="a")
    assert [e["event"] for e in events] == ["schedule.enabled", "schedule.disabled"]
    assert events[0]["requested_by"] == "ui"


def test_enable_completed_schedule_resets_runs_count(
    svc: DashboardService,
    client: TestClient,
    state_dir: Path,
    write_schedules: Callable[..., Path],
) -> None:
    write_schedules(_binding("a", enabled=False))
    store = ScheduleStateStore(state_dir)
    for i in range(7):
        store.record_fire(svc.workspace_root, "a", scheduled_for=NOW, run_id=f"r{i}", fire_at=NOW)
    store.set_status(svc.workspace_root, "a", "completed")
    r = client.post(f"{URL}/a/enable", headers=ORIGIN)
    assert r.status_code == 200 and r.json()["restarted"] is True
    after = ScheduleStateStore(state_dir).get(svc.workspace_root, "a")
    assert after is not None and after.runs_count == 0 and after.status == "active"


def test_enable_completed_while_daemon_holds_lock_is_409(
    svc: DashboardService,
    client: TestClient,
    state_dir: Path,
    write_schedules: Callable[..., Path],
) -> None:
    import fcntl

    write_schedules(_binding("a"))
    ScheduleStateStore(state_dir).set_status(svc.workspace_root, "a", "completed")
    lock = state_dir / "schedules" / "engine.lock"
    with open(lock, "a+") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        r = client.post(f"{URL}/a/enable", headers=ORIGIN)
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    assert r.status_code == 409


@pytest.mark.parametrize("verb", ["enable", "disable", "run-now"])
def test_mutations_unknown_schedule_404(
    client: TestClient, write_schedules: Callable[..., Path], verb: str
) -> None:
    write_schedules(_binding("a"))
    assert client.post(f"{URL}/nope/{verb}", headers=ORIGIN).status_code == 404


@pytest.mark.parametrize("verb", ["enable", "disable", "run-now"])
def test_mutations_with_invalid_file_400(
    client: TestClient, write_schedules: Callable[..., Path], verb: str
) -> None:
    write_schedules(_binding("a")).write_text("- not a mapping")
    assert client.post(f"{URL}/a/{verb}", headers=ORIGIN).status_code == 400


# -- run-now -----------------------------------------------------------------------------------


def test_run_now_writes_request_and_never_launches(
    svc: DashboardService,
    client: TestClient,
    state_dir: Path,
    stub_supervisor: StubSupervisor,
    write_schedules: Callable[..., Path],
) -> None:
    write_schedules(_binding("a"))
    r = client.post(f"{URL}/a/run-now", headers=ORIGIN)
    assert r.status_code == 202
    assert r.json() == {"queued": True, "schedule_id": "a"}
    (req,) = _requests(state_dir)
    assert req == {
        "kind": "run_now",
        "workspace_root": svc.workspace_root,
        "schedule_id": "a",
        "requested_at": NOW.isoformat(),
        "force": False,
        "requested_by": "ui",
    }
    assert stub_supervisor.launch_calls == [] and stub_supervisor.records == []


def test_run_now_active_fire_is_409_unless_forced(
    svc: DashboardService,
    client: TestClient,
    state_dir: Path,
    write_schedules: Callable[..., Path],
) -> None:
    write_schedules(_binding("a"))
    store = ScheduleStateStore(state_dir)
    store.record_fire(
        svc.workspace_root, "a", scheduled_for=NOW, run_id="run-1", fire_at=NOW, fire_key="k1"
    )
    assert client.post(f"{URL}/a/run-now", headers=ORIGIN).status_code == 409
    assert _requests(state_dir) == []
    r = client.post(f"{URL}/a/run-now?force=true", headers=ORIGIN)
    assert r.status_code == 202
    assert _requests(state_dir)[0]["force"] is True


def test_request_body_cannot_inject_workflow_or_args(
    client: TestClient, state_dir: Path, write_schedules: Callable[..., Path]
) -> None:
    write_schedules(_binding("a"))
    r = client.post(
        f"{URL}/a/run-now",
        headers=ORIGIN,
        json={"workflow": "/etc/evil.json", "run_args": {"x": 1}},
    )
    assert r.status_code == 202
    (req,) = _requests(state_dir)
    assert set(req) == {
        "kind",
        "workspace_root",
        "schedule_id",
        "requested_at",
        "force",
        "requested_by",
    }


# -- structure ---------------------------------------------------------------------------------


def test_service_module_never_launches() -> None:
    src = inspect.getsource(ui_service.DashboardService.request_schedule_fire)
    assert "Popen" not in src and "launch_run" not in src and "start_run" not in src


def test_typed_errors_carry_status_codes() -> None:
    assert issubclass(ScheduleNotFound, ScheduleApiError)
    assert (ScheduleNotFound.status_code, ScheduleFileInvalid.status_code) == (404, 400)
    assert ScheduleAlreadyActive.status_code == 409


def test_literal_schedules_route_precedes_parametrised_ones(client: TestClient) -> None:
    app: FastAPI = client.app  # type: ignore[assignment]
    paths = [getattr(r, "path", "") for r in app.routes]
    first_param = next(i for i, p in enumerate(paths) if p.startswith(f"{URL}/{{"))
    assert paths.index(URL) < first_param


def test_route_bodies_are_at_most_five_lines() -> None:
    import ast

    import agent_orchestrator.ui.app as ui_app

    tree = ast.parse(inspect.getsource(ui_app))
    names = {
        "list_schedules",
        "schedule_history",
        "enable_schedule",
        "disable_schedule",
        "run_schedule_now",
    }
    found = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name in names]
    assert {n.name for n in found} == names
    for n in found:
        assert n.end_lineno is not None
        assert n.end_lineno - n.body[0].lineno + 1 <= 5, n.name


def test_mutate_failure_surfaces_as_400(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, write_schedules: Callable[..., Path]
) -> None:
    from agent_orchestrator.schedules.errors import ScheduleFileError
    from agent_orchestrator.schedules.store import ScheduleStore

    write_schedules(_binding("a"))

    def boom(self: ScheduleStore, fn: object) -> None:
        raise ScheduleFileError("changed underneath us")

    monkeypatch.setattr(ScheduleStore, "mutate", boom)
    r = client.post(f"{URL}/a/disable", headers=ORIGIN)
    assert r.status_code == 400 and "changed underneath" in r.json()["detail"]
