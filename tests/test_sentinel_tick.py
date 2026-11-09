"""T-Sn3Tck: sentinel main-loop tick, control inbox, live overrides, metrics, fail-safe."""

from __future__ import annotations

import json
import logging
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
from agent_orchestrator.executors.fake import FakeExecutor
from agent_orchestrator.models import (
    AgentSpec,
    RepoRef,
    RepoSet,
    RunState,
    SentinelSpec,
    TaskContext,
    TaskResult,
    TaskSpec,
    WorkflowSpec,
)
from agent_orchestrator.runstate import RunStateStore
from agent_orchestrator.sentinel_settings import SentinelSettings, resolve_sentinel_settings

WAIT_S = 15.0


def _settings(**kw: Any) -> SentinelSettings:
    return resolve_sentinel_settings(workflow=SentinelSpec(enabled=True, **kw))


def _wf(n: int) -> WorkflowSpec:
    tasks = [
        TaskSpec(id=f"t{i}", agent="ag", instruction="specs/examples/instructions/design.md")
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


def _write_entry(
    run_dir: Path, entry_id: str, kind: str, payload: dict | None = None, **kw: Any
) -> Path:
    inbox = run_dir / "control" / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    body = {
        "schema": "ao.ctl/v1",
        "id": entry_id,
        "kind": kind,
        "actor": "cli:tester",
        "created_at": "2026-11-09T00:00:00+00:00",
        "payload": payload or {},
        **kw,
    }
    tmp = inbox / f"{entry_id}.json.tmp"
    tmp.write_text(json.dumps(body))
    final = inbox / f"{entry_id}.json"
    tmp.replace(final)
    return final


def _wait_for(cond: Any) -> bool:
    end = time.monotonic() + WAIT_S
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


class ScriptedExecutor(FakeExecutor):
    """Tracks concurrency; ``hooks[task_id](ctx)`` runs inside the worker thread."""

    def __init__(self, hooks: dict[str, Any] | None = None) -> None:
        super().__init__()
        self.hooks = hooks or {}
        self.lock = threading.Lock()
        self.running = 0
        self.max_running = 0

    def execute(self, ctx: TaskContext) -> TaskResult:
        with self.lock:
            self.running += 1
            self.max_running = max(self.max_running, self.running)
        try:
            hook = self.hooks.get(ctx.task_id)
            if hook is not None:
                hook(ctx)
            return super().execute(ctx)
        finally:
            with self.lock:
                self.running -= 1


@pytest.fixture(autouse=True)
def _fast_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    """Real ``wait`` but with the (>= 1 s) tick timeout capped so tests stay quick."""
    real = engine_mod.wait

    def fast(fs: Any, timeout: float | None = None, return_when: Any = None) -> Any:
        return real(fs, timeout=0.02 if timeout is not None else None, return_when=return_when)

    monkeypatch.setattr(engine_mod, "wait", fast)


class _Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 11, 9, tzinfo=UTC)
        self.calls = 0
        self.lock = threading.Lock()

    def __call__(self) -> datetime:
        with self.lock:
            self.calls += 1
            self.now += timedelta(seconds=1)
            return self.now


def _run_dir(rs: RunStateStore, state: RunState) -> Path:
    return rs._path(state.run_id).parent


def test_tick_runs_while_all_workers_blocked(tmp_path: Path) -> None:
    store, rs, reposets, agents = _setup(tmp_path)
    clock = _Clock()
    release = threading.Event()
    ticks: list[int] = []

    def block(ctx: TaskContext) -> None:
        # Both workers are parked; the only thing that can advance the clock is the tick.
        _wait_for(lambda: clock.calls >= 12)
        ticks.append(clock.calls)
        release.set()

    ex = ScriptedExecutor({"t0": block, "t1": block})
    orch = Orchestrator(
        ex, store, rs, max_parallel=2, clock=clock, sentinel=_settings(tick_seconds=1)
    )
    state = orch.run(_wf(2), reposets, agents)
    assert state.status == "succeeded"
    assert ex.max_running == 2
    assert ticks and min(ticks) >= 12  # >= 12 clock reads happened with both workers blocked
    assert state.sentinel is not None and state.sentinel.last_tick_at is not None
    assert state.metrics is not None and state.metrics.slot_seconds_busy > 0


def test_set_max_parallel_up_to_ceiling(tmp_path: Path) -> None:
    store, rs, reposets, agents = _setup(tmp_path)
    state0 = rs.new_run(_wf(3))
    run_dir = _run_dir(rs, state0)
    all3 = threading.Event()

    def first(ctx: TaskContext) -> None:
        _write_entry(run_dir, "cli-set-000001", "set", {"values": {"max_parallel": 3}})
        assert all3.wait(WAIT_S)

    def others(ctx: TaskContext) -> None:
        if ex.running == 3:
            all3.set()
        assert all3.wait(WAIT_S)

    ex = ScriptedExecutor({"t0": first, "t1": others, "t2": others})
    orch = Orchestrator(
        ex, store, rs, max_parallel=1, sentinel=_settings(tick_seconds=1, max_parallel_ceiling=3)
    )
    state = orch.run(_wf(3), reposets, agents, run_state=state0)
    assert state.status == "succeeded"
    assert ex.max_running == 3
    assert state.sentinel is not None
    assert state.sentinel.overrides["max_parallel"].value == 3
    assert state.sentinel.inbox_consumed == ["cli-set-000001"]
    assert state.effective_settings["sentinel.max_parallel"].source == "live-override"
    assert (run_dir / "control" / "inbox-processed" / "cli-set-000001.json").exists()
    assert not (run_dir / "control" / "inbox" / "cli-set-000001.json").exists()


def test_set_above_ceiling_is_quarantined(tmp_path: Path) -> None:
    store, rs, reposets, agents = _setup(tmp_path)
    state0 = rs.new_run(_wf(2))
    run_dir = _run_dir(rs, state0)
    rej = run_dir / "control" / "inbox-rejected"

    def first(ctx: TaskContext) -> None:
        _write_entry(run_dir, "cli-set-000002", "set", {"values": {"max_parallel": 9}})
        assert _wait_for(lambda: (rej / "cli-set-000002.reason.json").exists())

    ex = ScriptedExecutor({"t0": first})
    orch = Orchestrator(
        ex, store, rs, max_parallel=1, sentinel=_settings(tick_seconds=1, max_parallel_ceiling=3)
    )
    state = orch.run(_wf(2), reposets, agents, run_state=state0)
    assert state.status == "succeeded"
    assert ex.max_running == 1  # limit never raised
    assert json.loads((rej / "cli-set-000002.reason.json").read_text())["rule_id"] == "CTL-CEILING"
    assert (rej / "cli-set-000002.json").exists()
    assert state.sentinel is not None and "max_parallel" not in state.sentinel.overrides


def test_inbox_rules_tmp_size_json_dup_and_replay(tmp_path: Path) -> None:
    store, rs, reposets, agents = _setup(tmp_path)
    state0 = rs.new_run(_wf(2))
    run_dir = _run_dir(rs, state0)
    inbox = run_dir / "control" / "inbox"
    rej = run_dir / "control" / "inbox-rejected"
    # Crash replay: id already consumed (effect persisted), file still present => no 2nd effect.
    sentinel_tick.ensure_state(state0).inbox_consumed.append("cli-set-replay1")
    rs.save(state0)
    _write_entry(run_dir, "cli-set-replay1", "set", {"values": {"max_parallel": 3}})
    (inbox / "cli-note-halfwri.json.tmp").write_text("{not json")  # writer mid-rename: ignored
    big = inbox / "cli-note-oversize.json"
    big.write_text(" " * (sentinel_tick.INBOX_MAX_BYTES + 1))
    (inbox / "cli-note-badjson1.json").write_text("{nope")
    _write_entry(run_dir, "cli-kill-notyet01", "release_backlog", {}, rationale="x")
    _write_entry(run_dir, "cli-note-good0001", "note", {"text": "hello"})

    def first(ctx: TaskContext) -> None:
        assert _wait_for(lambda: (rej / "cli-kill-notyet01.reason.json").exists())

    ex = ScriptedExecutor({"t0": first})
    orch = Orchestrator(
        ex, store, rs, max_parallel=1, sentinel=_settings(tick_seconds=1, max_parallel_ceiling=3)
    )
    state = orch.run(_wf(2), reposets, agents, run_state=state0)

    def rule(name: str) -> str:
        return json.loads((rej / f"{name}.reason.json").read_text())["rule_id"]

    assert rule("cli-note-oversize") == "CTL-SIZE"
    assert rule("cli-note-badjson1") == "CTL-JSON"
    assert rule("cli-kill-notyet01") == "not_enabled_yet"
    assert (inbox / "cli-note-halfwri.json.tmp").exists()  # untouched
    assert ex.max_running == 1  # replayed set did NOT take effect
    assert state.sentinel is not None
    assert "max_parallel" not in state.sentinel.overrides
    assert state.sentinel.inbox_consumed.count("cli-set-replay1") == 1
    assert [n.text for n in state.sentinel.notes] == ["hello"]
    assert (run_dir / "control" / "inbox-processed" / "cli-set-replay1.json").exists()


def test_pause_resume_llm_hold_and_cancel_kinds(tmp_path: Path) -> None:
    store, rs, reposets, agents = _setup(tmp_path)
    state0 = rs.new_run(_wf(2))
    run_dir = _run_dir(rs, state0)
    seen: dict[str, Any] = {}

    def first(ctx: TaskContext) -> None:
        _write_entry(run_dir, "cli-pause-0001", "pause_llm")
        _write_entry(run_dir, "cli-hold-00001", "request_hold", rationale="look at this")
        assert _wait_for(lambda: "cli-hold-00001" in _consumed(rs, state0))
        _write_entry(run_dir, "cli-cancel-001", "cancel")
        assert _wait_for(lambda: "cli-cancel-001" in _consumed(rs, state0))

    def _consumed(rs_: RunStateStore, s: RunState) -> list[str]:
        loaded = rs_.load(s.run_id)
        return loaded.sentinel.inbox_consumed if loaded.sentinel else []

    ex = ScriptedExecutor({"t0": first})
    orch = Orchestrator(ex, store, rs, max_parallel=1, sentinel=_settings(tick_seconds=1))
    state = orch.run(_wf(2), reposets, agents, run_state=state0)
    assert state.status == "cancelled"  # graceful cancel wired through the inbox
    assert state.sentinel is not None
    assert state.sentinel.llm_paused is True
    assert state.sentinel.hold_requested is True
    assert state.sentinel.hold_reason == "look at this"
    assert "t1" not in state.tasks or state.tasks["t1"].status != "succeeded"
    del seen


def test_resume_restores_overrides(tmp_path: Path) -> None:
    store, rs, reposets, agents = _setup(tmp_path)
    state0 = rs.new_run(_wf(3))
    sent = sentinel_tick.ensure_state(state0)
    sent.overrides["max_parallel"] = sentinel_tick.SentinelOverride(value=3, actor="cli:x", at="t")
    ex = ScriptedExecutor()
    orch = Orchestrator(
        ex, store, rs, max_parallel=1, sentinel=_settings(tick_seconds=1, max_parallel_ceiling=3)
    )
    state = orch.run(_wf(3), reposets, agents, run_state=state0)
    assert state.effective_settings["sentinel.max_parallel"].source == "live-override"


def test_tick_exception_runs_as_if_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    store, rs, reposets, agents = _setup(tmp_path)

    def boom(*a: Any, **k: Any) -> Any:
        raise RuntimeError("injected")

    monkeypatch.setattr(sentinel_tick, "drain_inbox", boom)
    ex = ScriptedExecutor()
    orch = Orchestrator(
        ex, store, rs, max_parallel=1, sentinel=_settings(tick_seconds=1, max_parallel_ceiling=3)
    )
    with caplog.at_level(logging.ERROR):
        state = orch.run(_wf(3), reposets, agents)
    assert state.status == "succeeded"
    assert all(t.status == "succeeded" for t in state.tasks.values())
    errors = [r for r in caplog.records if getattr(r, "event", "") == "sentinel.error"]
    assert len(errors) == 1
    assert state.sentinel is not None
    assert state.sentinel.enabled is False and "injected" in (state.sentinel.disabled_reason or "")


def test_disabled_is_inert(tmp_path: Path) -> None:
    runs = []
    for name, sentinel in (("none", None), ("off", resolve_sentinel_settings())):
        sub = tmp_path / name
        sub.mkdir()
        store, rs, reposets, agents = _setup(sub)
        ex = ScriptedExecutor()
        state = Orchestrator(ex, store, rs, max_parallel=2, sentinel=sentinel).run(
            _wf(4), reposets, agents
        )
        assert state.sentinel is None and state.metrics is None
        assert not (_run_dir(rs, state) / "control").exists()
        runs.append(sorted(t.status for t in state.tasks.values()))
    assert runs[0] == runs[1] == ["succeeded"] * 4


def test_metrics_on_scripted_timeline(tmp_path: Path) -> None:
    state = RunState(run_id="r", workflow_id="w", started_at="x", updated_at="x")
    sentinel_tick.ensure_state(state)
    clock = _Clock()
    rt = sentinel_tick.SentinelRuntime(
        ceiling=4,
        tick_seconds=1,
        inbox_batch_max=50,
        live_limit=4,
        control_dir=tmp_path / "control",
        clock=clock,
        log=logging.getLogger("t"),
    )
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    sentinel_tick.update_metrics(state, rt, t0)  # first sample only anchors the timeline
    rt.occ_running, rt.occ_ready = 2, 1  # 2 busy, 1 idle-with-ready, 1 idle-no-ready
    sentinel_tick.update_metrics(state, rt, t0 + timedelta(seconds=10))
    rt.occ_running, rt.occ_ready = 4, 0  # applies to the NEXT interval
    sentinel_tick.update_metrics(state, rt, t0 + timedelta(seconds=20))
    m = state.metrics
    assert m is not None
    assert m.slot_seconds_busy == pytest.approx(2 * 10 + 4 * 10)
    assert m.slot_seconds_idle_with_ready == pytest.approx(1 * 10)
    assert m.slot_seconds_idle_no_ready == pytest.approx(1 * 10)
    assert m.utilisation_pct == pytest.approx(75.0)
    # no time passed / clock went backwards => no change
    sentinel_tick.update_metrics(state, rt, t0 + timedelta(seconds=20))
    assert m.slot_seconds_busy == pytest.approx(60.0)


@pytest.mark.slow
def test_tick_cost_at_1k_tasks(tmp_path: Path) -> None:
    store, rs, reposets, agents = _setup(tmp_path)
    orch = Orchestrator(
        ScriptedExecutor(), store, rs, sentinel=_settings(tick_seconds=1, max_parallel_ceiling=4)
    )
    state = rs.new_run(_wf(1000))
    rt = orch._start_sentinel(state, str(_run_dir(rs, state)), logging.getLogger("t"))
    assert rt is not None
    t = time.perf_counter()
    for _ in range(20):
        assert orch._sentinel_tick(rt, state) is rt
    assert (time.perf_counter() - t) / 20 < 0.05
