"""T-eyn5UG AC-10 (U-OC1): `outcomes._settle_reason` reports "cached" for a current hit."""

from __future__ import annotations

import sys
from pathlib import Path

from agent_orchestrator.artifacts import LocalFsArtifactStore
from agent_orchestrator.models import HookSpec, TaskSpec, WorkflowSpec
from agent_orchestrator.outcomes import _settle_reason, grade_run
from tests.cache._report_states import hit_rec, mixed_state, run_state, settled


class TestSettleReason:
    def test_current_hit_is_cached(self) -> None:
        state = mixed_state()
        assert _settle_reason(state.tasks["hit"], state=state, tid="hit") == "cached"

    def test_stale_hits_are_dispatched(self) -> None:
        state = mixed_state()
        for tid in ("stale_cycle", "stale_ended"):
            assert _settle_reason(state.tasks[tid], state=state, tid=tid) == "dispatched"

    def test_normal_success_with_records_present_is_dispatched(self) -> None:
        state = mixed_state()
        assert _settle_reason(state.tasks["miss"], state=state, tid="miss") == "dispatched"

    def test_without_state_the_behaviour_is_unchanged(self) -> None:
        state = mixed_state()
        assert _settle_reason(state.tasks["hit"]) == "dispatched"
        assert _settle_reason(settled(status="skipped")) == "skipped"
        assert _settle_reason(settled(), state=state) == "dispatched"  # tid is required too

    def test_empty_map_is_dispatched_and_skipped_as_before(self) -> None:
        state = run_state({"a": settled(), "b": settled(status="skipped")})
        assert _settle_reason(state.tasks["a"], state=state, tid="a") == "dispatched"
        assert _settle_reason(state.tasks["b"], state=state, tid="b") == "skipped"


class TestGradeRunReportsCached:
    def test_grade_run_rows_carry_cached_for_a_hit_only(self, tmp_path: Path) -> None:
        (tmp_path / "i.md").write_text("x")
        tasks = [
            TaskSpec(id=tid, agent="a", instruction="i.md", outputs=[]) for tid in ("hit", "miss")
        ]
        workflow = WorkflowSpec(
            version="1.0",
            id="wf",
            repo_set="rs",
            tasks=tasks,
            hooks={
                "g": HookSpec(
                    command=[sys.executable, "-c", "import sys; sys.exit(0)"], timeout_seconds=30
                )
            },
        )
        state = run_state({"hit": settled(), "miss": settled()}, {"hit": hit_rec()})
        grades = grade_run(state, workflow, "g", LocalFsArtifactStore(str(tmp_path)), str(tmp_path))
        reasons = {g.task_id: g.settle_reason for g in grades}
        assert reasons == {"hit": "cached", "miss": "dispatched"}
