"""Dashboard feedback + signals endpoints and service methods (E-Us9Kd4 T-Ui5Ij6, backend)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="dashboard API needs the optional [ui] extra")
from fastapi.testclient import TestClient  # noqa: E402

from agent_orchestrator.feedback import MAX_ENTRIES, MAX_NOTE_CHARS  # noqa: E402
from agent_orchestrator.project_config import ProjectConfig  # noqa: E402
from agent_orchestrator.ui.app import API_PREFIX, create_app  # noqa: E402
from agent_orchestrator.ui.security import DEFAULT_ALLOWED_HOSTS  # noqa: E402
from agent_orchestrator.ui.service import (  # noqa: E402
    DashboardConflictError,
    DashboardNotFoundError,
    DashboardService,
    DashboardValidationError,
)

from .conftest import StubSupervisor, make_run_state, write_run  # noqa: E402

RUN = "demo-20260724T100000Z"


@pytest.fixture()
def svc(workspace: Path, stub_supervisor: StubSupervisor) -> DashboardService:
    write_run(workspace, make_run_state(RUN))
    return DashboardService(
        str(workspace),
        supervisor=stub_supervisor,  # type: ignore[arg-type]
        project_config=ProjectConfig(),
    )


@pytest.fixture()
def client(svc: DashboardService) -> Iterator[TestClient]:
    with TestClient(create_app(svc)) as c:
        yield c


def _fb(run: str = RUN) -> str:
    return f"{API_PREFIX}/runs/{run}/feedback"


def _body(**kw: object) -> dict:
    return {"scope": "run", "rating": "good", "reasons": [], **kw}


class TestFeedbackService:
    def test_add_and_view(self, svc: DashboardService) -> None:
        out = svc.add_run_feedback(RUN, scope="run", rating="bad", reasons=["wrong"])
        assert out["entry"]["source"] == "dashboard"
        svc.add_run_feedback(RUN, scope="task", task_id="build", rating="good", reasons=[])
        view = svc.get_feedback(RUN)
        assert len(view["entries"]) == 2
        assert view["tasks"]["build"]["rating"] == "good"  # task entry beats run entry
        assert view["tasks"]["test"]["rating"] == "bad"  # falls back to run entry

    def test_unrated_task_is_none(self, svc: DashboardService) -> None:
        assert svc.get_feedback(RUN)["tasks"] == {"build": None, "test": None}

    def test_errors(self, svc: DashboardService) -> None:
        with pytest.raises(DashboardNotFoundError):
            svc.get_feedback("nope")
        with pytest.raises(DashboardValidationError):
            svc.get_feedback("..")
        with pytest.raises(DashboardNotFoundError):
            svc.add_run_feedback(RUN, scope="task", task_id="ghost", rating="ok", reasons=[])
        with pytest.raises(DashboardValidationError):
            svc.add_run_feedback(RUN, scope="task", rating="ok", reasons=[])

    def test_signals_without_survival(self, svc: DashboardService) -> None:
        out = svc.run_signals(RUN)
        assert out["run_id"] == RUN
        assert out["signals"]["run_status"] == "succeeded"
        assert out["survival"]["requested"] is False
        assert out["survival"]["available"] is False

    def test_entry_cap_conflict(self, svc: DashboardService, workspace: Path) -> None:
        fb = workspace / ".orchestrator" / "runs" / RUN / "feedback.json"
        entry = {"ts": "t", "scope": "run", "rating": "ok", "source": "cli"}
        fb.write_text(json.dumps({"schema_version": 1, "entries": [entry] * MAX_ENTRIES}))
        with pytest.raises(DashboardConflictError):
            svc.add_run_feedback(RUN, scope="run", rating="ok", reasons=[])


class TestFeedbackApi:
    def test_post_then_get(self, client: TestClient) -> None:
        r = client.post(_fb(), json=_body(rating="bad", reasons=["wrong"], note=" hi "))
        assert r.status_code == 201
        body = r.json()
        assert body["entry"]["note"] == "hi"
        assert body["entry"]["source"] == "dashboard"
        assert set(body["feedback"]) == {"run_id", "entries", "effective", "tasks"}
        got = client.get(_fb()).json()
        assert len(got["entries"]) == 1
        assert got["effective"][0]["rating"] == "bad"

    def test_client_cannot_set_source(self, client: TestClient) -> None:
        r = client.post(_fb(), json=_body(source="cli"))
        assert r.status_code == 422

    @pytest.mark.parametrize(
        "payload",
        [
            _body(rating="great"),
            _body(reasons=["bogus"]),
            _body(scope="galaxy"),
            _body(note="x" * (MAX_NOTE_CHARS + 1)),
            {"scope": "run"},
        ],
    )
    def test_bad_shapes_are_422(self, client: TestClient, payload: dict) -> None:
        assert client.post(_fb(), json=payload).status_code == 422

    def test_semantic_validation_is_400(self, client: TestClient) -> None:
        assert client.post(_fb(), json=_body(scope="task")).status_code == 400  # no task_id
        assert client.post(_fb(), json=_body(task_id="build")).status_code == 400  # run + task

    def test_unknown_task_and_run_are_404(self, client: TestClient) -> None:
        r = client.post(_fb(), json=_body(scope="task", task_id="ghost"))
        assert r.status_code == 404
        assert client.post(_fb("missing"), json=_body()).status_code == 404
        assert client.get(_fb("missing")).status_code == 404
        assert client.get(f"{API_PREFIX}/runs/missing/signals").status_code == 404

    @pytest.mark.parametrize("bad", ["..", "a%2Fb", "%2E%2E", "x" * 200, "a%2F..%2F..%2Fetc"])
    def test_bad_run_ids_rejected_and_nothing_escapes(
        self, client: TestClient, workspace: Path, bad: str
    ) -> None:
        r = client.post(_fb(bad), json=_body())
        assert 400 <= r.status_code < 500
        assert client.get(_fb(bad)).status_code in (400, 404)
        runs = workspace / ".orchestrator" / "runs"
        assert not list(workspace.rglob("feedback.json"))  # nothing written anywhere
        assert not (workspace / "feedback.json").exists()
        assert not (runs / "feedback.json").exists()

    def test_entry_cap_is_409(self, client: TestClient, workspace: Path) -> None:
        fb = workspace / ".orchestrator" / "runs" / RUN / "feedback.json"
        entry = {"ts": "t", "scope": "run", "rating": "ok", "source": "cli"}
        fb.write_text(json.dumps({"schema_version": 1, "entries": [entry] * MAX_ENTRIES}))
        assert client.post(_fb(), json=_body()).status_code == 409

    def test_corrupt_store_is_500_and_untouched(self, client: TestClient, workspace: Path) -> None:
        fb = workspace / ".orchestrator" / "runs" / RUN / "feedback.json"
        fb.write_text("{not json")
        assert client.post(_fb(), json=_body()).status_code == 500
        assert fb.read_text() == "{not json"

    def test_concurrent_posts_keep_all_entries(self, client: TestClient) -> None:
        n = 12
        with ThreadPoolExecutor(max_workers=6) as pool:
            codes = list(
                pool.map(
                    lambda i: client.post(_fb(), json=_body(note=f"n{i}")).status_code, range(n)
                )
            )
        assert codes == [201] * n
        notes = {e["note"] for e in client.get(_fb()).json()["entries"]}
        assert notes == {f"n{i}" for i in range(n)}

    def test_signals_shape(self, client: TestClient) -> None:
        body = client.get(f"{API_PREFIX}/runs/{RUN}/signals").json()
        assert set(body) == {"run_id", "signals", "survival"}
        assert set(body["survival"]) == {
            "requested",
            "available",
            "reason",
            "ref",
            "total",
            "tasks",
        }

    def test_signals_bad_ref_400(self, client: TestClient) -> None:
        base = f"{API_PREFIX}/runs/{RUN}/signals"
        assert client.get(base, params={"survival": "true", "ref": "--evil"}).status_code == 400
        assert client.get(base, params={"ref": "main"}).status_code == 400

    def test_signals_with_survival_degrades_gracefully(self, client: TestClient) -> None:
        r = client.get(f"{API_PREFIX}/runs/{RUN}/signals", params={"survival": "true"})
        assert r.status_code == 200
        assert r.json()["survival"]["requested"] is True


class TestFeedbackSecurity:
    @pytest.fixture()
    def hardened(self, svc: DashboardService) -> Iterator[TestClient]:
        app = create_app(svc, allowed_hosts=DEFAULT_ALLOWED_HOSTS)
        with TestClient(app, base_url="http://localhost") as c:
            yield c

    def test_cross_origin_post_rejected(self, hardened: TestClient, workspace: Path) -> None:
        r = hardened.post(_fb(), json=_body(), headers={"origin": "http://evil.example"})
        assert r.status_code == 403
        assert not list(workspace.rglob("feedback.json"))

    def test_same_origin_post_allowed(self, hardened: TestClient) -> None:
        r = hardened.post(_fb(), json=_body(), headers={"origin": "http://localhost"})
        assert r.status_code == 201

    def test_non_json_content_type_rejected(self, hardened: TestClient, workspace: Path) -> None:
        r = hardened.post(
            _fb(), content=json.dumps(_body()), headers={"content-type": "text/plain"}
        )
        assert r.status_code == 415
        assert not list(workspace.rglob("feedback.json"))
