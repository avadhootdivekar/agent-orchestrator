"""Regression tests for the *multi-record* half of the boot-resume spec-path recovery fix.

Context (real incident, 2026-08-28/29). Boot-resume spawned `ao resume --run-id <id>` with
none of the original launch's spec paths, so in a workspace whose `.ao/config.yaml`
deliberately carries no global `workflow` key the child exited instantly with
"--workflow is required". Recovering those paths from the launch record fixed the *spawn*,
but not the *live machine*: by then the failed resumes had each persisted their OWN launch
record, carrying `workflow_path=None` and a bare `--run-id`-only argv. Because
`ProcessSupervisor.reconcile()` returns records newest-first and `BootResumeGuard` dedupes
by run_id, the candidate that reached the spawn was built from the NEWEST record -- i.e. one
of the information-free ones the bug itself created. The recovery would have silently failed
on exactly the workspace it was written to repair.

`scan_resumable_runs` therefore emits one candidate per *run*, merging each spec path from
the newest record that actually carries it. These tests pin that behaviour against the real
record shapes transcribed from
`/usr/avadhoot/mounted/ao-runner-ai-models/.orchestrator/ui/launches/` (run
`e-0hdsi3-ad-firstdraft-20260827T105508Z`) on 2026-08-29.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from agent_orchestrator.service.boot_resume import _recover_spec_paths, scan_resumable_runs
from agent_orchestrator.ui.processes import LaunchRecord, ProcessSupervisor

from ..ui.conftest import make_run_state, write_run

# -- real production values (transcribed verbatim; test data, not config) ------------------

RUN_ID = "e-0hdsi3-ad-firstdraft-20260827T105508Z"
WORKSPACE = "/usr/avadhoot/mounted/ao-runner-ai-models"
WORKFLOW = f"{WORKSPACE}/workflows/routed-runner/runs/e-0hdsi3-ad-firstdraft/workflow.json"
REPOSETS = f"{WORKSPACE}/specs/reposets.json"
AGENTS = f"{WORKSPACE}/specs/agents.json"
# argv[0] is an absolute interpreter path + `-m agent_orchestrator.cli`, NOT the literal
# "ao" -- flag recovery must be positional-index-based, never offset- or argv[0]-dependent.
PYTHON = "/home/avadhoot/.local/share/uv/tools/agent-orchestrator/bin/python3"
CLI = [PYTHON, "-m", "agent_orchestrator.cli"]

HEALTHY_RUN_ARGV = [*CLI, "run", "--workflow", WORKFLOW, "--reposets", REPOSETS, "--agents", AGENTS]
HEALTHY_RESUME_ARGV = [
    *CLI, "resume", "--run-id", RUN_ID,
    "--workflow", WORKFLOW, "--reposets", REPOSETS, "--agents", AGENTS,
]  # fmt: skip
# What a pre-fix boot-resume actually wrote: no workflow_path, no spec flags at all.
POISONED_RESUME_ARGV = [*CLI, "resume", "--run-id", RUN_ID]


def _dead_pid() -> int:
    """A PID this process has fully reaped, so `os.kill(pid, 0)` reports it as truly gone."""
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def _persist(
    workspace: Path,
    *,
    launch_id: str,
    started_at: str,
    kind: str,
    argv: list[str],
    workflow_path: str | None,
    finished_at: str | None = "2026-08-28T14:20:39.233751+00:00",
    pid: int | None = None,
) -> None:
    """Persist a record through `ProcessSupervisor`'s own save, so the on-disk shape is
    byte-identical to production rather than a hand-rolled JSON guess."""
    record = LaunchRecord(
        launch_id=launch_id,
        kind=kind,
        pid=_dead_pid() if pid is None else pid,
        argv=argv,
        started_at=started_at,
        log_path=str(workspace / f"{launch_id}.log"),
        run_id=RUN_ID,
        finished_at=finished_at,
        workflow_path=workflow_path,
    )
    ProcessSupervisor(str(workspace))._save(record)


def _persist_live_incident_history(workspace: Path) -> None:
    """The exact four-record history the live ai-models workspace had: a healthy original
    run, a healthy manual resume, then the two information-free records the bug produced."""
    _persist(
        workspace,
        launch_id="launch-20260827T105508050966Z",
        started_at="2026-08-27T10:55:08.050966+00:00",
        kind="run",
        argv=HEALTHY_RUN_ARGV,
        workflow_path=WORKFLOW,
    )
    _persist(
        workspace,
        launch_id="launch-20260828T090500255241Z",
        started_at="2026-08-28T09:05:00.255241+00:00",
        kind="resume",
        argv=HEALTHY_RESUME_ARGV,
        workflow_path=WORKFLOW,
    )
    _persist(
        workspace,
        launch_id="launch-20260828T125856745899Z",
        started_at="2026-08-28T12:58:56.745899+00:00",
        kind="resume",
        argv=POISONED_RESUME_ARGV,
        workflow_path=None,
    )
    _persist(
        workspace,
        launch_id="launch-20260828T142039238703Z",
        started_at="2026-08-28T14:20:39.238703+00:00",
        kind="resume",
        argv=POISONED_RESUME_ARGV,
        workflow_path=None,
    )


class TestLiveIncidentHistory:
    def test_newest_records_being_poisoned_does_not_defeat_recovery(self, tmp_path: Path) -> None:
        """THE regression. The two newest records carry no spec paths at all; the candidate
        must still come out fully populated from the older, healthy ones."""
        _persist_live_incident_history(tmp_path)
        write_run(tmp_path, make_run_state(run_id=RUN_ID, status="running"))

        (candidate,) = scan_resumable_runs(str(tmp_path))
        assert candidate.workflow_path == WORKFLOW
        assert candidate.reposets == REPOSETS
        assert candidate.agents == AGENTS

    def test_one_candidate_per_run_not_per_launch_record(self, tmp_path: Path) -> None:
        """Four records, one run -> exactly one candidate. Previously this produced four,
        which `BootResumeGuard` then had to paper over by deduping on run_id (visible in the
        hub's boot_resume_decisions as an `approve` followed by `skip_already_this_boot`)."""
        _persist_live_incident_history(tmp_path)
        write_run(tmp_path, make_run_state(run_id=RUN_ID, status="running"))

        assert len(scan_resumable_runs(str(tmp_path))) == 1

    def test_a_still_live_launch_suppresses_the_run_entirely(self, tmp_path: Path) -> None:
        """Liveness is judged across ALL of a run's records, not per record: an unfinished
        launch plus older finished ones must yield no candidate, or boot-resume would race a
        second engine onto a run that is already being worked."""
        _persist_live_incident_history(tmp_path)
        _persist(
            tmp_path,
            launch_id="launch-20260829T090000000000Z",
            started_at="2026-08-29T09:00:00.000000+00:00",
            kind="resume",
            argv=HEALTHY_RESUME_ARGV,
            workflow_path=WORKFLOW,
            finished_at=None,
            pid=os.getpid(),  # genuinely alive, or reconcile() would just mark it finished
        )
        write_run(tmp_path, make_run_state(run_id=RUN_ID, status="running"))

        assert scan_resumable_runs(str(tmp_path)) == []


class TestRecoverSpecPaths:
    """Unit-level cover for the merge helper itself."""

    def _record(self, argv: list[str], workflow_path: str | None) -> LaunchRecord:
        return LaunchRecord(
            launch_id="l",
            kind="resume",
            pid=1,
            argv=argv,
            started_at="2026-08-28T00:00:00+00:00",
            log_path="/tmp/x.log",
            run_id=RUN_ID,
            workflow_path=workflow_path,
        )

    def test_newest_record_wins_when_several_carry_a_value(self) -> None:
        newest = self._record([*CLI, "resume", "--reposets", "/new.json"], "/new/workflow.json")
        oldest = self._record([*CLI, "run", "--reposets", "/old.json"], "/old/workflow.json")

        assert _recover_spec_paths([newest, oldest]) == ("/new/workflow.json", "/new.json", None)

    def test_each_path_is_recovered_independently(self) -> None:
        """A middle record may supply one path while another supplies the rest -- they are
        merged per field, not taken wholesale from one 'best' record."""
        newest = self._record(POISONED_RESUME_ARGV, None)
        middle = self._record([*CLI, "resume", "--agents", AGENTS], None)
        oldest = self._record(HEALTHY_RUN_ARGV, WORKFLOW)

        assert _recover_spec_paths([newest, middle, oldest]) == (WORKFLOW, REPOSETS, AGENTS)

    def test_workflow_path_falls_back_to_its_argv_flag(self) -> None:
        """A record whose `workflow_path` field never got populated is still usable when its
        argv shows the flag."""
        record = self._record([*CLI, "resume", "--workflow", WORKFLOW], None)

        assert _recover_spec_paths([record])[0] == WORKFLOW

    def test_all_none_when_no_record_carries_anything(self) -> None:
        record = self._record(POISONED_RESUME_ARGV, None)

        assert _recover_spec_paths([record, record]) == (None, None, None)
