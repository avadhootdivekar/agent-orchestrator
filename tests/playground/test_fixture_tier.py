"""Tier 1 — Fixture-based tests for playground examples.

Tests static correctness of example specs + fixtures WITHOUT running workflows.
This tier validates that authoring errors are caught cheaply and deterministically.

Covers requirements: FR-3, FR-7 (foundation), NFR-2, NFR-4, NFR-5, R2.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import jsonschema
import pytest

from agent_orchestrator.config import load_agents, load_reposets
from agent_orchestrator.dag import build_dag
from agent_orchestrator.models import TaskSpec
from agent_orchestrator.spec import cross_validate, load_workflow
from tests.playground.harness import (
    discover_examples,
    expanded_workflow,
    load_expected,
)

REPO_ROOT = Path(__file__).parent.parent.parent

# Known engine events that should appear in expected_events.json
KNOWN_ENGINE_EVENTS = {
    "run.start",
    "run.end",
    "task.start",
    "task.end",
    "task.injected",
    "loop.iterate",
    "budget.charge",
    "budget.reconcile",
}

# Valid id pattern from NFR-5
ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-_]*$")


class TestFixtureTierSpecValidation:
    """Validate example specs against JSON schemas (AC-1, AC-2)."""

    @pytest.mark.parametrize("example", discover_examples())
    def test_workflow_json_validates_against_schema(self, example: str) -> None:
        """Given workflow.json, when validated against workflow.schema.json, then passes."""
        schema_path = REPO_ROOT / "specs" / "workflow.schema.json"
        example_path = REPO_ROOT / "playground" / example
        workflow_path = example_path / "workflow.json"

        schema = json.loads(schema_path.read_text())
        workflow = json.loads(workflow_path.read_text())

        jsonschema.validate(workflow, schema)  # Raises ValidationError on fail

    @pytest.mark.parametrize("example", discover_examples())
    def test_reposet_json_validates_against_schema(self, example: str) -> None:
        """Given reposet.json, when validated against reposet.schema.json, then passes."""
        schema_path = REPO_ROOT / "specs" / "reposet.schema.json"
        example_path = REPO_ROOT / "playground" / example
        reposet_path = example_path / "reposet.json"

        schema = json.loads(schema_path.read_text())
        reposet = json.loads(reposet_path.read_text())

        jsonschema.validate(reposet, schema)  # Raises ValidationError on fail

    @pytest.mark.parametrize("example", discover_examples())
    def test_agents_fake_json_validates_against_schema(self, example: str) -> None:
        """Given agents.fake.json, when validated against agents.schema.json, then passes."""
        schema_path = REPO_ROOT / "specs" / "agents.schema.json"
        example_path = REPO_ROOT / "playground" / example
        agents_path = example_path / "agents.fake.json"

        schema = json.loads(schema_path.read_text())
        agents = json.loads(agents_path.read_text())

        jsonschema.validate(agents, schema)

    @pytest.mark.parametrize("example", discover_examples())
    def test_agents_claude_json_validates_against_schema(self, example: str) -> None:
        """Given agents.claude.json, when validated against agents.schema.json, then passes."""
        schema_path = REPO_ROOT / "specs" / "agents.schema.json"
        example_path = REPO_ROOT / "playground" / example
        agents_path = example_path / "agents.claude.json"

        schema = json.loads(schema_path.read_text())
        agents = json.loads(agents_path.read_text())

        jsonschema.validate(agents, schema)

    @pytest.mark.parametrize("example", discover_examples())
    def test_specs_cross_validate(self, example: str) -> None:
        """Given workflow/reposet/agents, when cross-validated, then no error."""
        example_path = REPO_ROOT / "playground" / example
        wf = load_workflow(example_path / "workflow.json")
        reposet_map = load_reposets(example_path / "reposet.json")
        agent_map_fake = load_agents(example_path / "agents.fake.json")
        agent_map_claude = load_agents(example_path / "agents.claude.json")

        # Both fake and claude agents should cross-validate
        cross_validate(wf, reposet_map, agent_map_fake)
        cross_validate(wf, reposet_map, agent_map_claude)


class TestFixtureTierManifest:
    """Validate fixture manifest correctness (AC-3)."""

    @pytest.mark.parametrize("example", discover_examples())
    def test_tasks_manifest_wellformed(self, example: str) -> None:
        """Given fixtures/tasks-manifest.json, when parsed, then is {"tasks":[...]}."""
        example_path = REPO_ROOT / "playground" / example
        manifest_path = example_path / "fixtures" / "tasks-manifest.json"

        manifest = json.loads(manifest_path.read_text())
        assert "tasks" in manifest
        assert isinstance(manifest["tasks"], list)

    @pytest.mark.parametrize("example", discover_examples())
    def test_manifest_tasks_are_valid_taskspecs(self, example: str) -> None:
        """Given manifest tasks, when validated as TaskSpec, then no error."""
        example_path = REPO_ROOT / "playground" / example
        manifest_path = example_path / "fixtures" / "tasks-manifest.json"

        manifest = json.loads(manifest_path.read_text())
        for task_data in manifest.get("tasks", []):
            task = TaskSpec(**task_data)  # Raises ValidationError if invalid
            assert task.id

    @pytest.mark.parametrize("example", discover_examples())
    def test_manifest_task_ids_unique(self, example: str) -> None:
        """Given manifest tasks, when checked, then all ids are unique."""
        example_path = REPO_ROOT / "playground" / example
        manifest_path = example_path / "fixtures" / "tasks-manifest.json"

        manifest = json.loads(manifest_path.read_text())
        ids = [t["id"] for t in manifest.get("tasks", [])]
        assert len(ids) == len(set(ids)), f"Duplicate ids in manifest: {ids}"

    @pytest.mark.parametrize("example", discover_examples())
    def test_manifest_task_ids_disjoint_from_spine(self, example: str) -> None:
        """Given manifest ids and spine ids, when checked, then no overlap."""
        example_path = REPO_ROOT / "playground" / example
        manifest_path = example_path / "fixtures" / "tasks-manifest.json"
        wf = load_workflow(example_path / "workflow.json")

        manifest = json.loads(manifest_path.read_text())
        manifest_ids = {t["id"] for t in manifest.get("tasks", [])}
        spine_ids = {t.id for t in wf.tasks}

        overlap = manifest_ids & spine_ids
        assert not overlap, f"Manifest ids overlap with spine: {overlap}"

    @pytest.mark.parametrize("example", discover_examples())
    def test_manifest_instruction_paths_exist(self, example: str) -> None:
        """Given manifest tasks, when checked, then all instruction paths exist."""
        example_path = REPO_ROOT / "playground" / example
        manifest_path = example_path / "fixtures" / "tasks-manifest.json"

        manifest = json.loads(manifest_path.read_text())
        for task_data in manifest.get("tasks", []):
            instr_path = example_path / task_data["instruction"]
            assert instr_path.exists(), f"Missing instruction file: {instr_path}"


class TestFixtureTierExpandedDAG:
    """Validate DAG acyclicity and expected edges (AC-4, R2 mitigation)."""

    @pytest.mark.parametrize("example", discover_examples())
    def test_expanded_workflow_is_acyclic(self, example: str) -> None:
        """Given spine + injected tasks merged, when build_dag runs, then no cycle."""
        wf = expanded_workflow(example)
        # Should not raise CycleError
        dag = build_dag(wf)
        dag.topological_order()  # Explicitly call to ensure no cycle

    @pytest.mark.parametrize("example", discover_examples())
    def test_expanded_workflow_inferred_edges_present(self, example: str) -> None:
        """Given expanded workflow, when DAG built, then expected inferred edges exist.

        For sum-of-array example: taskreview-t1 produces output/tasks/t1/review.md,
        which is consumed by integrate => edge taskreview-t1 -> integrate.
        """
        wf = expanded_workflow(example)
        dag = build_dag(wf)
        topo_order = dag.topological_order()

        # For sum-of-array, verify taskreview-t1 comes before integrate
        if example == "sum-of-array":
            assert "taskreview-t1" in topo_order
            assert "integrate" in topo_order
            idx_review = topo_order.index("taskreview-t1")
            idx_integrate = topo_order.index("integrate")
            assert idx_review < idx_integrate, (
                "taskreview-t1 should come before integrate "
                "(via output/tasks/t1/review.md inferred edge)"
            )


class TestFixtureTierExpectedStructure:
    """Validate expected_paths.json and expected_events.json (AC-5, AC-6)."""

    @pytest.mark.parametrize("example", discover_examples())
    def test_expected_paths_is_list(self, example: str) -> None:
        """Given expected_paths.json, when loaded, then is a list."""
        expected = load_expected(example)
        assert "output_artifacts" in expected
        assert isinstance(expected["output_artifacts"], list)

    @pytest.mark.parametrize("example", discover_examples())
    def test_expected_events_has_scenario_keys(self, example: str) -> None:
        """Given expected_events.json, when loaded, then has one_round and two_round."""
        expected = load_expected(example)
        assert "expected_events" in expected
        events = expected["expected_events"]
        assert "one_round" in events, "expected_events must have 'one_round' scenario"
        # two_round is optional but recommended
        if "two_round" in events:
            assert isinstance(events["two_round"], dict)

    @pytest.mark.parametrize("example", discover_examples())
    def test_expected_events_one_round_has_required_fields(self, example: str) -> None:
        """Given one_round scenario, when checked, then has event_types and task_start_order."""
        expected = load_expected(example)
        one_round = expected["expected_events"]["one_round"]
        assert "event_types" in one_round
        assert "task_start_order" in one_round
        assert isinstance(one_round["event_types"], list)
        assert isinstance(one_round["task_start_order"], list)

    @pytest.mark.parametrize("example", discover_examples())
    def test_expected_event_types_are_known(self, example: str) -> None:
        """Given expected event types, when checked, then all are in KNOWN_ENGINE_EVENTS."""
        expected = load_expected(example)
        events_data = expected["expected_events"]

        for scenario_key in ("one_round", "two_round"):
            if scenario_key in events_data:
                scenario = events_data[scenario_key]
                event_types = set(scenario.get("event_types", []))
                unknown = event_types - KNOWN_ENGINE_EVENTS
                assert not unknown, (
                    f"{scenario_key}: unknown event types {unknown}. Known: {KNOWN_ENGINE_EVENTS}"
                )


class TestFixtureTierIDs:
    """Validate ID format across all fixtures (AC-7, NFR-5)."""

    @pytest.mark.parametrize("example", discover_examples())
    def test_workflow_task_ids_match_pattern(self, example: str) -> None:
        """Given workflow task ids, when checked, then all match ^[a-z0-9][a-z0-9-_]*$."""
        example_path = REPO_ROOT / "playground" / example
        wf = load_workflow(example_path / "workflow.json")

        for task in wf.tasks:
            assert ID_PATTERN.match(task.id), (
                f"Task id '{task.id}' doesn't match pattern {ID_PATTERN.pattern}"
            )

    @pytest.mark.parametrize("example", discover_examples())
    def test_manifest_task_ids_match_pattern(self, example: str) -> None:
        """Given manifest task ids, when checked, then all match pattern."""
        example_path = REPO_ROOT / "playground" / example
        manifest_path = example_path / "fixtures" / "tasks-manifest.json"

        manifest = json.loads(manifest_path.read_text())
        for task in manifest.get("tasks", []):
            task_id = task["id"]
            assert ID_PATTERN.match(task_id), (
                f"Manifest task id '{task_id}' doesn't match pattern {ID_PATTERN.pattern}"
            )

    @pytest.mark.parametrize("example", discover_examples())
    def test_workflow_loop_ids_match_pattern(self, example: str) -> None:
        """Given workflow loop ids, when checked, then all match pattern."""
        example_path = REPO_ROOT / "playground" / example
        wf = load_workflow(example_path / "workflow.json")

        for loop in wf.loops or []:
            assert ID_PATTERN.match(loop.id), (
                f"Loop id '{loop.id}' doesn't match pattern {ID_PATTERN.pattern}"
            )
