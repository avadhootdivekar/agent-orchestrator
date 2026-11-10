"""T-Rk4Wn7 D3 regression: a failed task must not strand independent ready work."""

from __future__ import annotations

from tests.test_engine import (
    FakeExecutor,
    Orchestrator,
    WorkflowSpec,
    _fake_agents,
    _fake_reposets,
    _make_workspace,
    _task,
    _workflow,
)


class TestFailureDoesNotStrandIndependentWork:
    """T-Rk4Wn7 D3: a failed task fails only its dependents; queued independent ready tasks
    (more ready tasks than ``max_parallel``) keep dispatching, and the run ends ``failed``
    only once no runnable work is left."""

    def _wf(self) -> WorkflowSpec:
        return _workflow(
            [
                _task("a"),  # fails
                _task("a2", depends_on=["a"]),  # dependent of the failure -> never runs
                _task("b"),
                _task("c"),
                _task("d"),
                _task("e"),
            ]
        )

    def test_independent_queued_tasks_dispatch_after_sibling_failure(self, tmp_path) -> None:
        store, rs_store = _make_workspace(tmp_path)
        orch = Orchestrator(FakeExecutor(behaviors={"a": "fail"}), store, rs_store, max_parallel=2)
        state = orch.run(self._wf(), _fake_reposets(str(tmp_path)), _fake_agents())

        assert state.tasks["a"].status == "failed"
        for tid in ("b", "c", "d", "e"):
            assert state.tasks[tid].status == "succeeded", tid
        # only the dependent is left unrun
        assert state.tasks["a2"].status in ("pending", "not_taken")
        assert state.status == "failed"

    def test_resume_reruns_failed_and_its_dependent_only(self, tmp_path) -> None:
        store, rs_store = _make_workspace(tmp_path)
        wf = self._wf()
        orch = Orchestrator(FakeExecutor(behaviors={"a": "fail"}), store, rs_store, max_parallel=2)
        state = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents())
        existing = rs_store.prepare_resume(rs_store.load(state.run_id), wf)
        state2 = Orchestrator(FakeExecutor(), store, rs_store, max_parallel=2).run(
            wf, _fake_reposets(str(tmp_path)), _fake_agents(), run_state=existing
        )
        assert state2.status == "succeeded"
        assert state2.tasks["a2"].status == "succeeded"
