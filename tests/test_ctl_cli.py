"""T-Sn3Ctl: `ao ctl` CLI -- entry builder, client-side validation, status schema, live e2e."""

from __future__ import annotations

import json
import re
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from agent_orchestrator import control, sentinel_tick
from agent_orchestrator import engine as engine_mod
from agent_orchestrator.cli import app
from agent_orchestrator.engine import Orchestrator
from agent_orchestrator.models import SentinelSpec, TaskContext
from agent_orchestrator.sentinel_settings import resolve_sentinel_settings

from .test_sentinel_tick import ScriptedExecutor, _setup, _wf

runner = CliRunner()
WAIT_S = 20.0


@pytest.fixture(autouse=True)
def _fast_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    real = engine_mod.wait

    def fast(fs: Any, timeout: float | None = None, return_when: Any = None) -> Any:
        return real(fs, timeout=0.02 if timeout is not None else None, return_when=return_when)

    monkeypatch.setattr(engine_mod, "wait", fast)


def _ctl(ws: Path, *args: str) -> Any:
    return runner.invoke(app, ["ctl", args[0], *args[1:], "--workspace", str(ws)])


# -- unit ---------------------------------------------------------------------------------------


def test_entry_builder_and_atomic_write(tmp_path: Path) -> None:
    now = datetime(2026, 11, 9, 1, 2, 3, tzinfo=UTC)
    e = control.build_entry(
        "note", {"text": "hi"}, actor="cli:me", now=now, rand=lambda n: "ab" * n
    )
    assert e["schema"] == "ao.ctl/v1" and e["actor"] == "cli:me"
    assert e["id"] == "note-20261109t010203-abababab"
    assert sentinel_tick._valid_id(e["id"])
    path = control.write_entry(tmp_path, e)
    assert path == tmp_path / "control" / "inbox" / f"{e['id']}.json"
    assert json.loads(path.read_text()) == e
    assert not list((tmp_path / "control" / "inbox").glob("*.tmp"))
    assert "rationale" not in e
    assert control.build_entry("kill", rationale="r")["rationale"] == "r"


def test_default_actor_is_os_user() -> None:
    assert control.default_actor().startswith("cli:")


@pytest.mark.parametrize(
    "items,msg",
    [
        ([], "at least one"),
        (["max_parallel"], "key=value"),
        (["bogus=1"], "not live-overridable"),
        (["max_parallel=abc"], "invalid value"),
        (["max_parallel=0"], ">= 1"),
        (["max_parallel=1", "max_parallel=2"], "duplicate"),
    ],
)
def test_set_rejected_client_side(items: list[str], msg: str) -> None:
    with pytest.raises(control.ControlError, match=msg):
        control.parse_set_items(items)


def test_set_ceiling_and_typing() -> None:
    assert control.parse_set_items(["max_parallel=3"], ceiling=4) == {"max_parallel": 3}
    with pytest.raises(control.ControlError, match="ceiling"):
        control.parse_set_items(["max_parallel=5"], ceiling=4)


def test_resolve_run_prefix(tmp_path: Path) -> None:
    root = control.runs_root(tmp_path)
    for rid in ("wf-1", "wf-10", "other-2"):
        (root / rid).mkdir(parents=True)
        (root / rid / "state.json").write_text("{}")
    assert control.resolve_run_dir(tmp_path, "wf-1").name == "wf-1"  # exact beats prefix
    assert control.resolve_run_dir(tmp_path, "oth").name == "other-2"
    with pytest.raises(control.ControlError, match="ambiguous"):
        control.resolve_run_dir(tmp_path, "w")
    with pytest.raises(control.ControlError, match="no run matching"):
        control.resolve_run_dir(tmp_path, "zzz")
    with pytest.raises(control.ControlError, match="invalid"):
        control.resolve_run_dir(tmp_path, "../x")


def test_wait_outcome_timeout_with_fake_clock(tmp_path: Path) -> None:
    t = [0.0]

    def sleep(s: float) -> None:
        t[0] += s

    out = control.wait_outcome(
        tmp_path, "note-x-000000", timeout=1.0, poll=0.5, sleep=sleep, monotonic=lambda: t[0]
    )
    assert out == {"outcome": "timeout"}


# -- status schema ------------------------------------------------------------------------------

STATUS_KEYS = {
    "schema", "run_id", "run_status", "updated_at", "sentinel", "concurrency", "utilisation",
    "tasks", "ladder", "backlog", "llm", "inbox", "recent_actions",
}  # fmt: skip


def _norm(doc: dict[str, Any]) -> dict[str, Any]:
    text = json.dumps(doc, sort_keys=True)
    text = re.sub(r"\d{8}T\d{6}Z", "<TS>", text)
    text = re.sub(r"20\d\d-\d\d-\d\dT[\d:.+]+", "<ISO>", text)
    return json.loads(text)


def test_status_json_snapshot(tmp_path: Path) -> None:
    store, rs, _, _ = _setup(tmp_path)
    state = rs.new_run(_wf(2))
    sentinel_tick.ensure_state(state).notes.append(
        sentinel_tick.SentinelNote(
            id="note-1-000000", actor="cli:u", at="2026-11-09T00:00:00", text="n"
        )
    )
    state.tasks["t0"].status = "running"
    rs.save(state)
    res = _ctl(tmp_path, "status", state.run_id[:6], "--json")
    assert res.exit_code == 0, res.output
    doc = json.loads(res.stdout)
    assert set(doc) == STATUS_KEYS
    assert _norm(doc) == {
        "schema": "ao.ctl.status/v1",
        "run_id": "wf-<TS>",
        "run_status": "running",
        "updated_at": "<ISO>",
        "sentinel": {
            "enabled": True, "disabled_reason": None, "last_tick_at": None,
            "llm_paused": False, "hold_requested": False,
        },
        "concurrency": {"running": 1, "limit": None, "ceiling": None, "overrides": {}},
        "utilisation": {
            "pct": 0.0, "slot_seconds_busy": 0.0,
            "slot_seconds_idle_with_ready": 0.0, "slot_seconds_idle_no_ready": 0.0,
        },
        "tasks": {
            "counts": {"pending": 1, "running": 1},
            "running": [{"id": "t0", "liveness_age_s": None}],
        },
        "ladder": {},
        "backlog": {"held": []},
        "llm": {"generation": None, "spend_usd": None, "paused": False},
        "inbox": {"pending": [], "consumed_count": 0},
        "recent_actions": [
            {"id": "note-1-000000", "kind": "note", "actor": "cli:u", "at": "<ISO>", "text": "n"}
        ],
    }  # fmt: skip
    human = _ctl(tmp_path, "status", state.run_id)
    assert human.exit_code == 0 and "running=1" in human.output


def test_cli_errors(tmp_path: Path) -> None:
    store, rs, _, _ = _setup(tmp_path)
    state = rs.new_run(_wf(1))
    rs.save(state)
    inbox = control.runs_root(tmp_path) / state.run_id / "control" / "inbox"
    r = _ctl(tmp_path, "set", state.run_id, "bogus=1")
    assert r.exit_code == 2 and "not live-overridable" in r.output
    r = _ctl(tmp_path, "set", state.run_id, "max_parallel=x", "--json")
    assert r.exit_code == 2 and "invalid value" in json.loads(r.stdout)["error"]
    assert _ctl(tmp_path, "status", "nope").exit_code == 1
    assert _ctl(tmp_path, "note", state.run_id, "   ").exit_code == 2
    assert not inbox.exists()  # nothing written on a client-side rejection
    r = _ctl(tmp_path, "kill", state.run_id, "t0")  # --reason is required
    assert r.exit_code != 0 and not inbox.exists()
    r = _ctl(tmp_path, "cancel", state.run_id, "--json")  # no engine: queued
    q = json.loads(r.stdout)
    assert r.exit_code == 0 and q["outcome"] == "queued" and q["kind"] == "request_cancel"
    assert (inbox / f"{q['id']}.json").is_file()
    r = _ctl(tmp_path, "cancel", state.run_id, "--wait", "--wait-timeout", "0.3")
    assert r.exit_code == control_exit("timeout")


def control_exit(kind: str) -> int:
    from agent_orchestrator import ctl_cli

    return {"timeout": ctl_cli.EXIT_TIMEOUT, "rejected": ctl_cli.EXIT_REJECTED}[kind]


# -- e2e: CliRunner against a live engine thread ----------------------------------------------


def test_live_round_trip_set_pause_note_kill_cancel(tmp_path: Path) -> None:
    store, rs, reposets, agents = _setup(tmp_path)
    state0 = rs.new_run(_wf(2))
    rs.save(state0)
    run_id = state0.run_id
    cli_out: dict[str, Any] = {}
    done = threading.Event()

    def invoke(key: str, *args: str) -> None:
        r = _ctl(tmp_path, *args, "--wait", "--json")
        cli_out[key] = (r.exit_code, json.loads(r.stdout))

    def first(ctx: TaskContext) -> None:
        # Drive the CLI from a worker thread while the engine tick loop is live.
        invoke("set", "set", run_id, "max_parallel=2")
        invoke("bad", "set", run_id, "max_parallel=9")  # above ceiling: server-side quarantine
        invoke("release", "release", run_id)
        invoke("pause", "pause-llm", run_id)
        invoke("note", "note", run_id, "hello world")
        invoke("kill", "kill", run_id, "t0", "--reason", "test")
        invoke("cancel", "cancel", run_id)
        done.set()

    ex = ScriptedExecutor({"t0": first})
    sentinel = resolve_sentinel_settings(
        workflow=SentinelSpec(enabled=True, tick_seconds=1, max_parallel_ceiling=3)
    )
    orch = Orchestrator(ex, store, rs, max_parallel=1, sentinel=sentinel)
    final = orch.run(_wf(2), reposets, agents, run_state=state0)
    assert done.wait(WAIT_S)

    assert cli_out["set"][0] == 0 and cli_out["set"][1]["outcome"] == "consumed"
    assert cli_out["pause"][1]["outcome"] == "consumed"
    assert cli_out["note"][1]["outcome"] == "consumed"
    assert cli_out["cancel"][1]["outcome"] == "consumed"
    # release_backlog lands with T-Sn7Bkl: quarantined with the dedicated exit code.
    assert cli_out["release"][0] == 5 and cli_out["release"][1]["rule_id"] == "not_enabled_yet"
    # the ceiling is only known to the engine: server-side CTL-CEILING quarantine, exit 3.
    assert cli_out["bad"][0] == 3 and cli_out["bad"][1]["rule_id"] == "CTL-CEILING"
    # kill's engine semantics belong to T-Sn4Rec; here only the CLI contract: an outcome arrived.
    assert cli_out["kill"][1]["outcome"] in {"consumed", "rejected"}
    assert final.sentinel is not None
    assert final.sentinel.overrides["max_parallel"].value == 2
    assert final.sentinel.llm_paused is True
    assert [n.text for n in final.sentinel.notes] == ["hello world"]
    assert final.status == "cancelled"
