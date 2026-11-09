"""T-Sn5Ui1: ``GET /runs/{id}/sentinel`` + ``POST /runs/{id}/control`` (+ outcome probe)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="dashboard API needs the optional [ui] extra")
from fastapi.testclient import TestClient  # noqa: E402

from agent_orchestrator import sentinel_tick  # noqa: E402
from agent_orchestrator.models import (  # noqa: E402
    EffectiveSetting,
    SentinelState,
    TaskRunState,
)
from agent_orchestrator.project_config import ProjectConfig  # noqa: E402
from agent_orchestrator.ui.app import API_PREFIX, create_app  # noqa: E402
from agent_orchestrator.ui.service import (  # noqa: E402
    DashboardConflictError,
    DashboardNotFoundError,
    DashboardService,
    DashboardValidationError,
)

from .conftest import StubSupervisor, make_run_state, write_run  # noqa: E402

RUN = "demo-20260724T100000Z"
OLD = "old-20260101T000000Z"


def _inbox(workspace: Path, run: str = RUN) -> list[dict]:
    d = workspace / ".orchestrator" / "runs" / run / sentinel_tick.CONTROL_DIR / "inbox"
    return [json.loads(p.read_text()) for p in sorted(d.glob("*.json"))] if d.is_dir() else []


@pytest.fixture()
def svc(workspace: Path, stub_supervisor: StubSupervisor) -> DashboardService:
    state = make_run_state(
        RUN,
        status="running",
        tasks={"build": TaskRunState(status="running", started_at="2026-07-24T10:00:00+00:00")},
    )
    state.sentinel = SentinelState(llm_paused=True, llm_pause_reason="budget", generation=3)
    state.effective_settings["sentinel.max_parallel_ceiling"] = EffectiveSetting(
        value=4, source="cli"
    )
    write_run(workspace, state)
    write_run(workspace, make_run_state(OLD, status="succeeded"))  # predates the sentinel
    stub_supervisor.live_runs.add(RUN)
    return DashboardService(
        str(workspace),
        supervisor=stub_supervisor,  # type: ignore[arg-type]
        project_config=ProjectConfig(),
    )


@pytest.fixture()
def client(svc: DashboardService) -> Iterator[TestClient]:
    with TestClient(create_app(svc)) as c:
        yield c


def _sentinel(run: str = RUN) -> str:
    return f"{API_PREFIX}/runs/{run}/sentinel"


def _control(run: str = RUN) -> str:
    return f"{API_PREFIX}/runs/{run}/control"


class TestStatus:
    def test_new_run_surfaces_generation_pause_reason_and_ceiling(self, client: TestClient) -> None:
        doc = client.get(_sentinel()).json()
        assert doc["schema"] == "ao.ctl.status/v1"
        assert doc["llm"]["generation"] == 3
        assert doc["llm"]["paused"] is True and doc["llm"]["pause_reason"] == "budget"
        assert doc["concurrency"]["ceiling"] == 4
        assert [t["id"] for t in doc["tasks"]["running"]] == ["build"]

    def test_old_run_without_sentinel_state_reports_disabled(self, client: TestClient) -> None:
        r = client.get(_sentinel(OLD))
        assert r.status_code == 200
        assert r.json()["sentinel"]["enabled"] is False
        assert r.json()["llm"]["generation"] is None

    def test_unknown_run_is_404(self, client: TestClient) -> None:
        assert client.get(_sentinel("nope-1")).status_code == 404
        assert client.get(_sentinel("..%2F..%2Fetc")).status_code in (404, 400)


class TestControl:
    def test_valid_post_writes_one_inbox_entry_with_dashboard_actor(
        self, client: TestClient, workspace: Path
    ) -> None:
        r = client.post(
            _control(), json={"kind": "set", "payload": {"values": {"max_parallel": 3}}}
        )
        assert r.status_code == 202, r.text
        body = r.json()
        assert body["outcome"] == "queued" and body["kind"] == "set"
        (entry,) = _inbox(workspace)
        assert entry["id"] == body["id"] and entry["schema"] == sentinel_tick.CTL_SCHEMA
        assert entry["actor"] == "dashboard:local"  # auth off => no principal
        assert entry["payload"] == {"values": {"max_parallel": 3}}

    def test_kill_pause_resume_note_entries(self, client: TestClient, workspace: Path) -> None:
        posts = [
            {
                "kind": "kill",
                "payload": {"task_id": "build", "no_retry": True},
                "rationale": "stuck",
            },
            {"kind": "pause_llm"},
            {"kind": "resume_llm"},
            {"kind": "note", "payload": {"text": "  look at X "}},
        ]
        for p in posts:
            assert client.post(_control(), json=p).status_code == 202
        by_kind = {e["kind"]: e for e in _inbox(workspace)}
        assert by_kind["kill"]["payload"] == {"task_id": "build", "no_retry": True}
        assert by_kind["kill"]["rationale"] == "stuck"
        assert by_kind["note"]["payload"] == {"text": "look at X"}
        assert by_kind["pause_llm"]["payload"] == {} and "resume_llm" in by_kind

    @pytest.mark.parametrize(
        "body",
        [
            {"kind": "set", "payload": {"values": {"max_parallel": 9}}},  # above ceiling 4
            {"kind": "set", "payload": {"values": {"max_parallel": 0}}},
            {"kind": "set", "payload": {"values": {"tick_seconds": 3}}},
            {"kind": "kill", "payload": {"task_id": "build"}},  # reason required
            {"kind": "kill", "payload": {"task_id": "ghost"}, "rationale": "r"},
            {"kind": "note", "payload": {"text": "  "}},
            {"kind": "inject", "payload": {}},
            {"kind": "nope"},
        ],
    )
    def test_invalid_requests_are_400_and_write_nothing(
        self, client: TestClient, workspace: Path, body: dict
    ) -> None:
        r = client.post(_control(), json=body)
        assert r.status_code == 400, r.text
        assert _inbox(workspace) == []

    def test_unknown_fields_and_bad_body_are_422(self, client: TestClient) -> None:
        assert (
            client.post(_control(), json={"kind": "pause_llm", "actor": "root"}).status_code == 422
        )
        assert client.post(_control(), json={}).status_code == 422

    def test_unknown_run_404_and_finished_run_409(
        self, client: TestClient, workspace: Path
    ) -> None:
        assert client.post(_control("ghost-1"), json={"kind": "pause_llm"}).status_code == 404
        r = client.post(_control(OLD), json={"kind": "pause_llm"})
        assert r.status_code == 409
        assert _inbox(workspace, OLD) == []

    def test_outcome_probe_pending_consumed_rejected(
        self, client: TestClient, workspace: Path
    ) -> None:
        eid = client.post(_control(), json={"kind": "pause_llm"}).json()["id"]
        url = f"{_control()}/{eid}"
        assert client.get(url).json() == {"outcome": "pending"}
        ctl = workspace / ".orchestrator" / "runs" / RUN / sentinel_tick.CONTROL_DIR
        (ctl / sentinel_tick.REJECTED_DIR).mkdir(parents=True)
        (ctl / sentinel_tick.REJECTED_DIR / f"{eid}.reason.json").write_text(
            json.dumps({"rule_id": "CTL-CEILING", "detail": "d"})
        )
        assert client.get(url).json()["rule_id"] == "CTL-CEILING"
        assert client.get(f"{_control()}/..%2Fx").status_code in (400, 404)
        assert client.get(f"{_control()}/BAD").status_code == 400


class TestServiceLayer:
    def test_service_errors_map_to_types(self, svc: DashboardService) -> None:
        with pytest.raises(DashboardNotFoundError):
            svc.sentinel_status("ghost-1")
        with pytest.raises(DashboardConflictError):
            svc.submit_control(OLD, "pause_llm", {}, None, actor="dashboard:x")
        with pytest.raises(DashboardValidationError):
            svc.submit_control(RUN, "kill", {"task_id": "build"}, None, actor="dashboard:x")


class TestSecurity:
    """Mutating route: same-origin only (SecurityMiddleware) and deny-by-default auth."""

    def test_cross_origin_post_is_rejected_and_writes_nothing(
        self, client: TestClient, workspace: Path
    ) -> None:
        r = client.post(
            _control(), json={"kind": "pause_llm"}, headers={"origin": "http://evil.example"}
        )
        assert r.status_code == 403
        assert _inbox(workspace) == []

    def test_form_content_type_is_rejected(self, client: TestClient, workspace: Path) -> None:
        r = client.post(
            _control(),
            content="kind=pause_llm",
            headers={"content-type": "application/x-www-form-urlencoded"},
        )
        assert r.status_code in (403, 415, 422)
        assert _inbox(workspace) == []


def test_actor_is_the_authenticated_principal(
    svc: DashboardService, workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The route reads the middleware-attested principal (patched here; auth-on wiring itself is
    covered by tests/auth) -- the request body can never name the actor."""
    from types import SimpleNamespace

    import agent_orchestrator.ui.app as ui_app

    monkeypatch.setattr(ui_app, "current_principal", lambda _r: SimpleNamespace(username="alice"))
    with TestClient(create_app(svc)) as c:
        r = c.post(_control(), json={"kind": "pause_llm"})
    assert r.status_code == 202 and r.json()["actor"] == "dashboard:alice"
    assert _inbox(workspace)[0]["actor"] == "dashboard:alice"
