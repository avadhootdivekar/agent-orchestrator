"""Tests for T-l7t6TT-workflow-snapshot (epic E-k3AMEr): a run's static dependency
structure must be reconstructible from its own run directory, however the run was
launched (`ao ui`, bare `ao run`, `ao resume`, or a cron/event trigger).

Covers TASK.md acceptance criteria 1-10 (AC-11 -- pytest/ruff/mypy clean -- is verified
by the harness that runs this file, not by a test in it):

  AC1:  models.py gains SpecSession/WorkflowSnapshot + RunState.spec_sessions;
        runstate.py gains the named constants.
  AC2:  canonical_spec_json is stable across dict-key insertion order.
  AC3:  a fresh `ao run` (CliRunner, fake executor) writes exactly one SpecSession
        (session=1) and one workflow.snapshot.<sha12>.json whose body matches.
  AC4:  injected task ids are excluded from the snapshot, and are stable across resume
        (same static spec -> same sha -> no new file).
  AC5:  resume with an unchanged spec appends a second SpecSession with the SAME sha,
        still exactly one snapshot file, and its mtime is unchanged (no rewrite).
  AC6:  resume with a changed spec appends a SpecSession with a DIFFERENT sha, writes
        a SECOND snapshot file (the first left byte-identical), and logs a
        `run.spec_changed_on_resume` warning.
  AC7:  load_workflow_snapshot_at is tolerant (returns None + warns, never raises) for
        a missing file, an oversized file (checked via stat() BEFORE any read),
        invalid JSON, a schema-invalid body, and a sha mismatch.
  AC8:  a snapshot write failure (OSError) never fails the run; spec_sessions still
        records the session and a `run.snapshot_failed` warning is logged.
  AC9:  a pre-epic state.json (missing the spec_sessions key) still loads, defaulting
        to spec_sessions == [].
  AC10: RunStateStore.load_workflow_snapshot delegates to the shared module-level
        load_workflow_snapshot_at -- one parser, one size cap.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agent_orchestrator import runstate
from agent_orchestrator.artifacts import LocalFsArtifactStore
from agent_orchestrator.cli import app
from agent_orchestrator.executors.fake import FakeExecutor
from agent_orchestrator.models import (
    HookSpec,
    RunState,
    SpecSession,
    TaskSpec,
    WorkflowSnapshot,
    WorkflowSpec,
)
from agent_orchestrator.runstate import (
    WORKFLOW_SNAPSHOT_MAX_BYTES,
    WORKFLOW_SNAPSHOT_PREFIX,
    WORKFLOW_SNAPSHOT_SCHEMA_VERSION,
    WORKFLOW_SNAPSHOT_SHA_CHARS,
    WORKFLOW_SNAPSHOT_SUFFIX,
    RunStateStore,
    canonical_spec_json,
    load_workflow_snapshot_at,
)

cli_runner = CliRunner()


def _events(caplog: pytest.LogCaptureFixture, name: str) -> list[logging.LogRecord]:
    """Filter caplog records by the structured `event` extra (mirrors the pattern in
    tests/test_engine_workspace_lock_sync.py)."""
    return [r for r in caplog.records if getattr(r, "event", None) == name]


# ---------------------------------------------------------------------------
# AC-1: models and constants
# ---------------------------------------------------------------------------


class TestModelsAndConstants:
    def test_spec_session_fields(self) -> None:
        s = SpecSession(session=1, started_at="2026-01-01T00:00:00+00:00", spec_sha256="a" * 64)
        assert s.session == 1
        assert s.spec_sha256 == "a" * 64

    def test_workflow_snapshot_fields_and_default_schema_version(self) -> None:
        wf = WorkflowSpec(
            version="1.0",
            id="wf",
            repo_set="rs",
            tasks=[TaskSpec(id="t", agent="ag", instruction="i.md")],
        )
        snap = WorkflowSnapshot(
            run_id="run-1",
            spec_sha256="b" * 64,
            written_at="2026-01-01T00:00:00+00:00",
            workflow=wf,
        )
        assert snap.schema_version == 1
        assert snap.workflow.tasks[0].id == "t"

    def test_run_state_spec_sessions_defaults_empty(self) -> None:
        assert RunState.model_fields["spec_sessions"].default == []

    def test_named_constants_exist_with_expected_values(self) -> None:
        assert WORKFLOW_SNAPSHOT_PREFIX == "workflow.snapshot."
        assert WORKFLOW_SNAPSHOT_SUFFIX == ".json"
        assert WORKFLOW_SNAPSHOT_SHA_CHARS == 12
        assert WORKFLOW_SNAPSHOT_SCHEMA_VERSION == 1
        assert WORKFLOW_SNAPSHOT_MAX_BYTES == 20_000_000


# ---------------------------------------------------------------------------
# AC-2: canonical sha determinism
# ---------------------------------------------------------------------------


class TestCanonicalSpecJson:
    def test_stable_across_dict_key_insertion_order(self) -> None:
        """Two logically-equal specs whose ONLY difference is the insertion order of
        a dict-valued field (`hooks`) must hash identically -- this is exactly what
        `sort_keys=True` in canonical_spec_json guarantees, and what a naive
        `json.dumps` without it would NOT."""
        tasks = [TaskSpec(id="t1", agent="ag", instruction="i.md")]
        hooks_forward = {
            "h1": HookSpec(command=["echo", "1"]),
            "h2": HookSpec(command=["echo", "2"]),
        }
        hooks_reversed = {
            "h2": HookSpec(command=["echo", "2"]),
            "h1": HookSpec(command=["echo", "1"]),
        }
        wf_a = WorkflowSpec(version="1.0", id="wf", repo_set="rs", tasks=tasks, hooks=hooks_forward)
        wf_b = WorkflowSpec(
            version="1.0", id="wf", repo_set="rs", tasks=tasks, hooks=hooks_reversed
        )

        body_a = canonical_spec_json(wf_a)
        body_b = canonical_spec_json(wf_b)
        assert body_a == body_b

        sha_a = hashlib.sha256(body_a.encode("utf-8")).hexdigest()
        sha_b = hashlib.sha256(body_b.encode("utf-8")).hexdigest()
        assert sha_a == sha_b

    def test_semantically_different_specs_hash_differently(self) -> None:
        tasks_a = [TaskSpec(id="t1", agent="ag", instruction="i.md")]
        tasks_b = [TaskSpec(id="t1", agent="ag", instruction="i.md", timeout_seconds=99)]
        wf_a = WorkflowSpec(version="1.0", id="wf", repo_set="rs", tasks=tasks_a)
        wf_b = WorkflowSpec(version="1.0", id="wf", repo_set="rs", tasks=tasks_b)
        assert canonical_spec_json(wf_a) != canonical_spec_json(wf_b)


# ---------------------------------------------------------------------------
# AC-3: fresh run via CliRunner
# ---------------------------------------------------------------------------


def _write_cli_specs(tmp_path: Path, task_ids: list[str]) -> tuple[Path, Path, Path]:
    wf_path = tmp_path / "wf.json"
    tasks = []
    prev = None
    for tid in task_ids:
        task: dict = {
            "id": tid,
            "agent": "ag",
            "instruction": "specs/examples/instructions/design.md",
            "outputs": [f"out/{tid}.txt"],
        }
        if prev is not None:
            task["depends_on"] = [prev]
        tasks.append(task)
        prev = tid
    wf_path.write_text(
        json.dumps(
            {"version": "1.0", "id": "snap-fresh-wf", "repo_set": "default-set", "tasks": tasks}
        )
    )
    rs_path = tmp_path / "reposets.json"
    rs_path.write_text(
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
    ag_path = tmp_path / "agents.json"
    ag_path.write_text(json.dumps({"version": "1.0", "agents": {"ag": {"executor": "fake"}}}))
    return wf_path, rs_path, ag_path


def _invoke_run(wf_path: Path, rs_path: Path, ag_path: Path, tmp_path: Path):
    return cli_runner.invoke(
        app,
        [
            "run",
            "--workflow",
            str(wf_path),
            "--reposets",
            str(rs_path),
            "--agents",
            str(ag_path),
        ],
        env={"AO_WORKSPACE_ROOT": str(tmp_path)},
    )


class TestFreshRunViaCli:
    def test_fresh_run_writes_one_session_and_one_matching_snapshot(self, tmp_path: Path) -> None:
        task_ids = ["a", "b"]
        wf_path, rs_path, ag_path = _write_cli_specs(tmp_path, task_ids)

        result = _invoke_run(wf_path, rs_path, ag_path, tmp_path)
        assert result.exit_code == 0, f"CLI run failed:\n{result.output}"

        run_id_match = re.search(r"Run:\s+(\S+)", result.output)
        assert run_id_match, f"could not extract run_id:\n{result.output}"
        run_id = run_id_match.group(1)

        run_dir = tmp_path / ".orchestrator" / "runs" / run_id
        state = json.loads((run_dir / "state.json").read_text())

        assert len(state["spec_sessions"]) == 1
        assert state["spec_sessions"][0]["session"] == 1
        session_sha = state["spec_sessions"][0]["spec_sha256"]

        snap_files = sorted(run_dir.glob("workflow.snapshot.*.json"))
        assert len(snap_files) == 1
        assert snap_files[0].name == f"workflow.snapshot.{session_sha[:12]}.json"

        snap = json.loads(snap_files[0].read_text())
        assert snap["spec_sha256"] == session_sha
        assert [t["id"] for t in snap["workflow"]["tasks"]] == task_ids


# ---------------------------------------------------------------------------
# AC-4/5/6: resume behavior via the engine API (make_workflow/make_orchestrator
# fixtures from tests/conftest.py -- same pattern as test_dynamic_injection.py and
# test_resume_replay.py).
# ---------------------------------------------------------------------------


class TestInjectedTasksExcludedAndStableAcrossResume:
    def test_injected_ids_excluded_and_sha_unchanged_on_resume(
        self, make_workflow, make_orchestrator, workspace: Path, rs_store: RunStateStore
    ) -> None:
        manifest_path = "output/manifest.json"
        wf = make_workflow(
            [
                {
                    "id": "emitter",
                    "emit_tasks": True,
                    "task_manifest_path": manifest_path,
                    "outputs": [],
                },
            ],
            wf_id="snap-emit-wf",
        )
        emitted_d = {
            "id": "d",
            "agent": "ag",
            "instruction": "specs/instructions/emitter.md",
            "outputs": ["output/d.txt"],
            "depends_on": ["emitter"],
        }
        emitted_e = {
            "id": "e",
            "agent": "ag",
            "instruction": "specs/instructions/emitter.md",
            "outputs": ["output/e.txt"],
            "depends_on": ["d"],
        }

        # Session 1: emitter injects d, e; e fails so the run stops (deterministic,
        # no FakeExecutor behavior-injection needed for resume itself).
        executor1 = FakeExecutor(
            emit_payloads={"emitter": {"tasks": [emitted_d, emitted_e]}},
            behaviors={"e": "fail"},
        )
        orch1, reposets, agents = make_orchestrator(executor1)
        state1 = orch1.run(wf, reposets, agents)

        assert state1.status == "failed"
        assert {t.id for t in state1.injected_tasks} == {"d", "e"}
        assert len(state1.spec_sessions) == 1
        sha1 = state1.spec_sessions[0].spec_sha256

        run_dir = workspace / ".orchestrator" / "runs" / state1.run_id
        snap_files1 = sorted(run_dir.glob("workflow.snapshot.*.json"))
        assert len(snap_files1) == 1
        snap1 = json.loads(snap_files1[0].read_text())
        assert [t["id"] for t in snap1["workflow"]["tasks"]] == ["emitter"]

        # Session 2 (resume): prepare_resume merges d/e back into wf.tasks -- the
        # snapshot must still exclude them (they are in state.injected_tasks).
        loaded = rs_store.load(state1.run_id)
        prepared = rs_store.prepare_resume(loaded, wf)
        executor2 = FakeExecutor()
        orch2, _, _ = make_orchestrator(executor2)
        state2 = orch2.run(wf, reposets, agents, run_state=prepared)

        assert state2.status == "succeeded"
        assert len(state2.spec_sessions) == 2
        sha2 = state2.spec_sessions[1].spec_sha256
        assert sha2 == sha1  # same static spec -> no new file

        snap_files2 = sorted(run_dir.glob("workflow.snapshot.*.json"))
        assert len(snap_files2) == 1  # still exactly one file
        snap2 = json.loads(snap_files2[0].read_text())
        task_ids2 = [t["id"] for t in snap2["workflow"]["tasks"]]
        assert task_ids2 == ["emitter"]
        assert "d" not in task_ids2 and "e" not in task_ids2


class TestResumeUnchangedSpec:
    def test_two_sessions_same_sha_one_file_no_rewrite(
        self, make_workflow, make_orchestrator, workspace: Path, rs_store: RunStateStore
    ) -> None:
        wf = make_workflow(
            [
                {"id": "a", "outputs": ["output/a.txt"]},
                {"id": "b", "depends_on": ["a"], "outputs": ["output/b.txt"]},
            ],
            wf_id="snap-resume-unchanged-wf",
        )

        executor1 = FakeExecutor(behaviors={"b": "fail"})
        orch1, reposets, agents = make_orchestrator(executor1)
        state1 = orch1.run(wf, reposets, agents)
        assert state1.status == "failed"
        assert len(state1.spec_sessions) == 1

        run_dir = workspace / ".orchestrator" / "runs" / state1.run_id
        snap_files = sorted(run_dir.glob("workflow.snapshot.*.json"))
        assert len(snap_files) == 1
        mtime_before = snap_files[0].stat().st_mtime_ns
        content_before = snap_files[0].read_bytes()

        loaded = rs_store.load(state1.run_id)
        prepared = rs_store.prepare_resume(loaded, wf)
        executor2 = FakeExecutor()
        orch2, _, _ = make_orchestrator(executor2)
        state2 = orch2.run(wf, reposets, agents, run_state=prepared)

        assert state2.status == "succeeded"
        assert len(state2.spec_sessions) == 2
        assert state2.spec_sessions[0].spec_sha256 == state2.spec_sessions[1].spec_sha256

        snap_files_after = sorted(run_dir.glob("workflow.snapshot.*.json"))
        assert len(snap_files_after) == 1
        assert snap_files_after[0].stat().st_mtime_ns == mtime_before  # never rewritten
        assert snap_files_after[0].read_bytes() == content_before


class TestResumeChangedSpec:
    def test_changed_spec_writes_second_file_keeps_first_and_warns(
        self,
        make_workflow,
        make_orchestrator,
        workspace: Path,
        rs_store: RunStateStore,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        wf1 = make_workflow(
            [
                {"id": "a", "outputs": ["output/a.txt"]},
                {"id": "b", "depends_on": ["a"], "outputs": ["output/b.txt"]},
            ],
            wf_id="snap-resume-changed-wf",
        )

        executor1 = FakeExecutor(behaviors={"b": "fail"})
        orch1, reposets, agents = make_orchestrator(executor1)
        state1 = orch1.run(wf1, reposets, agents)
        assert state1.status == "failed"

        run_dir = workspace / ".orchestrator" / "runs" / state1.run_id
        snap_files_before = sorted(run_dir.glob("workflow.snapshot.*.json"))
        assert len(snap_files_before) == 1
        name_before = snap_files_before[0].name
        content_before = snap_files_before[0].read_bytes()

        # Same task ids, but "b" now declares a different timeout -- a field that
        # changes the canonical spec's sha without affecting FakeExecutor behavior.
        wf2 = make_workflow(
            [
                {"id": "a", "outputs": ["output/a.txt"]},
                {
                    "id": "b",
                    "depends_on": ["a"],
                    "outputs": ["output/b.txt"],
                    "timeout_seconds": 999,
                },
            ],
            wf_id="snap-resume-changed-wf",
        )

        loaded = rs_store.load(state1.run_id)
        prepared = rs_store.prepare_resume(loaded, wf2)
        executor2 = FakeExecutor()
        orch2, _, _ = make_orchestrator(executor2)
        with caplog.at_level(logging.WARNING, logger="agent_orchestrator"):
            state2 = orch2.run(wf2, reposets, agents, run_state=prepared)

        assert state2.status == "succeeded"
        assert len(state2.spec_sessions) == 2
        assert state2.spec_sessions[0].spec_sha256 != state2.spec_sessions[1].spec_sha256

        snap_files_after = sorted(run_dir.glob("workflow.snapshot.*.json"))
        assert len(snap_files_after) == 2

        old_file = next(f for f in snap_files_after if f.name == name_before)
        assert old_file.read_bytes() == content_before  # untouched

        warnings = _events(caplog, "run.spec_changed_on_resume")
        assert len(warnings) == 1
        assert warnings[0].levelno == logging.WARNING


# ---------------------------------------------------------------------------
# AC-7: tolerant load
# ---------------------------------------------------------------------------


def _valid_snapshot_body(sha: str, run_id: str = "run-1") -> str:
    wf = WorkflowSpec(
        version="1.0",
        id="wf",
        repo_set="rs",
        tasks=[TaskSpec(id="t", agent="ag", instruction="i.md")],
    )
    snap = WorkflowSnapshot(
        run_id=run_id, spec_sha256=sha, written_at="2026-01-01T00:00:00+00:00", workflow=wf
    )
    return snap.model_dump_json()


class TestLoadWorkflowSnapshotAtTolerant:
    def test_missing_file_returns_none_and_warns(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        sha = "a" * 64
        with caplog.at_level(logging.WARNING, logger="agent_orchestrator.runstate"):
            result = load_workflow_snapshot_at(tmp_path, sha)
        assert result is None
        assert len(_events(caplog, "run.workflow_snapshot_unavailable")) == 1

    def test_oversized_file_is_rejected_by_stat_before_any_read(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        sha = "b" * 64
        path = tmp_path / f"workflow.snapshot.{sha[:12]}.json"
        # Deliberately INVALID JSON: if the size check didn't run first (or ran
        # after a read), this content would fail parsing with a DIFFERENT warning
        # message than the size-cap one asserted below.
        path.write_text("this is not valid json and is long enough to matter")
        monkeypatch.setattr(runstate, "WORKFLOW_SNAPSHOT_MAX_BYTES", 5)

        with caplog.at_level(logging.WARNING, logger="agent_orchestrator.runstate"):
            result = load_workflow_snapshot_at(tmp_path, sha)

        assert result is None
        messages = [r.getMessage() for r in caplog.records]
        assert any("exceeds cap" in m for m in messages)
        assert not any("unreadable or invalid" in m for m in messages)

    def test_invalid_json_returns_none_and_warns(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        sha = "c" * 64
        path = tmp_path / f"workflow.snapshot.{sha[:12]}.json"
        path.write_text("{not valid json")
        with caplog.at_level(logging.WARNING, logger="agent_orchestrator.runstate"):
            result = load_workflow_snapshot_at(tmp_path, sha)
        assert result is None
        assert len(_events(caplog, "run.workflow_snapshot_unavailable")) == 1

    def test_schema_invalid_body_returns_none_and_warns(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        sha = "d" * 64
        path = tmp_path / f"workflow.snapshot.{sha[:12]}.json"
        path.write_text(json.dumps({"unexpected": "shape"}))
        with caplog.at_level(logging.WARNING, logger="agent_orchestrator.runstate"):
            result = load_workflow_snapshot_at(tmp_path, sha)
        assert result is None
        assert len(_events(caplog, "run.workflow_snapshot_unavailable")) == 1

    def test_sha_mismatch_returns_none_and_warns(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        requested_sha = "e" * 64
        body_sha = "f" * 64
        path = tmp_path / f"workflow.snapshot.{requested_sha[:12]}.json"
        path.write_text(_valid_snapshot_body(body_sha))
        with caplog.at_level(logging.WARNING, logger="agent_orchestrator.runstate"):
            result = load_workflow_snapshot_at(tmp_path, requested_sha)
        assert result is None
        assert len(_events(caplog, "run.workflow_snapshot_unavailable")) == 1

    def test_valid_snapshot_loads_successfully(self, tmp_path: Path) -> None:
        sha = "0" * 64
        path = tmp_path / f"workflow.snapshot.{sha[:12]}.json"
        path.write_text(_valid_snapshot_body(sha))
        result = load_workflow_snapshot_at(tmp_path, sha)
        assert result is not None
        assert result.spec_sha256 == sha

    @pytest.mark.parametrize(
        "malformed_sha",
        [
            "",
            "too-short",
            "A" * 64,  # uppercase -- HLD §13.2 pattern is lowercase-only
            "g" * 64,  # non-hex character
            "0" * 63,  # one char short
            "0" * 65,  # one char long
            "../../../etc/passwd",  # Gate G1 SHOULD-FIX: path-traversal attempt
            "0" * 12 + "/../../evil",
        ],
    )
    def test_malformed_sha_returns_none_and_warns_before_touching_disk(
        self,
        malformed_sha: str,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Gate G1 SHOULD-FIX (reviewer): a caller-supplied sha that doesn't match
        HLD §13.2's ``^[0-9a-f]{64}$`` pattern -- e.g. read back out of a
        ``state.json`` living in the agent-writable workspace -- must never reach
        path construction. Rejected tolerantly (warn + None), same as any other
        invalid input, and never raises even for a path-traversal-shaped value.
        """
        with caplog.at_level(logging.WARNING, logger="agent_orchestrator.runstate"):
            result = load_workflow_snapshot_at(tmp_path, malformed_sha)
        assert result is None
        assert len(_events(caplog, "run.workflow_snapshot_unavailable")) == 1
        # Nothing was created/escaped outside tmp_path as a side effect of the attempt.
        assert list(tmp_path.iterdir()) == []


# ---------------------------------------------------------------------------
# AC-8: a snapshot write failure never fails the run
# ---------------------------------------------------------------------------


class TestSnapshotWriteFailureNeverFailsRun:
    def test_oserror_on_snapshot_write_logs_warning_and_run_still_succeeds(
        self,
        make_workflow,
        make_orchestrator,
        workspace: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        wf = make_workflow([{"id": "a", "outputs": ["output/a.txt"]}], wf_id="snap-write-fail-wf")
        executor = FakeExecutor()
        orch, reposets, agents = make_orchestrator(executor)

        def _raise_oserror(self: RunStateStore, path: Path, snapshot: WorkflowSnapshot) -> None:
            raise OSError("disk full (simulated)")

        # Narrow monkeypatch target (T-l7t6TT AC-8): only the snapshot's own atomic
        # write raises -- state.json's save() is untouched, so the run can still
        # complete and persist state normally.
        monkeypatch.setattr(RunStateStore, "_write_snapshot_atomic", _raise_oserror)

        with caplog.at_level(logging.WARNING, logger="agent_orchestrator"):
            state = orch.run(wf, reposets, agents)

        assert state.status == "succeeded"
        assert len(state.spec_sessions) == 1  # session still recorded

        run_dir = workspace / ".orchestrator" / "runs" / state.run_id
        assert list(run_dir.glob("workflow.snapshot.*.json")) == []  # never written
        assert (run_dir / "state.json").exists()  # the run's own save was unaffected

        failures = _events(caplog, "run.snapshot_failed")
        assert len(failures) == 1
        assert failures[0].levelno == logging.WARNING


# ---------------------------------------------------------------------------
# AC-9: backward compatibility
# ---------------------------------------------------------------------------


class TestBackwardCompatibility:
    def test_pre_epic_state_json_without_spec_sessions_key_still_loads(
        self, tmp_path: Path
    ) -> None:
        store = LocalFsArtifactStore(str(tmp_path))
        rs = RunStateStore(str(tmp_path), store)
        wf = WorkflowSpec(
            version="1.0",
            id="wf",
            repo_set="rs",
            tasks=[TaskSpec(id="a", agent="ag", instruction="i.md")],
        )

        state = rs.new_run(wf)
        rs.save(state)

        state_path = tmp_path / ".orchestrator" / "runs" / state.run_id / "state.json"
        raw = json.loads(state_path.read_text())
        del raw["spec_sessions"]  # simulate a state.json written before this epic
        state_path.write_text(json.dumps(raw))

        loaded = rs.load(state.run_id)
        assert loaded.spec_sessions == []


# ---------------------------------------------------------------------------
# AC-10: RunStateStore.load_workflow_snapshot delegates to the shared helper
# ---------------------------------------------------------------------------


class TestLoadWorkflowSnapshotDelegatesToSharedHelper:
    def test_delegates_to_module_level_load_workflow_snapshot_at(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store = LocalFsArtifactStore(str(tmp_path))
        rs = RunStateStore(str(tmp_path), store)
        sentinel = object()
        calls: list[tuple[Path, str]] = []

        def _fake_loader(run_dir: Path, sha: str) -> object:
            calls.append((run_dir, sha))
            return sentinel

        monkeypatch.setattr(runstate, "load_workflow_snapshot_at", _fake_loader)

        result = rs.load_workflow_snapshot("run-123", "deadbeef" * 8)

        assert result is sentinel
        assert calls == [(rs._root / "run-123", "deadbeef" * 8)]
