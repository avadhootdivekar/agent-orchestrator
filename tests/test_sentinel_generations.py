"""T-Sn8Gen: LLM generations (pack, memory, handoff, rotation, cost, budget, resume)."""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from agent_orchestrator import engine as engine_mod
from agent_orchestrator import sentinel_gen as sg
from agent_orchestrator import sentinel_tick
from agent_orchestrator.artifacts import LocalFsArtifactStore
from agent_orchestrator.engine import Orchestrator
from agent_orchestrator.executors.fake import FakeExecutor
from agent_orchestrator.models import (
    AgentSpec,
    RepoRef,
    RepoSet,
    RunState,
    SentinelLlmSpec,
    SentinelSpec,
    SentinelState,
    TaskContext,
    TaskResult,
    TaskRunState,
    TaskSpec,
    WorkflowSpec,
    compute_run_usage_totals,
)
from agent_orchestrator.runstate import RunStateStore
from agent_orchestrator.sentinel_driver import (
    DriverStatus,
    GenerationLimits,
    Handle,
    SentinelDriver,
)
from agent_orchestrator.sentinel_settings import SentinelSettings, resolve_sentinel_settings
from agent_orchestrator.usage import aggregate_usage

T0 = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)


class ScriptedDriver(SentinelDriver):
    """Scripted fake: per-generation behaviour, writes the files a real generation would."""

    def __init__(self, script: dict[int, dict[str, Any]] | None = None) -> None:
        self.script = script or {}
        self.started: list[int] = []
        self.packs: dict[int, dict[str, bytes]] = {}
        self.stopped: list[int] = []
        self.lock = threading.Lock()

    def _plan(self, gen: int) -> dict[str, Any]:
        return {"polls": 1, "rc": 0, "cost": 0.25, "handoff": True, **self.script.get(gen, {})}

    def start(self, pack_dir: str, limits: GenerationLimits) -> Handle:
        gen = limits.generation
        plan = self._plan(gen)
        pack = Path(pack_dir)
        with self.lock:
            self.started.append(gen)
            self.packs[gen] = {p.name: p.read_bytes() for p in pack.iterdir()}
        gdir = pack.parent
        if plan.get("memory"):
            (gdir / "memory-append.md").write_text(plan["memory"])
        (gdir / "report.md").write_text(f"report g{gen}\n")
        for pid, kind, payload in plan.get("proposals", []):
            self._propose(Path(limits.inbox_dir), gen, pid, kind, payload)
        if plan["handoff"] and not plan.get("late_handoff"):
            self._write_handoff(gdir, gen, plan)
        return Handle(gen, pack_dir, {"plan": plan, "polls": 0, "stopped": False, "gdir": gdir})

    @staticmethod
    def _propose(inbox: Path, gen: int, pid: str, kind: str, payload: dict[str, Any]) -> None:
        inbox.mkdir(parents=True, exist_ok=True)
        body = {
            "schema": "ao.ctl/v1",
            "id": pid,
            "kind": kind,
            "actor": f"sentinel-g{gen:02d}",
            "created_at": T0.isoformat(),
            "payload": payload,
        }
        (inbox / f"{pid}.json.tmp").write_text(json.dumps(body))
        (inbox / f"{pid}.json.tmp").replace(inbox / f"{pid}.json")

    @staticmethod
    def _write_handoff(gdir: Path, gen: int, plan: dict[str, Any]) -> None:
        doc = {
            "schema": "ao.sentinel.handoff/v1",
            "generation": gen,
            "ended_reason": "explicit_handoff",
            "summary": f"summary of g{gen}",
            "watch_list": [],
            "do_not_repeat": [f"dnr-{gen}"],
            "open_directives": [],
            **plan.get("handoff_extra", {}),
        }
        (gdir / "handoff.json.tmp").write_text(json.dumps(doc))
        (gdir / "handoff.json.tmp").replace(gdir / "handoff.json")

    def poll(self, handle: Handle) -> DriverStatus:
        d = handle.data
        plan = d["plan"]
        if d["stopped"]:
            return DriverStatus(alive=False, returncode=-15, cost_usd=plan["cost"] / 2)
        if plan.get("hang"):
            return DriverStatus(alive=True, tokens_used=plan.get("tokens", 0))
        d["polls"] += 1
        if d["polls"] <= plan["polls"]:
            if d["polls"] == plan["polls"] and plan.get("late_handoff"):
                self._write_handoff(d["gdir"], handle.generation, plan)
            return DriverStatus(alive=True, tokens_used=plan.get("tokens", 0))
        return DriverStatus(
            alive=False,
            returncode=plan["rc"],
            cost_usd=plan["cost"],
            input_tokens=100,
            output_tokens=50,
            tokens_used=150,
            error=None if plan["rc"] == 0 else "boom",
        )

    def stop(self, handle: Handle) -> None:
        handle.data["stopped"] = True
        self.stopped.append(handle.generation)


# ---------------------------------------------------------------------------------------------
# Pure units: pack, memory, handoff
# ---------------------------------------------------------------------------------------------


def _state() -> RunState:
    st = RunState(
        run_id="r1", workflow_id="wf", started_at=T0.isoformat(), updated_at=T0.isoformat()
    )
    st.sentinel = SentinelState()
    st.tasks["t1"] = TaskRunState(status="running", running_seconds=12.5)
    st.tasks["t2"] = TaskRunState(status="succeeded")
    return st


def _pack(run_dir: Path, st: RunState, gen: int = 1) -> dict[str, bytes]:
    assert st.sentinel is not None
    return sg.build_pack(
        st,
        st.sentinel,
        sg.PackInputs(
            generation=gen,
            now=T0,
            triggers=["heartbeat"],
            contract=sg.render_contract(sentinel_tick.KINDS_ENABLED, {"ttl": 1}),
            run_dir=run_dir,
        ),
    )


def test_pack_is_byte_deterministic_under_fixed_clock(tmp_path: Path) -> None:
    (tmp_path / "sentinel").mkdir()
    (tmp_path / "sentinel" / "memory.md").write_text("- [g01] remember x\n")
    (tmp_path / "run.log").write_text(
        "\n".join(json.dumps({"event": f"e{i}", "n": i}) for i in range(100)) + "\nnot json\n"
    )
    a, b = _pack(tmp_path, _state()), _pack(tmp_path, _state())
    assert a == b
    assert list(a) == sorted(a)  # stable order
    manifest = json.loads(a["manifest.json"])
    digests = {f["name"]: f["sha256"] for f in manifest["files"]}
    assert digests["digest.json"] == hashlib.sha256(a["digest.json"]).hexdigest()
    # Only the last K events, oldest first.
    ev = a["events.jsonl"].decode().splitlines()
    assert len(ev) == sg.EVENTS_LAST_K and json.loads(ev[-1])["n"] == 99
    # A different clock changes the bytes (the clock is an input, not ambient).
    st = _state()
    assert st.sentinel is not None
    other = sg.build_pack(
        st,
        st.sentinel,
        sg.PackInputs(1, T0 + timedelta(seconds=1), ["heartbeat"], "c", tmp_path),
    )
    assert other["digest.json"] != a["digest.json"]


def test_pack_caps_enforced_and_flagged(tmp_path: Path) -> None:
    run = tmp_path
    (run / "outputs").mkdir()
    (run / "outputs" / "charter.json").write_bytes(b"x" * 500_000)
    (run / "control").mkdir()
    (run / "control" / "backlog.json").write_bytes(b"b" * 500_000)
    (run / "sentinel").mkdir()
    (run / "sentinel" / "memory.md").write_bytes(("- [g01] " + "m" * 100 + "\n").encode() * 3000)
    (run / "run.log").write_text(
        "".join(json.dumps({"event": "e", "pad": "p" * 2000}) + "\n" for _ in range(100))
    )
    st = _state()
    assert st.sentinel is not None
    st.sentinel.last_handoff_path = "sentinel/g01/handoff.json"
    (run / "sentinel" / "g01").mkdir()
    (run / "sentinel" / "g01" / "handoff.json").write_bytes(b"h" * 100_000)
    pack = _pack(run, st, gen=2)
    assert sum(len(v) for v in pack.values()) <= sg.PACK_MAX_BYTES
    for name, data in pack.items():
        assert len(data) <= sg.FILE_CAPS[name], name
    manifest = {f["name"]: f for f in json.loads(pack["manifest.json"])["files"]}
    for name in ("charter.json", "backlog.json", "memory.md", "previous-handoff.json"):
        assert manifest[name]["truncated"] is True, name
    assert manifest["digest.json"]["truncated"] is False
    assert b"truncated by pack assembler" in pack["charter.json"]
    # events keep the *tail* (most recent), the others the head.
    assert len(pack["events.jsonl"]) <= sg.FILE_CAPS["events.jsonl"]


def test_cut_respects_utf8_boundary() -> None:
    data = ("é" * 5000).encode()
    for keep in ("head", "tail"):
        out, was = sg._cut(data, 1001, keep)
        assert was and len(out) <= 1001
        out.decode("utf-8")


def test_memory_merge_pins_expiry_dedupe_cap() -> None:
    existing = (
        "- [g01][pin] always keep this\n- [g01][exp:g2] short lived\n- [g01] plain old\n"
        "garbage line\n"
    )
    append = (
        "- [pin] new pinned\n- [exp:+1] expires after g4\n- Plain OLD\n* star bullet\n"
        "not a bullet\n"
    )
    merged = sg.merge_memory(existing, append, generation=3)
    items = {i.text: i for i in sg.parse_memory(merged)}
    assert "short lived" not in items  # expired (g2 < 3)
    assert items["always keep this"].pinned
    assert items["new pinned"].pinned and items["new pinned"].seq == 3
    assert items["expires after g4"].expires == 4
    assert items["Plain OLD"].seq == 3 and "plain old" not in items  # dedupe, newest wins
    assert "star bullet" in items and "not a bullet" not in items
    # deterministic
    assert merged == sg.merge_memory(existing, append, generation=3)
    # cap: oldest unpinned evicted first, pinned survive
    big = "".join(f"- item {i} " + "z" * 200 + "\n" for i in range(50))
    capped = sg.merge_memory("- [g01][pin] keep\n", big, generation=2, max_bytes=2048)
    assert len(capped.encode()) <= 2048
    texts = [i.text for i in sg.parse_memory(capped)]
    assert "keep" in texts and any(t.startswith("item 49 ") for t in texts)
    assert not any(t.startswith("item 0 ") for t in texts)
    # appended line count/length bounds
    many = "".join(f"- l{i}\n" for i in range(500))
    assert len(sg.parse_memory_append(many, 1)) == sg.MEMORY_APPEND_MAX_LINES
    assert len(sg.parse_memory_append("- " + "w " * 1000, 1)[0].text) <= sg.MEMORY_LINE_MAX_CHARS


def test_handoff_validation(tmp_path: Path) -> None:
    good = {
        "schema": "ao.sentinel.handoff/v1",
        "generation": 2,
        "ended_reason": "ttl",
        "summary": "s",
        "watch_list": [{"task_id": "t", "concern": "c", "extra_ok": 1}],
        "do_not_repeat": [],
        "open_directives": [],
    }
    p = tmp_path / "handoff.json"
    assert sg.validate_handoff(p, 2).error == "missing"
    p.write_text(json.dumps(good))
    assert sg.validate_handoff(p, 2).handoff is not None
    assert "generation" in (sg.validate_handoff(p, 3).error or "")
    for bad in (
        {**good, "ended_reason": "nope"},
        {**good, "surprise": 1},
        {**good, "summary": "x" * 4001},
        {k: v for k, v in good.items() if k != "watch_list"},
    ):
        p.write_text(json.dumps(bad))
        assert sg.validate_handoff(p, 2).handoff is None
    p.write_text("{not json")
    assert (sg.validate_handoff(p, 2).error or "").startswith("invalid")
    p.write_bytes(b" " * (sg.HANDOFF_MAX_BYTES + 1))
    assert "too large" in (sg.validate_handoff(p, 2).error or "")


def test_ledger_is_hash_chained(tmp_path: Path) -> None:
    for i in range(3):
        sg.append_ledger(tmp_path, f"t{i}", "ev", {"i": i})
    recs = [json.loads(x) for x in (tmp_path / sg.LEDGER_FILE).read_text().splitlines()]
    assert [r["seq"] for r in recs] == [0, 1, 2]
    assert recs[1]["prev"] == recs[0]["hash"] and recs[0]["prev"] == ""
    body = {k: recs[2][k] for k in ("seq", "at", "event", "data", "prev")}
    assert recs[2]["hash"] == hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


def test_contract_rendered_from_schemas() -> None:
    text = sg.render_contract(sentinel_tick.KINDS_ENABLED, {"generation_ttl_minutes": 60})
    assert "ao.sentinel.handoff/v1" in text and "`note`" in text and "`kill`" in text
    assert "`directive`" not in text  # only kinds the engine accepts today
    assert "generation_ttl_minutes" in text


# ---------------------------------------------------------------------------------------------
# Lane (no engine): triggers, cooldown, caps, budget, TTL, tokens, ungraceful
# ---------------------------------------------------------------------------------------------


def _lane(tmp_path: Path, driver: ScriptedDriver, **llm: Any) -> tuple[sg.GenerationLane, RunState]:
    spec = SentinelLlmSpec(
        enabled=True,
        heartbeat_minutes=1,
        trigger_cooldown_minutes=0,
        max_generations_per_hour=100,
        **llm,
    )
    lane = sg.GenerationLane(
        spec=spec,
        driver=driver,
        run_dir=tmp_path,
        enabled_kinds=sentinel_tick.KINDS_ENABLED,
        run_cost_cap_usd=None,
        log=logging.getLogger("test"),
    )
    return lane, _state()


def _drive(
    lane: sg.GenerationLane, st: RunState, start: datetime, steps: int, dt: int = 61
) -> datetime:
    now = start
    for _ in range(steps):
        now += timedelta(seconds=dt)
        lane.tick(st, now)
        _wait_start(lane)
    return now


def _wait_start(lane: sg.GenerationLane) -> None:
    if lane._starting is not None:  # the lane executor runs driver.start off-thread
        try:
            lane._starting.result(timeout=5)
        except Exception:  # noqa: BLE001 -- start failures are the lane's to record
            pass


def test_heartbeat_starts_generation_and_cost_row(tmp_path: Path) -> None:
    drv = ScriptedDriver()
    lane, st = _lane(tmp_path, drv)
    assert lane.tick(st, T0) is False  # nothing due yet
    _drive(lane, st, T0, 6)
    assert drv.started and drv.started[0] == 1
    row = st.tasks["sentinel-g01"]
    assert row.status == "succeeded" and row.cumulative_cost_usd == 0.25
    assert row.agent == "sentinel" and row.dispatch_cycle == 1
    assert (tmp_path / "sentinel" / "g01" / "pack" / "manifest.json").is_file()
    assert (tmp_path / "sentinel" / "g01" / "handoff.json").is_file()
    assert st.sentinel is not None
    assert (st.sentinel.last_handoff_path or "").startswith("sentinel/g0")
    lane.close()


def test_trigger_cooldown_and_hour_cap(tmp_path: Path) -> None:
    drv = ScriptedDriver({n: {"polls": 0} for n in range(1, 50)})
    spec = SentinelLlmSpec(
        enabled=True, heartbeat_minutes=1, trigger_cooldown_minutes=10, max_generations_per_hour=2
    )
    lane = sg.GenerationLane(
        spec=spec,
        driver=drv,
        run_dir=tmp_path,
        enabled_kinds=sentinel_tick.KINDS_ENABLED,
        run_cost_cap_usd=None,
        log=logging.getLogger("t"),
    )
    st = _state()
    # every 61 s for 2 hours: heartbeat cooldown 10 min => <= 1 gen / 10 min; hour cap => 2 / h
    _drive(lane, st, T0, 120)
    starts = [datetime.fromisoformat(s) for s in (st.sentinel.gen_starts if st.sentinel else [])]
    assert len(drv.started) <= 4  # 2 per hour over ~2 h
    assert len(drv.started) >= 3
    assert len([s for s in starts if (starts[-1] - s).total_seconds() < 3600]) <= 2
    lane.close()


def test_human_note_and_failed_unit_triggers(tmp_path: Path) -> None:
    from agent_orchestrator.models import SentinelNote

    drv = ScriptedDriver({n: {"polls": 0} for n in range(1, 9)})
    lane, st = _lane(tmp_path, drv)
    st.started_at = (T0 + timedelta(days=1)).isoformat()  # suppress heartbeat for the test
    assert st.sentinel is not None
    now = T0 + timedelta(seconds=5)
    assert lane.pending_triggers(st, st.sentinel, now) == []
    st.sentinel.notes.append(
        SentinelNote(id="n1xxxx", actor="cli:a", at=now.isoformat(), text="hi")
    )
    assert lane.pending_triggers(st, st.sentinel, now) == ["human_note"]
    st.tasks["t3"] = TaskRunState(status="failed", ended_at=now.isoformat())
    assert lane.pending_triggers(st, st.sentinel, now) == ["unit_failed", "human_note"]
    lane.tick(st, now)
    _wait_start(lane)
    assert drv.started == [1]
    # kinds that fired are now cooled down / stale => no re-fire without a new event
    lane.tick(st, now + timedelta(seconds=1))
    assert lane.pending_triggers(st, st.sentinel, now + timedelta(seconds=2)) == []
    lane.close()


def test_ungraceful_exit_then_next_generation_carries_last_good_handoff(tmp_path: Path) -> None:
    drv = ScriptedDriver(
        {
            1: {"polls": 1},
            2: {"polls": 1, "rc": 1, "handoff": False, "cost": 0.1},
            3: {"polls": 1},
        }
    )
    lane, st = _lane(tmp_path, drv)
    _drive(lane, st, T0, 14)
    assert drv.started[:3] == [1, 2, 3]
    assert st.tasks["sentinel-g02"].status == "failed"
    assert st.sentinel is not None and st.sentinel.ungraceful_count == 1
    # g02 crashed without a handoff -> g03 still sees g01's handoff and an ungraceful note.
    prev = json.loads(drv.packs[3]["previous-handoff.json"])
    assert prev["generation"] == 1
    assert json.loads(drv.packs[3]["digest.json"])["llm"]["ungraceful_count"] == 1
    events = [
        json.loads(x)["event"]
        for x in (tmp_path / "sentinel" / "ledger.jsonl").read_text().splitlines()
    ]
    assert "sentinel_ungraceful" in events
    lane.close()


@pytest.mark.parametrize(
    ("kwargs", "plan", "reason"),
    [
        ({"generation_ttl_minutes": 1}, {"hang": True}, "ttl"),
        ({"max_tokens_per_generation": 1000}, {"hang": True, "tokens": 5000}, "max_tokens"),
        ({"max_triggers_per_generation": 1}, {"hang": True}, "max_triggers"),
    ],
)
def test_forced_rotation_reasons(
    tmp_path: Path, kwargs: dict[str, Any], plan: dict[str, Any], reason: str
) -> None:
    drv = ScriptedDriver({1: {**plan, "handoff": False}})
    lane, st = _lane(tmp_path, drv, **kwargs)
    _drive(lane, st, T0, 4, dt=61)
    assert drv.stopped[:1] == [1], reason
    assert st.sentinel is not None and st.sentinel.active_generation is None or drv.started[0] == 1
    ledger = [
        json.loads(x) for x in (tmp_path / "sentinel" / "ledger.jsonl").read_text().splitlines()
    ]
    ended = next(
        r for r in ledger if r["event"] == "generation_ended" and r["data"]["generation"] == 1
    )
    assert ended["data"]["forced"] == reason and ended["data"]["graceful"] is False
    lane.close()


def test_explicit_handoff_ends_live_generation(tmp_path: Path) -> None:
    drv = ScriptedDriver({1: {"hang": True, "handoff": True}})
    lane, st = _lane(tmp_path, drv)
    _drive(lane, st, T0, 4)
    assert drv.stopped == [1]
    assert st.tasks["sentinel-g01"].status == "succeeded"
    lane.close()


def test_budget_stops_new_generations_but_ticks_continue(tmp_path: Path) -> None:
    drv = ScriptedDriver({n: {"cost": 0.6, "polls": 0} for n in range(1, 20)})
    lane, st = _lane(tmp_path, drv, budget_usd=1.0)
    _drive(lane, st, T0, 30)
    assert drv.started == [1, 2]  # 0.6 + 0.6 >= 1.0 -> third never starts
    assert st.sentinel is not None and st.sentinel.llm_budget_exhausted
    ledger = (tmp_path / "sentinel" / "ledger.jsonl").read_text()
    assert ledger.count("budget_exhausted") == 1  # recorded once, ticks keep running quietly
    assert sg.generation_spend(st) == pytest.approx(1.2)
    lane.close()


def test_budget_pct_of_run_cost_cap(tmp_path: Path) -> None:
    spec = SentinelLlmSpec(enabled=True, budget_pct=10)
    assert sg.budget_cap_usd(spec, 20.0) == pytest.approx(2.0)
    assert sg.budget_cap_usd(spec, None) is None
    assert sg.budget_cap_usd(SentinelLlmSpec(enabled=True, budget_usd=3.0), 20.0) == 3.0


def test_paused_lane_starts_nothing(tmp_path: Path) -> None:
    drv = ScriptedDriver()
    lane, st = _lane(tmp_path, drv)
    assert st.sentinel is not None
    st.sentinel.llm_paused = True
    _drive(lane, st, T0, 10)
    assert drv.started == []
    lane.close()


def test_driver_start_failure_is_ungraceful_not_fatal(tmp_path: Path) -> None:
    class Boom(ScriptedDriver):
        def start(self, pack_dir: str, limits: GenerationLimits) -> Handle:
            raise OSError("no claude binary")

    lane, st = _lane(tmp_path, Boom())
    _drive(lane, st, T0, 4)
    assert st.tasks["sentinel-g01"].status == "failed"
    assert st.sentinel is not None and st.sentinel.ungraceful_count >= 1
    lane.close()


def test_reconcile_on_start_marks_lost_generation_ungraceful(tmp_path: Path) -> None:
    lane, st = _lane(tmp_path, ScriptedDriver())
    assert st.sentinel is not None
    st.sentinel.generation = 4
    st.sentinel.active_generation = "sentinel-g04"
    st.tasks["sentinel-g04"] = TaskRunState(status="running")
    assert lane.reconcile_on_start(st, T0) is True
    assert st.tasks["sentinel-g04"].status == "failed"
    assert st.sentinel.active_generation is None and st.sentinel.ungraceful_count == 1
    assert lane.reconcile_on_start(st, T0) is False  # idempotent
    lane.close()


def test_generation_rows_do_not_trip_failure_breakers() -> None:
    from agent_orchestrator.breakers import _consecutive_failure_streak

    st = _state()
    st.tasks["sentinel-g01"] = TaskRunState(status="failed", ended_at=T0.isoformat())
    st.tasks["t2"].ended_at = (T0 - timedelta(seconds=5)).isoformat()
    assert _consecutive_failure_streak(st) == 0


# ---------------------------------------------------------------------------------------------
# Engine end-to-end (temp workspace, scripted driver, FakeExecutor)
# ---------------------------------------------------------------------------------------------


class StepClock:
    """Every read advances 20 s so heartbeat/cooldown windows pass in a few ticks."""

    def __init__(self) -> None:
        self.now = datetime.now(UTC)  # same epoch as RunState.started_at (real clock)
        self.lock = threading.Lock()

    def __call__(self) -> datetime:
        with self.lock:
            self.now += timedelta(seconds=20)
            return self.now


class SlowExecutor(FakeExecutor):
    def __init__(self, delay: float = 0.25) -> None:
        super().__init__()
        self.delay = delay

    def execute(self, ctx: TaskContext) -> TaskResult:
        time.sleep(self.delay)
        return super().execute(ctx)


def _settings(**llm: Any) -> SentinelSettings:
    base = {
        "enabled": True,
        "heartbeat_minutes": 1,
        "trigger_cooldown_minutes": 0,
        "max_generations_per_hour": 1000,
        "generation_ttl_minutes": 600,
    }
    return resolve_sentinel_settings(
        workflow=SentinelSpec(enabled=True, tick_seconds=1, llm=SentinelLlmSpec(**{**base, **llm}))
    )


def _wf(n: int = 4) -> WorkflowSpec:
    tasks = [
        TaskSpec(
            id=f"t{i}",
            agent="ag",
            instruction="specs/examples/instructions/design.md",
            depends_on=[f"t{i - 1}"] if i else [],
        )
        for i in range(n)
    ]
    return WorkflowSpec(version="1.0", id="wf", repo_set="rs", tasks=tasks)


def _env(tmp_path: Path) -> tuple[LocalFsArtifactStore, RunStateStore, dict, dict]:
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


def _run(
    tmp_path: Path,
    drv: ScriptedDriver,
    settings: SentinelSettings | None = None,
    wf: WorkflowSpec | None = None,
    run_state: RunState | None = None,
    delay: float = 0.25,
) -> tuple[RunState, RunStateStore]:
    store, rs, reposets, agents = _env(tmp_path)
    orch = Orchestrator(
        SlowExecutor(delay),
        store,
        rs,
        max_parallel=1,
        clock=StepClock(),
        sentinel=settings or _settings(),
        sentinel_driver_factory=lambda _spec: drv,
    )
    return orch.run(wf or _wf(), reposets, agents, run_state=run_state), rs


def test_e2e_rotation_three_generations_handoff_carried_ungraceful_covered(
    tmp_path: Path,
) -> None:
    drv = ScriptedDriver(
        {
            1: {
                "memory": "- [pin] gen1 learned A\n",
                "proposals": [("g1-note-001", "note", {"text": "hello"})],
            },
            2: {"rc": 1, "handoff": False, "cost": 0.1},
            3: {"memory": "- gen3 learned B\n"},
        }
    )
    state, rs = _run(tmp_path, drv, wf=_wf(6))
    assert state.status == "succeeded"
    assert len(drv.started) >= 3 and drv.started[:3] == [1, 2, 3]
    run_dir = rs._path(state.run_id).parent
    # handoff carried g01 -> g02 (g02 ungraceful) -> g03 still sees g01's.
    assert json.loads(drv.packs[2]["previous-handoff.json"])["generation"] == 1
    assert json.loads(drv.packs[3]["previous-handoff.json"])["generation"] == 1
    # g04+ (if any) see g03's.
    if 4 in drv.packs:
        assert json.loads(drv.packs[4]["previous-handoff.json"])["generation"] == 3
    # memory merged tool-side and carried into the pack
    assert b"gen1 learned A" in drv.packs[2]["memory.md"]
    assert b"gen3 learned B" not in drv.packs[3]["memory.md"]
    mem = (run_dir / "sentinel" / "memory.md").read_text()
    assert "gen1 learned A" in mem and "gen3 learned B" in mem
    # proposal consumed through the normal inbox path
    assert state.sentinel is not None
    assert "g1-note-001" in state.sentinel.inbox_consumed
    assert any(n.text == "hello" for n in state.sentinel.notes)
    # ungraceful accounted + ledger
    assert state.sentinel.ungraceful_count >= 1
    assert state.tasks["sentinel-g02"].status == "failed"
    ledger = (run_dir / "sentinel" / "ledger.jsonl").read_text()
    assert "sentinel_ungraceful" in ledger and "generation_started" in ledger
    # nothing left running; the engine task pool slots were never used by generations
    assert state.sentinel.active_generation is None
    assert all(ts.status != "running" for ts in state.tasks.values())
    # lane stays outside max_parallel: workflow tasks ran strictly 1 at a time
    saved = rs.load(state.run_id)
    assert saved.sentinel is not None and saved.sentinel.generation == state.sentinel.generation


def test_e2e_cost_rows_in_run_cost_and_usage(tmp_path: Path) -> None:
    drv = ScriptedDriver({n: {"cost": 0.5} for n in range(1, 30)})
    state, _rs = _run(tmp_path, drv, wf=_wf(5))
    gens = [t for t in state.tasks if t.startswith("sentinel-g")]
    assert len(gens) >= 2
    spend = sum(state.tasks[t].cumulative_cost_usd for t in gens)
    assert spend > 0
    assert compute_run_usage_totals(state).cost_usd == pytest.approx(spend)  # run_cost_usd basis
    report = aggregate_usage([state], LocalFsArtifactStore(str(tmp_path)))
    group = next(g for g in report.groups if g.agent == "sentinel")
    assert group.tasks == len(gens) and group.cost_usd == pytest.approx(spend)


def test_e2e_budget_cap_stops_generations_ticks_continue(tmp_path: Path) -> None:
    drv = ScriptedDriver({n: {"cost": 0.6, "polls": 0} for n in range(1, 30)})
    state, rs = _run(tmp_path, drv, _settings(budget_usd=1.0), wf=_wf(6))
    assert state.status == "succeeded"  # the run itself is unaffected
    assert drv.started == [1, 2]
    assert state.sentinel is not None and state.sentinel.llm_budget_exhausted
    assert state.sentinel.last_tick_at is not None  # ticks continued to the end
    assert state.metrics is not None


def test_e2e_consecutive_rejections_auto_pause(tmp_path: Path) -> None:
    bad = [(f"g1-bad-{i:03d}", "kill", {"task_id": "nope"}) for i in range(3)]
    drv = ScriptedDriver({1: {"proposals": bad}, 2: {}})
    state, _rs = _run(tmp_path, drv, _settings(max_consecutive_rejections=3), wf=_wf(6))
    assert state.sentinel is not None
    assert state.sentinel.llm_paused is True
    assert "3 consecutive" in (state.sentinel.llm_pause_reason or "")
    assert drv.started == [1]  # paused: no further generations


def test_e2e_accepted_proposal_resets_rejection_streak(tmp_path: Path) -> None:
    props = [
        ("g1-a-bad1", "kill", {"task_id": "nope"}),
        ("g1-b-bad2", "kill", {"task_id": "nope"}),
        ("g1-c-ok01", "note", {"text": "fine"}),
        ("g1-d-bad3", "kill", {"task_id": "nope"}),
    ]
    drv = ScriptedDriver({1: {"proposals": props}})
    state, _rs = _run(tmp_path, drv, _settings(max_consecutive_rejections=3), wf=_wf(5))
    assert state.sentinel is not None and state.sentinel.llm_paused is False


def test_e2e_human_rejections_do_not_count(tmp_path: Path) -> None:
    store, rs, reposets, agents = _env(tmp_path)
    st = rs.new_run(_wf(3))
    run_dir = rs._path(st.run_id).parent
    inbox = run_dir / "control" / "inbox"
    inbox.mkdir(parents=True)
    for i in range(5):
        (inbox / f"cli-bad-{i:04d}.json").write_text(
            json.dumps(
                {
                    "schema": "ao.ctl/v1",
                    "id": f"cli-bad-{i:04d}",
                    "kind": "kill",
                    "actor": "cli:me",
                    "created_at": T0.isoformat(),
                    "payload": {"task_id": "nope"},
                }
            )
        )
    state, _ = _run(
        tmp_path, ScriptedDriver(), _settings(max_consecutive_rejections=2), run_state=st
    )
    assert state.sentinel is not None
    assert state.sentinel.consecutive_rejections == 0 and not state.sentinel.llm_paused


def test_e2e_resume_restores_counter_memory_handoff_no_duplicate_proposals(
    tmp_path: Path,
) -> None:
    # Session 1: g01 completes with memory + a proposal; run is then interrupted mid-g02.
    drv1 = ScriptedDriver(
        {
            1: {
                "memory": "- [pin] durable fact\n",
                "proposals": [("g1-note-001", "note", {"text": "once"})],
            },
            2: {"hang": True, "handoff": False},
        }
    )
    store, rs, reposets, agents = _env(tmp_path)
    wf = _wf(3)
    state0 = rs.new_run(wf)
    # Interrupt: first workflow task fails fast the run via a cancel after g02 started.
    cancel = threading.Event()

    class Interrupt(SlowExecutor):
        def execute(self, ctx: TaskContext) -> TaskResult:
            if 2 in drv1.started:
                cancel.set()
            return super().execute(ctx)

    orch = Orchestrator(
        Interrupt(0.3),
        store,
        rs,
        max_parallel=1,
        clock=StepClock(),
        cancel_fn=cancel.is_set,
        sentinel=_settings(),
        sentinel_driver_factory=lambda _s: drv1,
    )
    s1 = orch.run(wf, reposets, agents, run_state=state0)
    assert s1.sentinel is not None and s1.sentinel.generation >= 2
    gen_before = s1.sentinel.generation
    # Simulate a hard crash while g02 live: re-mark the row/active as the dead process left it.
    saved = rs.load(state0.run_id)
    assert saved.sentinel is not None
    saved.sentinel.active_generation = f"sentinel-g{gen_before:02d}"
    saved.tasks[f"sentinel-g{gen_before:02d}"] = TaskRunState(status="running")
    saved.status = "running"
    rs.save(saved)
    # Session 2 (resume): scripted g(N+1) re-proposes the SAME note id (duplicate) + new memory.
    drv2 = ScriptedDriver(
        {
            gen_before + 1: {
                "proposals": [("g1-note-001", "note", {"text": "once"})],
                "memory": "- later\n",
            }
        }
    )
    resumed = rs.prepare_resume(rs.load(state0.run_id), wf)
    s2, _ = _run(tmp_path, drv2, wf=wf, run_state=resumed)
    assert s2.status == "succeeded"
    assert s2.sentinel is not None
    assert drv2.started and drv2.started[0] == gen_before + 1  # counter restored, monotonic
    assert s2.sentinel.ungraceful_count >= 1  # the lost generation was reconciled
    pack = drv2.packs[gen_before + 1]
    assert b"durable fact" in pack["memory.md"]  # memory restored
    assert json.loads(pack["previous-handoff.json"])["generation"] == 1  # last handoff restored
    assert sum(1 for n in s2.sentinel.notes if n.text == "once") == 1  # no duplicate proposal
    assert s2.tasks[f"sentinel-g{gen_before:02d}"].status == "failed"


def test_e2e_disabled_llm_leaves_no_trace(tmp_path: Path) -> None:
    drv = ScriptedDriver()
    settings = resolve_sentinel_settings(workflow=SentinelSpec(enabled=True, tick_seconds=1))
    state, rs = _run(tmp_path, drv, settings, wf=_wf(3))
    assert drv.started == []
    assert not any(t.startswith("sentinel-g") for t in state.tasks)
    assert not (rs._path(state.run_id).parent / "sentinel").exists()
    assert state.sentinel is not None and state.sentinel.generation == 0


def test_e2e_lane_error_disables_only_llm_lane(tmp_path: Path) -> None:
    class Explodes(ScriptedDriver):
        def poll(self, handle: Handle) -> DriverStatus:
            raise RuntimeError("driver bug")

    state, _ = _run(tmp_path, Explodes(), wf=_wf(4))
    assert state.status == "succeeded"
    assert state.sentinel is not None and state.sentinel.llm_paused
    assert "lane error" in (state.sentinel.llm_pause_reason or "")
    assert state.sentinel.enabled  # the mechanical sentinel survived
    assert state.tasks["sentinel-g01"].status != "running"


def test_resume_llm_clears_pause_reason(tmp_path: Path) -> None:
    st = _state()
    assert st.sentinel is not None
    st.sentinel.llm_paused = True
    st.sentinel.llm_pause_reason = "x"
    st.sentinel.consecutive_rejections = 4
    rt = sentinel_tick.SentinelRuntime(
        ceiling=1,
        tick_seconds=1,
        inbox_batch_max=10,
        live_limit=1,
        control_dir=tmp_path / "control",
        clock=lambda: T0,
        log=logging.getLogger("t"),
    )
    inbox = tmp_path / "control" / "inbox"
    inbox.mkdir(parents=True)
    (inbox / "cli-resume-001.json").write_text(
        json.dumps(
            {
                "schema": "ao.ctl/v1",
                "id": "cli-resume-001",
                "kind": "resume_llm",
                "actor": "cli:me",
                "created_at": T0.isoformat(),
            }
        )
    )
    sentinel_tick.drain_inbox(st, rt, T0)
    assert not st.sentinel.llm_paused and st.sentinel.llm_pause_reason is None
    assert st.sentinel.consecutive_rejections == 0


# ---------------------------------------------------------------------------------------------
# ClaudeCliSentinelDriver against a fake `claude` executable (real subprocess, no LLM)
# ---------------------------------------------------------------------------------------------


def _fake_claude(tmp_path: Path, body: str) -> AgentSpec:
    script = tmp_path / "fake-claude.sh"
    script.write_text("#!/bin/bash\n" + body)
    script.chmod(0o755)
    return AgentSpec(executor="claude_cli", command_template=[str(script), "{prompt}"])


def _wait_dead(drv: SentinelDriver, handle: Handle, timeout: float = 10.0) -> DriverStatus:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        st = drv.poll(handle)
        if not st.alive:
            return st
        time.sleep(0.05)
    raise AssertionError("generation did not exit")


def test_claude_cli_driver_runs_parses_cost_and_reads_pack(tmp_path: Path) -> None:
    from agent_orchestrator.sentinel_driver import ClaudeCliSentinelDriver

    agent = _fake_claude(
        tmp_path,
        'echo "$1" > "$PWD/prompt.txt"\n'
        'echo \'{"type":"result","subtype":"success","total_cost_usd":0.07,'
        '"usage":{"input_tokens":10,"output_tokens":5}}\'\n',
    )
    pack = tmp_path / "g01" / "pack"
    pack.mkdir(parents=True)
    limits = GenerationLimits(
        ttl_seconds=600, max_tokens=1000, max_budget_usd=0.5, generation=1, inbox_dir="/x/inbox"
    )
    drv = ClaudeCliSentinelDriver(agent)
    handle = drv.start(str(pack), limits)
    st = _wait_dead(drv, handle)
    assert st.returncode == 0 and st.cost_usd == pytest.approx(0.07)
    assert (st.input_tokens, st.output_tokens, st.tokens_used) == (10, 5, 15)
    prompt = (tmp_path / "g01" / "prompt.txt").read_text()
    assert "g01" in prompt and str(pack) in prompt and "/x/inbox" in prompt  # paths only
    drv.stop(handle)  # idempotent after exit


def test_claude_cli_driver_nonzero_exit_and_stop_kills_process_group(tmp_path: Path) -> None:
    from agent_orchestrator.sentinel_driver import ClaudeCliSentinelDriver

    pack = tmp_path / "g01" / "pack"
    pack.mkdir(parents=True)
    limits = GenerationLimits(ttl_seconds=60, max_tokens=1000, generation=1)
    bad = ClaudeCliSentinelDriver(_fake_claude(tmp_path, "echo oops >&2\nexit 3\n"))
    st = _wait_dead(bad, bad.start(str(pack), limits))
    assert st.returncode == 3 and "oops" in (st.error or "") and st.cost_usd is None

    hang = ClaudeCliSentinelDriver(_fake_claude(tmp_path, "sleep 300 &\nwait\n"))
    handle = hang.start(str(pack), limits)
    assert hang.poll(handle).alive
    t0 = time.monotonic()
    hang.stop(handle)
    assert not hang.poll(handle).alive and time.monotonic() - t0 < 15


def test_make_driver_rejects_unknown_driver() -> None:
    from agent_orchestrator.sentinel_driver import ClaudeCliSentinelDriver, make_driver

    assert isinstance(make_driver("claude-cli", None, None), ClaudeCliSentinelDriver)
    with pytest.raises(ValueError, match="unknown sentinel driver"):
        make_driver("nope", None, None)
