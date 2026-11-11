"""T-Se4Bk5 R-2b / TQ-R2: the CLI ``resume --queue`` path never writes fire records.

The engine is the single writer of fire records (ADR-0025). ``update_governance`` is an unlocked
read-modify-write, so a non-engine process persisting a ledger cost mark between its ``get`` and
its persist can roll back an engine ``cap_state`` transition. Fixed clock, temp state dir, no
sleeps.
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
    ReadOnlyFireStore,
    readonly_fire_store,
)
from agent_orchestrator.service.usage_ledger import UsageLedger, probe_run

from .test_capped_resume import NOW, PER_RUN, T0, Rig


def _sibling(rig: Rig) -> Path:
    """A launched sibling fire k2 whose observed cost (1.0) is above its stored mark (0.0): any
    writable ledger read persists a mark into it."""
    g = FireGovernance(
        tz="UTC", week_start="monday", reserved_usd=PER_RUN, run_cost_cap_usd=PER_RUN
    )
    rec = FireIntentRecord(fire_key="k2", workspace_root=rig.root, schedule_id="s",
        scheduled_for=T0 + timedelta(minutes=1), created_at=T0, status="launched",
        run_id="r2", launch_id="l2", governance=g)  # fmt: skip
    rig.fires.write(rec)
    rig.write_state("r2", "running", 1.0)
    return rig.fires.directory(rig.root) / "k2.json"


def _request(rig: Rig, store: FireIntentStore) -> None:
    ledger = UsageLedger(
        store, clock=lambda: NOW, run_state_loader=probe_run, fs_now_ns=lambda: 10**19
    )
    cr.request_capped_resume(rig.root, "s", "r1", 1.0, "cli", state_dir=rig.tmp / "state",
        binding=rig.binding, fires=store, ledger=ledger, clock=lambda: NOW,
        new_id=lambda: "req-1", drain=None)  # fmt: skip


def test_queue_request_over_readonly_store_writes_no_fire_record(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.paused()
    k2 = _sibling(rig)
    before = {p: p.read_bytes() for p in k2.parent.glob("*.json")}
    _request(rig, readonly_fire_store(tmp_path / "state"))
    assert {p: p.read_bytes() for p in k2.parent.glob("*.json")} == before
    assert list((tmp_path / "state").rglob("req-1.json"))  # the request file itself was dropped


def test_writable_store_would_persist_a_mark_proving_the_guard_matters(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.paused()
    k2 = _sibling(rig)
    before = k2.read_bytes()
    _request(rig, FireIntentStore(tmp_path / "state"))
    assert k2.read_bytes() != before


def test_readonly_store_does_not_roll_back_engine_write_inside_update_governance(
    tmp_path: Path,
) -> None:
    """TQ-R2: the engine's cap transition lands between ``get`` and the would-be persist."""
    rig = Rig(tmp_path)
    rig.paused()
    _sibling(rig)
    engine_store = FireIntentStore(tmp_path / "state")

    class Racing(ReadOnlyFireStore):
        armed = False

        def update_governance(self, fire_key: str, **fields: Any) -> FireIntentRecord:
            self.armed = fire_key == "k2"
            return super().update_governance(fire_key, **fields)

        def get(self, *a: Any, **k: Any) -> Any:
            rec = super().get(*a, **k)
            if self.armed:  # right after the view's read, before any write-back
                self.armed = False
                engine_store.update_governance("k2", cap_state="aborted", final=True)
            return rec

    _request(rig, Racing(tmp_path / "state"))
    g = FireIntentStore(tmp_path / "state").get("k2", rig.root).governance  # type: ignore[union-attr]
    assert g is not None and g.cap_state == "aborted" and g.final is True


def test_duplicate_queued_resume_is_idempotent(tmp_path: Path) -> None:
    """w30-03: a second ``resume --queue`` of the same capped run must not drop a second file."""
    rig = Rig(tmp_path)
    rig.paused()
    store = readonly_fire_store(tmp_path / "state")
    ledger = UsageLedger(
        store, clock=lambda: NOW, run_state_loader=probe_run, fs_now_ns=lambda: 10**19
    )
    ids = iter(["req-1", "req-2"])

    def ask() -> cr.ResumeCappedRequest:
        return cr.request_capped_resume(rig.root, "s", "r1", 1.0, "cli",
            state_dir=rig.tmp / "state",
            binding=rig.binding, fires=store, ledger=ledger, clock=lambda: NOW,
            new_id=lambda: next(ids), drain=None)  # fmt: skip

    first, second = ask(), ask()
    files = list(cr.requests_dir(tmp_path / "state").glob("*.json"))
    assert len(files) == 1
    assert second.request_id == first.request_id
    assert (
        cr.find_pending_request(cr.requests_dir(tmp_path / "state"), rig.root, "r1", NOW)
        is not None
    )


def test_readonly_store_refuses_every_mutator_and_reads_still_work(tmp_path: Path) -> None:
    """T-Qp4Zm8 REVIEW G-4: every mutator raises and nothing lands on disk; reads are unaffected."""
    import pytest

    state = tmp_path / "state"
    root = str(tmp_path / "ws")
    rec = FireIntentRecord(fire_key="k1", workspace_root=root, schedule_id="s",
        scheduled_for=T0, created_at=T0, status="launched", run_id="r1")  # fmt: skip
    FireIntentStore(state).write(rec)
    ro = readonly_fire_store(state)
    path = ro.directory(root) / "k1.json"
    before = {p: p.read_bytes() for p in path.parent.glob("*.json")}

    new = rec.model_copy(update={"fire_key": "k2"})
    calls: list[Any] = [
        lambda: ro.write(new),
        lambda: ro.write_suppressed(
            "k3", "why", workspace_root=root, schedule_id="s", scheduled_for=T0
        ),
        lambda: ro.update("k1", status="failed"),
        lambda: ro._persist(rec),
        lambda: ro._delete(rec),
        lambda: ro.prune(max_age_days=0, max_records=0),
        lambda: ro.reconcile_orphans(lambda _p: False, lambda _r, _i: False),
    ]  # fmt: skip
    for call in calls:
        with pytest.raises(PermissionError, match="ReadOnlyFireStore refuses"):
            call()
    assert {p: p.read_bytes() for p in path.parent.glob("*.json")} == before
    # reads, and the in-memory-only governance merge, still work
    assert (
        ro.get("k1") == rec and ro.exists("k1", root) and [r.fire_key for r in ro.list()] == ["k1"]
    )
    assert ro.update_governance("k1", cost_observed_usd=2.0).governance is not None
    assert {p: p.read_bytes() for p in path.parent.glob("*.json")} == before
