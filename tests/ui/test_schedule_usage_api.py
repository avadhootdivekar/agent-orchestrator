"""``GET /api/schedules/{id}/usage`` and ``/api/schedules/governance-events`` (T-8knqVe).

Fixed clock; the scheduler state dir is a per-test temp dir.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml

pytest.importorskip("fastapi", reason="dashboard API needs the optional [ui] extra")
from fastapi.testclient import TestClient  # noqa: E402

from agent_orchestrator.service.fire_store import (  # noqa: E402
    FireGovernance,
    FireIntentRecord,
    FireIntentStore,
    ScheduleEventLog,
)
from agent_orchestrator.ui.app import API_PREFIX, create_app  # noqa: E402
from agent_orchestrator.ui.service import DashboardService  # noqa: E402

from .conftest import StubSupervisor  # noqa: E402

NOW = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)
URL = f"{API_PREFIX}/schedules"


def _binding(sid: str, per_run: float | None = None) -> dict[str, Any]:
    b = {
        "id": sid,
        "kind": "cron",
        "schedule": "0 * * * *",
        "workflow": "w.json",
        "limits": {
            "quotas": [{"period": "day", "max_runs": 2}],
            "cost": {"periods": [{"period": "day", "max_usd": 5.0}]},
        },
    }
    if per_run is not None:
        b["limits"]["cost"]["per_run_usd"] = per_run
    return b


@pytest.fixture()
def state_dir(tmp_path: Path) -> Path:
    return tmp_path / "state"


@pytest.fixture()
def svc(workspace: Path, stub_supervisor: StubSupervisor, state_dir: Path) -> DashboardService:
    path = workspace / ".ao" / "schedules.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(
            {
                "version": "1.0",
                "schedules": [_binding("a"), _binding("b"), _binding("p", per_run=1.0)],
            }
        )
    )
    return DashboardService(
        str(workspace),
        supervisor=stub_supervisor,  # type: ignore[arg-type]
        clock=lambda: NOW,
        schedule_state_dir=state_dir,
    )


@pytest.fixture()
def client(svc: DashboardService) -> TestClient:
    return TestClient(create_app(svc))


@pytest.fixture()
def emit(svc: DashboardService, state_dir: Path) -> Callable[..., None]:
    def _emit(event: str, sid: str, at: datetime = NOW, **fields: Any) -> None:
        ScheduleEventLog(state_dir, clock=lambda: at).emit(
            event, workspace=svc.workspace_root, schedule_id=sid, **fields
        )

    return _emit


def test_usage_empty_window_shows_limits(client: TestClient) -> None:
    r = client.get(f"{URL}/a/usage")
    assert r.status_code == 200
    body = r.json()
    assert body["schema"] == "ao.schedule.usage/v1" and body["schedule_id"] == "a"
    (w,) = body["windows"]
    assert (w["period"], w["runs_used"], w["runs_max"], w["max_usd"]) == ("day", 0, 2, 5.0)
    assert w["resets_at"] == "2026-11-12T00:00:00+00:00"
    assert w["skipped"] == 0 and w["quota_exhausted"] == 0


def test_usage_counts_quota_exhausted_skips_in_window_only(
    client: TestClient, emit: Callable[..., None]
) -> None:
    emit("schedule.governance.skipped", "a", reason="quota_exhausted")
    emit("schedule.governance.skipped", "a", reason="cost_cap_reached")
    emit("schedule.governance.skipped", "a", NOW - timedelta(days=2), reason="quota_exhausted")
    emit("schedule.governance.skipped", "b", reason="quota_exhausted")
    (w,) = client.get(f"{URL}/a/usage").json()["windows"]
    assert (w["skipped"], w["quota_exhausted"]) == (2, 1)


def test_usage_reflects_fire_records_through_the_real_ledger(
    client: TestClient, svc: DashboardService, state_dir: Path
) -> None:
    """The panel's numbers come from the same ledger the gate uses: seed fire records (no mocked
    ledger) and check runs / actual / reserved / confidence, window bounds and schedule scoping."""
    store = FireIntentStore(state_dir)

    def put(key: str, at: datetime, sid: str = "a", status: str = "launched", **gov: Any) -> None:
        store.write(
            FireIntentRecord(
                fire_key=key, workspace_root=svc.workspace_root, schedule_id=sid,
                scheduled_for=at, created_at=at, status=status,  # type: ignore[arg-type]
                run_id=f"run-{key}", governance=FireGovernance(**gov) if gov else None,
            )
        )  # fmt: skip

    put("done", NOW - timedelta(hours=3), final=True, cost_observed_usd=1.25,
        cost_confidence="actual", reserved_usd=2.0)  # fmt: skip
    put("running", NOW - timedelta(hours=1), reserved_usd=2.0)  # no run state yet: held
    put("skip", NOW - timedelta(hours=2), status="suppressed", reserved_usd=2.0)  # costs nothing
    put("yesterday", NOW - timedelta(days=1), final=True, cost_observed_usd=9.0)
    put("other", NOW - timedelta(hours=1), sid="b", final=True, cost_observed_usd=9.0)
    (w,) = client.get(f"{URL}/a/usage").json()["windows"]
    assert (w["runs_used"], w["runs_max"]) == (2, 2)
    assert w["actual_usd"] == pytest.approx(1.25) and w["reserved_usd"] == pytest.approx(2.0)
    assert w["confidence"] == "pending"
    (prev,) = client.get(
        f"{URL}/a/usage", params={"at": (NOW - timedelta(days=1)).isoformat()}
    ).json()["windows"]
    assert (prev["runs_used"], prev["actual_usd"]) == (1, pytest.approx(9.0))


def test_usage_at_selects_window(client: TestClient) -> None:
    (w,) = client.get(f"{URL}/a/usage", params={"at": "2026-11-05T10:00:00+00:00"}).json()[
        "windows"
    ]
    assert w["window_key"] == "day:2026-11-05"


def test_usage_errors(client: TestClient) -> None:
    assert client.get(f"{URL}/nope/usage").status_code == 404
    assert client.get(f"{URL}/a/usage", params={"at": "garbage"}).status_code == 400


@pytest.mark.parametrize("at", ["9999-12-31T23:59:59+00:00", "0001-01-01T00:00:00+00:00"])
def test_usage_at_out_of_range_is_400_not_500(client: TestClient, at: str) -> None:
    """T-lTW88y audit: window math overflows near datetime.max; refuse instead of a 500."""
    assert client.get(f"{URL}/a/usage", params={"at": at}).status_code == 400


def test_governance_events_newest_first_filtered_and_clamped(
    client: TestClient, emit: Callable[..., None]
) -> None:
    emit("schedule.governance.skipped", "a", NOW - timedelta(hours=2), reason="quota_exhausted")
    emit("schedule.governance.resumed", "a", NOW - timedelta(hours=1))
    emit("schedule.fired", "a", NOW)  # not governance
    emit("schedule.governance.skipped", "b", NOW, reason="cost_cap_reached", prompt_text="SECRET")
    events = client.get(f"{URL}/governance-events").json()["events"]
    assert [e["event"] for e in events] == [
        "schedule.governance.skipped",
        "schedule.governance.resumed",
        "schedule.governance.skipped",
    ]
    assert "SECRET" not in client.get(f"{URL}/governance-events").text
    only_a = client.get(f"{URL}/governance-events", params={"id": "a"}).json()["events"]
    assert {e["schedule_id"] for e in only_a} == {"a"} and len(only_a) == 2
    assert len(client.get(f"{URL}/governance-events", params={"limit": 1}).json()["events"]) == 1
    assert client.get(f"{URL}/governance-events", params={"limit": 0}).status_code == 422
    assert client.get(f"{URL}/governance-events", params={"limit": 501}).status_code == 422
    assert client.get(f"{URL}/governance-events", params={"id": "nope"}).status_code == 404


def test_usage_api_never_writes_fire_records(
    client: TestClient, svc: DashboardService, state_dir: Path
) -> None:
    """T-Se4Bk5 R-2: the dashboard is a second process; its ledger must not persist cost marks
    (unlocked read-modify-write racing the engine's fire writes)."""
    store = FireIntentStore(state_dir)
    store.write(
        FireIntentRecord(
            fire_key="k", workspace_root=svc.workspace_root, schedule_id="a",
            scheduled_for=NOW - timedelta(hours=1), created_at=NOW - timedelta(hours=1),
            status="launched", run_id="r",
            governance=FireGovernance(reserved_usd=2.0, run_cost_cap_usd=2.0),
        )
    )  # fmt: skip
    run_dir = Path(svc.workspace_root) / ".orchestrator" / "runs" / "r"
    run_dir.mkdir(parents=True)
    from agent_orchestrator.models import RunState, TaskRunState

    st = RunState(
        run_id="r", workflow_id="w", started_at=NOW.isoformat(), updated_at=NOW.isoformat(),
        status="running",
        tasks={
            "t": TaskRunState(status="succeeded", cumulative_cost_usd=1.5, actuals_available=True)
        },
    )  # type: ignore[arg-type]  # fmt: skip
    (run_dir / "state.json").write_text(st.model_dump_json(), encoding="utf-8")
    path = store.directory(svc.workspace_root) / "k.json"
    before = (path.read_bytes(), path.stat().st_mtime_ns)
    (w,) = client.get(f"{URL}/a/usage").json()["windows"]
    assert w["actual_usd"] == pytest.approx(1.5)  # observed cost is reported...
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before  # ...never persisted


def test_usage_row_keys_match_ui_type_ssot(client: TestClient) -> None:
    """Drift guard for the mocked-fetch UI test (``ui/src/test/schedule-usage.test.tsx``): the
    real endpoint's per-window field names must equal ``ScheduleUsageWindow`` in ``ui/src/types.ts``
    (the UI fixture is typed ``ScheduleUsageDoc``, so tsc ties the mock to the same interface)."""
    import re

    types_ts = Path(__file__).resolve().parents[2] / "ui" / "src" / "types.ts"
    if not types_ts.is_file():
        pytest.skip("ui/src/types.ts not present (installed wheel / sparse checkout)")
    text = types_ts.read_text()
    m = re.search(r"export interface ScheduleUsageWindow \{(.*?)\n\}", text, re.S)
    assert m, "ScheduleUsageWindow interface not found in ui/src/types.ts"
    ts_fields = set(re.findall(r"^\s+(\w+)\??:", m.group(1), re.M))
    doc = client.get(f"{URL}/a/usage").json()
    (w,) = doc["windows"]
    assert set(w) == ts_fields
    m2 = re.search(r"export interface ScheduleUsageDoc \{(.*?)\n\}", text, re.S)
    assert m2
    assert set(doc) == set(re.findall(r"^\s+(\w+)\??:", m2.group(1), re.M))


# -- paused runs + resume-capped (T-8knqVe AC3 / AC5) ---------------------------------------------

ORIGIN = {"Origin": "http://testserver"}


def _paused(
    svc: DashboardService,
    state_dir: Path,
    *,
    run_id: str = "r1",
    sid: str = "a",
    cap_state: str = "paused",
    expires: datetime | None = None,
    spent: float = 1.0,
    engine: float | None = None,
) -> None:
    """A governed fire record of schedule *sid*; ``paused`` unless *cap_state* says otherwise."""
    FireIntentStore(state_dir).write(
        FireIntentRecord(
            fire_key=f"k-{run_id}", workspace_root=svc.workspace_root, schedule_id=sid,
            scheduled_for=NOW - timedelta(hours=1), created_at=NOW - timedelta(hours=1),
            status="launched", run_id=run_id,
            governance=FireGovernance(
                cap_state=cap_state, run_cost_cap_usd=2.0, cost_observed_usd=spent,
                engine_cost_usd=engine,
                paused_at=NOW - timedelta(minutes=30),
                pause_expires_at=expires or NOW + timedelta(hours=10),
            ),
        )
    )  # fmt: skip


def _live_state(svc: DashboardService, run_id: str, cost: float) -> None:
    """A running ``state.json`` for *run_id* reporting *cost* (so a writable ledger store would
    raise the persisted high-water mark above the fire record's ``cost_observed_usd``)."""
    from agent_orchestrator.models import RunState, TaskRunState

    st = RunState(
        run_id=run_id, workflow_id="w", started_at=NOW.isoformat(), updated_at=NOW.isoformat(),
        status="running",
        tasks={"t0": TaskRunState(status="running", cumulative_cost_usd=cost,
                                  actuals_available=True)},
    )  # fmt: skip
    d = Path(svc.workspace_root) / ".orchestrator" / "runs" / run_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "state.json").write_text(st.model_dump_json(), encoding="utf-8")


def _queued(state_dir: Path) -> list[dict[str, Any]]:
    import json

    d = state_dir / "schedules" / "requests"
    return [json.loads(p.read_text()) for p in sorted(d.glob("*.json"))] if d.is_dir() else []


def test_paused_runs_lists_only_paused_with_bounds(
    client: TestClient, svc: DashboardService, state_dir: Path
) -> None:
    _paused(svc, state_dir, run_id="r1")
    _paused(svc, state_dir, run_id="r2", cap_state="resumed")
    _paused(svc, state_dir, run_id="r3", sid="b")
    _paused(svc, state_dir, run_id="r4", expires=NOW - timedelta(minutes=1))
    rows = client.get(f"{URL}/a/paused-runs").json()["paused"]
    assert {r["run_id"] for r in rows} == {"r1", "r4"}
    r1 = next(r for r in rows if r["run_id"] == "r1")
    assert (r1["spent_usd"], r1["cap_usd"]) == (1.0, 2.0)
    assert r1["resumable"] is True and r1["refusal"] is None
    assert r1["default_add_usd"] > 0
    assert r1["max_add_usd"] is None  # binding "a" has no per_run_usd: no MAX_USD ceiling (G-8)
    assert r1["overshoot_usd"] == 0.0  # spent 1.0 of cap 2.0
    r4 = next(r for r in rows if r["run_id"] == "r4")
    assert (r4["resumable"], r4["refusal"]) == (False, "expired")
    assert client.get(f"{URL}/nope/paused-runs").status_code == 404


def test_paused_runs_spent_is_the_engine_cost_that_overshoot_is_measured_on(
    client: TestClient, svc: DashboardService, state_dir: Path
) -> None:
    """REVIEW G-13: charge 5.0 (reservation-inflated) vs engine cost 2.5, cap 2.0 -> the row reads
    spent 2.5 / cap 2.0 / overshoot 0.5, which reconciles."""
    _paused(svc, state_dir, run_id="r1", spent=5.0, engine=2.5)
    (row,) = client.get(f"{URL}/a/paused-runs").json()["paused"]
    assert (row["spent_usd"], row["cap_usd"], row["overshoot_usd"]) == (2.5, 2.0, 0.5)


def test_usage_get_does_not_scan_paused_or_events_via_build_usage_doc(
    client: TestClient, svc: DashboardService, state_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """REVIEW G-16/G-19: the dashboard usage GET builds only the windows. ``build_usage_doc`` is
    patched in the consumer module (``ui.service``); ``paused_records`` / ``governance_events``
    are the helpers the shared builder would call, patched in ``usage_view``."""
    from agent_orchestrator.schedules import usage_view
    from agent_orchestrator.ui import service as ui_service

    def boom(*a: Any, **k: Any) -> Any:
        raise AssertionError("usage GET must not call build_usage_doc / paused scan")

    monkeypatch.setattr(ui_service, "build_usage_doc", boom, raising=False)
    monkeypatch.setattr(usage_view, "build_usage_doc", boom)
    monkeypatch.setattr(usage_view, "paused_records", boom)
    monkeypatch.setattr(usage_view, "governance_events", boom)
    assert client.get(f"{URL}/a/usage").status_code == 200


def test_cost_cap_option_lookup_uses_read_only_fire_store(
    svc: DashboardService, monkeypatch: pytest.MonkeyPatch
) -> None:
    """REVIEW G-20: ``_check_cost_cap_option`` looks the run up through a ``ReadOnlyFireStore``."""
    from agent_orchestrator.service.fire_store import ReadOnlyFireStore
    from agent_orchestrator.ui import service as ui_service

    seen: list[Any] = []

    def spy(workspace: Any, run_id: str, *, fires: Any) -> bool:
        seen.append(fires)
        return False

    monkeypatch.setattr(ui_service, "is_governed_run", spy)
    svc._check_cost_cap_option({"cost_cap_usd": 1.0}, run_id="r1")
    assert len(seen) == 1 and isinstance(seen[0], ReadOnlyFireStore)


def test_cli_warn_if_schedule_governed_uses_read_only_fire_store(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """REVIEW G-20: ``cli._warn_if_schedule_governed`` reads fires via a ``ReadOnlyFireStore``."""
    from agent_orchestrator import cli
    from agent_orchestrator.service import capped_resume
    from agent_orchestrator.service.fire_store import ReadOnlyFireStore

    monkeypatch.setenv("AO_SERVICE_STATE_DIR", str(tmp_path))
    seen: list[Any] = []

    def spy(workspace: Any, run_id: str, *, fires: Any) -> bool:
        seen.append(fires)
        return False

    monkeypatch.setattr(capped_resume, "is_governed_run", spy)
    cli._warn_if_schedule_governed(str(tmp_path), "r1")
    assert len(seen) == 1 and isinstance(seen[0], ReadOnlyFireStore)


def test_paused_runs_max_add_is_per_run_bound_only_when_per_run_usd_is_set(
    client: TestClient, svc: DashboardService, state_dir: Path
) -> None:
    """G-8: with per_run_usd the ceiling is per_run * factor; without it null (never MAX_USD)."""
    from agent_orchestrator.service.capped_resume import MAX_RESUME_FACTOR

    _paused(svc, state_dir, run_id="rp", sid="p")
    (row,) = client.get(f"{URL}/p/paused-runs").json()["paused"]
    assert row["max_add_usd"] == 1.0 * MAX_RESUME_FACTOR
    _paused(svc, state_dir, run_id="rn", sid="a")
    (row,) = client.get(f"{URL}/a/paused-runs").json()["paused"]
    assert row["max_add_usd"] is None


def test_paused_runs_and_usage_share_the_cli_paused_row_with_overshoot(
    client: TestClient, svc: DashboardService, state_dir: Path
) -> None:
    """G-3 / T-8knqVe AC5: the paused list is the shared ``paused_row`` (overshoot included) and
    the usage windows come from ``build_usage_doc`` -- same numbers as the CLI document."""
    from agent_orchestrator.schedules.usage_view import build_usage_doc
    from agent_orchestrator.service.fire_store import ScheduleEventLog, readonly_fire_store
    from agent_orchestrator.service.usage_ledger import UsageLedger

    _paused(svc, state_dir, run_id="r1", spent=2.75)  # cap 2.0 -> overshoot 0.75
    (row,) = client.get(f"{URL}/a/paused-runs").json()["paused"]
    assert (row["spent_usd"], row["cap_usd"], row["overshoot_usd"]) == (2.75, 2.0, 0.75)
    fires = readonly_fire_store(state_dir)
    cli = build_usage_doc(
        workspace=svc.workspace_root, at=NOW, generated_at=NOW,
        bindings=[b for b in svc._load_schedule_file().bindings if b.id == "a"],
        ledger=UsageLedger(fires), fires=fires, events=ScheduleEventLog(state_dir),
    )["schedules"][0]  # fmt: skip
    assert cli["paused"][0]["overshoot_usd"] == 0.75
    (w,) = client.get(f"{URL}/a/usage").json()["windows"]
    (cw,) = cli["windows"]
    assert (w["window_key"], w["runs_used"], w["actual_usd"], w["reserved_usd"]) == (
        cw["key"], cw["runs"], cw["actual_usd"], cw["reserved_usd"],
    )  # fmt: skip


def test_resume_capped_queues_a_bounded_request(
    client: TestClient, svc: DashboardService, state_dir: Path
) -> None:
    _paused(svc, state_dir)
    r = client.post(f"{URL}/a/runs/r1/resume-capped", json={"add_usd": 1.5}, headers=ORIGIN)
    assert r.status_code == 202, r.text
    assert r.json()["queued"] is True and r.json()["add_usd"] == 1.5
    (req,) = _queued(state_dir)
    assert req["kind"] == "resume_capped" and req["run_id"] == "r1" and req["add_usd"] == 1.5
    assert req["requested_by"] == "ui"


def test_resume_capped_null_amount_uses_default(
    client: TestClient, svc: DashboardService, state_dir: Path
) -> None:
    _paused(svc, state_dir)
    r = client.post(f"{URL}/a/runs/r1/resume-capped", json={"add_usd": None}, headers=ORIGIN)
    assert r.status_code == 202 and r.json()["add_usd"] > 0
    assert client.post(f"{URL}/b/runs/r1/resume-capped", json={}, headers=ORIGIN).status_code == 404


@pytest.mark.parametrize(
    "body", [{"add_usd": 0}, {"add_usd": -1}, {"add_usd": "x"}, {"add_usd": 1e12},
             {"add_usd": 1, "workflow": "evil.json"}, {"argv": ["x"]}, {"cost_cap_usd": 99}],
)  # fmt: skip
def test_resume_capped_rejects_bad_bodies_422(
    client: TestClient, svc: DashboardService, state_dir: Path, body: dict[str, Any]
) -> None:
    _paused(svc, state_dir)
    r = client.post(f"{URL}/a/runs/r1/resume-capped", json=body, headers=ORIGIN)
    assert r.status_code == 422
    assert _queued(state_dir) == []


def test_resume_capped_rejects_nonfinite_422(
    client: TestClient, svc: DashboardService, state_dir: Path
) -> None:
    _paused(svc, state_dir)
    r = client.post(
        f"{URL}/a/runs/r1/resume-capped", content='{"add_usd": NaN}',
        headers={**ORIGIN, "Content-Type": "application/json"},
    )  # fmt: skip
    assert r.status_code == 422 and _queued(state_dir) == []


def test_resume_capped_over_per_resume_max_is_422_bad_amount(
    client: TestClient, svc: DashboardService, state_dir: Path
) -> None:
    """Schedule ``p`` has per_run_usd=1.0, so one resume may add at most $1.0 (H-2 / ADR-0025)."""
    _paused(svc, state_dir, sid="p")
    r = client.post(f"{URL}/p/runs/r1/resume-capped", json={"add_usd": 1.5}, headers=ORIGIN)
    assert r.status_code == 422 and r.json()["reason"] == "bad_amount"
    assert _queued(state_dir) == []
    ok = client.post(f"{URL}/p/runs/r1/resume-capped", json={"add_usd": 1.0}, headers=ORIGIN)
    assert ok.status_code == 202


def _record_bytes(state_dir: Path) -> dict[str, bytes]:
    """Every state-dir file except the queued request (the one allowed write)."""
    return {
        str(p.relative_to(state_dir)): p.read_bytes()
        for p in sorted(state_dir.rglob("*"))
        if p.is_file() and "requests" not in p.parts
    }


def test_resume_capped_request_never_writes_fire_records(
    client: TestClient, svc: DashboardService, state_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """G-1 / R-2c: the dashboard resume path is read-only on fire records (byte-equal before/after)
    and hands ``check_resume`` a read-only store for BOTH the fires and the ledger."""
    from agent_orchestrator.service.fire_store import ReadOnlyFireStore
    from agent_orchestrator.ui import service as ui_service

    _paused(svc, state_dir, spent=1.0)
    # G-15: the run has since spent 1.5 > cost_observed_usd + HWM_PERSIST_STEP_USD, so a WRITABLE
    # store would persist a raised mark -- the byte-equality assert below discriminates.
    _live_state(svc, "r1", 1.5)
    seen: dict[str, Any] = {}
    real = ui_service.request_capped_resume

    def spy(*args: Any, **kw: Any) -> Any:
        seen["fires"], seen["ledger_store"] = kw["fires"], kw["ledger"]._fires
        return real(*args, **kw)

    monkeypatch.setattr(ui_service, "request_capped_resume", spy)
    before = _record_bytes(state_dir)
    assert before, "fixture must have written a fire record"
    r = client.post(f"{URL}/a/runs/r1/resume-capped", json={"add_usd": 1.5}, headers=ORIGIN)
    assert r.status_code == 202, r.text
    assert isinstance(seen["fires"], ReadOnlyFireStore)
    assert isinstance(seen["ledger_store"], ReadOnlyFireStore)
    assert _record_bytes(state_dir) == before


def test_resume_capped_over_headroom_is_409(
    client: TestClient, svc: DashboardService, state_dir: Path
) -> None:
    """No per-run cap: the day's $5 headroom bounds it."""
    _paused(svc, state_dir)
    r = client.post(f"{URL}/a/runs/r1/resume-capped", json={"add_usd": 6.0}, headers=ORIGIN)
    assert r.status_code == 409 and r.json()["reason"] == "no_headroom"
    assert _queued(state_dir) == []


@pytest.mark.parametrize(
    ("cap_state", "reason"), [("resumed", "not_paused"), ("expired", "expired"),
                              ("aborted", "aborted"), ("none", "not_paused")],
)  # fmt: skip
def test_resume_capped_refusals_are_409_with_reason(
    client: TestClient, svc: DashboardService, state_dir: Path, cap_state: str, reason: str
) -> None:
    _paused(svc, state_dir, cap_state=cap_state)
    r = client.post(f"{URL}/a/runs/r1/resume-capped", json={"add_usd": 1}, headers=ORIGIN)
    assert r.status_code == 409 and r.json()["reason"] == reason and r.json()["detail"]
    assert _queued(state_dir) == []


def test_resume_capped_overdue_pause_is_409_expired(
    client: TestClient, svc: DashboardService, state_dir: Path
) -> None:
    _paused(svc, state_dir, expires=NOW - timedelta(seconds=1))
    r = client.post(f"{URL}/a/runs/r1/resume-capped", json={"add_usd": 1}, headers=ORIGIN)
    assert r.status_code == 409 and r.json()["reason"] == "expired"


def test_resume_capped_no_headroom_is_409_with_numbers(
    client: TestClient, svc: DashboardService, state_dir: Path
) -> None:
    """Day cap is $5: a prior $4.80 spend leaves $0.20, so +$1 is refused with the headroom."""
    _paused(svc, state_dir, run_id="r1", spent=4.8)
    r = client.post(f"{URL}/a/runs/r1/resume-capped", json={"add_usd": 1}, headers=ORIGIN)
    assert r.status_code == 409 and r.json()["reason"] == "no_headroom"
    assert r.json()["remaining_usd"] == pytest.approx(0.2, abs=0.05)


def test_resume_capped_unknown_schedule_or_run_404(
    client: TestClient, svc: DashboardService, state_dir: Path
) -> None:
    _paused(svc, state_dir)
    assert (
        client.post(f"{URL}/nope/runs/r1/resume-capped", json={}, headers=ORIGIN).status_code == 404
    )
    r = client.post(f"{URL}/a/runs/ghost/resume-capped", json={}, headers=ORIGIN)
    assert r.status_code == 404 and r.json()["reason"] == "schedule_not_found"


def test_resume_capped_cross_origin_is_403(svc: DashboardService, state_dir: Path) -> None:
    """The Origin gate (``ui/security.py``) covers the new POST like every mutating route."""
    from agent_orchestrator.ui.security import DEFAULT_ALLOWED_HOSTS

    _paused(svc, state_dir)
    with TestClient(
        create_app(svc, allowed_hosts=DEFAULT_ALLOWED_HOSTS), base_url="http://localhost"
    ) as c:
        r = c.post(
            f"{URL}/a/runs/r1/resume-capped", json={"add_usd": 1},
            headers={"origin": "http://evil.example"},
        )  # fmt: skip
        assert r.status_code == 403
        r = c.post(
            f"{URL}/a/runs/r1/resume-capped", content="{}",
            headers={"origin": "http://localhost", "content-type": "text/plain"},
        )  # fmt: skip
        assert r.status_code in (403, 415, 422)
    assert _queued(state_dir) == []
