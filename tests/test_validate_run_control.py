"""Tests for routing + circuit-breaker static validation (T-w6p2c8, epic E-rc7k2v).

Covers LLD §4.4 rules 1-11 (docs-md/lld-run-control-routing-breakers.md). Most
rules are exercised directly against `validate_run_control(workflow, graph)`
(mirrors tests/test_route_cones.py's conventions) for speed and precision.
Rule 5 (R2 -- the most important rule) and rule 8 (unreachable endpoint) are
ALSO driven through a real CliRunner `ao validate` invocation end to end
(memory `engine-api-tests-dont-cover-cli`: engine/spec-level tests alone don't
prove the CLI wiring in cli._load_all actually runs this validation).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

import pytest
from typer.testing import CliRunner

from agent_orchestrator.breakers import BREAKER_REGISTRY
from agent_orchestrator.cli import app
from agent_orchestrator.dag import build_dag
from agent_orchestrator.errors import SpecValidationError
from agent_orchestrator.models import (
    CircuitBreakerSpec,
    RouterSpec,
    RouteSpec,
    TaskSpec,
    WorkflowSpec,
)
from agent_orchestrator.spec import validate_run_control

runner = CliRunner()


# ---------------------------------------------------------------------------
# Unit-level helpers (mirrors tests/test_route_cones.py's _workflow/_task style)
# ---------------------------------------------------------------------------


def _task(
    tid: str,
    depends_on: list[str] | None = None,
    inputs: list[str] | None = None,
    outputs: list[str] | None = None,
    join: Literal["all", "any"] = "all",
) -> TaskSpec:
    return TaskSpec(
        id=tid,
        agent="agent",
        instruction="instr.md",
        depends_on=depends_on or [],
        inputs=inputs or [],
        outputs=outputs or [],
        join=join,
    )


def _workflow(
    tasks: list[TaskSpec],
    branches: list[RouterSpec] | None = None,
    circuit_breakers: list[CircuitBreakerSpec] | None = None,
    wf_id: str = "test-wf",
) -> WorkflowSpec:
    return WorkflowSpec(
        version="1.0",
        id=wf_id,
        repo_set="rs",
        tasks=tasks,
        branches=branches or [],
        circuit_breakers=circuit_breakers or [],
    )


def _validate(workflow: WorkflowSpec) -> list[str]:
    """Build the DAG then run validate_run_control -- the exact sequence
    cli._load_all uses (build_dag -> topological_order -> validate_run_control)."""
    graph = build_dag(workflow)
    graph.topological_order()  # surfaces CycleError, matching cli._load_all
    return validate_run_control(workflow, graph)


# ---------------------------------------------------------------------------
# CliRunner helper (mirrors tests/test_cli.py's _write_workflow/_write_reposets)
# ---------------------------------------------------------------------------


def _write_cli_specs(tmp_path: Path, workflow: dict) -> tuple[Path, Path, Path]:
    wf = tmp_path / "workflow.json"
    wf.write_text(json.dumps(workflow))
    rs = tmp_path / "reposets.json"
    rs.write_text(
        json.dumps(
            {
                "version": "1.0",
                "repo_sets": {
                    "default-set": {
                        "workspace_root": str(tmp_path),
                        "repos": [{"id": "core", "path": ".", "role": "primary"}],
                    }
                },
            }
        )
    )
    ag = tmp_path / "agents.json"
    ag.write_text(json.dumps({"version": "1.0", "agents": {"ag": {"executor": "fake"}}}))
    return wf, rs, ag


# ---------------------------------------------------------------------------
# Rule 1: unknown router task
# ---------------------------------------------------------------------------


class TestRule1UnknownRouterTask:
    def test_unknown_router_task_id_rejected(self) -> None:
        tasks = [_task("a")]
        router = RouterSpec(
            id="r",
            router_task_id="nonexistent",
            verdict_path="out/v.json",
            routes={"x": RouteSpec(entry=["a"]), "y": RouteSpec(entry=["a"])},
        )
        workflow = _workflow(tasks, branches=[router])

        with pytest.raises(SpecValidationError) as exc_info:
            _validate(workflow)
        assert "nonexistent" in str(exc_info.value)
        assert "r" in exc_info.value.path


# ---------------------------------------------------------------------------
# Rule 2: unknown entry
# ---------------------------------------------------------------------------


class TestRule2UnknownEntry:
    def test_unknown_entry_rejected(self) -> None:
        tasks = [_task("head"), _task("real", depends_on=["head"])]
        router = RouterSpec(
            id="r",
            router_task_id="head",
            verdict_path="out/v.json",
            routes={"x": RouteSpec(entry=["real"]), "y": RouteSpec(entry=["ghost"])},
        )
        workflow = _workflow(tasks, branches=[router])

        with pytest.raises(SpecValidationError) as exc_info:
            _validate(workflow)
        assert "ghost" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Rule 3: entry must be downstream of the router
# ---------------------------------------------------------------------------


class TestRule3EntryNotDownstreamOfRouter:
    def test_entry_not_reachable_from_router_rejected(self) -> None:
        tasks = [
            _task("head"),
            _task("real", depends_on=["head"]),
            _task("orphan"),  # independent -- not downstream of head at all
        ]
        router = RouterSpec(
            id="r",
            router_task_id="head",
            verdict_path="out/v.json",
            routes={"x": RouteSpec(entry=["real"]), "y": RouteSpec(entry=["orphan"])},
        )
        workflow = _workflow(tasks, branches=[router])

        with pytest.raises(SpecValidationError) as exc_info:
            _validate(workflow)
        msg = str(exc_info.value)
        assert "orphan" in msg
        assert "head" in msg


# ---------------------------------------------------------------------------
# Rule 4: route-entry disjointness
# ---------------------------------------------------------------------------


class TestRule4RouteEntryDisjointness:
    def test_entry_reachable_from_other_route_rejected(self) -> None:
        tasks = [
            _task("head"),
            _task("a1", depends_on=["head"]),
            _task("b1", depends_on=["a1"]),  # downstream of a1 -- not disjoint from route "a"
        ]
        router = RouterSpec(
            id="r",
            router_task_id="head",
            verdict_path="out/v.json",
            routes={"a": RouteSpec(entry=["a1"]), "b": RouteSpec(entry=["b1"])},
        )
        workflow = _workflow(tasks, branches=[router])

        with pytest.raises(SpecValidationError) as exc_info:
            _validate(workflow)
        msg = str(exc_info.value)
        assert "a1" in msg
        assert "b1" in msg


# ---------------------------------------------------------------------------
# Rule 5 (R2): inferred cross-route coupling -- the most important rule
# ---------------------------------------------------------------------------


class TestRule5InferredCrossRouteCoupling:
    def test_inferred_edge_into_convergence_task_rejected(self) -> None:
        tasks = [
            _task("classify", outputs=["out/verdict.json"]),
            _task("bug-fix", depends_on=["classify"], outputs=["shared/x.json"]),
            _task("epic-plan", depends_on=["classify"]),
            # "converge" declares epic-plan as its only dependency, but ALSO
            # reads bug-fix's output path -- an undeclared, inferred edge.
            _task("converge", depends_on=["epic-plan"], inputs=["shared/x.json"]),
        ]
        router = RouterSpec(
            id="r",
            router_task_id="classify",
            verdict_path="out/verdict.json",
            routes={"bug": RouteSpec(entry=["bug-fix"]), "epic": RouteSpec(entry=["epic-plan"])},
        )
        workflow = _workflow(tasks, branches=[router])

        with pytest.raises(SpecValidationError) as exc_info:
            _validate(workflow)
        msg = str(exc_info.value)
        assert "converge" in msg
        assert "bug-fix" in msg
        assert "shared/x.json" in msg

    def test_declared_convergence_with_explicit_join_not_rejected(self) -> None:
        """Guardrail negative test: depends_on + explicit join must NOT trip
        rule 5, even though the same shared path also matches an input/output
        pair (the classic false-positive rule 5 must avoid)."""
        tasks = [
            _task("classify", outputs=["out/verdict.json"]),
            _task("bug-fix", depends_on=["classify"], outputs=["shared/x.json"]),
            _task("bug-sink", depends_on=["bug-fix"]),
            _task("epic-plan", depends_on=["classify"]),
            _task("epic-sink", depends_on=["epic-plan"]),
            _task(
                "converge",
                depends_on=["bug-fix", "epic-plan"],
                inputs=["shared/x.json"],
                join="any",
            ),
        ]
        router = RouterSpec(
            id="r",
            router_task_id="classify",
            verdict_path="out/verdict.json",
            routes={"bug": RouteSpec(entry=["bug-fix"]), "epic": RouteSpec(entry=["epic-plan"])},
        )
        workflow = _workflow(tasks, branches=[router])

        warnings = _validate(workflow)  # must not raise
        assert warnings == []


# ---------------------------------------------------------------------------
# Rule 6: convergence needs join (WARN only, never a hard fail)
# ---------------------------------------------------------------------------


class TestRule6ConvergenceNeedsJoinWarning:
    def test_declared_convergence_with_default_join_warns_not_rejects(self) -> None:
        tasks = [
            _task("head"),
            _task("a1", depends_on=["head"]),
            _task("a1-sink", depends_on=["a1"]),
            _task("b1", depends_on=["head"]),
            _task("b1-sink", depends_on=["b1"]),
            _task("merge", depends_on=["a1", "b1"]),  # join left at default "all"
        ]
        router = RouterSpec(
            id="r",
            router_task_id="head",
            verdict_path="out/v.json",
            routes={"a": RouteSpec(entry=["a1"]), "b": RouteSpec(entry=["b1"])},
        )
        workflow = _workflow(tasks, branches=[router])

        warnings = _validate(workflow)  # must not raise
        assert len(warnings) == 1
        assert "merge" in warnings[0]
        assert "join" in warnings[0].lower()

    def test_declared_convergence_via_cli_exits_0_with_warning(self, tmp_path: Path) -> None:
        """Given a spec with an unmarked convergence / When `ao validate` /
        Then exit 0, but a WARNING is printed (rule 6 is non-fatal)."""
        workflow = {
            "version": "1.0",
            "id": "warn-wf",
            "repo_set": "default-set",
            "tasks": [
                {"id": "head", "agent": "ag", "instruction": "i.md"},
                {"id": "a1", "agent": "ag", "instruction": "i.md", "depends_on": ["head"]},
                {"id": "a1-sink", "agent": "ag", "instruction": "i.md", "depends_on": ["a1"]},
                {"id": "b1", "agent": "ag", "instruction": "i.md", "depends_on": ["head"]},
                {"id": "b1-sink", "agent": "ag", "instruction": "i.md", "depends_on": ["b1"]},
                {
                    "id": "merge",
                    "agent": "ag",
                    "instruction": "i.md",
                    "depends_on": ["a1", "b1"],
                },
            ],
            "branches": [
                {
                    "id": "r",
                    "router_task_id": "head",
                    "verdict_path": "out/v.json",
                    "routes": {"a": {"entry": ["a1"]}, "b": {"entry": ["b1"]}},
                }
            ],
        }
        wf, rs, ag = _write_cli_specs(tmp_path, workflow)

        result = runner.invoke(
            app,
            ["validate", "--workflow", str(wf), "--reposets", str(rs), "--agents", str(ag)],
        )

        assert result.exit_code == 0, f"Validate should still succeed:\n{result.output}"
        assert "WARNING" in result.output
        assert "merge" in result.output


# ---------------------------------------------------------------------------
# Rule 7: any-join satisfiability
# ---------------------------------------------------------------------------


class TestRule7AnyJoinSatisfiability:
    def test_any_join_unsatisfiable_rejected(self) -> None:
        """`p2` (join='all', default) converges routes a/b and is therefore
        ALWAYS not-taken (exactly one of its two producers is always the
        unselected route). `j`'s only effective dependency is p2, so j
        (join='any') can never be active under any verdict selection."""
        tasks = [
            _task("classify"),
            _task("a1", depends_on=["classify"]),
            _task("a1-sink", depends_on=["a1"]),
            _task("b1", depends_on=["classify"]),
            _task("b1-sink", depends_on=["b1"]),
            _task("p2", depends_on=["a1", "b1"]),
            _task("j", depends_on=["p2"], join="any"),
        ]
        router = RouterSpec(
            id="router",
            router_task_id="classify",
            verdict_path="out/v.json",
            routes={"a": RouteSpec(entry=["a1"]), "b": RouteSpec(entry=["b1"])},
        )
        workflow = _workflow(tasks, branches=[router])

        with pytest.raises(SpecValidationError) as exc_info:
            _validate(workflow)
        msg = str(exc_info.value)
        assert "j" in msg
        assert "unsatisfiable" in msg.lower()

    def test_any_join_satisfiable_via_disjoint_routes_not_rejected(self) -> None:
        """Negative test: the normal any-join re-join pattern (one producer
        per mutually exclusive route) is always satisfiable -- exactly one
        producer is active under every verdict selection -- and must not
        reject."""
        tasks = [
            _task("classify"),
            _task("a1", depends_on=["classify"]),
            _task("a1-sink", depends_on=["a1"]),
            _task("b1", depends_on=["classify"]),
            _task("b1-sink", depends_on=["b1"]),
            _task("rejoin", depends_on=["a1", "b1"], join="any"),
        ]
        router = RouterSpec(
            id="router",
            router_task_id="classify",
            verdict_path="out/v.json",
            routes={"a": RouteSpec(entry=["a1"]), "b": RouteSpec(entry=["b1"])},
        )
        workflow = _workflow(tasks, branches=[router])

        warnings = _validate(workflow)  # must not raise
        assert warnings == []


# ---------------------------------------------------------------------------
# Rule 8: unreachable endpoint
# ---------------------------------------------------------------------------


class TestRule8UnreachableEndpoint:
    def test_no_sink_in_exclusive_cone_rejected(self) -> None:
        tasks = [
            _task("classify"),
            _task("x1", depends_on=["classify"]),
            _task("y1", depends_on=["classify"]),
            _task("merge", depends_on=["x1", "y1"], join="any"),
        ]
        router = RouterSpec(
            id="r",
            router_task_id="classify",
            verdict_path="out/v.json",
            routes={"x": RouteSpec(entry=["x1"]), "y": RouteSpec(entry=["y1"])},
        )
        workflow = _workflow(tasks, branches=[router])

        with pytest.raises(SpecValidationError) as exc_info:
            _validate(workflow)
        msg = str(exc_info.value)
        assert "no sink" in msg.lower()

    def test_unreachable_endpoint_via_cli(self, tmp_path: Path) -> None:
        """CliRunner companion (memory `engine-api-tests-dont-cover-cli`)."""
        workflow = {
            "version": "1.0",
            "id": "endpoint-wf",
            "repo_set": "default-set",
            "tasks": [
                {"id": "classify", "agent": "ag", "instruction": "i.md"},
                {"id": "x1", "agent": "ag", "instruction": "i.md", "depends_on": ["classify"]},
                {"id": "y1", "agent": "ag", "instruction": "i.md", "depends_on": ["classify"]},
                {
                    "id": "merge",
                    "agent": "ag",
                    "instruction": "i.md",
                    "depends_on": ["x1", "y1"],
                    "join": "any",
                },
            ],
            "branches": [
                {
                    "id": "r",
                    "router_task_id": "classify",
                    "verdict_path": "out/v.json",
                    "routes": {"x": {"entry": ["x1"]}, "y": {"entry": ["y1"]}},
                }
            ],
        }
        wf, rs, ag = _write_cli_specs(tmp_path, workflow)

        result = runner.invoke(
            app,
            ["validate", "--workflow", str(wf), "--reposets", str(rs), "--agents", str(ag)],
        )

        assert result.exit_code != 0
        assert "no sink" in result.output.lower()


# ---------------------------------------------------------------------------
# Rule 9: nested router rejection (MVP boundary)
# ---------------------------------------------------------------------------


class TestRule9NestedRouterRejected:
    def test_nested_router_rejected(self) -> None:
        tasks = [
            _task("outer"),
            _task("mid", depends_on=["outer"]),
            _task("other1", depends_on=["outer"]),
            _task("p1", depends_on=["mid"]),
            _task("q1", depends_on=["mid"]),
        ]
        router1 = RouterSpec(
            id="router1",
            router_task_id="outer",
            verdict_path="out/v1.json",
            routes={"only": RouteSpec(entry=["mid"]), "other": RouteSpec(entry=["other1"])},
        )
        router2 = RouterSpec(
            id="router2",
            router_task_id="mid",  # lies inside router1's "only" cone
            verdict_path="out/v2.json",
            routes={"p": RouteSpec(entry=["p1"]), "q": RouteSpec(entry=["q1"])},
        )
        workflow = _workflow(tasks, branches=[router1, router2])

        with pytest.raises(SpecValidationError) as exc_info:
            _validate(workflow)
        msg = str(exc_info.value)
        assert "router1" in msg
        assert "router2" in msg


# ---------------------------------------------------------------------------
# Rule 10: breaker refs
# ---------------------------------------------------------------------------


class TestRule10BreakerRefs:
    def test_unknown_verdict_task_id_rejected(self) -> None:
        tasks = [_task("a")]
        breaker = CircuitBreakerSpec(
            id="verdict-breaker",
            condition="verdict",
            action="fail",
            task_id="nonexistent",
            verdict_path="out/v.json",
        )
        workflow = _workflow(tasks, circuit_breakers=[breaker])

        with pytest.raises(SpecValidationError) as exc_info:
            _validate(workflow)
        assert "nonexistent" in str(exc_info.value)

    def test_duplicate_breaker_id_rejected(self) -> None:
        tasks = [_task("a")]
        b1 = CircuitBreakerSpec(id="dup", condition="task_failures", action="fail", threshold=3)
        b2 = CircuitBreakerSpec(
            id="dup", condition="consecutive_failures", action="stop", threshold=2
        )
        workflow = _workflow(tasks, circuit_breakers=[b1, b2])

        with pytest.raises(SpecValidationError) as exc_info:
            _validate(workflow)
        msg = str(exc_info.value)
        assert "dup" in msg
        assert "duplicate" in msg.lower()

    def test_condition_not_implemented_rejected(self) -> None:
        tasks = [_task("a")]
        breaker = CircuitBreakerSpec(id="future", condition="failure_ratio", action="fail")
        workflow = _workflow(tasks, circuit_breakers=[breaker])

        with pytest.raises(SpecValidationError) as exc_info:
            _validate(workflow)
        assert "not implemented" in str(exc_info.value).lower()

    def test_projected_cost_exceeds_still_not_implemented(self) -> None:
        """The reserved pre-flight-estimate name stays distinct from task_cost_usd/
        run_cost_usd (E-9h3m7k) — it is NOT implemented by this epic."""
        tasks = [_task("a")]
        breaker = CircuitBreakerSpec(id="future", condition="projected_cost_exceeds", action="fail")
        workflow = _workflow(tasks, circuit_breakers=[breaker])

        with pytest.raises(SpecValidationError) as exc_info:
            _validate(workflow)
        assert "not implemented" in str(exc_info.value).lower()

    @pytest.mark.parametrize("condition", ["task_cost_usd", "run_cost_usd"])
    def test_actual_cost_conditions_accepted(self, condition: str) -> None:
        """E-9h3m7k FR-4: task_cost_usd/run_cost_usd pass validation with a threshold."""
        tasks = [_task("a")]
        breaker = CircuitBreakerSpec(
            id="cost-cap", condition=condition, action="fail", threshold=3.0
        )
        workflow = _workflow(tasks, circuit_breakers=[breaker])

        _validate(workflow)  # must not raise

    def test_run_active_seconds_accepted(self) -> None:
        """E-3JTmVu regression: run_active_seconds was in BREAKER_REGISTRY and the schema
        but missing from the validator's implemented-conditions set, so `ao validate`
        rejected a fully-implemented condition as "not implemented"."""
        tasks = [_task("a")]
        breaker = CircuitBreakerSpec(
            id="active-cap", condition="run_active_seconds", action="stop", threshold=3600
        )
        workflow = _workflow(tasks, circuit_breakers=[breaker])

        _validate(workflow)  # must not raise

    @pytest.mark.parametrize("condition", sorted(BREAKER_REGISTRY))
    def test_every_registry_condition_accepted(self, condition: str) -> None:
        """Every condition the engine actually implements (BREAKER_REGISTRY) must pass
        validate_run_control — pins the validator's set to the registry so the two can
        never drift apart again."""
        tasks = [_task("a")]
        extra: dict[str, object] = {"threshold": 3}
        if condition == "stop_file":
            extra = {"path": "control/stop.flag"}
        elif condition == "verdict":
            extra = {"task_id": "a", "verdict_path": "out/v.json"}
        breaker = CircuitBreakerSpec(id="reg", condition=condition, action="fail", **extra)
        workflow = _workflow(tasks, circuit_breakers=[breaker])

        _validate(workflow)  # must not raise


# ---------------------------------------------------------------------------
# Rule 11: reserved-suffix / id-pattern
# ---------------------------------------------------------------------------


class TestRule11ReservedIdPattern:
    def test_route_id_bad_pattern_rejected(self) -> None:
        """Route ids are dict keys of `router.routes` -- the JSON Schema does
        NOT constrain dict keys, so this is the only enforcement point."""
        tasks = [_task("head"), _task("a1", depends_on=["head"]), _task("b1", depends_on=["head"])]
        router = RouterSpec(
            id="r",
            router_task_id="head",
            verdict_path="out/v.json",
            routes={"ok-route": RouteSpec(entry=["a1"]), "Bad-Route": RouteSpec(entry=["b1"])},
        )
        workflow = _workflow(tasks, branches=[router])

        with pytest.raises(SpecValidationError) as exc_info:
            _validate(workflow)
        assert "Bad-Route" in str(exc_info.value)

    def test_route_id_with_reserved_iter_marker_rejected(self) -> None:
        tasks = [_task("head"), _task("a1", depends_on=["head"]), _task("b1", depends_on=["head"])]
        router = RouterSpec(
            id="r",
            router_task_id="head",
            verdict_path="out/v.json",
            routes={"ok-route": RouteSpec(entry=["a1"]), "step__iter2": RouteSpec(entry=["b1"])},
        )
        workflow = _workflow(tasks, branches=[router])

        with pytest.raises(SpecValidationError) as exc_info:
            _validate(workflow)
        assert "__iter" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Positive: a valid multi-endpoint + breaker spec passes cleanly
# ---------------------------------------------------------------------------


class TestPositiveValidSpec:
    def test_valid_multi_endpoint_and_breaker_spec_passes_cleanly(self) -> None:
        tasks = [
            _task("classify", outputs=["out/verdict.json"]),
            _task("bug-fix", depends_on=["classify"], outputs=["out/bug.txt"]),
            _task("epic-plan", depends_on=["classify"], outputs=["out/epic.txt"]),
        ]
        router = RouterSpec(
            id="classify-router",
            router_task_id="classify",
            verdict_path="out/verdict.json",
            routes={"bug": RouteSpec(entry=["bug-fix"]), "epic": RouteSpec(entry=["epic-plan"])},
        )
        breaker = CircuitBreakerSpec(
            id="too-many-failures", condition="task_failures", action="fail", threshold=3
        )
        workflow = _workflow(tasks, branches=[router], circuit_breakers=[breaker])

        warnings = _validate(workflow)
        assert warnings == []

    def test_valid_spec_via_cli_exits_0_ok(self, tmp_path: Path) -> None:
        workflow = {
            "version": "1.0",
            "id": "clean-wf",
            "repo_set": "default-set",
            "tasks": [
                {
                    "id": "classify",
                    "agent": "ag",
                    "instruction": "i.md",
                    "outputs": ["out/verdict.json"],
                },
                {
                    "id": "bug-fix",
                    "agent": "ag",
                    "instruction": "i.md",
                    "depends_on": ["classify"],
                    "outputs": ["out/bug.txt"],
                },
                {
                    "id": "epic-plan",
                    "agent": "ag",
                    "instruction": "i.md",
                    "depends_on": ["classify"],
                    "outputs": ["out/epic.txt"],
                },
            ],
            "branches": [
                {
                    "id": "classify-router",
                    "router_task_id": "classify",
                    "verdict_path": "out/verdict.json",
                    "routes": {"bug": {"entry": ["bug-fix"]}, "epic": {"entry": ["epic-plan"]}},
                }
            ],
            "circuit_breakers": [
                {
                    "id": "too-many-failures",
                    "condition": "task_failures",
                    "action": "fail",
                    "threshold": 3,
                }
            ],
        }
        wf, rs, ag = _write_cli_specs(tmp_path, workflow)

        result = runner.invoke(
            app,
            ["validate", "--workflow", str(wf), "--reposets", str(rs), "--agents", str(ag)],
        )

        assert result.exit_code == 0, f"Validate failed:\n{result.output}"
        assert "OK" in result.output
        assert "WARNING" not in result.output


# ---------------------------------------------------------------------------
# R2 (rule 5) driven via a real CliRunner `ao validate` invocation
# ---------------------------------------------------------------------------


class TestCliRunnerR2InferredCoupling:
    """CliRunner companion for rule 5 (memory `engine-api-tests-dont-cover-cli`)."""

    def test_r2_inferred_cross_route_coupling_via_cli(self, tmp_path: Path) -> None:
        workflow = {
            "version": "1.0",
            "id": "r2-wf",
            "repo_set": "default-set",
            "tasks": [
                {
                    "id": "classify",
                    "agent": "ag",
                    "instruction": "i.md",
                    "outputs": ["out/verdict.json"],
                },
                {
                    "id": "bug-fix",
                    "agent": "ag",
                    "instruction": "i.md",
                    "depends_on": ["classify"],
                    "outputs": ["shared/x.json"],
                },
                {
                    "id": "epic-plan",
                    "agent": "ag",
                    "instruction": "i.md",
                    "depends_on": ["classify"],
                },
                {
                    "id": "converge",
                    "agent": "ag",
                    "instruction": "i.md",
                    "depends_on": ["epic-plan"],
                    "inputs": ["shared/x.json"],
                },
            ],
            "branches": [
                {
                    "id": "classify-router",
                    "router_task_id": "classify",
                    "verdict_path": "out/verdict.json",
                    "routes": {"bug": {"entry": ["bug-fix"]}, "epic": {"entry": ["epic-plan"]}},
                }
            ],
        }
        wf, rs, ag = _write_cli_specs(tmp_path, workflow)

        result = runner.invoke(
            app,
            ["validate", "--workflow", str(wf), "--reposets", str(rs), "--agents", str(ag)],
        )

        assert result.exit_code != 0
        assert "converge" in result.output
        assert "bug-fix" in result.output


# ---------------------------------------------------------------------------
# Cycle detection now surfaces during `ao validate` too (cli._load_all wiring)
# ---------------------------------------------------------------------------


class TestCycleDetectionDuringValidate:
    def test_cycle_detected_via_ao_validate_cli(self, tmp_path: Path) -> None:
        """Regression guard for the cli._load_all wiring change: build_dag is
        now called (and topological_order run) inside `ao validate` too, not
        just `ao run`/`resume` -- a cycle must surface here as well."""
        workflow = {
            "version": "1.0",
            "id": "cyclic-wf",
            "repo_set": "default-set",
            "tasks": [
                {"id": "a", "agent": "ag", "instruction": "i.md", "depends_on": ["b"]},
                {"id": "b", "agent": "ag", "instruction": "i.md", "depends_on": ["a"]},
            ],
        }
        wf, rs, ag = _write_cli_specs(tmp_path, workflow)

        result = runner.invoke(
            app,
            ["validate", "--workflow", str(wf), "--reposets", str(rs), "--agents", str(ag)],
        )

        assert result.exit_code != 0
        assert "cycle" in result.output.lower()
