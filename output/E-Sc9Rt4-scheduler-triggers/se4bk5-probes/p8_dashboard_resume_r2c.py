"""T-Se4Bk5 probe p8 (w27-05 re-review): the dashboard ``POST .../resume-capped`` route (T-8knqVe,
``4f6bbc1``) validates through a WRITABLE ``FireIntentStore`` (``ui/service.py``
``DashboardService.request_capped_resume``), so the dashboard process persists ledger cost marks
into sibling fire records: the same unlocked non-engine writer as R-2b (finding R-2c).

Run (it imports the dashboard test fixtures relatively, so copy it into tests/ui):
    cp output/E-Sc9Rt4-scheduler-triggers/se4bk5-probes/p8_dashboard_resume_r2c.py \
       tests/ui/test_zz_p8_probe.py && uv run pytest -q tests/ui/test_zz_p8_probe.py; \
       rm tests/ui/test_zz_p8_probe.py
Result on eca653b: test 1 FAILS (the sibling record was rewritten by the dashboard request);
test 2 PASSES (the read-only usage GET writes nothing, the R-2 control).
After the fix (``readonly_fire_store(sdir)`` in ``request_capped_resume``) test 1 passes.
"""

# ruff: noqa: F811  (pytest fixtures are imported from the dashboard test module)
from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from agent_orchestrator.models import RunState, TaskRunState
from agent_orchestrator.service.fire_store import FireGovernance, FireIntentRecord, FireIntentStore
from agent_orchestrator.ui.service import DashboardService

from .test_schedule_usage_api import NOW, ORIGIN, URL, _paused, client, state_dir, svc  # noqa: F401


def _sibling(svc: DashboardService, state_dir: Path) -> Path:
    """Launched sibling fire ``k2`` of schedule ``a`` (mark 0.0) whose run has observed 1.5."""
    store = FireIntentStore(state_dir)
    store.write(
        FireIntentRecord(
            fire_key="k2", workspace_root=svc.workspace_root, schedule_id="a",
            scheduled_for=NOW - timedelta(minutes=20), created_at=NOW - timedelta(minutes=20),
            status="launched", run_id="r2",
            governance=FireGovernance(reserved_usd=2.0, run_cost_cap_usd=2.0),
        )
    )  # fmt: skip
    run_dir = Path(svc.workspace_root) / ".orchestrator" / "runs" / "r2"
    run_dir.mkdir(parents=True)
    st = RunState(
        run_id="r2", workflow_id="w", started_at=NOW.isoformat(), updated_at=NOW.isoformat(),
        status="running",
        tasks={
            "t": TaskRunState(status="succeeded", cumulative_cost_usd=1.5, actuals_available=True)
        },
    )  # type: ignore[arg-type]  # fmt: skip
    (run_dir / "state.json").write_text(st.model_dump_json(), encoding="utf-8")
    return store.directory(svc.workspace_root) / "k2.json"


def test_probe_dashboard_resume_request_writes_no_fire_record(
    client: TestClient, svc: DashboardService, state_dir: Path
) -> None:
    _paused(svc, state_dir)
    k2 = _sibling(svc, state_dir)
    before = k2.read_bytes()
    r = client.post(f"{URL}/a/runs/r1/resume-capped", json={"add_usd": 1.0}, headers=ORIGIN)
    assert r.status_code == 202, r.text
    assert k2.read_bytes() == before, "dashboard resume request persisted a cost mark on k2 (R-2c)"


def test_probe_dashboard_usage_get_writes_no_fire_record(
    client: TestClient, svc: DashboardService, state_dir: Path
) -> None:
    _paused(svc, state_dir)
    k2 = _sibling(svc, state_dir)
    before = k2.read_bytes()
    assert client.get(f"{URL}/a/usage").status_code == 200
    assert k2.read_bytes() == before
