"""T-Sn5Ui1 AC3 (automated): dashboard control API -> live engine tick -> dispatch follows.

A real ``Orchestrator`` runs on a temp workspace (fake executor, 1 s tick); from inside a running
task the dashboard's ``TestClient`` POSTs ``set max_parallel=3`` and a ``pause_llm``, polls the
verdict endpoint, and the engine's dispatch concurrency follows. Never touches a live workspace.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("fastapi", reason="dashboard API needs the optional [ui] extra")
from fastapi.testclient import TestClient  # noqa: E402

from agent_orchestrator import engine as engine_mod  # noqa: E402
from agent_orchestrator.engine import Orchestrator  # noqa: E402
from agent_orchestrator.models import SentinelSpec, TaskContext  # noqa: E402
from agent_orchestrator.project_config import ProjectConfig  # noqa: E402
from agent_orchestrator.sentinel_settings import resolve_sentinel_settings  # noqa: E402
from agent_orchestrator.ui.app import API_PREFIX, create_app  # noqa: E402
from agent_orchestrator.ui.service import DashboardService  # noqa: E402

from ..test_sentinel_tick import ScriptedExecutor, _setup, _wf  # noqa: E402
from .conftest import StubSupervisor  # noqa: E402

WAIT_S = 20.0


@pytest.fixture(autouse=True)
def _fast_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    real = engine_mod.wait

    def fast(fs: Any, timeout: float | None = None, return_when: Any = None) -> Any:
        return real(fs, timeout=0.02 if timeout is not None else None, return_when=return_when)

    monkeypatch.setattr(engine_mod, "wait", fast)


def _verdict(client: TestClient, run_id: str, entry_id: str) -> dict:
    deadline = time.monotonic() + WAIT_S
    while time.monotonic() < deadline:
        got = client.get(f"{API_PREFIX}/runs/{run_id}/control/{entry_id}").json()
        if got["outcome"] != "pending":
            return got
        time.sleep(0.05)
    return {"outcome": "timeout"}


def test_dashboard_set_max_parallel_pause_and_status_follow_live_engine(
    tmp_path: Path, stub_supervisor: StubSupervisor
) -> None:
    store, rs, reposets, agents = _setup(tmp_path)
    state0 = rs.new_run(_wf(3))
    run_id = state0.run_id
    svc = DashboardService(
        str(tmp_path),
        supervisor=stub_supervisor,  # type: ignore[arg-type]
        project_config=ProjectConfig(),
    )
    out: dict[str, Any] = {}
    all3 = threading.Event()

    with TestClient(create_app(svc)) as client:

        def first(ctx: TaskContext) -> None:
            r = client.post(
                f"{API_PREFIX}/runs/{run_id}/control",
                json={"kind": "set", "payload": {"values": {"max_parallel": 3}}},
            )
            out["post"] = r.status_code
            out["set"] = _verdict(client, run_id, r.json()["id"])
            rb = client.post(
                f"{API_PREFIX}/runs/{run_id}/control",
                json={"kind": "set", "payload": {"values": {"max_parallel": 9}}},
            )
            out["above_ceiling_http"] = rb.status_code  # engine-side ceiling only: queued ...
            out["above_ceiling"] = _verdict(client, run_id, rb.json()["id"])  # ... then quarantined
            rp = client.post(f"{API_PREFIX}/runs/{run_id}/control", json={"kind": "pause_llm"})
            out["pause"] = _verdict(client, run_id, rp.json()["id"])
            out["status"] = client.get(f"{API_PREFIX}/runs/{run_id}/sentinel").json()
            assert all3.wait(WAIT_S)

        def others(ctx: TaskContext) -> None:
            if ex.running == 3:
                all3.set()
            assert all3.wait(WAIT_S)

        ex = ScriptedExecutor({"t0": first, "t1": others, "t2": others})
        sentinel = resolve_sentinel_settings(
            workflow=SentinelSpec(enabled=True, tick_seconds=1, max_parallel_ceiling=3)
        )
        final = Orchestrator(ex, store, rs, max_parallel=1, sentinel=sentinel).run(
            _wf(3), reposets, agents, run_state=state0
        )

    assert final.status == "succeeded"
    assert ex.max_running == 3  # dispatch followed the live override
    assert out["post"] == 202 and out["set"] == {"outcome": "consumed"}
    assert out["above_ceiling"]["outcome"] == "rejected"
    assert out["above_ceiling"]["rule_id"] == "CTL-CEILING"
    assert out["pause"] == {"outcome": "consumed"}
    status = out["status"]
    assert status["concurrency"]["limit"] == 3
    assert status["concurrency"]["overrides"]["max_parallel"]["actor"] == "dashboard:local"
    assert status["llm"]["paused"] is True
    assert final.sentinel is not None and final.sentinel.llm_paused is True
