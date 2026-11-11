"""T-Sv5Hb3: scheduler wiring into the `ao service` daemon. Fixed clocks, temp workspaces,
stub uvicorn/Supervisor/engine (nothing serves, spawns or forks)."""

from __future__ import annotations

import logging
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from agent_orchestrator.cli import app
from agent_orchestrator.schedules import ScheduleFile, ScheduleStore
from agent_orchestrator.service import cli as service_cli
from agent_orchestrator.service import hub as hub_module
from agent_orchestrator.service import schedule_engine as se
from agent_orchestrator.service.cli import (
    build_schedule_engine,
    build_status_provider,
    monitor_tick,
    shutdown_daemon,
)
from agent_orchestrator.service.registry import (
    ServiceRegistry,
    ServiceRegistryFile,
    WorkspaceEntry,
)
from agent_orchestrator.service.systemd import render_unit

from ..schedules._fixtures import minimal_limits

runner = CliRunner()
T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _clock() -> datetime:
    return T0


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.setenv("AO_SERVICE_CONFIG", str(tmp_path / "service.yaml"))
    monkeypatch.setenv("AO_SERVICE_STATE_DIR", str(tmp_path / "state"))


def _write_schedules(root: Path) -> None:
    from agent_orchestrator.schedules.models import ScheduleBinding

    root.mkdir(parents=True, exist_ok=True)
    ScheduleStore(str(root)).save(
        ScheduleFile(
            schedules=[
                ScheduleBinding(
                    id="s",
                    kind="interval",
                    interval_seconds=60,
                    workflow="w.json",
                    limits=minimal_limits(),
                )
            ]
        )
    )


# -- registry (AC1, AC2) ----

PRE_EPIC_REGISTRY = """\
workspaces:
- root: /tmp/ws-a
  port: 8801
  host: null
  autoresume: true
"""


def test_pre_epic_registry_loads_verbatim(tmp_path: Path) -> None:
    path = tmp_path / "service.yaml"
    path.write_text(PRE_EPIC_REGISTRY)
    data = ServiceRegistry(path).load()
    assert [(w.root, w.port, w.autoresume, w.schedules) for w in data.workspaces] == [
        ("/tmp/ws-a", 8801, True, False)
    ]
    cfg = data.schedules
    assert (cfg.enabled, cfg.eval_interval_seconds, cfg.max_concurrent_global) == (True, 15.0, 3)
    assert (cfg.webhook.enabled, cfg.webhook.host, cfg.webhook.port) == (False, "127.0.0.1", 8771)


def test_newer_registry_keys_are_ignored(tmp_path: Path) -> None:
    path = tmp_path / "service.yaml"
    path.write_text(
        "workspaces:\n- {root: /tmp/x, future_field: 1}\nfuture_block: {a: 1}\n"
        "schedules: {enabled: false, future_knob: 2, webhook: {port: 9000, tls: true}}\n"
    )
    data = ServiceRegistry(path).load()
    assert data.workspaces[0].schedules is False
    assert data.schedules.enabled is False and data.schedules.webhook.port == 9000


def test_policy_block_survives_add_remove_save(tmp_path: Path) -> None:
    reg = ServiceRegistry(tmp_path / "service.yaml")
    reg.mutate(
        lambda d: d.model_copy(
            update={"schedules": d.schedules.model_copy(update={"max_concurrent_global": 7})}
        )
    )
    reg.add(str(tmp_path / "a"), schedules=True)
    reg.add(str(tmp_path / "b"))
    reg.remove(str(tmp_path / "b"))
    data = reg.load()
    assert data.schedules.max_concurrent_global == 7
    assert [w.schedules for w in data.workspaces] == [True]


def test_set_schedules_unknown_workspace_raises(tmp_path: Path) -> None:
    with pytest.raises(KeyError):
        ServiceRegistry(tmp_path / "service.yaml").set_schedules(str(tmp_path / "nope"), True)


# -- CLI add / set / list (AC1) ----


def test_add_set_list_schedules_flag(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    assert runner.invoke(app, ["service", "add", str(ws)]).exit_code == 0
    assert ServiceRegistry().load().workspaces[0].schedules is False  # F5 default

    assert runner.invoke(app, ["service", "set", str(ws), "--schedules"]).exit_code == 0
    assert ServiceRegistry().load().workspaces[0].schedules is True
    listing = runner.invoke(app, ["service", "list"]).output
    # w21-05 review: AUTORESUME also prints "True", so check the SCHEDULES column itself
    lines = listing.splitlines()
    hdr = next(i for i, ln in enumerate(lines) if ln.startswith("ROOT"))
    assert lines[hdr].split()[3] == "SCHEDULES" and lines[hdr + 1].split()[3] == "True"

    assert runner.invoke(app, ["service", "set", str(ws), "--no-schedules"]).exit_code == 0
    assert ServiceRegistry().load().workspaces[0].schedules is False

    assert runner.invoke(app, ["service", "add", str(ws), "--schedules"]).exit_code == 0
    assert ServiceRegistry().load().workspaces[0].schedules is True


def test_set_unregistered_fails(tmp_path: Path) -> None:
    result = runner.invoke(app, ["service", "set", str(tmp_path), "--schedules"])
    assert result.exit_code == 1


# -- build_schedule_engine: kill switches, shared collaborators (AC3, AC6, AC6a) ----


class _StubEngine:
    instances: list[_StubEngine] = []

    def __init__(self, registry_path: Path, **kwargs: Any) -> None:
        self.registry_path, self.kwargs = registry_path, kwargs
        self.calls: list[str] = []
        self.fire_store = self.state_store = self.event_log = object()
        self.usage_ledger, self.governance_lock_path = object(), Path("gov.lock")
        _StubEngine.instances.append(self)

    def process_supervisor(self, root: str) -> object:
        return object()

    def register_trigger_source(self, kind: str, factory: Any) -> None:
        self.sources = getattr(self, "sources", {})
        self.sources[kind] = factory

    def start(self) -> None:
        self.calls.append("start")

    def tick(self) -> None:
        self.calls.append("tick")

    def shutdown(self) -> None:
        self.calls.append("shutdown")

    def status_snapshot(self) -> dict[str, Any]:
        return {"enabled": True, "totals": {"active": 1}}


@pytest.fixture()
def stub_engine(monkeypatch: pytest.MonkeyPatch) -> type[_StubEngine]:
    _StubEngine.instances = []
    monkeypatch.setattr(se, "ScheduleEngine", _StubEngine)
    return _StubEngine


def test_cli_flag_kill_switch_skips_construction(
    tmp_path: Path, stub_engine: type[_StubEngine]
) -> None:
    out = build_schedule_engine(
        ServiceRegistryFile(),
        tmp_path / "r.yaml",
        tmp_path / "st",
        object(),
        no_schedules=True,  # type: ignore[arg-type]
    )
    assert out is None and stub_engine.instances == []


def test_registry_kill_switch_skips_construction(
    tmp_path: Path, stub_engine: type[_StubEngine]
) -> None:
    reg = ServiceRegistryFile.model_validate({"schedules": {"enabled": False}})
    assert (
        build_schedule_engine(reg, tmp_path / "r.yaml", tmp_path / "st", object()) is None  # type: ignore[arg-type]
    )
    assert stub_engine.instances == []


def test_engine_built_with_registry_policy_and_started(
    tmp_path: Path, stub_engine: type[_StubEngine]
) -> None:
    reg = ServiceRegistryFile.model_validate(
        {"schedules": {"eval_interval_seconds": 5, "max_concurrent_global": 9}}
    )
    engine = build_schedule_engine(
        reg,
        tmp_path / "r.yaml",
        tmp_path / "st",
        object(),
        clock=_clock,  # type: ignore[arg-type]
    )
    assert isinstance(engine, _StubEngine)
    assert engine.calls == ["start"]
    assert engine.registry_path == tmp_path / "r.yaml"
    assert engine.kwargs["eval_interval_seconds"] == 5
    assert engine.kwargs["max_concurrent_global"] == 9


def test_launcher_shares_engine_stores_and_supervisor(tmp_path: Path) -> None:
    engine = build_schedule_engine(
        ServiceRegistryFile(),
        tmp_path / "r.yaml",
        tmp_path / "st",
        object(),
        clock=_clock,  # type: ignore[arg-type]
    )
    assert engine is not None
    try:
        launcher = engine._launcher.target  # noqa: SLF001
        assert launcher._fires is engine.fire_store  # noqa: SLF001
        assert launcher._state is engine.state_store  # noqa: SLF001
        assert launcher._events is engine.event_log  # noqa: SLF001
        assert launcher._ledger is engine.usage_ledger  # noqa: SLF001  (L2)
        assert launcher._lock_path == engine.governance_lock_path  # noqa: SLF001  (L2)
        assert launcher._supervisor_factory == engine.process_supervisor  # noqa: SLF001
    finally:
        engine.shutdown()


class _Mono:
    """Settable monotonic clock: lets a test step past the engine's ``eval_interval`` (FU-3)."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float = 3600.0) -> None:
        self.now += seconds


def _real_engine(tmp_path: Path, mono: _Mono | None = None) -> se.ScheduleEngine:
    engine = build_schedule_engine(
        ServiceRegistryFile(),
        tmp_path / "service.yaml",
        tmp_path / "st",
        object(),
        clock=_clock,  # type: ignore[arg-type]
        monotonic=mono or _Mono(),
    )
    assert engine is not None
    return engine


def _fire_events(engine: se.ScheduleEngine) -> list[dict[str, Any]]:
    return [e for e in engine.event_log.read() if e["event"].startswith("schedule.fire")]


def _tick_twice(engine: se.ScheduleEngine, mono: _Mono) -> list[Any]:
    """First tick = first sighting (seeds the interval anchor); the second, an hour later on both
    clocks and past the rate limit, is the one that would fire an opted-in schedule."""
    first = engine.tick(T0)
    mono.advance()
    return [*first, *engine.tick(datetime(2026, 1, 1, 1, tzinfo=UTC))]


def test_monotonic_seam_reaches_the_engine(tmp_path: Path) -> None:
    mono = _Mono()
    engine = _real_engine(tmp_path, mono)
    try:
        assert engine.monotonic is mono
        assert engine.clock is _clock
    finally:
        engine.shutdown()


def test_valid_schedule_file_with_default_flag_fires_nothing(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    _write_schedules(ws)
    ServiceRegistry(tmp_path / "service.yaml").add(str(ws))  # schedules defaults to False
    mono = _Mono()
    engine = _real_engine(tmp_path, mono)
    try:
        assert _tick_twice(engine, mono) == []
        assert _fire_events(engine) == []
        assert engine.status_snapshot()["by_workspace"] == []  # not opted in -> not listed
    finally:
        engine.shutdown()


def test_opted_in_workspace_fires_on_the_same_two_ticks(tmp_path: Path) -> None:
    """Control for the test above (mutation check, FU-3): identical setup but opted in, so the
    second tick DOES fire. If the opt-in filter regressed, the previous test would fail; if the
    seam stopped working (rate-limited tick), this one would."""
    ws = tmp_path / "ws"
    _write_schedules(ws)
    ServiceRegistry(tmp_path / "service.yaml").add(str(ws), schedules=True)
    mono = _Mono()
    engine = _real_engine(tmp_path, mono)
    try:
        decisions = _tick_twice(engine, mono)
        assert [d.schedule_id for d in decisions] == ["s"]
    finally:
        engine.shutdown()


def test_f23_workspace_opted_in_after_start_is_seen(tmp_path: Path) -> None:
    """Pins the F23 choice: the engine re-reads the registry file (documented in HLD §6.1)."""
    engine = _real_engine(tmp_path)
    try:
        assert engine.status_snapshot()["by_workspace"] == []
        ws = tmp_path / "ws"
        _write_schedules(ws)
        ServiceRegistry(tmp_path / "service.yaml").add(str(ws), schedules=True)
        rows = engine.status_snapshot()["by_workspace"]
        assert [r["root"] for r in rows] == [str(ws.resolve())]
        assert rows[0]["schedules"][0]["id"] == "s"
    finally:
        engine.shutdown()


# -- monitor loop composition (AC4, AC5, AC8) ----


class _Order:
    def __init__(self) -> None:
        self.log: list[str] = []


class _Sup:
    def __init__(self, order: _Order) -> None:
        self.order = order

    def tick(self) -> None:
        self.order.log.append("sup.tick")

    def shutdown(self) -> None:
        self.order.log.append("sup.shutdown")


class _Eng:
    def __init__(self, order: _Order, *, fail_tick: bool = False, fail_shutdown: bool = False):
        self.order, self.fail_tick, self.fail_shutdown = order, fail_tick, fail_shutdown

    def tick(self) -> None:
        self.order.log.append("eng.tick")
        if self.fail_tick:
            raise RuntimeError("engine blew up")

    def shutdown(self) -> None:
        self.order.log.append("eng.shutdown")
        if self.fail_shutdown:
            raise RuntimeError("shutdown blew up")


def test_engine_ticks_right_after_supervisor() -> None:
    order = _Order()
    monitor_tick(_Sup(order), _Eng(order))  # type: ignore[arg-type]
    assert order.log == ["sup.tick", "eng.tick"]


def test_no_engine_means_supervisor_only() -> None:
    order = _Order()
    monitor_tick(_Sup(order), None)  # type: ignore[arg-type]
    assert order.log == ["sup.tick"]


def test_raising_engine_is_logged_and_loop_continues(caplog: pytest.LogCaptureFixture) -> None:
    order = _Order()
    sup, eng = _Sup(order), _Eng(order, fail_tick=True)
    with caplog.at_level(logging.ERROR, logger=service_cli.logger.name):
        monitor_tick(sup, eng)  # type: ignore[arg-type]
        monitor_tick(sup, eng)  # type: ignore[arg-type]
    assert order.log == ["sup.tick", "eng.tick"] * 2
    records = [r for r in caplog.records if r.getMessage() == "schedule.engine_tick_failed"]
    assert len(records) == 2 and records[0].levelno == logging.ERROR
    assert records[0].exc_info is not None  # traceback kept, not a bare pass


def test_shutdown_engine_first_and_error_does_not_block_supervisor() -> None:
    order = _Order()
    servers = [SimpleNamespace(should_exit=False)]
    thread = threading.Thread(target=lambda: None)
    thread.start()
    shutdown_daemon(_Sup(order), _Eng(order, fail_shutdown=True), servers, [thread])  # type: ignore[arg-type,list-item]
    assert order.log == ["eng.shutdown", "sup.shutdown"]
    assert servers[0].should_exit is True


# -- `ao service run` ordering via stubs (AC3, AC6, AC7, AC8) ----


class _StubUvicorn:
    def __init__(self) -> None:
        self.configs: list[tuple[Any, dict[str, Any]]] = []
        outer = self

        class Config:
            def __init__(self, app: Any, **kwargs: Any) -> None:
                outer.configs.append((app, kwargs))

        class Server:
            def __init__(self, config: Any) -> None:
                self.should_exit = False

            def run(self) -> None:
                return None

        self.Config, self.Server = Config, Server


class _OneTickEvent:
    """Loop body runs exactly once."""

    def __init__(self) -> None:
        self.n = 0

    def set(self) -> None:
        pass

    def is_set(self) -> bool:
        self.n += 1
        return self.n > 1

    def wait(self, timeout: float | None = None) -> bool:
        return False


@pytest.fixture()
def run_env(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    _StubEngine.instances = []
    order = _Order()
    uv = _StubUvicorn()
    # The webhook listener's header-deadline protocol subclasses uvicorn's real h11 protocol
    # (``webhook_http_protocol``, N2): load it before ``uvicorn`` itself is replaced by the stub.
    pytest.importorskip("uvicorn.protocols.http.h11_impl")
    monkeypatch.setitem(sys.modules, "uvicorn", uv)

    class Supervisor:
        def __init__(self, registry: Any, **kwargs: Any) -> None:
            order.log.append("sup.init")

        def start(self) -> None:
            order.log.append("sup.start")

        def tick(self) -> None:
            order.log.append("sup.tick")

        def status_snapshot(self) -> dict[str, Any]:
            return {"workspaces": []}

        def shutdown(self) -> None:
            order.log.append("sup.shutdown")

    class Engine(_StubEngine):
        def start(self) -> None:
            order.log.append("eng.start")

        def tick(self) -> None:
            order.log.append("eng.tick")

        def shutdown(self) -> None:
            order.log.append("eng.shutdown")

    monkeypatch.setattr(service_cli, "Supervisor", Supervisor)
    monkeypatch.setattr(se, "ScheduleEngine", Engine)
    hub_calls: list[Any] = []

    def build_hub_app(status_provider: Any, *, auth: Any = None) -> object:
        hub_calls.append(status_provider)
        return object()

    monkeypatch.setattr(hub_module, "build_hub_app", build_hub_app)
    monkeypatch.setattr(
        service_cli,
        "threading",
        SimpleNamespace(Event=_OneTickEvent, Thread=threading.Thread),
    )
    monkeypatch.setattr(service_cli.signal, "signal", lambda *_a, **_k: None)
    return SimpleNamespace(order=order, uvicorn=uv, hub_calls=hub_calls)


def test_run_startup_tick_and_shutdown_order(run_env: SimpleNamespace) -> None:
    result = runner.invoke(app, ["service", "run"])
    assert result.exit_code == 0, result.output
    assert run_env.order.log == [
        "sup.init",
        "sup.start",
        "eng.start",  # AC3: only after supervisor.start() returned
        "sup.tick",
        "eng.tick",  # AC4: immediately after supervisor.tick()
        "eng.shutdown",  # AC8: engine before supervisor
        "sup.shutdown",
    ]
    # status provider handed to the hub carries the schedules key
    assert run_env.hub_calls[0]()["schedules"]["enabled"] is True


def test_run_engine_start_failure_degrades_to_no_scheduler(
    run_env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """H2: EngineLockHeldError from engine.start() must not crash `ao service run`."""

    class LockedEngine(_StubEngine):
        def start(self) -> None:
            run_env.order.log.append("eng.start")
            raise se.EngineLockHeldError("engine.lock held by `ao schedule daemon`")

        def tick(self) -> None:  # pragma: no cover - must never run
            run_env.order.log.append("eng.tick")

        def shutdown(self) -> None:
            run_env.order.log.append("eng.shutdown")

    monkeypatch.setattr(se, "ScheduleEngine", LockedEngine)
    result = runner.invoke(app, ["service", "run"])
    assert result.exit_code == 0, result.output
    log = run_env.order.log
    assert "eng.tick" not in log  # the loop never ticks a dead engine
    assert log.count("eng.shutdown") == 1  # the half-started engine released its lock once
    assert "sup.tick" in log and log[-1] == "sup.shutdown"  # dashboards supervised + shut down
    assert run_env.hub_calls[0]()["schedules"]["enabled"] is False


def test_run_no_schedules_flag_never_builds_engine(run_env: SimpleNamespace) -> None:
    result = runner.invoke(app, ["service", "run", "--no-schedules"])
    assert result.exit_code == 0, result.output
    assert not any(e.startswith("eng.") for e in run_env.order.log)
    assert _StubEngine.instances == []


def test_run_registry_kill_switch_never_builds_engine(
    run_env: SimpleNamespace, tmp_path: Path
) -> None:
    (tmp_path / "service.yaml").write_text(yaml.safe_dump({"schedules": {"enabled": False}}))
    result = runner.invoke(app, ["service", "run"])
    assert result.exit_code == 0, result.output
    assert not any(e.startswith("eng.") for e in run_env.order.log)


def test_run_webhook_builds_real_app_and_opens_second_socket(run_env: SimpleNamespace) -> None:
    pytest.importorskip("fastapi")  # needs the `ui` extra (L3c)
    result = runner.invoke(app, ["service", "run", "--webhook"])
    assert result.exit_code == 0, result.output
    assert len(run_env.uvicorn.configs) == 2  # hub + webhook listener
    assert type(run_env.uvicorn.configs[1][0]).__name__ == "FastAPI"


def test_run_webhook_nonloopback_warns(run_env: SimpleNamespace) -> None:
    pytest.importorskip("fastapi")  # needs the `ui` extra (L3c)
    result = runner.invoke(app, ["service", "run", "--webhook", "--webhook-host", "0.0.0.0"])
    assert result.exit_code == 0, result.output
    assert "non-loopback" in result.output and "HMAC" in result.output


def test_engine_registers_webhook_source(stub_engine: type[_StubEngine], tmp_path: Path) -> None:
    engine = build_schedule_engine(
        ServiceRegistryFile(),
        tmp_path / "r.yaml",
        tmp_path / "st",
        object(),  # type: ignore[arg-type]
    )
    assert "webhook" in engine.sources  # type: ignore[union-attr]


def test_run_without_webhook_opens_only_the_hub_socket(run_env: SimpleNamespace) -> None:
    result = runner.invoke(app, ["service", "run"])
    assert result.exit_code == 0, result.output
    assert len(run_env.uvicorn.configs) == 1


# -- status (AC9, AC10, AC11) ----


class _Repo:
    def aggregate(self) -> Any:
        return SimpleNamespace(total_runs=0, runs_by_status={})


class _SnapSup:
    def status_snapshot(self) -> dict[str, Any]:
        return {"workspaces": [{"root": "/ws"}], "hub_port": 8770}


class _SnapEngine:
    def __init__(self) -> None:
        self.calls = 0

    def status_snapshot(self) -> dict[str, Any]:
        self.calls += 1
        return {"enabled": True, "totals": {"active": 2}}


def test_status_provider_merges_schedules_inside_ttl_cache() -> None:
    now = [0.0]
    engine = _SnapEngine()
    provider = build_status_provider(
        _SnapSup(),  # type: ignore[arg-type]
        clock=lambda: now[0],
        run_repository_factory=lambda root: _Repo(),  # type: ignore[arg-type,return-value]
        ttl_seconds=5.0,
        schedule_engine=engine,  # type: ignore[arg-type]
    )
    first = provider()
    assert first["schedules"] == {"enabled": True, "totals": {"active": 2}}
    provider()
    assert engine.calls == 1  # cached
    now[0] = 6.0
    provider()
    assert engine.calls == 2


def test_nfr1_payload_unchanged_apart_from_schedules_key(tmp_path: Path) -> None:
    def make(**kw: Any) -> dict[str, Any]:
        return build_status_provider(
            _SnapSup(),  # type: ignore[arg-type]
            run_repository_factory=lambda root: _Repo(),  # type: ignore[arg-type,return-value]
            **kw,
        )()

    baseline = make()
    expected = {
        "workspaces": [{"root": "/ws", "run_summary": {"total_runs": 0, "runs_by_status": {}}}],
        "hub_port": 8770,
    }
    assert {k: v for k, v in baseline.items() if k != "schedules"} == expected
    assert baseline["schedules"] == {"enabled": False}

    # real engine, no schedules.yaml anywhere, no registry block: still only the new key,
    # and no schedule events are written.
    engine = _real_engine(tmp_path)
    try:
        engine.tick(T0)
        with_engine = make(schedule_engine=engine)
        assert {k: v for k, v in with_engine.items() if k != "schedules"} == expected
        assert engine.event_log.read() == []
    finally:
        engine.shutdown()


def test_status_command_prints_schedules_summary_from_live_hub(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    live = {
        "workspaces": [],
        "schedules": {
            "enabled": True,
            "totals": {"active": 3, "disabled": 1, "quarantined": 0},
            "next_fire_at": "2026-01-01T00:01:00Z",
            "live_scheduled_runs": 2,
            "webhook": {"enabled": False},
        },
    }
    monkeypatch.setattr(service_cli, "_probe_hub_status", lambda port: live)
    out = runner.invoke(app, ["service", "status"]).output
    assert "Schedules: 3 active, 1 disabled" in out
    assert "next fire 2026-01-01T00:01:00Z" in out and "live scheduled runs 2" in out
    assert "webhook off" in out


def _write_state(tmp_path: Path, schedules: dict[str, Any]) -> None:
    import json

    sdir = tmp_path / "state" / "schedules"
    sdir.mkdir(parents=True)
    (sdir / "state.json").write_text(json.dumps({"schedules": schedules}))


def test_status_fallback_prints_formatted_schedules_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC10: hub down -> same summary as live, derived from state.json (fixed timestamps)."""
    _write_state(
        tmp_path,
        {
            "w|a": {
                "status": "active",
                "next_fire_at": "2026-01-01T00:05:00Z",
                "active_fire_keys": ["k1", "k2"],
            },
            "w|b": {"status": "active", "next_fire_at": "2026-01-01T00:01:00Z"},
            "w|c": {"status": "disabled", "next_fire_at": "2025-01-01T00:00:00Z"},
            "w|d": {"status": "quarantined"},
        },
    )
    monkeypatch.setattr(service_cli, "_probe_hub_status", lambda port: None)
    out = runner.invoke(app, ["service", "status"]).output
    assert '"schedules"' in out  # raw sections still printed
    assert (
        "Schedules (persisted, daemon not running): 2 active, 1 disabled, 1 quarantined; "
        "next fire 2026-01-01T00:01:00Z; live scheduled runs 2; webhook unknown"
    ) in out


def test_status_fallback_empty_state_has_no_next_fire(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_state(tmp_path, {})
    monkeypatch.setattr(service_cli, "_probe_hub_status", lambda port: None)
    out = runner.invoke(app, ["service", "status"]).output
    assert "0 active, 0 disabled, 0 quarantined; next fire -; live scheduled runs 0" in out


def test_status_fallback_corrupt_state_does_not_break_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sdir = tmp_path / "state" / "schedules"
    sdir.mkdir(parents=True)
    (sdir / "state.json").write_text("{not json")
    monkeypatch.setattr(service_cli, "_probe_hub_status", lambda port: None)
    result = runner.invoke(app, ["service", "status"])
    assert result.exit_code == 0
    assert "Schedules (persisted" in result.output or "No persisted" in result.output


# -- systemd (AC12, AC13) ----


def test_unit_default_has_no_webhook_flags_and_keeps_hardening() -> None:
    text = render_unit("/usr/bin/ao")
    assert "webhook" not in text
    assert "ExecStart=/usr/bin/ao service run --hub-host 127.0.0.1 --hub-port 8770\n" in text
    assert "KillMode=process" in text


def test_unit_webhook_flags_only_when_enabled() -> None:
    base = render_unit("/usr/bin/ao")
    on = render_unit("/usr/bin/ao", webhook=True, webhook_host="127.0.0.1", webhook_port=8771)
    assert "--webhook --webhook-host 127.0.0.1 --webhook-port 8771\n" in on
    assert "KillMode=process" in on
    assert on.replace(" --webhook --webhook-host 127.0.0.1 --webhook-port 8771", "") == base


def test_install_print_default_is_byte_identical_to_pre_epic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_orchestrator.service import systemd

    monkeypatch.setattr(systemd, "resolve_ao_executable", lambda: "/usr/bin/ao")
    out = runner.invoke(app, ["service", "install", "--print"]).output
    assert "--webhook" not in out
    assert out.rstrip("\n") == render_unit("/usr/bin/ao").rstrip("\n")


def test_workspace_entry_defaults() -> None:
    assert WorkspaceEntry(root="/x").schedules is False
