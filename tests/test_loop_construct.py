"""Tests for Feature Area 2b/2c — LoopSpec construct and gate evaluation.

Covers T-5isej3 acceptance criteria 2, 3, 7, 8, 9, 10 and HLD §6.3 edge cases.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_orchestrator.artifacts import LocalFsArtifactStore, read_gate
from agent_orchestrator.errors import GateError, SpecValidationError
from agent_orchestrator.executors.fake import FakeExecutor
from agent_orchestrator.models import LoopSpec, TaskSpec, WorkflowSpec
from agent_orchestrator.spec import cross_validate

# ---------------------------------------------------------------------------
# Unit: read_gate
# ---------------------------------------------------------------------------


class TestReadGate:
    def test_happy_path_continue_true(self, tmp_path: Path) -> None:
        store = LocalFsArtifactStore(str(tmp_path))
        (tmp_path / "gate.json").write_text('{"continue": true}')
        assert read_gate(store, "gate.json") is True

    def test_happy_path_continue_false(self, tmp_path: Path) -> None:
        store = LocalFsArtifactStore(str(tmp_path))
        (tmp_path / "gate.json").write_text('{"continue": false}')
        assert read_gate(store, "gate.json") is False

    def test_custom_field(self, tmp_path: Path) -> None:
        store = LocalFsArtifactStore(str(tmp_path))
        (tmp_path / "gate.json").write_text('{"approved": true, "extra": "data"}')
        assert read_gate(store, "gate.json", field="approved") is True

    def test_missing_file_raises_gate_error(self, tmp_path: Path) -> None:
        store = LocalFsArtifactStore(str(tmp_path))
        with pytest.raises(GateError, match="not found"):
            read_gate(store, "no-gate.json")

    def test_missing_field_raises_gate_error(self, tmp_path: Path) -> None:
        store = LocalFsArtifactStore(str(tmp_path))
        (tmp_path / "gate.json").write_text('{"other": true}')
        with pytest.raises(GateError, match="missing field"):
            read_gate(store, "gate.json")

    def test_non_bool_value_raises_gate_error(self, tmp_path: Path) -> None:
        store = LocalFsArtifactStore(str(tmp_path))
        (tmp_path / "gate.json").write_text('{"continue": "yes"}')
        with pytest.raises(GateError, match="must be bool"):
            read_gate(store, "gate.json")

    def test_not_a_dict_raises_gate_error(self, tmp_path: Path) -> None:
        store = LocalFsArtifactStore(str(tmp_path))
        (tmp_path / "gate.json").write_text("[true]")
        with pytest.raises(GateError, match="JSON object"):
            read_gate(store, "gate.json")

    def test_invalid_json_raises_gate_error(self, tmp_path: Path) -> None:
        store = LocalFsArtifactStore(str(tmp_path))
        (tmp_path / "gate.json").write_text("{bad json")
        with pytest.raises(GateError, match="not valid JSON"):
            read_gate(store, "gate.json")


# ---------------------------------------------------------------------------
# Unit: clone_body (Orchestrator._clone_body)
# ---------------------------------------------------------------------------


class TestCloneBody:
    def _make_wf(self, tasks: list[dict]) -> WorkflowSpec:
        """Build a minimal WorkflowSpec for clone_body tests."""
        task_specs = [TaskSpec(**t) for t in tasks]
        return WorkflowSpec(
            version="1.0",
            id="wf",
            repo_set="rs",
            tasks=task_specs,
        )

    def _make_loop(self, body: list[str], gate_id: str, gate_path: str) -> LoopSpec:
        return LoopSpec(
            id="test-loop",
            body=body,
            gate_task_id=gate_id,
            gate_output_path=gate_path,
        )

    def _make_orch(self, tmp_path: Path):
        from agent_orchestrator.engine import Orchestrator
        from agent_orchestrator.runstate import RunStateStore

        store = LocalFsArtifactStore(str(tmp_path))
        rs = RunStateStore(str(tmp_path), store)
        return Orchestrator(FakeExecutor(), store, rs)

    def test_clone_suffixes_ids(self, tmp_path: Path) -> None:
        wf = self._make_wf(
            [
                {"id": "dev", "agent": "ag", "instruction": "i.md"},
                {"id": "review", "agent": "ag", "instruction": "i.md", "depends_on": ["dev"]},
            ]
        )
        loop = self._make_loop(["dev", "review"], "review", "output/gate.json")
        orch = self._make_orch(tmp_path)
        clones = orch._clone_body(loop, 2, wf)
        assert [c.id for c in clones] == ["dev__iter2", "review__iter2"]

    def test_clone_rewrites_intra_body_depends_on(self, tmp_path: Path) -> None:
        wf = self._make_wf(
            [
                {"id": "dev", "agent": "ag", "instruction": "i.md"},
                {"id": "review", "agent": "ag", "instruction": "i.md", "depends_on": ["dev"]},
            ]
        )
        loop = self._make_loop(["dev", "review"], "review", "output/gate.json")
        orch = self._make_orch(tmp_path)
        clones = orch._clone_body(loop, 2, wf)
        review_clone = next(c for c in clones if c.id == "review__iter2")
        assert "dev__iter2" in review_clone.depends_on
        # External dep (none here) should be unchanged

    def test_clone_chains_first_task_to_previous_iter_last_task(self, tmp_path: Path) -> None:
        wf = self._make_wf(
            [
                {"id": "dev", "agent": "ag", "instruction": "i.md"},
                {"id": "review", "agent": "ag", "instruction": "i.md", "depends_on": ["dev"]},
            ]
        )
        loop = self._make_loop(["dev", "review"], "review", "output/gate.json")
        orch = self._make_orch(tmp_path)
        # Clone iter 2: first task (dev__iter2) should depend on review (iter1 last)
        clones = orch._clone_body(loop, 2, wf)
        dev_clone = next(c for c in clones if c.id == "dev__iter2")
        assert "review" in dev_clone.depends_on  # chain to iter1 last task

    def test_clone_iter3_chains_to_iter2(self, tmp_path: Path) -> None:
        """Iter 3 first task must chain to iter 2's last task."""
        wf = self._make_wf(
            [
                {"id": "dev", "agent": "ag", "instruction": "i.md"},
                {"id": "review", "agent": "ag", "instruction": "i.md", "depends_on": ["dev"]},
            ]
        )
        loop = self._make_loop(["dev", "review"], "review", "output/gate.json")
        orch = self._make_orch(tmp_path)
        clones = orch._clone_body(loop, 3, wf)
        dev3 = next(c for c in clones if c.id == "dev__iter3")
        assert "review__iter2" in dev3.depends_on

    def test_gate_path_suffix_for_iter2(self, tmp_path: Path) -> None:
        """Gate path for iter 2 should be suffixed before extension."""
        from agent_orchestrator.engine import Orchestrator
        from agent_orchestrator.runstate import RunStateStore

        store = LocalFsArtifactStore(str(tmp_path))
        rs = RunStateStore(str(tmp_path), store)
        orch = Orchestrator(FakeExecutor(), store, rs)

        loop = LoopSpec(
            id="lp",
            body=["a", "b"],
            gate_task_id="b",
            gate_output_path="output/gate.json",
        )
        assert orch._gate_path_for_iter(loop, 1) == "output/gate.json"
        assert orch._gate_path_for_iter(loop, 2) == "output/gate__iter2.json"
        assert orch._gate_path_for_iter(loop, 3) == "output/gate__iter3.json"

    def test_gate_path_no_extension(self, tmp_path: Path) -> None:
        from agent_orchestrator.engine import Orchestrator
        from agent_orchestrator.runstate import RunStateStore

        store = LocalFsArtifactStore(str(tmp_path))
        rs = RunStateStore(str(tmp_path), store)
        orch = Orchestrator(FakeExecutor(), store, rs)

        loop = LoopSpec(
            id="lp",
            body=["a"],
            gate_task_id="a",
            gate_output_path="output/gate",
        )
        assert orch._gate_path_for_iter(loop, 2) == "output/gate__iter2"


# ---------------------------------------------------------------------------
# Unit: cross_validate LoopSpec constraints
# ---------------------------------------------------------------------------


class TestCrossValidateLoopSpec:
    def _make_wf_with_loops(
        self, tasks: list[dict], loops: list[dict], tmp_path: Path
    ) -> WorkflowSpec:
        instr_dir = tmp_path / "specs" / "instructions"
        instr_dir.mkdir(parents=True, exist_ok=True)
        task_specs = []
        for t in tasks:
            (instr_dir / f"{t['id']}.md").write_text(f"instruction for {t['id']}")
            task_specs.append(TaskSpec(**t))
        loop_specs = [LoopSpec(**lp) for lp in loops]
        return WorkflowSpec(
            version="1.0",
            id="wf",
            repo_set="rs",
            tasks=task_specs,
            loops=loop_specs,
        )

    def test_valid_loop_passes(self, tmp_path: Path) -> None:
        wf = self._make_wf_with_loops(
            [
                {"id": "dev", "agent": "ag", "instruction": "specs/instructions/dev.md"},
                {"id": "review", "agent": "ag", "instruction": "specs/instructions/review.md"},
            ],
            [
                {
                    "id": "lp",
                    "body": ["dev", "review"],
                    "gate_task_id": "review",
                    "gate_output_path": "output/gate.json",
                }
            ],
            tmp_path,
        )
        cross_validate(wf, {"rs": object()}, {"ag": object()})

    def test_body_task_not_in_workflow_raises(self, tmp_path: Path) -> None:
        wf = self._make_wf_with_loops(
            [{"id": "dev", "agent": "ag", "instruction": "specs/instructions/dev.md"}],
            [
                {
                    "id": "lp",
                    "body": ["dev", "nonexistent"],
                    "gate_task_id": "dev",
                    "gate_output_path": "output/gate.json",
                }
            ],
            tmp_path,
        )
        with pytest.raises(SpecValidationError, match="nonexistent"):
            cross_validate(wf, {"rs": object()}, {"ag": object()})

    def test_gate_task_not_in_body_raises(self, tmp_path: Path) -> None:
        wf = self._make_wf_with_loops(
            [
                {"id": "dev", "agent": "ag", "instruction": "specs/instructions/dev.md"},
                {"id": "review", "agent": "ag", "instruction": "specs/instructions/review.md"},
            ],
            [
                {
                    "id": "lp",
                    "body": ["dev"],
                    "gate_task_id": "review",
                    "gate_output_path": "output/gate.json",
                }
            ],
            tmp_path,
        )
        with pytest.raises(SpecValidationError, match="gate_task_id"):
            cross_validate(wf, {"rs": object()}, {"ag": object()})

    def test_overlapping_loop_bodies_raises(self, tmp_path: Path) -> None:
        wf = self._make_wf_with_loops(
            [
                {"id": "dev", "agent": "ag", "instruction": "specs/instructions/dev.md"},
                {"id": "review", "agent": "ag", "instruction": "specs/instructions/review.md"},
            ],
            [
                {
                    "id": "lp1",
                    "body": ["dev"],
                    "gate_task_id": "dev",
                    "gate_output_path": "output/gate1.json",
                },
                {
                    "id": "lp2",
                    "body": ["dev", "review"],
                    "gate_task_id": "review",
                    "gate_output_path": "output/gate2.json",
                },
            ],
            tmp_path,
        )
        with pytest.raises(SpecValidationError, match="at most one loop"):
            cross_validate(wf, {"rs": object()}, {"ag": object()})

    def test_gate_task_is_emitter_raises(self, tmp_path: Path) -> None:
        wf = self._make_wf_with_loops(
            [
                {
                    "id": "emitter",
                    "agent": "ag",
                    "instruction": "specs/instructions/emitter.md",
                    "emit_tasks": True,
                    "task_manifest_path": "output/m.json",
                },
            ],
            [
                {
                    "id": "lp",
                    "body": ["emitter"],
                    "gate_task_id": "emitter",
                    "gate_output_path": "output/gate.json",
                }
            ],
            tmp_path,
        )
        with pytest.raises(SpecValidationError, match="emit_tasks"):
            cross_validate(wf, {"rs": object()}, {"ag": object()})

    def test_body_id_with_iter_suffix_raises(self, tmp_path: Path) -> None:
        """Body task ids containing __iter are reserved and must not be authored."""
        # The cross_validate checks task ids for __iter, so we need a task with __iter in id
        # but that task id also violates the schema pattern — test the spec rule directly
        instr_dir = tmp_path / "specs" / "instructions"
        instr_dir.mkdir(parents=True, exist_ok=True)
        (instr_dir / "t__iter2.md").write_text("instr")
        task = TaskSpec(id="t__iter2", agent="ag", instruction="specs/instructions/t__iter2.md")
        wf = WorkflowSpec(version="1.0", id="wf", repo_set="rs", tasks=[task])
        with pytest.raises(SpecValidationError, match="__iter"):
            cross_validate(wf, {"rs": object()}, {"ag": object()})


# ---------------------------------------------------------------------------
# Integration: loop runs N iterations then stops
# ---------------------------------------------------------------------------


class TestLoopIntegration:
    def _loop_wf(self, make_workflow, gate_path: str, max_iterations: int = 5):
        """Build a dev→review loop workflow with a static finalize task."""
        return make_workflow(
            [
                {"id": "develop", "outputs": ["output/impl.md"]},
                {
                    "id": "review",
                    "inputs": ["output/impl.md"],
                    "depends_on": ["develop"],
                },
                {
                    "id": "finalize",
                    "depends_on": ["dev-review-loop"],
                    "outputs": ["output/final.md"],
                },
            ],
            wf_id="loop-wf",
            loops=[
                {
                    "id": "dev-review-loop",
                    "body": ["develop", "review"],
                    "gate_task_id": "review",
                    "gate_output_path": gate_path,
                    "max_iterations": max_iterations,
                }
            ],
        )

    def test_loop_runs_until_gate_false(
        self,
        make_workflow,
        make_orchestrator,
    ) -> None:
        """Gate sequence [True, True, False] → 3 iterations total (iter1 + iter2 + iter3 stops)."""
        gate_path = "output/review-verdict.json"
        wf = self._loop_wf(make_workflow, gate_path, max_iterations=5)
        # 3 invocations: iter1 -> True, iter2 -> True, iter3 -> False
        executor = FakeExecutor(
            gate_payloads={"review": [True, True, False]},
        )
        orch, reposets, agents = make_orchestrator(executor)
        state = orch.run(wf, reposets, agents)

        assert state.status == "succeeded"
        # Iteration 1 (un-suffixed) + iter2 + iter3
        assert "review" in state.tasks
        assert "review__iter2" in state.tasks
        assert "review__iter3" in state.tasks
        # iter4 must NOT exist (gate said false at iter3)
        assert "review__iter4" not in state.tasks
        # finalize runs after loop
        assert state.tasks["finalize"].status == "succeeded"
        # Loop iterations recorded
        assert state.loop_iterations.get("dev-review-loop", 1) >= 3

    def test_loop_stops_at_max_iterations_gate_always_continue(
        self,
        make_workflow,
        make_orchestrator,
    ) -> None:
        """With gate always True and max_iterations=2, loop stops cleanly after 2 iterations."""
        gate_path = "output/review-verdict.json"
        wf = self._loop_wf(make_workflow, gate_path, max_iterations=2)
        # Gate always says continue
        executor = FakeExecutor(gate_payloads={"review": [True, True, True, True, True]})
        orch, reposets, agents = make_orchestrator(executor)
        state = orch.run(wf, reposets, agents)

        assert state.status == "succeeded"
        assert "review" in state.tasks
        assert "review__iter2" in state.tasks
        # iter3 must NOT exist (max_iterations=2 reached)
        assert "review__iter3" not in state.tasks
        assert state.tasks["finalize"].status == "succeeded"

    def test_loop_gate_false_on_first_iter_no_second_iter(
        self,
        make_workflow,
        make_orchestrator,
    ) -> None:
        """If gate says False on iteration 1, no iteration 2 is created."""
        gate_path = "output/review-verdict.json"
        wf = self._loop_wf(make_workflow, gate_path, max_iterations=5)
        executor = FakeExecutor(gate_payloads={"review": [False]})
        orch, reposets, agents = make_orchestrator(executor)
        state = orch.run(wf, reposets, agents)

        assert state.status == "succeeded"
        assert "review" in state.tasks
        assert "review__iter2" not in state.tasks
        assert state.tasks["finalize"].status == "succeeded"

    def test_loop_gate_missing_file_fails_run(
        self,
        make_workflow,
        make_orchestrator,
    ) -> None:
        """Gate file not found → GateError → run fails."""
        gate_path = "output/review-verdict.json"
        wf = self._loop_wf(make_workflow, gate_path, max_iterations=3)
        # FakeExecutor does not write gate file (no gate_payloads configured)
        executor = FakeExecutor()
        orch, reposets, agents = make_orchestrator(executor)
        state = orch.run(wf, reposets, agents)

        assert state.status == "failed"

    def test_loop_gate_missing_field_fails_run(
        self,
        make_workflow,
        make_orchestrator,
        workspace: Path,
    ) -> None:
        """Gate file with wrong field → GateError → run fails."""
        gate_path = "output/review-verdict.json"
        wf = self._loop_wf(make_workflow, gate_path, max_iterations=3)
        # Write a gate file with the wrong field before the run
        (workspace / "output").mkdir(parents=True, exist_ok=True)
        (workspace / gate_path).write_text('{"approved": true}')
        executor = FakeExecutor()
        orch, reposets, agents = make_orchestrator(executor)
        state = orch.run(wf, reposets, agents)

        assert state.status == "failed"

    def test_static_task_depends_on_loop_id(
        self,
        make_workflow,
        make_orchestrator,
    ) -> None:
        """A static task with depends_on:[<loop_id>] runs after the final iteration."""
        gate_path = "output/review-verdict.json"
        wf = self._loop_wf(make_workflow, gate_path, max_iterations=5)
        executor = FakeExecutor(gate_payloads={"review": [False]})  # 1 iteration
        orch, reposets, agents = make_orchestrator(executor)
        state = orch.run(wf, reposets, agents)

        assert state.status == "succeeded"
        # finalize depends_on: [dev-review-loop] -> must run after loop
        finalize_ts = state.tasks.get("finalize")
        assert finalize_ts is not None
        assert finalize_ts.status == "succeeded"

    def test_loop_origin_is_loop(
        self,
        make_workflow,
        make_orchestrator,
    ) -> None:
        """Cloned iteration tasks have origin='loop'."""
        gate_path = "output/review-verdict.json"
        wf = self._loop_wf(make_workflow, gate_path, max_iterations=3)
        executor = FakeExecutor(gate_payloads={"review": [True, False]})
        orch, reposets, agents = make_orchestrator(executor)
        state = orch.run(wf, reposets, agents)

        assert state.status == "succeeded"
        assert "develop__iter2" in state.tasks
        assert state.tasks["develop__iter2"].origin == "loop"
        assert state.tasks["review__iter2"].origin == "loop"

    def test_resume_mid_loop_does_not_reclone(
        self,
        make_workflow,
        make_orchestrator,
        workspace: Path,
        rs_store,
    ) -> None:
        """Resume mid-loop: loop_iterations prevents re-cloning already-materialized iterations."""
        gate_path = "output/review-verdict.json"
        wf = make_workflow(
            [
                {"id": "develop", "outputs": ["output/impl.md"]},
                {
                    "id": "review",
                    "inputs": ["output/impl.md"],
                    "depends_on": ["develop"],
                },
            ],
            wf_id="resume-loop-wf",
            loops=[
                {
                    "id": "lp",
                    "body": ["develop", "review"],
                    "gate_task_id": "review",
                    "gate_output_path": gate_path,
                    "max_iterations": 3,
                }
            ],
        )

        # Run 1: 2 iterations succeed, then fail at iter2 review
        executor1 = FakeExecutor(
            gate_payloads={"review": [True]},
            behaviors={"review__iter2": "fail"},
        )
        orch1, reposets, agents = make_orchestrator(executor1)
        state1 = orch1.run(wf, reposets, agents)
        assert state1.status == "failed"
        run_id = state1.run_id

        # Verify iter2 was injected
        assert "develop__iter2" in state1.tasks
        assert state1.loop_iterations.get("lp", 1) == 2

        # Resume: iter2 should not be re-cloned; only review__iter2 should re-run
        loaded = rs_store.load(run_id)
        import copy as _copy

        wf2 = _copy.deepcopy(wf)
        prepared = rs_store.prepare_resume(loaded, wf2)

        executor2 = FakeExecutor(gate_payloads={"review": [False]})
        orch2, _, _ = make_orchestrator(executor2)
        state2 = orch2.run(wf2, reposets, agents, run_state=prepared)

        # iter3 must NOT have been created — loop stopped at iter2 (gate said False)
        assert "review__iter3" not in state2.tasks

    def test_deterministic_loop_graph_across_runs(
        self,
        make_workflow,
        make_orchestrator,
    ) -> None:
        """Same loop spec run twice produces identical expanded task ids (NFR-2)."""
        import copy

        gate_path = "output/review-verdict.json"
        wf_orig = make_workflow(
            [
                {"id": "develop", "outputs": ["output/impl.md"]},
                {"id": "review", "inputs": ["output/impl.md"], "depends_on": ["develop"]},
            ],
            wf_id="det-loop-wf",
            loops=[
                {
                    "id": "lp",
                    "body": ["develop", "review"],
                    "gate_task_id": "review",
                    "gate_output_path": gate_path,
                    "max_iterations": 3,
                }
            ],
        )

        def run_and_get_ids(wf_copy):
            executor = FakeExecutor(gate_payloads={"review": [True, False]})
            orch, reposets, agents = make_orchestrator(executor)
            state = orch.run(wf_copy, reposets, agents)
            return sorted(state.tasks.keys())

        ids1 = run_and_get_ids(copy.deepcopy(wf_orig))
        ids2 = run_and_get_ids(copy.deepcopy(wf_orig))
        assert ids1 == ids2

    def test_post_dev_review_cycle(
        self,
        make_workflow,
        make_orchestrator,
    ) -> None:
        """Post-dev review/audit: body = [review, remediate], gate = review verdict."""
        gate_path = "output/audit-verdict.json"
        wf = make_workflow(
            [
                {"id": "audit", "depends_on": [], "outputs": []},
                {"id": "remediate", "depends_on": ["audit"], "outputs": []},
                {"id": "ship", "depends_on": ["audit-loop"], "outputs": ["output/ship.txt"]},
            ],
            wf_id="post-dev-review",
            loops=[
                {
                    "id": "audit-loop",
                    "body": ["audit", "remediate"],
                    "gate_task_id": "audit",
                    "gate_output_path": gate_path,
                    "gate_field": "continue",
                    "max_iterations": 3,
                }
            ],
        )
        # Issues exist: iter1 → True (continue), iter2 → False (clean)
        executor = FakeExecutor(gate_payloads={"audit": [True, False]})
        orch, reposets, agents = make_orchestrator(executor)
        state = orch.run(wf, reposets, agents)

        assert state.status == "succeeded"
        assert "audit" in state.tasks
        assert "audit__iter2" in state.tasks
        assert "audit__iter3" not in state.tasks
        assert state.tasks["ship"].status == "succeeded"

    def test_loop_spec_in_example_workflow_validates(self) -> None:
        """The loop example spec file validates against workflow.schema.json."""
        from pathlib import Path as P

        from agent_orchestrator.spec import load_workflow

        spec_path = P(__file__).parent.parent / "specs" / "examples" / "workflow-loop.json"
        wf = load_workflow(spec_path)
        assert len(wf.loops) == 1
        assert wf.loops[0].id == "dev-review-loop"

    def test_dynamic_spec_in_example_workflow_validates(self) -> None:
        """The dynamic example spec file validates against workflow.schema.json."""
        from pathlib import Path as P

        from agent_orchestrator.spec import load_workflow

        spec_path = P(__file__).parent.parent / "specs" / "examples" / "workflow-dynamic.json"
        wf = load_workflow(spec_path)
        emitter = wf.task("discover")
        assert emitter.emit_tasks is True
        assert emitter.task_manifest_path == "output/dynamic-manifest.json"
