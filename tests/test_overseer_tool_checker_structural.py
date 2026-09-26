"""Tests for `overseer_tool.py`'s M3 structural contract checkers (`intake-check`,
`ckpt-check`) -- T-HPJcc6, the structural half of M3 (rules R1-R10, R15, plus the
`intake-check`-only charter checks). Semantic rules R11-R14/R16/R13c are T-tAKBBB's job and
are NOT exercised here.

Ticket: `meta/tickets/E-YAAGhk-overseer-runner-template/T-HPJcc6-tool-structural-checkers/`.
Mirrors the fixture-helper conventions already established by `test_overseer_tool_gates.py`/
`test_overseer_tool_budget.py`/`test_overseer_tool_detectors.py` (each test file is
self-contained -- no cross-test-file imports).
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from agent_orchestrator.models import TaskSpec
from agent_orchestrator.templates import _render

_REPO_ROOT = Path(__file__).resolve().parents[1]
_TEMPLATE_DIR = (
    _REPO_ROOT / "src" / "agent_orchestrator" / "templates" / "builtin" / "overseer-runner"
)
_TOOL_PATH = _TEMPLATE_DIR / "tools" / "overseer_tool.py"


def _load_overseer_tool() -> ModuleType:
    spec = importlib.util.spec_from_file_location("overseer_tool_under_test_checker", _TOOL_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


ov = _load_overseer_tool()

_NOW = "2026-01-01T00:00:00+00:00"
_INSTANCE_DIR = "instance"

_WORK_UNIT_INSTRUCTION = "workflows/overseer-runner/instructions/10-work-unit.md"
_STABILIZE_INSTRUCTION = "workflows/overseer-runner/instructions/11-stabilize-unit.md"
_EXPANDER_INSTRUCTION = "workflows/overseer-runner/instructions/30-expander.md"

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
    "expand": {"agent": "architect", "instruction": _EXPANDER_INSTRUCTION},
}


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


def write_digest(inst: Path, k: int, *, allowed_wave_size: int = 6) -> None:
    path = ov.digest_path(inst, k)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"schema": ov.DIGEST_SCHEMA, "cadence": {"allowed_wave_size": allowed_wave_size}}
        )
    )


def write_manifest(inst: Path, filename: str, data: dict) -> Path:
    path = inst / "outputs" / "manifests" / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))
    return path


def write_manifest_entries(inst: Path, filename: str, entries: list[dict]) -> Path:
    return write_manifest(inst, filename, {"tasks": entries})


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
    verify_inputs = [f"{_INSTANCE_DIR}/outputs/charter.json"]
    if emitter.startswith("ck-"):
        verify_inputs.append(f"{_INSTANCE_DIR}/outputs/checkpoints/{emitter}/verdict.json")
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


def setup_ckpt_happy_path(
    ws: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    k: int = 1,
    existing_state_tasks: dict[str, dict] | None = None,
    **cfg_overrides: object,
) -> tuple[Path, str, str, int, dict]:
    """A fully valid `ck-{k:02d}` checkpoint manifest (one unit + the next checkpoint), plus
    every supporting fixture (config, charter, prompt, brief, digest, state, hook context) --
    a zero-violation baseline that individual tests mutate one field at a time.
    """
    inst = ws / _INSTANCE_DIR
    cfg_dict = write_config(inst, **cfg_overrides)
    prompt_sha256 = write_prompt(inst)
    write_charter(inst, make_charter_dict(prompt_sha256))
    emitter = f"ck-{k:02d}"
    next_wave = k + 1
    unit_id = f"w{next_wave:02d}-01-alpha"
    write_brief(inst, next_wave, unit_id)
    unit_entry = make_unit_entry(next_wave, 1, "alpha", emitter=emitter)
    ckpt_entry = make_next_ckpt_entry(next_wave, [unit_id], emitter=emitter, cfg_dict=cfg_dict)
    write_manifest_entries(inst, f"ck-{k:02d}.json", [unit_entry, ckpt_entry])
    write_digest(inst, k, allowed_wave_size=cfg_dict["wave_size"])
    write_state(ws, "run-1", existing_state_tasks or {})
    set_hook_context(monkeypatch, tmp_path, "run-1", emitter)
    return inst, unit_id, emitter, next_wave, cfg_dict


def setup_intake_happy_path(
    ws: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **cfg_overrides: object
) -> tuple[Path, str, dict]:
    inst = ws / _INSTANCE_DIR
    cfg_dict = write_config(inst, **cfg_overrides)
    prompt_sha256 = write_prompt(inst)
    write_charter(inst, make_charter_dict(prompt_sha256))
    unit_id = "w01-01-alpha"
    write_brief(inst, 1, unit_id)
    unit_entry = make_unit_entry(1, 1, "alpha", emitter="intake")
    ckpt_entry = make_next_ckpt_entry(1, [unit_id], emitter="intake", cfg_dict=cfg_dict)
    write_manifest_entries(inst, "intake.json", [unit_entry, ckpt_entry])
    write_state(ws, "run-1", {})
    set_hook_context(monkeypatch, tmp_path, "run-1", "intake")
    return inst, unit_id, cfg_dict


def load_check_result(inst: Path, *, checkpoint: str | None = None) -> dict:
    result_dir = (
        ov.checkpoint_dir(inst, int(checkpoint.split("-")[1])) if checkpoint else inst / "outputs"
    )
    return json.loads((result_dir / "check-result.json").read_text())


def rule_ids_of(violations: list) -> set[str]:
    return {v.rule_id for v in violations}


# =============================================================================================
# ===== Happy path + idempotency + dry-run (AC3) ==============================================
# =============================================================================================


def test_ckpt_check_happy_path_passes_and_writes_ok_check_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path
    inst, *_ = setup_ckpt_happy_path(ws, tmp_path, monkeypatch)
    ov.ckpt_check(make_args(ws))  # must not raise
    result = json.loads((ov.checkpoint_dir(inst, 1) / "check-result.json").read_text())
    assert result["schema"] == ov.CHECK_RESULT_SCHEMA
    assert result["ok"] is True
    assert result["violations"] == []


def test_intake_check_happy_path_passes_locks_charter_and_writes_ok_check_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path
    inst, _unit_id, _cfg = setup_intake_happy_path(ws, tmp_path, monkeypatch)

    ov.intake_check(make_args(ws))  # must not raise

    lock = json.loads((inst / "outputs" / "overseer" / "charter.lock.json").read_text())
    expected_charter_sha256 = hashlib.sha256(
        (inst / "outputs" / "charter.json").read_bytes()
    ).hexdigest()
    expected_prompt_sha256 = hashlib.sha256((inst / "prompt.md").read_bytes()).hexdigest()
    assert lock["sha256"] == expected_charter_sha256
    assert lock["prompt_sha256"] == expected_prompt_sha256
    assert "locked_at" in lock

    events = [line for line in ov.read_ledger_lines(inst) if line.get("event") == "charter_locked"]
    assert len(events) == 1

    result = json.loads((inst / "outputs" / "check-result.json").read_text())
    assert result["ok"] is True
    assert result["violations"] == []


def test_intake_check_second_run_on_unchanged_inputs_does_not_duplicate_ledger_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path
    inst, *_ = setup_intake_happy_path(ws, tmp_path, monkeypatch)

    ov.intake_check(make_args(ws))
    ov.intake_check(make_args(ws))

    events = [line for line in ov.read_ledger_lines(inst) if line.get("event") == "charter_locked"]
    assert len(events) == 1


def test_intake_check_dry_run_writes_nothing_at_all(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path
    inst, *_ = setup_intake_happy_path(ws, tmp_path, monkeypatch)

    ov.intake_check(make_args(ws, dry_run=True))  # must not raise

    assert not (inst / "outputs" / "overseer" / "charter.lock.json").exists()
    assert not (inst / "outputs" / "check-result.json").exists()
    events = [line for line in ov.read_ledger_lines(inst) if line.get("event") == "charter_locked"]
    assert events == []


def test_ckpt_check_dry_run_writes_nothing_on_violation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path
    inst, *_ = setup_ckpt_happy_path(ws, tmp_path, monkeypatch)
    write_manifest(inst, "ck-01.json", {"tasks": []})

    with pytest.raises(ov.CheckViolations):
        ov.ckpt_check(make_args(ws, dry_run=True))
    assert not (ov.checkpoint_dir(inst, 1) / "check-result.json").exists()


def test_ckpt_check_violation_writes_ok_false_check_result_with_rule_and_message_and_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path
    inst, *_ = setup_ckpt_happy_path(ws, tmp_path, monkeypatch)
    write_manifest(inst, "ck-01.json", {"tasks": []})

    with pytest.raises(ov.CheckViolations):
        ov.ckpt_check(make_args(ws))

    result = json.loads((ov.checkpoint_dir(inst, 1) / "check-result.json").read_text())
    assert result["ok"] is False
    assert len(result["violations"]) == 1
    v = result["violations"][0]
    assert set(v) == {"rule", "message", "path"}
    assert v["rule"] == "OV-R10"


# =============================================================================================
# ===== Charter checks (intake-check only) =====================================================
# =============================================================================================


def test_intake_check_wrong_prompt_sha256_fails_and_does_not_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path
    inst, *_ = setup_intake_happy_path(ws, tmp_path, monkeypatch)
    charter = json.loads((inst / "outputs" / "charter.json").read_text())
    charter["prompt_sha256"] = "0" * 64
    write_charter(inst, charter)

    with pytest.raises(ov.CheckViolations) as excinfo:
        ov.intake_check(make_args(ws))
    assert "CHR-1" in rule_ids_of(excinfo.value.violations)
    assert not (inst / "outputs" / "overseer" / "charter.lock.json").exists()


def test_intake_check_charter_missing_is_chr1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path
    inst, *_ = setup_intake_happy_path(ws, tmp_path, monkeypatch)
    (inst / "outputs" / "charter.json").unlink()

    with pytest.raises(ov.CheckViolations) as excinfo:
        ov.intake_check(make_args(ws))
    assert "CHR-1" in rule_ids_of(excinfo.value.violations)


def test_intake_check_charter_wrong_schema_version_is_r15(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path
    inst, *_ = setup_intake_happy_path(ws, tmp_path, monkeypatch)
    charter = json.loads((inst / "outputs" / "charter.json").read_text())
    charter["schema"] = "ao.overseer.charter/v0"
    write_charter(inst, charter)

    with pytest.raises(ov.CheckViolations) as excinfo:
        ov.intake_check(make_args(ws))
    assert "R15" in rule_ids_of(excinfo.value.violations)


@pytest.mark.parametrize("num_asks", [0, 13])
def test_intake_check_charter_ask_count_out_of_range_is_chr1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, num_asks: int
) -> None:
    ws = tmp_path
    inst, *_ = setup_intake_happy_path(ws, tmp_path, monkeypatch)
    charter = json.loads((inst / "outputs" / "charter.json").read_text())
    charter["asks"] = [
        {
            "ask_id": f"A{i}",
            "statement": "x",
            "deliverable_type": "code",
            "acceptance": [{"id": f"A{i}.1", "text": "x"}],
            "usable_bar": ["x"],
            "priority": 1,
        }
        for i in range(1, num_asks + 1)
    ]
    write_charter(inst, charter)

    with pytest.raises(ov.CheckViolations) as excinfo:
        ov.intake_check(make_args(ws))
    assert "CHR-1" in rule_ids_of(excinfo.value.violations)


def test_intake_check_charter_ask_missing_acceptance_is_chr1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path
    inst, *_ = setup_intake_happy_path(ws, tmp_path, monkeypatch)
    charter = json.loads((inst / "outputs" / "charter.json").read_text())
    charter["asks"][0]["acceptance"] = []
    write_charter(inst, charter)

    with pytest.raises(ov.CheckViolations) as excinfo:
        ov.intake_check(make_args(ws))
    assert "CHR-1" in rule_ids_of(excinfo.value.violations)


def test_intake_check_charter_ask_missing_usable_bar_is_chr1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path
    inst, *_ = setup_intake_happy_path(ws, tmp_path, monkeypatch)
    charter = json.loads((inst / "outputs" / "charter.json").read_text())
    charter["asks"][0]["usable_bar"] = []
    write_charter(inst, charter)

    with pytest.raises(ov.CheckViolations) as excinfo:
        ov.intake_check(make_args(ws))
    assert "CHR-1" in rule_ids_of(excinfo.value.violations)


# =============================================================================================
# ===== AC1: one passing + one failing fixture per rule id (parametrized) =====================
# ===== The happy-path tests above are each rule's "passing fixture"; this section provides ===
# ===== each rule's "failing fixture", asserting the exact rule id via CheckViolations. =======
# =============================================================================================


def test_r1_trailing_comma_in_manifest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = tmp_path
    inst, *_ = setup_ckpt_happy_path(ws, tmp_path, monkeypatch)
    (inst / "outputs" / "manifests" / "ck-01.json").write_text('{"tasks": [1, 2,]}')

    with pytest.raises(ov.CheckViolations) as excinfo:
        ov.ckpt_check(make_args(ws))
    assert "R1" in rule_ids_of(excinfo.value.violations)


def test_r1_manifest_not_shaped_tasks_object(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path
    inst, *_ = setup_ckpt_happy_path(ws, tmp_path, monkeypatch)
    write_manifest(inst, "ck-01.json", {"not_tasks": []})

    with pytest.raises(ov.CheckViolations) as excinfo:
        ov.ckpt_check(make_args(ws))
    assert "R1" in rule_ids_of(excinfo.value.violations)


def test_r2_unit_missing_required_field(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = tmp_path
    inst, *_ = setup_ckpt_happy_path(ws, tmp_path, monkeypatch)
    manifest = json.loads((inst / "outputs" / "manifests" / "ck-01.json").read_text())
    del manifest["tasks"][0]["effort"]
    write_manifest(inst, "ck-01.json", manifest)

    with pytest.raises(ov.CheckViolations) as excinfo:
        ov.ckpt_check(make_args(ws))
    assert "R2" in rule_ids_of(excinfo.value.violations)


def test_r2_unit_unexpected_extra_field(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = tmp_path
    inst, *_ = setup_ckpt_happy_path(ws, tmp_path, monkeypatch)
    manifest = json.loads((inst / "outputs" / "manifests" / "ck-01.json").read_text())
    manifest["tasks"][0]["not_a_real_field"] = "surprise"
    write_manifest(inst, "ck-01.json", manifest)

    with pytest.raises(ov.CheckViolations) as excinfo:
        ov.ckpt_check(make_args(ws))
    assert "R2" in rule_ids_of(excinfo.value.violations)


def test_r2_next_checkpoint_missing_required_field(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path
    inst, *_ = setup_ckpt_happy_path(ws, tmp_path, monkeypatch)
    manifest = json.loads((inst / "outputs" / "manifests" / "ck-01.json").read_text())
    del manifest["tasks"][1]["emit_tasks"]
    write_manifest(inst, "ck-01.json", manifest)

    with pytest.raises(ov.CheckViolations) as excinfo:
        ov.ckpt_check(make_args(ws))
    assert "R2" in rule_ids_of(excinfo.value.violations)


def test_r2_tail_unexpected_extra_field(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = tmp_path
    inst, _unit_id, emitter, _next_wave, _cfg = setup_ckpt_happy_path(ws, tmp_path, monkeypatch)
    tail = make_tail_entries(emitter=emitter)
    tail[0]["surprise_field"] = 1
    write_manifest_entries(inst, "ck-01.json", tail)

    with pytest.raises(ov.CheckViolations) as excinfo:
        ov.ckpt_check(make_args(ws))
    assert "R2" in rule_ids_of(excinfo.value.violations)


def test_r3_duplicate_id_within_manifest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = tmp_path
    inst, unit_id, emitter, next_wave, cfg_dict = setup_ckpt_happy_path(ws, tmp_path, monkeypatch)
    manifest = json.loads((inst / "outputs" / "manifests" / "ck-01.json").read_text())
    manifest["tasks"].append(dict(manifest["tasks"][0]))  # duplicate the unit entry verbatim
    write_manifest(inst, "ck-01.json", manifest)

    with pytest.raises(ov.CheckViolations) as excinfo:
        ov.ckpt_check(make_args(ws))
    assert "R3" in rule_ids_of(excinfo.value.violations)


def test_r3_id_already_exists_in_state_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path
    # setup_ckpt_happy_path(k=1) always names its one unit "w02-01-alpha" (next_wave=2).
    inst, unit_id, *_ = setup_ckpt_happy_path(
        ws,
        tmp_path,
        monkeypatch,
        existing_state_tasks={"w02-01-alpha": {"status": "succeeded"}},
    )

    with pytest.raises(ov.CheckViolations) as excinfo:
        ov.ckpt_check(make_args(ws))
    assert "R3" in rule_ids_of(excinfo.value.violations)


def test_r3_unknown_id_pattern(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = tmp_path
    inst, *_ = setup_ckpt_happy_path(ws, tmp_path, monkeypatch)
    manifest = json.loads((inst / "outputs" / "manifests" / "ck-01.json").read_text())
    manifest["tasks"][0]["id"] = "totally-not-a-valid-id"
    write_manifest(inst, "ck-01.json", manifest)

    with pytest.raises(ov.CheckViolations) as excinfo:
        ov.ckpt_check(make_args(ws))
    assert "R3" in rule_ids_of(excinfo.value.violations)


def test_r3_unit_id_embeds_wrong_wave_number(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path
    inst, *_ = setup_ckpt_happy_path(ws, tmp_path, monkeypatch)
    manifest = json.loads((inst / "outputs" / "manifests" / "ck-01.json").read_text())
    # This manifest emits wave 2 (ck-01 reviews wave 1) -- "w05-01-alpha" embeds the wrong wave.
    manifest["tasks"][0]["id"] = "w05-01-alpha"
    manifest["tasks"][1]["depends_on"] = ["w05-01-alpha"]
    manifest["tasks"][1]["inputs"][-1] = f"{_INSTANCE_DIR}/outputs/progress/w05-01-alpha.json"
    write_manifest(inst, "ck-01.json", manifest)

    with pytest.raises(ov.CheckViolations) as excinfo:
        ov.ckpt_check(make_args(ws))
    assert "R3" in rule_ids_of(excinfo.value.violations)


def test_r4_unit_count_exceeds_allowed_wave_size(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path
    inst, *_ = setup_ckpt_happy_path(ws, tmp_path, monkeypatch)
    write_digest(inst, 1, allowed_wave_size=0)  # one unit already exceeds a cap of 0

    with pytest.raises(ov.CheckViolations) as excinfo:
        ov.ckpt_check(make_args(ws))
    assert "R4" in rule_ids_of(excinfo.value.violations)


def test_r4_intake_time_cap_from_derive_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R4 at intake-check time uses `_intake_allowed_wave_size` (no digest exists yet) --
    shrinking `wave_size` to 0 in config forces the derived cap to reject even one unit.
    """
    ws = tmp_path
    inst, *_ = setup_intake_happy_path(ws, tmp_path, monkeypatch, wave_size=1, max_waves=1)
    manifest = json.loads((inst / "outputs" / "manifests" / "intake.json").read_text())
    second_unit = make_unit_entry(1, 2, "beta", emitter="intake")
    write_brief(inst, 1, second_unit["id"])
    manifest["tasks"].insert(1, second_unit)
    manifest["tasks"][-1]["depends_on"] = [manifest["tasks"][0]["id"], second_unit["id"]]
    manifest["tasks"][-1]["inputs"].append(
        f"{_INSTANCE_DIR}/outputs/progress/{second_unit['id']}.json"
    )
    write_manifest(inst, "intake.json", manifest)

    with pytest.raises(ov.CheckViolations) as excinfo:
        ov.intake_check(make_args(ws))
    assert "R4" in rule_ids_of(excinfo.value.violations)


def test_r5_expander_present_when_cap_is_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path
    inst, unit_id, emitter, next_wave, cfg_dict = setup_ckpt_happy_path(
        ws, tmp_path, monkeypatch, max_expanders_per_wave=0
    )
    manifest = json.loads((inst / "outputs" / "manifests" / "ck-01.json").read_text())
    expander_id = f"w{next_wave:02d}-02-expand-thing"
    expander_entry = make_unit_entry(next_wave, 2, "expand-thing", emitter=emitter, kind="expand")
    expander_entry["emit_tasks"] = True
    manifest["tasks"].insert(1, expander_entry)
    write_manifest(inst, "ck-01.json", manifest)
    assert expander_id  # sanity: id constructed as expected

    with pytest.raises(ov.CheckViolations) as excinfo:
        ov.ckpt_check(make_args(ws))
    assert "R5" in rule_ids_of(excinfo.value.violations)


def test_r6_brief_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = tmp_path
    inst, unit_id, next_wave = (None, None, None)
    inst, unit_id, emitter, next_wave, _cfg = setup_ckpt_happy_path(ws, tmp_path, monkeypatch)
    ov.brief_path(inst, next_wave, unit_id).unlink()

    with pytest.raises(ov.CheckViolations) as excinfo:
        ov.ckpt_check(make_args(ws))
    assert "R6" in rule_ids_of(excinfo.value.violations)


def test_r6_instruction_points_at_workspace_content_not_kind_map(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ticket AC2's exact scenario: a unit whose `instruction` points at
    `outputs/waves/w01/w01-01-x.md` (NOT the kind_map's real instruction path) -- dev-security
    CRITICAL #1, no fuzzy matching.
    """
    ws = tmp_path
    inst, *_ = setup_ckpt_happy_path(ws, tmp_path, monkeypatch)
    manifest = json.loads((inst / "outputs" / "manifests" / "ck-01.json").read_text())
    manifest["tasks"][0]["instruction"] = "outputs/waves/w01/w01-01-x.md"
    write_manifest(inst, "ck-01.json", manifest)

    with pytest.raises(ov.CheckViolations) as excinfo:
        ov.ckpt_check(make_args(ws))
    assert "R6" in rule_ids_of(excinfo.value.violations)


def test_r6_agent_does_not_match_kind_map(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = tmp_path
    inst, *_ = setup_ckpt_happy_path(ws, tmp_path, monkeypatch)
    manifest = json.loads((inst / "outputs" / "manifests" / "ck-01.json").read_text())
    manifest["tasks"][0]["agent"] = "not-the-real-agent"
    write_manifest(inst, "ck-01.json", manifest)

    with pytest.raises(ov.CheckViolations) as excinfo:
        ov.ckpt_check(make_args(ws))
    assert "R6" in rule_ids_of(excinfo.value.violations)


def test_r6_ask_ids_not_subset_of_charter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = tmp_path
    inst, unit_id, emitter, next_wave, _cfg = setup_ckpt_happy_path(ws, tmp_path, monkeypatch)
    write_brief(inst, next_wave, unit_id, ask_ids=["A99-does-not-exist"])

    with pytest.raises(ov.CheckViolations) as excinfo:
        ov.ckpt_check(make_args(ws))
    assert "R6" in rule_ids_of(excinfo.value.violations)


def test_r7_unit_outputs_not_exactly_report_and_breadcrumb(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path
    inst, *_ = setup_ckpt_happy_path(ws, tmp_path, monkeypatch)
    manifest = json.loads((inst / "outputs" / "manifests" / "ck-01.json").read_text())
    manifest["tasks"][0]["outputs"] = ["some/other/path.md"]
    write_manifest(inst, "ck-01.json", manifest)

    with pytest.raises(ov.CheckViolations) as excinfo:
        ov.ckpt_check(make_args(ws))
    assert "R7" in rule_ids_of(excinfo.value.violations)


def test_r7_unit_inputs_missing_brief_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = tmp_path
    inst, *_ = setup_ckpt_happy_path(ws, tmp_path, monkeypatch)
    manifest = json.loads((inst / "outputs" / "manifests" / "ck-01.json").read_text())
    manifest["tasks"][0]["inputs"] = [f"{_INSTANCE_DIR}/overseer-contract.md"]
    write_manifest(inst, "ck-01.json", manifest)

    with pytest.raises(ov.CheckViolations) as excinfo:
        ov.ckpt_check(make_args(ws))
    assert "R7" in rule_ids_of(excinfo.value.violations)


def test_r8_dangling_depends_on(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = tmp_path
    inst, *_ = setup_ckpt_happy_path(ws, tmp_path, monkeypatch)
    manifest = json.loads((inst / "outputs" / "manifests" / "ck-01.json").read_text())
    manifest["tasks"][0]["depends_on"] = ["ck-01", "totally-nonexistent-id"]
    write_manifest(inst, "ck-01.json", manifest)

    with pytest.raises(ov.CheckViolations) as excinfo:
        ov.ckpt_check(make_args(ws))
    assert "R8" in rule_ids_of(excinfo.value.violations)


def test_r8_depends_on_previous_wave_unit_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ticket AC2's exact scenario: a `depends_on` on a PREVIOUS wave's unit id (not part of
    THIS manifest) -- the contract requires referencing it via `inputs` instead.
    """
    ws = tmp_path
    inst, *_ = setup_ckpt_happy_path(ws, tmp_path, monkeypatch)
    manifest = json.loads((inst / "outputs" / "manifests" / "ck-01.json").read_text())
    manifest["tasks"][0]["depends_on"] = ["ck-01", "w01-01-old-wave-unit"]
    write_manifest(inst, "ck-01.json", manifest)

    with pytest.raises(ov.CheckViolations) as excinfo:
        ov.ckpt_check(make_args(ws))
    assert "R8" in rule_ids_of(excinfo.value.violations)


def test_r8_depends_on_missing_emitter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = tmp_path
    inst, unit_id, emitter, *_ = setup_ckpt_happy_path(ws, tmp_path, monkeypatch)
    manifest = json.loads((inst / "outputs" / "manifests" / "ck-01.json").read_text())
    manifest["tasks"][0]["depends_on"] = []
    write_manifest(inst, "ck-01.json", manifest)

    with pytest.raises(ov.CheckViolations) as excinfo:
        ov.ckpt_check(make_args(ws))
    assert "R8" in rule_ids_of(excinfo.value.violations)


def test_r9_unit_carrying_post_hook(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = tmp_path
    inst, *_ = setup_ckpt_happy_path(ws, tmp_path, monkeypatch)
    manifest = json.loads((inst / "outputs" / "manifests" / "ck-01.json").read_text())
    manifest["tasks"][0]["post_hook"] = {"use": "ov-ckpt-check"}
    write_manifest(inst, "ck-01.json", manifest)

    with pytest.raises(ov.CheckViolations) as excinfo:
        ov.ckpt_check(make_args(ws))
    assert "R9" in rule_ids_of(excinfo.value.violations)


def test_r9_model_on_a_unit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = tmp_path
    inst, *_ = setup_ckpt_happy_path(ws, tmp_path, monkeypatch)
    manifest = json.loads((inst / "outputs" / "manifests" / "ck-01.json").read_text())
    manifest["tasks"][0]["model"] = "some-model"
    write_manifest(inst, "ck-01.json", manifest)

    with pytest.raises(ov.CheckViolations) as excinfo:
        ov.ckpt_check(make_args(ws))
    assert "R9" in rule_ids_of(excinfo.value.violations)


def test_r9_max_turns_on_a_unit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = tmp_path
    inst, *_ = setup_ckpt_happy_path(ws, tmp_path, monkeypatch)
    manifest = json.loads((inst / "outputs" / "manifests" / "ck-01.json").read_text())
    manifest["tasks"][0]["max_turns"] = 5
    write_manifest(inst, "ck-01.json", manifest)

    with pytest.raises(ov.CheckViolations) as excinfo:
        ov.ckpt_check(make_args(ws))
    assert "R9" in rule_ids_of(excinfo.value.violations)


def test_r9_unit_wrong_pre_hook_shape(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = tmp_path
    inst, *_ = setup_ckpt_happy_path(ws, tmp_path, monkeypatch)
    manifest = json.loads((inst / "outputs" / "manifests" / "ck-01.json").read_text())
    manifest["tasks"][0]["pre_hook"] = {"use": "ov-unit-gate", "on_failure": "fail_task"}
    write_manifest(inst, "ck-01.json", manifest)

    with pytest.raises(ov.CheckViolations) as excinfo:
        ov.ckpt_check(make_args(ws))
    assert "R9" in rule_ids_of(excinfo.value.violations)


def test_r9_unit_invalid_effort(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = tmp_path
    inst, *_ = setup_ckpt_happy_path(ws, tmp_path, monkeypatch)
    manifest = json.loads((inst / "outputs" / "manifests" / "ck-01.json").read_text())
    manifest["tasks"][0]["effort"] = "xhigh"  # valid for overseer_effort, never a unit
    write_manifest(inst, "ck-01.json", manifest)

    with pytest.raises(ov.CheckViolations) as excinfo:
        ov.ckpt_check(make_args(ws))
    assert "R9" in rule_ids_of(excinfo.value.violations)


def test_r10_empty_tasks_list(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = tmp_path
    inst, *_ = setup_ckpt_happy_path(ws, tmp_path, monkeypatch)
    write_manifest(inst, "ck-01.json", {"tasks": []})

    with pytest.raises(ov.CheckViolations) as excinfo:
        ov.ckpt_check(make_args(ws))
    assert "R10" in rule_ids_of(excinfo.value.violations)


def test_r10_both_next_checkpoint_and_tail_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path
    inst, unit_id, emitter, next_wave, cfg_dict = setup_ckpt_happy_path(ws, tmp_path, monkeypatch)
    manifest = json.loads((inst / "outputs" / "manifests" / "ck-01.json").read_text())
    manifest["tasks"].extend(make_tail_entries(emitter=emitter))
    write_manifest(inst, "ck-01.json", manifest)

    with pytest.raises(ov.CheckViolations) as excinfo:
        ov.ckpt_check(make_args(ws))
    assert "R10" in rule_ids_of(excinfo.value.violations)


def test_r10_final_push_present_when_config_final_push_false(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path
    inst, unit_id, emitter, next_wave, cfg_dict = setup_ckpt_happy_path(
        ws, tmp_path, monkeypatch, final_push=False
    )
    tail = make_tail_entries(emitter=emitter, final_push=True)  # present despite final_push=False
    write_manifest_entries(inst, "ck-01.json", tail)

    with pytest.raises(ov.CheckViolations) as excinfo:
        ov.ckpt_check(make_args(ws))
    assert "R10" in rule_ids_of(excinfo.value.violations)


def test_r10_final_push_missing_when_config_final_push_true(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path
    inst, unit_id, emitter, next_wave, cfg_dict = setup_ckpt_happy_path(
        ws, tmp_path, monkeypatch, final_push=True
    )
    tail = make_tail_entries(emitter=emitter, final_push=False)  # missing despite final_push=True
    write_manifest_entries(inst, "ck-01.json", tail)

    with pytest.raises(ov.CheckViolations) as excinfo:
        ov.ckpt_check(make_args(ws))
    assert "R10" in rule_ids_of(excinfo.value.violations)


def test_r10_next_checkpoint_missing_unit_breadcrumb_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path
    inst, unit_id, *_ = setup_ckpt_happy_path(ws, tmp_path, monkeypatch)
    manifest = json.loads((inst / "outputs" / "manifests" / "ck-01.json").read_text())
    manifest["tasks"][1]["inputs"] = [
        p for p in manifest["tasks"][1]["inputs"] if not p.endswith(f"{unit_id}.json")
    ]
    write_manifest(inst, "ck-01.json", manifest)

    with pytest.raises(ov.CheckViolations) as excinfo:
        ov.ckpt_check(make_args(ws))
    assert "R10" in rule_ids_of(excinfo.value.violations)


def test_r10_next_checkpoint_wrong_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = tmp_path
    inst, *_ = setup_ckpt_happy_path(ws, tmp_path, monkeypatch)
    manifest = json.loads((inst / "outputs" / "manifests" / "ck-01.json").read_text())
    manifest["tasks"][1]["id"] = "ck-99"
    write_manifest(inst, "ck-01.json", manifest)

    with pytest.raises(ov.CheckViolations) as excinfo:
        ov.ckpt_check(make_args(ws))
    assert "R10" in rule_ids_of(excinfo.value.violations)


def test_r10_next_checkpoint_missing_model_when_overseer_model_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path
    inst, unit_id, emitter, next_wave, cfg_dict = setup_ckpt_happy_path(
        ws, tmp_path, monkeypatch, overseer_model="claude-sonnet-5"
    )
    manifest = json.loads((inst / "outputs" / "manifests" / "ck-01.json").read_text())
    manifest["tasks"][1].pop("model", None)
    write_manifest(inst, "ck-01.json", manifest)

    with pytest.raises(ov.CheckViolations) as excinfo:
        ov.ckpt_check(make_args(ws))
    assert "R10" in rule_ids_of(excinfo.value.violations)


def test_r10_next_checkpoint_has_model_when_overseer_model_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path
    inst, unit_id, emitter, next_wave, cfg_dict = setup_ckpt_happy_path(
        ws, tmp_path, monkeypatch, overseer_model=""
    )
    manifest = json.loads((inst / "outputs" / "manifests" / "ck-01.json").read_text())
    manifest["tasks"][1]["model"] = "should-not-be-here"
    write_manifest(inst, "ck-01.json", manifest)

    with pytest.raises(ov.CheckViolations) as excinfo:
        ov.ckpt_check(make_args(ws))
    assert "R10" in rule_ids_of(excinfo.value.violations)


def test_r10_hold_shape_depends_on_emitter_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The HOLD/empty-wave shape (`depends_on == [emitter]`) is a VALID R10 shape, not a
    violation, when zero units/expanders are emitted alongside the next checkpoint."""
    ws = tmp_path
    inst = ws / _INSTANCE_DIR
    cfg_dict = write_config(inst)
    prompt_sha256 = write_prompt(inst)
    write_charter(inst, make_charter_dict(prompt_sha256))
    ckpt_entry = make_next_ckpt_entry(2, [], emitter="ck-01", cfg_dict=cfg_dict)
    write_manifest_entries(inst, "ck-01.json", [ckpt_entry])
    write_digest(inst, 1, allowed_wave_size=cfg_dict["wave_size"])
    write_state(ws, "run-1", {})
    set_hook_context(monkeypatch, tmp_path, "run-1", "ck-01")

    ov.ckpt_check(make_args(ws))  # must not raise


def test_r10_tail_missing_charter_input_on_final_verify(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path
    inst, unit_id, emitter, next_wave, cfg_dict = setup_ckpt_happy_path(ws, tmp_path, monkeypatch)
    tail = make_tail_entries(emitter=emitter)
    tail[0]["inputs"] = [i for i in tail[0]["inputs"] if "charter.json" not in i]
    write_manifest_entries(inst, "ck-01.json", tail)

    with pytest.raises(ov.CheckViolations) as excinfo:
        ov.ckpt_check(make_args(ws))
    assert "R10" in rule_ids_of(excinfo.value.violations)


def test_r10_tail_final_push_wrong_isolation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path
    inst, unit_id, emitter, next_wave, cfg_dict = setup_ckpt_happy_path(ws, tmp_path, monkeypatch)
    tail = make_tail_entries(emitter=emitter)
    tail[-1]["isolation"] = "worktree"
    write_manifest_entries(inst, "ck-01.json", tail)

    with pytest.raises(ov.CheckViolations) as excinfo:
        ov.ckpt_check(make_args(ws))
    assert "R10" in rule_ids_of(excinfo.value.violations)


def test_r15_manifest_over_1mib(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = tmp_path
    inst, *_ = setup_ckpt_happy_path(ws, tmp_path, monkeypatch)
    path = inst / "outputs" / "manifests" / "ck-01.json"
    path.write_text("x" * int(1.5 * 1024 * 1024))

    with pytest.raises(ov.CheckViolations) as excinfo:
        ov.ckpt_check(make_args(ws))
    assert "R15" in rule_ids_of(excinfo.value.violations)


def test_r15_brief_over_1mib(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = tmp_path
    inst, unit_id, emitter, next_wave, _cfg = setup_ckpt_happy_path(ws, tmp_path, monkeypatch)
    ov.brief_path(inst, next_wave, unit_id).write_text("x" * (ov.JSON_MAX_BYTES + 1000))

    with pytest.raises(ov.CheckViolations) as excinfo:
        ov.ckpt_check(make_args(ws))
    assert "R15" in rule_ids_of(excinfo.value.violations)


def test_r15_brief_wrong_schema_version(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = tmp_path
    inst, unit_id, emitter, next_wave, _cfg = setup_ckpt_happy_path(ws, tmp_path, monkeypatch)
    write_brief(inst, next_wave, unit_id, schema="ao.overseer.brief/v0")

    with pytest.raises(ov.CheckViolations) as excinfo:
        ov.ckpt_check(make_args(ws))
    assert "R15" in rule_ids_of(excinfo.value.violations)


# =============================================================================================
# ===== Additional coverage: classification edge cases, next-checkpoint field-by-field, =======
# ===== charter edge cases, digest-wiring internal errors (>=90% line coverage target). =======
# =============================================================================================


def test_manifest_not_found_is_r1(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = tmp_path
    inst, *_ = setup_ckpt_happy_path(ws, tmp_path, monkeypatch)
    (inst / "outputs" / "manifests" / "ck-01.json").unlink()

    with pytest.raises(ov.CheckViolations) as excinfo:
        ov.ckpt_check(make_args(ws))
    assert "R1" in rule_ids_of(excinfo.value.violations)


def test_non_dict_entry_in_tasks_list_is_unknown_and_flagged_r3(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path
    inst, *_ = setup_ckpt_happy_path(ws, tmp_path, monkeypatch)
    manifest = json.loads((inst / "outputs" / "manifests" / "ck-01.json").read_text())
    manifest["tasks"].append("not-a-dict-entry")
    write_manifest(inst, "ck-01.json", manifest)

    with pytest.raises(ov.CheckViolations) as excinfo:
        ov.ckpt_check(make_args(ws))
    assert "R3" in rule_ids_of(excinfo.value.violations)


def test_entry_missing_id_field_is_r3(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = tmp_path
    inst, *_ = setup_ckpt_happy_path(ws, tmp_path, monkeypatch)
    manifest = json.loads((inst / "outputs" / "manifests" / "ck-01.json").read_text())
    del manifest["tasks"][0]["id"]
    write_manifest(inst, "ck-01.json", manifest)

    with pytest.raises(ov.CheckViolations) as excinfo:
        ov.ckpt_check(make_args(ws))
    assert "R3" in rule_ids_of(excinfo.value.violations)


def test_r6_brief_ask_ids_empty_list(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = tmp_path
    inst, unit_id, emitter, next_wave, _cfg = setup_ckpt_happy_path(ws, tmp_path, monkeypatch)
    write_brief(inst, next_wave, unit_id, ask_ids=[])

    with pytest.raises(ov.CheckViolations) as excinfo:
        ov.ckpt_check(make_args(ws))
    assert "R6" in rule_ids_of(excinfo.value.violations)


def test_r6_brief_kind_unknown_is_not_in_kind_map(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path
    inst, unit_id, emitter, next_wave, _cfg = setup_ckpt_happy_path(ws, tmp_path, monkeypatch)
    write_brief(inst, next_wave, unit_id, kind="not-a-real-kind")

    with pytest.raises(ov.CheckViolations) as excinfo:
        ov.ckpt_check(make_args(ws))
    assert "R6" in rule_ids_of(excinfo.value.violations)


def test_r8_depends_on_not_a_list(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = tmp_path
    inst, *_ = setup_ckpt_happy_path(ws, tmp_path, monkeypatch)
    manifest = json.loads((inst / "outputs" / "manifests" / "ck-01.json").read_text())
    manifest["tasks"][0]["depends_on"] = "not-a-list"
    write_manifest(inst, "ck-01.json", manifest)

    with pytest.raises(ov.CheckViolations) as excinfo:
        ov.ckpt_check(make_args(ws))
    assert "R8" in rule_ids_of(excinfo.value.violations)


@pytest.mark.parametrize(
    "field, bad_value",
    [
        ("agent", "not-manager"),
        ("instruction", "some/other/path.md"),
        ("task_manifest_path", "wrong/path.json"),
        ("outputs", ["wrong.json"]),
        ("effort", "low"),
    ],
)
def test_next_checkpoint_exact_shape_field_mismatches_are_r10(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str, bad_value: object
) -> None:
    ws = tmp_path
    inst, *_ = setup_ckpt_happy_path(ws, tmp_path, monkeypatch)
    manifest = json.loads((inst / "outputs" / "manifests" / "ck-01.json").read_text())
    manifest["tasks"][1][field] = bad_value
    write_manifest(inst, "ck-01.json", manifest)

    with pytest.raises(ov.CheckViolations) as excinfo:
        ov.ckpt_check(make_args(ws))
    assert "R10" in rule_ids_of(excinfo.value.violations)


def test_next_checkpoint_wrong_pre_hook_shape_is_r10(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path
    inst, *_ = setup_ckpt_happy_path(ws, tmp_path, monkeypatch)
    manifest = json.loads((inst / "outputs" / "manifests" / "ck-01.json").read_text())
    manifest["tasks"][1]["pre_hook"] = {"use": "ov-ckpt-prep", "on_failure": "ignore"}
    write_manifest(inst, "ck-01.json", manifest)

    with pytest.raises(ov.CheckViolations) as excinfo:
        ov.ckpt_check(make_args(ws))
    assert "R10" in rule_ids_of(excinfo.value.violations)


def test_next_checkpoint_wrong_post_hook_shape_is_r10(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path
    inst, *_ = setup_ckpt_happy_path(ws, tmp_path, monkeypatch)
    manifest = json.loads((inst / "outputs" / "manifests" / "ck-01.json").read_text())
    manifest["tasks"][1]["post_hook"] = {"use": "ov-ckpt-check", "on_failure": "ignore"}
    write_manifest(inst, "ck-01.json", manifest)

    with pytest.raises(ov.CheckViolations) as excinfo:
        ov.ckpt_check(make_args(ws))
    assert "R10" in rule_ids_of(excinfo.value.violations)


@pytest.mark.parametrize(
    "which, field, bad_value",
    [
        ("final-verify", "agent", "not-tester"),
        ("final-verify", "instruction", "wrong.md"),
        ("final-verify", "depends_on", ["wrong-emitter"]),
        ("final-verify", "outputs", ["wrong.md"]),
        ("closeout", "agent", "not-manager"),
        ("closeout", "instruction", "wrong.md"),
        ("closeout", "depends_on", ["wrong-dep"]),
        ("closeout", "outputs", ["wrong.md"]),
        ("final-push", "agent", "not-git-operator"),
        ("final-push", "instruction", "wrong.md"),
        ("final-push", "depends_on", ["wrong-dep"]),
        ("final-push", "outputs", ["wrong.md"]),
    ],
)
def test_tail_entry_exact_shape_field_mismatches_are_r10(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, which: str, field: str, bad_value: object
) -> None:
    ws = tmp_path
    inst, unit_id, emitter, next_wave, cfg_dict = setup_ckpt_happy_path(ws, tmp_path, monkeypatch)
    tail = make_tail_entries(emitter=emitter)
    by_id = {e["id"]: e for e in tail}
    by_id[which][field] = bad_value
    write_manifest_entries(inst, "ck-01.json", tail)

    with pytest.raises(ov.CheckViolations) as excinfo:
        ov.ckpt_check(make_args(ws))
    assert "R10" in rule_ids_of(excinfo.value.violations)


def test_ckpt_check_missing_digest_is_internal_tool_error_not_a_rule_violation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`digest.json` is TOOL-owned (written by `ckpt_prep`'s pre_hook before this post_hook
    ever runs) -- its absence is a wiring problem (`ToolError`, exit 1), never a `CheckViolations`
    rule finding."""
    ws = tmp_path
    inst, *_ = setup_ckpt_happy_path(ws, tmp_path, monkeypatch)
    ov.digest_path(inst, 1).unlink()

    with pytest.raises(ov.ToolError):
        ov.ckpt_check(make_args(ws))


def test_ckpt_check_malformed_digest_is_internal_tool_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path
    inst, *_ = setup_ckpt_happy_path(ws, tmp_path, monkeypatch)
    ov.digest_path(inst, 1).write_text("not valid json")

    with pytest.raises(ov.ToolError):
        ov.ckpt_check(make_args(ws))


def test_ckpt_check_digest_missing_allowed_wave_size_is_internal_tool_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path
    inst, *_ = setup_ckpt_happy_path(ws, tmp_path, monkeypatch)
    ov.digest_path(inst, 1).write_text(json.dumps({"schema": ov.DIGEST_SCHEMA, "cadence": {}}))

    with pytest.raises(ov.ToolError):
        ov.ckpt_check(make_args(ws))


def test_intake_check_charter_malformed_json_is_chr1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path
    inst, *_ = setup_intake_happy_path(ws, tmp_path, monkeypatch)
    (inst / "outputs" / "charter.json").write_text("{not valid json")

    with pytest.raises(ov.CheckViolations) as excinfo:
        ov.intake_check(make_args(ws))
    assert "CHR-1" in rule_ids_of(excinfo.value.violations)


def test_intake_check_charter_oversize_is_r15(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path
    inst, *_ = setup_intake_happy_path(ws, tmp_path, monkeypatch)
    (inst / "outputs" / "charter.json").write_text("x" * (ov.JSON_MAX_BYTES + 1000))

    with pytest.raises(ov.CheckViolations) as excinfo:
        ov.intake_check(make_args(ws))
    assert "R15" in rule_ids_of(excinfo.value.violations)


def test_intake_check_charter_not_a_json_object_is_chr1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path
    inst, *_ = setup_intake_happy_path(ws, tmp_path, monkeypatch)
    (inst / "outputs" / "charter.json").write_text(json.dumps([1, 2, 3]))

    with pytest.raises(ov.CheckViolations) as excinfo:
        ov.intake_check(make_args(ws))
    assert "CHR-1" in rule_ids_of(excinfo.value.violations)


def test_intake_check_prompt_md_missing_is_chr1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path
    inst, *_ = setup_intake_happy_path(ws, tmp_path, monkeypatch)
    (inst / "prompt.md").unlink()

    with pytest.raises(ov.CheckViolations) as excinfo:
        ov.intake_check(make_args(ws))
    assert "CHR-1" in rule_ids_of(excinfo.value.violations)


def test_intake_check_charter_ask_entry_not_a_dict_is_chr1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path
    inst, *_ = setup_intake_happy_path(ws, tmp_path, monkeypatch)
    charter = json.loads((inst / "outputs" / "charter.json").read_text())
    charter["asks"] = ["not-a-dict"]
    write_charter(inst, charter)

    with pytest.raises(ov.CheckViolations) as excinfo:
        ov.intake_check(make_args(ws))
    assert "CHR-1" in rule_ids_of(excinfo.value.violations)


# =============================================================================================
# ===== main() CLI-boundary: multi-violation stderr + exit code (AC1's "parametrized" spirit) =
# =============================================================================================


@pytest.mark.parametrize(
    "mutate, expected_rule",
    [
        (lambda m: m["tasks"].__setitem__(0, {**m["tasks"][0], "effort": "xhigh"}), "OV-R9"),
        (lambda m: m["tasks"].__setitem__(1, {**m["tasks"][1], "id": "ck-99"}), "OV-R10"),
    ],
)
def test_main_ckpt_check_prints_ov_prefixed_rule_id_and_exits_2(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    mutate,
    expected_rule,
) -> None:
    ws = tmp_path
    inst, *_ = setup_ckpt_happy_path(ws, tmp_path, monkeypatch)
    manifest = json.loads((inst / "outputs" / "manifests" / "ck-01.json").read_text())
    mutate(manifest)
    write_manifest(inst, "ck-01.json", manifest)

    code = ov.main(
        [
            "ckpt-check",
            "--workspace-root",
            str(ws),
            "--instance-dir",
            _INSTANCE_DIR,
            "--now",
            _NOW,
        ]
    )
    assert code == 2
    captured = capsys.readouterr()
    assert expected_rule in captured.err


def test_main_ckpt_check_multiple_violations_one_stderr_line_each(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ws = tmp_path
    inst, *_ = setup_ckpt_happy_path(ws, tmp_path, monkeypatch)
    manifest = json.loads((inst / "outputs" / "manifests" / "ck-01.json").read_text())
    manifest["tasks"][0]["model"] = "x"  # R9
    manifest["tasks"][1]["id"] = "ck-99"  # R10
    write_manifest(inst, "ck-01.json", manifest)

    code = ov.main(
        ["ckpt-check", "--workspace-root", str(ws), "--instance-dir", _INSTANCE_DIR, "--now", _NOW]
    )
    assert code == 2
    lines = [line for line in capsys.readouterr().err.splitlines() if line.strip()]
    assert len(lines) >= 2
    assert any(line.startswith("OV-R9:") for line in lines)
    assert any(line.startswith("OV-R10:") for line in lines)


def test_main_ckpt_check_dry_run_task_id_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--dry-run --task-id` only substitutes for the hook context file's own `task_id`
    (`read_hook_context`'s documented contract) -- `AO_HOOK_CONTEXT_PATH` (for `run_id`) must
    still be set, exactly as `setup_ckpt_happy_path` already arranges."""
    ws = tmp_path
    inst, *_ = setup_ckpt_happy_path(ws, tmp_path, monkeypatch)

    code = ov.main(
        [
            "ckpt-check",
            "--workspace-root",
            str(ws),
            "--instance-dir",
            _INSTANCE_DIR,
            "--now",
            _NOW,
            "--dry-run",
            "--task-id",
            "ck-01",
        ]
    )
    assert code == 0


def test_main_intake_check_success_exit_0(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = tmp_path
    setup_intake_happy_path(ws, tmp_path, monkeypatch)
    code = ov.main(
        [
            "intake-check",
            "--workspace-root",
            str(ws),
            "--instance-dir",
            _INSTANCE_DIR,
            "--now",
            _NOW,
        ]
    )
    assert code == 0


# =============================================================================================
# ===== AC4: the contract's OWN rendered example shapes must pass with zero violations ========
# =============================================================================================

_DUMMY_ID = "o-ab12cd-sample-slug"
_DUMMY_INSTANCE_DIR = f"workflows/overseer-runner/runs/{_DUMMY_ID}"
_DUMMY_WORKSPACE_ROOT = "/tmp/dummy-workspace-checker-structural"

_CONTRACT_DUMMY_VALUES: dict[str, str] = {
    "id": _DUMMY_ID,
    "instance_dir": _DUMMY_INSTANCE_DIR,
    "workspace_root": _DUMMY_WORKSPACE_ROOT,
    "params.repo_set": "default-set",
    "params.run_budget_usd": "2000",
    "params.task_budget_usd": "75",
    "params.converge_pct": "80",
    "params.stabilize_pct": "90",
    "params.closeout_pct": "95",
    "params.wave_size": "6",
    "params.max_waves": "12",
    "params.wave_max_minutes": "90",
    "params.max_attempts_per_item": "3",
    "params.max_expanders_per_wave": "0",
    "params.max_injected_tasks": "160",
    "params.final_push": "true",
    "params.overseer_effort": "high",
    "params.overseer_model": "",
    "params.python_bin": "python3",
}


def _render_template_file(rel_path: str) -> str:
    raw = (_TEMPLATE_DIR / rel_path).read_text(encoding="utf-8")
    target_is_json = rel_path.endswith(".json.tmpl") or rel_path.endswith(".json")
    return _render(raw, _CONTRACT_DUMMY_VALUES, source_desc=rel_path, escape_json=target_is_json)


def _extract_fenced_json_block(text: str, heading_prefix: str, *, slug: str = "sample-slug") -> Any:
    start = text.index(heading_prefix)
    fence_start = text.index("```json", start) + len("```json")
    fence_end = text.index("```", fence_start)
    raw = text[fence_start:fence_end].replace("<slug>", slug)
    return json.loads(raw)


def test_ac4_contract_unit_and_next_checkpoint_shapes_pass_intake_check_with_zero_violations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rendered_contract = _render_template_file("overseer-contract.md.tmpl")
    unit_entry = _extract_fenced_json_block(rendered_contract, "### Unit (kind", slug="sample-slug")
    assert isinstance(unit_entry, dict)
    # The "Next checkpoint" example's own depends_on/inputs reference TWO units
    # (w01-01-<slug>, w01-02-<slug>) -- construct the second by mirroring the contract's own
    # "substitute NN" instruction (its own text: "the numeric wave/checkpoint segments").
    second_unit_entry = json.loads(
        json.dumps(unit_entry).replace("w01-01-sample-slug", "w01-02-sample-slug")
    )
    next_ckpt_entry = _extract_fenced_json_block(rendered_contract, "### Next checkpoint")

    ws = tmp_path
    inst = ws / _DUMMY_INSTANCE_DIR
    write_config(
        inst,
        wave_size=6,
        max_expanders_per_wave=0,
        final_push=True,
        overseer_effort="high",
        overseer_model="",
        kind_map=_KIND_MAP,
    )
    prompt_sha256 = write_prompt(inst)
    write_charter(inst, make_charter_dict(prompt_sha256))
    write_brief(inst, 1, "w01-01-sample-slug")
    write_brief(inst, 1, "w01-02-sample-slug")
    write_manifest_entries(inst, "intake.json", [unit_entry, second_unit_entry, next_ckpt_entry])
    write_state(ws, "run-1", {})
    set_hook_context(monkeypatch, tmp_path, "run-1", "intake")

    args = argparse.Namespace(
        workspace_root=str(ws),
        instance_dir=_DUMMY_INSTANCE_DIR,
        now=_NOW,
        dry_run=False,
        task_id=None,
        reason=None,
    )
    ov.intake_check(args)  # must not raise -- zero violations
    result = json.loads((inst / "outputs" / "check-result.json").read_text())
    assert result["ok"] is True
    assert result["violations"] == []


def test_ac4_contract_tail_shape_passes_ckpt_check_with_zero_violations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rendered_contract = _render_template_file("overseer-contract.md.tmpl")
    tail_entries = _extract_fenced_json_block(rendered_contract, "### Tail (terminal")
    assert isinstance(tail_entries, list) and len(tail_entries) == 3

    ws = tmp_path
    inst = ws / _DUMMY_INSTANCE_DIR
    write_config(inst, final_push=True, kind_map=_KIND_MAP)
    write_manifest_entries(inst, "ck-01.json", tail_entries)
    write_digest(inst, 1, allowed_wave_size=6)
    write_state(ws, "run-1", {})
    set_hook_context(monkeypatch, tmp_path, "run-1", "ck-01")

    args = argparse.Namespace(
        workspace_root=str(ws),
        instance_dir=_DUMMY_INSTANCE_DIR,
        now=_NOW,
        dry_run=False,
        task_id=None,
        reason=None,
    )
    ov.ckpt_check(args)  # must not raise -- zero violations
    result = json.loads((ov.checkpoint_dir(inst, 1) / "check-result.json").read_text())
    assert result["ok"] is True
    assert result["violations"] == []


# =============================================================================================
# ===== AC5: every ACCEPTED entry builds a real TaskSpec, and every hook name it references ===
# ===== exists in the rendered workflow.json.tmpl's hooks map (property-style over fixtures) ==
# =============================================================================================


def _rendered_workflow_hooks() -> dict:
    workflow = json.loads(_render_template_file("workflow.json.tmpl"))
    return workflow["hooks"]


@pytest.mark.parametrize(
    "entry",
    [
        make_unit_entry(2, 1, "alpha", emitter="ck-01"),
        make_next_ckpt_entry(2, ["w02-01-alpha"], emitter="ck-01", cfg_dict=make_config_dict()),
        *make_tail_entries(emitter="ck-01", final_push=True),
    ],
)
def test_accepted_fixture_entries_construct_a_real_taskspec_with_wired_hooks(entry: dict) -> None:
    task = TaskSpec(**entry)
    hooks = _rendered_workflow_hooks()
    if task.pre_hook is not None:
        assert task.pre_hook.use in hooks
    if task.post_hook is not None:
        assert task.post_hook.use in hooks


def test_contract_unit_and_next_checkpoint_and_tail_fixtures_construct_taskspec() -> None:
    """Same property as above, applied directly to the CONTRACT's own rendered fixtures
    (belt-and-suspenders alongside the AC4 tests, which already prove they pass the checker)."""
    rendered_contract = _render_template_file("overseer-contract.md.tmpl")
    hooks = _rendered_workflow_hooks()

    unit_entry = _extract_fenced_json_block(rendered_contract, "### Unit (kind")
    unit_task = TaskSpec(**unit_entry)
    assert unit_task.pre_hook is not None and unit_task.pre_hook.use in hooks

    next_ckpt_entry = _extract_fenced_json_block(rendered_contract, "### Next checkpoint")
    next_ckpt_task = TaskSpec(**next_ckpt_entry)
    assert next_ckpt_task.pre_hook is not None and next_ckpt_task.pre_hook.use in hooks
    assert next_ckpt_task.post_hook is not None and next_ckpt_task.post_hook.use in hooks

    for tail_entry in _extract_fenced_json_block(rendered_contract, "### Tail (terminal"):
        TaskSpec(**tail_entry)
