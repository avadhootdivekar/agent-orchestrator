"""Tests for Feature Area 2a — dynamic task injection (emit_tasks).

Covers T-5isej3 acceptance criteria 1, 4, 5, 6 and HLD §6.3 edge cases.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_orchestrator.artifacts import LocalFsArtifactStore, read_task_manifest
from agent_orchestrator.errors import CycleError, SpecValidationError
from agent_orchestrator.executors.fake import FakeExecutor
from agent_orchestrator.models import TaskSpec
from agent_orchestrator.spec import cross_validate

# ---------------------------------------------------------------------------
# Unit: read_task_manifest
# ---------------------------------------------------------------------------


class TestReadTaskManifest:
    def test_happy_path_returns_task_specs(self, tmp_path: Path) -> None:
        store = LocalFsArtifactStore(str(tmp_path))
        manifest = tmp_path / "manifest.json"
        manifest.write_text(
            json.dumps(
                {
                    "tasks": [
                        {
                            "id": "injected-a",
                            "agent": "ag",
                            "instruction": "specs/i.md",
                        }
                    ]
                }
            )
        )
        tasks = read_task_manifest(store, "manifest.json")
        assert len(tasks) == 1
        assert tasks[0].id == "injected-a"
        assert tasks[0].agent == "ag"

    def test_missing_file_raises_value_error(self, tmp_path: Path) -> None:
        store = LocalFsArtifactStore(str(tmp_path))
        with pytest.raises(ValueError, match="not found"):
            read_task_manifest(store, "no-such-manifest.json")

    def test_not_a_dict_raises_value_error(self, tmp_path: Path) -> None:
        store = LocalFsArtifactStore(str(tmp_path))
        (tmp_path / "m.json").write_text("[1, 2, 3]")
        with pytest.raises(ValueError, match=r'\{"tasks"'):
            read_task_manifest(store, "m.json")

    def test_missing_tasks_key_raises_value_error(self, tmp_path: Path) -> None:
        store = LocalFsArtifactStore(str(tmp_path))
        (tmp_path / "m.json").write_text('{"artifacts": []}')
        with pytest.raises(ValueError, match=r'\{"tasks"'):
            read_task_manifest(store, "m.json")

    def test_invalid_task_spec_raises_value_error(self, tmp_path: Path) -> None:
        store = LocalFsArtifactStore(str(tmp_path))
        # Missing required 'instruction' field
        (tmp_path / "m.json").write_text(json.dumps({"tasks": [{"id": "x", "agent": "ag"}]}))
        with pytest.raises(ValueError, match="invalid TaskSpec"):
            read_task_manifest(store, "m.json")

    def test_invalid_json_raises_value_error(self, tmp_path: Path) -> None:
        store = LocalFsArtifactStore(str(tmp_path))
        (tmp_path / "m.json").write_text("{not valid json")
        with pytest.raises(ValueError, match="not valid JSON"):
            read_task_manifest(store, "m.json")

    def test_multiple_tasks_returned(self, tmp_path: Path) -> None:
        store = LocalFsArtifactStore(str(tmp_path))
        (tmp_path / "m.json").write_text(
            json.dumps(
                {
                    "tasks": [
                        {"id": "a", "agent": "ag", "instruction": "i.md"},
                        {"id": "b", "agent": "ag", "instruction": "i.md"},
                    ]
                }
            )
        )
        tasks = read_task_manifest(store, "m.json")
        assert [t.id for t in tasks] == ["a", "b"]


# ---------------------------------------------------------------------------
# Unit: cross_validate emit_tasks / task_manifest_path constraints
# ---------------------------------------------------------------------------


class TestCrossValidateEmitTasks:
    def _minimal_wf(self, tasks: list[dict], workspace: Path):
        from agent_orchestrator.models import WorkflowDefaults, WorkflowSpec

        instr_dir = workspace / "specs" / "instructions"
        instr_dir.mkdir(parents=True, exist_ok=True)
        task_specs = []
        for t in tasks:
            instr_path = f"specs/instructions/{t['id']}.md"
            (workspace / instr_path).write_text(f"instruction for {t['id']}")
            task_specs.append(TaskSpec(**t))
        return WorkflowSpec(
            version="1.0",
            id="wf",
            repo_set="rs",
            tasks=task_specs,
            defaults=WorkflowDefaults(),
        )

    def test_emit_tasks_without_manifest_path_raises(self, tmp_path: Path) -> None:
        wf = self._minimal_wf(
            [
                {
                    "id": "emitter",
                    "agent": "ag",
                    "instruction": "specs/instructions/emitter.md",
                    "emit_tasks": True,
                }
            ],
            tmp_path,
        )
        with pytest.raises(SpecValidationError, match="task_manifest_path"):
            cross_validate(wf, {"rs": object()}, {"ag": object()})

    def test_manifest_path_without_emit_tasks_raises(self, tmp_path: Path) -> None:
        wf = self._minimal_wf(
            [
                {
                    "id": "t",
                    "agent": "ag",
                    "instruction": "specs/instructions/t.md",
                    "task_manifest_path": "output/m.json",
                }
            ],
            tmp_path,
        )
        with pytest.raises(SpecValidationError, match="emit_tasks"):
            cross_validate(wf, {"rs": object()}, {"ag": object()})

    def test_both_set_passes(self, tmp_path: Path) -> None:
        wf = self._minimal_wf(
            [
                {
                    "id": "emitter",
                    "agent": "ag",
                    "instruction": "specs/instructions/emitter.md",
                    "emit_tasks": True,
                    "task_manifest_path": "output/m.json",
                }
            ],
            tmp_path,
        )
        # No exception raised
        cross_validate(wf, {"rs": object()}, {"ag": object()})

    def test_neither_set_passes(self, tmp_path: Path) -> None:
        wf = self._minimal_wf(
            [{"id": "t", "agent": "ag", "instruction": "specs/instructions/t.md"}],
            tmp_path,
        )
        cross_validate(wf, {"rs": object()}, {"ag": object()})

    def test_reserved_iter_suffix_in_task_id_raises(self, tmp_path: Path) -> None:
        """Authored task ids must not contain __iter (reserved for loop cloning)."""
        from agent_orchestrator.models import WorkflowSpec

        instr_dir = tmp_path / "specs" / "instructions"
        instr_dir.mkdir(parents=True, exist_ok=True)
        instr_path = "specs/instructions/t__iter2.md"
        (tmp_path / instr_path).write_text("instr")
        task = TaskSpec(id="t__iter2", agent="ag", instruction=instr_path)
        wf = WorkflowSpec(version="1.0", id="wf", repo_set="rs", tasks=[task])
        with pytest.raises(SpecValidationError, match="__iter"):
            cross_validate(wf, {"rs": object()}, {"ag": object()})


# ---------------------------------------------------------------------------
# Integration: emit_tasks run injects + completes
# ---------------------------------------------------------------------------


class TestEmitTasksIntegration:
    def test_emit_injects_and_completes(
        self,
        make_workflow,
        make_orchestrator,
        workspace: Path,
    ) -> None:
        """An emit_tasks task injects new tasks; both the emitter and injected tasks succeed."""
        manifest_path = "output/task-manifest.json"
        injected_output = "output/injected.txt"

        # Build a workflow: emitter + a static downstream
        wf = make_workflow(
            [
                {
                    "id": "emitter",
                    "emit_tasks": True,
                    "task_manifest_path": manifest_path,
                    "outputs": [],
                },
            ],
            wf_id="dynamic-wf",
        )

        # FakeExecutor: on success of "emitter", write the task manifest
        emitted_task = {
            "id": "injected-a",
            "agent": "ag",
            "instruction": "specs/instructions/emitter.md",  # reuse existing stub
            "outputs": [injected_output],
            "depends_on": ["emitter"],
        }
        executor = FakeExecutor(
            emit_payloads={"emitter": {"tasks": [emitted_task]}},
        )
        orch, reposets, agents = make_orchestrator(executor)
        state = orch.run(wf, reposets, agents)

        assert state.status == "succeeded"
        assert state.tasks["emitter"].status == "succeeded"
        assert "injected-a" in state.tasks
        assert state.tasks["injected-a"].status == "succeeded"
        assert state.tasks["injected-a"].origin == "injected"
        assert (workspace / injected_output).exists()
        # Injected task recorded in RunState
        assert any(t.id == "injected-a" for t in state.injected_tasks)

    def test_duplicate_injected_id_fails_run(
        self,
        make_workflow,
        make_orchestrator,
    ) -> None:
        """Injecting a task with an id that already exists fails the run (NFR-6)."""
        manifest_path = "output/dup-manifest.json"
        wf = make_workflow(
            [
                {
                    "id": "emitter",
                    "emit_tasks": True,
                    "task_manifest_path": manifest_path,
                },
            ]
        )
        # Emit a task with the same id as an existing task
        executor = FakeExecutor(
            emit_payloads={
                "emitter": {
                    "tasks": [
                        {
                            "id": "emitter",  # duplicate!
                            "agent": "ag",
                            "instruction": "specs/instructions/emitter.md",
                        }
                    ]
                }
            }
        )
        orch, reposets, agents = make_orchestrator(executor)
        state = orch.run(wf, reposets, agents)
        assert state.status == "failed"
        assert state.tasks["emitter"].status == "failed"

    def test_cyclic_injection_raises_cycle_error(
        self,
        make_workflow,
        make_orchestrator,
    ) -> None:
        """Injected tasks that form a cycle cause CycleError and fail the run."""
        manifest_path = "output/cycle-manifest.json"
        wf = make_workflow(
            [
                {
                    "id": "emitter",
                    "emit_tasks": True,
                    "task_manifest_path": manifest_path,
                }
            ]
        )
        # Inject two tasks that form a cycle
        executor = FakeExecutor(
            emit_payloads={
                "emitter": {
                    "tasks": [
                        {
                            "id": "cycle-a",
                            "agent": "ag",
                            "instruction": "specs/instructions/emitter.md",
                            "depends_on": ["cycle-b"],
                        },
                        {
                            "id": "cycle-b",
                            "agent": "ag",
                            "instruction": "specs/instructions/emitter.md",
                            "depends_on": ["cycle-a"],
                        },
                    ]
                }
            }
        )
        orch, reposets, agents = make_orchestrator(executor)
        with pytest.raises(CycleError):
            orch.run(wf, reposets, agents)

    def test_malformed_manifest_fails_emitter(
        self,
        make_workflow,
        make_orchestrator,
        workspace: Path,
    ) -> None:
        """A malformed task manifest causes the emitter task to fail."""
        manifest_path = "output/bad-manifest.json"
        wf = make_workflow(
            [
                {
                    "id": "emitter",
                    "emit_tasks": True,
                    "task_manifest_path": manifest_path,
                }
            ]
        )
        # Write a bad manifest file before the run
        (workspace / manifest_path).parent.mkdir(parents=True, exist_ok=True)
        (workspace / manifest_path).write_text('{"wrong_key": []}')

        # FakeExecutor won't write the emit_payload (not configured), so the
        # manifest at manifest_path will be the bad one we pre-wrote.
        # But FakeExecutor emit_payloads writes OVER the file — we need to NOT
        # configure emit_payloads and instead write the bad file in the test.
        # The issue: FakeExecutor writes the file AFTER succeed; the pre-written file
        # will be the bad manifest if emit_payloads is not set.
        executor = FakeExecutor()  # no emit_payloads -> file stays as bad
        orch, reposets, agents = make_orchestrator(executor)
        state = orch.run(wf, reposets, agents)
        assert state.status == "failed"
        assert state.tasks["emitter"].status == "failed"

    def test_resume_after_injection_skips_emitter_reruns_injected(
        self,
        make_workflow,
        make_orchestrator,
        workspace: Path,
        rs_store,
    ) -> None:
        """Resume after partial injection: emitter skipped, incomplete injected tasks re-run."""
        manifest_path = "output/resume-manifest.json"
        injected_out = "output/injected-out.txt"

        wf = make_workflow(
            [
                {
                    "id": "emitter",
                    "emit_tasks": True,
                    "task_manifest_path": manifest_path,
                    "outputs": ["output/emitter.txt"],
                },
            ],
            wf_id="resume-wf",
        )

        emitted_task = {
            "id": "injected-b",
            "agent": "ag",
            "instruction": "specs/instructions/emitter.md",
            "outputs": [injected_out],
            "depends_on": ["emitter"],
        }

        # First run: emitter succeeds, injected-b fails
        executor1 = FakeExecutor(
            emit_payloads={"emitter": {"tasks": [emitted_task]}},
            behaviors={"injected-b": "fail"},
        )
        orch1, reposets, agents = make_orchestrator(executor1)
        state1 = orch1.run(wf, reposets, agents)
        assert state1.status == "failed"
        assert state1.tasks["emitter"].status == "succeeded"
        assert state1.tasks["injected-b"].status == "failed"
        run_id = state1.run_id

        # Resume: emitter should be skipped; injected-b should succeed
        loaded = rs_store.load(run_id)
        # prepare_resume will merge injected_tasks back into wf
        prepared = rs_store.prepare_resume(loaded, wf)
        assert prepared.tasks["emitter"].status in ("succeeded", "skipped")

        executor2 = FakeExecutor()
        orch2, _, _ = make_orchestrator(executor2)
        state2 = orch2.run(wf, reposets, agents, run_state=prepared)

        assert state2.status == "succeeded"
        assert state2.tasks["emitter"].status in ("succeeded", "skipped")
        assert state2.tasks["injected-b"].status == "succeeded"

    def test_deterministic_expanded_ids(
        self,
        make_workflow,
        make_orchestrator,
        workspace: Path,
    ) -> None:
        """Same spec + manifest run twice under fixed clock yields identical expanded ids."""
        import copy

        manifest_path = "output/det-manifest.json"
        wf_orig = make_workflow(
            [{"id": "emitter", "emit_tasks": True, "task_manifest_path": manifest_path}],
            wf_id="det-wf",
        )

        emitted_task = {
            "id": "det-injected",
            "agent": "ag",
            "instruction": "specs/instructions/emitter.md",
        }
        executor = FakeExecutor(
            emit_payloads={"emitter": {"tasks": [emitted_task]}},
        )
        orch, reposets, agents = make_orchestrator(executor)

        # Run 1
        wf1 = copy.deepcopy(wf_orig)
        state1 = orch.run(wf1, reposets, agents)
        ids1 = sorted(state1.tasks.keys())

        # Run 2 — use a fresh copy so we don't carry injected state from run 1
        wf2 = copy.deepcopy(wf_orig)
        executor2 = FakeExecutor(
            emit_payloads={"emitter": {"tasks": [emitted_task]}},
        )
        orch2, _, _ = make_orchestrator(executor2)
        state2 = orch2.run(wf2, reposets, agents)
        ids2 = sorted(state2.tasks.keys())

        assert ids1 == ids2

    def test_engine_never_reads_payload_content_static_check(self) -> None:
        """NFR-1 static audit: engine.py must not call open() or .read() on artifact paths.

        All control-file reads (task manifest, gate, output manifest) must go through
        artifacts.read_task_manifest / read_gate / read_manifest — never direct open().
        This mirrors the existing NFR-1 check in test_integration.py.
        """
        import re
        from pathlib import Path as P

        engine_src = (
            P(__file__).parent.parent / "src" / "agent_orchestrator" / "engine.py"
        ).read_text()

        open_calls = re.findall(r"\bopen\s*\(", engine_src)
        assert not open_calls, f"engine.py contains open() call(s): {open_calls}"

        read_calls = re.findall(r"\.read\s*\(", engine_src)
        assert not read_calls, f"engine.py contains .read() call(s): {read_calls}"

        # Verify control-read helpers are used (not direct json.load)
        assert "read_task_manifest" in engine_src
        assert "read_gate" in engine_src

    def test_nested_emission_depth_2(
        self,
        make_workflow,
        make_orchestrator,
        workspace: Path,
    ) -> None:
        """Nested emission: static emitter A emits B (with emit_tasks), B emits leaf C.

        Verifies that the engine's dynamic-expansion hook fires for ANY succeeded task
        with emit_tasks=true, including injected tasks. This unblocks nested fan-out
        patterns (e.g. planner → phase-decomposer → tasks).

        Acceptance Criteria:
        - C executes and completes (origin="injected")
        - state.injected_tasks contains both B and C (injection depth 2)
        - The expansion hook fired for injected task B, not only static A

        Constraint (memory `skipped-emit-task-never-injects`):
        - Both A and B must have skip_if_outputs_exist: false, or they skip and never inject
        """
        manifest_a_path = "output/manifest-a.json"
        manifest_b_path = "output/manifest-b.json"
        output_c = "output/c.txt"

        # Static emitter A (skip_if_outputs_exist: false) will emit B
        wf = make_workflow(
            [
                {
                    "id": "emitter-a",
                    "emit_tasks": True,
                    "task_manifest_path": manifest_a_path,
                    "skip_if_outputs_exist": False,
                }
            ],
            wf_id="nested-emit-wf",
        )

        # B is also an emitter (skip_if_outputs_exist: false) that will emit C
        # Reuse existing stub instruction (created by make_workflow for emitter-a)
        emitter_b_spec = {
            "id": "emitter-b",
            "agent": "ag",
            "instruction": "specs/instructions/emitter-a.md",  # reuse existing stub
            "emit_tasks": True,
            "task_manifest_path": manifest_b_path,
            "skip_if_outputs_exist": False,
            "depends_on": ["emitter-a"],
        }

        # C is the leaf task (emitted by B)
        leaf_c_spec = {
            "id": "leaf-c",
            "agent": "ag",
            "instruction": "specs/instructions/emitter-a.md",  # reuse existing stub
            "outputs": [output_c],
            "depends_on": ["emitter-b"],
        }

        # FakeExecutor writes manifests at the right time:
        # - When A succeeds, write B's spec to manifest_a_path
        # - When B succeeds, write C's spec to manifest_b_path
        executor = FakeExecutor(
            emit_payloads={
                "emitter-a": {"tasks": [emitter_b_spec]},
                "emitter-b": {"tasks": [leaf_c_spec]},
            }
        )
        orch, reposets, agents = make_orchestrator(executor)
        state = orch.run(wf, reposets, agents)

        # Assertions
        assert state.status == "succeeded", f"Expected succeeded, got {state.status}"
        assert state.tasks["emitter-a"].status == "succeeded"
        assert state.tasks["emitter-b"].status == "succeeded"
        assert state.tasks["leaf-c"].status == "succeeded"

        # Verify origins: A is static, B and C are injected
        assert state.tasks["emitter-a"].origin == "static"
        assert state.tasks["emitter-b"].origin == "injected"
        assert state.tasks["leaf-c"].origin == "injected"

        # Verify outputs: C wrote its output
        assert (workspace / output_c).exists()

        # Verify injected_tasks contains both B and C (depth 2)
        injected_ids = {t.id for t in state.injected_tasks}
        assert injected_ids == {"emitter-b", "leaf-c"}, (
            f"Expected emitter-b and leaf-c in injected_tasks, got {injected_ids}"
        )

        # Verify expansion hook fired for injected task B (not only static A)
        # Manifests are read in order: A's manifest -> B injected -> B's manifest -> C injected
        # This is the key assertion that nested emission works.
        task_count = len(state.tasks)
        assert task_count == 3, f"Expected 3 tasks total (A, B, C), got {task_count}"
