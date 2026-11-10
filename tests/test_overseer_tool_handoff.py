"""Tests for `overseer_tool.py`'s A3 self-split handoff state (validate/ingest/signals/digest).

Checker-side R13 coverage lives in `test_overseer_tool_checker_semantic.py`.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

_TOOL_PATH = (
    Path(__file__).resolve().parents[1]
    / "src/agent_orchestrator/templates/builtin/overseer-runner/tools/overseer_tool.py"
)


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("overseer_tool_under_test_handoff", _TOOL_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


ov = _load()
_NOW = "2026-01-01T00:00:00+00:00"

_GOOD = {
    "reason": "time",
    "stopped_at": "parser done",
    "staged": ["core:abc123"],
    "remaining": "wire CLI",
    "next_hint": "start at cli.py",
}


def crumb(**over: object) -> dict:
    base = {
        "outcome": "partial",
        "verdict": "pass",
        "needs_input": False,
        "changed_paths": ["core:a.py"],
        "handoff": dict(_GOOD),
    }
    base.update(over)
    return base


def line(unit_id: str, wave: int, *, handoff: dict | None = None, **over: object) -> dict:
    base = {
        "type": "unit",
        "wave": wave,
        "unit_id": unit_id,
        "ask_ids": ["A1"],
        "work_item": "A1/x",
        "kind": "implement",
        "outcome": "partial" if handoff else "done",
        "verdict": "pass",
        "changed_paths": ["core:a.py"],
        "needs_input": False,
        "handoff": handoff,
    }
    base.update(over)
    return base


def test_validate_handoff_valid_and_truncates() -> None:
    long = dict(_GOOD, remaining="x" * 900)
    handoff, bad = ov.validate_handoff(crumb(handoff=long))
    assert bad is None and handoff is not None
    assert len(handoff["remaining"]) == ov.HANDOFF_REMAINING_CAP


def test_validate_handoff_not_a_candidate_cases() -> None:
    assert ov.validate_handoff({"outcome": "done"}) == (None, None)
    for over in ({"outcome": "done"}, {"verdict": "fail"}, {"needs_input": True}):
        handoff, bad = ov.validate_handoff(crumb(**over))
        assert handoff is None and bad


def test_validate_handoff_invalid_variants() -> None:
    for patch in ({"reason": "bored"}, {"stopped_at": ""}, {"remaining": " "}, {"staged": "x"}):
        handoff, bad = ov.validate_handoff(crumb(handoff=dict(_GOOD, **patch)))
        assert handoff is None and bad
    assert ov.validate_handoff(crumb(handoff="oops"))[1]


def test_validate_handoff_requires_something_staged() -> None:
    handoff, bad = ov.validate_handoff(crumb(handoff=dict(_GOOD, staged=[]), changed_paths=[]))
    assert handoff is None and "nothing staged" in bad
    handoff, bad = ov.validate_handoff(crumb(handoff=dict(_GOOD, staged=[])))
    assert bad is None and handoff is not None


def test_unit_state_values() -> None:
    assert ov.unit_state(line("u", 1, handoff=_GOOD)) == "handoff"
    assert ov.unit_state(line("u", 1)) == "done"
    assert ov.unit_state(line("u", 1, verdict="fail", outcome="partial")) == "failed"
    assert ov.unit_state(line("u", 1, outcome="partial")) == "partial"
    assert ov.unit_state(line("u", 1, outcome="blocked")) == "blocked"
    assert ov.unit_state(line("u", 1, handoff=_GOOD, needs_input=True)) == "blocked"


def _ingest(tmp_path: Path, breadcrumb: dict) -> dict:
    inst = tmp_path / "inst"
    brief = ov.brief_path(inst, 1, "w01-01-a")
    brief.parent.mkdir(parents=True)
    brief.write_text(json.dumps({"ask_ids": ["A1"], "work_item": "A1/x", "kind": "implement"}))
    cr = ov.breadcrumb_path(inst, "w01-01-a")
    cr.parent.mkdir(parents=True)
    cr.write_text(json.dumps(dict(breadcrumb, unit_id="w01-01-a")))
    state = ov.State(
        tasks={
            "w01-01-a": ov.TaskInfo(
                status="succeeded",
                attempts=1,
                started_at=None,
                ended_at=None,
                cumulative_cost_usd=1.0,
            )
        },
        injected_ids=["w01-01-a"],
        breaker_overrides={},
        spent=1.0,
        status="running",
    )
    assert ov.ingest_ledger(inst, ["w01-01-a"], state, 1, ov.parse_now(_NOW)) == 1
    assert ov.ingest_ledger(inst, ["w01-01-a"], state, 1, ov.parse_now(_NOW)) == 0
    ov.verify_ledger_chain(inst)
    return next(ln for ln in ov.read_ledger_lines(inst) if ln["type"] == "unit")


def test_ingest_records_handoff_and_state(tmp_path: Path) -> None:
    ln = _ingest(tmp_path, crumb())
    assert ln["state"] == "handoff"
    assert ln["handoff"]["reason"] == "time"
    assert "handoff_invalid" not in ln


def test_ingest_invalid_handoff_degrades_to_plain_partial(tmp_path: Path) -> None:
    ln = _ingest(tmp_path, crumb(handoff=dict(_GOOD, reason="bored")))
    assert ln["state"] == "partial"
    assert ln["handoff"] is None
    assert "reason" in ln["handoff_invalid"]


def test_handoffs_excluded_from_attempt_cap_and_period() -> None:
    cfg = type("C", (), {"max_attempts_per_item": 2})()
    lines = [line(f"w0{i}-01-a", i, handoff=_GOOD) for i in (1, 2, 3)]
    assert ov._attempt_cap_signals(cfg, lines) == []
    assert ov._period_mirror_signals(lines) == []
    # a plain (non-handoff) repeat is still detected
    plain = [line(f"w0{i}-01-a", i) for i in (1, 2, 3)]
    assert {s["type"] for s in ov._period_mirror_signals(plain)} == {"period_repeat"}
    assert len(ov._attempt_cap_signals(cfg, plain)) == 1


def test_repeated_failure_ignores_handoff_between_failures() -> None:
    lines = [
        line("w01-01-a", 1, verdict="fail"),
        line("w02-01-a", 2, handoff=_GOOD),
        line("w03-01-a", 3, verdict="fail"),
    ]
    assert len(ov._repeated_failure_signals(lines)) == 1


def test_handoff_chain_signal_severity_and_reset() -> None:
    two = [line(f"w0{i}-01-a", i, handoff=_GOOD) for i in (1, 2)]
    sig = ov._handoff_chain_signals(two)
    assert [s["severity"] for s in sig] == ["medium"]
    three = two + [line("w03-01-a", 3, handoff=_GOOD)]
    sig = ov._handoff_chain_signals(three)
    assert sig[0]["severity"] == "high" and sig[0]["evidence"]["chain"] == 3
    assert sig[0]["evidence"]["reasons"] == ["time"] * 3
    assert ov._handoff_chain_signals(three + [line("w04-01-a", 4)]) == []
    assert ov._handoff_chain_signals(two[:1]) == []


def test_open_handoffs_digest_entries_sorted_and_closed_by_done() -> None:
    lines = [
        line("w01-01-a", 1, handoff=_GOOD, work_item="A1/b"),
        line("w01-02-b", 1, handoff=_GOOD, work_item="A1/a"),
        line("w02-01-c", 2, handoff=_GOOD, work_item="A1/c"),
        line("w03-01-c", 3, work_item="A1/c"),  # continuation finished -> no longer open
    ]
    out = ov.open_handoffs(lines)
    assert [h["work_item"] for h in out] == ["A1/a", "A1/b"]
    assert out[0]["remaining"] == "wire CLI" and out[0]["chain"] == 1


def _stall_fixture(tmp_path: Path, lines: list[dict]) -> int:
    cfg = type("C", (), {"stall_waves": 1})()
    return ov._consecutive_stall_waves(cfg, tmp_path, max(ln["wave"] for ln in lines), lines)


def test_stall_counts_staged_handoff_as_productive(tmp_path: Path) -> None:
    assert _stall_fixture(tmp_path, [line("w01-01-a", 1, handoff=_GOOD)]) == 0
    unstaged = line("w01-01-a", 1, handoff=_GOOD, changed_paths=[])
    assert _stall_fixture(tmp_path, [unstaged]) == 1


def test_progress_counts_handed_off_not_done(tmp_path: Path) -> None:
    inst = tmp_path
    for ln in (line("w01-01-a", 1, handoff=_GOOD),):
        ov.append_chained(inst, ln, ov.parse_now(_NOW), dry_run=False)
    cfg = type("C", (), {"stall_waves": 2})()
    prog = ov.compute_progress(cfg, inst, 1)
    assert prog["work_items_handed_off"] == 1
    assert prog["work_items_done"] == 0
    assert prog["per_ask"][0]["done_items"] == 0
