"""T-Se4Bk5 probe p7 (w24-06 R-2 re-verify): `ao schedule resume` on the queue path still writes
fire records from a non-engine process (R-2b).

Run (it imports the capped-resume test rig relatively, so copy it into tests/service):
    cp output/E-Sc9Rt4-scheduler-triggers/se4bk5-probes/p7_resume_queue_r2.py \
       tests/service/test_zz_p7_probe.py && uv run pytest -q tests/service/test_zz_p7_probe.py -s; \
       rm tests/service/test_zz_p7_probe.py
Result on 1d5c6f9: 1 failed, 2 passed.
- test 1 PASSES: it asserts the bug, i.e. the CLI request rewrote sibling record k2.
- test 2 FAILS: the engine's `cap_state=aborted, final=True` is lost
  (k2 after race: none False 1.0).
- test 3 PASSES: the same request over readonly_fire_store writes nothing (the proposed fix).
After the fix (readonly_fire_store on the CLI request side), test 1 flips to fail and test 2 passes.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

from agent_orchestrator.service import capped_resume as cr
from agent_orchestrator.service.fire_store import (
    FireGovernance,
    FireIntentRecord,
    FireIntentStore,
    readonly_fire_store,
)
from agent_orchestrator.service.usage_ledger import UsageLedger, probe_run

from .test_capped_resume import NOW, PER_RUN, T0, Rig


def _sibling(rig: Rig) -> Path:
    g = FireGovernance(
        tz="UTC", week_start="monday", reserved_usd=PER_RUN, run_cost_cap_usd=PER_RUN
    )
    rec = FireIntentRecord(fire_key="k2", workspace_root=rig.root, schedule_id="s",
        scheduled_for=T0 + timedelta(minutes=1), created_at=T0, status="launched",
        run_id="r2", launch_id="l2", governance=g)  # fmt: skip
    rig.fires.write(rec)
    rig.write_state("r2", "running", 1.0)
    return rig.fires.directory(rig.root) / "k2.json"


def _cli_request(rig: Rig, store: FireIntentStore, loader: Any = probe_run) -> None:
    ledger = UsageLedger(
        store, clock=lambda: NOW, run_state_loader=loader, fs_now_ns=lambda: 10**19
    )
    cr.request_capped_resume(rig.root, "s", "r1", 1.0, "cli", state_dir=rig.tmp / "state",
        binding=rig.binding, fires=store, ledger=ledger, clock=lambda: NOW,
        new_id=lambda: "req-probe", drain=None)  # fmt: skip


def test_probe_resume_queue_cli_writes_sibling_fire_record(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.paused()
    path = _sibling(rig)
    before = path.read_bytes()
    _cli_request(rig, FireIntentStore(tmp_path / "state"))  # what resume_cmd builds today
    assert path.read_bytes() != before, "CLI resume request persisted a cost mark on k2"


def test_probe_resume_queue_cli_clobbers_concurrent_engine_cap_write(tmp_path: Path) -> None:
    """The real RMW window is inside update_governance (get -> persist). Land the engine's
    cap transition right after the CLI's get() of k2 and see whether it survives."""
    rig = Rig(tmp_path)
    rig.paused()
    _sibling(rig)
    engine_store = FireIntentStore(tmp_path / "state")

    class CliStore(FireIntentStore):
        armed = False

        def update_governance(self, fire_key: str, **fields: Any) -> FireIntentRecord:
            CliStore.armed = fire_key == "k2"
            return super().update_governance(fire_key, **fields)

        def get(self, *a: Any, **k: Any) -> Any:
            rec = super().get(*a, **k)
            if CliStore.armed:
                CliStore.armed = False
                engine_store.update_governance("k2", cap_state="aborted", final=True)
            return rec

    _cli_request(rig, CliStore(tmp_path / "state"))
    g = FireIntentStore(tmp_path / "state").get("k2", rig.root).governance
    assert g is not None
    print("k2 after race:", g.cap_state, g.final, g.cost_observed_usd)
    assert g.cap_state == "aborted", f"engine cap transition lost: cap_state={g.cap_state}"


def test_probe_resume_queue_with_readonly_store_writes_nothing(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.paused()
    path = _sibling(rig)
    before = path.read_bytes()
    _cli_request(rig, readonly_fire_store(tmp_path / "state"))
    assert path.read_bytes() == before
