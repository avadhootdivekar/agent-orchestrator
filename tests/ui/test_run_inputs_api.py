"""Awaiting-input dashboard routes (HLD §5.3): GET/POST /api/runs/{id}/inputs[/{request_id}]."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="dashboard API needs the optional [ui] extra")
from fastapi.testclient import TestClient  # noqa: E402

from agent_orchestrator import inputs  # noqa: E402
from agent_orchestrator.models import (  # noqa: E402
    InputField,
    InputRequestSpec,
    TaskRunState,
)
from agent_orchestrator.project_config import ProjectConfig  # noqa: E402
from agent_orchestrator.ui.app import API_PREFIX, create_app  # noqa: E402
from agent_orchestrator.ui.service import DashboardService  # noqa: E402

from .conftest import StubSupervisor, make_run_state, write_run  # noqa: E402

RUN = "demo-20260724T100000Z"
HOSTILE = "<img src=x onerror=alert(1)> pick one"


def _spec() -> InputRequestSpec:
    return InputRequestSpec(
        prompt=HOSTILE,
        fields=[
            InputField(name="db", type="choice", choices=["postgres", "sqlite"], default="sqlite"),
            InputField(name="note", type="text", required=False, max_chars=20),
            InputField(name="ok", type="bool", default=True),
        ],
    )


@pytest.fixture()
def run_dir(workspace: Path) -> Path:
    state = make_run_state(
        RUN,
        status="running",
        tasks={"pick": TaskRunState(status="awaiting_input", attempts=1)},
    )
    return write_run(workspace, state)


@pytest.fixture()
def request_id(run_dir: Path) -> str:
    return inputs.open_request(run_dir, "pick", _spec()).request_id


@pytest.fixture()
def svc(workspace: Path, run_dir: Path, stub_supervisor: StubSupervisor) -> DashboardService:
    return DashboardService(
        str(workspace),
        supervisor=stub_supervisor,  # type: ignore[arg-type]
        project_config=ProjectConfig(),
    )


@pytest.fixture()
def client(svc: DashboardService) -> Iterator[TestClient]:
    with TestClient(create_app(svc)) as c:
        yield c


def _url(run: str = RUN, rid: str = "") -> str:
    return f"{API_PREFIX}/runs/{run}/inputs" + (f"/{rid}" if rid else "")


class TestGet:
    def test_lists_requests_and_waiting_flag(self, client: TestClient, request_id: str) -> None:
        r = client.get(_url())
        assert r.status_code == 200
        body = r.json()
        assert body["waiting_for_input"] is True
        assert r.headers["content-type"].startswith("application/json")
        (req,) = body["requests"]
        assert req["request_id"] == request_id and req["state"] == "open"
        assert req["prompt"] == HOSTILE  # served verbatim as JSON text, never as HTML
        assert [f["name"] for f in req["fields"]] == ["db", "note", "ok"]
        assert req["fields"][0]["choices"] == ["postgres", "sqlite"]

    def test_no_requests(self, workspace: Path, stub_supervisor: StubSupervisor) -> None:
        write_run(workspace, make_run_state(RUN, status="running"))
        svc = DashboardService(
            str(workspace),
            supervisor=stub_supervisor,  # type: ignore[arg-type]
            project_config=ProjectConfig(),
        )
        with TestClient(create_app(svc)) as c:
            assert c.get(_url()).json() == {
                "run_id": RUN,
                "waiting_for_input": False,
                "requests": [],
            }

    def test_unknown_run_is_404(self, client: TestClient) -> None:
        assert client.get(_url("nope")).status_code == 404

    def test_run_summary_carries_waiting_flag(self, client: TestClient) -> None:
        body = client.get(f"{API_PREFIX}/runs/{RUN}").json()
        assert body["summary"]["waiting_for_input"] is True
        assert body["summary"]["task_counts"] == {"awaiting_input": 1}


class TestPost:
    def test_answer_is_recorded_via_shared_writer(
        self, client: TestClient, run_dir: Path, request_id: str, stub_supervisor: StubSupervisor
    ) -> None:
        r = client.post(_url(rid=request_id), json={"values": {"db": "postgres", "ok": False}})
        assert r.status_code == 200
        body = r.json()
        assert body["resumed"] is False and "launch" not in body
        ans = body["request"]["answer"]
        assert ans["values"] == {"db": "postgres", "ok": False}
        assert (ans["source"], ans["assumed"]) == ("web", False)  # source is server-set
        on_disk = json.loads((run_dir / "inputs" / f"{request_id}.json").read_text())
        assert on_disk["state"] == "answered"
        assert stub_supervisor.launch_calls == []

    def test_answer_and_resume(
        self, client: TestClient, request_id: str, stub_supervisor: StubSupervisor
    ) -> None:
        r = client.post(_url(rid=request_id), json={"values": {"db": "sqlite"}, "resume": True})
        assert r.status_code == 200
        body = r.json()
        assert body["resumed"] is True and body["launch"]["kind"] == "resume"
        assert [c["run_id"] for c in stub_supervisor.launch_calls] == [RUN]

    def test_resume_refused_keeps_answer(
        self, client: TestClient, run_dir: Path, request_id: str, stub_supervisor: StubSupervisor
    ) -> None:
        stub_supervisor.live_runs.add(RUN)
        r = client.post(_url(rid=request_id), json={"values": {"db": "sqlite"}, "resume": True})
        assert r.status_code == 409
        assert inputs.load_request(run_dir, request_id).state == "answered"

    @pytest.mark.parametrize(
        "values",
        [
            {"db": "mysql"},  # not a listed choice
            {"nope": "x"},  # unknown field
            {"db": "sqlite", "note": "x" * 21},  # over max_chars
            {"db": "sqlite", "ok": "maybe"},  # not a bool
        ],
    )
    def test_invalid_values_are_400_and_leave_request_open(
        self, client: TestClient, run_dir: Path, request_id: str, values: dict
    ) -> None:
        r = client.post(_url(rid=request_id), json={"values": values})
        assert r.status_code == 400
        assert inputs.load_request(run_dir, request_id).state == "open"

    def test_required_field_without_default_is_400(self, client: TestClient, run_dir: Path) -> None:
        spec = InputRequestSpec(prompt="p", fields=[InputField(name="who", type="text")])
        rid = inputs.open_request(run_dir, "pick", spec).request_id
        assert client.post(_url(rid=rid), json={"values": {}}).status_code == 400

    def test_reanswer_same_is_idempotent_different_is_409(
        self, client: TestClient, request_id: str
    ) -> None:
        first = {"values": {"db": "postgres", "ok": True}}
        assert client.post(_url(rid=request_id), json=first).status_code == 200
        assert client.post(_url(rid=request_id), json=first).status_code == 200
        other = client.post(_url(rid=request_id), json={"values": {"db": "sqlite", "ok": True}})
        assert other.status_code == 409

    def test_unknown_request_and_run_are_404(self, client: TestClient, request_id: str) -> None:
        assert client.post(_url(rid="q-nope-1"), json={"values": {}}).status_code == 404
        assert client.post(_url("nope", request_id), json={"values": {}}).status_code == 404

    @pytest.mark.parametrize(
        "body",
        [
            {"values": {"db": "sqlite"}, "source": "cli"},  # extra key forbidden
            {"values": {"db": ["a"]}},  # non-scalar value
            {"values": {f"f{i}": "x" for i in range(21)}},  # too many fields
        ],
    )
    def test_malformed_body_is_422(self, client: TestClient, request_id: str, body: dict) -> None:
        assert client.post(_url(rid=request_id), json=body).status_code == 422

    def test_bad_request_id_is_rejected(self, client: TestClient) -> None:
        assert client.post(_url(rid="BAD ID"), json={"values": {}}).status_code == 400
