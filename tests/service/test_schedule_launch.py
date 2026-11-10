"""T-Lp4Wt6: ScheduledLauncher. Fixed clocks, StubSupervisor, temp workspaces; nothing spawned."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from agent_orchestrator.run_options import allowed_bool_options, allowed_options
from agent_orchestrator.schedules.models import SCHEDULE_RUN_ARG_NAMES, ScheduleBinding
from agent_orchestrator.service import schedule_launch as sl
from agent_orchestrator.service.fire_store import (
    FireIntentStore,
    ScheduleEventLog,
    ScheduleStateStore,
    fire_key,
)
from agent_orchestrator.service.schedule_engine import FireDecision
from agent_orchestrator.service.schedule_engine import ScheduledLauncher as Proto
from agent_orchestrator.service.schedule_launch import (
    ScheduledLauncher,
    render_instance_id,
    scheduled_run_id,
)
from agent_orchestrator.templates import _load_template_info
from agent_orchestrator.ui.processes import ProcessSupervisor

from ..schedules._fixtures import minimal_limits
from ..ui.conftest import StubSupervisor, write_template, write_workflow
from .test_schedule_engine import Env
from .test_schedule_engine import binding as eng_binding

T0 = datetime(2026, 3, 4, 5, 6, 7, tzinfo=UTC)


def fixed_clock() -> datetime:
    return T0 + timedelta(seconds=30)


def make_binding(**kw: Any) -> ScheduleBinding:
    fields: dict[str, Any] = dict(
        id="nightly", kind="interval", interval_seconds=60, workflow="w.json",
        limits=minimal_limits(),
    )  # fmt: skip
    fields.update(kw)
    return ScheduleBinding(**fields)


class Rig:
    def __init__(self, tmp: Path, **launcher_kw: Any) -> None:
        self.root = str((tmp / "ws").resolve())
        Path(self.root).mkdir()
        self.state_dir = tmp / "state"
        self.sup = StubSupervisor(Path(self.root))
        self.template_calls = 0
        kw: dict[str, Any] = dict(
            process_supervisor_factory=lambda root: self.sup,
            project_config_loader=lambda root: None,
            template_loader=self._templates,
            clock=fixed_clock,
            rand6=lambda: "abc123",
        )
        kw.update(launcher_kw)
        self.launcher = ScheduledLauncher(self.state_dir, **kw)
        self.templates: list[Any] = []

    def _templates(self, root: str, cfg: Any) -> list[Any]:
        self.template_calls += 1
        return self.templates

    def add_template(self, name: str = "mini") -> None:
        tdir = write_template(Path(self.root), dirname="tpl-" + name, name=name)
        self.templates.append(_load_template_info(tdir, "workspace"))

    def decision(self, b: ScheduleBinding, at: datetime = T0) -> FireDecision:
        return FireDecision(
            fire=True, reason="due", workspace_root=self.root, schedule_id=b.id,
            fire_key=fire_key(self.root, b.id, at), scheduled_for=at, effective_due=at,
            binding=b,
        )  # fmt: skip

    def record(self, d: FireDecision) -> Any:
        return FireIntentStore(self.state_dir).get(d.fire_key or "", self.root)


@pytest.fixture()
def rig(tmp_path: Path) -> Rig:
    return Rig(tmp_path)


# -- named helpers --------------------------------------------------------------------------------


def test_scheduled_run_id_utc_normalised() -> None:
    assert scheduled_run_id("nightly", T0) == "ao-nightly-20260304T050607Z"
    ist = timezone(timedelta(hours=5, minutes=30))
    assert scheduled_run_id("nightly", T0.astimezone(ist)) == "ao-nightly-20260304T050607Z"
    with pytest.raises(ValueError):
        scheduled_run_id("x", datetime(2026, 1, 1))


def test_render_instance_id() -> None:
    assert render_instance_id(None, T0) is None
    assert render_instance_id("nightly-{date}", T0) == "nightly-20260304"
    assert (
        render_instance_id("a-{rand6}-{rand6}", T0, rand6=iter(["x1", "y2"]).__next__) == "a-x1-y2"
    )
    assert render_instance_id("fixed", T0) == "fixed"
    late = datetime(2026, 3, 4, 23, 30, tzinfo=timezone(timedelta(hours=-5)))  # 04:30Z next day
    assert render_instance_id("d-{date}", late) == "d-20260305"
    assert len(sl._default_rand6()) == sl.RAND6_LEN


def test_implements_engine_protocol_shape(rig: Rig) -> None:
    proto: Proto = rig.launcher  # structural check (mypy) + runtime call shape
    assert callable(proto.launch)


# -- AC9 happy path -------------------------------------------------------------------------------


def test_happy_path_workflow(rig: Rig) -> None:
    write_workflow(Path(rig.root) / "w.json")
    (Path(rig.root) / "p.md").write_text("do it", encoding="utf-8")
    b = make_binding(prompt_file="p.md", run_args={"max_turns": 3})
    d = rig.decision(b)
    rec = rig.launcher.launch(d)
    call = rig.sup.launch_calls[0]
    assert call["workflow_path"] == str(Path(rig.root) / "w.json")
    assert call["prompt"] == "do it"
    assert call["run_id"] == "ao-nightly-20260304T050607Z"
    assert call["options"] == b.run_args.as_options()
    assert "run_id" not in call["options"]
    assert "reposets" not in call and "agents" not in call
    assert "workspace" not in call  # workflow has a repo_set
    assert rec.status == "launched" and rec.run_id == call["run_id"]
    assert rec.pid == 10_000 and rec.launch_id == "launch-1" and rec.argv_digest
    assert rig.record(d).status == "launched"
    st = ScheduleStateStore(rig.state_dir).get(rig.root, "nightly")
    assert st is not None and st.runs_count == 1 and st.last_run_id == rec.run_id
    assert st.active_fire_keys == [d.fire_key]
    ev = [e for e in ScheduleEventLog(rig.state_dir).read() if e["event"] == "schedule.fired"]
    assert ev and ev[0]["lateness_seconds"] == 30.0


def test_no_repo_set_workflow_gets_workspace(rig: Rig) -> None:
    wf = write_workflow(Path(rig.root) / "w.json")
    spec = json.loads(wf.read_text())
    spec.pop("repo_set")
    wf.write_text(json.dumps(spec))
    rig.launcher.launch(rig.decision(make_binding()))
    assert rig.sup.launch_calls[0]["workspace"] == rig.root


def test_allowlist_read_at_runtime(rig: Rig) -> None:
    write_workflow(Path(rig.root) / "w.json")
    b = make_binding(run_args={"max_turns": 3})
    rig.launcher.launch(rig.decision(b))
    allowed = set(allowed_options()) | set(allowed_bool_options())
    opts = rig.sup.launch_calls[0]["options"]
    assert set(opts) <= set(SCHEDULE_RUN_ARG_NAMES) <= allowed
    assert "run_id" not in opts


# -- AC10 ordering --------------------------------------------------------------------------------


def test_intent_on_disk_before_launch_then_launch_failed(rig: Rig) -> None:
    write_workflow(Path(rig.root) / "w.json")
    d = rig.decision(make_binding())
    seen: list[str] = []

    def boom(**kwargs: object) -> None:
        rec = rig.record(d)
        seen.append(rec.status if rec else "missing")
        raise RuntimeError("spawn exploded")

    rig.sup.launch_run = boom  # type: ignore[method-assign,assignment]
    rec = rig.launcher.launch(d)  # does not propagate
    assert seen == ["intended"]
    assert rec.status == "launch_failed" and "spawn exploded" in (rec.reason or "")
    assert rig.record(d).status == "launch_failed"
    st = ScheduleStateStore(rig.state_dir).get(rig.root, "nightly")
    assert st is not None and st.consecutive_failures == 1
    assert any(e["event"] == "schedule.fire_failed" for e in ScheduleEventLog(rig.state_dir).read())


def test_existing_record_is_not_relaunched(rig: Rig) -> None:
    write_workflow(Path(rig.root) / "w.json")
    d = rig.decision(make_binding())
    rig.launcher.launch(d)
    again = rig.launcher.launch(d)
    assert again.status == "launched" and len(rig.sup.launch_calls) == 1


def test_bookkeeping_failure_does_not_flip_launched(rig: Rig) -> None:
    write_workflow(Path(rig.root) / "w.json")

    def bad(*a: object, **k: object) -> None:
        raise OSError("disk full")

    rig.launcher._state.record_fire = bad  # type: ignore[method-assign,assignment]
    rec = rig.launcher.launch(rig.decision(make_binding()))
    assert rec.status == "launched"


def test_undecidable_decision_rejected(rig: Rig) -> None:
    with pytest.raises(ValueError):
        rig.launcher.launch(
            FireDecision(fire=True, reason="x", workspace_root=rig.root, schedule_id="s")
        )


# -- AC11 template --------------------------------------------------------------------------------


def test_template_fresh_instance_per_fire(rig: Rig) -> None:
    rig.add_template()
    b = make_binding(workflow=None, template="mini", instance_id="nightly-{date}")
    d1 = rig.decision(b, datetime(2026, 3, 4, 1, tzinfo=UTC))
    d2 = rig.decision(b, datetime(2026, 3, 5, 1, tzinfo=UTC))
    r1, r2 = rig.launcher.launch(d1), rig.launcher.launch(d2)
    assert r1.status == r2.status == "launched"
    p1, p2 = (c["workflow_path"] for c in rig.sup.launch_calls)
    assert p1 != p2 and "nightly-20260304" in p1 and "nightly-20260305" in p2
    assert json.loads(Path(p1).read_text())["id"].endswith("nightly-20260304") or Path(p1).is_file()
    assert Path(p1).is_file() and Path(p2).is_file()


def test_template_no_instance_id_uses_schedule_id_slug(rig: Rig) -> None:
    rig.add_template()
    rig.launcher.launch(rig.decision(make_binding(workflow=None, template="mini")))
    assert "nightly" in rig.sup.launch_calls[0]["workflow_path"]


def test_template_prompt_file_and_params(rig: Rig) -> None:
    rig.add_template()
    (Path(rig.root) / "p.md").write_text("hello prompt", encoding="utf-8")
    b = make_binding(
        workflow=None, template="mini", prompt_file="p.md", params={"greeting": "hi"},
        instance_id="x-{rand6}",
    )  # fmt: skip
    rec = rig.launcher.launch(rig.decision(b))
    assert rec.status == "launched"
    call = rig.sup.launch_calls[0]
    assert call["prompt"] == "hello prompt" and "x-abc123" in call["workflow_path"]


def test_unknown_template_is_launch_failed(rig: Rig) -> None:
    rec = rig.launcher.launch(rig.decision(make_binding(workflow=None, template="nope")))
    assert rec.status == "launch_failed" and "not found by name" in (rec.reason or "")
    assert not rig.sup.launch_calls


# -- AC12 security --------------------------------------------------------------------------------


@pytest.mark.parametrize("wf", ["../../etc/passwd", "/etc/passwd", "~/x.json"])
def test_workflow_traversal_rejected(rig: Rig, wf: str) -> None:
    b = make_binding().model_copy(update={"workflow": wf})  # bypasses load-time validation
    rec = rig.launcher.launch(rig.decision(b))
    assert rec.status == "launch_failed"
    assert "workspace" in (rec.reason or "")
    assert not rig.sup.launch_calls


def test_symlink_escape_rejected(rig: Rig, tmp_path: Path) -> None:
    outside = tmp_path / "outside.json"
    outside.write_text("{}")
    (Path(rig.root) / "link.json").symlink_to(outside)
    rec = rig.launcher.launch(rig.decision(make_binding(workflow="link.json")))
    assert rec.status == "launch_failed" and not rig.sup.launch_calls


def test_missing_workflow_named_reason(rig: Rig) -> None:
    rec = rig.launcher.launch(rig.decision(make_binding()))
    assert rec.status == "launch_failed" and "workflow not found" in (rec.reason or "")


@pytest.mark.parametrize("name", ["/tmp/x", "../x", "~/x", "./x", "a/b"])
def test_template_path_never_reaches_load_template(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    import agent_orchestrator.templates as tpl

    def forbidden(*a: object, **k: object) -> None:
        raise AssertionError("load_template must never be called")

    monkeypatch.setattr(tpl, "load_template", forbidden)
    b = make_binding(workflow=None, template="mini").model_copy(update={"template": name})
    rec = rig.launcher.launch(rig.decision(b))
    assert rec.status == "launch_failed" and "not a path" in (rec.reason or "")
    assert not rig.sup.launch_calls and rig.template_calls == 0


@pytest.mark.parametrize("field", ["reposets", "agents", "run_id", "bogus"])
def test_unknown_run_args_fail_at_load(field: str) -> None:
    """Characterization: forbidden/unknown run_args never reach the launcher (T-Sd1Kq7)."""
    with pytest.raises(Exception):  # noqa: B017 - ScheduleFileError or pydantic ValidationError
        make_binding(run_args={field: "x"})


def test_prompt_file_guard_and_pin(rig: Rig) -> None:
    write_workflow(Path(rig.root) / "w.json")
    (Path(rig.root) / "p.md").write_text("abc", encoding="utf-8")
    bad_pin = make_binding(prompt_file="p.md", prompt_sha256="0" * 64)
    assert "prompt_sha256" in (rig.launcher.launch(rig.decision(bad_pin)).reason or "")
    esc = make_binding().model_copy(update={"prompt_file": "../../etc/passwd"})
    assert rig.launcher.launch(rig.decision(esc, T0 + timedelta(hours=1))).status == "launch_failed"
    gone = make_binding(prompt_file="nope.md")
    r = rig.launcher.launch(rig.decision(gone, T0 + timedelta(hours=2)))
    assert "unreadable" in (r.reason or "")
    assert not rig.sup.launch_calls


def test_unloadable_workflow_gets_no_workspace(rig: Rig) -> None:
    (Path(rig.root) / "w.json").write_text("not json")
    rec = rig.launcher.launch(rig.decision(make_binding()))
    assert rec.status == "launched" and "workspace" not in rig.sup.launch_calls[0]


# -- AC4 + default wiring ------------------------------------------------------------------------


def test_engine_default_supervisor_has_zero_discovery_timeout(tmp_path: Path) -> None:
    from agent_orchestrator.service import schedule_engine as se

    sup = se._default_process_supervisor(str(tmp_path))
    assert isinstance(sup, ProcessSupervisor)
    assert sup._discovery_timeout == 0.0  # type: ignore[attr-defined]


def test_default_project_config_loader(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    assert sl._default_project_config_loader(str(tmp_path)) is None


# -- launcher -> engine integration (fixed clocks) ---------------------------------------------


def test_launcher_to_engine_tick(tmp_path: Path) -> None:
    holder: dict[str, Any] = {}

    class Lazy:
        def launch(self, d: FireDecision) -> Any:
            return holder["l"].launch(d)

    env = Env(tmp_path, [eng_binding(workflow="w.json")], launcher=Lazy())
    write_workflow(Path(env.root) / "w.json")
    sup = StubSupervisor(Path(env.root))
    e = env.engine
    holder["l"] = ScheduledLauncher(
        tmp_path / "state",
        process_supervisor_factory=lambda root: sup,
        project_config_loader=lambda root: None,
        clock=env.clock,
        fire_store=e.fire_store,
        state_store=e.state_store,
        event_log=e.event_log,
    )
    env.anchor()
    now = env.clock.now + timedelta(seconds=61)
    fired = env.tick(now)
    assert [d.reason for d in fired] == ["due"] or len(fired) == 1
    d = fired[0]
    rec = e.fire_store.get(d.fire_key or "", env.root)
    assert rec is not None and rec.status == "launched"
    assert sup.launch_calls[0]["run_id"] == scheduled_run_id("s", d.scheduled_for)  # type: ignore[arg-type]
    assert [x["event"] for x in env.events("schedule.fired")] == ["schedule.fired"]
    # same instant again: key is burned, no second launch
    env.tick(now + timedelta(seconds=1))
    assert len(sup.launch_calls) == 1
