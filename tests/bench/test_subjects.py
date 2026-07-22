"""Unit tests for bench/subjects.py: Subject ABC, SUBJECT_REGISTRY, and the three MVP
subjects (AC 1-7 of T-Sbj9Ka).

Real subjects (`ClaudeCliSubject`, `AoWorkflowSubject`) are exercised with
`agent_orchestrator.bench.subjects._run_with_timeout` monkeypatched -- the seam both
share for spawn+capture+timeout+kill -- so these tests are fast, deterministic, and
network-free (mirrors this repo's own `ClaudeCliExecutor` test style of mocking at the
subprocess boundary, not deeper). `test_run_with_timeout_kills_process_group_on_timeout`
separately proves that seam's real kill mechanics using a plain `python -c
"time.sleep(...)"` child (no `claude`/`ao`, no network, bounded at ~1s).

Exactly one test (`test_claude_cli_subject_real_haiku_smoke`) is `@pytest.mark.real_llm`
-- skipped unless `AO_E2E_REAL_LLM=1` is set AND `claude` is on PATH (mirrors
`tests/playground/harness.py`'s `requires_claude()` + `conftest.py`'s real_llm gate,
reimplemented locally here since `tests/playground/` and `tests/bench/conftest.py` are
outside this task's file ownership).
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
import uuid
from pathlib import Path

import pytest

from agent_orchestrator.artifacts import LocalFsArtifactStore
from agent_orchestrator.bench import subjects
from agent_orchestrator.bench.errors import SubjectError
from agent_orchestrator.bench.registries import SUBJECT_REGISTRY
from agent_orchestrator.bench.spec import BenchTask, GraderConfig, SubjectSpec
from agent_orchestrator.bench.subjects import (
    AoWorkflowSubject,
    ClaudeCliSubject,
    FakeSubject,
)
from agent_orchestrator.bench.workspace import BENCH_WORKSPACE_ROOT, RunContext
from agent_orchestrator.models import RunState, TaskRunState
from agent_orchestrator.runstate import RunStateStore

# ---------------------------------------------------------------------------
# Shared builders
# ---------------------------------------------------------------------------


def _task(task_id: str = "t1") -> BenchTask:
    return BenchTask(
        id=task_id,
        category="bugfix",
        instruction=f"tasks/{task_id}/instruction.md",
        fixture=f"tasks/{task_id}/fixture",
        grader=GraderConfig(type="fake"),
    )


def _subject_spec(
    *, subject_id: str = "fake-pass", subject_type: str = "fake", **extra: object
) -> SubjectSpec:
    data: dict[str, object] = {"version": "1.0", "id": subject_id, "type": subject_type}
    data.update(extra)
    return SubjectSpec(**data)


def _make_ctx(
    base: Path,
    *,
    max_turns: int | None = None,
    budget_total: int | None = None,
    subject_base_dir: Path | None = None,
    timeout_seconds: int = 30,
) -> RunContext:
    repo_dir = base / "repo"
    repo_dir.mkdir(exist_ok=True)
    capture_dir = base / "capture"
    capture_dir.mkdir(exist_ok=True)
    return RunContext(
        workspace=str(base),
        repo_dir=str(repo_dir),
        instruction_path=str(repo_dir / "INSTRUCTION.md"),
        capture_dir=str(capture_dir),
        timeout_seconds=timeout_seconds,
        max_turns=max_turns,
        budget_total=budget_total,
        subject_base_dir=str(subject_base_dir) if subject_base_dir is not None else None,
    )


@pytest.fixture()
def binaries_present(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pretend `claude`/`uv` are on PATH, regardless of the actual host -- keeps the
    PATH-probe branch deterministic across environments (AC2 tests supply their own
    `shutil.which` override instead of this fixture).
    """
    monkeypatch.setattr(subjects.shutil, "which", lambda name: f"/usr/bin/{name}")


def _write_fake_run_state(
    ws: Path,
    run_id: str,
    *,
    cost: float = 0.05,
    in_tokens: int = 200,
    out_tokens: int = 80,
    attempts: int = 1,
) -> None:
    """Persist a minimal, real `state.json` via `RunStateStore` (not hand-authored
    JSON) so `AoWorkflowSubject`'s `compute_run_usage_totals`/`RunStateStore.load`
    reuse is exercised against the exact on-disk shape the real engine produces.
    """
    store = LocalFsArtifactStore(str(ws))
    rs_store = RunStateStore(str(ws), store)
    state = RunState(
        run_id=run_id,
        workflow_id="ao-epic",
        repo_set="default-set",
        started_at="2026-07-22T00:00:00+00:00",
        updated_at="2026-07-22T00:00:00+00:00",
        status="succeeded",
        tasks={
            "implement": TaskRunState(
                status="succeeded",
                attempts=attempts,
                cumulative_input_tokens=in_tokens,
                cumulative_output_tokens=out_tokens,
                cumulative_cost_usd=cost,
            ),
        },
    )
    rs_store.save(state)


def _write_reposet_template(path: Path, *, repo_set_id: str = "default-set") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "version": "1.0",
                "repo_sets": {
                    repo_set_id: {
                        "description": "template",
                        "workspace_root": "PLACEHOLDER",
                        "repos": [{"id": "core", "path": "PLACEHOLDER", "role": "primary"}],
                    }
                },
            }
        )
    )


def _ao_subject_spec(base_dir: Path, **extra: object) -> SubjectSpec:
    (base_dir / "workflow.json").write_text("{}")
    _write_reposet_template(base_dir / "reposet.json")
    (base_dir / "agents.json").write_text("{}")
    data: dict[str, object] = {
        "workflow": "workflow.json",
        "reposets": "reposet.json",
        "agents": "agents.json",
    }
    data.update(extra)
    return _subject_spec(subject_id="ao-epic", subject_type="ao_workflow", **data)


# ---------------------------------------------------------------------------
# Registration (AC7 companion -- all three subjects self-register at import time)
# ---------------------------------------------------------------------------


def test_all_subjects_registered() -> None:
    assert SUBJECT_REGISTRY["claude_cli"] is ClaudeCliSubject
    assert SUBJECT_REGISTRY["ao_workflow"] is AoWorkflowSubject
    assert SUBJECT_REGISTRY["fake"] is FakeSubject


# ---------------------------------------------------------------------------
# FakeSubject (AC1)
# ---------------------------------------------------------------------------


def test_fake_subject_copy_solution_applies_fix_and_removes_marker(tmp_path: Path) -> None:
    ctx = _make_ctx(tmp_path)
    solution_dir = Path(ctx.repo_dir) / ".bench-solution"
    solution_dir.mkdir()
    (solution_dir / "fix.py").write_text("fixed\n")
    spec = _subject_spec(
        scripted_effect="copy-solution", fake_cost=0.01, fake_tokens={"in": 10, "out": 5}
    )

    result = FakeSubject(spec).run(_task(), ctx)

    assert result.status == "succeeded"
    assert (Path(ctx.repo_dir) / "fix.py").read_text() == "fixed\n"
    assert not solution_dir.exists()
    assert result.cost_usd == 0.01
    assert result.input_tokens == 10
    assert result.output_tokens == 5
    assert result.wall_clock_seconds >= 0.0
    assert result.argv == []
    assert result.capture_dir == ctx.capture_dir


def test_fake_subject_noop_leaves_workspace_unchanged(tmp_path: Path) -> None:
    ctx = _make_ctx(tmp_path)
    (Path(ctx.repo_dir) / "app.py").write_text("original\n")
    spec = _subject_spec(scripted_effect="noop")

    result = FakeSubject(spec).run(_task(), ctx)

    assert result.status == "succeeded"
    assert (Path(ctx.repo_dir) / "app.py").read_text() == "original\n"


def test_fake_subject_default_effect_is_noop(tmp_path: Path) -> None:
    ctx = _make_ctx(tmp_path)
    spec = _subject_spec()

    result = FakeSubject(spec).run(_task(), ctx)

    assert result.status == "succeeded"
    assert list(Path(ctx.repo_dir).iterdir()) == []


@pytest.mark.parametrize(
    ("effect", "expected_status"),
    [("fail", "failed"), ("timeout", "timed_out"), ("error", "error")],
)
def test_fake_subject_status_scripted_effects(
    tmp_path: Path, effect: str, expected_status: str
) -> None:
    ctx = _make_ctx(tmp_path)
    spec = _subject_spec(scripted_effect=effect)

    result = FakeSubject(spec).run(_task(), ctx)

    assert result.status == expected_status
    if expected_status == "error":
        assert result.raw_error is not None
        assert effect in result.raw_error
    else:
        assert result.raw_error is None


def test_fake_subject_unknown_effect_raises_subject_error(tmp_path: Path) -> None:
    ctx = _make_ctx(tmp_path)
    spec = _subject_spec(scripted_effect="bogus-effect")

    with pytest.raises(SubjectError, match="unknown scripted_effect"):
        FakeSubject(spec).run(_task(), ctx)


def test_fake_subject_no_sleeping(tmp_path: Path) -> None:
    ctx = _make_ctx(tmp_path)
    spec = _subject_spec(scripted_effect="noop")

    t0 = time.monotonic()
    result = FakeSubject(spec).run(_task(), ctx)
    elapsed = time.monotonic() - t0

    assert elapsed < 0.5  # no scripted delay -- real near-zero wall clock
    assert result.wall_clock_seconds < 0.5


# ---------------------------------------------------------------------------
# ClaudeCliSubject -- PATH probe (AC2)
# ---------------------------------------------------------------------------


def test_claude_cli_subject_missing_binary_returns_error_not_exception(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(subjects.shutil, "which", lambda _name: None)
    ctx = _make_ctx(tmp_path)
    spec = _subject_spec(subject_id="claude-haiku", subject_type="claude_cli")

    result = ClaudeCliSubject(spec).run(_task(), ctx)

    assert result.status == "error"
    assert result.raw_error is not None
    assert "PATH" in result.raw_error


# ---------------------------------------------------------------------------
# ClaudeCliSubject -- mocked subprocess (reuse-of-core-parsing proof)
# ---------------------------------------------------------------------------


def _transcript_jsonl(*, cost: float, in_tokens: int, out_tokens: int, assistant_turns: int) -> str:
    events: list[dict] = [{"type": "system", "subtype": "init"}]
    for i in range(assistant_turns):
        events.append(
            {"type": "assistant", "message": {"content": [{"type": "text", "text": f"turn {i}"}]}}
        )
    events.append(
        {
            "type": "result",
            "subtype": "success",
            "total_cost_usd": cost,
            "usage": {
                "input_tokens": in_tokens,
                "output_tokens": out_tokens,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
            },
        }
    )
    return "\n".join(json.dumps(e) for e in events) + "\n"


def test_claude_cli_subject_success_reuses_core_usage_parsing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, binaries_present: None
) -> None:
    ctx = _make_ctx(tmp_path, max_turns=None)
    captured: dict[str, object] = {}

    def fake_run_with_timeout(argv, *, cwd, stdout_path, stderr_path, timeout_seconds, env=None):
        captured["argv"] = argv
        captured["cwd"] = cwd
        stdout_path.write_text(
            _transcript_jsonl(cost=0.0123, in_tokens=100, out_tokens=50, assistant_turns=2)
        )
        stderr_path.write_text("")
        return 0, False

    monkeypatch.setattr(subjects, "_run_with_timeout", fake_run_with_timeout)
    spec = _subject_spec(
        subject_id="claude-haiku",
        subject_type="claude_cli",
        model="claude-haiku-4-5",
        permission_mode="bypassPermissions",
        max_turns=5,
    )

    result = ClaudeCliSubject(spec).run(_task(), ctx)

    assert result.status == "succeeded"
    assert result.cost_usd == 0.0123
    assert result.input_tokens == 100
    assert result.output_tokens == 50
    assert result.turns == 2
    assert result.attempts == 1
    assert result.resolved_model == "claude-haiku-4-5"
    assert result.resolved_permission_mode == "bypassPermissions"

    argv = captured["argv"]
    assert "--model" in argv and "claude-haiku-4-5" in argv
    assert "--permission-mode" in argv and "bypassPermissions" in argv
    assert "--max-turns" in argv and "5" in argv
    assert "--output-format" in argv and "stream-json" in argv
    assert "--verbose" in argv
    assert captured["cwd"] == ctx.repo_dir
    assert Path(ctx.capture_dir, "transcript.jsonl").exists()


def test_claude_cli_subject_defaults_permission_mode_and_omits_model(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, binaries_present: None
) -> None:
    ctx = _make_ctx(tmp_path)
    captured: dict[str, object] = {}

    def fake_run_with_timeout(argv, *, cwd, stdout_path, stderr_path, timeout_seconds, env=None):
        captured["argv"] = argv
        stdout_path.write_text("")
        stderr_path.write_text("")
        return 0, False

    monkeypatch.setattr(subjects, "_run_with_timeout", fake_run_with_timeout)
    spec = _subject_spec(subject_id="claude-default", subject_type="claude_cli")  # no model set

    result = ClaudeCliSubject(spec).run(_task(), ctx)

    assert result.resolved_permission_mode == "bypassPermissions"
    assert result.resolved_model is None
    assert "--model" not in captured["argv"]
    assert ctx.instruction_path in captured["argv"][2]  # argv[2] is the rendered prompt


def test_claude_cli_subject_nonzero_exit_returns_failed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, binaries_present: None
) -> None:
    ctx = _make_ctx(tmp_path)

    def fake_run_with_timeout(argv, *, cwd, stdout_path, stderr_path, timeout_seconds, env=None):
        stdout_path.write_text("")
        stderr_path.write_text("boom: something broke\n")
        return 1, False

    monkeypatch.setattr(subjects, "_run_with_timeout", fake_run_with_timeout)
    spec = _subject_spec(subject_type="claude_cli")

    result = ClaudeCliSubject(spec).run(_task(), ctx)

    assert result.status == "failed"
    assert result.raw_error is not None
    assert "boom" in result.raw_error


def test_claude_cli_subject_timeout_returns_timed_out_with_partial_capture(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, binaries_present: None
) -> None:
    ctx = _make_ctx(tmp_path)

    def fake_run_with_timeout(argv, *, cwd, stdout_path, stderr_path, timeout_seconds, env=None):
        stdout_path.write_text('{"type": "assistant", "message": {"content": []}}\n')
        stderr_path.write_text("")
        return -9, True

    monkeypatch.setattr(subjects, "_run_with_timeout", fake_run_with_timeout)
    spec = _subject_spec(subject_type="claude_cli")

    result = ClaudeCliSubject(spec).run(_task(), ctx)

    assert result.status == "timed_out"
    assert result.raw_error is not None and "timed out" in result.raw_error
    assert Path(ctx.capture_dir, "transcript.jsonl").read_text() != ""


def test_claude_cli_subject_quota_exhausted_returns_error_not_solve(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, binaries_present: None
) -> None:
    ctx = _make_ctx(tmp_path)

    def fake_run_with_timeout(argv, *, cwd, stdout_path, stderr_path, timeout_seconds, env=None):
        stdout_path.write_text("")
        stderr_path.write_text("Error: you've hit your daily limit. Try again later.\n")
        return 1, False

    monkeypatch.setattr(subjects, "_run_with_timeout", fake_run_with_timeout)
    spec = _subject_spec(subject_type="claude_cli")

    result = ClaudeCliSubject(spec).run(_task(), ctx)

    assert result.status == "error"
    assert result.raw_error is not None
    assert "quota" in result.raw_error.lower()


def test_claude_cli_subject_spawn_failure_returns_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, binaries_present: None
) -> None:
    ctx = _make_ctx(tmp_path)

    def fake_run_with_timeout(*_a, **_k):
        raise OSError("No such file or directory: 'claude'")

    monkeypatch.setattr(subjects, "_run_with_timeout", fake_run_with_timeout)
    spec = _subject_spec(subject_type="claude_cli")

    result = ClaudeCliSubject(spec).run(_task(), ctx)

    assert result.status == "error"
    assert result.raw_error is not None
    assert "Failed to spawn" in result.raw_error


# ---------------------------------------------------------------------------
# AoWorkflowSubject -- PATH probe / spec validation (AC2)
# ---------------------------------------------------------------------------


def test_ao_workflow_subject_missing_required_fields_returns_error(tmp_path: Path) -> None:
    ctx = _make_ctx(tmp_path)
    # no workflow/reposets/agents fields set
    spec = _subject_spec(subject_id="ao-epic", subject_type="ao_workflow")

    result = AoWorkflowSubject(spec).run(_task(), ctx)

    assert result.status == "error"
    assert result.raw_error is not None
    assert "missing required field" in result.raw_error


def test_ao_workflow_subject_uv_missing_returns_error_not_exception(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ctx = _make_ctx(tmp_path, subject_base_dir=tmp_path)
    spec = _ao_subject_spec(tmp_path)
    monkeypatch.setattr(subjects.shutil, "which", lambda _name: None)

    result = AoWorkflowSubject(spec).run(_task(), ctx)

    assert result.status == "error"
    assert result.raw_error is not None
    assert "uv" in result.raw_error.lower()


def test_ao_workflow_subject_missing_template_file_returns_error(
    tmp_path: Path, binaries_present: None
) -> None:
    ctx = _make_ctx(tmp_path, subject_base_dir=tmp_path)
    spec = _subject_spec(
        subject_id="ao-epic",
        subject_type="ao_workflow",
        workflow="nope-workflow.json",
        reposets="nope-reposet.json",
        agents="nope-agents.json",
    )

    result = AoWorkflowSubject(spec).run(_task(), ctx)

    assert result.status == "error"
    assert result.raw_error is not None
    assert "template not found" in result.raw_error


# ---------------------------------------------------------------------------
# AoWorkflowSubject -- mocked subprocess (reuse-of-core-usage-totals proof)
# ---------------------------------------------------------------------------


def test_ao_workflow_subject_success_computes_usage_from_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, binaries_present: None
) -> None:
    ctx = _make_ctx(tmp_path, subject_base_dir=tmp_path)
    spec = _ao_subject_spec(
        tmp_path, model="claude-haiku-4-5", max_turns=8, max_parallel=2, budget_total=10000
    )
    captured: dict[str, object] = {}

    def fake_run_with_timeout(argv, *, cwd, stdout_path, stderr_path, timeout_seconds, env=None):
        captured["argv"] = argv
        captured["cwd"] = cwd
        captured["env"] = env
        stdout_path.write_text("ao run ok\n")
        stderr_path.write_text("")
        _write_fake_run_state(
            Path(ctx.workspace),
            "ao-epic-20260722T000000Z",
            cost=0.05,
            in_tokens=200,
            out_tokens=80,
            attempts=3,
        )
        return 0, False

    monkeypatch.setattr(subjects, "_run_with_timeout", fake_run_with_timeout)

    result = AoWorkflowSubject(spec).run(_task(), ctx)

    assert result.status == "succeeded"
    assert result.cost_usd == pytest.approx(0.05)
    assert result.input_tokens == 200
    assert result.output_tokens == 80
    assert result.attempts == 3
    assert result.turns is None
    assert result.resolved_model == "claude-haiku-4-5"

    assert captured["cwd"] == subjects.REPO_ROOT
    argv = captured["argv"]
    assert "--budget-total" in argv and "10000" in argv
    env = captured["env"]
    assert env["AO_MODEL"] == "claude-haiku-4-5"
    assert env["AO_MAX_TURNS"] == "8"
    assert env["AO_MAX_PARALLEL"] == "2"
    assert env["AO_WORKSPACE_ROOT"] == ctx.workspace

    rendered = json.loads((Path(ctx.workspace) / "reposet.rendered.json").read_text())
    rs = rendered["repo_sets"]["default-set"]
    assert rs["workspace_root"] == ctx.workspace
    assert rs["repos"][0]["path"] == ctx.repo_dir


def test_ao_workflow_subject_ctx_budget_and_max_turns_override_spec(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, binaries_present: None
) -> None:
    ctx = _make_ctx(tmp_path, subject_base_dir=tmp_path, max_turns=99, budget_total=1)
    spec = _ao_subject_spec(tmp_path, max_turns=8, budget_total=10000)
    captured: dict[str, object] = {}

    def fake_run_with_timeout(argv, *, cwd, stdout_path, stderr_path, timeout_seconds, env=None):
        captured["argv"] = argv
        captured["env"] = env
        stdout_path.write_text("")
        stderr_path.write_text("")
        _write_fake_run_state(Path(ctx.workspace), "ao-epic-run")
        return 0, False

    monkeypatch.setattr(subjects, "_run_with_timeout", fake_run_with_timeout)

    AoWorkflowSubject(spec).run(_task(), ctx)

    assert captured["env"]["AO_MAX_TURNS"] == "99"
    assert "--budget-total" in captured["argv"] and "1" in captured["argv"]


def test_ao_workflow_subject_zero_run_dirs_raises_subject_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, binaries_present: None
) -> None:
    ctx = _make_ctx(tmp_path, subject_base_dir=tmp_path)
    spec = _ao_subject_spec(tmp_path)

    def fake_run_with_timeout(argv, *, cwd, stdout_path, stderr_path, timeout_seconds, env=None):
        stdout_path.write_text("")
        stderr_path.write_text("ao run failed before writing any state\n")
        return 1, False

    monkeypatch.setattr(subjects, "_run_with_timeout", fake_run_with_timeout)

    with pytest.raises(SubjectError, match="no run directory"):
        AoWorkflowSubject(spec).run(_task(), ctx)


def test_ao_workflow_subject_multiple_run_dirs_raises_subject_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, binaries_present: None
) -> None:
    ctx = _make_ctx(tmp_path, subject_base_dir=tmp_path)
    spec = _ao_subject_spec(tmp_path)

    def fake_run_with_timeout(argv, *, cwd, stdout_path, stderr_path, timeout_seconds, env=None):
        stdout_path.write_text("")
        stderr_path.write_text("")
        _write_fake_run_state(Path(ctx.workspace), "run-a")
        _write_fake_run_state(Path(ctx.workspace), "run-b")
        return 0, False

    monkeypatch.setattr(subjects, "_run_with_timeout", fake_run_with_timeout)

    with pytest.raises(SubjectError, match="expected exactly one run dir"):
        AoWorkflowSubject(spec).run(_task(), ctx)


def test_ao_workflow_subject_missing_state_json_cost_none_still_graded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, binaries_present: None
) -> None:
    ctx = _make_ctx(tmp_path, subject_base_dir=tmp_path)
    spec = _ao_subject_spec(tmp_path)

    def fake_run_with_timeout(argv, *, cwd, stdout_path, stderr_path, timeout_seconds, env=None):
        stdout_path.write_text("")
        stderr_path.write_text("")
        run_dir = Path(ctx.workspace) / ".orchestrator" / "runs" / "ao-epic-mystery"
        run_dir.mkdir(parents=True)  # a run dir exists, but no state.json inside it
        return 0, False

    monkeypatch.setattr(subjects, "_run_with_timeout", fake_run_with_timeout)

    result = AoWorkflowSubject(spec).run(_task(), ctx)

    assert result.status == "succeeded"
    assert result.cost_usd is None
    assert result.input_tokens is None
    assert result.attempts is None


def test_ao_workflow_subject_timeout_still_grades_partial_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, binaries_present: None
) -> None:
    ctx = _make_ctx(tmp_path, subject_base_dir=tmp_path)
    spec = _ao_subject_spec(tmp_path)

    def fake_run_with_timeout(argv, *, cwd, stdout_path, stderr_path, timeout_seconds, env=None):
        stdout_path.write_text("")
        stderr_path.write_text("")
        _write_fake_run_state(Path(ctx.workspace), "ao-epic-partial", cost=0.02)
        return -9, True

    monkeypatch.setattr(subjects, "_run_with_timeout", fake_run_with_timeout)

    result = AoWorkflowSubject(spec).run(_task(), ctx)

    assert result.status == "timed_out"
    assert result.raw_error is not None and "timed out" in result.raw_error
    assert result.cost_usd == pytest.approx(0.02)  # partial run still cost-attributed


def test_ao_workflow_subject_nonzero_exit_still_grades_partial_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, binaries_present: None
) -> None:
    ctx = _make_ctx(tmp_path, subject_base_dir=tmp_path)
    spec = _ao_subject_spec(tmp_path)

    def fake_run_with_timeout(argv, *, cwd, stdout_path, stderr_path, timeout_seconds, env=None):
        stdout_path.write_text("")
        stderr_path.write_text("second task crashed\n")
        _write_fake_run_state(Path(ctx.workspace), "ao-epic-failed", cost=0.01)
        return 1, False

    monkeypatch.setattr(subjects, "_run_with_timeout", fake_run_with_timeout)

    result = AoWorkflowSubject(spec).run(_task(), ctx)

    assert result.status == "failed"
    assert result.raw_error is not None and "crashed" in result.raw_error
    assert result.cost_usd == pytest.approx(0.01)


# ---------------------------------------------------------------------------
# _run_with_timeout -- real (non-claude, non-network) process-group kill mechanics
# ---------------------------------------------------------------------------


def test_run_with_timeout_kills_process_group_on_timeout(tmp_path: Path) -> None:
    stdout_path = tmp_path / "out.txt"
    stderr_path = tmp_path / "err.txt"
    argv = [sys.executable, "-c", "import time; time.sleep(5)"]

    t0 = time.monotonic()
    returncode, timed_out = subjects._run_with_timeout(
        argv, cwd=str(tmp_path), stdout_path=stdout_path, stderr_path=stderr_path, timeout_seconds=1
    )
    elapsed = time.monotonic() - t0

    assert timed_out is True
    assert elapsed < 4.0  # killed promptly, not left to run the full 5s sleep
    assert returncode != 0
    assert stdout_path.exists() and stderr_path.exists()  # capture files always created


def test_run_with_timeout_returns_zero_on_success(tmp_path: Path) -> None:
    stdout_path = tmp_path / "out.txt"
    stderr_path = tmp_path / "err.txt"
    argv = [sys.executable, "-c", "print('hi')"]

    returncode, timed_out = subjects._run_with_timeout(
        argv,
        cwd=str(tmp_path),
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        timeout_seconds=10,
    )

    assert timed_out is False
    assert returncode == 0
    assert stdout_path.read_text().strip() == "hi"


# ---------------------------------------------------------------------------
# [real_llm] ClaudeCliSubject end-to-end at haiku (AC3)
# ---------------------------------------------------------------------------


@pytest.fixture()
def bench_ws_target() -> Path:
    (BENCH_WORKSPACE_ROOT / "tests").mkdir(parents=True, exist_ok=True)
    return BENCH_WORKSPACE_ROOT / "tests" / f"ws-{uuid.uuid4().hex[:12]}"


@pytest.mark.real_llm
def test_claude_cli_subject_real_haiku_smoke(bench_ws_target: Path) -> None:
    if os.environ.get("AO_E2E_REAL_LLM") != "1":
        pytest.skip(
            "real_llm tier disabled; set AO_E2E_REAL_LLM=1 to enable "
            "(e.g. AO_E2E_REAL_LLM=1 uv run pytest -m real_llm)"
        )
    if shutil.which("claude") is None:
        pytest.skip("claude CLI binary not found on PATH; skipping real_llm tier")

    repo_dir = bench_ws_target / "repo"
    repo_dir.mkdir(parents=True)
    capture_dir = bench_ws_target / "capture"
    capture_dir.mkdir()
    instruction_path = repo_dir / "INSTRUCTION.md"
    instruction_path.write_text("Reply with exactly the single word OK. Do not use any tools.\n")

    ctx = RunContext(
        workspace=str(bench_ws_target),
        repo_dir=str(repo_dir),
        instruction_path=str(instruction_path),
        capture_dir=str(capture_dir),
        timeout_seconds=120,
        max_turns=3,
    )
    spec = _subject_spec(
        subject_id="claude-haiku-smoke",
        subject_type="claude_cli",
        model="haiku",
        permission_mode="bypassPermissions",
        max_turns=3,
        prompt_template=(
            "Reply with exactly the single word OK. Do not read {instruction} or use any "
            "tools; just answer directly."
        ),
    )

    result = ClaudeCliSubject(spec).run(_task(), ctx)

    # Plumbing assertion, not a score assertion (ASSUMPTION A5: only the harness
    # scaffolding is deterministic) -- the point is that cost/tokens/capture are
    # populated from the REUSED core parser, not that the model behaved as asked.
    assert result.status in ("succeeded", "failed")
    assert result.cost_usd is not None
    assert result.input_tokens is not None
    assert result.output_tokens is not None
    transcript = Path(ctx.capture_dir, "transcript.jsonl")
    assert transcript.exists()
    assert transcript.stat().st_size > 0
