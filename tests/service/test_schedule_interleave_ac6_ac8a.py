"""T-Te3Qw8 AC6 + AC8a: Supervisor/engine interleaving and the design-review regressions F2/F7/F9.

Fixed clocks everywhere (engine clock + monotonic stubs, ``_FakeMonotonic`` for the supervisor);
the only real processes are trivial ``sh``/``python -c`` children. Reuses the ``Env`` harness of
``test_schedule_engine`` (read-only import) rather than re-inventing it.
"""

from __future__ import annotations

import os
import signal
import sys
from datetime import timedelta
from pathlib import Path

import pytest

from agent_orchestrator.service import schedule_engine as se
from agent_orchestrator.service.cli import monitor_tick
from agent_orchestrator.service.fire_store import FireIntentRecord, fire_key
from agent_orchestrator.service.registry import ServiceRegistry, ServiceRegistryFile, WorkspaceEntry
from agent_orchestrator.service.schedule_engine import EngineLockHeldError, probe_engine_lock
from agent_orchestrator.service.supervisor import BACKOFF_BASE_SECONDS, Supervisor
from agent_orchestrator.ui.processes import ProcessSupervisor

from .test_schedule_engine import (
    DAY,
    NOW1,
    T0,
    Env,
    FakeRepo,
    binding,
    env_for,
)
from .test_supervisor import (
    _FakeMonotonic,
    _free_port,
    _no_sleep,
    _sleep_child_spawner,
    _wait_until,
)


def _supervisor(tmp_path: Path, clock: _FakeMonotonic) -> Supervisor:
    ws = tmp_path / "svc-ws"
    ws.mkdir()
    reg_path = tmp_path / "svc-registry.yaml"
    data = ServiceRegistryFile(
        workspaces=[WorkspaceEntry(root=str(ws.resolve()), port=_free_port())]
    )
    ServiceRegistry(reg_path).save(data)
    return Supervisor(
        registry=data,
        state_dir=tmp_path / "svc-state",
        hub_port=8770,
        registry_path=reg_path,
        child_spawner=_sleep_child_spawner(),
        monotonic=clock,
        sleeper=_no_sleep,
    )


# -- AC6 -----------------------------------------------------------------------------------------


def test_ac6_child_killed_mid_evaluation_is_detected_and_restarted(tmp_path: Path) -> None:
    """The kill lands INSIDE the engine's launch (i.e. mid schedule evaluation); the very next
    ``Supervisor.tick()`` of the alternating loop still notices and, after backoff, restarts it."""
    env = env_for(tmp_path / "eng")
    env.anchor()
    clock = _FakeMonotonic(start=0.0)
    sup = _supervisor(tmp_path, clock)
    sup.start()
    try:
        child = next(iter(sup._children.values()))
        assert child.popen is not None
        original_pid = child.popen.pid
        real_launch = env.launcher.launch

        def launch_and_kill(d: se.FireDecision) -> FireIntentRecord:
            os.kill(original_pid, signal.SIGKILL)
            assert child.popen is not None
            assert _wait_until(lambda: child.popen.poll() is not None)  # type: ignore[union-attr]
            return real_launch(d)

        env.launcher.launch = launch_and_kill  # type: ignore[method-assign]

        # alternating loop, one iteration: supervisor first (child alive), then the engine fires
        sup.tick()
        assert child.popen is not None and child.restart_count == 0
        assert len(env.tick(NOW1)) == 1  # engine completed its fire despite the dead child

        sup.tick()  # next alternation: the kill is detected, restart scheduled behind backoff
        assert child.popen is None and child.restart_count == 1
        assert child.next_retry_at == pytest.approx(BACKOFF_BASE_SECONDS)

        env.launcher.launch = real_launch  # type: ignore[method-assign]
        env.end_run("run-1")
        clock.advance(BACKOFF_BASE_SECONDS)
        env.tick(NOW1 + timedelta(seconds=60))  # engine still healthy between the two ticks
        sup.tick()
        assert child.popen is not None and child.popen.pid != original_pid
        assert sorted(str(r.run_id) for r in env.engine.fire_store.list()) == ["run-1", "run-2"]
    finally:
        sup.shutdown(grace_seconds=2.0)


def test_ac6_monitor_tick_alternation_survives_engine_budget_overrun(tmp_path: Path) -> None:
    """Drives the daemon's own ``monitor_tick`` (supervisor then engine). A monotonic jump forces
    the engine over its tick budget: the unreached schedule ``b`` is left untouched, the supervisor
    keeps restarting its killed child, and ``b`` is picked up on the next alternation."""
    env = env_for(tmp_path / "eng", [binding("a"), binding("b")])
    env.anchor("a")  # b has no state at all
    clock = _FakeMonotonic(start=0.0)
    sup = _supervisor(tmp_path, clock)
    sup.start()
    try:
        child = next(iter(sup._children.values()))
        assert child.popen is not None
        pid = child.popen.pid
        os.kill(pid, signal.SIGKILL)
        assert _wait_until(lambda: child.popen.poll() is not None)  # type: ignore[union-attr]

        env.clock.now = NOW1
        env.mono.t += 20
        env.mono.jump_at_call = env.mono.calls + 3  # tick start, a's check, b's check -> overrun
        monitor_tick(sup, env.engine)
        assert child.popen is None and child.restart_count == 1  # supervisor half ran first
        assert [d.schedule_id for d in env.engine.fire_store.list()] == ["a"]
        over = env.events("schedule.tick_budget_exceeded")
        assert len(over) == 1 and over[0]["schedule_id"] == "b"
        assert env.engine.state_store.get(env.root, "b") is None  # left unchanged

        env.mono.jump_at_call = None
        env.end_run("run-1")
        env.anchor("b")
        env.clock.now = NOW1 + timedelta(seconds=60)
        env.mono.t += 20
        clock.advance(BACKOFF_BASE_SECONDS)
        monitor_tick(sup, env.engine)
        assert child.popen is not None and child.popen.pid != pid
        assert "b" in {r.schedule_id for r in env.engine.fire_store.list()}
    finally:
        sup.shutdown(grace_seconds=2.0)


# -- AC8a ----------------------------------------------------------------------------------------


def test_f2_child_launched_by_tick_is_reaped_next_tick_and_key_retired(tmp_path: Path) -> None:
    """Through the engine's OWN launch path (tick -> launcher -> shared ProcessSupervisor), not a
    hand-written fire record: once the child exits the next tick reaps it (no zombie left for
    ``os.waitpid``) and the fire key is retired, so the zombie does not hold 'active'."""
    holder: dict[str, FireIntentRecord] = {}
    env: Env

    class ProcessLauncher:
        def launch(self, d: se.FireDecision) -> FireIntentRecord:
            assert d.fire_key and d.scheduled_for
            sup = env.engine.process_supervisor(d.workspace_root)
            launched = sup.launch_run(workflow_path="w.json", run_id="ao-s-20260101T000100Z")
            env.engine.fire_store.write(
                FireIntentRecord(
                    fire_key=d.fire_key, workspace_root=d.workspace_root, schedule_id=d.schedule_id,
                    scheduled_for=d.scheduled_for, created_at=NOW1, status="launched",
                    run_id=launched.run_id,
                )
            )  # fmt: skip
            env.engine.state_store.record_fire(
                d.workspace_root, d.schedule_id, scheduled_for=d.scheduled_for,
                run_id=launched.run_id or "", fire_at=NOW1, fire_key=d.fire_key,
            )  # fmt: skip
            holder["pid"] = launched  # type: ignore[assignment]
            return env.engine.fire_store.get(d.fire_key)  # type: ignore[return-value]

    def factory(root: str) -> ProcessSupervisor:
        return ProcessSupervisor(
            root,
            ao_command=[sys.executable, "-c", "import sys; sys.exit(0)"],
            run_id_discovery_timeout=0.0,
        )

    env = env_for(
        tmp_path,
        process_supervisor_factory=factory,
        run_repository_factory=lambda r: FakeRepo({}),
        launcher=ProcessLauncher(),
    )
    env.anchor()
    assert len(env.tick(NOW1)) == 1
    launched = holder["pid"]
    pid = launched.pid  # type: ignore[attr-defined]
    key = fire_key(env.root, "s", NOW1)
    # wait for exit WITHOUT reaping, so the child is a genuine zombie until the engine reaps it
    assert _wait_until(
        lambda: os.waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None,
        timeout=10,
    )
    # an off-schedule tick reaps (reconcile runs on every call); the next DUE tick then evaluates
    # overlap=skip: a zombie still counted as active would burn it as overlap_skip instead of firing
    env.tick(NOW1 + timedelta(seconds=20))
    sup = env.engine.process_supervisor(env.root)
    assert pid not in sup._children  # type: ignore[attr-defined]
    with pytest.raises(ChildProcessError):
        os.waitpid(pid, os.WNOHANG)
    second = env.tick(NOW1 + timedelta(seconds=60))
    assert len(second) == 1 and second[0].reason == "due"
    st = env.engine.state_store.get(env.root, "s")
    assert st is not None and key not in st.active_fire_keys
    assert not env.events("schedule.skipped_overlap")


def test_f7_jitter_fire_record_goes_intended_to_launched(tmp_path: Path) -> None:
    """``jitter=300, grace=60, catch_up=skip``: the jittered fire is not 'late', fires at its
    effective_due, and its record is written ``intended`` then updated to ``launched`` exactly once
    (the launcher observes no pre-existing record for the key, i.e. no F8-style collision)."""
    for i in range(50):
        sid = f"s{i}"
        env = Env(
            tmp_path / sid,
            [
                binding(
                    sid, interval_seconds=DAY, jitter_seconds=300, catch_up="skip", grace_seconds=60
                )
            ],
        )
        env.anchor(sid)
        sf = T0 + timedelta(seconds=DAY)
        off = se._jitter_offset(fire_key(env.root, sid, sf), env.binding(sid))
        if off > 60:
            break
    else:
        pytest.fail("no id with jitter offset > grace")
    key = fire_key(env.root, sid, sf)
    seen_before: list[bool] = []
    statuses: list[str] = []
    real_write = env.engine.fire_store.write

    def write(rec: FireIntentRecord) -> None:
        statuses.append(rec.status)
        real_write(rec)

    real_update = env.engine.fire_store.update

    def update(k: str, **kw: object) -> FireIntentRecord:
        out = real_update(k, **kw)
        statuses.append(out.status)
        return out

    env.engine.fire_store.write = write
    env.engine.fire_store.update = update
    real_launch = env.launcher.launch

    def launch(d: se.FireDecision) -> FireIntentRecord:
        seen_before.append(env.engine.fire_store.exists(key, env.root))
        return real_launch(d)

    env.launcher.launch = launch  # type: ignore[method-assign]

    assert env.tick(sf + timedelta(seconds=off - 1)) == []  # jitter_pending, nothing yet
    assert env.tick(sf + timedelta(seconds=off)) != []
    assert seen_before == [False]
    assert statuses == ["intended", "launched"]
    rec = env.engine.fire_store.get(key)
    assert rec is not None and rec.status == "launched" and rec.run_id == "run-1"
    assert not env.events("schedule.catch_up")


def test_f9_second_engine_refuses_to_start(tmp_path: Path) -> None:
    """A second engine on the same state dir refuses to start; the engine lock is the single
    arbiter (probe agrees), and it is released again on shutdown."""
    env = env_for(tmp_path)
    state = tmp_path / "state"
    env.engine.start()
    try:
        assert probe_engine_lock(state)
        other = env.make_engine()
        with pytest.raises(EngineLockHeldError):
            other.start()
        # a refused engine must not have stolen or released the first one's lock
        assert probe_engine_lock(state)
    finally:
        env.engine.shutdown()
    assert not probe_engine_lock(state)
    again = env.make_engine()
    again.start()
    again.shutdown()
