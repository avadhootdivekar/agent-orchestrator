"""Table-driven tests for `overseer_tool.py`'s budget stage machine + cadence math (M1).

Ticket: `meta/tickets/E-YAAGhk-overseer-runner-template/T-ABDjSj-tool-state-ledger-budget/`.
Covers AC1 (a-k), AC2 (cadence), AC7 (config CFG-0/1/3), AC8 (state.json field contract).

The tool is standalone stdlib (no repo-internal imports) and is not an installed package, so
it is loaded from its source path via `importlib.util.spec_from_file_location` -- exactly as
the ticket's AC10 coverage command expects. `agent_orchestrator.models` IS imported directly
by THIS test file (never by the tool itself) for the AC8 contract test only.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import sys
from pathlib import Path
from types import ModuleType

import pytest

from agent_orchestrator.models import RunState, TaskRunState, compute_run_usage_totals

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
    spec = importlib.util.spec_from_file_location("overseer_tool_under_test_budget", _TOOL_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Dataclasses (with `from __future__ import annotations` in the tool) resolve their
    # string annotations via `sys.modules[cls.__module__]` -- the module must be registered
    # BEFORE `exec_module` runs, or `dataclass()` itself raises at class-definition time.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


ov = _load_overseer_tool()


def make_config_dict(**overrides: object) -> dict:
    """Template-default `overseer-config.json` (HLD S13.1/S13.4), the same defaults the
    $2000 example (S12.2) is built on: converge_pct=80, stabilize_pct=90, closeout_pct=95,
    run_budget_usd=2000, wave_size=6, max_waves=12.
    """
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


# =============================================================================================
# ===== AC1: the budget stage machine (derive_budget), table-driven ==========================
# =============================================================================================


def _derive(**kwargs: object):
    """`derive_budget` with sane defaults for every param a given test doesn't care about."""
    # Explicitly widened to dict[str, object] (matching **kwargs' own inferred type) so
    # `.update(kwargs)` below doesn't narrow `defaults` to the literal types of the
    # defaults alone (float | bool | str), which `.update()` would then reject `kwargs`
    # against (Pyright/strict-mypy finding, T-ABDjSj follow-up).
    defaults: dict[str, object] = dict(
        spent=0.0,
        run_budget_usd=2000.0,
        wave_size=6,
        time_cap=ov.DEFAULT_TIME_CAP_TASKS,
        est_unit_cost_usd=20.0,
        est_ckpt_cost_usd=20.0,
        final_push=True,
        prev_stage="explore",
        converge_pct=80.0,
        stabilize_pct=90.0,
        closeout_pct=95.0,
        stabilize_wave_size=4,
        k=1,
        max_waves=12,
        stabilize_passes_before=0,
        max_stabilize_passes=2,
        forced_closeout=False,
    )
    defaults.update(kwargs)
    return ov.derive_budget(**defaults)


def test_ac1a_2000_example_converge_then_stabilize_then_closeout() -> None:
    """HLD S12.2's $2000 example, run as a real sequential 3-checkpoint chain (the latch
    threaded through `prev_stage`/`stabilize_passes_before` exactly as `compute_budget` would).
    """
    # Checkpoint 1: spent $1,450, projected next wave ~$1,650 (>= 80%) -> converge.
    r1 = _derive(
        spent=1450.0, est_unit_cost_usd=20.0, est_ckpt_cost_usd=20.0, prev_stage="explore", k=1
    )
    assert r1.stage_raw == "explore"
    assert r1.stage == "converge"
    assert r1.must_close is False

    # Checkpoint 2: spent $1,760, projected ~$1,860 (>= 90%) -> stabilize (latched from converge).
    r2 = _derive(
        spent=1760.0, est_unit_cost_usd=10.0, est_ckpt_cost_usd=10.0, prev_stage=r1.stage, k=2
    )
    assert r2.stage == "stabilize"
    assert r2.must_close is False

    # Checkpoint 3: spent $1,905 (>= 95% alone) -> closeout, must_close.
    stabilize_passes_before = 0 + (1 if r2.stage == "stabilize" else 0)
    r3 = _derive(
        spent=1905.0,
        est_unit_cost_usd=20.0,
        est_ckpt_cost_usd=20.0,
        prev_stage=r2.stage,
        k=3,
        stabilize_passes_before=stabilize_passes_before,
    )
    assert r3.stage_raw == "closeout"
    assert r3.stage == "closeout"
    assert r3.must_close is True
    assert any("stage=closeout" in reason for reason in r3.must_close_reasons)


def test_ac1b_no_settled_units_uses_defaults() -> None:
    assert ov.median_or_default([], default=8.0) == 8.0
    assert (
        ov.time_cap_from_median_duration(None, wave_max_minutes=90.0) == ov.DEFAULT_TIME_CAP_TASKS
    )


def test_ac1c_zero_settled_costs_are_used_not_defaults() -> None:
    assert ov.median_or_default([0.0, 0.0, 0.0], default=8.0) == 0.0
    # A zero median duration also means "no time constraint" (division-by-zero avoided),
    # distinct from the true no-data case above, but likewise not artificially starved.
    assert ov.time_cap_from_median_duration(0.0, wave_max_minutes=90.0) == ov.DEFAULT_TIME_CAP_TASKS


def test_ac1d_projection_crosses_threshold_while_spend_has_not() -> None:
    result = _derive(
        spent=1500.0, est_unit_cost_usd=20.0, est_ckpt_cost_usd=20.0, prev_stage="explore"
    )
    assert result.stage_raw == "explore"  # 75% alone stays under converge_pct=80
    assert result.stage_projected == "converge"  # but the projection crosses 80%
    assert result.stage == "converge"


def test_ac1e_latch_never_de_escalates_on_a_lower_later_spend() -> None:
    result = _derive(
        spent=100.0,
        est_unit_cost_usd=1.0,
        est_ckpt_cost_usd=1.0,
        prev_stage="converge",
    )
    # Spent alone (5%) and the projection are both "explore" -- but the latch keeps "converge".
    assert result.stage_raw == "explore"
    assert result.stage_projected == "explore"
    assert result.stage == "converge"


def test_ac1f_effective_budget_honors_and_records_once(tmp_path: Path) -> None:
    inst = tmp_path / "instance"
    write_config(inst)
    cfg = ov.load_config(inst)
    (inst / "control").mkdir(parents=True, exist_ok=True)
    (inst / "control" / "budget-override.json").write_text(
        json.dumps({"run_budget_usd": 3000, "reason": "operator asked for more runway"})
    )
    state = ov.State(
        tasks={},
        injected_ids=[],
        breaker_overrides={"run-budget-backstop": 3000},
        spent=300.0,
        status="running",
    )
    now = ov.parse_now("2026-01-01T00:00:00+00:00")

    budget_usd, info = ov.effective_budget(cfg, state, inst, now, record=True)
    assert budget_usd == 3000.0
    assert info["honored"] is True
    assert info["newly_honored"] is True
    ledger_lines = ov.read_ledger_lines(inst)
    assert [line for line in ledger_lines if line.get("event") == "budget_override"] != []

    # Idempotent: honoring the SAME override again does not re-append (FR-16).
    _budget_usd2, info2 = ov.effective_budget(cfg, state, inst, now, record=True)
    assert info2["newly_honored"] is False
    assert (
        len([line for line in ov.read_ledger_lines(inst) if line.get("event") == "budget_override"])
        == 1
    )


def test_ac1f_honored_override_unlatches(tmp_path: Path) -> None:
    inst = tmp_path / "instance"
    write_config(inst)
    cfg = ov.load_config(inst)
    (inst / "control").mkdir(parents=True, exist_ok=True)
    (inst / "control" / "budget-override.json").write_text(
        json.dumps({"run_budget_usd": 3000, "reason": "operator asked for more runway"})
    )
    state = ov.State(
        tasks={},
        injected_ids=[],
        breaker_overrides={"run-budget-backstop": 3000},
        spent=300.0,
        status="running",
    )
    now = ov.parse_now("2026-01-01T00:00:00+00:00")

    # Simulate a PRIOR checkpoint that had already latched to "stabilize" before the override
    # existed. `compute_budget` itself both honors+records the override AND derives the stage
    # in one call, so the "newly honored" moment and the un-latch happen together here.
    (ov.checkpoint_dir(inst, 1)).mkdir(parents=True, exist_ok=True)
    ov.write_json_atomic(
        ov.digest_path(inst, 1), {"budget": {"stage": "stabilize"}, "generated_at": ov.now_iso(now)}
    )
    result = ov.compute_budget(cfg, state, inst, 2, now)
    assert result["budget"]["override_applied"]["honored"] is True
    # Freshly honored this checkpoint -> the latch resets to "explore" before recomputing,
    # and 300/3000 = 10% is comfortably "explore" under the new budget.
    assert result["budget"]["stage"] == "explore"
    ov.write_json_atomic(
        ov.digest_path(inst, 2), {"budget": result["budget"], "generated_at": ov.now_iso(now)}
    )

    # A SUBSEQUENT checkpoint (override already recorded, no longer "newly honored") resumes
    # normal latching from ck-02's own recorded stage ("explore"), not a fresh un-latch.
    result2 = ov.compute_budget(cfg, state, inst, 3, now)
    assert result2["budget"]["override_applied"]["honored"] is True
    assert result2["budget"]["stage"] == "explore"


def test_ac1g_refused_override_without_matching_extension(tmp_path: Path) -> None:
    inst = tmp_path / "instance"
    write_config(inst)
    cfg = ov.load_config(inst)
    (inst / "control").mkdir(parents=True, exist_ok=True)
    (inst / "control" / "budget-override.json").write_text(
        json.dumps({"run_budget_usd": 3000, "reason": "operator asked"})
    )
    now = ov.parse_now("2026-01-01T00:00:00+00:00")

    # No --extend-breaker at all.
    state_no_ext = ov.State(
        tasks={}, injected_ids=[], breaker_overrides={}, spent=300.0, status="running"
    )
    budget_usd, info = ov.effective_budget(cfg, state_no_ext, inst, now, record=True)
    assert budget_usd == cfg.run_budget_usd
    assert info["honored"] is False
    assert ov.read_ledger_lines(inst) == []

    # An extension exists but is smaller than what was requested.
    state_low_ext = ov.State(
        tasks={},
        injected_ids=[],
        breaker_overrides={"run-budget-backstop": 2500},
        spent=300.0,
        status="running",
    )
    budget_usd2, info2 = ov.effective_budget(cfg, state_low_ext, inst, now, record=True)
    assert budget_usd2 == cfg.run_budget_usd
    assert info2["honored"] is False
    assert ov.read_ledger_lines(inst) == []


def test_ac1h_lower_override_value_may_jump_straight_to_closeout(tmp_path: Path) -> None:
    inst = tmp_path / "instance"
    write_config(inst)
    cfg = ov.load_config(inst)
    (inst / "control").mkdir(parents=True, exist_ok=True)
    (inst / "control" / "budget-override.json").write_text(
        json.dumps({"run_budget_usd": 100, "reason": "cut scope, run is nearly done"})
    )
    now = ov.parse_now("2026-01-01T00:00:00+00:00")
    # $96 spent against the ORIGINAL $2000 budget would be a trivial 4.8% (explore); against
    # the honored $100 override it is 96% -- past closeout_pct=95.
    state = ov.State(
        tasks={},
        injected_ids=[],
        breaker_overrides={"run-budget-backstop": 100},
        spent=96.0,
        status="running",
    )
    result = ov.compute_budget(cfg, state, inst, 1, now)
    assert result["budget"]["override_applied"]["honored"] is True
    assert result["budget"]["run_budget_usd"] == 100.0
    assert result["budget"]["stage"] == "closeout"
    assert result["must_close"] is True


def test_ac1i_tail_tasks_count() -> None:
    assert ov.tail_tasks_count(final_push=True) == 3
    assert ov.tail_tasks_count(final_push=False) == 2


@pytest.mark.parametrize(
    ("k", "max_waves", "stabilize_passes", "max_stabilize_passes", "expected"),
    [
        (12, 12, 0, 2, True),  # K >= max_waves
        (5, 12, 2, 2, True),  # stabilize_passes >= max_stabilize_passes
        (5, 12, 0, 2, False),  # neither condition holds (stage itself isn't closeout here)
    ],
)
def test_ac1j_must_close_at_max_waves_and_max_stabilize_passes(
    k: int, max_waves: int, stabilize_passes: int, max_stabilize_passes: int, expected: bool
) -> None:
    must_close, _reasons = ov.compute_must_close(
        stage="converge",
        k=k,
        max_waves=max_waves,
        stabilize_passes=stabilize_passes,
        max_stabilize_passes=max_stabilize_passes,
        forced_closeout=False,
    )
    assert must_close is expected


def test_ac1j_must_close_forced_closeout_event() -> None:
    must_close, reasons = ov.compute_must_close(
        stage="explore",
        k=1,
        max_waves=12,
        stabilize_passes=0,
        max_stabilize_passes=2,
        forced_closeout=True,
    )
    assert must_close is True
    assert any("forced_closeout" in reason for reason in reasons)


@pytest.mark.parametrize(
    ("stage", "expected"),
    [
        ("explore", {"continue", "redirect", "hold", "stabilize", "closeout"}),
        ("converge", {"continue", "redirect", "hold", "stabilize", "closeout"}),
        ("stabilize", {"stabilize", "closeout", "hold"}),
        ("closeout", {"closeout"}),
    ],
)
def test_ac1k_allowed_decisions_per_stage(stage: str, expected: set[str]) -> None:
    assert set(ov.allowed_decisions_for_stage(stage, must_close=False)) == expected


def test_ac1k_must_close_forces_closeout_only_regardless_of_stage() -> None:
    for stage in ("explore", "converge", "stabilize", "closeout"):
        assert ov.allowed_decisions_for_stage(stage, must_close=True) == ["closeout"]


# =============================================================================================
# ===== AC2: cadence (allowed_wave_size) ======================================================
# =============================================================================================


@pytest.mark.parametrize(
    (
        "stage",
        "wave_size",
        "stabilize_wave_size",
        "time_cap",
        "budget_cap",
        "budget_cap_100",
        "expected",
    ),
    [
        ("explore", 6, 4, 10, 3, 20, 3),  # min(wave_size, time_cap, budget_cap)
        ("explore", 6, 4, 2, 20, 20, 2),  # time_cap is the binding constraint
        ("converge", 6, 4, 20, 0, 20, 1),  # budget_cap=0 still floors at 1 (keeps the run moving)
        (
            "stabilize",
            6,
            4,
            20,
            20,
            2,
            2,
        ),  # stabilize uses stabilize_wave_size + budget_cap_to_100pct
        ("stabilize", 6, 4, 20, 20, 20, 4),  # stabilize_wave_size is the binding constraint
        ("closeout", 6, 4, 20, 20, 20, 0),  # closeout never plans new units
    ],
)
def test_ac2_allowed_wave_size_formula(
    stage: str,
    wave_size: int,
    stabilize_wave_size: int,
    time_cap: int,
    budget_cap: int,
    budget_cap_100: int,
    expected: int,
) -> None:
    assert (
        ov.compute_allowed_wave_size(
            stage, wave_size, stabilize_wave_size, time_cap, budget_cap, budget_cap_100
        )
        == expected
    )


def test_ac2_null_started_or_ended_at_excluded_from_duration() -> None:
    assert ov._duration_seconds(None, "2026-01-01T00:01:00+00:00") is None
    assert ov._duration_seconds("2026-01-01T00:00:00+00:00", None) is None
    assert ov._duration_seconds(None, None) is None
    assert ov._duration_seconds("2026-01-01T00:00:00+00:00", "2026-01-01T00:01:00+00:00") == 60.0


def test_ac2_median_duration_excludes_null_entries_in_compute_budget(tmp_path: Path) -> None:
    inst = tmp_path / "instance"
    write_config(inst)
    cfg = ov.load_config(inst)
    now = ov.parse_now("2026-01-01T00:00:00+00:00")
    state = ov.State(tasks={}, injected_ids=[], breaker_overrides={}, spent=0.0, status="running")

    # Three wave-1 unit lines: durations 10s, 30s, and a null (excluded) -- median of {10,30}=20.
    for unit_id, duration_s, cost in (
        ("w01-01-a", 10.0, 5.0),
        ("w01-02-b", 30.0, 5.0),
        ("w01-03-c", None, 5.0),
    ):
        ov.append_chained(
            inst,
            {
                "type": "unit",
                "wave": 1,
                "unit_id": unit_id,
                "cost_usd": cost,
                "duration_s": duration_s,
                "outcome": "done",
                "verdict": "pass",
            },
            now,
        )

    result = ov.compute_budget(cfg, state, inst, 1, now)
    assert result["cadence"]["median_unit_duration_s"] == 20.0


# =============================================================================================
# ===== AC7: config validation (CFG-0/1/3) ====================================================
# =============================================================================================


def test_ac7_cfg1_bad_percent_ordering(tmp_path: Path) -> None:
    inst = tmp_path / "instance"
    write_config(inst, converge_pct=90, stabilize_pct=80, closeout_pct=95)
    with pytest.raises(ov.Violation) as excinfo:
        ov.load_config(inst)
    assert excinfo.value.rule_id == "CFG-1"


def test_ac7_cfg3_max_injected_tasks_below_need_includes_number(tmp_path: Path) -> None:
    inst = tmp_path / "instance"
    # need = 12*(6+1+0*(4+1))+3 = 87; set max_injected_tasks below that.
    write_config(inst, max_injected_tasks=10)
    with pytest.raises(ov.Violation) as excinfo:
        ov.load_config(inst)
    assert excinfo.value.rule_id == "CFG-3"
    assert "87" in str(excinfo.value)


def test_ac7_cfg0_malformed_json(tmp_path: Path) -> None:
    inst = tmp_path / "instance"
    inst.mkdir(parents=True, exist_ok=True)
    (inst / "overseer-config.json").write_text("{not valid json")
    with pytest.raises(ov.Violation) as excinfo:
        ov.load_config(inst)
    assert excinfo.value.rule_id == "CFG-0"


def test_ac7_cfg0_non_numeric_field(tmp_path: Path) -> None:
    inst = tmp_path / "instance"
    write_config(inst, wave_size="not-a-number")
    with pytest.raises(ov.Violation) as excinfo:
        ov.load_config(inst)
    assert excinfo.value.rule_id == "CFG-0"


def test_ac7_cfg0_missing_file(tmp_path: Path) -> None:
    inst = tmp_path / "instance"
    inst.mkdir(parents=True, exist_ok=True)
    with pytest.raises(ov.Violation) as excinfo:
        ov.load_config(inst)
    assert excinfo.value.rule_id == "CFG-0"


def test_load_config_happy_path_reads_every_field(tmp_path: Path) -> None:
    inst = tmp_path / "instance"
    write_config(inst)
    cfg = ov.load_config(inst)
    assert cfg.converge_pct == 80
    assert cfg.wave_size == 6
    assert cfg.run_budget_usd == 2000
    assert cfg.final_push is True
    assert cfg.runs_root == ".orchestrator/runs"


# =============================================================================================
# ===== AC8: state.json field contract ========================================================
# =============================================================================================


def test_ac8_state_contract_matches_real_run_state(tmp_path: Path) -> None:
    rs = RunState(
        run_id="run-1",
        workflow_id="wf-1",
        repo_set="main",
        started_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:05:00+00:00",
        status="running",
        tasks={
            "w01-01-foo": TaskRunState(
                status="succeeded",
                started_at="2026-01-01T00:00:00+00:00",
                ended_at="2026-01-01T00:01:00+00:00",
                cumulative_cost_usd=12.5,
            ),
            "w01-02-bar": TaskRunState(status="failed", cumulative_cost_usd=3.25),
            "w01-03-pending": TaskRunState(status="pending"),
        },
    )
    state_path = tmp_path / "state.json"
    state_path.write_text(rs.model_dump_json())

    loaded = ov.load_state(state_path)

    assert loaded.spent == pytest.approx(compute_run_usage_totals(rs).cost_usd)
    assert loaded.spent == pytest.approx(15.75)
    assert loaded.tasks["w01-01-foo"].status == "succeeded"
    assert loaded.tasks["w01-01-foo"].cumulative_cost_usd == pytest.approx(12.5)
    assert loaded.status == "running"


def test_load_state_missing_file_is_st1(tmp_path: Path) -> None:
    with pytest.raises(ov.Violation) as excinfo:
        ov.load_state(tmp_path / "does-not-exist" / "state.json")
    assert excinfo.value.rule_id == "ST-1"


def test_load_state_missing_status_field_is_st2(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"tasks": {"w01-01-a": {"attempts": 1}}}))
    with pytest.raises(ov.Violation) as excinfo:
        ov.load_state(path)
    assert excinfo.value.rule_id == "ST-2"


def test_load_state_missing_tasks_field_is_st2(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"status": "running"}))
    with pytest.raises(ov.Violation) as excinfo:
        ov.load_state(path)
    assert excinfo.value.rule_id == "ST-2"


def test_budget_cap_for_threshold_descends_to_largest_fitting_n() -> None:
    # spent=0, est_unit=10, est_ckpt=0, reserve=0, budget=100, threshold=50% -> limit=50 -> n<=5
    assert (
        ov.budget_cap_for_threshold(
            spent=0.0,
            est_unit_cost_usd=10.0,
            est_ckpt_cost_usd=0.0,
            reserve_tail_usd=0.0,
            run_budget_usd=100.0,
            threshold_pct=50.0,
            wave_size=10,
        )
        == 5
    )


def test_time_cap_from_median_duration_floors_division() -> None:
    # 90 minutes = 5400s; each unit takes 700s -> floor(5400/700) = 7
    assert ov.time_cap_from_median_duration(700.0, wave_max_minutes=90.0) == 7
    assert math.isfinite(ov.time_cap_from_median_duration(700.0, wave_max_minutes=90.0))


# =============================================================================================
# ===== Extra coverage: bounded IO, path confinement, clock, config/state edge branches ======
# =============================================================================================


def test_read_json_bounded_oversize_raises(tmp_path: Path) -> None:
    path = tmp_path / "big.json"
    path.write_text(json.dumps({"padding": "x" * 100}))
    with pytest.raises(ov.OversizeInputError):
        ov.read_json_bounded(path, max_bytes=10)


def test_read_json_bounded_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        ov.read_json_bounded(tmp_path / "nope.json", max_bytes=1024)


def test_write_json_atomic_cleans_up_temp_file_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "out.json"

    def _boom(_src: str, _dst: str) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(ov.os, "replace", _boom)
    with pytest.raises(OSError):
        ov.write_json_atomic(path, {"a": 1})
    assert not path.exists()
    # No leftover .tmp-* file either.
    assert list(tmp_path.iterdir()) == []


def test_confine_workspace_rejects_escaping_instance_dir(tmp_path: Path) -> None:
    with pytest.raises(ov.ToolError):
        ov.confine_workspace(str(tmp_path), "../outside")


def test_confine_workspace_accepts_nested_instance_dir(tmp_path: Path) -> None:
    ws, inst = ov.confine_workspace(str(tmp_path), "workflows/o-1/instance")
    assert inst == (ws / "workflows" / "o-1" / "instance")


def test_parse_now_accepts_naive_iso_and_assumes_utc() -> None:
    dt = ov.parse_now("2026-01-01T00:00:00")
    assert dt.tzinfo is not None
    assert ov.now_iso(dt) == "2026-01-01T00:00:00+00:00"


def test_read_hook_context_requires_env_var_without_dry_run() -> None:
    args = argparse.Namespace(dry_run=False, task_id=None)
    with pytest.raises(ov.ToolError):
        ov.read_hook_context(args)


def test_read_hook_context_missing_run_id_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx_path = tmp_path / "context.json"
    ctx_path.write_text(json.dumps({"task_id": "ck-01"}))
    monkeypatch.setenv("AO_HOOK_CONTEXT_PATH", str(ctx_path))
    args = argparse.Namespace(dry_run=False, task_id=None)
    with pytest.raises(ov.ToolError):
        ov.read_hook_context(args)


def test_read_hook_context_dry_run_task_id_overrides_context_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx_path = tmp_path / "context.json"
    ctx_path.write_text(json.dumps({"run_id": "run-1", "task_id": "ck-01"}))
    monkeypatch.setenv("AO_HOOK_CONTEXT_PATH", str(ctx_path))
    args = argparse.Namespace(dry_run=True, task_id="ck-02")
    ctx = ov.read_hook_context(args)
    assert ctx.task_id == "ck-02"
    assert ctx.run_id == "run-1"


def test_read_hook_context_dry_run_without_context_file_needs_task_id() -> None:
    args = argparse.Namespace(dry_run=True, task_id=None)
    with pytest.raises(ov.ToolError):
        ov.read_hook_context(args)


def test_require_number_rejects_bool_and_other_types() -> None:
    with pytest.raises(ov.Violation) as excinfo:
        ov._require_number({"x": True}, "x")
    assert excinfo.value.rule_id == "CFG-0"
    with pytest.raises(ov.Violation) as excinfo2:
        ov._require_number({"x": [1, 2]}, "x")
    assert excinfo2.value.rule_id == "CFG-0"


def test_load_config_oversize_is_cfg0(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    inst = tmp_path / "instance"
    write_config(inst)
    monkeypatch.setattr(ov, "JSON_MAX_BYTES", 10)
    with pytest.raises(ov.Violation) as excinfo:
        ov.load_config(inst)
    assert excinfo.value.rule_id == "CFG-0"


def test_load_config_non_dict_root_is_cfg0(tmp_path: Path) -> None:
    inst = tmp_path / "instance"
    inst.mkdir(parents=True, exist_ok=True)
    (inst / "overseer-config.json").write_text(json.dumps([1, 2, 3]))
    with pytest.raises(ov.Violation) as excinfo:
        ov.load_config(inst)
    assert excinfo.value.rule_id == "CFG-0"


def test_ac7_cfg2_non_positive_sizes(tmp_path: Path) -> None:
    inst = tmp_path / "instance"
    write_config(inst, wave_size=0)
    with pytest.raises(ov.Violation) as excinfo:
        ov.load_config(inst)
    assert excinfo.value.rule_id == "CFG-2"


def test_load_state_oversize_is_st2(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"status": "running", "tasks": {}}))
    monkeypatch.setattr(ov, "STATE_MAX_BYTES", 5)
    with pytest.raises(ov.Violation) as excinfo:
        ov.load_state(path)
    assert excinfo.value.rule_id == "ST-2"


def test_load_state_malformed_json_is_st2(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text("{not valid")
    with pytest.raises(ov.Violation) as excinfo:
        ov.load_state(path)
    assert excinfo.value.rule_id == "ST-2"


def test_load_state_non_dict_root_is_st2(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text(json.dumps([1, 2, 3]))
    with pytest.raises(ov.Violation) as excinfo:
        ov.load_state(path)
    assert excinfo.value.rule_id == "ST-2"


def test_load_state_null_cost_defaults_to_zero(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text(
        json.dumps(
            {
                "status": "running",
                "tasks": {"a": {"status": "succeeded", "cumulative_cost_usd": None}},
            }
        )
    )
    state = ov.load_state(path)
    assert state.tasks["a"].cumulative_cost_usd == 0.0


def test_load_state_non_numeric_cost_is_st2(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text(
        json.dumps(
            {
                "status": "running",
                "tasks": {"a": {"status": "succeeded", "cumulative_cost_usd": "lots"}},
            }
        )
    )
    with pytest.raises(ov.Violation) as excinfo:
        ov.load_state(path)
    assert excinfo.value.rule_id == "ST-2"


def test_load_state_injected_tasks_not_a_list_is_st2(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"status": "running", "tasks": {}, "injected_tasks": "nope"}))
    with pytest.raises(ov.Violation) as excinfo:
        ov.load_state(path)
    assert excinfo.value.rule_id == "ST-2"


def test_load_state_injected_task_missing_id_is_st2(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text(
        json.dumps({"status": "running", "tasks": {}, "injected_tasks": [{"not_id": 1}]})
    )
    with pytest.raises(ov.Violation) as excinfo:
        ov.load_state(path)
    assert excinfo.value.rule_id == "ST-2"


def test_load_state_breaker_overrides_not_a_dict_is_st2(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"status": "running", "tasks": {}, "breaker_overrides": [1, 2]}))
    with pytest.raises(ov.Violation) as excinfo:
        ov.load_state(path)
    assert excinfo.value.rule_id == "ST-2"


def test_effective_budget_malformed_override_file_is_refused(tmp_path: Path) -> None:
    inst = tmp_path / "instance"
    write_config(inst)
    cfg = ov.load_config(inst)
    (inst / "control").mkdir(parents=True, exist_ok=True)
    (inst / "control" / "budget-override.json").write_text("{not valid")
    now = ov.parse_now("2026-01-01T00:00:00+00:00")
    state = ov.State(tasks={}, injected_ids=[], breaker_overrides={}, spent=0.0, status="running")

    budget_usd, info = ov.effective_budget(cfg, state, inst, now)
    assert budget_usd == cfg.run_budget_usd
    assert info["honored"] is False


def test_effective_budget_non_dict_override_root_is_refused(tmp_path: Path) -> None:
    inst = tmp_path / "instance"
    write_config(inst)
    cfg = ov.load_config(inst)
    (inst / "control").mkdir(parents=True, exist_ok=True)
    (inst / "control" / "budget-override.json").write_text(json.dumps([1, 2, 3]))
    now = ov.parse_now("2026-01-01T00:00:00+00:00")
    state = ov.State(tasks={}, injected_ids=[], breaker_overrides={}, spent=0.0, status="running")

    budget_usd, info = ov.effective_budget(cfg, state, inst, now)
    assert budget_usd == cfg.run_budget_usd
    assert info["honored"] is False


def test_effective_budget_non_numeric_requested_amount_is_refused(tmp_path: Path) -> None:
    inst = tmp_path / "instance"
    write_config(inst)
    cfg = ov.load_config(inst)
    (inst / "control").mkdir(parents=True, exist_ok=True)
    (inst / "control" / "budget-override.json").write_text(
        json.dumps({"run_budget_usd": "lots", "reason": "x"})
    )
    now = ov.parse_now("2026-01-01T00:00:00+00:00")
    state = ov.State(
        tasks={},
        injected_ids=[],
        breaker_overrides={"run-budget-backstop": 999999},
        spent=0.0,
        status="running",
    )

    budget_usd, info = ov.effective_budget(cfg, state, inst, now)
    assert budget_usd == cfg.run_budget_usd
    assert info["honored"] is False


def test_read_prev_stage_and_count_prior_stabilize_tolerate_malformed_digests(
    tmp_path: Path,
) -> None:
    inst = tmp_path / "instance"
    ov.checkpoint_dir(inst, 1).mkdir(parents=True, exist_ok=True)
    # A malformed prior digest (not even an object) must not crash the reader -- treated the
    # same as "absent" (fail-soft on OWN prior output, not an agent-authored contract input).
    ov.write_json_atomic(ov.digest_path(inst, 1), [1, 2, 3])

    assert ov.read_prev_stage(inst, 2) == "explore"
    assert ov.count_prior_stabilize_passes(inst, 2) == 0
