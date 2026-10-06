"""T-28J9oR AC-1..AC-5: spec fields, record model, currency predicate (U-M1..U-M5)."""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest
from pydantic import BaseModel, ValidationError

from agent_orchestrator.models import (
    RESULT_CACHE_HIT,
    RESULT_CACHE_INELIGIBLE,
    RESULT_CACHE_MISS,
    RESULT_CACHE_WOULD_HIT,
    ResultCacheRecord,
    RunState,
    TaskRunState,
    TaskSpec,
    WorkflowDefaults,
    is_current_result_cache_record,
)

SCHEMA = json.loads(
    (Path(__file__).parents[2] / "specs" / "workflow.schema.json").read_text(encoding="utf-8")
)
KEY = "a" * 64
STAMP = "2026-10-05T00:00:00Z"


def _task(**extra: object) -> dict:
    return {"id": "t", "agent": "ag", "instruction": "i.md", **extra}


def _rec(**kw: object) -> ResultCacheRecord:
    base: dict = {"outcome": RESULT_CACHE_MISS, "mode": "on", "mode_source": "cli"}
    base.update({"dispatch_cycle": 1, "at": STAMP})
    base.update(kw)
    return ResultCacheRecord(**base)


def _spec(**extra: object) -> dict:
    return {
        "version": "1.0",
        "id": "wf",
        "repo_set": "rs",
        "tasks": [_task()],
        **extra,
    }


def _state(result_cache: dict[str, ResultCacheRecord] | None = None) -> RunState:
    return RunState(
        run_id="r",
        workflow_id="w",
        repo_set="rs",
        started_at=STAMP,
        updated_at=STAMP,
        result_cache=result_cache or {},
    )


class TestSpecFields:
    """U-M1."""

    @pytest.mark.parametrize("bad", ["yes", 1, 0, "true", []])
    def test_task_cache_rejects_non_bool(self, bad: object) -> None:
        with pytest.raises(ValidationError):
            TaskSpec(id="t", agent="a", instruction="i", cache=bad)  # type: ignore[arg-type]

    @pytest.mark.parametrize("bad", ["yes", 1, 0, "true"])
    def test_defaults_cache_rejects_non_bool(self, bad: object) -> None:
        with pytest.raises(ValidationError):
            WorkflowDefaults(cache=bad)  # type: ignore[arg-type]

    @pytest.mark.parametrize("ok", [True, False, None])
    def test_accepts_bool_and_none(self, ok: bool | None) -> None:
        assert TaskSpec(id="t", agent="a", instruction="i", cache=ok).cache is ok
        assert WorkflowDefaults(cache=ok).cache is ok

    def test_default_is_none(self) -> None:
        assert TaskSpec(id="t", agent="a", instruction="i").cache is None
        assert WorkflowDefaults().cache is None

    def test_schema_accepts_true_at_both_levels(self) -> None:
        spec = _spec(defaults={"cache": True})
        spec["tasks"] = [_task(cache=True)]
        jsonschema.validate(spec, SCHEMA)

    def test_schema_rejects_string_at_task_level(self) -> None:
        spec = _spec()
        spec["tasks"] = [_task(cache="yes")]
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(spec, SCHEMA)

    def test_schema_rejects_string_in_defaults(self) -> None:
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(_spec(defaults={"cache": "yes"}), SCHEMA)


class TestBackwardCompat:
    """U-M2."""

    def test_pre_epic_state_loads_with_empty_result_cache(self) -> None:
        dumped = _state().model_dump(mode="json")
        assert "result_cache" in dumped
        del dumped["result_cache"]
        assert RunState.model_validate(dumped).result_cache == {}


class TestCurrency:
    """U-M3."""

    def _ts(self, **kw: object) -> TaskRunState:
        base: dict = {"status": "succeeded", "dispatch_cycle": 1, "ended_at": "E1"}
        base.update(kw)
        return TaskRunState(**base)

    def test_false_for_missing_task_state(self) -> None:
        assert is_current_result_cache_record(_rec(), None) is False

    def test_false_for_cycle_mismatch(self) -> None:
        assert is_current_result_cache_record(_rec(dispatch_cycle=2), self._ts()) is False

    @pytest.mark.parametrize(
        "outcome", [RESULT_CACHE_MISS, RESULT_CACHE_WOULD_HIT, RESULT_CACHE_INELIGIBLE]
    )
    def test_true_for_current_non_hit(self, outcome: str) -> None:
        ts = self._ts(status="failed", ended_at=None)
        assert is_current_result_cache_record(_rec(outcome=outcome), ts) is True

    def test_hit_requires_succeeded_and_bound_ended_at(self) -> None:
        hit = _rec(outcome=RESULT_CACHE_HIT, ended_at="E1")
        assert is_current_result_cache_record(hit, self._ts()) is True
        assert is_current_result_cache_record(hit, self._ts(status="failed")) is False
        assert is_current_result_cache_record(hit, self._ts(ended_at="E2")) is False
        assert is_current_result_cache_record(hit, self._ts(ended_at=None)) is False

    def test_hit_without_binding_is_never_current(self) -> None:
        unbound = _rec(outcome=RESULT_CACHE_HIT, ended_at=None)
        assert is_current_result_cache_record(unbound, self._ts(ended_at=None)) is False


class TestBounds:
    """U-M4."""

    def test_infinite_cost_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _rec(saved_cost_usd=float("inf"))

    def test_nan_seconds_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _rec(saved_seconds=float("nan"))

    def test_long_reason_detail_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _rec(reason_detail="x" * 300)

    def test_bad_key_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _rec(key="not-a-key")
        assert _rec(key=KEY).key == KEY

    def test_old_reader_validates_new_dump(self) -> None:
        class OldRunState(BaseModel):
            run_id: str
            workflow_id: str
            repo_set: str
            started_at: str
            updated_at: str

        state = _state({"t": _rec()})
        assert OldRunState.model_validate(state.model_dump(mode="json")).run_id == "r"

    def test_runstate_has_no_extra_override(self) -> None:
        assert "extra" not in RunState.model_config


class TestDerivedFields:
    """U-M5 (ADR-0019 D35)."""

    def test_constructed_record_derives(self) -> None:
        rec = _rec(outcome=RESULT_CACHE_HIT, saved_input_tokens=7, saved_output_tokens=5)
        assert rec.hit is True
        assert rec.saved_tokens == 12
        assert _rec(outcome=RESULT_CACHE_WOULD_HIT).hit is False

    def test_loaded_json_cannot_forge_derived_fields(self) -> None:
        forged = _rec().model_dump(mode="json")
        forged.update(hit=True, saved_tokens=999, saved_input_tokens=1, saved_output_tokens=2)
        loaded = ResultCacheRecord.model_validate(forged)
        assert loaded.hit is False
        assert loaded.saved_tokens == 3

    def test_dump_contains_derived_fields(self) -> None:
        dumped = _rec(outcome=RESULT_CACHE_HIT).model_dump()
        assert dumped["hit"] is True
        assert "saved_tokens" in dumped

    def test_state_roundtrip_keeps_derivation(self) -> None:
        state = _state({"t": _rec(outcome=RESULT_CACHE_HIT, saved_input_tokens=2)})
        back = RunState.model_validate_json(state.model_dump_json())
        assert back.result_cache["t"].hit is True
        assert back.result_cache["t"].saved_tokens == 2
