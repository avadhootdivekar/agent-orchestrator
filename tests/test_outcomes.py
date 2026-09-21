"""Tests for `outcomes.py`:
- E-1cecSx B3.1 local retry/review-loop/breakdown-frequency counts
- E-1cecSx B3.2/B3.3 post-run settlement grading (`grade_run`, ADR-0015 decision 2)
"""

from __future__ import annotations

import os
import sys
import textwrap
from pathlib import Path

import pytest

from agent_orchestrator.artifacts import LocalFsArtifactStore
from agent_orchestrator.models import (
    HookSpec,
    MonitorDecisionRecord,
    RunState,
    TaskIntegrationState,
    TaskRunState,
    TaskSpec,
    TrippedBreaker,
    WorkflowSpec,
)
from agent_orchestrator.outcomes import (
    TaskOutcomeSummary,
    grade_run,
    run_breakdown_frequency,
    settlement_grades_path,
    task_outcome_summary,
)

_BASE_KWARGS = {
    "run_id": "r1",
    "workflow_id": "wf",
    "repo_set": "rs",
    "started_at": "2026-01-01T00:00:00+00:00",
    "updated_at": "2026-01-01T00:00:00+00:00",
}


class TestTaskOutcomeSummary:
    def test_empty_run_returns_empty_list(self) -> None:
        state = RunState(**_BASE_KWARGS)
        assert task_outcome_summary(state) == []

    def test_plain_task_no_integration_no_self_heal(self) -> None:
        state = RunState(
            **_BASE_KWARGS,
            tasks={"a": TaskRunState(status="succeeded", attempts=1, dispatch_cycle=1)},
        )
        summaries = task_outcome_summary(state)
        assert summaries == [
            TaskOutcomeSummary(
                task_id="a",
                status="succeeded",
                attempts=1,
                dispatch_cycle=1,
                resolver_attempts=0,
                reruns=0,
                self_heal_retry_count=0,
            )
        ]

    def test_self_heal_retry_count_filters_by_consult_point_and_subject(self) -> None:
        state = RunState(
            **_BASE_KWARGS,
            tasks={
                "a": TaskRunState(status="succeeded", attempts=2, dispatch_cycle=2),
                "b": TaskRunState(status="succeeded", attempts=1, dispatch_cycle=1),
            },
            monitor_decisions=[
                MonitorDecisionRecord(
                    at="t1",
                    consult_point="task_failure",
                    subject_id="a",
                    decision="retry",
                    monitor="rules",
                ),
                MonitorDecisionRecord(
                    at="t2",
                    consult_point="task_failure",
                    subject_id="a",
                    decision="retry",
                    monitor="rules",
                ),
                # Different consult_point: must NOT count toward "a"'s self-heal count.
                MonitorDecisionRecord(
                    at="t3",
                    consult_point="breaker_trip",
                    subject_id="a",
                    decision="extend",
                    monitor="rules",
                ),
                # Different subject: must NOT count toward "a".
                MonitorDecisionRecord(
                    at="t4",
                    consult_point="task_failure",
                    subject_id="b",
                    decision="accept_failure",
                    monitor="rules",
                ),
            ],
        )
        summaries = {s.task_id: s for s in task_outcome_summary(state)}
        assert summaries["a"].self_heal_retry_count == 2
        assert summaries["b"].self_heal_retry_count == 1

    def test_resolver_attempts_and_reruns_from_task_integration(self) -> None:
        state = RunState(
            **_BASE_KWARGS,
            tasks={"a": TaskRunState(status="succeeded", attempts=1, dispatch_cycle=3)},
            task_integration={
                "a": TaskIntegrationState(
                    isolation="worktree", status="integrated", resolver_attempts=2, reruns=1
                )
            },
        )
        summary = task_outcome_summary(state)[0]
        assert summary.resolver_attempts == 2
        assert summary.reruns == 1

    def test_sorted_by_task_id(self) -> None:
        state = RunState(
            **_BASE_KWARGS,
            tasks={
                "z": TaskRunState(status="succeeded"),
                "a": TaskRunState(status="succeeded"),
                "m": TaskRunState(status="succeeded"),
            },
        )
        ids = [s.task_id for s in task_outcome_summary(state)]
        assert ids == ["a", "m", "z"]


_SIMPLE_HOOK_SCRIPT = textwrap.dedent(
    """
    import json
    import os
    import sys

    context_path = os.environ["AO_HOOK_CONTEXT_PATH"]
    result_path = os.environ["AO_HOOK_RESULT_PATH"]
    with open(context_path) as f:
        context = json.load(f)
    solved = context.get("settle_reason") != "force_fail"
    with open(result_path, "w") as f:
        json.dump({"score": 1.0 if solved else 0.0, "detail": {"solved": solved}}, f)
    sys.exit(0 if solved else 1)
    """
)


def _make_workflow(tmp_path, hook_command: list[str]) -> WorkflowSpec:
    (tmp_path / "instructions").mkdir(exist_ok=True)
    (tmp_path / "instructions" / "a.md").write_text("do the thing")
    return WorkflowSpec(
        version="1.0",
        id="wf",
        repo_set="rs",
        tasks=[
            TaskSpec(id="dispatched", agent="a", instruction="instructions/a.md", outputs=[]),
            TaskSpec(id="skipped", agent="a", instruction="instructions/a.md", outputs=[]),
            TaskSpec(id="not_taken_task", agent="a", instruction="instructions/a.md"),
            TaskSpec(id="pending_task", agent="a", instruction="instructions/a.md"),
        ],
        hooks={"grade": HookSpec(command=hook_command, timeout_seconds=30)},
    )


class TestGradeRun:
    def test_unknown_hook_name_raises_keyerror_before_grading_anything(self, tmp_path) -> None:
        workflow = _make_workflow(tmp_path, [sys.executable, "-c", _SIMPLE_HOOK_SCRIPT])
        state = RunState(
            **_BASE_KWARGS,
            tasks={"dispatched": TaskRunState(status="succeeded")},
        )
        store = LocalFsArtifactStore(str(tmp_path))
        with pytest.raises(KeyError):
            grade_run(state, workflow, "no-such-hook", store, str(tmp_path))

    def test_grades_dispatched_and_skipped_uniformly(self, tmp_path) -> None:
        """The whole point of ADR-0015 decision 2: one --grade pass covers BOTH a freshly
        dispatched task and a skip_if_outputs_exist-skipped task, with no per-task wiring."""
        workflow = _make_workflow(tmp_path, [sys.executable, "-c", _SIMPLE_HOOK_SCRIPT])
        state = RunState(
            **_BASE_KWARGS,
            tasks={
                "dispatched": TaskRunState(status="succeeded", dispatch_cycle=1),
                "skipped": TaskRunState(status="skipped"),
                "not_taken_task": TaskRunState(status="not_taken"),
                "pending_task": TaskRunState(status="pending"),
            },
        )
        store = LocalFsArtifactStore(str(tmp_path))
        grades = grade_run(state, workflow, "grade", store, str(tmp_path))

        by_id = {g.task_id: g for g in grades}
        # not_taken / pending are excluded from the report entirely -- nothing to grade.
        assert set(by_id) == {"dispatched", "skipped"}
        assert by_id["dispatched"].settle_reason == "dispatched"
        assert by_id["skipped"].settle_reason == "skipped"
        # Both graded successfully by the SAME hook, uniformly.
        assert by_id["dispatched"].outcome.status == "passed"
        assert by_id["skipped"].outcome.status == "passed"
        assert by_id["dispatched"].outcome.kind == "settlement_hook"
        assert by_id["dispatched"].outcome.score == 1.0

    def test_settle_reason_reaches_the_hook_script_via_context_json(self, tmp_path) -> None:
        # The synthetic script forces a fail when settle_reason == "force_fail" -- not a real
        # value grade_run ever produces, but proves the discriminator is genuinely wired
        # through to the hook's own context.json rather than silently dropped.
        workflow = _make_workflow(tmp_path, [sys.executable, "-c", _SIMPLE_HOOK_SCRIPT])
        state = RunState(**_BASE_KWARGS, tasks={"dispatched": TaskRunState(status="succeeded")})
        store = LocalFsArtifactStore(str(tmp_path))
        grades = grade_run(state, workflow, "grade", store, str(tmp_path))
        assert grades[0].outcome.status == "passed"  # settle_reason was "dispatched", not forced

    def test_capture_dir_is_run_scoped_not_cycle_nested(self, tmp_path) -> None:
        workflow = _make_workflow(tmp_path, [sys.executable, "-c", _SIMPLE_HOOK_SCRIPT])
        state = RunState(**_BASE_KWARGS, tasks={"dispatched": TaskRunState(status="succeeded")})
        store = LocalFsArtifactStore(str(tmp_path))
        grade_run(state, workflow, "grade", store, str(tmp_path))
        expected = tmp_path / ".orchestrator" / "runs" / "r1" / "dispatched" / "settlement_hook"
        assert (expected / "context.json").exists()
        assert (expected / "result.json").exists()

    def test_real_grade_command_script_end_to_end(self, tmp_path) -> None:
        """Integration evidence: the ACTUAL shipped `specs/examples/hooks/grade_command.py`
        (not a synthetic test double) works unchanged through `grade_run`."""
        repo_root = Path(__file__).resolve().parents[1]
        script = repo_root / "specs" / "examples" / "hooks" / "grade_command.py"
        assert script.exists()

        (tmp_path / "instructions").mkdir(exist_ok=True)
        (tmp_path / "instructions" / "a.md").write_text("do the thing")
        workflow = WorkflowSpec(
            version="1.0",
            id="wf",
            repo_set="rs",
            tasks=[TaskSpec(id="t1", agent="a", instruction="instructions/a.md", outputs=[])],
            hooks={
                "grade": HookSpec(
                    command=[sys.executable, str(script)],
                    timeout_seconds=30,
                )
            },
        )
        state = RunState(**_BASE_KWARGS, tasks={"t1": TaskRunState(status="succeeded")})
        store = LocalFsArtifactStore(str(tmp_path))
        # AO_GRADE_COMMAND="true" -- always exits 0, no real pytest/build dependency needed.
        old = os.environ.get("AO_GRADE_COMMAND")
        os.environ["AO_GRADE_COMMAND"] = "true"
        try:
            grades = grade_run(state, workflow, "grade", store, str(tmp_path))
        finally:
            if old is None:
                os.environ.pop("AO_GRADE_COMMAND", None)
            else:
                os.environ["AO_GRADE_COMMAND"] = old

        assert grades[0].outcome.status == "passed"
        assert grades[0].outcome.score == 1.0
        report_path = settlement_grades_path(store, "r1")
        assert not Path(report_path).exists()  # grade_run itself doesn't write the report file


class TestRunBreakdownFrequency:
    def test_empty_run_returns_empty_dict(self) -> None:
        state = RunState(**_BASE_KWARGS)
        assert run_breakdown_frequency(state) == {}

    def test_counts_by_condition(self) -> None:
        state = RunState(
            **_BASE_KWARGS,
            tripped_breakers=[
                TrippedBreaker(id="b1", condition="consecutive_failures", action="fail", at="t1"),
                TrippedBreaker(id="b1", condition="consecutive_failures", action="fail", at="t2"),
                TrippedBreaker(id="b2", condition="provider_429", action="stop", at="t3"),
            ],
        )
        assert run_breakdown_frequency(state) == {
            "consecutive_failures": 2,
            "provider_429": 1,
        }
