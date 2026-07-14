"""Schema + pydantic model tests for the routing/circuit-breaker foundation
(T-b7q2m4, epic E-rc7k2v). Types only — no engine/control-flow behaviour is
exercised here; that lands in downstream tickets.

Covers TASK.md acceptance criteria 1-4:
  1. workflow.schema.json accepts branches/circuit_breakers/join, still rejects
     unknown keys, and enforces conditional `required` per breaker condition.
  2. WorkflowSpec parses the new blocks; TaskSpec.join defaults "all"; new
     RunState/TaskRunState fields default.
  3. An old state.json (pre-dating this change) deserialises with defaults (NFR-5).
  4. TaskStatus includes "not_taken"; TaskRunState(status="not_taken") round-trips.
"""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from agent_orchestrator.models import (
    CircuitBreakerSpec,
    RouterSpec,
    RouteSpec,
    RunState,
    TaskRunState,
    TaskSpec,
    TrippedBreaker,
    WorkflowSpec,
)
from agent_orchestrator.spec import load_workflow

_SCHEMA_PATH = Path(__file__).parent.parent / "specs" / "workflow.schema.json"
_FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _schema() -> dict:
    return json.loads(_SCHEMA_PATH.read_text())


def _base_workflow_dict() -> dict:
    """A minimal workflow dict with a router + a threshold breaker + a join."""
    return {
        "version": "1.0",
        "id": "route-demo",
        "repo_set": "rs",
        "tasks": [
            {
                "id": "classify",
                "agent": "ag",
                "instruction": "i.md",
                "outputs": ["out/verdict.json"],
            },
            {"id": "bug-fix", "agent": "ag", "instruction": "i.md", "depends_on": ["classify"]},
            {"id": "epic-plan", "agent": "ag", "instruction": "i.md", "depends_on": ["classify"]},
            {
                "id": "converge",
                "agent": "ag",
                "instruction": "i.md",
                "depends_on": ["bug-fix", "epic-plan"],
                "join": "any",
            },
        ],
        "branches": [
            {
                "id": "classify-router",
                "router_task_id": "classify",
                "verdict_path": "out/verdict.json",
                "routes": {
                    "bug": {"entry": ["bug-fix"]},
                    "epic": {"entry": ["epic-plan"]},
                },
            }
        ],
        "circuit_breakers": [
            {
                "id": "too-many-fails",
                "condition": "task_failures",
                "action": "stop",
                "threshold": 3,
            },
        ],
    }


class TestSchemaAcceptsRoutingAndBreakers:
    """AC1: schema accepts branches/circuit_breakers/join; still rejects unknown
    keys; conditional `required` per breaker condition is enforced."""

    def test_valid_workflow_with_branches_breakers_join_validates(self) -> None:
        jsonschema.validate(_base_workflow_dict(), _schema())

    def test_unknown_top_level_key_still_rejected(self) -> None:
        data = dict(_base_workflow_dict(), unknown_key=True)
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(data, _schema())

    def test_unknown_router_key_rejected(self) -> None:
        data = _base_workflow_dict()
        data["branches"][0]["extra"] = "nope"
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(data, _schema())

    def test_unknown_circuit_breaker_key_rejected(self) -> None:
        data = _base_workflow_dict()
        data["circuit_breakers"][0]["extra"] = "nope"
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(data, _schema())

    @pytest.mark.parametrize(
        "breaker",
        [
            # missing threshold:
            {"id": "b", "condition": "task_failures", "action": "stop"},
            {"id": "b", "condition": "consecutive_failures", "action": "stop"},
            {"id": "b", "condition": "run_wall_clock_seconds", "action": "stop"},
            {"id": "b", "condition": "run_active_seconds", "action": "stop"},
            {"id": "b", "condition": "injected_task_count", "action": "stop"},
            {"id": "b", "condition": "task_cost_usd", "action": "stop"},
            {"id": "b", "condition": "run_cost_usd", "action": "stop"},
            # missing task_id + verdict_path, then missing verdict_path only:
            {"id": "b", "condition": "verdict", "action": "fail"},
            {"id": "b", "condition": "verdict", "action": "fail", "task_id": "classify"},
            # missing path:
            {"id": "b", "condition": "stop_file", "action": "stop"},
        ],
    )
    def test_conditional_required_fields_enforced(self, breaker: dict) -> None:
        data = _base_workflow_dict()
        data["circuit_breakers"] = [breaker]
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(data, _schema())

    def test_run_active_seconds_breaker_with_threshold_validates(self) -> None:
        """E-3JTmVu FR-1: run_active_seconds accepted by the schema with a threshold."""
        data = _base_workflow_dict()
        data["circuit_breakers"] = [
            {
                "id": "active-cap",
                "condition": "run_active_seconds",
                "action": "stop",
                "threshold": 3600,
            },
        ]
        jsonschema.validate(data, _schema())

    @pytest.mark.parametrize("condition", ["task_cost_usd", "run_cost_usd"])
    def test_actual_cost_breaker_with_fractional_threshold_validates(self, condition: str) -> None:
        """E-9h3m7k FR-4: threshold widened to number, accepts fractional USD amounts."""
        data = _base_workflow_dict()
        data["circuit_breakers"] = [
            {"id": "cost-cap", "condition": condition, "action": "fail", "threshold": 2.5},
        ]
        jsonschema.validate(data, _schema())

    def test_threshold_zero_still_rejected(self) -> None:
        """exclusiveMinimum 0 — a zero threshold (would trip immediately) is invalid."""
        data = _base_workflow_dict()
        data["circuit_breakers"] = [
            {"id": "b", "condition": "run_cost_usd", "action": "fail", "threshold": 0},
        ]
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(data, _schema())

    def test_verdict_breaker_with_required_fields_validates(self) -> None:
        data = _base_workflow_dict()
        data["circuit_breakers"] = [
            {
                "id": "verdict-halt",
                "condition": "verdict",
                "action": "fail",
                "task_id": "classify",
                "verdict_path": "out/verdict.json",
            }
        ]
        jsonschema.validate(data, _schema())

    def test_stop_file_breaker_with_path_validates(self) -> None:
        data = _base_workflow_dict()
        data["circuit_breakers"] = [
            {"id": "stopper", "condition": "stop_file", "action": "stop", "path": "control/stop"}
        ]
        jsonschema.validate(data, _schema())

    def test_non_mvp_condition_names_still_accepted_by_schema(self) -> None:
        """Full §6 catalog is enumerated in schema even though only 6 are wired (§7)."""
        data = _base_workflow_dict()
        data["circuit_breakers"] = [
            {"id": "ratio", "condition": "failure_ratio", "action": "stop"},
        ]
        jsonschema.validate(data, _schema())

    def test_branches_and_circuit_breakers_default_to_empty(self) -> None:
        data = _base_workflow_dict()
        del data["branches"]
        del data["circuit_breakers"]
        del data["tasks"][3]["join"]
        jsonschema.validate(data, _schema())


class TestWorkflowSpecParsesRoutingAndBreakers:
    """AC2: WorkflowSpec parses new blocks; TaskSpec.join defaults "all"; new
    RunState/TaskRunState fields all have defaults."""

    def test_load_workflow_parses_branches_and_circuit_breakers(self, tmp_path: Path) -> None:
        spec_path = tmp_path / "workflow.json"
        spec_path.write_text(json.dumps(_base_workflow_dict()))

        wf = load_workflow(spec_path)

        assert len(wf.branches) == 1
        router = wf.branches[0]
        assert isinstance(router, RouterSpec)
        assert router.id == "classify-router"
        assert router.router_task_id == "classify"
        assert router.verdict_path == "out/verdict.json"
        assert router.verdict_field == "routes"  # default
        assert router.default_route is None  # default
        assert set(router.routes) == {"bug", "epic"}
        assert isinstance(router.routes["bug"], RouteSpec)
        assert router.routes["bug"].entry == ["bug-fix"]

        assert len(wf.circuit_breakers) == 1
        breaker = wf.circuit_breakers[0]
        assert isinstance(breaker, CircuitBreakerSpec)
        assert breaker.condition == "task_failures"
        assert breaker.action == "stop"
        assert breaker.threshold == 3
        assert breaker.scope == "run"  # default
        assert breaker.window == "run"  # default
        assert breaker.field == "halt"  # default

        assert wf.task("converge").join == "any"
        assert wf.task("bug-fix").join == "all"  # default

    def test_task_join_defaults_to_all(self) -> None:
        task = TaskSpec(id="t", agent="ag", instruction="i.md")
        assert task.join == "all"

    def test_workflow_spec_branches_and_circuit_breakers_default_empty(self) -> None:
        wf = WorkflowSpec(
            version="1.0",
            id="wf",
            repo_set="rs",
            tasks=[TaskSpec(id="t", agent="ag", instruction="i.md")],
        )
        assert wf.branches == []
        assert wf.circuit_breakers == []

    def test_run_state_route_decisions_and_tripped_breakers_default_empty(self) -> None:
        state = RunState(
            run_id="r1",
            workflow_id="wf",
            repo_set="rs",
            started_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
        )
        assert state.route_decisions == {}
        assert state.tripped_breakers == []

    def test_task_run_state_route_and_not_taken_reason_default_none(self) -> None:
        ts = TaskRunState()
        assert ts.route is None
        assert ts.not_taken_reason is None

    def test_tripped_breaker_round_trips(self) -> None:
        tb = TrippedBreaker(
            id="too-many-fails",
            condition="task_failures",
            action="stop",
            at="2026-01-01T00:00:00+00:00",
            detail={"failures": 3, "threshold": 3},
        )
        loaded = TrippedBreaker.model_validate_json(tb.model_dump_json())
        assert loaded == tb


class TestBackwardCompatOldStateJson:
    """AC3 (NFR-5): a state.json written before this change deserialises with
    defaults — regression test loading a fixture lacking route_decisions and
    tripped_breakers (and the new per-task route/not_taken_reason fields)."""

    def test_old_state_json_fixture_loads_with_new_field_defaults(self) -> None:
        fixture_path = _FIXTURES_DIR / "state_pre_routing_breakers.json"
        raw = fixture_path.read_text()
        assert "route_decisions" not in raw
        assert "tripped_breakers" not in raw
        assert "not_taken_reason" not in raw

        state = RunState.model_validate_json(raw)

        # Run-level new fields default.
        assert state.route_decisions == {}
        assert state.tripped_breakers == []
        # Pre-existing fields still load correctly.
        assert state.run_id == "test-wf-20260101T000000Z"
        assert state.status == "succeeded"
        assert set(state.tasks) == {"a", "b"}
        assert state.tasks["a"].status == "succeeded"

        # Per-task new fields default.
        for ts in state.tasks.values():
            assert ts.route is None
            assert ts.not_taken_reason is None

    def test_old_state_json_fixture_round_trips_after_load(self) -> None:
        """Loading then re-saving an old state.json must not error and must
        carry the new fields forward with their defaults."""
        fixture_path = _FIXTURES_DIR / "state_pre_routing_breakers.json"
        state = RunState.model_validate_json(fixture_path.read_text())
        reloaded = RunState.model_validate_json(state.model_dump_json())
        assert reloaded == state


class TestNotTakenStatus:
    """AC4: TaskStatus includes not_taken; round-trips through
    model_dump_json/model_validate_json."""

    def test_not_taken_is_a_valid_task_run_state_status(self) -> None:
        ts = TaskRunState(
            status="not_taken",
            not_taken_reason="router=classify route=bug not selected",
        )
        assert ts.status == "not_taken"

    def test_not_taken_round_trips_through_json(self) -> None:
        ts = TaskRunState(
            status="not_taken",
            route="classify-router:epic",
            not_taken_reason="router=classify-router route=bug not selected",
        )
        dumped = ts.model_dump_json()
        loaded = TaskRunState.model_validate_json(dumped)
        assert loaded == ts
        assert loaded.status == "not_taken"

    def test_not_taken_task_in_run_state_round_trips(self) -> None:
        state = RunState(
            run_id="r1",
            workflow_id="wf",
            repo_set="rs",
            started_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
            tasks={"skipped-route-task": TaskRunState(status="not_taken")},
            route_decisions={"classify-router": ["epic"]},
        )
        loaded = RunState.model_validate_json(state.model_dump_json())
        assert loaded.tasks["skipped-route-task"].status == "not_taken"
        assert loaded.route_decisions == {"classify-router": ["epic"]}
