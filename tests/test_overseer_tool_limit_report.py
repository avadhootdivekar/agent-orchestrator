"""Tests for `overseer_tool.py`'s A2 limit insight (reader fields, ledger `limit` block,
digest `limit_events`, and the read-only `limit-report` subcommand)."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

_TOOL_PATH = (
    Path(__file__).resolve().parents[1]
    / "src/agent_orchestrator/templates/builtin/overseer-runner/tools/overseer_tool.py"
)


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("overseer_tool_under_test_limit", _TOOL_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


ov = _load()
_NOW = "2026-01-01T00:00:00+00:00"
_START = "2026-01-01T00:00:00+00:00"


def _ended(seconds: int) -> str:
    return f"2026-01-01T{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:00+00:00"


def _cfg_dict() -> dict:
    return {
        "schema": "ao.overseer.config/v1",
        "converge_pct": 80,
        "stabilize_pct": 90,
        "closeout_pct": 95,
        "wave_size": 6,
        "max_waves": 12,
        "wave_max_minutes": 90,
        "max_attempts_per_item": 3,
        "max_expanders_per_wave": 0,
        "max_injected_tasks": 160,
        "run_budget_usd": 2000,
        "task_budget_usd": 75,
        "stall_waves": 2,
        "stabilize_wave_size": 4,
        "max_stabilize_passes": 2,
        "sub_wave_size": 4,
        "default_unit_cost_usd": 8,
        "default_ckpt_cost_usd": 5,
        "contract_version": 1,
    }


def _write_state(tmp_path: Path, tasks: dict, trips: list | None = None) -> Path:
    data = {
        "status": "running",
        "tasks": tasks,
        "injected_tasks": [{"id": t} for t in tasks],
        "tripped_breakers": trips if trips is not None else [],
    }
    path = tmp_path / "state.json"
    path.write_text(json.dumps(data))
    return path


def _task(**over: object) -> dict:
    base = {
        "status": "succeeded",
        "attempts": 1,
        "started_at": _START,
        "ended_at": _ended(600),
        "cumulative_cost_usd": 10.0,
        "model": "sonnet",
        "effort": "medium",
        "agent": "developer",
    }
    base.update(over)
    return base


def _seed(inst: Path, unit_id: str, wave: int, **crumb: object) -> None:
    for path, data in (
        (
            ov.brief_path(inst, wave, unit_id),
            {
                "unit_id": unit_id,
                "ask_ids": ["A2"],
                "work_item": f"A2/{unit_id}",
                "kind": "implement",
            },
        ),
        (
            ov.breadcrumb_path(inst, unit_id),
            {
                "unit_id": unit_id,
                "outcome": "done",
                "verdict": "pass",
                "changed_paths": ["core:a"],
                "needs_input": False,
                **crumb,
            },
        ),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))


_HANDOFF = {
    "reason": "turns",
    "stopped_at": "parser done",
    "staged": ["core:abc"],
    "remaining": "cli",
}


@pytest.fixture
def run(tmp_path: Path):
    inst = tmp_path / "inst"
    inst.mkdir()
    (inst / "overseer-config.json").write_text(json.dumps(_cfg_dict()))
    tasks = {
        "w01-01-split": _task(),
        "w01-02-timeout": _task(status="failed", ended_at=_ended(7200), model=None),
        "w01-03-tripped": _task(status="failed", cumulative_cost_usd=80.0),
        "w01-04-plain": _task(),
        "w01-05-old": {"status": "succeeded", "attempts": 1},
    }
    trips = [
        {
            "id": "task-budget-cap",
            "condition": "task_cost_usd",
            "action": "fail",
            "at": _NOW,
            "detail": {"task_id": "w01-03-tripped", "cost_usd": 80.0, "threshold": 75},
        }
    ]
    state = ov.load_state(_write_state(tmp_path, tasks, trips))
    _seed(inst, "w01-01-split", 1, outcome="partial", verdict="na", handoff=_HANDOFF)
    _seed(inst, "w01-02-timeout", 1, outcome="failed", verdict="fail")
    _seed(inst, "w01-03-tripped", 1, outcome="failed", verdict="fail")
    _seed(inst, "w01-04-plain", 1)
    _seed(inst, "w01-05-old", 1)
    ov.ingest_ledger(
        inst,
        sorted(tasks),
        state,
        1,
        ov.parse_now(_NOW),
        task_cost_limit_usd=75.0,
        run_budget_usd=2000.0,
    )
    return inst, state


def test_load_state_reads_provenance_and_breakers(run) -> None:
    _, state = run
    info = state.tasks["w01-01-split"]
    assert (info.model, info.effort, info.agent) == ("sonnet", "medium", "developer")
    assert state.tasks["w01-05-old"].model is None  # tolerant of absent fields
    assert state.tripped_breakers[0]["id"] == "task-budget-cap"


def test_ledger_limit_blocks(run) -> None:
    inst, _ = run
    by_id = {ln["unit_id"]: ln for ln in ov.read_ledger_lines(inst) if ln["type"] == "unit"}
    split = by_id["w01-01-split"]
    assert split["model"] == "sonnet" and split["turn_cap"] == 30 and split["timeout_s"] == 7200
    assert split["limit"]["basis"] == "self_reported" and split["limit"]["reason"] == "turns"
    assert split["limit"]["cost_vs_task_cap"] == round(10 / 75, 4)
    assert split["limit"]["run_budget_usd"] == 2000.0
    timeout = by_id["w01-02-timeout"]["limit"]
    assert (timeout["basis"], timeout["reason"]) == ("inferred", "time")
    tripped = by_id["w01-03-tripped"]["limit"]
    assert (tripped["basis"], tripped["reason"]) == ("observed", "cost")
    assert tripped["breaker"]["id"] == "task-budget-cap"
    assert by_id["w01-04-plain"]["limit"]["reason"] == "none"
    assert by_id["w01-05-old"]["model"] is None


def test_limit_events_rows_sorted_and_filtered(run) -> None:
    inst, _ = run
    lines = [ln for ln in ov.read_ledger_lines(inst) if ln["type"] == "unit"]
    events = ov.limit_events(lines)
    assert [e["unit_id"] for e in events] == ["w01-01-split", "w01-02-timeout", "w01-03-tripped"]
    assert ov.limit_events(lines, wave=2) == []
    assert events[0]["handoff_reason"] == "turns" and events[0]["ended_at"] == _ended(600)


def test_limit_events_tolerates_pre_a2_ledger_line() -> None:
    old = {"type": "unit", "wave": 1, "unit_id": "w01-01-x", "outcome": "partial", "verdict": "na"}
    (event,) = ov.limit_events([old])
    assert event["model"] is None and event["basis"] == "none" and event["run_budget_usd"] is None


def _args(inst: Path, fmt: str, wave: int | None = None) -> argparse.Namespace:
    return argparse.Namespace(
        workspace_root=str(inst.parent), instance_dir=str(inst), format=fmt, wave=wave
    )


def test_limit_report_json_and_md_deterministic_read_only(run, capsys) -> None:
    inst, _ = run
    before = (inst / "outputs" / "ledger.jsonl").read_bytes()
    ov.limit_report(_args(inst, "json"))
    data = json.loads(capsys.readouterr().out)
    assert len(data["limit_events"]) == 3 and data["limit_gaps"] == list(ov.LIMIT_GAPS)
    ov.limit_report(_args(inst, "md"))
    md1 = capsys.readouterr().out
    ov.limit_report(_args(inst, "md"))
    assert md1 == capsys.readouterr().out
    assert "w01-01-split" in md1 and "turns [self_reported]" in md1 and "## Known gaps" in md1
    assert "inferred" in md1 and "observed" in md1
    assert (inst / "outputs" / "ledger.jsonl").read_bytes() == before


def test_limit_report_cli_subcommand_parses() -> None:
    args = ov._build_parser().parse_args(
        [
            "limit-report",
            "--workspace-root",
            "w",
            "--instance-dir",
            "i",
            "--wave",
            "2",
            "--format",
            "md",
        ]
    )
    assert (args.subcommand, args.wave, args.format) == ("limit-report", 2, "md")
    assert ov._DISPATCH["limit-report"] is ov.limit_report


def test_digest_limit_events_cap_constant() -> None:
    assert ov.LIMIT_EVENTS_CAP == 20 and all(isinstance(g, str) for g in ov.LIMIT_GAPS)
