"""T-Ev3Qm5: ScheduleEngine decision policy. Fixed clocks, recording-stub launcher, no subprocess
(except the single F2 zombie-reaping test, which spawns a trivial child)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from agent_orchestrator.schedules import ScheduleFile, ScheduleStore
from agent_orchestrator.schedules.models import ScheduleBinding, WatchSpec
from agent_orchestrator.service import schedule_engine as se
from agent_orchestrator.service.fire_store import FireIntentRecord, fire_key
from agent_orchestrator.service.schedule_engine import (
    Candidate,
    EngineLockHeldError,
    FireDecision,
    FireReason,
    ScheduleEngine,
    probe_engine_lock,
)
from agent_orchestrator.ui.processes import ProcessSupervisor
from agent_orchestrator.ui.runs import RunNotFoundError

from ..schedules._fixtures import minimal_limits

T0 = datetime(2026, 1, 1, tzinfo=UTC)
DAY = 86400


class Clock:
    def __init__(self, now: datetime = T0) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class Mono:
    """Monotonic stub; ``jump_at_call`` makes the Nth call leap past the tick budget."""

    def __init__(self) -> None:
        self.t = 1000.0
        self.calls = 0
        self.jump_at_call: int | None = None

    def __call__(self) -> float:
        self.calls += 1
        if self.jump_at_call is not None and self.calls == self.jump_at_call:
            self.t += 10
        return self.t


class FakeRegistry:
    roots: list[str] = []

    def __init__(self, path: Path) -> None:
        pass

    def load(self) -> Any:
        return SimpleNamespace(
            workspaces=[SimpleNamespace(root=r, schedules=True) for r in FakeRegistry.roots]
        )


class FakeSup:
    def __init__(self, alive: set[str]) -> None:
        self.alive = alive
        self.reconciled = 0

    def is_running(self, run_id: str) -> bool:
        return run_id in self.alive

    def reconcile(self) -> list[Any]:
        self.reconciled += 1
        return []


class FakeRepo:
    def __init__(self, statuses: dict[str, str]) -> None:
        self.statuses = statuses

    def load_state(self, run_id: str) -> Any:
        if run_id not in self.statuses:
            raise RunNotFoundError(run_id)
        return SimpleNamespace(status=self.statuses[run_id])

    def exists(self, run_id: str) -> bool:
        return run_id in self.statuses


class RecordingLauncher:
    """Mimics T-Lp4Wt6's contract: intended -> launched, record_fire; never raises."""

    def __init__(self, env: Env) -> None:
        self.env = env
        self.decisions: list[FireDecision] = []

    def launch(self, d: FireDecision) -> FireIntentRecord:
        e = self.env.engine
        self.decisions.append(d)
        assert d.fire_key and d.scheduled_for
        run_id = f"run-{len(self.decisions)}"
        e.fire_store.write(
            FireIntentRecord(
                fire_key=d.fire_key,
                workspace_root=d.workspace_root,
                schedule_id=d.schedule_id,
                scheduled_for=d.scheduled_for,
                effective_due=d.effective_due,
                created_at=self.env.clock.now,
                status="intended",
            )
        )
        rec = e.fire_store.update(d.fire_key, status="launched", run_id=run_id)
        self.env.alive.add(run_id)
        self.env.statuses[run_id] = "running"
        e.state_store.record_fire(
            d.workspace_root,
            d.schedule_id,
            scheduled_for=d.scheduled_for,
            run_id=run_id,
            fire_at=self.env.clock.now,
            fire_key=d.fire_key,
        )
        return rec

    def finish(self, run_id: str) -> None:
        self.env.alive.discard(run_id)
        self.env.statuses[run_id] = "succeeded"


class Env:
    def __init__(self, tmp: Path, bindings: list[ScheduleBinding], **kw: Any) -> None:
        self.tmp = tmp
        self.root = str((tmp / "ws").resolve())
        self.clock = Clock()
        self.mono = Mono()
        self.alive: set[str] = set()
        self.statuses: dict[str, str] = {}
        FakeRegistry.roots = [self.root]
        self.write_bindings(self.root, bindings)
        self.launcher = RecordingLauncher(self)
        self.engine = self.make_engine(**kw)

    @staticmethod
    def write_bindings(root: str, bindings: list[ScheduleBinding]) -> None:
        Path(root).mkdir(parents=True, exist_ok=True)
        ScheduleStore(root).save(ScheduleFile(schedules=bindings))

    def make_engine(self, **kw: Any) -> ScheduleEngine:
        defaults: dict[str, Any] = dict(
            state_dir=self.tmp / "state",
            clock=self.clock,
            monotonic=self.mono,
            launcher=self.launcher,
            registry_factory=FakeRegistry,
            process_supervisor_factory=lambda root: FakeSup(self.alive),
            run_repository_factory=lambda root: FakeRepo(self.statuses),
        )
        defaults.update(kw)
        return ScheduleEngine(self.tmp / "registry.yaml", **defaults)

    @property
    def entry(self) -> Any:
        return SimpleNamespace(root=self.root, schedules=True)

    def binding(self, sid: str = "s") -> ScheduleBinding:
        return next(b for b in ScheduleStore(self.root).load().bindings if b.id == sid)

    def anchor(self, sid: str = "s", at: datetime = T0) -> None:
        self.engine.state_store.advance_anchor(self.root, sid, at)

    def evaluate(self, sid: str = "s", now: datetime = T0, **kw: Any) -> FireDecision:
        return self.engine.evaluate(self.entry, self.binding(sid), now, **kw)

    def make_active(self, sid: str = "s", key: str = "prior", run_id: str = "r0") -> None:
        e = self.engine
        e.fire_store.write(
            FireIntentRecord(
                fire_key=key,
                workspace_root=self.root,
                schedule_id=sid,
                scheduled_for=T0,
                created_at=T0,
                status="launched",
                run_id=run_id,
            )
        )
        e.state_store.record_fire(
            self.root, sid, scheduled_for=T0, run_id=run_id, fire_at=T0, fire_key=key
        )
        self.alive.add(run_id)
        self.statuses[run_id] = "running"

    def end_run(self, run_id: str = "r0") -> None:
        self.alive.discard(run_id)
        self.statuses[run_id] = "succeeded"

    def tick(self, now: datetime) -> list[FireDecision]:
        self.clock.now = now
        self.mono.t += 20  # > eval interval
        return self.engine.tick(now)

    def events(self, name: str | None = None) -> list[dict[str, Any]]:
        ev = self.engine.event_log.read()
        return [e for e in ev if name is None or e["event"] == name]


def binding(sid: str = "s", **kw: Any) -> ScheduleBinding:
    fields: dict[str, Any] = dict(
        id=sid, kind="interval", interval_seconds=60, workflow="w.json", limits=minimal_limits()
    )
    fields.update(kw)
    return ScheduleBinding(**fields)


def env_for(tmp_path: Path, bindings: list[ScheduleBinding] | None = None, **kw: Any) -> Env:
    return Env(tmp_path, bindings or [binding()], **kw)


def launch(env: Env, d: FireDecision) -> None:
    assert d.fire
    env.launcher.launch(d)


# -- AC4 / module shape --------------------------------------------------------------------------


def test_no_signal_registration_in_module() -> None:
    assert "signal.signal" not in Path(se.__file__).read_text()


def test_fire_reasons_cover_required_set() -> None:
    required = {
        "not_due", "jitter_pending", "never", "disabled", "completed", "quarantined",
        "until_satisfied", "already_fired", "missed", "overlap_skip", "overlap_queued",
        "concurrency", "file_invalid",
    }  # fmt: skip
    assert required <= {r.value for r in FireReason}


def test_not_due_reason(tmp_path: Path) -> None:
    env = env_for(tmp_path)
    env.anchor()
    d = env.evaluate(now=T0 + timedelta(seconds=30))
    assert (d.fire, d.reason) == (False, "not_due")


def test_disabled_reason(tmp_path: Path) -> None:
    env = env_for(tmp_path, [binding(enabled=False)])
    assert env.evaluate().reason == "disabled"


@pytest.mark.parametrize("status", ["completed", "quarantined"])
def test_terminal_status_reasons(tmp_path: Path, status: str) -> None:
    env = env_for(tmp_path)
    env.engine.state_store.set_status(env.root, "s", status)  # type: ignore[arg-type]
    assert env.evaluate().reason == status


def test_unregistered_event_kind_never(tmp_path: Path) -> None:
    env = env_for(
        tmp_path, [binding(kind="file_watch", interval_seconds=None, watch=WatchSpec(paths=["a"]))]
    )
    env.engine._source_factories.pop("file_watch")  # the production file_watch factory is default
    assert env.evaluate().reason == "never"


# -- AC14 overlap matrix -------------------------------------------------------------------------

NOW1 = T0 + timedelta(seconds=60)


def _overlap_env(tmp_path: Path, policy: str, scenario: str) -> Env:
    env = env_for(tmp_path, [binding(overlap=policy, max_concurrent=5)])
    env.anchor()
    if scenario != "none":
        env.make_active()
    if scenario == "full":
        env.engine.state_store.set_queued(env.root, "s", "queued-key")
        env.engine._queued_meta[(env.root, "s")] = ("queued-key", T0, None)
    return env


@pytest.mark.parametrize(
    ("policy", "scenario", "reason", "suppressed", "queued"),
    [
        ("skip", "none", "due", None, None),
        ("skip", "active", "overlap_skip", "overlap", None),
        ("skip", "full", "overlap_skip", "overlap", "queued-key"),
        ("queue", "none", "due", None, None),
        ("queue", "active", "overlap_queued", None, "NEW"),
        ("queue", "full", "overlap_queued", "queue_full", "queued-key"),
        ("allow", "none", "due", None, None),
        ("allow", "active", "due", None, None),
        ("allow", "full", "due", None, "queued-key"),
    ],
)
def test_overlap_matrix(
    tmp_path: Path,
    policy: str,
    scenario: str,
    reason: str,
    suppressed: str | None,
    queued: str | None,
) -> None:
    env = _overlap_env(tmp_path, policy, scenario)
    key = fire_key(env.root, "s", NOW1)
    before = env.engine.state_store.get(env.root, "s")
    assert before is not None or scenario == "none"
    # "full" with a drain candidate is only reachable while the run is active; "allow/full"
    # has an active run, so the queued fire stays buffered.
    d = env.evaluate(now=NOW1)
    assert d.reason == reason
    rec = env.engine.fire_store.get(key, env.root)
    if suppressed is None:
        assert rec is None
    else:
        assert rec is not None and rec.status == "suppressed" and rec.reason == suppressed
    got = env.engine.state_store.get(env.root, "s")
    expect = key if queued == "NEW" else queued
    assert (got.queued_fire_key if got else None) == expect


def test_queue_drains_on_first_tick_after_run_ends(tmp_path: Path) -> None:
    env = env_for(tmp_path, [binding(overlap="queue")])
    env.anchor()
    env.make_active()
    assert env.tick(NOW1) == []  # queued, not fired
    key = fire_key(env.root, "s", NOW1)
    assert env.engine.state_store.get(env.root, "s").queued_fire_key == key  # type: ignore[union-attr]
    assert env.engine.fire_store.get(key, env.root) is None  # F8: no record for the queued key
    env.end_run()
    fired = env.tick(NOW1 + timedelta(seconds=10))
    assert [d.reason for d in fired] == ["queue_drain"]
    rec = env.engine.fire_store.get(key, env.root)
    assert (
        rec is not None and rec.status == "launched"
    )  # F8: intended -> launched, never suppressed
    assert env.engine.state_store.get(env.root, "s").queued_fire_key is None  # type: ignore[union-attr]


def test_queue_lost_on_restart_is_dropped_loudly(tmp_path: Path) -> None:
    env = env_for(tmp_path, [binding(overlap="queue")])
    env.anchor()
    env.make_active()
    env.tick(NOW1)
    env.end_run()
    env.engine._queued_meta.clear()  # simulate a restart
    # legacy state: key persisted without its instant
    env.engine.state_store.set_queued(env.root, "s", fire_key(env.root, "s", NOW1))
    assert env.tick(NOW1 + timedelta(seconds=10)) == []
    assert env.engine.state_store.get(env.root, "s").queued_fire_key is None  # type: ignore[union-attr]
    assert any(e["reason"] == "queue_lost_on_restart" for e in env.events("schedule.missed"))


def test_queued_fire_survives_restart_with_original_scheduled_for(tmp_path: Path) -> None:
    env = env_for(tmp_path, [binding(overlap="queue")])
    env.anchor()
    env.make_active()
    assert env.tick(NOW1) == []
    key = fire_key(env.root, "s", NOW1)
    st = env.engine.state_store.get(env.root, "s")
    assert st is not None and st.queued_scheduled_for == NOW1
    env.end_run()
    env.engine._queued_meta.clear()  # simulate a restart: only state.json survives
    fired = env.tick(NOW1 + timedelta(seconds=10))
    assert [d.reason for d in fired] == ["queue_drain"]
    assert fired[0].scheduled_for == NOW1 and fired[0].fire_key == key
    after = env.engine.state_store.get(env.root, "s")
    assert after is not None and (after.queued_fire_key, after.queued_scheduled_for) == (None, None)
    assert not env.events("schedule.missed")


# -- AC15 catch-up -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("catch_up", "lateness", "fires", "catch_event"),
    [
        ("once", 59, True, False),
        ("once", 61, True, True),
        ("once", 3599, True, True),
        ("once", 3601, False, False),
        ("skip", 59, True, False),
        ("skip", 61, False, False),
        ("skip", 3599, False, False),
    ],
)
def test_catch_up_boundaries(
    tmp_path: Path, catch_up: str, lateness: int, fires: bool, catch_event: bool
) -> None:
    env = env_for(
        tmp_path,
        [
            binding(
                interval_seconds=DAY,
                catch_up=catch_up,
                grace_seconds=60,
                catch_up_window_seconds=3600,
            )
        ],
    )
    env.anchor()
    d = env.evaluate(now=T0 + timedelta(seconds=DAY + lateness))
    assert d.fire is fires
    if not fires:
        assert d.reason == "missed"
        assert env.engine.fire_store.get(d.fire_key or "", env.root).reason == "missed"  # type: ignore[union-attr]
        assert env.events("schedule.missed")
    assert bool(env.events("schedule.catch_up")) is catch_event


def test_backlog_collapses_to_one_decision(tmp_path: Path) -> None:
    env = env_for(tmp_path)
    env.anchor()
    now = T0 + timedelta(seconds=60 * 50)
    fired = env.tick(now)
    assert len(fired) == 1 and fired[0].scheduled_for == now
    missed = env.events("schedule.missed")
    assert missed[0]["skipped_instants"] == 49  # N-1, reported once (not 49 fsyncs)
    assert len(env.launcher.decisions) == 1  # never backfills


def test_collapse_bounded_by_max_steps(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(se, "MAX_CATCHUP_STEPS", 3)
    env = env_for(tmp_path)
    env.anchor()
    d = env.evaluate(now=T0 + timedelta(seconds=60 * 50))
    assert d.scheduled_for == T0 + timedelta(seconds=60 * 4)  # 1 + 3 steps, then stops


# -- AC16 jitter ---------------------------------------------------------------------------------


def _jitter_env(tmp_path: Path, jitter: int = 3600, **kw: Any) -> tuple[Env, datetime, int]:
    env = env_for(tmp_path, [binding(interval_seconds=DAY, jitter_seconds=jitter, **kw)])
    env.anchor()
    sf = T0 + timedelta(seconds=DAY)
    return env, sf, se._jitter_offset(fire_key(env.root, "s", sf), env.binding())


def test_jitter_deterministic_across_processes_and_bounded(tmp_path: Path) -> None:
    env, sf, off = _jitter_env(tmp_path)
    key = fire_key(env.root, "s", sf)
    code = (
        "import sys;from agent_orchestrator.service.schedule_engine import _jitter_offset;"
        "from types import SimpleNamespace as N;"
        "print(_jitter_offset(sys.argv[1], N(jitter_seconds=3600)))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code, key], capture_output=True, text=True, check=True
    )
    assert int(out.stdout) == off
    for i in range(200):
        k = fire_key(env.root, "s", T0 + timedelta(seconds=i))
        assert 0 <= se._jitter_offset(k, SimpleNamespace(jitter_seconds=300)) <= 300  # type: ignore[arg-type]


def test_jitter_pending_then_fires_and_restart_fires_once(tmp_path: Path) -> None:
    env, sf, off = _jitter_env(tmp_path)
    assert off > 0
    d = env.evaluate(now=sf + timedelta(seconds=off - 1))
    assert d.reason == "jitter_pending"
    # restart between scheduled_for and effective_due: a fresh engine on the same state dir
    env.engine = env.make_engine()
    d = env.evaluate(now=sf + timedelta(seconds=off))
    assert d.fire and d.fire_key == fire_key(env.root, "s", sf)
    launch(env, d)
    env.engine = env.make_engine()
    again = env.evaluate(now=sf + timedelta(seconds=off + 1))
    assert not again.fire
    assert len(env.launcher.decisions) == 1


# -- AC17 at-most-once ---------------------------------------------------------------------------


def test_same_instant_fires_once_and_rewind_never(tmp_path: Path) -> None:
    env = env_for(tmp_path)
    env.anchor()
    d1 = env.evaluate(now=NOW1)
    assert d1.fire
    launch(env, d1)
    d2 = env.evaluate(now=NOW1)
    assert not d2.fire
    rewound = env.evaluate(now=NOW1 - timedelta(hours=1))
    assert not rewound.fire
    assert len(env.launcher.decisions) == 1


def test_already_fired_burned_key_after_anchor_lost(tmp_path: Path) -> None:
    """If the anchor never advanced (crash) the burned key still blocks, after catch-up."""
    env = env_for(tmp_path)
    env.anchor()
    d = env.evaluate(now=NOW1)
    launch(env, d)
    env.anchor()  # rewind the anchor to simulate lost state
    assert env.evaluate(now=NOW1).reason == "already_fired"


# -- AC17a regressions ---------------------------------------------------------------------------


def test_f7_jitter_beyond_grace_is_not_lateness(tmp_path: Path) -> None:
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
    d = env.evaluate(sid, now=sf + timedelta(seconds=off))
    assert d.fire and d.reason == "due"
    assert not env.events("schedule.catch_up")


def test_f13_overlap_streak_does_not_exceed_catchup_steps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(se, "MAX_CATCHUP_STEPS", 5)
    env = env_for(tmp_path)
    env.anchor()
    env.make_active()
    for i in range(1, 21):
        assert env.evaluate(now=T0 + timedelta(seconds=60 * i)).reason == "overlap_skip"
    env.end_run()
    later = T0 + timedelta(seconds=60 * 21)
    d = env.evaluate(now=later)
    assert d.fire and d.scheduled_for == later


def test_f9_second_engine_refuses_and_probe(tmp_path: Path) -> None:
    env = env_for(tmp_path)
    state = tmp_path / "state"
    assert not probe_engine_lock(state)
    env.engine.start()
    assert probe_engine_lock(state)
    with pytest.raises(EngineLockHeldError):
        env.make_engine().start()
    env.engine.shutdown()
    env.engine.shutdown()  # idempotent
    assert not probe_engine_lock(state)
    second = env.make_engine()
    second.start()
    second.shutdown()


def test_f2_real_supervisor_child_exit_is_reaped_and_key_retired(tmp_path: Path) -> None:
    created: list[ProcessSupervisor] = []

    def factory(root: str) -> ProcessSupervisor:
        sup = ProcessSupervisor(
            root,
            ao_command=[sys.executable, "-c", "import sys; sys.exit(0)"],
            run_id_discovery_timeout=0.0,
        )
        created.append(sup)
        return sup

    env = env_for(
        tmp_path, process_supervisor_factory=factory, run_repository_factory=lambda r: FakeRepo({})
    )
    env.anchor()
    key = "k-zombie"
    sup = env.engine.process_supervisor(env.root)
    assert env.engine.process_supervisor(env.root) is sup and len(created) == 1  # cached
    assert sup._discovery_timeout == 0.0
    rec = sup.launch_run(workflow_path="w.json", run_id="ao-s-20260101T000000Z")
    env.engine.fire_store.write(
        FireIntentRecord(
            fire_key=key, workspace_root=env.root, schedule_id="s", scheduled_for=T0,
            created_at=T0, status="launched", run_id=rec.run_id,
        )
    )  # fmt: skip
    env.engine.state_store.record_fire(
        env.root, "s", scheduled_for=T0, run_id=rec.run_id or "", fire_at=T0, fire_key=key
    )
    deadline = time.time() + 10
    while time.time() < deadline and sup._children[rec.pid].poll() is None:
        time.sleep(0.05)
    # poll() above already reaped it for this instance; a *fresh* instance would see a zombie as
    # alive -- so prove the engine's tick (reconcile) is what retires the key.
    sup2 = env.engine.process_supervisor(env.root)
    assert sup2 is sup
    env.engine.tick(T0 + timedelta(seconds=5))
    env.engine._live_runs()
    assert key not in env.engine.state_store.get(env.root, "s").active_fire_keys  # type: ignore[union-attr]
    with pytest.raises(ChildProcessError):
        os.waitpid(rec.pid, os.WNOHANG)


# -- AC18 concurrency ----------------------------------------------------------------------------


def test_concurrency_per_workspace_does_not_burn_key_but_overlap_does(tmp_path: Path) -> None:
    env = env_for(tmp_path, [binding(overlap="allow", max_concurrent=1)])
    env.anchor()
    env.make_active()
    key = fire_key(env.root, "s", NOW1)
    d = env.evaluate(now=NOW1)
    assert d.reason == "concurrency" and not env.engine.fire_store.exists(key, env.root)
    assert env.events("schedule.skipped_concurrency")
    # the same instant fires once the slot frees (inside grace)
    env.end_run()
    assert env.evaluate(now=NOW1 + timedelta(seconds=5)).fire
    # overlap skip, by contrast, burns the key
    env2 = env_for(tmp_path / "o", [binding(overlap="skip")])
    env2.anchor()
    env2.make_active()
    env2.evaluate(now=NOW1)
    assert env2.engine.fire_store.exists(fire_key(env2.root, "s", NOW1), env2.root)


def test_global_cap(tmp_path: Path) -> None:
    env = env_for(
        tmp_path,
        [
            binding("a", overlap="allow", max_concurrent=5),
            binding("b", overlap="allow", max_concurrent=5),
        ],
        max_concurrent_global=1,
    )
    env.anchor("a")
    env.anchor("b")
    env.make_active("a")
    assert env.evaluate("b", now=NOW1).reason == "concurrency"


def test_caps_count_fires_accepted_earlier_in_same_tick(tmp_path: Path) -> None:
    env = env_for(tmp_path, [binding("a"), binding("b")], max_concurrent_global=1)
    env.anchor("a")
    env.anchor("b")
    fired = env.tick(NOW1)
    assert len(fired) == 1
    assert env.events("schedule.skipped_concurrency")


# -- AC19 isolation ------------------------------------------------------------------------------


def test_eval_exception_isolated_then_quarantined(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = env_for(tmp_path, [binding("bad"), binding("good", interval_seconds=DAY)])
    env.anchor("bad")
    env.anchor("good", T0 - timedelta(seconds=DAY))  # good is due immediately
    real = env.engine.evaluate
    calls: list[str] = []

    def flaky(entry: Any, b: ScheduleBinding, now: datetime, **kw: Any) -> FireDecision:
        calls.append(b.id)
        if b.id == "bad":
            raise RuntimeError("boom")
        return real(entry, b, now, **kw)

    monkeypatch.setattr(env.engine, "evaluate", flaky)
    fired = env.tick(NOW1)
    assert [d.schedule_id for d in fired] == ["good"]  # next binding still fired
    for i in range(2, 6):
        env.tick(NOW1 + timedelta(minutes=i))
    st = env.engine.state_store.get(env.root, "bad")
    assert st is not None and st.status == "quarantined"
    assert len(env.events("schedule.eval_failed")) == 5 and env.events("schedule.quarantined")
    calls.clear()
    env.tick(NOW1 + timedelta(minutes=9))
    assert "bad" not in calls  # sixth tick does not evaluate it


def test_success_clears_error_streak(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = env_for(tmp_path)
    env.anchor()
    env.engine.state_store.bump_eval_error(env.root, "s")
    env.tick(T0 + timedelta(seconds=10))
    assert env.engine.state_store.get(env.root, "s").consecutive_eval_errors == 0  # type: ignore[union-attr]


def test_invalid_file_skips_only_that_workspace(tmp_path: Path) -> None:
    env = env_for(tmp_path)
    env.anchor()
    bad = tmp_path / "bad-ws"
    (bad / ".ao").mkdir(parents=True)
    (bad / ".ao" / "schedules.yaml").write_text("schedules: [not: valid: yaml")
    FakeRegistry.roots = [str(bad.resolve()), env.root]
    fired = env.tick(NOW1)
    assert [d.schedule_id for d in fired] == ["s"]
    ev = env.events("schedule.file_invalid")
    assert ev and ev[0]["reason"] == "file_invalid"


def test_registry_failure_never_raises(tmp_path: Path) -> None:
    class Boom:
        def __init__(self, p: Path) -> None:
            raise OSError("nope")

    env = env_for(tmp_path, registry_factory=Boom)
    assert env.tick(NOW1) == []
    assert env.engine.status_snapshot()["totals"]["active"] == 0


# -- AC20 budget ---------------------------------------------------------------------------------


def test_budget_exceeded_stops_leaves_rest_untouched_then_resumes(tmp_path: Path) -> None:
    env = env_for(tmp_path, [binding("a"), binding("b")])
    env.anchor("a")  # b deliberately has no state at all
    env.mono.jump_at_call = env.mono.calls + 3  # tick start, binding a check, binding b check
    env.mono.t += 20
    env.clock.now = NOW1
    fired = env.engine.tick(NOW1)
    assert [d.schedule_id for d in fired] == ["a"]
    ev = env.events("schedule.tick_budget_exceeded")
    assert len(ev) == 1 and ev[0]["schedule_id"] == "b"
    assert env.engine.state_store.get(env.root, "b") is None  # unchanged
    env.mono.jump_at_call = None
    env.end_run("run-1")
    env.anchor("b")
    fired2 = env.tick(NOW1 + timedelta(seconds=60))
    assert "b" in {d.schedule_id for d in fired2}  # next tick picks it up first (cursor)


# -- self-rate-limit -----------------------------------------------------------------------------


def test_tick_rate_limited_by_monotonic(tmp_path: Path) -> None:
    env = env_for(tmp_path)
    env.anchor()
    env.clock.now = NOW1
    assert len(env.engine.tick(NOW1)) == 1
    env.mono.t += 5
    assert env.engine.tick(NOW1 + timedelta(seconds=60)) == []  # < 15 s: skipped entirely
    env.mono.t += 10
    env.end_run("run-1")
    assert len(env.engine.tick(NOW1 + timedelta(seconds=120))) == 1


def test_tick_reconciles_cached_supervisors_every_call(tmp_path: Path) -> None:
    env = env_for(tmp_path)
    sup = env.engine.process_supervisor(env.root)
    env.engine.tick(NOW1)
    env.engine.tick(NOW1)  # rate-limited, still reconciles (reaping must not wait 15 s)
    assert sup.reconciled == 2  # type: ignore[attr-defined]


# -- start / orphans -----------------------------------------------------------------------------


def test_start_reconciles_orphans_and_launches_nothing(tmp_path: Path) -> None:
    env = env_for(tmp_path)
    env.engine.fire_store.write(
        FireIntentRecord(
            fire_key="orph", workspace_root=env.root, schedule_id="s", scheduled_for=T0,
            created_at=T0, status="intended", pid=2**22 + 7,
        )
    )  # fmt: skip
    env.engine.start()
    env.engine.shutdown()
    assert env.engine.fire_store.get("orph", env.root).status == "orphaned"  # type: ignore[union-attr]
    assert env.events("schedule.fire_orphaned") and env.launcher.decisions == []


# -- AC12 run-now --------------------------------------------------------------------------------


def _request(env: Env, **kw: Any) -> Path:
    d = tmp = env.tmp / "state" / "schedules" / "requests"
    d.mkdir(parents=True, exist_ok=True)
    body = {
        "workspace_root": env.root,
        "schedule_id": "s",
        "requested_at": T0.isoformat(),
        "force": False,
    }
    body.update(kw)
    p = tmp / f"{len(list(d.iterdir()))}.json"
    p.write_text(json.dumps(body))
    return p


def test_run_now_fires_undue_schedule_and_deletes_request(tmp_path: Path) -> None:
    env = env_for(tmp_path)
    env.anchor()
    p = _request(env)
    fired = env.tick(T0 + timedelta(seconds=5))
    assert len(fired) == 1 and fired[0].scheduled_for == T0 and not p.exists()


def test_run_now_expired_discarded(tmp_path: Path) -> None:
    env = env_for(tmp_path)
    env.anchor()
    p = _request(env, requested_at=(T0 - timedelta(seconds=se.REQUEST_TTL_SECONDS + 1)).isoformat())
    assert env.tick(T0) == [] and not p.exists()
    assert env.events("schedule.request_expired")


def test_run_now_force_bypasses_overlap_only(tmp_path: Path) -> None:
    env = env_for(tmp_path, [binding(overlap="skip", max_concurrent=1)])
    env.anchor()
    env.make_active()
    _request(env, force=False)
    assert env.tick(T0 + timedelta(seconds=5)) == []  # overlap skip applies
    _request(env, force=True, requested_at=(T0 + timedelta(seconds=6)).isoformat())
    assert (
        env.tick(T0 + timedelta(seconds=10)) == []
    )  # force passes overlap, concurrency still blocks
    assert env.events("schedule.skipped_concurrency")


def test_webhook_requests_left_in_place(tmp_path: Path) -> None:
    env = env_for(tmp_path)
    p = _request(env, kind="webhook")
    assert env.engine.drain_run_now_requests(T0) == [] and p.exists()


def test_run_now_invalid_file_discarded(tmp_path: Path) -> None:
    env = env_for(tmp_path)
    d = tmp_path / "state" / "schedules" / "requests"
    d.mkdir(parents=True)
    (d / "bad.json").write_text("{nope")
    assert env.engine.drain_run_now_requests(T0) == [] and not (d / "bad.json").exists()


# -- TriggerSource (F6) --------------------------------------------------------------------------


class FakeSource:
    def __init__(self) -> None:
        self.committed: list[Candidate] = []
        self.next: Candidate | None = None

    def observe(self, b: Any, st: Any, now: datetime) -> Candidate | None:
        return self.next

    def commit(self, b: Any, c: Candidate) -> None:
        self.committed.append(c)


def _event_env(tmp_path: Path, **kw: Any) -> tuple[Env, FakeSource]:
    env = env_for(
        tmp_path,
        [binding(kind="file_watch", interval_seconds=None, watch=WatchSpec(paths=["a"]), **kw)],
    )
    src = FakeSource()
    env.engine.register_trigger_source("file_watch", lambda root, b: src)
    return env, src


def test_event_source_commits_only_after_launch(tmp_path: Path) -> None:
    env, src = _event_env(tmp_path)
    src.next = Candidate(scheduled_for=T0)
    fired = env.tick(T0 + timedelta(seconds=1))
    assert len(fired) == 1 and len(src.committed) == 1


def test_event_source_not_committed_on_concurrency_skip(tmp_path: Path) -> None:
    env, src = _event_env(tmp_path, overlap="allow", max_concurrent=1)
    env.make_active()
    src.next = Candidate(scheduled_for=T0)
    assert env.evaluate(now=T0 + timedelta(seconds=1)).reason == "concurrency"
    assert src.committed == []


def test_event_source_committed_on_overlap_skip_and_none_is_not_due(tmp_path: Path) -> None:
    env, src = _event_env(tmp_path)
    assert env.evaluate(now=T0).reason == "not_due"
    env.make_active()
    src.next = Candidate(scheduled_for=T0)
    assert env.evaluate(now=T0 + timedelta(seconds=1)).reason == "overlap_skip"
    assert len(src.committed) == 1


def test_register_rejects_pure_kinds(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        env_for(tmp_path).engine.register_trigger_source("cron", lambda r, b: FakeSource())


# -- launcher failure / no launcher --------------------------------------------------------------


def test_launcher_exception_never_escapes_tick(tmp_path: Path) -> None:
    class Raising:
        def launch(self, d: FireDecision) -> FireIntentRecord:
            raise RuntimeError("spawn failed")

    env = env_for(tmp_path, launcher=Raising())
    env.anchor()
    assert len(env.tick(NOW1)) == 1
    assert env.events("schedule.fire_failed")
    assert env.engine.state_store.get(env.root, "s").consecutive_failures == 1  # type: ignore[union-attr]


def test_no_launcher_still_decides(tmp_path: Path) -> None:
    env = env_for(tmp_path, launcher=None)
    env.anchor()
    assert len(env.tick(NOW1)) == 1


def test_default_supervisor_factory_uses_zero_discovery_timeout(tmp_path: Path) -> None:
    sup = se._default_process_supervisor(str(tmp_path))
    assert sup._discovery_timeout == 0.0


# -- AC13 snapshot -------------------------------------------------------------------------------


def test_status_snapshot_shape(tmp_path: Path) -> None:
    env = env_for(tmp_path, [binding("a"), binding("b", enabled=False)])
    env.anchor("a")
    env.tick(NOW1)
    snap = env.engine.status_snapshot()
    assert set(snap) == {
        "enabled", "eval_interval_seconds", "totals", "next_fire_at", "live_scheduled_runs",
        "max_concurrent_global", "recent_failures", "last_tick_at", "last_tick_duration_ms",
        "webhook", "by_workspace",
    }  # fmt: skip
    assert snap["totals"] == {"active": 1, "disabled": 1, "completed": 0, "quarantined": 0}
    assert snap["live_scheduled_runs"] == 1 and snap["last_tick_at"] is not None
    rows = snap["by_workspace"][0]["schedules"]
    assert {r["id"] for r in rows} == {"a", "b"} and rows[0]["kind"] == "interval"
    json.dumps(snap)  # JSON-serialisable


# -- w10-03 review fixes (T-Ev3Qm5 M1-M4, L1/L3/L4/L7) ----------------------------


def test_m1_run_now_and_natural_instant_in_one_tick_fire_once_under_overlap_skip(
    tmp_path: Path,
) -> None:
    env = env_for(tmp_path, [binding(overlap="skip", max_concurrent=3)])
    env.anchor()
    _request(env, requested_at=NOW1.isoformat())
    fired = env.tick(NOW1)
    assert len(fired) == 1  # the second fire of the same schedule is an overlap, not a launch
    assert len(env.launcher.decisions) == 1
    assert env.events("schedule.skipped_overlap")


def test_m1_two_run_now_requests_in_one_tick_fire_once(tmp_path: Path) -> None:
    env = env_for(tmp_path, [binding(overlap="skip", max_concurrent=3)])
    env.anchor()
    _request(env, requested_at=NOW1.isoformat())
    _request(env, requested_at=(NOW1 + timedelta(seconds=1)).isoformat())
    assert len(env.tick(NOW1 + timedelta(seconds=2))) == 1


def test_m2_jitter_larger_than_period_still_fires_every_period(tmp_path: Path) -> None:
    env = env_for(
        tmp_path,
        [
            binding(
                interval_seconds=60,
                jitter_seconds=300,
                overlap="allow",
                max_concurrent=100,
                catch_up="once",
                grace_seconds=600,  # head-of-line delay behind a long jitter is not "missed"
                catch_up_window_seconds=3600,
            )
        ],
        max_concurrent_global=1000,
    )
    env.anchor()
    for step in range(1, 241):  # one hour of 15 s ticks
        env.tick(T0 + timedelta(seconds=15 * step))
    fired_keys = {d.fire_key for d in env.launcher.decisions}
    assert len(fired_keys) == len(env.launcher.decisions)  # never twice
    assert (
        len(env.launcher.decisions) >= 50
    )  # ~60 minus the jitter tail; was 7 before the fix (review P1)


def test_m3_run_now_blocked_by_concurrency_is_retried_not_dropped(tmp_path: Path) -> None:
    env = env_for(tmp_path, [binding(overlap="allow", max_concurrent=1)])
    env.anchor()
    env.make_active()
    _request(env, force=True, requested_at=T0.isoformat())
    assert env.tick(T0 + timedelta(seconds=5)) == []
    requests = env.tmp / "state" / "schedules" / "requests"
    assert len(list(requests.glob("*.json"))) == 1  # written back, not lost
    assert env.events("schedule.run_now_deferred")
    env.end_run()
    fired = env.tick(T0 + timedelta(seconds=25))
    assert len(fired) == 1 and fired[0].scheduled_for == T0
    assert not list(requests.glob("*.json"))


def test_m3_deferred_run_now_still_expires_after_ttl(tmp_path: Path) -> None:
    env = env_for(tmp_path, [binding(overlap="allow", max_concurrent=1)])
    env.anchor()
    env.make_active()
    _request(env, requested_at=T0.isoformat())
    env.tick(T0 + timedelta(seconds=5))
    env.tick(T0 + timedelta(seconds=se.REQUEST_TTL_SECONDS + 30))
    assert env.events("schedule.request_expired")
    assert not list((env.tmp / "state" / "schedules" / "requests").glob("*.json"))


def test_m4_global_cap_skip_does_not_burn_key(tmp_path: Path) -> None:
    env = env_for(
        tmp_path,
        [binding("a", overlap="allow", max_concurrent=5), binding("b", overlap="skip")],
        max_concurrent_global=1,
    )
    env.anchor("a")
    env.anchor("b")
    env.make_active("a")
    d = env.evaluate("b", now=NOW1)  # main (non-overlap) branch
    assert d.reason == "concurrency"
    assert not env.engine.fire_store.exists(fire_key(env.root, "b", NOW1), env.root)


def test_m4_other_schedules_run_filling_workspace_slot_does_not_burn_key(tmp_path: Path) -> None:
    env = env_for(tmp_path, [binding("a"), binding("b", max_concurrent=1)])
    env.anchor("a")
    env.anchor("b")
    env.make_active("a")
    d = env.evaluate("b", now=NOW1)
    assert d.reason == "concurrency"
    assert not env.engine.fire_store.exists(fire_key(env.root, "b", NOW1), env.root)
    env.end_run()  # the slot frees inside grace: the same instant now fires
    assert env.evaluate("b", now=NOW1 + timedelta(seconds=5)).fire


def test_m4_queue_drain_concurrency_skip_keeps_queue_and_burns_nothing(tmp_path: Path) -> None:
    env = env_for(tmp_path, [binding("a"), binding("b", overlap="queue", max_concurrent=1)])
    env.anchor("b")
    env.make_active("a")
    env.engine.state_store.set_queued(env.root, "b", "qkey", T0)
    env.engine._queued_meta[(env.root, "b")] = ("qkey", T0, None)
    d = env.evaluate("b", now=NOW1)
    assert d.reason == "concurrency" and d.fire_key == "qkey"
    assert not env.engine.fire_store.exists("qkey", env.root)
    assert env.engine.state_store.get(env.root, "b").queued_fire_key == "qkey"  # type: ignore[union-attr]


def test_l1_backlog_collapse_reported_once_while_instant_waits(tmp_path: Path) -> None:
    env = env_for(tmp_path, [binding(overlap="allow", max_concurrent=1)])
    env.anchor()
    env.make_active()  # slot full: the collapsed instant is retried every tick
    for i in range(4):
        env.tick(T0 + timedelta(seconds=60 * 10 + i))
    collapsed = [e for e in env.events("schedule.missed") if e.get("reason") == "backlog_collapsed"]
    assert len(collapsed) == 1


def test_l3_no_catch_up_event_for_already_recorded_instant(tmp_path: Path) -> None:
    env = env_for(
        tmp_path,
        [binding(interval_seconds=DAY, catch_up="once", grace_seconds=60,
                 catch_up_window_seconds=3600)],
    )  # fmt: skip
    env.anchor()
    due = T0 + timedelta(seconds=DAY)
    env.engine.fire_store.write_suppressed(
        fire_key(env.root, "s", due), "launch_failed", workspace_root=env.root,
        schedule_id="s", scheduled_for=due,
    )  # fmt: skip
    d = env.evaluate(now=due + timedelta(seconds=600))
    assert d.reason == "already_fired"
    assert not env.events("schedule.catch_up")


def test_l4_bookkeeping_failure_does_not_discard_other_decisions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = env_for(
        tmp_path,
        [binding("a", max_concurrent=5), binding("b", max_concurrent=5)],
        max_concurrent_global=5,
    )
    env.anchor("a")
    env.anchor("b")
    env.engine.state_store.bump_eval_error(env.root, "a")

    def boom(*_a: Any, **_k: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(env.engine.state_store, "clear_eval_errors", boom)
    fired = env.tick(NOW1)
    assert {d.schedule_id for d in fired} == {"a", "b"}
    assert not env.events("schedule.eval_failed")


def test_l7_status_snapshot_error_keeps_full_shape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = env_for(tmp_path)
    ok = set(env.engine.status_snapshot())

    def boom() -> dict[str, Any]:
        raise RuntimeError("x")

    monkeypatch.setattr(env.engine, "_snapshot", boom)
    snap = env.engine.status_snapshot()
    assert ok <= set(snap) and snap["error"] == "x"
    assert snap["totals"]["active"] == 0 and snap["by_workspace"] == []


# -- public fire() seam (W4: run-now --local no longer reaches into _launcher) --------------------


def test_fire_launches_decision_and_returns_record(tmp_path: Path) -> None:
    env = env_for(tmp_path)
    env.anchor()
    d = env.evaluate(now=T0 + timedelta(seconds=60))
    rec = env.engine.fire(d)
    assert rec is not None and rec.status == "launched" and rec.fire_key == d.fire_key
    assert env.launcher.decisions == [d]


def test_fire_without_launcher_returns_none(tmp_path: Path) -> None:
    env = env_for(tmp_path, launcher=None)
    env.anchor()
    d = env.evaluate(now=T0 + timedelta(seconds=60))
    assert env.engine.fire(d) is None


def test_fire_propagates_launcher_errors(tmp_path: Path) -> None:
    env = env_for(tmp_path)
    env.anchor()
    d = env.evaluate(now=T0 + timedelta(seconds=60))

    def boom(_: FireDecision) -> FireIntentRecord:
        raise RuntimeError("launch exploded")

    env.launcher.launch = boom  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="launch exploded"):
        env.engine.fire(d)
