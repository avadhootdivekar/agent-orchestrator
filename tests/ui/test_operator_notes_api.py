"""Queued operator notes endpoints + service (A6 backend)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="dashboard API needs the optional [ui] extra")
from fastapi.testclient import TestClient  # noqa: E402

from agent_orchestrator.feedback import (  # noqa: E402
    MAX_OPERATOR_NOTE_CHARS,
    MAX_OPERATOR_NOTES,
    OPERATOR_NOTES_FILE,
)
from agent_orchestrator.project_config import ProjectConfig  # noqa: E402
from agent_orchestrator.ui.app import API_PREFIX, create_app  # noqa: E402
from agent_orchestrator.ui.service import (  # noqa: E402
    DashboardConflictError,
    DashboardNotFoundError,
    DashboardService,
)

from .conftest import StubSupervisor, make_run_state, write_run  # noqa: E402

RUN = "demo-20260724T100000Z"


@pytest.fixture()
def live(stub_supervisor: StubSupervisor) -> StubSupervisor:
    stub_supervisor.live_runs.add(RUN)
    return stub_supervisor


@pytest.fixture()
def svc(workspace: Path, live: StubSupervisor) -> DashboardService:
    write_run(workspace, make_run_state(RUN, status="running"))
    return DashboardService(
        str(workspace),
        supervisor=live,  # type: ignore[arg-type]
        project_config=ProjectConfig(),
    )


@pytest.fixture()
def client(svc: DashboardService) -> Iterator[TestClient]:
    with TestClient(create_app(svc)) as c:
        yield c


def _url(run: str = RUN) -> str:
    return f"{API_PREFIX}/runs/{run}/notes"


class TestApi:
    def test_post_without_any_pending_request_then_get(
        self, client: TestClient, workspace: Path
    ) -> None:
        r = client.post(_url(), json={"text": "  use the staging db  "})
        assert r.status_code == 201
        body = r.json()
        assert body["note"]["text"] == "use the staging db"
        assert body["note"]["source"] == "dashboard"  # server-set
        assert body["notes"]["accepting"] is True
        md = workspace / ".orchestrator" / "runs" / RUN / OPERATOR_NOTES_FILE
        assert "use the staging db" in md.read_text(encoding="utf-8")

        client.post(_url(), json={"text": "second"})
        got = client.get(_url()).json()
        assert [n["text"] for n in got["notes"]] == ["use the staging db", "second"]
        assert got["max_chars"] == MAX_OPERATOR_NOTE_CHARS
        assert got["max_notes"] == MAX_OPERATOR_NOTES

    def test_html_is_stored_and_served_verbatim_as_json(self, client: TestClient) -> None:
        evil = "<img src=x onerror=alert(1)>"
        client.post(_url(), json={"text": evil})
        r = client.get(_url())
        assert r.headers["content-type"].startswith("application/json")
        assert r.json()["notes"][0]["text"] == evil

    def test_unknown_run_is_404(self, client: TestClient) -> None:
        assert client.post(_url("nope"), json={"text": "hi"}).status_code == 404
        assert client.get(_url("nope")).status_code == 404

    def test_not_live_is_409_and_nothing_written(
        self, client: TestClient, live: StubSupervisor, workspace: Path
    ) -> None:
        live.live_runs.discard(RUN)
        r = client.post(_url(), json={"text": "hi"})
        assert r.status_code == 409 and "not live" in r.json()["detail"]
        assert not (workspace / ".orchestrator" / "runs" / RUN / OPERATOR_NOTES_FILE).exists()
        got = client.get(_url()).json()  # history stays readable, but flags it
        assert got["accepting"] is False and got["notes"] == []

    def test_finished_run_state_is_409_even_if_process_lingers(
        self, workspace: Path, live: StubSupervisor
    ) -> None:
        write_run(workspace, make_run_state(RUN, status="succeeded"))
        svc = DashboardService(
            str(workspace),
            supervisor=live,  # type: ignore[arg-type]
            project_config=ProjectConfig(),
        )
        with pytest.raises(DashboardConflictError):
            svc.add_run_operator_note(RUN, text="hi")

    @pytest.mark.parametrize(
        "body",
        [
            {},
            {"text": ""},
            {"text": "   "},
            {"text": "ok", "source": "cli"},  # client cannot set the source
            {"text": "ok", "extra": 1},
            {"text": 5},
            {"text": "x" * (MAX_OPERATOR_NOTE_CHARS * 2 + 1)},  # body-level bound
        ],
    )
    def test_invalid_bodies(self, client: TestClient, body: dict) -> None:
        assert client.post(_url(), json=body).status_code in (400, 422)

    def test_oversize_is_rejected_not_trimmed(self, client: TestClient) -> None:
        r = client.post(_url(), json={"text": "x" * (MAX_OPERATOR_NOTE_CHARS + 1)})
        assert r.status_code == 400
        assert client.get(_url()).json()["notes"] == []

    def test_note_cap_is_409(self, client: TestClient) -> None:
        for i in range(MAX_OPERATOR_NOTES):
            assert client.post(_url(), json={"text": f"n{i}"}).status_code == 201
        assert client.post(_url(), json={"text": "extra"}).status_code == 409

    def test_corrupt_index_is_500_and_untouched(self, client: TestClient, workspace: Path) -> None:
        idx = workspace / ".orchestrator" / "runs" / RUN / "operator-notes.json"
        idx.write_text("{nope")
        r = client.post(_url(), json={"text": "hi"})
        assert r.status_code == 500
        assert idx.read_text() == "{nope"


class TestService:
    def test_unknown_run(self, svc: DashboardService) -> None:
        with pytest.raises(DashboardNotFoundError):
            svc.get_operator_notes("nope")
