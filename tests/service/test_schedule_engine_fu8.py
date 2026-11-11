"""T-Se4Bk5 TEST-QUALITY FU-8: engine first-sighting anchor, capped-resume crash window, and the
engine drain wired to the real ``ScheduledLauncher.launch_capped_resume``.

Fixed clocks, temp workspaces, no process spawned.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from agent_orchestrator.service import capped_resume as cr
from agent_orchestrator.service.fire_store import FireIntentRecord

from .test_capped_resume import NOW, PER_RUN, Rig, RunRepo, _week
from .test_schedule_engine import T0, Env, binding

# -- first sighting ---------------------------------------------------------------------------


def test_first_sighting_pins_anchor_survives_restart_and_fires_exactly_once(tmp_path: Path) -> None:
    env = Env(tmp_path, [binding(interval_seconds=60)])
    assert env.engine.state_store.get(env.root, "s") is None  # never seen: no anchor
    assert env.tick(T0) == []  # first sighting never fires, even though "now" is a valid instant
    st = env.engine.state_store.get(env.root, "s")
    assert st is not None and st.last_scheduled_for == T0  # anchor pinned at first sight

    # Restart: a fresh engine over the same state dir must keep the ORIGINAL anchor. If it
    # re-pinned "now" each time, a daemon restarting every <period> would never fire.
    env.engine = env.make_engine()
    assert env.tick(T0 + timedelta(seconds=30)) == []
    st = env.engine.state_store.get(env.root, "s")
    assert st is not None and st.last_scheduled_for == T0

    [d] = env.tick(T0 + timedelta(seconds=61))
    assert d.fire and d.scheduled_for == T0 + timedelta(seconds=60)
    env.end_run("run-1")
    # the same instant is never fired twice, even across another restart
    env.engine = env.make_engine()
    assert env.tick(T0 + timedelta(seconds=62)) == []
    assert len(env.launcher.decisions) == 1


# -- capped-resume crash window -----------------------------------------------------------------


class _Crash(BaseException):
    """Stands in for the daemon dying mid-resume (not caught by ``except Exception``)."""


def _crash_after_governance_write(rig: Rig) -> None:
    def boom(rec: FireIntentRecord, new_cap_usd: float) -> Any:
        raise _Crash

    rig.launcher.launch_capped_resume = boom  # type: ignore[method-assign]
    rig.paused()
    rig.request(1.5)
    with pytest.raises(_Crash):
        rig.engine.drain_resume_requests()


def test_crash_between_governance_write_and_spawn_leaves_a_durable_resumed_record(
    tmp_path: Path,
) -> None:
    rig = Rig(tmp_path)
    _crash_after_governance_write(rig)
    g = rig.gov()
    # What is durable after the crash: the record already claims "resumed" with the new cap, one
    # audit entry, and the request file is gone (delete-first => at-most-once).
    assert (g.cap_state, g.run_cost_cap_usd, g.final) == ("resumed", PER_RUN + 1.5, False)
    assert [r["request_id"] for r in g.resumes] == ["req1"]
    assert list((rig.tmp / "state" / "schedules" / "requests").glob("*.json")) == []
    assert rig.launcher.calls == []  # nothing was spawned
    st = rig.engine.state_store.get(rig.root, "s")
    assert st is None or "k1" not in st.active_fire_keys


def test_after_the_crash_a_restart_never_spawns_a_second_child_or_double_charges(
    tmp_path: Path,
) -> None:
    rig = Rig(tmp_path)
    _crash_after_governance_write(rig)
    held_before = rig.ledger.usage(rig.root, "s", _week())["week"]

    rig.env.engine = rig.engine = rig.env.make_engine(  # "restart": fresh engine, same state
        launcher=rig.launcher,
        run_repository_factory=lambda root: RunRepo(root),
    )
    rig.env.tick(NOW + timedelta(minutes=5))
    rig.env.tick(NOW + timedelta(minutes=10))
    assert rig.launcher.calls == []  # the sweep must not guess-respawn the child
    g = rig.gov()
    assert g.cap_state == "resumed" and len(g.resumes) == 1  # no duplicate resume entry
    # and the window is not charged twice for the same run
    assert rig.ledger.usage(rig.root, "s", _week())["week"].runs == held_before.runs


def test_stranded_resumed_record_is_held_at_its_new_cap_and_cannot_be_resumed_again(
    tmp_path: Path,
) -> None:
    """PINNED CURRENT BEHAVIOUR (not endorsed): after the crash nothing finalises the record or
    re-offers the resume -- ``_sweep_unfinalized`` only looks at ``cap_state == "none"`` -- so it
    keeps holding the new cap in the window until the week rolls. Recorded as a follow-up."""
    rig = Rig(tmp_path)
    _crash_after_governance_write(rig)
    rig.env.tick(NOW + timedelta(minutes=5))
    assert rig.gov().cap_state == "resumed" and rig.gov().final is False
    with pytest.raises(Exception, match="cap_state|resumed|not paused"):
        rig.request(1.0)


# -- engine drain wired to the real ScheduledLauncher --------------------------------------------


def test_engine_drain_wired_to_the_real_launcher_resumes_the_same_instance(tmp_path: Path) -> None:
    """One path end to end: request file -> ``ScheduleEngine.tick`` -> ``drain_resume_capped`` ->
    the real ``ScheduledLauncher.launch_capped_resume`` (stub supervisor) -> record + active key."""
    from .test_capped_resume import _capped_launch_rig, limits

    lrig, d = _capped_launch_rig(tmp_path)
    rec = lrig.launcher.launch(d)
    assert rec.run_id is not None and rec.governance is not None
    first_launches = len(lrig.sup.launch_calls)
    # the run hit its cap: the retire hook's outcome, set directly
    lrig.launcher._fires.update_governance(  # noqa: SLF001
        rec.fire_key, cap_state="paused", paused_at=T0, pause_expires_at=T0 + timedelta(hours=1)
    )
    env = Env(
        tmp_path,
        [binding("nightly", interval_seconds=86400, limits=limits(per_run=2))],
        launcher=lrig.launcher,
    )
    env.clock.now = T0 + timedelta(minutes=1)
    req_dir = tmp_path / "state" / "schedules" / "requests"
    cr.request_capped_resume(
        env.root, "nightly", rec.run_id, 1.5, "cli",
        state_dir=tmp_path / "state", binding=env.binding("nightly"),
        fires=env.engine.fire_store, ledger=env.engine.usage_ledger,
        clock=lambda: env.clock.now, new_id=lambda: "req-w", drain=None,
    )  # fmt: skip
    assert len(list(req_dir.glob("*.json"))) == 1
    env.tick(env.clock.now)  # nothing is due (daily interval); the drain is the only action
    assert env.launcher.decisions == []
    assert list(req_dir.glob("*.json")) == []
    assert len(lrig.sup.launch_calls) == first_launches + 1
    call = lrig.sup.launch_calls[-1]
    assert call["run_id"] == rec.run_id and call["options"] == {"cost_cap_usd": 1.5}
    got = lrig.launcher._fires.get(rec.fire_key, env.root)  # noqa: SLF001
    assert got is not None and got.governance is not None
    assert (got.governance.cap_state, got.governance.run_cost_cap_usd) == ("resumed", 1.5)
    st = env.engine.state_store.get(env.root, d.schedule_id)
    assert st is not None and rec.fire_key in st.active_fire_keys
    assert [e["run_id"] for e in env.events("schedule.governance.resumed")] == [rec.run_id]
