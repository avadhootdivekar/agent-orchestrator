"""Unit tests for the dashboard's subprocess supervisor (E-Ui7Kq2 FR-R1, FR-R2).

Most tests use a harmless stub command (``python -c ...``) instead of the real CLI, so the
supervisor's own behaviour — argv construction, record persistence, run-id attribution,
cancellation — is what is under test rather than the engine.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

from agent_orchestrator.ui.processes import (
    ALLOWED_BOOL_OPTIONS,
    ALLOWED_OPTIONS,
    LaunchError,
    ProcessSupervisor,
    _render_options,
)

from .conftest import make_run_state, write_run

# A stub "ao" that exits immediately — enough to exercise spawn/record/reconcile.
NOOP_COMMAND = [sys.executable, "-c", "pass"]
# A stub that blocks until signalled, for cancellation tests.
SLEEP_COMMAND = [sys.executable, "-c", "import time; time.sleep(60)"]

# Run-id discovery blocks until a run directory appears; the production default is 10s,
# which every negative-path test here would pay in full. These tests assert on discovery
# LOGIC, not on the timeout value, so they use a short one.
FAST_DISCOVERY = 1.0


def _wait_until(predicate, timeout: float = 10.0, interval: float = 0.02) -> bool:
    """Poll *predicate* until true or *timeout* elapses. Returns the final result."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


class TestOptionRendering:
    """The allow-list is a security boundary, not a convenience."""

    def test_renders_allow_listed_options_as_flags(self) -> None:
        argv = _render_options({"model": "claude-opus-4-8", "max_parallel": 4})
        assert argv == ["--model", "claude-opus-4-8", "--max-parallel", "4"]

    def test_drops_unknown_keys(self) -> None:
        # An unauthenticated UI must never be able to inject arbitrary argv into a
        # subprocess via an unexpected request-body key.
        assert _render_options({"rm": "-rf", "__proto__": "x", "shell": "true"}) == []

    def test_drops_none_and_empty_values(self) -> None:
        assert _render_options({"model": None, "effort": ""}) == []

    def test_renders_booleans_as_paired_bare_flags(self) -> None:
        assert _render_options({"self_heal": True}) == ["--self-heal"]
        assert _render_options({"self_heal": False}) == ["--no-self-heal"]

    def test_every_allow_listed_key_maps_to_a_dashed_flag(self) -> None:
        for flag in ALLOWED_OPTIONS.values():
            assert flag.startswith("--")
        for on_flag, off_flag in ALLOWED_BOOL_OPTIONS.values():
            assert on_flag.startswith("--") and off_flag.startswith("--")

    def test_values_are_passed_as_separate_argv_entries(self) -> None:
        # Separate entries (not "--model=x") means no shell parsing is ever involved, so a
        # value containing spaces or quotes cannot become extra arguments.
        argv = _render_options({"model": "weird value; rm -rf /"})
        assert argv == ["--model", "weird value; rm -rf /"]


class TestLaunching:
    def test_launch_run_builds_the_expected_argv(self, workspace: Path) -> None:
        supervisor = ProcessSupervisor(
            str(workspace), ao_command=NOOP_COMMAND, run_id_discovery_timeout=FAST_DISCOVERY
        )
        record = supervisor.launch_run(
            workflow_path="/ws/wf.json",
            reposets="/ws/reposets.json",
            agents="/ws/agents.json",
            options={"model": "claude-opus-4-8"},
        )
        assert record.argv[: len(NOOP_COMMAND)] == NOOP_COMMAND
        assert record.argv[len(NOOP_COMMAND)] == "run"
        assert "--workflow" in record.argv and "/ws/wf.json" in record.argv
        assert "--reposets" in record.argv and "--agents" in record.argv
        assert "--model" in record.argv and "claude-opus-4-8" in record.argv

    def test_prompt_is_passed_by_file_never_on_the_command_line(self, workspace: Path) -> None:
        # Prompts can be long and are visible in `ps` if passed as an argument, so the
        # supervisor must always use --prompt-file.
        supervisor = ProcessSupervisor(
            str(workspace), ao_command=NOOP_COMMAND, run_id_discovery_timeout=FAST_DISCOVERY
        )
        record = supervisor.launch_run(workflow_path="/ws/wf.json", prompt="secret prompt text")

        assert "--prompt" not in record.argv
        assert "--prompt-file" in record.argv
        assert "secret prompt text" not in record.argv

        prompt_path = Path(record.argv[record.argv.index("--prompt-file") + 1])
        assert prompt_path.read_text(encoding="utf-8") == "secret prompt text"
        assert record.prompt_chars == len("secret prompt text")

    def test_blank_prompt_is_omitted_entirely(self, workspace: Path) -> None:
        supervisor = ProcessSupervisor(
            str(workspace), ao_command=NOOP_COMMAND, run_id_discovery_timeout=FAST_DISCOVERY
        )
        record = supervisor.launch_run(workflow_path="/ws/wf.json", prompt="   ")
        assert "--prompt-file" not in record.argv
        assert record.prompt_chars == 0

    def test_general_instructions_become_repeated_flags(self, workspace: Path) -> None:
        supervisor = ProcessSupervisor(
            str(workspace), ao_command=NOOP_COMMAND, run_id_discovery_timeout=FAST_DISCOVERY
        )
        record = supervisor.launch_run(
            workflow_path="/ws/wf.json",
            general_instructions=["a.md", "b.md"],
        )
        assert record.argv.count("--general-instruction") == 2
        assert "a.md" in record.argv and "b.md" in record.argv

    def test_launch_resume_targets_the_run_id(self, workspace: Path) -> None:
        supervisor = ProcessSupervisor(
            str(workspace), ao_command=NOOP_COMMAND, run_id_discovery_timeout=FAST_DISCOVERY
        )
        record = supervisor.launch_resume("run-123", workflow_path="/ws/wf.json")

        assert "resume" in record.argv
        assert "--run-id" in record.argv and "run-123" in record.argv
        assert record.run_id == "run-123", "resume knows its run id up front"

    def test_launch_writes_a_persistent_record(self, workspace: Path) -> None:
        supervisor = ProcessSupervisor(
            str(workspace), ao_command=NOOP_COMMAND, run_id_discovery_timeout=FAST_DISCOVERY
        )
        record = supervisor.launch_run(workflow_path="/ws/wf.json")

        # A fresh supervisor (as after a dashboard restart) still sees the launch.
        reloaded = ProcessSupervisor(
            str(workspace), ao_command=NOOP_COMMAND, run_id_discovery_timeout=FAST_DISCOVERY
        ).load_records()
        assert [r.launch_id for r in reloaded] == [record.launch_id]

    def test_launch_captures_output_to_a_log(self, workspace: Path) -> None:
        supervisor = ProcessSupervisor(
            str(workspace),
            ao_command=[sys.executable, "-c", "print('hello from the child')"],
            run_id_discovery_timeout=FAST_DISCOVERY,
        )
        record = supervisor.launch_run()
        assert _wait_until(lambda: "hello" in supervisor.read_log(record.launch_id))

    def test_unlaunchable_command_raises_launch_error(self, workspace: Path) -> None:
        supervisor = ProcessSupervisor(
            str(workspace),
            ao_command=["/definitely/not/a/real/binary"],
            run_id_discovery_timeout=FAST_DISCOVERY,
        )
        with pytest.raises(LaunchError):
            supervisor.launch_run()

    def test_corrupt_record_files_are_skipped(self, workspace: Path) -> None:
        supervisor = ProcessSupervisor(
            str(workspace), ao_command=NOOP_COMMAND, run_id_discovery_timeout=FAST_DISCOVERY
        )
        supervisor.launch_run()
        (supervisor.launches_dir / "garbage.json").write_text("{oops", encoding="utf-8")
        assert len(supervisor.load_records()) == 1


class TestRunIdDiscovery:
    def test_attributes_a_newly_created_run_directory_to_the_launch(self, workspace: Path) -> None:
        # Simulate the engine creating its run dir by writing one from the "child".
        supervisor = ProcessSupervisor(
            str(workspace),
            ao_command=[
                sys.executable,
                "-c",
                (
                    "import pathlib,sys;"
                    f"d=pathlib.Path({str(workspace)!r})/'.orchestrator'/'runs'/'wf-20260724T120000Z';"
                    "d.mkdir(parents=True, exist_ok=True);"
                    "(d/'state.json').write_text('{}');"
                    "import time; time.sleep(0.5)"
                ),
            ],
        )
        record = supervisor.launch_run(workflow_path="/ws/wf.json")
        assert record.run_id == "wf-20260724T120000Z"

    def test_pre_existing_runs_are_never_adopted(self, workspace: Path) -> None:
        write_run(workspace, make_run_state(run_id="older-run"))
        supervisor = ProcessSupervisor(
            str(workspace), ao_command=NOOP_COMMAND, run_id_discovery_timeout=FAST_DISCOVERY
        )

        record = supervisor.launch_run(workflow_path="/ws/wf.json")
        assert record.run_id != "older-run"

    def test_a_launch_that_creates_no_run_reports_no_run_id(self, workspace: Path) -> None:
        # The child exits immediately without writing a run dir (e.g. bad flags). That is a
        # normal, reportable outcome — not a hang and not a crash.
        supervisor = ProcessSupervisor(
            str(workspace), ao_command=NOOP_COMMAND, run_id_discovery_timeout=FAST_DISCOVERY
        )
        assert supervisor.launch_run(workflow_path="/ws/wf.json").run_id is None

    def test_record_for_run_finds_the_owning_launch(self, workspace: Path) -> None:
        supervisor = ProcessSupervisor(
            str(workspace), ao_command=NOOP_COMMAND, run_id_discovery_timeout=FAST_DISCOVERY
        )
        supervisor.launch_resume("run-abc")
        assert supervisor.record_for_run("run-abc") is not None
        assert supervisor.record_for_run("run-xyz") is None


class TestLifecycle:
    def test_reconcile_marks_exited_processes_finished(self, workspace: Path) -> None:
        supervisor = ProcessSupervisor(
            str(workspace), ao_command=NOOP_COMMAND, run_id_discovery_timeout=FAST_DISCOVERY
        )
        supervisor.launch_resume("run-1")

        assert _wait_until(
            lambda: all(r.finished_at is not None for r in supervisor.reconcile())
        ), "a child that has exited must stop being reported as running"

    def test_is_running_is_false_once_the_child_exits(self, workspace: Path) -> None:
        supervisor = ProcessSupervisor(
            str(workspace), ao_command=NOOP_COMMAND, run_id_discovery_timeout=FAST_DISCOVERY
        )
        supervisor.launch_resume("run-1")
        assert _wait_until(lambda: not supervisor.is_running("run-1"))

    def test_is_running_is_true_while_the_child_lives(self, workspace: Path) -> None:
        supervisor = ProcessSupervisor(
            str(workspace), ao_command=SLEEP_COMMAND, run_id_discovery_timeout=FAST_DISCOVERY
        )
        record = supervisor.launch_resume("run-long")
        try:
            assert supervisor.is_running("run-long") is True
        finally:
            os.killpg(os.getpgid(record.pid), 9)

    def test_is_running_is_false_for_a_run_this_dashboard_never_launched(
        self, workspace: Path
    ) -> None:
        supervisor = ProcessSupervisor(
            str(workspace), ao_command=NOOP_COMMAND, run_id_discovery_timeout=FAST_DISCOVERY
        )
        assert supervisor.is_running("some-other-run") is False


class TestCancel:
    def test_cancel_terminates_a_live_child(self, workspace: Path) -> None:
        supervisor = ProcessSupervisor(
            str(workspace), ao_command=SLEEP_COMMAND, run_id_discovery_timeout=FAST_DISCOVERY
        )
        record = supervisor.launch_resume("run-long")
        assert supervisor.is_running("run-long")

        cancelled = supervisor.cancel("run-long")
        assert cancelled.cancelled is True
        assert cancelled.finished_at is not None
        assert _wait_until(lambda: not supervisor.is_running("run-long"))
        # Belt and braces: the PID really is gone.
        with pytest.raises(ProcessLookupError):
            os.kill(record.pid, 0)

    def test_cancel_kills_the_whole_process_group_not_just_the_child(self, workspace: Path) -> None:
        # The engine spawns `claude` children; signalling only the direct child would
        # orphan them and keep spending tokens. The supervisor signals the group, so a
        # grandchild must die too.
        marker = workspace / "grandchild-alive.txt"

        # Real script files, not nested `-c` strings: the quoting of a script-inside-a-
        # script is unreadable and easy to get silently wrong.
        grandchild = workspace / "grandchild.py"
        grandchild.write_text(
            f"import pathlib, time\n"
            f"pathlib.Path({str(marker)!r}).write_text('x')\n"
            f"time.sleep(60)\n",
            encoding="utf-8",
        )
        child = workspace / "child.py"
        child.write_text(
            f"import subprocess, sys, time\n"
            f"subprocess.Popen([sys.executable, {str(grandchild)!r}])\n"
            f"time.sleep(60)\n",
            encoding="utf-8",
        )

        supervisor = ProcessSupervisor(str(workspace), ao_command=[sys.executable, str(child)])
        record = supervisor.launch_resume("run-tree")
        assert _wait_until(lambda: marker.exists()), "grandchild did not start"

        supervisor.cancel("run-tree")

        # Nothing in the group should survive; the group id is gone entirely.
        assert _wait_until(lambda: not _group_alive(record.pid))

    def test_cancelling_an_unknown_run_raises(self, workspace: Path) -> None:
        supervisor = ProcessSupervisor(
            str(workspace), ao_command=NOOP_COMMAND, run_id_discovery_timeout=FAST_DISCOVERY
        )
        with pytest.raises(LaunchError, match="not launched from this dashboard"):
            supervisor.cancel("never-launched")

    def test_cancelling_an_already_finished_run_raises(self, workspace: Path) -> None:
        supervisor = ProcessSupervisor(
            str(workspace), ao_command=NOOP_COMMAND, run_id_discovery_timeout=FAST_DISCOVERY
        )
        supervisor.launch_resume("run-done")
        assert _wait_until(lambda: not supervisor.is_running("run-done"))

        with pytest.raises(LaunchError, match="no longer running"):
            supervisor.cancel("run-done")


def _group_alive(pid: int) -> bool:
    """True if the process group led by *pid* still has any member."""
    try:
        os.killpg(os.getpgid(pid), 0)
    except (ProcessLookupError, PermissionError, OSError):
        return False
    return True
