"""Tests for `overseer_tool.py`'s hold gate, unit gate, and request-closeout (M1).

Ticket: `meta/tickets/E-YAAGhk-overseer-runner-template/T-ABDjSj-tool-state-ledger-budget/`.
Covers AC4 (hold gate: HOLD/INT-2/INT-4 + archive/answer flow), AC5 (unit gate:
BUDGET/FANOUT + zero files written on the success path), AC5b (`request-closeout`: RUNNING
refusal, no_op report/breadcrumb writing, forced_closeout event, and its effect on the next
`ckpt-prep` digest).
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path
from types import ModuleType

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_TOOL_PATH = (
    _REPO_ROOT
    / "src"
    / "agent_orchestrator"
    / "templates"
    / "builtin"
    / "overseer-runner"
    / "tools"
    / "overseer_tool.py"
)


def _load_overseer_tool() -> ModuleType:
    spec = importlib.util.spec_from_file_location("overseer_tool_under_test_gates", _TOOL_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


ov = _load_overseer_tool()

_NOW = "2026-01-01T00:00:00+00:00"


# =============================================================================================
# ===== Fixture helpers ========================================================================
# =============================================================================================


def make_config_dict(**overrides: object) -> dict:
    base: dict = {
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
        "final_push": True,
        "overseer_effort": "high",
        "overseer_model": "",
        "python_bin": "python3",
        "run_budget_usd": 2000,
        "task_budget_usd": 75,
        "stall_waves": 2,
        "stabilize_wave_size": 4,
        "max_stabilize_passes": 2,
        "sub_wave_size": 4,
        "default_unit_cost_usd": 8,
        "default_ckpt_cost_usd": 5,
        "runs_root": ".orchestrator/runs",
        "contract_version": 1,
        "kind_map": {},
    }
    base.update(overrides)
    return base


def write_config(inst: Path, **overrides: object) -> None:
    inst.mkdir(parents=True, exist_ok=True)
    (inst / "overseer-config.json").write_text(json.dumps(make_config_dict(**overrides)))


def write_state(ws: Path, run_id: str, tasks: dict[str, dict], **overrides: object) -> Path:
    base: dict = {
        "run_id": run_id,
        "workflow_id": overrides.get("workflow_id", "wf-1"),
        "repo_set": "main",
        "started_at": _NOW,
        "updated_at": _NOW,
        "status": overrides.get("status", "running"),
        "tasks": tasks,
        "injected_tasks": [{"id": tid} for tid in tasks],
        "breaker_overrides": overrides.get("breaker_overrides", {}),
    }
    path = ws / ".orchestrator" / "runs" / run_id / "state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(base))
    return path


def set_hook_context(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, run_id: str, task_id: str
) -> None:
    ctx_path = tmp_path / f"context-{task_id}.json"
    ctx_path.write_text(
        json.dumps({"run_id": run_id, "task_id": task_id, "repo_paths": {}, "output_paths": []})
    )
    monkeypatch.setenv("AO_HOOK_CONTEXT_PATH", str(ctx_path))


def make_args(
    ws: Path, *, dry_run: bool = False, task_id: str | None = None, reason: str | None = None
):
    return argparse.Namespace(
        workspace_root=str(ws),
        instance_dir="instance",
        now=_NOW,
        dry_run=dry_run,
        task_id=task_id,
        reason=reason,
    )


def write_brief(inst: Path, k: int, unit_id: str, **fields: object) -> None:
    path = ov.brief_path(inst, k, unit_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "schema": "ao.overseer.brief/v1",
        "unit_id": unit_id,
        "wave": k,
        "ask_ids": ["A1"],
        "work_item": "A1/parser",
        "kind": "implement",
        "goal": "do the thing",
        "acceptance": ["it works"],
    }
    data.update(fields)
    path.write_text(json.dumps(data))


# =============================================================================================
# ===== AC4: hold gate =========================================================================
# =============================================================================================


def test_hold_gate_no_pending_hold_and_no_stray_request_is_a_no_op(tmp_path: Path) -> None:
    inst = tmp_path / "instance"
    now = ov.parse_now(_NOW)
    ov.hold_gate(inst, 1, now)  # empty ledger, no control/ files -- must not raise


def test_hold_gate_stray_request_without_hold_decision_is_int4(tmp_path: Path) -> None:
    inst = tmp_path / "instance"
    now = ov.parse_now(_NOW)
    (inst / "control").mkdir(parents=True)
    (inst / "control" / "hold-request.json").write_text(json.dumps({"questions": ["why?"]}))

    with pytest.raises(ov.Violation) as excinfo:
        ov.hold_gate(inst, 1, now)
    assert excinfo.value.rule_id == "INT-4"


def test_hold_gate_request_with_no_answer_is_hold(tmp_path: Path) -> None:
    inst = tmp_path / "instance"
    now = ov.parse_now(_NOW)
    ov.append_chained(
        inst,
        {"type": "event", "event": "checkpoint", "checkpoint": "ck-01", "decision": "hold"},
        now,
    )
    (inst / "control").mkdir(parents=True)
    (inst / "control" / "hold-request.json").write_text(
        json.dumps({"questions": ["why?"], "needs_input_path": "needs-input/ck-02.md"})
    )

    with pytest.raises(ov.Violation) as excinfo:
        ov.hold_gate(inst, 2, now)
    assert excinfo.value.rule_id == "HOLD"
    assert str(excinfo.value).startswith("HOLD:")
    assert "needs-input/ck-02.md" in str(excinfo.value)


def test_hold_gate_answer_older_than_request_is_still_hold(tmp_path: Path) -> None:
    inst = tmp_path / "instance"
    now = ov.parse_now(_NOW)
    ov.append_chained(
        inst,
        {"type": "event", "event": "checkpoint", "checkpoint": "ck-01", "decision": "hold"},
        now,
    )
    (inst / "control").mkdir(parents=True)
    req_path = inst / "control" / "hold-request.json"
    ans_path = inst / "control" / "hold-answer.md"
    ans_path.write_text("my answer")
    req_path.write_text(json.dumps({"questions": ["why?"]}))
    # Force the answer to be OLDER than the request (it was written first above, but make the
    # ordering explicit and unambiguous regardless of filesystem timestamp resolution).
    ans_time = os.stat(ans_path).st_mtime
    os.utime(req_path, (ans_time + 10, ans_time + 10))

    with pytest.raises(ov.Violation) as excinfo:
        ov.hold_gate(inst, 2, now)
    assert excinfo.value.rule_id == "HOLD"


def test_hold_gate_valid_newer_answer_archives_deletes_and_appends_event(tmp_path: Path) -> None:
    inst = tmp_path / "instance"
    now = ov.parse_now(_NOW)
    ov.append_chained(
        inst,
        {"type": "event", "event": "checkpoint", "checkpoint": "ck-01", "decision": "hold"},
        now,
    )
    (inst / "control").mkdir(parents=True)
    req_path = inst / "control" / "hold-request.json"
    ans_path = inst / "control" / "hold-answer.md"
    req_path.write_text(json.dumps({"questions": ["why?"]}))
    req_time = os.stat(req_path).st_mtime
    ans_path.write_text("here is my answer")
    os.utime(ans_path, (req_time + 10, req_time + 10))

    ov.hold_gate(inst, 2, now)  # must not raise -- the hold is answered

    assert not req_path.exists()
    assert not ans_path.exists()
    archived = ov.checkpoint_dir(inst, 2) / "hold"
    assert (archived / "request.json").is_file()
    assert (archived / "answer.md").read_text() == "here is my answer"
    events = [line for line in ov.read_ledger_lines(inst) if line.get("event") == "hold_answered"]
    assert len(events) == 1
    assert events[0]["checkpoint"] == "ck-02"

    # Re-running the gate now (this checkpoint is un-gated, not re-answered) is a plain no-op.
    ov.hold_gate(inst, 2, now)


def test_hold_gate_malformed_request_json_is_int2_not_an_uncaught_exception(
    tmp_path: Path,
) -> None:
    """Regression for a reviewer finding on T-ABDjSj's review: a corrupted
    hold-request.json (a hold WAS decided, so the file must be readable) used to bubble
    an uncaught `json.JSONDecodeError` out of `hold_gate` -- `main()`'s generic except
    would then exit 1 with a bare `repr()`, bypassing the rule-id/`prep-result.json`
    path entirely. It must fail closed through `INT-2` like every other broken-request
    case (deleted file, stray file)."""
    inst = tmp_path / "instance"
    now = ov.parse_now(_NOW)
    ov.append_chained(
        inst,
        {"type": "event", "event": "checkpoint", "checkpoint": "ck-01", "decision": "hold"},
        now,
    )
    (inst / "control").mkdir(parents=True)
    (inst / "control" / "hold-request.json").write_text("{not valid json")

    with pytest.raises(ov.Violation) as excinfo:
        ov.hold_gate(inst, 2, now)
    assert excinfo.value.rule_id == "INT-2"


def test_hold_gate_answered_decision_with_request_deleted_is_int2(tmp_path: Path) -> None:
    inst = tmp_path / "instance"
    now = ov.parse_now(_NOW)
    ov.append_chained(
        inst,
        {"type": "event", "event": "checkpoint", "checkpoint": "ck-01", "decision": "hold"},
        now,
    )
    # No hold-request.json at all -- someone deleted it (or it never existed).

    with pytest.raises(ov.Violation) as excinfo:
        ov.hold_gate(inst, 2, now)
    assert excinfo.value.rule_id == "INT-2"


def test_hold_gate_after_hold_answered_event_is_a_no_op(tmp_path: Path) -> None:
    inst = tmp_path / "instance"
    now = ov.parse_now(_NOW)
    ov.append_chained(
        inst,
        {"type": "event", "event": "checkpoint", "checkpoint": "ck-01", "decision": "hold"},
        now,
    )
    ov.append_chained(inst, {"type": "event", "event": "hold_answered", "checkpoint": "ck-02"}, now)
    # A NEW stray request appearing after the hold was already answered is INT-4, not HOLD.
    (inst / "control").mkdir(parents=True)
    (inst / "control" / "hold-request.json").write_text(json.dumps({"questions": ["another?"]}))

    with pytest.raises(ov.Violation) as excinfo:
        ov.hold_gate(inst, 3, now)
    assert excinfo.value.rule_id == "INT-4"


# =============================================================================================
# ===== AC5: unit gate =========================================================================
# =============================================================================================


def test_unit_gate_over_budget_is_budget_violation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path
    inst = ws / "instance"
    write_config(inst, run_budget_usd=100)
    write_state(ws, "run-1", {"w01-01-a": {"status": "succeeded", "cumulative_cost_usd": 150.0}})
    set_hook_context(monkeypatch, tmp_path, "run-1", "w01-02-b")
    args = make_args(ws)

    with pytest.raises(ov.Violation) as excinfo:
        ov.unit_gate(args)
    assert excinfo.value.rule_id == "BUDGET"
    message = str(excinfo.value)
    assert message.startswith("BUDGET:")
    assert "request-closeout" in message or "budget-override.json" in message
    assert "--extend-breaker" in message


def test_unit_gate_over_fanout_is_fanout_violation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path
    inst = ws / "instance"
    # Shrink `need` (CFG-3's own floor) so a small max_injected_tasks is still config-valid:
    # need = max_waves*(wave_size+1+0) + 3 = 1*(1+1)+3 = 5.
    write_config(inst, wave_size=1, max_waves=1, max_injected_tasks=5)
    tasks = {
        f"w01-{i:02d}-x": {"status": "pending"} for i in range(1, 8)
    }  # 7 > max_injected_tasks=5
    write_state(ws, "run-1", tasks)
    set_hook_context(monkeypatch, tmp_path, "run-1", "w01-08-c")
    args = make_args(ws)

    with pytest.raises(ov.Violation) as excinfo:
        ov.unit_gate(args)
    assert excinfo.value.rule_id == "FANOUT"
    assert str(excinfo.value).startswith("FANOUT:")


def test_unit_gate_pass_writes_zero_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = tmp_path
    inst = ws / "instance"
    write_config(inst)
    write_state(ws, "run-1", {"w01-01-a": {"status": "succeeded", "cumulative_cost_usd": 1.0}})
    set_hook_context(monkeypatch, tmp_path, "run-1", "w01-02-b")
    args = make_args(ws)

    before = sorted(str(p) for p in ws.rglob("*") if p.is_file())
    ov.unit_gate(args)  # must not raise
    after = sorted(str(p) for p in ws.rglob("*") if p.is_file())

    assert before == after


# =============================================================================================
# ===== AC5b: request-closeout =================================================================
# =============================================================================================


def _write_workflow_json(inst: Path, workflow_id: str = "wf-1") -> None:
    inst.mkdir(parents=True, exist_ok=True)
    (inst / "workflow.json").write_text(json.dumps({"id": workflow_id}))


def test_request_closeout_refuses_while_running(tmp_path: Path) -> None:
    ws = tmp_path
    inst = ws / "instance"
    write_config(inst)
    _write_workflow_json(inst)
    write_state(ws, "run-1", {"w01-01-a": {"status": "pending"}}, status="running")
    args = make_args(ws, reason="operator asked")

    with pytest.raises(ov.Violation) as excinfo:
        ov.request_closeout(args)
    assert excinfo.value.rule_id == "RUNNING"


def test_request_closeout_writes_no_op_for_pending_units_and_never_overwrites(
    tmp_path: Path,
) -> None:
    ws = tmp_path
    inst = ws / "instance"
    write_config(inst)
    _write_workflow_json(inst)
    write_state(
        ws,
        "run-1",
        {
            "w01-01-a": {"status": "pending"},
            "w01-02-b": {"status": "pending"},
            "w01-03-c": {"status": "succeeded", "cumulative_cost_usd": 1.0},
        },
        status="failed",
    )
    # w01-02-b already has a real report -- request-closeout must never overwrite it.
    existing_report = inst / "outputs" / "waves" / "w01" / "w01-02-b.md"
    existing_report.parent.mkdir(parents=True, exist_ok=True)
    existing_report.write_text("real work happened here")

    args = make_args(ws, reason="operator asked for close-out")
    ov.request_closeout(args)

    new_report = inst / "outputs" / "waves" / "w01" / "w01-01-a.md"
    assert new_report.is_file()
    assert "skipped: operator requested close-out" in new_report.read_text()
    new_crumb = json.loads(ov.breadcrumb_path(inst, "w01-01-a").read_text())
    assert new_crumb["outcome"] == "no_op"
    assert new_crumb["verdict"] == "na"

    # Never overwritten.
    assert existing_report.read_text() == "real work happened here"
    assert not ov.breadcrumb_path(inst, "w01-02-b").exists()

    # succeeded unit untouched.
    assert not (inst / "outputs" / "waves" / "w01" / "w01-03-c.md").exists()

    events = [line for line in ov.read_ledger_lines(inst) if line.get("event") == "forced_closeout"]
    assert len(events) == 1
    assert events[0]["reason"] == "operator asked for close-out"
    assert "w01-01-a" in events[0]["pending_units"]


def test_request_closeout_second_call_appends_no_new_report_for_already_skipped_unit(
    tmp_path: Path,
) -> None:
    ws = tmp_path
    inst = ws / "instance"
    write_config(inst)
    _write_workflow_json(inst)
    write_state(ws, "run-1", {"w01-01-a": {"status": "pending"}}, status="failed")
    args = make_args(ws, reason="first call")

    ov.request_closeout(args)
    report_path = inst / "outputs" / "waves" / "w01" / "w01-01-a.md"
    first_content = report_path.read_text()

    args2 = make_args(ws, reason="second call")
    ov.request_closeout(args2)
    assert report_path.read_text() == first_content  # untouched by the second call

    events = [line for line in ov.read_ledger_lines(inst) if line.get("event") == "forced_closeout"]
    assert len(events) == 2  # one event per call, but no NEW report/breadcrumb writes


def test_request_closeout_then_ckpt_prep_digest_has_must_close_and_closeout_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path
    inst = ws / "instance"
    write_config(inst)
    _write_workflow_json(inst)
    write_brief(inst, 1, "w01-01-a")
    write_state(ws, "run-1", {"w01-01-a": {"status": "pending"}}, status="failed")

    closeout_args = make_args(ws, reason="operator forced close-out")
    ov.request_closeout(closeout_args)

    # Simulate `ao resume`: the engine marks the skipped unit "skipped" and the run resumes.
    write_state(
        ws,
        "run-1",
        {"w01-01-a": {"status": "skipped", "cumulative_cost_usd": 0.0}},
        status="running",
    )
    set_hook_context(monkeypatch, tmp_path, "run-1", "ck-01")
    ckpt_args = make_args(ws)

    ov.ckpt_prep(ckpt_args)

    digest = json.loads(ov.digest_path(inst, 1).read_text())
    assert digest["must_close"] is True
    assert any("forced_closeout" in reason for reason in digest["must_close_reasons"])
    assert digest["allowed_decisions"] == ["closeout"]

    unit_lines = [line for line in ov.read_ledger_lines(inst) if line["type"] == "unit"]
    assert unit_lines[0]["engine_status"] == "skipped"
    assert unit_lines[0]["outcome"] == "no_op"


# =============================================================================================
# ===== Extra coverage: intake-prep, resolve_run_id_for_instance, parse_ck_task_id, main() ====
# =============================================================================================


def test_intake_prep_happy_path_creates_output_dirs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ws = tmp_path
    inst = ws / "instance"
    write_config(inst)
    write_state(ws, "run-1", {})
    set_hook_context(monkeypatch, tmp_path, "run-1", "intake")
    args = make_args(ws)

    ov.intake_prep(args)

    for rel in ov._OUTPUT_SUBDIRS:
        assert (inst / rel).is_dir()
    captured = capsys.readouterr()
    assert "python" in captured.out


def test_intake_prep_missing_state_file_is_st1_and_writes_prep_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path
    inst = ws / "instance"
    write_config(inst)
    # No state.json written at all for run-1.
    set_hook_context(monkeypatch, tmp_path, "run-1", "intake")
    args = make_args(ws)

    with pytest.raises(ov.Violation) as excinfo:
        ov.intake_prep(args)
    assert excinfo.value.rule_id == "ST-1"

    result = json.loads((inst / "outputs" / ov.RESULT_FILENAME).read_text())
    assert result["ok"] is False
    assert result["rule_id"] == "ST-1"


def test_intake_prep_dry_run_violation_writes_no_prep_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path
    inst = ws / "instance"
    write_config(inst)
    set_hook_context(monkeypatch, tmp_path, "run-1", "intake")
    args = make_args(ws, dry_run=True)

    with pytest.raises(ov.Violation):
        ov.intake_prep(args)
    assert not (inst / "outputs" / ov.RESULT_FILENAME).exists()


def test_parse_ck_task_id_rejects_non_checkpoint_ids() -> None:
    assert ov.parse_ck_task_id("ck-07") == 7
    with pytest.raises(ov.ToolError):
        ov.parse_ck_task_id("w01-01-a")


def test_ckpt_prep_violation_writes_prep_result_into_checkpoint_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path
    inst = ws / "instance"
    write_config(inst)
    write_state(ws, "run-1", {})
    # A stray hold-request.json with no backing hold decision -> INT-4, raised AFTER `k` is
    # already known, so the result lands in this checkpoint's own output dir.
    (inst / "control").mkdir(parents=True)
    (inst / "control" / "hold-request.json").write_text(json.dumps({"questions": ["x"]}))
    set_hook_context(monkeypatch, tmp_path, "run-1", "ck-01")
    args = make_args(ws)

    with pytest.raises(ov.Violation) as excinfo:
        ov.ckpt_prep(args)
    assert excinfo.value.rule_id == "INT-4"

    result = json.loads((ov.checkpoint_dir(inst, 1) / ov.RESULT_FILENAME).read_text())
    assert result["rule_id"] == "INT-4"


def test_resolve_run_id_for_instance_missing_workflow_json_is_st1(tmp_path: Path) -> None:
    ws = tmp_path
    inst = ws / "instance"
    write_config(inst)
    cfg = ov.load_config(inst)
    with pytest.raises(ov.Violation) as excinfo:
        ov.resolve_run_id_for_instance(ws, inst, cfg)
    assert excinfo.value.rule_id == "ST-1"


def test_resolve_run_id_for_instance_malformed_workflow_json_is_st1(tmp_path: Path) -> None:
    """Regression for a reviewer finding on T-ABDjSj's review: a corrupted workflow.json
    used to bubble an uncaught `json.JSONDecodeError` instead of failing closed through
    ST-1 -- consistent with this same function's own per-candidate state.json reads,
    which already caught this three lines further down."""
    ws = tmp_path
    inst = ws / "instance"
    write_config(inst)
    (inst / "workflow.json").write_text("{not valid json")
    cfg = ov.load_config(inst)
    with pytest.raises(ov.Violation) as excinfo:
        ov.resolve_run_id_for_instance(ws, inst, cfg)
    assert excinfo.value.rule_id == "ST-1"


def test_resolve_run_id_for_instance_no_matching_run_is_st1(tmp_path: Path) -> None:
    ws = tmp_path
    inst = ws / "instance"
    write_config(inst)
    _write_workflow_json(inst, workflow_id="wf-does-not-exist")
    cfg = ov.load_config(inst)
    with pytest.raises(ov.Violation) as excinfo:
        ov.resolve_run_id_for_instance(ws, inst, cfg)
    assert excinfo.value.rule_id == "ST-1"


def test_resolve_run_id_for_instance_skips_malformed_state_files_and_picks_latest(
    tmp_path: Path,
) -> None:
    ws = tmp_path
    inst = ws / "instance"
    write_config(inst)
    _write_workflow_json(inst, workflow_id="wf-1")

    # A malformed state.json under a candidate run dir must be skipped, not crash resolution.
    malformed_dir = ws / ".orchestrator" / "runs" / "run-bad"
    malformed_dir.mkdir(parents=True)
    (malformed_dir / "state.json").write_text("{not valid")

    write_state(ws, "run-old", {}, workflow_id="wf-1")
    old_state_path = ws / ".orchestrator" / "runs" / "run-old" / "state.json"
    old_state = json.loads(old_state_path.read_text())
    old_state["started_at"] = "2020-01-01T00:00:00+00:00"
    old_state_path.write_text(json.dumps(old_state))

    write_state(ws, "run-new", {}, workflow_id="wf-1")
    new_state_path = ws / ".orchestrator" / "runs" / "run-new" / "state.json"
    new_state = json.loads(new_state_path.read_text())
    new_state["started_at"] = "2030-01-01T00:00:00+00:00"
    new_state_path.write_text(json.dumps(new_state))

    cfg = ov.load_config(inst)
    assert ov.resolve_run_id_for_instance(ws, inst, cfg) == "run-new"


# =============================================================================================
# ===== main() / argparse (CLI boundary, exit codes 0/1/2) ====================================
# =============================================================================================


def test_main_exit_0_on_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = tmp_path
    inst = ws / "instance"
    write_config(inst)
    write_state(ws, "run-1", {"w01-01-a": {"status": "succeeded", "cumulative_cost_usd": 1.0}})
    set_hook_context(monkeypatch, tmp_path, "run-1", "w01-02-b")

    code = ov.main(
        [
            "unit-gate",
            "--workspace-root",
            str(ws),
            "--instance-dir",
            "instance",
            "--now",
            _NOW,
        ]
    )
    assert code == 0


def test_main_exit_2_on_violation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ws = tmp_path
    inst = ws / "instance"
    write_config(inst, run_budget_usd=1)
    write_state(ws, "run-1", {"w01-01-a": {"status": "succeeded", "cumulative_cost_usd": 5.0}})
    set_hook_context(monkeypatch, tmp_path, "run-1", "w01-02-b")

    code = ov.main(
        ["unit-gate", "--workspace-root", str(ws), "--instance-dir", "instance", "--now", _NOW]
    )
    assert code == 2
    captured = capsys.readouterr()
    assert captured.err.startswith("BUDGET:")


def test_main_exit_1_on_internal_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ws = tmp_path
    inst = ws / "instance"
    write_config(inst)
    write_state(ws, "run-1", {})
    # `--dry-run` with a --task-id that is not a valid "ck-NN" checkpoint id raises a plain
    # ToolError (not a Violation) from `parse_ck_task_id` -- an internal-error exit, not a
    # rule-id violation, since it signals a wiring problem, not a data contract break.
    code = ov.main(
        [
            "ckpt-prep",
            "--workspace-root",
            str(ws),
            "--instance-dir",
            "instance",
            "--now",
            _NOW,
            "--dry-run",
            "--task-id",
            "not-a-checkpoint",
        ]
    )
    assert code == 1
    captured = capsys.readouterr()
    assert captured.err.startswith("overseer_tool internal error:")


def test_main_request_closeout_requires_reason_argparse_error(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        ov.main(
            ["request-closeout", "--workspace-root", str(tmp_path), "--instance-dir", "instance"]
        )
