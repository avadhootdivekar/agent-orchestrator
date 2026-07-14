"""CliRunner end-to-end tests for `ao resume --extend-breaker` (T-nVWE1W, epic E-3JTmVu).

Covers TASK.md acceptance criteria 1-5:
  1. Unknown --extend-breaker id -> exit 1, clear error.
  2. --extend-by-seconds AND --extend-by-same together -> exit 1.
  3. --extend-breaker given but neither extend flag given -> exit 1.
  3b. An extend flag given without --extend-breaker -> exit 1 (defensive; not explicitly
      required by TASK.md but a natural companion validation).
  4. --extend-breaker omitted -> resume behaves exactly as before (already covered by the
     pre-existing tests/test_resume_replay.py CLI suite, which stays green unmodified --
     see that file's TestE2EResumeReplayRouting).
  5. Full mechanism through the real CLI: a `run_wall_clock_seconds` breaker that already
     recorded a trip (manufactured RunState -- same convention as
     tests/test_resume_replay.py's TestInjectedTaskCountBreakerResume, since the CLI's
     `resume` has no way to inject a fake/stepping clock) is extended via `ao resume
     --extend-breaker ... --extend-by-seconds/--extend-by-same`; the run then proceeds past
     the point it previously stopped, the confirmation line is printed, and a SEPARATE
     manufactured scenario proves the SAME mechanism trips again once the effective
     (extended) threshold is also exceeded.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from typer.testing import CliRunner

from agent_orchestrator.artifacts import LocalFsArtifactStore
from agent_orchestrator.cli import app
from agent_orchestrator.models import RunState, TaskRunState, TrippedBreaker
from agent_orchestrator.runstate import RunStateStore

runner = CliRunner()


def _write_specs(tmp_path: Path, *, threshold: float) -> tuple[Path, Path, Path]:
    wf_path = tmp_path / "workflow.json"
    wf_path.write_text(
        json.dumps(
            {
                "version": "1.0",
                "id": "extend-wf",
                "repo_set": "rs",
                "tasks": [
                    {
                        "id": "a",
                        "agent": "ag",
                        "instruction": "specs/examples/instructions/design.md",
                        "outputs": ["out/a.txt"],
                    },
                    {
                        "id": "b",
                        "agent": "ag",
                        "instruction": "specs/examples/instructions/design.md",
                        "depends_on": ["a"],
                        "outputs": ["out/b.txt"],
                    },
                    {
                        "id": "c",
                        "agent": "ag",
                        "instruction": "specs/examples/instructions/design.md",
                        "depends_on": ["b"],
                        "outputs": ["out/c.txt"],
                    },
                ],
                "circuit_breakers": [
                    {
                        "id": "wallclock",
                        "condition": "run_wall_clock_seconds",
                        "action": "stop",
                        "threshold": threshold,
                    }
                ],
            }
        )
    )
    rs_path = tmp_path / "reposets.json"
    rs_path.write_text(
        json.dumps(
            {
                "version": "1.0",
                "repo_sets": {
                    "rs": {
                        "workspace_root": str(tmp_path),
                        "repos": [{"id": "core", "path": ".", "role": "primary"}],
                    }
                },
            }
        )
    )
    ag_path = tmp_path / "agents.json"
    ag_path.write_text(json.dumps({"version": "1.0", "agents": {"ag": {"executor": "fake"}}}))
    return wf_path, rs_path, ag_path


def _manufacture_tripped_run(tmp_path: Path, *, threshold: float) -> str:
    """Build + persist a RunState as if a previous process already recorded a
    `run_wall_clock_seconds` trip at task boundary "a" (task "a" succeeded; "b"/"c" still
    pending; the breaker already latched). `started_at` is set 2 real hours in the past so
    the condition (elapsed >= threshold) genuinely holds under the CLI's real, uninjectable
    clock -- both at manufacture time and (unless the extension pushes the effective
    threshold comfortably past ~7200s) after resume.
    """
    store = LocalFsArtifactStore(str(tmp_path))
    rs_store = RunStateStore(str(tmp_path), store)
    (tmp_path / "out").mkdir(parents=True, exist_ok=True)
    (tmp_path / "out" / "a.txt").write_text("done")

    now = datetime.now(UTC)
    started = now - timedelta(hours=2)
    state = RunState(
        run_id="extend-wf-20260101T000000Z",
        workflow_id="extend-wf",
        repo_set="rs",
        started_at=started.isoformat(),
        updated_at=now.isoformat(),
        status="failed",
        tasks={
            "a": TaskRunState(
                status="succeeded",
                started_at=started.isoformat(),
                ended_at=started.isoformat(),
                outputs_present=True,
            ),
            "b": TaskRunState(),
            "c": TaskRunState(),
        },
        tripped_breakers=[
            TrippedBreaker(
                id="wallclock",
                condition="run_wall_clock_seconds",
                action="stop",
                at=now.isoformat(),
                detail={"elapsed": 7200.0, "threshold": threshold},
            )
        ],
    )
    rs_store.save(state)
    return state.run_id


def _invoke_resume(tmp_path: Path, wf: Path, rs: Path, ag: Path, run_id: str, extra: list[str]):
    return runner.invoke(
        app,
        [
            "resume",
            "--run-id",
            run_id,
            "--workflow",
            str(wf),
            "--reposets",
            str(rs),
            "--agents",
            str(ag),
            *extra,
        ],
        env={"AO_WORKSPACE_ROOT": str(tmp_path)},
    )


# ---------------------------------------------------------------------------
# Validation errors (AC1-3b)
# ---------------------------------------------------------------------------


class TestExtendBreakerValidation:
    def test_unknown_breaker_id_exits_1(self, tmp_path: Path) -> None:
        wf, rs, ag = _write_specs(tmp_path, threshold=100)
        run_id = _manufacture_tripped_run(tmp_path, threshold=100)

        result = _invoke_resume(
            tmp_path, wf, rs, ag, run_id, ["--extend-breaker", "nope", "--extend-by-same"]
        )

        assert result.exit_code == 1, result.output
        assert "nope" in result.output

    def test_both_extend_flags_given_exits_1(self, tmp_path: Path) -> None:
        wf, rs, ag = _write_specs(tmp_path, threshold=100)
        run_id = _manufacture_tripped_run(tmp_path, threshold=100)

        result = _invoke_resume(
            tmp_path,
            wf,
            rs,
            ag,
            run_id,
            [
                "--extend-breaker",
                "wallclock",
                "--extend-by-seconds",
                "10",
                "--extend-by-same",
            ],
        )

        assert result.exit_code == 1, result.output

    def test_neither_extend_flag_given_exits_1(self, tmp_path: Path) -> None:
        wf, rs, ag = _write_specs(tmp_path, threshold=100)
        run_id = _manufacture_tripped_run(tmp_path, threshold=100)

        result = _invoke_resume(tmp_path, wf, rs, ag, run_id, ["--extend-breaker", "wallclock"])

        assert result.exit_code == 1, result.output

    def test_extend_flag_without_extend_breaker_exits_1(self, tmp_path: Path) -> None:
        wf, rs, ag = _write_specs(tmp_path, threshold=100)
        run_id = _manufacture_tripped_run(tmp_path, threshold=100)

        result = _invoke_resume(tmp_path, wf, rs, ag, run_id, ["--extend-by-same"])

        assert result.exit_code == 1, result.output

    def test_extension_persists_even_if_a_later_unrelated_flag_fails_validation(
        self, tmp_path: Path
    ) -> None:
        """Reviewer-flagged gap, closed: the extension used to be applied only in-memory
        and relied on orch.run()'s later save -- an unrelated CLI validation failure
        (--on-exhaustion here) occurring AFTER the extension block but before orch.run()
        would silently discard an already-logged extension. `apply_breaker_extension` is
        now followed by an immediate `rs_store.save(existing)`, so the extension survives
        even though this invocation still correctly exits 1 for the separate bad flag."""
        wf, rs, ag = _write_specs(tmp_path, threshold=100)
        run_id = _manufacture_tripped_run(tmp_path, threshold=100)

        result = _invoke_resume(
            tmp_path,
            wf,
            rs,
            ag,
            run_id,
            [
                "--extend-breaker",
                "wallclock",
                "--extend-by-seconds",
                "100000",
                "--on-exhaustion",
                "bogus-value",
            ],
        )

        assert result.exit_code == 1, result.output
        assert "Extended breaker 'wallclock': 100" in result.output

        state_path = tmp_path / ".orchestrator" / "runs" / run_id / "state.json"
        state = json.loads(state_path.read_text())
        assert state["breaker_overrides"] == {"wallclock": 100100.0}
        assert state["tripped_breakers"] == []  # un-latch also persisted
        # The run itself never actually proceeded (bad --on-exhaustion stopped it before
        # orch.run()) -- task "b" is still whatever prepare_resume left it as (pending).
        assert state["tasks"]["b"]["status"] == "pending"


# ---------------------------------------------------------------------------
# AC5: full mechanism through the real CLI.
# ---------------------------------------------------------------------------


class TestExtendBreakerEndToEnd:
    def test_extend_by_seconds_continues_past_the_stop_point(self, tmp_path: Path) -> None:
        wf, rs, ag = _write_specs(tmp_path, threshold=100)
        run_id = _manufacture_tripped_run(tmp_path, threshold=100)

        result = _invoke_resume(
            tmp_path,
            wf,
            rs,
            ag,
            run_id,
            ["--extend-breaker", "wallclock", "--extend-by-seconds", "100000"],
        )

        assert result.exit_code == 0, result.output
        assert "Extended breaker 'wallclock': 100" in result.output
        assert "100100" in result.output

        state_path = tmp_path / ".orchestrator" / "runs" / run_id / "state.json"
        state = json.loads(state_path.read_text())
        assert state["status"] == "succeeded"
        assert state["tasks"]["b"]["status"] == "succeeded"
        assert state["tasks"]["c"]["status"] == "succeeded"
        assert state["breaker_overrides"] == {"wallclock": 100100.0}
        # Un-latched and never re-tripped (100100 >> ~7200s real elapsed).
        assert state["tripped_breakers"] == []

    def test_extend_by_same_can_trip_again_once_still_exceeded(self, tmp_path: Path) -> None:
        """A deliberately small threshold (1000s, ~16.7min) means even doubling it via
        --extend-by-same (new threshold 2000s) stays well under the ~7200s real elapsed --
        proving the un-latch is real (the breaker trips again on the very next boundary),
        not a permanent bypass."""
        wf, rs, ag = _write_specs(tmp_path, threshold=1000)
        run_id = _manufacture_tripped_run(tmp_path, threshold=1000)

        result = _invoke_resume(
            tmp_path, wf, rs, ag, run_id, ["--extend-breaker", "wallclock", "--extend-by-same"]
        )

        assert "Extended breaker 'wallclock': 1000" in result.output
        assert "2000" in result.output
        assert result.exit_code == 1, result.output

        state_path = tmp_path / ".orchestrator" / "runs" / run_id / "state.json"
        state = json.loads(state_path.read_text())
        assert state["status"] == "failed"
        assert state["breaker_overrides"] == {"wallclock": 2000.0}
        # Re-tripped: the un-latch made it eligible again, and elapsed (~7200s) still
        # exceeds the new, extended threshold (2000s).
        assert len(state["tripped_breakers"]) == 1
        assert state["tripped_breakers"][0]["id"] == "wallclock"
        # Task "b" ran (the breaker only halts AFTER a task boundary settles); "c" never
        # dispatched -- the breaker halted before its dispatch.
        assert state["tasks"]["b"]["status"] == "succeeded"
        assert state["tasks"]["c"]["status"] == "pending"
