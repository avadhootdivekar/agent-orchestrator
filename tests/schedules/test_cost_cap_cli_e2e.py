"""T-88wSLC: cost cap, `ao schedule usage` and capped-run resume end to end via CliRunner.

The only stand-in is the process supervisor: instead of spawning a child it runs the *real*
``ao run`` / ``ao resume`` in this process with a scripted executor (``FakeExecutor`` reporting
``cost_usd`` per task, the T-et7Edf AC15 seam). Everything else is real: ``ao schedule`` CLI,
``ScheduleEngine``, governance gate, fire/ledger stores, the real argv-forwarding check, the
engine cost-cap breaker and its sticky ``cost_cap_usd``. Fixed injected clock
(``schedules.cli.now_utc``),
temp workspace + state dir (never a real ``$HOME``), no sleeps, no network.
"""

# ruff: noqa: F811

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

import agent_orchestrator.executors as executors_pkg
from agent_orchestrator.cli import app
from agent_orchestrator.executors.fake import FakeExecutor
from agent_orchestrator.schedules import cli as sched_cli
from agent_orchestrator.service.fire_store import FireIntentStore, ScheduleStateStore

from ..test_e2e_cli_max_parallel import _write_simple_agents, _write_simple_reposet
from ..ui.conftest import StubSupervisor
from .test_cli_e2e import Env, env  # noqa: F401  # shared fixture

runner = CliRunner()
T0 = datetime(2026, 3, 4, 5, 6, 7, tzinfo=UTC)
TASKS = ("t1", "t2", "t3", "t4")
TASK_COST = 0.40


class ScriptedRunSupervisor(StubSupervisor):
    """Runs the real ``ao run`` / ``ao resume`` synchronously inside ``launch_*``.

    The recorded ``argv`` mirrors the real child's (``--cost-cap-usd <cap>``) so the launcher's
    fail-closed forwarding check is exercised, not bypassed.
    """

    def __init__(self, workspace: Path, specs: list[str]) -> None:
        super().__init__(workspace)
        self.specs = specs
        self.exit_codes: list[int] = []

    def _argv(self, cmd: str, options: dict[str, Any]) -> list[str]:
        argv = ["ao", cmd]
        if "cost_cap_usd" in options:
            argv += ["--cost-cap-usd", str(options["cost_cap_usd"])]
        return argv

    def launch_run(self, **kwargs: Any) -> Any:
        rec = super().launch_run(**kwargs)
        rec.argv = self._argv("run", kwargs["options"])
        args = ["run", *self.specs, "--run-id", str(kwargs["run_id"]), *rec.argv[2:]]
        self.exit_codes.append(runner.invoke(app, args).exit_code)
        self.live_runs.discard(str(kwargs["run_id"]))  # the child has exited
        return rec

    def launch_resume(self, run_id: str, **kwargs: Any) -> Any:
        rec = super().launch_resume(run_id, **kwargs)
        rec.argv = self._argv("resume", kwargs["options"])
        args = ["resume", "--run-id", run_id, *self.specs, *rec.argv[2:]]
        self.exit_codes.append(runner.invoke(app, args).exit_code)
        self.live_runs.discard(run_id)
        return rec


def _write_chain(tmp: Path, n: int = len(TASKS)) -> Path:
    wf = tmp / "chain.json"
    instr = tmp / "specs" / "instructions"
    instr.mkdir(parents=True, exist_ok=True)
    tasks = []
    for i, tid in enumerate(TASKS[:n]):
        (instr / f"{tid}.md").write_text(f"# {tid}\n")
        task: dict[str, Any] = {
            "id": tid,
            "agent": "ag",
            "instruction": f"specs/instructions/{tid}.md",
            "outputs": [f"output/{tid}.txt"],
        }
        if i:
            task["depends_on"] = [TASKS[i - 1]]
            task["inputs"] = [f"output/{TASKS[i - 1]}.txt"]
        tasks.append(task)
    wf.write_text(json.dumps({"version": "1.0", "id": "chain", "repo_set": "rs", "tasks": tasks}))
    return wf


@pytest.fixture()
def capenv(env: Env, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Env:
    """The shared schedule ``env`` with a scripted-cost executor and a real-run supervisor."""
    wf = _write_chain(tmp_path)
    specs = [
        "--workflow", str(wf),
        "--reposets", str(_write_simple_reposet(tmp_path)),
        "--agents", str(_write_simple_agents(tmp_path)),
    ]  # fmt: skip

    class _Costed(executors_pkg.DispatchExecutor):
        def __init__(self) -> None:
            super().__init__()
            self._fake = FakeExecutor(token_outputs={t: {"cost_usd": TASK_COST} for t in TASKS})

    monkeypatch.setattr(executors_pkg, "DispatchExecutor", _Costed)
    monkeypatch.setenv("AO_WORKSPACE_ROOT", str(env.ws))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    env.set_tasks = lambda n: _write_chain(tmp_path, n)  # type: ignore[attr-defined]
    sup = ScriptedRunSupervisor(env.ws, specs)
    monkeypatch.setattr(sched_cli, "_SUPERVISOR_FACTORY", lambda _root: sup)
    env.sup = sup  # type: ignore[attr-defined]
    return env


def _tick(env: Env, monkeypatch: pytest.MonkeyPatch, at: datetime) -> int:
    monkeypatch.setattr(sched_cli, "now_utc", lambda: at)
    res = runner.invoke(app, ["schedule", "daemon", "--once", "--workspace", str(env.ws)])
    assert res.exit_code == 0, res.output
    return int(res.stdout.strip())


def _arm(env: Env, at: datetime) -> None:
    ScheduleStateStore(env.state).advance_anchor(
        env.ws, "nightly", at - timedelta(seconds=INTERVAL)
    )


def _state(env: Env, run_id: str) -> dict:
    return json.loads(next(env.ws.rglob(f"{run_id}/state.json")).read_text())


def _usage(at: datetime | None = None) -> dict:
    args = ["schedule", "usage", "nightly", "--json"] + (["--at", at.isoformat()] if at else [])
    res = runner.invoke(app, args)
    assert res.exit_code == 0, res.output
    return json.loads(res.stdout)["schedules"][0]


INTERVAL = 3600
SETTLE = T0 + timedelta(seconds=INTERVAL)  # the next due tick: the engine settles a finished run
# only while evaluating a due schedule (see the ticket's finding), and day=1 makes that tick skip
# instead of firing a second run into the shared artifact dir.
ADD = ("add", "nightly", "--interval", str(INTERVAL), "--workflow", "w.json", "--overlap", "allow")


def _fire_capped(
    env: Env, monkeypatch: pytest.MonkeyPatch, *extra: str, quota: str = "day=1"
) -> str:
    """Add the schedule, fire once (the run executes to its cap), tick again so the engine settles
    the finished run (pause/abort/final). Returns the run id."""
    env.run(*ADD, "--quota", quota, "--cost-per-run", "1.00", *extra)
    _arm(env, T0)
    assert _tick(env, monkeypatch, T0) == 1
    _tick(env, monkeypatch, SETTLE)
    return str(env.sup.launch_calls[0]["run_id"])  # type: ignore[attr-defined]


def _tasks_done(st: dict) -> list[str]:
    return [t for t in TASKS if st["tasks"].get(t, {}).get("status") == "succeeded"]


def test_per_run_cap_halts_serial_run_after_task_3_and_usage_shows_paused_overshoot(
    capenv: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    rid = _fire_capped(capenv, monkeypatch)
    st = _state(capenv, rid)
    assert capenv.sup.exit_codes == [1]  # type: ignore[attr-defined]
    assert st["status"] == "failed" and st["cost_cap_usd"] == 1.0
    assert [t["id"] for t in st["tripped_breakers"]] == ["builtin.cost_cap"]
    assert _tasks_done(st) == ["t1", "t2", "t3"]  # 3 x $0.40 = $1.20 >= $1.00 -> t4 never ran
    doc = _usage()
    (p,) = doc["paused"]
    assert p["run_id"] == rid and p["cost_usd"] == pytest.approx(1.2)
    assert p["cap_usd"] == 1.0 and p["overshoot_usd"] == pytest.approx(0.2)
    assert (
        p["expires_at"] == "2026-03-07T06:06:07+00:00"
    )  # paused_at (SETTLE) + default 72h pause_ttl
    day = next(w for w in doc["windows"] if w["period"] == "day")
    assert day["runs"] == 1 and day["actual_usd"] == pytest.approx(1.2)
    text = runner.invoke(app, ["schedule", "usage", "nightly"]).output
    assert "paused" in text and "overshoot $0.20" in text
    hist = runner.invoke(app, ["schedule", "history", "nightly", "--json", "-n", "50"])
    assert "schedule.governance.run_cost_capped" in hist.stdout


def test_resume_local_with_the_real_cli_wiring_launches_the_run(
    capenv: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    rid = _fire_capped(capenv, monkeypatch)
    capenv.run("resume", "nightly", "--run", rid, "--add-usd", "1.00", "--local")
    assert _tasks_done(_state(capenv, rid)) == list(TASKS)


def test_resume_local_add_usd_completes_task_4_and_charges_the_same_fire(
    capenv: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    rid = _fire_capped(capenv, monkeypatch, quota="day=1")
    res = capenv.run("resume", "nightly", "--run", rid, "--add-usd", "1.00", "--local")
    assert "new cap $2.20" in res.output  # engine cost $1.20 + $1.00 top-up
    st = _state(capenv, rid)
    assert st["status"] == "succeeded" and st["cost_cap_usd"] == pytest.approx(2.2)
    assert _tasks_done(st) == list(TASKS)
    assert capenv.sup.exit_codes == [1, 0]  # type: ignore[attr-defined]
    assert [c.get("run_id") for c in capenv.sup.launch_calls] == [rid, rid]  # type: ignore[attr-defined]
    # a resume is the same fire: day=1 is not re-spent, nothing is paused any more, spend is $1.60
    _tick(capenv, monkeypatch, SETTLE + timedelta(seconds=INTERVAL))
    doc = _usage()
    assert doc["paused"] == []
    day = doc["windows"][0]
    assert day["runs"] == 1 and day["actual_usd"] == pytest.approx(1.6)
    launched = [r for r in FireIntentStore(capenv.state).list() if r.status != "suppressed"]
    assert len(launched) == 1 and launched[0].governance.final  # type: ignore[union-attr]


def test_plain_ao_resume_keeps_the_sticky_cap_and_dispatches_nothing_new(
    capenv: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    rid = _fire_capped(capenv, monkeypatch)
    before = _state(capenv, rid)
    res = runner.invoke(app, ["resume", "--run-id", rid, *capenv.sup.specs])  # type: ignore[attr-defined]
    assert res.exit_code == 1, res.output
    after = _state(capenv, rid)
    assert after["cost_cap_usd"] == 1.0  # persisted cap survives a flag-less resume
    assert _tasks_done(after) == ["t1", "t2", "t3"]  # zero new dispatches
    assert sum(t.get("cumulative_cost_usd", 0.0) for t in after["tasks"].values()) == pytest.approx(
        sum(t.get("cumulative_cost_usd", 0.0) for t in before["tasks"].values())
    )


def test_on_cap_abort_is_final_and_resume_is_refused_exit_3(
    capenv: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    rid = _fire_capped(capenv, monkeypatch, "--on-cap", "abort")
    assert _usage()["paused"] == []  # aborted, not paused
    rec = FireIntentStore(capenv.state).find_by_run_id(str(capenv.ws), rid)
    assert rec is not None and rec.governance.cap_state == "aborted" and rec.governance.final  # type: ignore[union-attr]
    res = runner.invoke(app, ["schedule", "resume", "nightly", "--run", rid, "--local"])
    assert res.exit_code == 3 and "refused (aborted)" in res.output
    assert _tasks_done(_state(capenv, rid)) == ["t1", "t2", "t3"]


def test_resume_queue_cli_uses_a_read_only_fire_store(
    capenv: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R-2b: the CLI request side must not hold a writable fire store (engine is sole writer)."""
    from agent_orchestrator.service import capped_resume as cr
    from agent_orchestrator.service.fire_store import ReadOnlyFireStore

    rid = _fire_capped(capenv, monkeypatch)
    seen: list[Any] = []
    real = cr.request_capped_resume

    def spy(*a: Any, **kw: Any) -> Any:
        seen.append((kw["fires"], kw["ledger"]))
        return real(*a, **kw)

    monkeypatch.setattr(cr, "request_capped_resume", spy)
    res = runner.invoke(app, ["schedule", "resume", "nightly", "--run", rid, "--queue"])
    assert res.exit_code == 0, res.output
    assert len(seen) == 1 and isinstance(seen[0][0], ReadOnlyFireStore)


def test_pause_expiry_then_resume_refused_exit_3(
    capenv: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    rid = _fire_capped(capenv, monkeypatch)
    assert len(_usage()["paused"]) == 1
    _tick(capenv, monkeypatch, SETTLE + timedelta(days=3, seconds=INTERVAL))  # past paused_at + 72h
    assert _usage(SETTLE + timedelta(days=3, seconds=INTERVAL))["paused"] == []
    res = runner.invoke(app, ["schedule", "resume", "nightly", "--run", rid, "--local"])
    assert res.exit_code == 3 and "refused (expired)" in res.output


def test_one_task_run_crossing_its_cap_succeeds_with_overshoot_and_no_pause(
    capenv: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    capenv.set_tasks(1)  # type: ignore[attr-defined]
    capenv.run(*ADD, "--quota", "day=1", "--cost-per-run", "0.30")  # one $0.40 task > $0.30 cap
    _arm(capenv, T0)
    assert _tick(capenv, monkeypatch, T0) == 1
    _tick(capenv, monkeypatch, SETTLE)
    rid = str(capenv.sup.launch_calls[0]["run_id"])  # type: ignore[attr-defined]
    st = _state(capenv, rid)
    assert st["status"] == "succeeded" and not st["tripped_breakers"]
    doc = _usage()
    assert doc["paused"] == []
    assert doc["windows"][0]["actual_usd"] == pytest.approx(TASK_COST)


def test_cli_deferred_shim_forwards_launch_capped_resume(
    capenv: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B-1 regression: the ``schedules.cli`` ``_Deferred`` shim forwards the resume call."""
    engine = sched_cli._build_engine(capenv.state, None)
    shim = engine._launcher
    assert hasattr(shim, "launch_capped_resume")
    calls: list[tuple[Any, float]] = []

    class _Target:
        def launch_capped_resume(self, rec: Any, new_cap_usd: float) -> str:
            calls.append((rec, new_cap_usd))
            return "ok"

    shim.target = _Target()
    assert shim.launch_capped_resume("rec", 3.0) == "ok"
    assert calls == [("rec", 3.0)]
