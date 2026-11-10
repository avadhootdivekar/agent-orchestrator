"""T-Sn4Rec: liveness sampling, kill ladder (retry -> escalate -> fail), process-group kill."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from agent_orchestrator import engine as engine_mod
from agent_orchestrator import sentinel_tick
from agent_orchestrator.artifacts import LocalFsArtifactStore
from agent_orchestrator.engine import Orchestrator
from agent_orchestrator.executors import claude_cli
from agent_orchestrator.executors.claude_cli import ClaudeCliExecutor
from agent_orchestrator.executors.fake import FakeExecutor
from agent_orchestrator.models import (
    AgentSpec,
    CancelToken,
    RepoRef,
    RepoSet,
    RunState,
    SentinelRecoverySpec,
    SentinelSpec,
    TaskContext,
    TaskResult,
    TaskSpec,
    WorkflowSpec,
)
from agent_orchestrator.runstate import RunStateStore
from agent_orchestrator.sentinel_settings import SentinelSettings, resolve_sentinel_settings

WAIT_S = 15.0


def _wait_for(cond: Any) -> bool:
    end = time.monotonic() + WAIT_S
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


def _run_dir(rs: RunStateStore, state: RunState) -> Path:
    return rs._path(state.run_id).parent


def _write_entry(run_dir: Path, entry_id: str, kind: str, payload: dict | None = None) -> Path:
    inbox = run_dir / "control" / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    body = {
        "schema": "ao.ctl/v1",
        "id": entry_id,
        "kind": kind,
        "actor": "cli:tester",
        "created_at": "2026-11-09T00:00:00+00:00",
        "payload": payload or {},
    }
    tmp = inbox / f"{entry_id}.json.tmp"
    tmp.write_text(json.dumps(body))
    final = inbox / f"{entry_id}.json"
    tmp.replace(final)
    return final


STEP = timedelta(seconds=90)


class StepClock:
    """Every read advances 90 s: a stalled task crosses ``idle_kill_minutes`` in a few ticks."""

    def __init__(self) -> None:
        self.now = datetime(2026, 11, 9, tzinfo=UTC)
        self.lock = threading.Lock()

    def __call__(self) -> datetime:
        with self.lock:
            self.now += STEP
            return self.now


def _settings(**recovery: Any) -> SentinelSettings:
    rec = {"force": True, "idle_kill_minutes": 1, **recovery}
    return resolve_sentinel_settings(
        workflow=SentinelSpec(enabled=True, tick_seconds=1, recovery=SentinelRecoverySpec(**rec))
    )


def _wf(n: int = 1, **task: Any) -> WorkflowSpec:
    tasks = [
        TaskSpec(
            id=f"t{i}", agent="ag", instruction="specs/examples/instructions/design.md", **task
        )
        for i in range(n)
    ]
    return WorkflowSpec(version="1.0", id="wf", repo_set="rs", tasks=tasks)


def _setup(tmp_path: Path) -> tuple[LocalFsArtifactStore, RunStateStore, dict, dict]:
    store = LocalFsArtifactStore(str(tmp_path))
    rs = RunStateStore(str(tmp_path), store)
    reposets = {
        "rs": RepoSet(
            workspace_root=str(tmp_path), repos=[RepoRef(id="core", path=".", role="primary")]
        )
    }
    return store, rs, reposets, {"ag": AgentSpec(executor="fake")}


@pytest.fixture(autouse=True)
def _fast_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    real = engine_mod.wait

    def fast(fs: Any, timeout: float | None = None, return_when: Any = None) -> Any:
        return real(fs, timeout=0.02 if timeout is not None else None, return_when=return_when)

    monkeypatch.setattr(engine_mod, "wait", fast)


class StallExecutor(FakeExecutor):
    """Dispatches in ``stall_dispatches`` hang until the cancel event fires, then report killed."""

    def __init__(
        self, stall_dispatches: set[tuple[str, int]] | None = None, stall_all: bool = False
    ):
        super().__init__()
        self.stall = stall_dispatches or set()
        self.stall_all = stall_all
        self.calls: dict[str, int] = {}
        self.agents_seen: list[tuple[str, int, Any]] = []
        self.prior: list[tuple[str, int, str | None]] = []

    def execute(self, ctx: TaskContext) -> TaskResult:
        n = self.calls[ctx.task_id] = self.calls.get(ctx.task_id, 0) + 1
        self.agents_seen.append((ctx.task_id, n, ctx.agent.turn_budget))
        self.prior.append((ctx.task_id, n, ctx.prior_attempts_path))
        if (self.stall_all or (ctx.task_id, n) in self.stall) and ctx.cancel_event is not None:
            assert ctx.cancel_event.wait(20), "never cancelled"
            tok = ctx.cancel_event
            return TaskResult(
                task_id=ctx.task_id,
                status="killed",
                attempts=1,
                kill_reason=tok.reason,
                kill_source=tok.source,
            )
        return super().execute(ctx)


def test_idle_kill_retry_escalate_then_fail(tmp_path: Path) -> None:
    store, rs, reposets, agents = _setup(tmp_path)
    ex = StallExecutor(stall_all=True)
    orch = Orchestrator(
        ex, store, rs, max_parallel=1, clock=StepClock(), sentinel=_settings(systemic_kill_alarm=99)
    )
    state = orch.run(_wf(), reposets, agents)
    ts = state.tasks["t0"]
    assert state.status == "failed" and ts.status == "failed"
    # kills #1 (retry), #2 (escalate), #3 (> max_recovery_kills_per_task=2 -> fail)
    assert ex.calls["t0"] == 3
    assert ts.recovery is not None and ts.recovery.kills == 3
    assert ts.recovery.last_reason == "idle" and ts.recovery.last_source == "mechanical"
    assert [b for _, _, b in ex.agents_seen] == [None, None, "high"]  # retry, then escalate
    assert ts.recovery.escalation_step == 1
    # retry-context note handed to the re-dispatch
    note = ex.prior[1][2]
    assert ex.prior[0][2] is None and note is not None
    assert "killed" in Path(note).read_text()
    assert state.sentinel is not None and len(state.sentinel.kill_log) == 3


def test_kill_does_not_consume_max_attempts_and_recovers(tmp_path: Path) -> None:
    store, rs, reposets, agents = _setup(tmp_path)
    ex = StallExecutor({("t0", 1)})
    orch = Orchestrator(
        ex, store, rs, max_parallel=1, clock=StepClock(), sentinel=_settings(systemic_kill_alarm=99)
    )
    wf = _wf()
    state = orch.run(wf, reposets, agents)
    assert state.status == "succeeded"
    assert state.tasks["t0"].recovery is not None and state.tasks["t0"].recovery.kills == 1
    assert ex.calls["t0"] == 2


def test_stuck_factor_needs_three_samples_and_floor() -> None:
    from agent_orchestrator.models import SentinelRecoverySpec, TaskRunState

    spec = SentinelRecoverySpec(
        idle_kill_minutes=600,
        force=True,
        stuck_factor=3,
        stuck_min_minutes=20,
        stuck_activity_veto=False,  # these tests exercise the duration rule alone
    )
    now = datetime(2026, 11, 9, 12, tzinfo=UTC)

    def peer(i: int, minutes: float) -> TaskRunState:
        start = now - timedelta(hours=5)
        return TaskRunState(
            status="succeeded",
            agent="ag",
            started_at=start.isoformat(),
            ended_at=(start + timedelta(minutes=minutes)).isoformat(),
        )

    def state_with(peers: int, running_min: float) -> tuple[RunState, TaskRunState]:
        st = RunState(run_id="r", workflow_id="w", started_at="x", updated_at="x")
        for i in range(peers):
            st.tasks[f"p{i}"] = peer(i, 2)  # median 2 min -> 3x = 6 min
        me = TaskRunState(status="running", agent="ag", running_seconds=running_min * 60)
        me.last_activity_at = now.isoformat()
        st.tasks["me"] = me
        return st, me

    trig = sentinel_tick._trigger_for
    st, me = state_with(2, 30)
    assert trig(st, spec, "me", me, now) is None  # only 2 samples
    st, me = state_with(3, 30)
    assert trig(st, spec, "me", me, now) == "stuck_factor"
    st, me = state_with(3, 10)
    assert trig(st, spec, "me", me, now) is None  # > 3x median (6 min) but under the 20 min floor


def test_systemic_alarm_stops_kills(tmp_path: Path) -> None:
    store, rs, reposets, agents = _setup(tmp_path)
    ex = StallExecutor(stall_all=True)
    orch = Orchestrator(
        ex,
        store,
        rs,
        max_parallel=1,
        clock=StepClock(),
        sentinel=_settings(systemic_kill_alarm=2, max_recovery_kills_per_task=5),
    )
    wf = _wf()
    # After the 2nd kill the alarm trips: no further automatic kills, so the 3rd dispatch would
    # hang; release it by a manual kill (manual kills are never blocked by the alarm).
    state0 = rs.new_run(wf)
    run_dir = _run_dir(rs, state0)

    def releaser() -> None:
        assert _wait_for(lambda: ex.calls.get("t0", 0) >= 3)
        _write_entry(run_dir, "cli-kill-sysalarm", "kill", {"task_id": "t0", "no_retry": True})

    th = threading.Thread(target=releaser)
    th.start()
    state = orch.run(wf, reposets, agents, run_state=state0)
    th.join()
    sent = state.sentinel
    assert sent is not None and sent.systemic_alarm and sent.llm_paused
    mech = [k for k in sent.kill_log if k.source == "mechanical"]
    assert len(mech) == 2  # stopped after the alarm
    assert [k.source for k in sent.kill_log][-1] == "manual"
    alarms = (run_dir / "control" / sentinel_tick.ALARMS_FILE).read_text()
    assert '"kind": "systemic"' in alarms


def test_manual_kill_no_retry_fails_and_default_retries(tmp_path: Path) -> None:
    store, rs, reposets, agents = _setup(tmp_path)
    wf = _wf(2)
    wf.tasks[1].depends_on = ["t0"]  # sequential: a failing t1 must not abort a still-running t0
    state0 = rs.new_run(wf)
    run_dir = _run_dir(rs, state0)
    ex = StallExecutor({("t0", 1), ("t1", 1)})
    clock = StepClock()
    clock.now = datetime(2026, 11, 9, tzinfo=UTC)
    # Idle threshold far away: only manual kills act here.
    orch = Orchestrator(
        ex, store, rs, max_parallel=2, clock=clock, sentinel=_settings(idle_kill_minutes=100000)
    )

    def killer() -> None:
        assert _wait_for(lambda: ex.calls.get("t0"))
        _write_entry(run_dir, "cli-kill-t0plain1", "kill", {"task_id": "t0"})
        assert _wait_for(lambda: ex.calls.get("t1"))
        _write_entry(run_dir, "cli-kill-t1noretr", "kill", {"task_id": "t1", "no_retry": True})

    th = threading.Thread(target=killer)
    th.start()
    state = orch.run(wf, reposets, agents, run_state=state0)
    th.join()
    assert state.tasks["t0"].status == "succeeded" and ex.calls["t0"] == 2
    assert state.tasks["t1"].status == "failed" and ex.calls["t1"] == 1
    assert state.tasks["t1"].recovery is not None and state.tasks["t1"].recovery.no_retry


def test_kill_of_unknown_task_is_quarantined(tmp_path: Path) -> None:
    store, rs, reposets, agents = _setup(tmp_path)
    wf = _wf(1)
    state0 = rs.new_run(wf)
    run_dir = _run_dir(rs, state0)
    release = threading.Event()

    class Ex(StallExecutor):
        def execute(self, ctx: TaskContext) -> TaskResult:
            _write_entry(run_dir, "cli-kill-nothere1", "kill", {"task_id": "ghost"})
            rej = run_dir / "control" / "inbox-rejected" / "cli-kill-nothere1.json"
            assert _wait_for(rej.exists)
            release.set()
            return super().execute(ctx)

    state = Orchestrator(Ex(), store, rs, max_parallel=1, sentinel=_settings()).run(
        wf, reposets, agents, run_state=state0
    )
    assert release.is_set() and state.status == "succeeded"


def test_resume_mid_ladder_preserves_recovery_no_double_kill(tmp_path: Path) -> None:
    store, rs, reposets, agents = _setup(tmp_path)
    wf = _wf()
    state0 = rs.new_run(wf)
    ts = state0.tasks.setdefault(
        "t0", __import__("agent_orchestrator.models", fromlist=["x"]).TaskRunState()
    )
    ts.status = "killed"
    ts.recovery = __import__("agent_orchestrator.models", fromlist=["x"]).TaskRecovery(
        kills=1, last_reason="idle", last_source="mechanical"
    )
    sentinel_tick.ensure_state(state0).inbox_consumed.append("cli-kill-already1")
    rs.save(state0)
    run_dir = _run_dir(rs, state0)
    _write_entry(run_dir, "cli-kill-already1", "kill", {"task_id": "t0"})  # replayed, must not fire
    resumed = rs.prepare_resume(rs.load(state0.run_id), wf)
    assert resumed.tasks["t0"].recovery is not None and resumed.tasks["t0"].recovery.kills == 1
    ex = StallExecutor()
    state = Orchestrator(
        ex, store, rs, max_parallel=1, clock=StepClock(), sentinel=_settings()
    ).run(wf, reposets, agents, run_state=resumed)
    assert state.status == "succeeded"
    assert ex.calls["t0"] == 1  # redispatched once, not killed again
    assert state.tasks["t0"].recovery is not None and state.tasks["t0"].recovery.kills == 1
    assert state.sentinel is not None and len(state.sentinel.kill_log) == 0


def test_barrier_task_is_alarm_only(tmp_path: Path) -> None:
    from agent_orchestrator.models import SentinelRecoverySpec, TaskRunState

    spec = SentinelRecoverySpec(idle_kill_minutes=1, force=True)
    rt = sentinel_tick.SentinelRuntime(
        ceiling=1,
        tick_seconds=1,
        inbox_batch_max=5,
        live_limit=1,
        control_dir=tmp_path / "control",
        clock=StepClock(),
        log=__import__("logging").getLogger("t"),
        recovery=spec,
    )
    st = RunState(run_id="r", workflow_id="w", started_at="x", updated_at="x")
    sentinel_tick.ensure_state(st)
    now = datetime(2026, 11, 9, 12, tzinfo=UTC)
    st.tasks["b"] = TaskRunState(
        status="running", last_activity_at=(now - timedelta(hours=1)).isoformat()
    )
    rt.tokens["b"] = CancelToken()
    assert sentinel_tick.evaluate_recovery(st, rt, now, protected={"b"}) == []
    assert not rt.tokens["b"].is_set() and "b" in rt.alarmed
    spec.include_barriers = True
    assert sentinel_tick.evaluate_recovery(st, rt, now, protected={"b"}) == ["b"]


@pytest.mark.skipif(os.name != "posix", reason="process groups")
def test_real_subprocess_tree_killed_within_2s_no_orphans(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pidfile = tmp_path / "grandchild.pid"
    script = f"sleep 300 & echo $! > {pidfile}; sleep 301; wait"
    monkeypatch.setattr(
        claude_cli, "build_claude_argv", lambda agent, prompt: ["bash", "-c", script]
    )
    out = tmp_path / "out"
    token = CancelToken(grace_seconds=1)
    token.reason, token.source = "idle", "mechanical"
    ctx = TaskContext(
        run_id="r",
        task_id="t",
        agent=AgentSpec(executor="claude_cli"),
        instruction_path="i.md",
        input_paths=[],
        output_paths=[],
        repo_paths={},
        timeout_seconds=60,
        cwd=str(tmp_path),
        output_dir=str(out),
        cancel_event=token,
    )
    holder: dict[str, TaskResult] = {}
    th = threading.Thread(target=lambda: holder.setdefault("r", ClaudeCliExecutor().execute(ctx)))
    th.start()
    assert _wait_for(lambda: pidfile.exists() and pidfile.read_text().strip())
    gpid = int(pidfile.read_text())
    os.kill(gpid, 0)  # alive
    t0 = time.monotonic()
    token.set()
    th.join(10)
    elapsed = time.monotonic() - t0
    assert not th.is_alive() and elapsed < 2.0, elapsed
    res = holder["r"]
    assert res.status == "killed" and res.kill_reason == "idle" and res.kill_source == "mechanical"
    time.sleep(0.2)
    with pytest.raises(ProcessLookupError):
        os.kill(gpid, 0)
    assert subprocess.run(["pgrep", "-f", "sleep 301"], capture_output=True).returncode == 1


def test_disabled_sentinel_wires_no_cancel_hooks(tmp_path: Path) -> None:
    store, rs, reposets, agents = _setup(tmp_path)
    ex = FakeExecutor()
    state = Orchestrator(ex, store, rs, max_parallel=1).run(_wf(), reposets, agents)
    assert state.status == "succeeded"
    assert ex.contexts["t0"].cancel_event is None and ex.contexts["t0"].activity_path is None
    assert state.tasks["t0"].recovery is None and state.tasks["t0"].last_activity_at is None


def test_retried_task_gets_per_attempt_clock_no_second_stuck_kill() -> None:
    """T-Rk4Wn7 D1: kill -> retry is not re-killed within stuck_min_minutes of its own start."""
    from agent_orchestrator.models import SentinelRecoverySpec, TaskRecovery, TaskRunState

    spec = SentinelRecoverySpec(
        idle_kill_minutes=600,
        force=True,
        stuck_factor=3,
        stuck_min_minutes=20,
        stuck_activity_veto=False,  # these tests exercise the duration rule alone
    )
    t0 = datetime(2026, 11, 9, 12, tzinfo=UTC)  # fixed clock
    st = RunState(run_id="r", workflow_id="w", started_at="x", updated_at="x")
    for i in range(3):  # peer median 2 min -> threshold 6 min, floored at 20 min
        s = t0 - timedelta(hours=5)
        st.tasks[f"p{i}"] = TaskRunState(
            status="succeeded",
            agent="ag",
            started_at=s.isoformat(),
            ended_at=(s + timedelta(minutes=2)).isoformat(),
        )
    # First attempt began 25 min ago (legitimately stuck-killed), then was re-dispatched.
    st.tasks["me"] = TaskRunState(
        status="running", agent="ag", started_at=(t0 - timedelta(minutes=25)).isoformat()
    )
    rt = sentinel_tick.SentinelRuntime(
        ceiling=1,
        tick_seconds=1,
        inbox_batch_max=1,
        live_limit=1,
        control_dir=Path("."),
        clock=lambda: t0,
        log=logging.getLogger("t"),
        recovery=spec,
    )
    sentinel_tick.register_task(st, rt, "me", t0)  # recovery re-dispatch at t0
    st.tasks["me"].recovery = TaskRecovery(kills=1)
    rt.stat_size = lambda _p: 1

    for minutes in (0.25, 5, 19):  # polls inside the new attempt's first stuck_min_minutes
        now = t0 + timedelta(minutes=minutes)
        sentinel_tick.sample_liveness(st, rt, now)
        assert sentinel_tick.evaluate_recovery(st, rt, now, set()) == []
    now = t0 + timedelta(minutes=21)
    sentinel_tick.sample_liveness(st, rt, now)
    assert sentinel_tick._trigger_for(st, spec, "me", st.tasks["me"], now) == "stuck_factor"


def test_retry_cooldown_blocks_rapid_rekills() -> None:
    from agent_orchestrator.models import TaskRecovery, TaskRunState

    t0 = datetime(2026, 11, 9, 12, tzinfo=UTC)
    ts = TaskRunState(status="running", agent="ag")
    ts.attempt_started_at = t0.isoformat()
    ts.recovery = TaskRecovery(kills=1)
    assert sentinel_tick._in_retry_cooldown(ts, t0 + timedelta(seconds=15))
    assert not sentinel_tick._in_retry_cooldown(
        ts, t0 + timedelta(seconds=sentinel_tick.RETRY_COOLDOWN_SECONDS)
    )


# -- T-Rk4Wn7 D2: comparable cohort, activity veto, systemic dedupe ---------------------------

_T0 = datetime(2026, 11, 9, 12, tzinfo=UTC)


def _d2_rt(spec: SentinelRecoverySpec) -> sentinel_tick.SentinelRuntime:
    return sentinel_tick.SentinelRuntime(
        ceiling=1,
        tick_seconds=1,
        inbox_batch_max=1,
        live_limit=1,
        control_dir=Path("."),
        clock=lambda: _T0,
        log=logging.getLogger("t"),
        recovery=spec,
    )


def _d2_state(peer_cohort: str | None, me: dict[str, Any] | None = None) -> RunState:
    from agent_orchestrator.models import TaskRunState

    st = RunState(run_id="r", workflow_id="w", started_at="x", updated_at="x")
    for i in range(3):  # 2-minute peers -> threshold 6 min, floored by stuck_min 20 min
        s = _T0 - timedelta(hours=5)
        st.tasks[f"p{i}"] = TaskRunState(
            status="succeeded",
            agent="ag",
            stuck_cohort=peer_cohort,
            started_at=s.isoformat(),
            ended_at=(s + timedelta(minutes=2)).isoformat(),
        )
    st.tasks["me"] = TaskRunState(
        status="running",
        agent="ag",
        started_at=(_T0 - timedelta(minutes=30)).isoformat(),
        attempt_started_at=(_T0 - timedelta(minutes=30)).isoformat(),
        running_seconds=30 * 60.0,
        **(me or {}),
    )
    return st


def _stuck(st: RunState, spec: SentinelRecoverySpec, last_activity_min_ago: float | None) -> Any:
    ts = st.tasks["me"]
    if last_activity_min_ago is not None:
        ts.last_activity_at = (_T0 - timedelta(minutes=last_activity_min_ago)).isoformat()
    return sentinel_tick._trigger_for(st, spec, "me", ts, _T0)


_SPEC = dict(idle_kill_minutes=600, force=True, stuck_factor=3, stuck_min_minutes=20)


def test_peer_cohort_is_agent_and_tag() -> None:
    spec = SentinelRecoverySpec(**_SPEC, stuck_activity_veto=False)
    # Untagged task vs untagged peers: legacy behaviour, killed.
    assert _stuck(_d2_state(None), spec, None) == sentinel_tick.REASON_STUCK
    # Tagged task vs untagged short peers: no comparable cohort -> no baseline -> not killed.
    assert _stuck(_d2_state(None, {"stuck_cohort": "rust-sweep"}), spec, None) is None
    # Tagged task vs same-tag short peers: comparable and over threshold -> killed.
    st = _d2_state("rust-sweep", {"stuck_cohort": "rust-sweep"})
    assert _stuck(st, spec, None) == sentinel_tick.REASON_STUCK
    # Tagged peers are not a baseline for an untagged task either.
    assert _stuck(_d2_state("rust-sweep"), spec, None) is None


def test_explicit_expected_minutes_and_no_stuck_kill() -> None:
    spec = SentinelRecoverySpec(**_SPEC, stuck_activity_veto=False)
    # expected 60 min -> threshold 180 min; 30 min run is fine despite 2-minute peers.
    assert _stuck(_d2_state(None, {"expected_minutes": 60}), spec, None) is None
    # expected 5 min -> threshold 15 min -> killed even with no peers at all.
    st = _d2_state(None, {"expected_minutes": 5})
    for k in ("p0", "p1", "p2"):
        del st.tasks[k]
    assert _stuck(st, spec, None) == sentinel_tick.REASON_STUCK
    assert _stuck(_d2_state(None, {"no_stuck_kill": True}), spec, None) is None


def test_recent_activity_vetoes_stuck_kill() -> None:
    spec = SentinelRecoverySpec(**_SPEC)  # veto on by default; window = idle_kill_minutes (600)
    assert _stuck(_d2_state(None), spec, 1) is None  # output 1 min ago -> vetoed
    assert _stuck(_d2_state(None), spec, 601) == sentinel_tick.REASON_IDLE  # idle fires itself
    off = SentinelRecoverySpec(**_SPEC, stuck_activity_veto=False)
    assert _stuck(_d2_state(None), off, 1) == sentinel_tick.REASON_STUCK
    short = SentinelRecoverySpec(**_SPEC, stuck_veto_minutes=5)
    assert _stuck(_d2_state(None), short, 10) == sentinel_tick.REASON_STUCK  # outside window


def test_register_task_copies_stuck_hints_from_spec() -> None:
    st = _d2_state(None)
    spec = SentinelRecoverySpec(**_SPEC)
    task = TaskSpec(
        id="me",
        agent="ag",
        instruction="i",
        stuck_cohort="c",
        expected_minutes=7,
        no_stuck_kill=True,
    )
    sentinel_tick.register_task(st, _d2_rt(spec), "me", _T0, task=task)
    ts = st.tasks["me"]
    assert (ts.stuck_cohort, ts.expected_minutes, ts.no_stuck_kill) == ("c", 7, True)


def test_repeat_kills_of_one_task_are_one_systemic_incident() -> None:
    spec = SentinelRecoverySpec(systemic_kill_alarm=3, max_recovery_kills_per_task=5)
    st = _d2_state(None)
    rt = _d2_rt(spec)
    for i in range(3):  # three kills of ONE task inside 32 s (the live incident)
        sentinel_tick.settle_kill(
            st, rt, "me", "stuck_factor", "mechanical", False, _T0 + timedelta(seconds=16 * i)
        )
    assert st.sentinel is not None and not st.sentinel.systemic_alarm
    # Kills of three distinct tasks still trip it.
    for t in ("p0", "p1"):
        sentinel_tick.settle_kill(
            st, rt, t, "idle", "mechanical", False, _T0 + timedelta(minutes=1)
        )
    assert st.sentinel.systemic_alarm


def test_systemic_dedupe_window_zero_restores_old_counting() -> None:
    spec = SentinelRecoverySpec(
        systemic_kill_alarm=3, max_recovery_kills_per_task=5, systemic_task_window_seconds=0
    )
    st = _d2_state(None)
    rt = _d2_rt(spec)
    for i in range(3):
        sentinel_tick.settle_kill(
            st, rt, "me", "stuck_factor", "mechanical", False, _T0 + timedelta(seconds=16 * i)
        )
    assert st.sentinel is not None and st.sentinel.systemic_alarm
