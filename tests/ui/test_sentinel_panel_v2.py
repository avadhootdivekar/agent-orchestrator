"""T-Sn9Pow: GET /runs/{id}/sentinel/panel (generations, proposals, directives)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="dashboard API needs the optional [ui] extra")
from fastapi.testclient import TestClient  # noqa: E402

from agent_orchestrator.models import SentinelState, TaskRunState  # noqa: E402
from agent_orchestrator.project_config import ProjectConfig  # noqa: E402
from agent_orchestrator.ui.app import API_PREFIX, create_app  # noqa: E402
from agent_orchestrator.ui.service import DashboardService  # noqa: E402

from .conftest import StubSupervisor, make_run_state, write_run  # noqa: E402

RUN = "demo-20260724T100000Z"
BARE = "bare-20260724T100000Z"


def _w(path: Path, doc: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(doc if isinstance(doc, str) else json.dumps(doc))


@pytest.fixture()
def client(workspace: Path, stub_supervisor: StubSupervisor) -> Iterator[TestClient]:
    state = make_run_state(
        RUN,
        status="running",
        tasks={
            "sentinel-g01": TaskRunState(
                status="succeeded",
                started_at="2026-07-24T10:00:00+00:00",
                ended_at="2026-07-24T10:20:00+00:00",
                cumulative_cost_usd=0.5,
            ),
            "sentinel-g02": TaskRunState(status="running", started_at="2026-07-24T11:00:00+00:00"),
            "build": TaskRunState(status="running"),
        },
    )
    state.sentinel = SentinelState(generation=2)
    write_run(workspace, state)
    write_run(workspace, make_run_state(BARE, status="succeeded"))
    run_dir = workspace / ".orchestrator" / "runs" / RUN
    s, c = run_dir / "sentinel", run_dir / "control"
    _w(s / "g01" / "triggers.jsonl", '{"kind":"idle_slots_no_backlog"}\n{"kind":"heartbeat"}\n')
    _w(
        s / "ledger.jsonl",
        json.dumps(
            {
                "event": "generation_ended",
                "data": {"generation": 1, "graceful": True, "ended_reason": "explicit_handoff"},
            }
        )
        + "\n",
    )
    ok = {"id": "g1-inj-0001", "kind": "inject", "actor": "sentinel-g01", "created_at": "t1"}
    human = {"id": "ctl-note-001", "kind": "note", "actor": "ctl:me", "created_at": "t2"}
    bad = {"id": "g1-kill-001", "kind": "kill", "actor": "sentinel-g01", "created_at": "t3"}
    _w(c / "inbox-processed" / "g1-inj-0001.json", ok)
    _w(c / "inbox-processed" / "ctl-note-001.json", human)
    _w(c / "inbox-rejected" / "g1-kill-001.json", bad)
    _w(
        c / "inbox-rejected" / "g1-kill-001.reason.json",
        {"rule_id": "PWR-KILL-RATE", "detail": "cap"},
    )
    _w(
        c / "directives.jsonl",
        "".join(
            json.dumps(
                {
                    "id": i,
                    "at": "t",
                    "actor": "sentinel-g01",
                    "severity": "warn",
                    "text": f"text {i}",
                    "rationale": "r",
                }
            )
            + "\n"
            for i in ("dir-open-1", "dir-acked1")
        ),
    )
    _w(
        c / "directive-acks.jsonl",
        json.dumps(
            {
                "directive_id": "dir-acked1",
                "response": "adopt",
                "checkpoint": "ck-02",
                "rationale": None,
                "at": "t9",
            }
        )
        + "\n",
    )
    svc = DashboardService(
        str(workspace),
        supervisor=stub_supervisor,
        project_config=ProjectConfig(),  # type: ignore[arg-type]
    )
    with TestClient(create_app(svc)) as tc:
        yield tc


def test_panel_v2_document(client: TestClient) -> None:
    doc = client.get(f"{API_PREFIX}/runs/{RUN}/sentinel/panel").json()
    assert doc["schema"] == "ao.sentinel.panel/v2"
    g1, g2 = doc["generations"]
    assert g1["generation"] == 1 and g1["cost_usd"] == 0.5 and g1["graceful"] is True
    assert g1["triggers"] == ["idle_slots_no_backlog", "heartbeat"]
    assert g1["ended_reason"] == "explicit_handoff" and g2["status"] == "running"
    assert doc["spend_usd"] == 0.5
    assert [p["id"] for p in doc["proposals"]["applied"]] == ["g1-inj-0001"]  # human excluded
    (q,) = doc["proposals"]["quarantined"]
    assert q["id"] == "g1-kill-001" and q["rule_id"] == "PWR-KILL-RATE"
    open_, acked = doc["directives"]
    assert open_["ack"] is None and acked["ack"]["response"] == "adopt"
    assert acked["ack"]["checkpoint"] == "ck-02"


def test_run_without_footprint_gets_empty_lists_and_unknown_is_404(client: TestClient) -> None:
    doc = client.get(f"{API_PREFIX}/runs/{BARE}/sentinel/panel").json()
    assert doc["generations"] == [] and doc["directives"] == []
    assert doc["proposals"] == {"applied": [], "quarantined": []}
    assert client.get(f"{API_PREFIX}/runs/nope-1/sentinel/panel").status_code == 404
