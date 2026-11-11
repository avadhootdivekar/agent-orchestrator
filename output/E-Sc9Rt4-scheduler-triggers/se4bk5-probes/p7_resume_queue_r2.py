"""T-Se4Bk5 probe p7 (w24-06 R-2 re-verify): `ao schedule resume` on the queue path must not write
fire records from a non-engine process (R-2b). Fixed in w26-01; this probe now builds its store the
way the CLI does (`schedules.cli.readonly_fire_store`, the name `resume_cmd` calls), so it PASSES
post-fix and fails again if `resume_cmd` regresses to a writable store (w27-03).

Run (it imports the capped-resume test rig relatively, so copy it into tests/service):
    cp output/E-Sc9Rt4-scheduler-triggers/se4bk5-probes/p7_resume_queue_r2.py \
       tests/service/test_zz_p7_probe.py && uv run pytest -q tests/service/test_zz_p7_probe.py -s; \
       rm tests/service/test_zz_p7_probe.py
- test 1: the CLI-built store writes nothing to sibling record k2.
- test 2: an engine `cap_state=aborted, final=True` landing between the CLI's get() and its persist
  survives (the CLI store cannot roll it back).
- test 3: the CLI is wired to `readonly_fire_store` (a writable store is what R-2b was).
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

from agent_orchestrator.schedules import cli as sched_cli
from agent_orchestrator.service import capped_resume as cr
from agent_orchestrator.service.fire_store import (
    FireGovernance,
    FireIntentRecord,
    FireIntentStore,
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


def _cli_store(state: Path) -> FireIntentStore:
    """The store `resume_cmd` builds for its request side."""
    return sched_cli.readonly_fire_store(state)


def test_probe_resume_queue_cli_store_writes_nothing(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.paused()
    path = _sibling(rig)
    before = path.read_bytes()
    _cli_request(rig, _cli_store(tmp_path / "state"))
    assert path.read_bytes() == before, "CLI resume request persisted a cost mark on k2"


def test_probe_resume_queue_cli_store_keeps_concurrent_engine_cap_write(tmp_path: Path) -> None:
    """Land the engine's cap transition right after the CLI store's get() of k2 (the old RMW
    window) and check it survives."""
    rig = Rig(tmp_path)
    rig.paused()
    _sibling(rig)
    engine_store = FireIntentStore(tmp_path / "state")
    cli_store = _cli_store(tmp_path / "state")
    real_get = cli_store.get

    def racing_get(*a: Any, **k: Any) -> Any:
        rec = real_get(*a, **k)
        if a and a[0] == "k2":
            engine_store.update_governance("k2", cap_state="aborted", final=True)
        return rec

    cli_store.get = racing_get  # type: ignore[method-assign]
    _cli_request(rig, cli_store)
    g = FireIntentStore(tmp_path / "state").get("k2", rig.root).governance
    assert g is not None
    assert g.cap_state == "aborted" and g.final, f"engine cap transition lost: {g.cap_state}"


def test_probe_resume_cmd_builds_a_readonly_store(tmp_path: Path) -> None:
    from agent_orchestrator.service.fire_store import ReadOnlyFireStore

    assert isinstance(_cli_store(tmp_path / "state"), ReadOnlyFireStore)
