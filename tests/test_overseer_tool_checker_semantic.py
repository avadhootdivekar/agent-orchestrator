"""Tests for `overseer_tool.py`'s M3 SEMANTIC contract checks -- T-tAKBBB, the semantic half
of M3 (rules R11, R12, R13, R13c, R14, R16), plus the AC3 post-success ledger events
(`checkpoint`/`hold_requested`) `ckpt_check` now appends. Structural rules R1-R10/R15
(T-HPJcc6) are NOT re-tested here -- see `test_overseer_tool_checker_structural.py`.

Ticket: `meta/tickets/E-YAAGhk-overseer-runner-template/T-tAKBBB-tool-semantic-checkers/`.
Mirrors the fixture-helper conventions already established by
`test_overseer_tool_checker_structural.py` (each test file is self-contained -- no
cross-test-file imports; every helper below is a purpose-built variant, not a shared import).
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_TEMPLATE_DIR = (
    _REPO_ROOT / "src" / "agent_orchestrator" / "templates" / "builtin" / "overseer-runner"
)
_TOOL_PATH = _TEMPLATE_DIR / "tools" / "overseer_tool.py"


def _load_overseer_tool() -> ModuleType:
    spec = importlib.util.spec_from_file_location("overseer_tool_under_test_semantic", _TOOL_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


ov = _load_overseer_tool()

_NOW = "2026-01-01T00:00:00+00:00"
_NOW_DT = ov.parse_now(_NOW)
_INSTANCE_DIR = "instance"

_WORK_UNIT_INSTRUCTION = "workflows/overseer-runner/instructions/10-work-unit.md"
_STABILIZE_INSTRUCTION = "workflows/overseer-runner/instructions/11-stabilize-unit.md"

_KIND_MAP = {
    "research": {"agent": "architect", "instruction": _WORK_UNIT_INSTRUCTION},
    "design": {"agent": "architect", "instruction": _WORK_UNIT_INSTRUCTION},
    "implement": {"agent": "developer", "instruction": _WORK_UNIT_INSTRUCTION},
    "fix": {"agent": "developer", "instruction": _WORK_UNIT_INSTRUCTION},
    "document": {"agent": "developer", "instruction": _WORK_UNIT_INSTRUCTION},
    "stabilize": {"agent": "developer", "instruction": _STABILIZE_INSTRUCTION},
    "test": {"agent": "tester", "instruction": _WORK_UNIT_INSTRUCTION},
    "verify": {"agent": "tester", "instruction": _WORK_UNIT_INSTRUCTION},
    "review": {"agent": "reviewer", "instruction": _WORK_UNIT_INSTRUCTION},
}

_DEFAULT_ALLOWED_DECISIONS = ["continue", "redirect", "hold", "stabilize", "closeout"]


# =============================================================================================
# ===== Fixture helpers (self-contained -- mirrors test_overseer_tool_checker_structural.py) ==
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
        "kind_map": _KIND_MAP,
    }
    base.update(overrides)
    return base


def write_config(inst: Path, **overrides: object) -> dict:
    inst.mkdir(parents=True, exist_ok=True)
    cfg_dict = make_config_dict(**overrides)
    (inst / "overseer-config.json").write_text(json.dumps(cfg_dict))
    return cfg_dict


def write_state(ws: Path, run_id: str, tasks: dict[str, dict], **overrides: object) -> Path:
    base: dict = {
        "run_id": run_id,
        "workflow_id": "wf-1",
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


def make_args(ws: Path, *, dry_run: bool = False, task_id: str | None = None) -> argparse.Namespace:
    return argparse.Namespace(
        workspace_root=str(ws),
        instance_dir=_INSTANCE_DIR,
        now=_NOW,
        dry_run=dry_run,
        task_id=task_id,
        reason=None,
    )


def write_prompt(inst: Path, text: str = "Build the thing, end to end.") -> str:
    inst.mkdir(parents=True, exist_ok=True)
    (inst / "prompt.md").write_text(text)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def make_charter_dict(prompt_sha256: str, **overrides: object) -> dict:
    base: dict = {
        "schema": ov.CHARTER_SCHEMA,
        "prompt_sha256": prompt_sha256,
        "asks": [
            {
                "ask_id": "A1",
                "statement": "Do the thing.",
                "deliverable_type": "code",
                "acceptance": [{"id": "A1.1", "text": "it works"}],
                "usable_bar": ["it compiles"],
                "priority": 1,
            }
        ],
        "global_constraints": [],
        "assumptions": [],
        "out_of_scope": [],
        "open_questions": [],
    }
    base.update(overrides)
    return base


def write_charter(inst: Path, charter: dict) -> None:
    (inst / "outputs").mkdir(parents=True, exist_ok=True)
    (inst / "outputs" / "charter.json").write_text(json.dumps(charter))


def write_brief(inst: Path, wave: int, unit_id: str, **overrides: object) -> None:
    base: dict = {
        "schema": ov.BRIEF_SCHEMA,
        "unit_id": unit_id,
        "wave": wave,
        "ask_ids": ["A1"],
        "work_item": "A1/parser",
        "kind": "implement",
        "goal": "do it",
        "acceptance": ["it works"],
        "approach_change": None,
        "prior_attempts": [],
        "context_paths": [],
        "touches": [],
    }
    base.update(overrides)
    path = ov.brief_path(inst, wave, unit_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(base))


def write_manifest_entries(inst: Path, filename: str, entries: list[dict]) -> Path:
    path = inst / "outputs" / "manifests" / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"tasks": entries}))
    return path


def make_unit_entry(
    wave: int, num: int, slug: str, *, emitter: str, kind: str = "implement", **overrides: object
) -> dict:
    unit_id = f"w{wave:02d}-{num:02d}-{slug}"
    kind_entry = _KIND_MAP[kind]
    entry: dict = {
        "id": unit_id,
        "agent": kind_entry["agent"],
        "instruction": kind_entry["instruction"],
        "depends_on": [emitter],
        "inputs": [
            f"{_INSTANCE_DIR}/overseer-contract.md",
            f"{_INSTANCE_DIR}/outputs/charter.json",
            f"{_INSTANCE_DIR}/outputs/waves/w{wave:02d}/briefs/{unit_id}.json",
        ],
        "outputs": [
            f"{_INSTANCE_DIR}/outputs/waves/w{wave:02d}/{unit_id}.md",
            f"{_INSTANCE_DIR}/outputs/progress/{unit_id}.json",
        ],
        "pre_hook": {"use": "ov-unit-gate"},
        "timeout_seconds": 7200,
        "skip_if_outputs_exist": True,
        "effort": "medium",
    }
    entry.update(overrides)
    return entry


def make_next_ckpt_entry(
    next_wave: int, unit_ids: list[str], *, emitter: str, cfg_dict: dict, **overrides: object
) -> dict:
    ck_id = f"ck-{next_wave:02d}"
    entry: dict = {
        "id": ck_id,
        "agent": "manager",
        "instruction": ov._CHECKPOINT_INSTRUCTION,
        "depends_on": list(unit_ids) if unit_ids else [emitter],
        "inputs": [
            f"{_INSTANCE_DIR}/overseer-contract.md",
            f"{_INSTANCE_DIR}/overseer-config.json",
            f"{_INSTANCE_DIR}/outputs/charter.json",
            *(f"{_INSTANCE_DIR}/outputs/progress/{uid}.json" for uid in unit_ids),
        ],
        "outputs": [
            f"{_INSTANCE_DIR}/outputs/checkpoints/{ck_id}/digest.json",
            f"{_INSTANCE_DIR}/outputs/checkpoints/{ck_id}/verdict.json",
            f"{_INSTANCE_DIR}/outputs/checkpoints/{ck_id}/report.md",
        ],
        "emit_tasks": True,
        "task_manifest_path": f"{_INSTANCE_DIR}/outputs/manifests/{ck_id}.json",
        "pre_hook": {"use": "ov-ckpt-prep"},
        "post_hook": {"use": "ov-ckpt-check", "on_failure": "fail_task"},
        "timeout_seconds": 3600,
        "skip_if_outputs_exist": False,
        "effort": cfg_dict["overseer_effort"],
    }
    if cfg_dict.get("overseer_model"):
        entry["model"] = cfg_dict["overseer_model"]
    entry.update(overrides)
    return entry


def make_tail_entries(*, emitter: str, final_push: bool = True) -> list[dict]:
    verify_inputs = [
        f"{_INSTANCE_DIR}/outputs/charter.json",
        f"{_INSTANCE_DIR}/outputs/checkpoints/{emitter}/verdict.json",
    ]
    entries = [
        {
            "id": "final-verify",
            "agent": "tester",
            "instruction": ov._FINAL_VERIFY_INSTRUCTION,
            "depends_on": [emitter],
            "inputs": verify_inputs,
            "outputs": [f"{_INSTANCE_DIR}/outputs/final/verify.md"],
            "timeout_seconds": 7200,
            "skip_if_outputs_exist": False,
        },
        {
            "id": "closeout",
            "agent": "manager",
            "instruction": ov._CLOSEOUT_INSTRUCTION,
            "depends_on": ["final-verify"],
            "inputs": [
                f"{_INSTANCE_DIR}/outputs/charter.json",
                f"{_INSTANCE_DIR}/outputs/final/verify.md",
            ],
            "outputs": [f"{_INSTANCE_DIR}/outputs/final/closeout.md"],
            "timeout_seconds": 3600,
            "skip_if_outputs_exist": False,
        },
    ]
    if final_push:
        entries.append(
            {
                "id": "final-push",
                "agent": "git-operator",
                "instruction": ov._FINAL_PUSH_INSTRUCTION,
                "depends_on": ["closeout"],
                "inputs": [f"{_INSTANCE_DIR}/outputs/final/closeout.md"],
                "outputs": [f"{_INSTANCE_DIR}/outputs/final/push-report.md"],
                "timeout_seconds": 2400,
                "skip_if_outputs_exist": False,
                "isolation": "none",
            }
        )
    return entries


def write_digest(
    inst: Path,
    k: int,
    *,
    allowed_wave_size: int = 6,
    stage: str = "explore",
    allowed_decisions: list[str] | None = None,
    must_close: bool = False,
    signals: list[dict] | None = None,
) -> None:
    data = {
        "schema": ov.DIGEST_SCHEMA,
        "cadence": {"allowed_wave_size": allowed_wave_size},
        "budget": {"stage": stage},
        "allowed_decisions": list(allowed_decisions)
        if allowed_decisions is not None
        else list(_DEFAULT_ALLOWED_DECISIONS),
        "must_close": must_close,
        "signals": signals or [],
    }
    path = ov.digest_path(inst, k)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def mutate_digest(inst: Path, k: int, **overrides: object) -> dict:
    path = ov.digest_path(inst, k)
    data = json.loads(path.read_text())
    data.update(overrides)
    path.write_text(json.dumps(data))
    return data


def write_verdict(inst: Path, k: int, **overrides: object) -> dict:
    base: dict = {
        "schema": ov.VERDICT_SCHEMA,
        "checkpoint": f"ck-{k:02d}",
        "stage": "explore",
        "decision": "continue",
        "rationale": "keep going",
        "next_wave_goal": "finish the work",
        "alignment": [{"ask_id": "A1", "status": "on_track", "evidence": "progressing well"}],
        "criteria": [{"id": "A1.1", "status": "unmet"}],
        "signal_responses": [],
        "hold_questions": [],
    }
    base.update(overrides)
    path = ov.verdict_path(inst, k)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(base))
    return base


def mutate_verdict(inst: Path, k: int, **overrides: object) -> dict:
    path = ov.verdict_path(inst, k)
    data = json.loads(path.read_text())
    data.update(overrides)
    path.write_text(json.dumps(data))
    return data


def write_hold_request(inst: Path, **overrides: object) -> None:
    base: dict = {
        "schema": ov.HOLD_SCHEMA,
        "checkpoint": "ck-01",
        "created_at": _NOW,
        "questions": ["what should we do?"],
        "needs_input_path": "control/needs-input.md",
    }
    base.update(overrides)
    path = inst / "control" / "hold-request.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(base))


def append_ledger_unit(
    inst: Path,
    *,
    wave: int,
    unit_id: str,
    work_item: str,
    kind: str,
    unit_verdict: str = "pass",
    outcome: str = "done",
    ask_ids: tuple[str, ...] = ("A1",),
) -> None:
    ov.append_chained(
        inst,
        {
            "type": "unit",
            "wave": wave,
            "unit_id": unit_id,
            "ask_ids": list(ask_ids),
            "work_item": work_item,
            "kind": kind,
            "attempt_no": 1,
            "outcome": outcome,
            "verdict": unit_verdict,
            "engine_status": "succeeded",
            "cost_usd": 1.0,
            "duration_s": 10.0,
            "changed_paths": [],
            "needs_input": False,
        },
        _NOW_DT,
        dry_run=False,
    )


def append_ledger_event(inst: Path, event: dict) -> None:
    ov.append_chained(inst, event, _NOW_DT, dry_run=False)


def rule_ids_of(violations: list) -> set[str]:
    return {v.rule_id for v in violations}


def load_check_result(inst: Path, k: int) -> dict:
    return json.loads((ov.checkpoint_dir(inst, k) / "check-result.json").read_text())


# =============================================================================================
# ===== R11: stage restrictions ================================================================
# =============================================================================================


def setup_r11_baseline(
    ws: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    stage: str,
    existing_work_item: str = "A1/parser",
    **cfg_overrides: object,
) -> tuple[Path, dict, str, int]:
    inst = ws / _INSTANCE_DIR
    cfg_dict = write_config(inst, **cfg_overrides)
    prompt_sha256 = write_prompt(inst)
    write_charter(inst, make_charter_dict(prompt_sha256))
    k = 1
    emitter = f"ck-{k:02d}"
    next_wave = k + 1
    hist_unit_id = f"w{k:02d}-01-hist"
    write_brief(inst, k, hist_unit_id, work_item=existing_work_item, kind="implement")
    append_ledger_unit(
        inst, wave=k, unit_id=hist_unit_id, work_item=existing_work_item, kind="implement"
    )
    write_digest(inst, k, allowed_wave_size=cfg_dict["wave_size"], stage=stage)
    write_state(ws, "run-1", {})
    set_hook_context(monkeypatch, tmp_path, "run-1", emitter)
    return inst, cfg_dict, emitter, next_wave


def run_r11_case(
    ws: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    stage: str,
    new_work_item: str,
    new_kind: str,
    existing_work_item: str = "A1/parser",
) -> Path:
    inst, cfg_dict, emitter, next_wave = setup_r11_baseline(
        ws, tmp_path, monkeypatch, stage=stage, existing_work_item=existing_work_item
    )
    unit_id = f"w{next_wave:02d}-01-new"
    write_brief(inst, next_wave, unit_id, work_item=new_work_item, kind=new_kind)
    unit_entry = make_unit_entry(next_wave, 1, "new", emitter=emitter, kind=new_kind)
    ckpt_entry = make_next_ckpt_entry(next_wave, [unit_id], emitter=emitter, cfg_dict=cfg_dict)
    write_manifest_entries(inst, f"{emitter}.json", [unit_entry, ckpt_entry])
    return inst


def test_r11_converge_existing_work_item_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path
    run_r11_case(
        ws, tmp_path, monkeypatch, stage="converge", new_work_item="A1/parser", new_kind="implement"
    )
    ov.ckpt_check(make_args(ws))  # must not raise


def test_r11_converge_new_work_item_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = tmp_path
    run_r11_case(
        ws,
        tmp_path,
        monkeypatch,
        stage="converge",
        new_work_item="A1/brand-new-scope",
        new_kind="implement",
    )
    with pytest.raises(ov.CheckViolations) as excinfo:
        ov.ckpt_check(make_args(ws))
    assert "R11" in rule_ids_of(excinfo.value.violations)


def test_r11_stabilize_allowed_kind_passes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = tmp_path
    run_r11_case(
        ws, tmp_path, monkeypatch, stage="stabilize", new_work_item="A1/parser", new_kind="verify"
    )
    ov.ckpt_check(make_args(ws))  # must not raise


def test_r11_stabilize_disallowed_kind_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path
    run_r11_case(
        ws,
        tmp_path,
        monkeypatch,
        stage="stabilize",
        new_work_item="A1/parser",
        new_kind="implement",
    )
    with pytest.raises(ov.CheckViolations) as excinfo:
        ov.ckpt_check(make_args(ws))
    assert "R11" in rule_ids_of(excinfo.value.violations)


def test_r11_explore_stage_has_no_restriction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path
    run_r11_case(
        ws,
        tmp_path,
        monkeypatch,
        stage="explore",
        new_work_item="A1/brand-new-scope",
        new_kind="implement",
    )
    ov.ckpt_check(make_args(ws))  # must not raise -- explore has no R11 restriction at all


# =============================================================================================
# ===== R13: attempt cap =======================================================================
# =============================================================================================


def setup_r13_baseline(
    ws: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    capped_attempts: int,
    work_item: str = "A1/parser",
    max_attempts_per_item: int = 3,
    **cfg_overrides: object,
) -> tuple[Path, dict, str, int]:
    inst = ws / _INSTANCE_DIR
    cfg_dict = write_config(inst, max_attempts_per_item=max_attempts_per_item, **cfg_overrides)
    prompt_sha256 = write_prompt(inst)
    write_charter(inst, make_charter_dict(prompt_sha256))
    k = capped_attempts
    emitter = f"ck-{k:02d}"
    next_wave = k + 1
    for wave in range(1, capped_attempts + 1):
        unit_id = f"w{wave:02d}-01-hist"
        write_brief(inst, wave, unit_id, work_item=work_item, kind="implement")
        append_ledger_unit(
            inst,
            wave=wave,
            unit_id=unit_id,
            work_item=work_item,
            kind="implement",
            unit_verdict="fail",
        )
    write_digest(inst, k, allowed_wave_size=cfg_dict["wave_size"], stage="explore")
    write_state(ws, "run-1", {})
    set_hook_context(monkeypatch, tmp_path, "run-1", emitter)
    return inst, cfg_dict, emitter, next_wave


def run_r13_case(
    ws: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    capped_attempts: int,
    approach_change: str | None,
    work_item: str = "A1/parser",
    max_attempts_per_item: int = 3,
) -> Path:
    inst, cfg_dict, emitter, next_wave = setup_r13_baseline(
        ws,
        tmp_path,
        monkeypatch,
        capped_attempts=capped_attempts,
        work_item=work_item,
        max_attempts_per_item=max_attempts_per_item,
    )
    unit_id = f"w{next_wave:02d}-01-new"
    write_brief(
        inst,
        next_wave,
        unit_id,
        work_item=work_item,
        kind="implement",
        approach_change=approach_change,
    )
    unit_entry = make_unit_entry(next_wave, 1, "new", emitter=emitter, kind="implement")
    ckpt_entry = make_next_ckpt_entry(next_wave, [unit_id], emitter=emitter, cfg_dict=cfg_dict)
    write_manifest_entries(inst, f"{emitter}.json", [unit_entry, ckpt_entry])
    return inst


def test_r13_below_cap_no_restriction_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path
    run_r13_case(ws, tmp_path, monkeypatch, capped_attempts=1, approach_change=None)
    ov.ckpt_check(make_args(ws))  # must not raise


def test_r13_at_cap_with_approach_change_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path
    run_r13_case(
        ws, tmp_path, monkeypatch, capped_attempts=3, approach_change="try a different approach"
    )
    ov.ckpt_check(make_args(ws))  # must not raise


def test_r13_at_cap_without_approach_change_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path
    run_r13_case(ws, tmp_path, monkeypatch, capped_attempts=3, approach_change=None)
    with pytest.raises(ov.CheckViolations) as excinfo:
        ov.ckpt_check(make_args(ws))
    assert "R13" in rule_ids_of(excinfo.value.violations)


def test_r13_above_cap_plus_one_forbidden_even_with_approach_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path
    run_r13_case(
        ws, tmp_path, monkeypatch, capped_attempts=5, approach_change="try yet another approach"
    )
    with pytest.raises(ov.CheckViolations) as excinfo:
        ov.ckpt_check(make_args(ws))
    assert "R13" in rule_ids_of(excinfo.value.violations)


# =============================================================================================
# ===== R13c: rename dodge =====================================================================
# =============================================================================================


def seed_capped_work_item(
    inst: Path,
    *,
    work_item: str,
    count: int,
    latest_goal: str,
    latest_touches: tuple[str, ...] = (),
    earlier_goal: str = "original implementation attempt",
) -> list[str]:
    unit_ids = []
    for i in range(count):
        wave = i + 1
        unit_id = f"w{wave:02d}-01-hist"
        is_latest = i == count - 1
        write_brief(
            inst,
            wave,
            unit_id,
            work_item=work_item,
            kind="implement",
            goal=latest_goal if is_latest else earlier_goal,
            touches=list(latest_touches) if is_latest else [],
        )
        append_ledger_unit(
            inst,
            wave=wave,
            unit_id=unit_id,
            work_item=work_item,
            kind="implement",
            unit_verdict="fail",
        )
        unit_ids.append(unit_id)
    return unit_ids


def setup_r13c_baseline(
    ws: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    capped_attempts: int = 3,
    capped_work_item: str = "A1/parser",
    max_attempts_per_item: int = 3,
    latest_goal: str = "fix the parser tokenizer bug",
    latest_touches: tuple[str, ...] = (),
) -> tuple[Path, dict, str, int]:
    inst = ws / _INSTANCE_DIR
    cfg_dict = write_config(inst, max_attempts_per_item=max_attempts_per_item)
    prompt_sha256 = write_prompt(inst)
    write_charter(inst, make_charter_dict(prompt_sha256))
    k = capped_attempts
    emitter = f"ck-{k:02d}"
    next_wave = k + 1
    seed_capped_work_item(
        inst,
        work_item=capped_work_item,
        count=capped_attempts,
        latest_goal=latest_goal,
        latest_touches=latest_touches,
    )
    write_digest(inst, k, allowed_wave_size=cfg_dict["wave_size"], stage="explore")
    write_state(ws, "run-1", {})
    set_hook_context(monkeypatch, tmp_path, "run-1", emitter)
    return inst, cfg_dict, emitter, next_wave


def run_r13c_case(
    ws: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    new_work_item: str,
    new_goal: str = "",
    new_touches: tuple[str, ...] = (),
    new_prior_attempts: tuple[str, ...] = (),
    new_approach_change: str | None = None,
    latest_goal: str = "fix the parser tokenizer bug",
    latest_touches: tuple[str, ...] = (),
) -> Path:
    inst, cfg_dict, emitter, next_wave = setup_r13c_baseline(
        ws, tmp_path, monkeypatch, latest_goal=latest_goal, latest_touches=latest_touches
    )
    unit_id = f"w{next_wave:02d}-01-new"
    write_brief(
        inst,
        next_wave,
        unit_id,
        work_item=new_work_item,
        kind="implement",
        goal=new_goal,
        touches=list(new_touches),
        prior_attempts=list(new_prior_attempts),
        approach_change=new_approach_change,
    )
    unit_entry = make_unit_entry(next_wave, 1, "new", emitter=emitter, kind="implement")
    ckpt_entry = make_next_ckpt_entry(next_wave, [unit_id], emitter=emitter, cfg_dict=cfg_dict)
    write_manifest_entries(inst, f"{emitter}.json", [unit_entry, ckpt_entry])
    return inst


def test_r13c_similar_goal_to_capped_item_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path
    run_r13c_case(
        ws,
        tmp_path,
        monkeypatch,
        new_work_item="A1/renamed-parser",
        new_goal="Fix tokenizer bug in parser",
        latest_goal="fix the parser tokenizer bug",
    )
    with pytest.raises(ov.CheckViolations) as excinfo:
        ov.ckpt_check(make_args(ws))
    violations = excinfo.value.violations
    assert "R13c" in rule_ids_of(violations)
    message = " ".join(v.message for v in violations if v.rule_id == "R13c")
    assert "A1/parser" in message  # names the capped work item
    assert "approach_change" in message and "descope" in message and "hold" in message


def test_r13c_unrelated_goal_passes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = tmp_path
    run_r13c_case(
        ws,
        tmp_path,
        monkeypatch,
        new_work_item="A1/unrelated-feature",
        new_goal="Write onboarding documentation for new users",
        latest_goal="fix the parser tokenizer bug",
    )
    ov.ckpt_check(make_args(ws))  # must not raise


def test_r13c_shared_touches_glob_rejected_despite_dissimilar_goal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path
    run_r13c_case(
        ws,
        tmp_path,
        monkeypatch,
        new_work_item="A1/unrelated-feature",
        new_goal="Write onboarding documentation for new users",
        new_touches=("src/parser/*.py",),
        latest_goal="fix the parser tokenizer bug",
        latest_touches=("src/parser/*.py",),
    )
    with pytest.raises(ov.CheckViolations) as excinfo:
        ov.ckpt_check(make_args(ws))
    assert "R13c" in rule_ids_of(excinfo.value.violations)


def test_r13c_shared_prior_attempts_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path
    run_r13c_case(
        ws,
        tmp_path,
        monkeypatch,
        new_work_item="A1/unrelated-feature",
        new_goal="Write onboarding documentation for new users",
        new_prior_attempts=("w03-01-hist",),
        latest_goal="fix the parser tokenizer bug",
    )
    with pytest.raises(ov.CheckViolations) as excinfo:
        ov.ckpt_check(make_args(ws))
    assert "R13c" in rule_ids_of(excinfo.value.violations)


def test_r13c_does_not_misfire_on_an_existing_continuing_work_item(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path
    run_r13c_case(
        ws,
        tmp_path,
        monkeypatch,
        new_work_item="A1/parser",  # SAME key as the capped item -- not "new"
        new_goal="fix the parser tokenizer bug",
        new_approach_change="try a different approach this time",
        latest_goal="fix the parser tokenizer bug",
    )
    ov.ckpt_check(make_args(ws))  # must not raise (R13c doesn't apply; R13's own cap is satisfied)


# =============================================================================================
# ===== R12/R14/R16: verdict checks + decision<->manifest consistency + must_close ============
# =============================================================================================


def setup_verdict_baseline(
    ws: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    decision: str = "continue",
    alignment: list[dict] | None = None,
    criteria: list[dict] | None = None,
    signals: list[dict] | None = None,
    signal_responses: list[dict] | None = None,
    hold_questions: list[str] | None = None,
    extra_ledger_events: tuple[dict, ...] = (),
    stage: str = "explore",
    allowed_decisions: list[str] | None = None,
    must_close: bool = False,
    **cfg_overrides: object,
) -> tuple[Path, dict, str, int, dict]:
    """A fully R1-R16-passing `ck-01` scenario (decision=`continue`, one wave-2 unit
    continuing the existing, non-capped `A1/parser` work item) that individual tests mutate
    one field at a time -- mirrors `setup_ckpt_happy_path` in the structural test file.
    """
    inst = ws / _INSTANCE_DIR
    cfg_dict = write_config(inst, **cfg_overrides)
    prompt_sha256 = write_prompt(inst)
    charter = make_charter_dict(
        prompt_sha256,
        asks=[
            {
                "ask_id": "A1",
                "statement": "Build the parser.",
                "deliverable_type": "code",
                "acceptance": [{"id": "A1.1", "text": "parser works"}],
                "usable_bar": ["it compiles"],
                "priority": 1,
            },
            {
                "ask_id": "A2",
                "statement": "Write the docs.",
                "deliverable_type": "doc",
                "acceptance": [{"id": "A2.1", "text": "docs published"}],
                "usable_bar": ["docs exist"],
                "priority": 2,
            },
        ],
    )
    write_charter(inst, charter)
    k = 1
    emitter = f"ck-{k:02d}"
    next_wave = k + 1

    hist_unit_id = f"w{k:02d}-01-hist"
    write_brief(inst, k, hist_unit_id, work_item="A1/parser", kind="implement", ask_ids=["A1"])
    append_ledger_unit(
        inst, wave=k, unit_id=hist_unit_id, work_item="A1/parser", kind="implement", ask_ids=("A1",)
    )
    for event in extra_ledger_events:
        append_ledger_event(inst, dict(event))

    write_digest(
        inst,
        k,
        allowed_wave_size=cfg_dict["wave_size"],
        stage=stage,
        allowed_decisions=allowed_decisions,
        must_close=must_close,
        signals=signals,
    )

    new_unit_id = f"w{next_wave:02d}-01-cont"
    write_brief(
        inst, next_wave, new_unit_id, work_item="A1/parser", kind="implement", ask_ids=["A1"]
    )
    unit_entry = make_unit_entry(next_wave, 1, "cont", emitter=emitter, kind="implement")
    ckpt_entry = make_next_ckpt_entry(next_wave, [new_unit_id], emitter=emitter, cfg_dict=cfg_dict)
    write_manifest_entries(inst, f"{emitter}.json", [unit_entry, ckpt_entry])

    default_alignment = [
        {"ask_id": "A1", "status": "on_track", "evidence": "parser work is progressing"},
        {"ask_id": "A2", "status": "on_track", "evidence": "docs work is progressing"},
    ]
    default_criteria = [{"id": "A1.1", "status": "unmet"}, {"id": "A2.1", "status": "unmet"}]
    if signal_responses is None:
        signal_responses = (
            [{"signal_id": s["id"], "response": "accept", "rationale": "x" * 45} for s in signals]
            if signals
            else []
        )
    write_verdict(
        inst,
        k,
        decision=decision,
        alignment=alignment if alignment is not None else default_alignment,
        criteria=criteria if criteria is not None else default_criteria,
        signal_responses=signal_responses,
        hold_questions=hold_questions if hold_questions is not None else [],
    )
    write_state(ws, "run-1", {})
    set_hook_context(monkeypatch, tmp_path, "run-1", emitter)
    return inst, cfg_dict, emitter, k, charter


def test_r12_baseline_passes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = tmp_path
    setup_verdict_baseline(ws, tmp_path, monkeypatch)
    ov.ckpt_check(make_args(ws))  # must not raise


def test_r12_malformed_verdict_json_fails_not_silently_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression (dev-epic finding, post-T-tAKBBB review): a verdict.json that EXISTS but
    is unparseable must be an R12 violation, not silently treated as "no verdict yet" and
    passed through with zero violations. A genuinely MISSING verdict.json is already caught
    by the engine's own missing_outputs check (existence-only) at a different boundary, so
    that case is deliberately NOT re-tested here (it would require monkeypatching the
    engine, out of this tool-level test's scope) -- this test is specifically about the
    "file exists but is garbage" gap that check cannot see."""
    ws = tmp_path
    inst, _cfg, _emitter, k, _charter = setup_verdict_baseline(ws, tmp_path, monkeypatch)
    ov.verdict_path(inst, k).write_text("{not valid json")
    with pytest.raises(ov.CheckViolations) as excinfo:
        ov.ckpt_check(make_args(ws))
    assert "R12" in rule_ids_of(excinfo.value.violations)


def test_r12_wrong_schema_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = tmp_path
    inst, _cfg, _emitter, k, _charter = setup_verdict_baseline(ws, tmp_path, monkeypatch)
    mutate_verdict(inst, k, schema="ao.overseer.verdict/v0")
    with pytest.raises(ov.CheckViolations) as excinfo:
        ov.ckpt_check(make_args(ws))
    assert "R12" in rule_ids_of(excinfo.value.violations)


def test_r12_missing_required_field_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = tmp_path
    inst, _cfg, _emitter, k, _charter = setup_verdict_baseline(ws, tmp_path, monkeypatch)
    mutate_verdict(inst, k, rationale=12345)  # wrong type, not a string
    with pytest.raises(ov.CheckViolations) as excinfo:
        ov.ckpt_check(make_args(ws))
    assert "R12" in rule_ids_of(excinfo.value.violations)


def test_r12_decision_in_allowed_decisions_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path
    setup_verdict_baseline(ws, tmp_path, monkeypatch)
    ov.ckpt_check(make_args(ws))  # decision=continue is in the default allowed_decisions


def test_r12_decision_not_in_allowed_decisions_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path
    inst, _cfg, _emitter, k, _charter = setup_verdict_baseline(ws, tmp_path, monkeypatch)
    mutate_digest(inst, k, allowed_decisions=["hold", "closeout"])
    with pytest.raises(ov.CheckViolations) as excinfo:
        ov.ckpt_check(make_args(ws))
    assert "R12" in rule_ids_of(excinfo.value.violations)


def test_r12_signal_responses_complete_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path
    sig = {"id": "S-01-01", "type": "stall", "severity": "medium", "message": "stalled"}
    setup_verdict_baseline(
        ws,
        tmp_path,
        monkeypatch,
        signals=[sig],
        signal_responses=[{"signal_id": "S-01-01", "response": "redirect", "rationale": "ok"}],
    )
    ov.ckpt_check(make_args(ws))  # must not raise


def test_r12_signal_responses_missing_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path
    sig = {"id": "S-01-01", "type": "stall", "severity": "medium", "message": "stalled"}
    setup_verdict_baseline(ws, tmp_path, monkeypatch, signals=[sig], signal_responses=[])
    with pytest.raises(ov.CheckViolations) as excinfo:
        ov.ckpt_check(make_args(ws))
    assert "R12" in rule_ids_of(excinfo.value.violations)


def test_r12_signal_responses_duplicate_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path
    sig = {"id": "S-01-01", "type": "stall", "severity": "medium", "message": "stalled"}
    setup_verdict_baseline(
        ws,
        tmp_path,
        monkeypatch,
        signals=[sig],
        signal_responses=[
            {"signal_id": "S-01-01", "response": "redirect", "rationale": "a"},
            {"signal_id": "S-01-01", "response": "accept", "rationale": "b" * 45},
        ],
    )
    with pytest.raises(ov.CheckViolations) as excinfo:
        ov.ckpt_check(make_args(ws))
    assert "R12" in rule_ids_of(excinfo.value.violations)


def test_r12_accept_high_signal_with_long_rationale_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path
    sig = {"id": "S-01-01", "type": "attempt_cap", "severity": "high", "message": "capped"}
    setup_verdict_baseline(
        ws,
        tmp_path,
        monkeypatch,
        signals=[sig],
        signal_responses=[{"signal_id": "S-01-01", "response": "accept", "rationale": "x" * 45}],
    )
    ov.ckpt_check(make_args(ws))  # must not raise


def test_r12_accept_high_signal_with_short_rationale_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path
    sig = {"id": "S-01-01", "type": "attempt_cap", "severity": "high", "message": "capped"}
    setup_verdict_baseline(
        ws,
        tmp_path,
        monkeypatch,
        signals=[sig],
        signal_responses=[{"signal_id": "S-01-01", "response": "accept", "rationale": "too short"}],
    )
    with pytest.raises(ov.CheckViolations) as excinfo:
        ov.ckpt_check(make_args(ws))
    assert "R12" in rule_ids_of(excinfo.value.violations)


def test_r12_alignment_covers_every_ask_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path
    setup_verdict_baseline(ws, tmp_path, monkeypatch)  # baseline covers A1 and A2
    ov.ckpt_check(make_args(ws))  # must not raise


def test_r12_alignment_missing_ask_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = tmp_path
    setup_verdict_baseline(
        ws,
        tmp_path,
        monkeypatch,
        alignment=[{"ask_id": "A1", "status": "on_track", "evidence": "ok"}],  # A2 missing
    )
    with pytest.raises(ov.CheckViolations) as excinfo:
        ov.ckpt_check(make_args(ws))
    assert "R12" in rule_ids_of(excinfo.value.violations)


def test_r12_criteria_covers_every_criterion_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path
    setup_verdict_baseline(ws, tmp_path, monkeypatch)  # baseline covers A1.1 and A2.1
    ov.ckpt_check(make_args(ws))  # must not raise


def test_r12_criteria_missing_criterion_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path
    setup_verdict_baseline(
        ws,
        tmp_path,
        monkeypatch,
        criteria=[{"id": "A1.1", "status": "unmet"}],  # A2.1 missing
    )
    with pytest.raises(ov.CheckViolations) as excinfo:
        ov.ckpt_check(make_args(ws))
    assert "R12" in rule_ids_of(excinfo.value.violations)


def test_r12_deferred_with_reason_and_evidence_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path
    setup_verdict_baseline(
        ws,
        tmp_path,
        monkeypatch,
        alignment=[
            {"ask_id": "A1", "status": "on_track", "evidence": "ok"},
            {
                "ask_id": "A2",
                "status": "deferred",
                "deferred_reason": "out_of_budget",
                "evidence": "x" * 45,
            },
        ],
    )
    ov.ckpt_check(make_args(ws))  # must not raise


def test_r12_deferred_without_reason_or_evidence_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path
    setup_verdict_baseline(
        ws,
        tmp_path,
        monkeypatch,
        alignment=[
            {"ask_id": "A1", "status": "on_track", "evidence": "ok"},
            {"ask_id": "A2", "status": "deferred"},
        ],
    )
    with pytest.raises(ov.CheckViolations) as excinfo:
        ov.ckpt_check(make_args(ws))
    assert "R12" in rule_ids_of(excinfo.value.violations)


def test_r12_human_descoped_with_prior_hold_answered_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path
    setup_verdict_baseline(
        ws,
        tmp_path,
        monkeypatch,
        alignment=[
            {"ask_id": "A1", "status": "on_track", "evidence": "ok"},
            {
                "ask_id": "A2",
                "status": "deferred",
                "deferred_reason": "human_descoped",
                "evidence": "x" * 45,
            },
        ],
        extra_ledger_events=(
            {
                "type": "event",
                "event": "hold_answered",
                "checkpoint": "ck-00",
                "answer_sha256": "a" * 64,
            },
        ),
    )
    ov.ckpt_check(make_args(ws))  # must not raise


def test_r12_human_descoped_without_prior_hold_answered_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path
    setup_verdict_baseline(
        ws,
        tmp_path,
        monkeypatch,
        alignment=[
            {"ask_id": "A1", "status": "on_track", "evidence": "ok"},
            {
                "ask_id": "A2",
                "status": "deferred",
                "deferred_reason": "human_descoped",
                "evidence": "x" * 45,
            },
        ],
    )
    with pytest.raises(ov.CheckViolations) as excinfo:
        ov.ckpt_check(make_args(ws))
    assert "R12" in rule_ids_of(excinfo.value.violations)


# =============================================================================================
# ===== R14: decision <-> manifest consistency (per-decision-branch fixtures) =================
# =============================================================================================


def test_r14_continue_with_next_ckpt_and_units_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path
    setup_verdict_baseline(ws, tmp_path, monkeypatch, decision="continue")
    ov.ckpt_check(make_args(ws))  # must not raise


def test_r14_continue_with_zero_unit_next_ckpt_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path
    inst, cfg_dict, emitter, k, _charter = setup_verdict_baseline(
        ws, tmp_path, monkeypatch, decision="continue"
    )
    next_wave = k + 1
    ckpt_entry = make_next_ckpt_entry(next_wave, [], emitter=emitter, cfg_dict=cfg_dict)
    write_manifest_entries(inst, f"{emitter}.json", [ckpt_entry])
    with pytest.raises(ov.CheckViolations) as excinfo:
        ov.ckpt_check(make_args(ws))
    assert "R14" in rule_ids_of(excinfo.value.violations)


def setup_hold_baseline(
    ws: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    hold_questions: list[str] | None = None,
    write_hold_req: bool = True,
    **cfg_overrides: object,
) -> tuple[Path, dict, str, int]:
    inst = ws / _INSTANCE_DIR
    cfg_dict = write_config(inst, **cfg_overrides)
    prompt_sha256 = write_prompt(inst)
    write_charter(inst, make_charter_dict(prompt_sha256))
    k = 1
    emitter = f"ck-{k:02d}"
    next_wave = k + 1
    ckpt_entry = make_next_ckpt_entry(next_wave, [], emitter=emitter, cfg_dict=cfg_dict)
    write_manifest_entries(inst, f"{emitter}.json", [ckpt_entry])
    write_digest(inst, k, allowed_wave_size=cfg_dict["wave_size"], stage="explore")
    if write_hold_req:
        write_hold_request(inst, checkpoint=emitter)
    write_verdict(
        inst,
        k,
        decision="hold",
        alignment=[{"ask_id": "A1", "status": "at_risk", "evidence": "needs human input, blocked"}],
        criteria=[{"id": "A1.1", "status": "unmet"}],
        hold_questions=hold_questions
        if hold_questions is not None
        else ["what should we do next?"],
    )
    write_state(ws, "run-1", {})
    set_hook_context(monkeypatch, tmp_path, "run-1", emitter)
    return inst, cfg_dict, emitter, k


def test_r14_hold_with_zero_units_and_valid_request_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path
    setup_hold_baseline(ws, tmp_path, monkeypatch)
    ov.ckpt_check(make_args(ws))  # must not raise


def test_r14_hold_missing_hold_request_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path
    setup_hold_baseline(ws, tmp_path, monkeypatch, write_hold_req=False)
    with pytest.raises(ov.CheckViolations) as excinfo:
        ov.ckpt_check(make_args(ws))
    assert "R14" in rule_ids_of(excinfo.value.violations)


def test_r14_hold_request_with_no_questions_is_invalid_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path
    inst, _cfg, _emitter, _k = setup_hold_baseline(ws, tmp_path, monkeypatch)
    write_hold_request(inst, questions=[])  # schema-invalid: needs >= 1 questions
    with pytest.raises(ov.CheckViolations) as excinfo:
        ov.ckpt_check(make_args(ws))
    assert "R14" in rule_ids_of(excinfo.value.violations)


def test_r14_hold_request_from_a_different_checkpoint_is_invalid_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression for a reviewer finding on T-tAKBBB's review: a stale
    control/hold-request.json left over from an EARLIER checkpoint's own hold decision must
    not validate for a DIFFERENT checkpoint currently deciding hold -- the request's own
    `checkpoint` field must equal the checkpoint actually being checked, not just be some
    valid-looking string."""
    ws = tmp_path
    inst, _cfg, emitter, _k = setup_hold_baseline(ws, tmp_path, monkeypatch)
    write_hold_request(inst, checkpoint="ck-99")  # emitter is ck-01 by default (mismatch)
    assert emitter != "ck-99"
    with pytest.raises(ov.CheckViolations) as excinfo:
        ov.ckpt_check(make_args(ws))
    assert "R14" in rule_ids_of(excinfo.value.violations)


def test_r14_hold_with_units_in_next_ckpt_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path
    inst, cfg_dict, emitter, k = setup_hold_baseline(ws, tmp_path, monkeypatch)
    next_wave = k + 1
    unit_id = f"w{next_wave:02d}-01-extra"
    write_brief(inst, next_wave, unit_id, work_item="A1/parser", kind="implement")
    unit_entry = make_unit_entry(next_wave, 1, "extra", emitter=emitter, kind="implement")
    ckpt_entry = make_next_ckpt_entry(next_wave, [unit_id], emitter=emitter, cfg_dict=cfg_dict)
    write_manifest_entries(inst, f"{emitter}.json", [unit_entry, ckpt_entry])
    with pytest.raises(ov.CheckViolations) as excinfo:
        ov.ckpt_check(make_args(ws))
    assert "R14" in rule_ids_of(excinfo.value.violations)


def test_r12_hold_requires_hold_questions_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path
    setup_hold_baseline(ws, tmp_path, monkeypatch, hold_questions=[])
    with pytest.raises(ov.CheckViolations) as excinfo:
        ov.ckpt_check(make_args(ws))
    assert "R12" in rule_ids_of(excinfo.value.violations)


def setup_closeout_baseline(
    ws: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    stage: str = "stabilize",
    final_push: bool = True,
    **cfg_overrides: object,
) -> tuple[Path, dict, str, int]:
    inst = ws / _INSTANCE_DIR
    cfg_dict = write_config(inst, final_push=final_push, **cfg_overrides)
    prompt_sha256 = write_prompt(inst)
    write_charter(inst, make_charter_dict(prompt_sha256))
    k = 1
    emitter = f"ck-{k:02d}"
    write_manifest_entries(
        inst, f"{emitter}.json", make_tail_entries(emitter=emitter, final_push=final_push)
    )
    write_digest(
        inst,
        k,
        allowed_wave_size=0,
        stage=stage,
        allowed_decisions=["closeout", "stabilize", "hold"],
    )
    write_verdict(
        inst,
        k,
        decision="closeout",
        alignment=[{"ask_id": "A1", "status": "met", "evidence": "work is done"}],
        criteria=[{"id": "A1.1", "status": "met"}],
    )
    write_state(ws, "run-1", {})
    set_hook_context(monkeypatch, tmp_path, "run-1", emitter)
    return inst, cfg_dict, emitter, k


def test_r14_closeout_with_tail_passes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = tmp_path
    setup_closeout_baseline(ws, tmp_path, monkeypatch, stage="stabilize")
    ov.ckpt_check(make_args(ws))  # must not raise (stabilize is not an "early" stage)


def test_r14_closeout_missing_tail_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = tmp_path
    setup_verdict_baseline(ws, tmp_path, monkeypatch, decision="closeout")
    with pytest.raises(ov.CheckViolations) as excinfo:
        ov.ckpt_check(make_args(ws))
    assert "R14" in rule_ids_of(excinfo.value.violations)


def setup_early_closeout_baseline(
    ws: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    with_verify_evidence: bool,
    stage: str = "converge",
) -> tuple[Path, dict, str, int]:
    inst = ws / _INSTANCE_DIR
    cfg_dict = write_config(inst)
    prompt_sha256 = write_prompt(inst)
    write_charter(inst, make_charter_dict(prompt_sha256))
    k = 2
    emitter = f"ck-{k:02d}"
    write_brief(inst, 1, "w01-01-impl", work_item="A1/parser", kind="implement", ask_ids=["A1"])
    append_ledger_unit(
        inst,
        wave=1,
        unit_id="w01-01-impl",
        work_item="A1/parser",
        kind="implement",
        ask_ids=("A1",),
    )
    if with_verify_evidence:
        write_brief(inst, 2, "w02-01-verify", work_item="A1/parser", kind="verify", ask_ids=["A1"])
        append_ledger_unit(
            inst,
            wave=2,
            unit_id="w02-01-verify",
            work_item="A1/parser",
            kind="verify",
            ask_ids=("A1",),
        )
    write_manifest_entries(
        inst, f"{emitter}.json", make_tail_entries(emitter=emitter, final_push=True)
    )
    write_digest(inst, k, allowed_wave_size=0, stage=stage)
    write_verdict(
        inst,
        k,
        decision="closeout",
        alignment=[{"ask_id": "A1", "status": "met", "evidence": "verified early"}],
        criteria=[{"id": "A1.1", "status": "met"}],
    )
    write_state(ws, "run-1", {})
    set_hook_context(monkeypatch, tmp_path, "run-1", emitter)
    return inst, cfg_dict, emitter, k


def test_r14_early_closeout_with_verify_evidence_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path
    setup_early_closeout_baseline(ws, tmp_path, monkeypatch, with_verify_evidence=True)
    ov.ckpt_check(make_args(ws))  # must not raise


def test_r14_early_closeout_without_verify_evidence_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path
    setup_early_closeout_baseline(ws, tmp_path, monkeypatch, with_verify_evidence=False)
    with pytest.raises(ov.CheckViolations) as excinfo:
        ov.ckpt_check(make_args(ws))
    assert "R14" in rule_ids_of(excinfo.value.violations)


def test_r14_closeout_in_stabilize_stage_is_not_early_and_needs_no_verify_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path
    # stage=stabilize is NOT in _EARLY_CLOSEOUT_STAGES -- no verify-evidence requirement even
    # though no verify unit exists at all.
    setup_closeout_baseline(ws, tmp_path, monkeypatch, stage="stabilize")
    ov.ckpt_check(make_args(ws))  # must not raise


# =============================================================================================
# ===== R16: must_close => closeout ============================================================
# =============================================================================================


def test_r16_must_close_with_closeout_decision_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path
    inst, _cfg, _emitter, k = setup_closeout_baseline(ws, tmp_path, monkeypatch, stage="stabilize")
    mutate_digest(inst, k, must_close=True, allowed_decisions=["closeout"])
    ov.ckpt_check(make_args(ws))  # must not raise


def test_r16_must_close_with_non_closeout_decision_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path
    inst, _cfg, _emitter, k, _charter = setup_verdict_baseline(
        ws, tmp_path, monkeypatch, decision="continue"
    )
    mutate_digest(inst, k, must_close=True)
    with pytest.raises(ov.CheckViolations) as excinfo:
        ov.ckpt_check(make_args(ws))
    assert "R16" in rule_ids_of(excinfo.value.violations)


# =============================================================================================
# ===== AC3: post-success ledger events (checkpoint / hold_requested), idempotent, dry-run ====
# =============================================================================================


def test_ac3_success_appends_exactly_one_checkpoint_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path
    inst, _cfg, emitter, k, _charter = setup_verdict_baseline(ws, tmp_path, monkeypatch)
    ov.ckpt_check(make_args(ws))

    events = [line for line in ov.read_ledger_lines(inst) if line.get("event") == "checkpoint"]
    assert len(events) == 1
    assert events[0]["checkpoint"] == emitter
    assert events[0]["decision"] == "continue"
    assert isinstance(events[0]["verdict_sha256"], str) and len(events[0]["verdict_sha256"]) == 64
    assert isinstance(events[0]["manifest_sha256"], str) and len(events[0]["manifest_sha256"]) == 64
    ov.verify_ledger_chain(inst)  # must not raise -- the chain still verifies


def test_ac3_hold_decision_also_appends_hold_requested(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path
    inst, _cfg, emitter, _k = setup_hold_baseline(ws, tmp_path, monkeypatch)
    ov.ckpt_check(make_args(ws))

    events = [line for line in ov.read_ledger_lines(inst) if line.get("type") == "event"]
    event_names = {e["event"] for e in events}
    assert "checkpoint" in event_names
    assert "hold_requested" in event_names
    assert (
        sum(1 for e in events if e["event"] == "hold_requested" and e["checkpoint"] == emitter) == 1
    )
    ov.verify_ledger_chain(inst)


def test_ac3_dry_run_appends_no_events(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = tmp_path
    inst, _cfg, _emitter, _k, _charter = setup_verdict_baseline(ws, tmp_path, monkeypatch)
    before = ov.read_ledger_lines(inst)
    ov.ckpt_check(make_args(ws, dry_run=True))
    after = ov.read_ledger_lines(inst)
    assert after == before
    assert not any(line.get("type") == "event" for line in after)


def test_ac3_rerun_after_success_does_not_duplicate_events(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path
    inst, _cfg, emitter, _k, _charter = setup_verdict_baseline(ws, tmp_path, monkeypatch)
    ov.ckpt_check(make_args(ws))
    ov.ckpt_check(make_args(ws))  # re-run after success -- must not raise, must not duplicate

    events = [line for line in ov.read_ledger_lines(inst) if line.get("event") == "checkpoint"]
    assert len(events) == 1
    assert events[0]["checkpoint"] == emitter
    ov.verify_ledger_chain(inst)


# =============================================================================================
# ===== AC4: structural + semantic violations compose (never short-circuit each other) ========
# =============================================================================================


def test_ac4_combined_structural_r8_and_semantic_r12_violations_reported_together(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path
    inst, _cfg, emitter, k, _charter = setup_verdict_baseline(ws, tmp_path, monkeypatch)

    manifest_path = inst / "outputs" / "manifests" / f"{emitter}.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["tasks"][0]["depends_on"] = ["does-not-exist"]  # breaks R8 (structural)
    manifest_path.write_text(json.dumps(manifest))
    mutate_digest(
        inst, k, allowed_decisions=["hold"]
    )  # breaks R12 (semantic): continue not allowed

    with pytest.raises(ov.CheckViolations) as excinfo:
        ov.ckpt_check(make_args(ws))
    ids = rule_ids_of(excinfo.value.violations)
    assert "R8" in ids
    assert "R12" in ids

    result = load_check_result(inst, k)
    assert result["ok"] is False
    assert {"OV-R8", "OV-R12"}.issubset({v["rule"] for v in result["violations"]})
