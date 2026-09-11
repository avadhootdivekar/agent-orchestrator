"""Structured event contract for per-task git isolation (E-Wk9Tz3 `T-Cx4Jf1` Part B).

Pins HLD §11 M9's `worktree.*` / `integration.*` / `scheduling.*` event set and the field
set every per-task `integration.*` line carries — plus the two amendments that ride on the
same emissions: S-5/AC-11 (`RunIntegrationState.tier_counts` is actually INCREMENTED, and
a task's `tier_reached`/`conflicted_count` survive a later clean attempt) and S-7/AC-12
(`worktree.retention_high` fires exactly ONCE per run).

Everything here drives `ao run` through `typer.testing.CliRunner` against real git repos
(CLAUDE.md: e2e from as outer a boundary as possible) and asserts on `run.log`'s structured
JSON lines, never on stdout prose. `FakeExecutor` writes tracked files inside each task's
worktree, so the conflicts are genuine rebase conflicts, not simulated ones.
"""

from __future__ import annotations

import json
import os
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

import agent_orchestrator.executors as executors_pkg
from agent_orchestrator.cli import app
from agent_orchestrator.engine import _WORKTREE_RETENTION_WARN_THRESHOLD
from agent_orchestrator.executors.fake import FakeExecutor

runner = CliRunner()


# ---------------------------------------------------------------------------------------
# The contract under test, spelled out once (HLD §11 M9) rather than inline per assertion.
# ---------------------------------------------------------------------------------------

#: Events the engine emits ONCE per settled integration attempt. Exactly one of these must
#: appear per terminal path — "no silent exits" (AC-7). Distinguishable from the
#: integrator's own per-repo lines of the same name by carrying the `duration_ms` field.
TERMINAL_EVENTS = frozenset(
    {
        "integration.merged",
        "integration.resolver_dispatched",
        "integration.rerun_dispatched",
        "integration.failed",
    }
)

#: `TaskIntegrationState.status` -> the terminal event that status must have been announced
#: by (AC-7's "every terminal path emits exactly one terminal event").
TERMINAL_EVENT_FOR_STATUS = {
    "integrated": "integration.merged",
    "conflict_resolver": "integration.resolver_dispatched",
    "conflict_rerun": "integration.rerun_dispatched",
    "failed": "integration.failed",
}

#: Fields HLD §11 M9 requires on every per-task `integration.*` line the engine emits.
#: `run_id` is injected by the run-scoped LoggerAdapter, so it is asserted separately.
REQUIRED_TERMINAL_FIELDS = ("task_id", "repo", "tier", "conflicted", "attempt", "duration_ms")

#: Conflict-marker text that must never reach a log line: `conflicted` is a COUNT, and even
#: the paths-carrying `integration.failed` never carries file CONTENT (NFR-1).
CONFLICT_MARKERS = ("<<<<<<<", "=======", ">>>>>>>")

#: Loggers inside `isolation/` that emit `integration.*`/`worktree.*` lines through their
#: own module-level logger rather than the run-scoped adapter, and so carry no `run_id`.
#: A known, recorded divergence from HLD §11 M9 -- see the event-contract test.
MODULE_LOGGER_SOURCES = frozenset(
    {
        "agent_orchestrator.isolation.resolvers",
        "agent_orchestrator.isolation.worktrees",
        "agent_orchestrator.isolation.git",
    }
)


# ---------------------------------------------------------------------------------------
# Helpers (same shape as tests/test_e2e_isolation.py's, kept local so this file owns its
# own fixtures and neither ticket's test file has to import from the other)
# ---------------------------------------------------------------------------------------


def _git(args: list[str], cwd: Path, *, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run git with a fixed identity (determinism); assert success unless *check* is False."""
    result = subprocess.run(
        [
            "git",
            "-c",
            "user.name=ao-test",
            "-c",
            "user.email=ao-test@example.invalid",
            *args,
        ],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
        env={
            **os.environ,
            "GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z",
            "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z",
        },
    )
    if check and result.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {result.stderr}")
    return result


def _create_test_repo(tmp_path: Path, files: dict[str, str] | None = None) -> Path:
    """A git repo at ``tmp_path/repo`` — the path `_setup_workflow`'s reposets point at."""
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _git(["init", "-q", "-b", "main"], repo)
    _git(["config", "user.name", "ao-test"], repo)
    _git(["config", "user.email", "ao-test@example.invalid"], repo)
    (repo / "README.md").write_text("hi\n")
    for name, content in (files or {}).items():
        (repo / name).write_text(content)
    _git(["add", "-A"], repo)
    _git(["commit", "-q", "-m", "base"], repo)
    return repo


def _env(tmp_path: Path, **overrides: str) -> dict[str, str]:
    """Environment for a CLI invocation over the *tmp_path* workspace.

    `AO_STATE_DIR` is a SIBLING of the workspace, never a child: `ao run` refuses a state
    dir inside the workspace root, because the per-task artifact-containment guard would
    then see one task's worktree as an ordinary workspace path and any isolated task could
    read and write its siblings' worktrees.
    """
    env = {
        "AO_WORKSPACE_ROOT": str(tmp_path),
        "HOME": str(tmp_path / "home"),
        "AO_STATE_DIR": str(tmp_path.parent / f"{tmp_path.name}-ao-state"),
    }
    env.update(overrides)
    return env


def _setup_workflow(
    tmp_path: Path,
    tasks: list[dict[str, Any]],
    integration_config: dict[str, Any] | None = None,
    **workflow_extras: Any,
) -> None:
    (tmp_path / "specs" / "instructions").mkdir(parents=True, exist_ok=True)
    for task in tasks:
        task.setdefault("instruction", f"specs/instructions/{task['id']}.md")
        (tmp_path / task["instruction"]).write_text(f"# {task['id']}\n")

    workflow = {
        "version": "1.0",
        "id": "events-wf",
        "repo_set": "rs",
        "defaults": {"isolation": "worktree"},
        "integration": {
            "sync_checkout": "never",
            "ladder": ["auto", "mechanical"],
            **(integration_config or {}),
        },
        "tasks": tasks,
        **workflow_extras,
    }
    (tmp_path / "workflow.json").write_text(json.dumps(workflow))
    (tmp_path / "reposets.json").write_text(
        json.dumps(
            {
                "version": "1.0",
                "repo_sets": {
                    "rs": {
                        "workspace_root": str(tmp_path),
                        "repos": [{"id": "core", "path": "repo", "role": "primary"}],
                    }
                },
            }
        )
    )
    (tmp_path / "agents.json").write_text(
        json.dumps({"version": "1.0", "agents": {"ag": {"executor": "fake"}}})
    )


def _patch_executor(
    monkeypatch: pytest.MonkeyPatch,
    repo_writes: dict[str, Any],
    *,
    write_outputs: bool = True,
) -> None:
    class _Patched(executors_pkg.DispatchExecutor):
        def __init__(self) -> None:
            super().__init__()
            self._fake = FakeExecutor(repo_writes=repo_writes, write_outputs=write_outputs)

    monkeypatch.setattr(executors_pkg, "DispatchExecutor", _Patched)


def _invoke_run(tmp_path: Path, *, max_parallel: str = "2") -> Any:
    return runner.invoke(
        app,
        [
            "run",
            "--workflow",
            str(tmp_path / "workflow.json"),
            "--reposets",
            str(tmp_path / "reposets.json"),
            "--agents",
            str(tmp_path / "agents.json"),
            "--max-parallel",
            max_parallel,
        ],
        env=_env(tmp_path),
    )


def _run_dir(tmp_path: Path) -> Path:
    return next((tmp_path / ".orchestrator" / "runs").iterdir())


def _log_lines(run_dir: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line) for line in (run_dir / "run.log").read_text().splitlines() if line.strip()
    ]


def _events(lines: list[dict[str, Any]], name: str) -> list[dict[str, Any]]:
    return [ln for ln in lines if ln.get("event") == name]


def _engine_terminal_events(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The engine's own per-settle terminal lines.

    `Integrator` emits per-REPO `integration.merged`/`integration.failed` lines of the same
    name; only the engine's per-TASK settle lines carry `duration_ms` (it measures the
    integrate() call it made), so that key is the discriminator — no name-mangling or
    logger-name sniffing needed.
    """
    return [ln for ln in lines if ln.get("event") in TERMINAL_EVENTS and "duration_ms" in ln]


# ---------------------------------------------------------------------------------------
# AC-7 — the event contract
# ---------------------------------------------------------------------------------------


class TestEventContract:
    def test_clean_t1_and_failure_emit_the_hld_m9_event_contract(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AC-7, one run covering all three outcomes.

        Wave 1 (`ok_a`/`ok_b`, dispatched together off the same base): both append a
        DIFFERENT line to the end of `f.txt`. Whichever lands first is the CLEAN
        integration (tier `auto`); the other hits a real rebase conflict that T1's union
        resolver legitimately resolves (tier `mechanical`).

        Wave 2 (`bad_a`/`bad_b`, gated behind wave 1 so they share a base with each other
        and not with wave 1): both rewrite line 1 of `g.txt`, which is deliberately NOT in
        `resolvers.union`. One lands clean; the other's conflict survives T1, and with
        neither `llm` nor `rerun` in the ladder the escalation goes straight to T4 —
        `integration.failed`.
        """
        _create_test_repo(tmp_path, files={"f.txt": "a\nb\nc\n", "g.txt": "g1\ng2\n"})
        _setup_workflow(
            tmp_path,
            [
                {"id": "ok_a", "agent": "ag", "outputs": ["output/ok_a.txt"]},
                {"id": "ok_b", "agent": "ag", "outputs": ["output/ok_b.txt"]},
                {
                    "id": "bad_a",
                    "agent": "ag",
                    "outputs": ["output/bad_a.txt"],
                    "depends_on": ["ok_a", "ok_b"],
                },
                {
                    "id": "bad_b",
                    "agent": "ag",
                    "outputs": ["output/bad_b.txt"],
                    "depends_on": ["ok_a", "ok_b"],
                },
            ],
            integration_config={
                "sync_checkout": "never",
                "ladder": ["auto", "mechanical"],
                "resolvers": {"union": ["f.txt"]},
            },
        )
        _patch_executor(
            monkeypatch,
            {
                "ok_a": {"core": {"f.txt": "a\nb\nc\nOK-A\n"}},
                "ok_b": {"core": {"f.txt": "a\nb\nc\nOK-B\n"}},
                "bad_a": {"core": {"g.txt": "g1-from-A\ng2\n"}},
                "bad_b": {"core": {"g.txt": "g1-from-B\ng2\n"}},
            },
        )

        _invoke_run(tmp_path)
        run_dir = _run_dir(tmp_path)
        lines = _log_lines(run_dir)
        state = json.loads((run_dir / "state.json").read_text())

        # --- the scenario really happened (never assert a contract over a vacuous run) ---
        assert _events(lines, "integration.conflict"), "fixture broken: no conflict occurred"
        resolved = _events(lines, "integration.resolved")
        assert resolved, "fixture broken: T1 never resolved anything"
        assert any(ev["tier"] == "mechanical" for ev in resolved)
        ti = state["task_integration"]
        assert any(v["status"] == "integrated" for v in ti.values())
        assert any(v["status"] == "failed" for v in ti.values())

        # --- (1) the HLD §11 M9 event set is present -------------------------------------
        seen = {ln.get("event") for ln in lines}
        for expected in (
            "worktree.created",
            "worktree.removed",
            "integration.activated",
            "integration.started",
            "integration.squashed",
            "integration.rebased",
            "integration.conflict",
            "integration.resolved",
            "integration.verify_started",
            "integration.verify_passed",
            "integration.merged",
            "integration.failed",
            "integration.sync_skipped",
            "integration.summary",
        ):
            assert expected in seen, f"HLD §11 M9 event missing from run.log: {expected}"

        # Exactly one run-scoped lifecycle line each.
        assert len(_events(lines, "integration.activated")) == 1
        assert len(_events(lines, "integration.summary")) == 1

        # --- (2) run_id on every line the RUN-SCOPED logger emits -------------------------
        # DIVERGENCE from HLD §11 M9's "every integration.* line carries run_id, task_id,
        # repo": the per-path / per-worktree lines emitted inside `isolation/resolvers.py`
        # and `isolation/worktrees.py` go through those modules' own module-level loggers,
        # not the run-scoped adapter `get_run_logger` hands `Integrator` -- so they carry no
        # `run_id` (and, for `resolvers.py`, no `task_id` either). Neither module is this
        # ticket's to edit, and `run.log` is a per-run file so nothing is actually
        # unattributable; recorded in STATUS.md for the docs ticket rather than papered
        # over. The lines the engine and the integrator emit are held to the full contract.
        run_id = state["run_id"]
        for ln in lines:
            event = ln.get("event") or ""
            if not event.startswith(("integration.", "worktree.")):
                continue
            if ln["logger"] in MODULE_LOGGER_SOURCES:
                continue
            assert ln.get("run_id") == run_id, f"{event} carries no run_id"

        # --- (3) the per-task field set --------------------------------------------------
        terminals = _engine_terminal_events(lines)
        assert terminals
        for ln in terminals:
            for key in REQUIRED_TERMINAL_FIELDS:
                assert key in ln, f"{ln['event']} is missing HLD §11 M9 field {key!r}"
            assert ln["task_id"] in ti
            assert isinstance(ln["repo"], list) and ln["repo"], "repo names the task's repos"
            assert isinstance(ln["conflicted"], int), "`conflicted` is a COUNT, never contents"
            assert isinstance(ln["duration_ms"], int)
            assert ln["tier"] in (None, "auto", "mechanical", "llm", "rerun")

        merged = [ln for ln in terminals if ln["event"] == "integration.merged"]
        assert merged
        for ln in merged:
            # HLD §11 M9 `from`/`to` shas, per repo.
            assert set(ln["head_from"]) == set(ln["head_to"])
            assert all(isinstance(sha, str) for sha in ln["head_to"].values())

        clean = [ln for ln in merged if ln["tier"] == "auto" and ln["conflicted"] == 0]
        assert clean, "expected at least one clean, zero-conflict integration"
        assert any(ln["tier"] == "mechanical" for ln in merged), "expected a T1 mechanical land"

        failed = [ln for ln in terminals if ln["event"] == "integration.failed"]
        assert failed
        for ln in failed:
            # AC-10 keeps the PATHS on the failure line (an operator needs them); the
            # count is what HLD §11 M9 pins, and neither is ever file content.
            assert ln["conflicted"] == len(ln["conflicted_paths"])
            assert ln["reason"]
            assert ln["worktrees"] and ln["branches"]

        # --- (4) `conflicted` is a count and no line ever carries file CONTENT ------------
        blob = (run_dir / "run.log").read_text()
        for marker in CONFLICT_MARKERS:
            assert marker not in blob, f"run.log leaked conflict-marker content: {marker!r}"

        # --- (5) every terminal path emitted exactly one terminal event -------------------
        per_task: Counter[str] = Counter(ln["task_id"] for ln in terminals)
        for tid, record in ti.items():
            assert per_task[tid] >= 1, f"{tid} settled with no terminal event (silent exit)"
            last = [ln for ln in terminals if ln["task_id"] == tid][-1]
            assert last["event"] == TERMINAL_EVENT_FOR_STATUS[record["status"]], (
                f"{tid} ended {record['status']!r} but its last terminal event was "
                f"{last['event']!r}"
            )

    def test_missing_declared_output_still_emits_one_terminal_event(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AC-7 "no silent exits", for the path that HAD one.

        An isolated task whose declared output never materialises is short-circuited by the
        R-2 worker-side gate, so `integrate()` is never called and no `integration.started`
        is ever logged. Before Part B this settle released the worktree and marked the task
        failed while emitting nothing at all — the one genuinely silent terminal path.
        """
        _create_test_repo(tmp_path)
        _setup_workflow(tmp_path, [{"id": "solo", "agent": "ag", "outputs": ["output/solo.txt"]}])
        _patch_executor(
            monkeypatch, {"solo": {"core": {"touched.txt": "x\n"}}}, write_outputs=False
        )

        _invoke_run(tmp_path, max_parallel="1")
        run_dir = _run_dir(tmp_path)
        lines = _log_lines(run_dir)
        state = json.loads((run_dir / "state.json").read_text())

        assert state["task_integration"]["solo"]["status"] == "failed"
        assert not _events(lines, "integration.started"), "integrate() must never have run"

        terminals = _engine_terminal_events(lines)
        assert len(terminals) == 1
        (only,) = terminals
        assert only["event"] == "integration.failed"
        assert only["task_id"] == "solo"
        assert only["reason"].startswith("missing_outputs:")
        assert only["tier"] is None and only["duration_ms"] is None
        assert only["conflicted"] == 0

    def test_overlap_preference_wave_line_only_at_soft(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """HLD §11 M9 `scheduling.overlap_preferred`: one line per wave, chosen ids +
        score, and ONLY when `resolve_overlap_preference` derives `soft` — which it does
        here because the workflow isolates its tasks.
        """
        _create_test_repo(tmp_path)
        _setup_workflow(
            tmp_path,
            [
                {"id": "t1", "agent": "ag", "outputs": ["output/t1.txt"], "touches": ["src/a.py"]},
                {"id": "t2", "agent": "ag", "outputs": ["output/t2.txt"], "touches": ["src/b.py"]},
            ],
        )
        _patch_executor(
            monkeypatch,
            {"t1": {"core": {"one.txt": "1\n"}}, "t2": {"core": {"two.txt": "2\n"}}},
        )

        _invoke_run(tmp_path)
        lines = _log_lines(_run_dir(tmp_path))
        waves = _events(lines, "scheduling.overlap_preferred")
        assert waves, "soft overlap preference must log the ordering it chose"
        for ln in waves:
            assert ln["chosen"]
            assert set(ln["scores"]) == set(ln["chosen"])
            assert all(isinstance(v, (int, float)) for v in ln["scores"].values())


# ---------------------------------------------------------------------------------------
# AC-11 / S-5 — tier_counts is INCREMENTED, and a task's tier survives a later clean attempt
# ---------------------------------------------------------------------------------------


def _teach_rerere(repo: Path, ours: str, theirs: str, resolution: str) -> None:
    """Prime ``$GIT_DIR/rr-cache`` with the resolution of the *ours*/*theirs* conflict.

    Teaching requires driving a genuinely conflicting rebase to COMPLETION (rerere only
    remembers a completed resolution), on throwaway branches deleted afterwards. Every
    worktree the engine later creates shares this repo's common dir, so the taught
    resolution is replayed automatically by the `-c rerere.enabled=true -c
    rerere.autoupdate=true` flags `isolation/git.py` already passes on every call.
    """
    rr = ["-c", "rerere.enabled=true", "-c", "rerere.autoupdate=true"]
    _git(["checkout", "-q", "-b", "teach-ours"], repo)
    (repo / "f.txt").write_text(ours)
    _git(["commit", "-qam", "teach ours"], repo)
    _git(["checkout", "-q", "main"], repo)
    _git(["checkout", "-q", "-b", "teach-theirs"], repo)
    (repo / "f.txt").write_text(theirs)
    _git(["commit", "-qam", "teach theirs"], repo)

    conflict = _git([*rr, "rebase", "teach-ours"], repo, check=False)
    assert conflict.returncode != 0, "teach rebase must conflict (fixture broken)"
    (repo / "f.txt").write_text(resolution)
    _git([*rr, "add", "f.txt"], repo)
    _git([*rr, "rebase", "--continue"], repo)

    _git(["checkout", "-q", "main"], repo)
    _git(["branch", "-qD", "teach-ours"], repo)
    _git(["branch", "-qD", "teach-theirs"], repo)


class TestTierCounts:
    def test_rerere_replay_increments_tier_counts_in_status_json(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AC-11/S-5: a rerere replay is the cheapest non-free tier there is — it lands with
        no agent, no operator and (under the default structural verify) no review at all.
        It must still be COUNTED, so an operator can see how much of a run's integration was
        absorbed by a cache primed in some earlier run.

        `RunIntegrationState.tier_counts` shipped as a field with `T-Sc7Rm2` and was
        surfaced in `ao status`/`status.json` by Part A, but nothing ever incremented it —
        it was `{}` on every real run. This pins the increment.
        """
        repo = _create_test_repo(tmp_path, files={"f.txt": "a\nb\nc\n"})
        _teach_rerere(repo, "a-A\nb\nc\n", "a-B\nb\nc\n", "a-RESOLVED\nb\nc\n")

        _setup_workflow(
            tmp_path,
            [
                {"id": "rr_a", "agent": "ag", "outputs": ["output/rr_a.txt"]},
                {"id": "rr_b", "agent": "ag", "outputs": ["output/rr_b.txt"]},
            ],
        )
        _patch_executor(
            monkeypatch,
            {
                "rr_a": {"core": {"f.txt": "a-A\nb\nc\n"}},
                "rr_b": {"core": {"f.txt": "a-B\nb\nc\n"}},
            },
        )

        result = _invoke_run(tmp_path)
        assert result.exit_code == 0, result.output

        run_dir = _run_dir(tmp_path)
        lines = _log_lines(run_dir)
        status = json.loads((run_dir / "status.json").read_text())

        # The replay genuinely happened: a conflict was reported with NO unresolved paths
        # (rerere had already staged every hunk) and T1 credited it as `mechanical`.
        conflicts = _events(lines, "integration.conflict")
        assert conflicts, "fixture broken: the rebase never conflicted"
        assert any(ev["paths"] == [] for ev in conflicts), "rerere did not replay"
        assert any(ev["tier"] == "mechanical" for ev in _events(lines, "integration.resolved"))

        tier_counts = status["integration"]["tier_counts"]
        assert tier_counts.get("mechanical") == 1, tier_counts
        assert tier_counts.get("auto") == 1, tier_counts
        assert sum(tier_counts.values()) == 2

        # ...and the same histogram rides on the run's own summary line.
        (summary,) = _events(lines, "integration.summary")
        assert summary["tier_counts"] == tier_counts

        # Per-task: the replaying task shows `mechanical`, the clean one `auto`.
        by_tier = {t["id"]: t["tier_reached"] for t in status["tasks"]}
        assert sorted(by_tier.values()) == ["auto", "mechanical"]

    def test_conflict_then_rerun_keeps_the_highest_tier_and_the_conflict_count(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """S-5, coordinator finding (2026-09-07): a task that conflicted, escalated to T3
        and then landed cleanly on a fresh base used to report `tier_reached: "auto"` and
        `conflicted_count: 0` — the successful final attempt overwrote the conflicting one,
        so `status.json` forgot the task was ever anything but free. Only `run.log`
        remembered.

        Semantics implemented: highest-tier-reached across attempts (`rerun` outranks
        `mechanical` outranks `auto`, per `models.DEFAULT_LADDER`), with the conflicting
        attempt's path count surviving a later clean one.
        """
        _create_test_repo(tmp_path, files={"f.txt": "a\nb\nc\n"})
        _setup_workflow(
            tmp_path,
            [
                {"id": "cr_a", "agent": "ag", "outputs": ["output/cr_a.txt"]},
                {"id": "cr_b", "agent": "ag", "outputs": ["output/cr_b.txt"]},
            ],
            integration_config={
                "sync_checkout": "never",
                # No `union` rule for f.txt -> T1 cannot resolve -> T3 rerun.
                "ladder": ["auto", "mechanical", "rerun"],
            },
        )
        _patch_executor(
            monkeypatch,
            {
                "cr_a": {"core": {"f.txt": "a-A\nb\nc\n"}},
                "cr_b": {"core": {"f.txt": "a-B\nb\nc\n"}},
            },
        )

        result = _invoke_run(tmp_path)
        assert result.exit_code == 0, result.output

        run_dir = _run_dir(tmp_path)
        lines = _log_lines(run_dir)
        status = json.loads((run_dir / "status.json").read_text())

        (rerun_ev,) = _events(lines, "integration.rerun_dispatched")
        rerun_task = rerun_ev["task_id"]
        assert rerun_ev["conflicted"] == 1
        assert rerun_ev["tier"] == "rerun"

        by_id = {t["id"]: t for t in status["tasks"]}
        assert by_id[rerun_task]["integration_status"] == "integrated"
        assert by_id[rerun_task]["tier_reached"] == "rerun", (
            "the clean rerun attempt must not erase the fact that this task conflicted"
        )
        assert by_id[rerun_task]["conflicted_count"] == 1

        # The run histogram credits every tier the run actually consumed, `rerun` included
        # — no IntegrationResult ever reports tier_reached == "rerun", so a naive
        # "count tier_reached" would make the most expensive tier permanently invisible.
        tier_counts = status["integration"]["tier_counts"]
        assert tier_counts["rerun"] == 1, tier_counts
        assert tier_counts["mechanical"] == 1, tier_counts
        assert tier_counts["auto"] == 2, tier_counts


# ---------------------------------------------------------------------------------------
# AC-12 / S-7 — worktree.retention_high, exactly once per run
# ---------------------------------------------------------------------------------------


class TestRetentionWarning:
    def test_retention_high_fires_exactly_once_per_run(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AC-12/S-7. `keep_worktrees: "on_failure"` (the default) retains EVERY failed
        task's worktree by design, so a systemic failure retains one per task. The warning
        must fire once — at the named threshold — and never again for the rest of the run,
        no matter how many further failures pile up; a warning repeated per failure is
        noise an operator learns to filter out, which defeats the point.

        Driven with `THRESHOLD + 1` uniformly-failing isolated tasks dispatched in ONE wave
        (`--max-parallel` == the task count) so the threshold is crossed strictly more than
        once: a task failure halts the run, and `_drain_remaining` then settles every
        still-in-flight sibling, so every one of them reaches the retention check. The
        latch, not the emission, is what is under test.
        """
        task_count = _WORKTREE_RETENTION_WARN_THRESHOLD + 1
        _create_test_repo(tmp_path)
        _setup_workflow(
            tmp_path,
            [
                {"id": f"f{i}", "agent": "ag", "outputs": [f"output/f{i}.txt"]}
                for i in range(task_count)
            ],
        )
        _patch_executor(
            monkeypatch,
            {f"f{i}": {"core": {f"t{i}.txt": "x\n"}} for i in range(task_count)},
            write_outputs=False,
        )

        _invoke_run(tmp_path, max_parallel=str(task_count))
        run_dir = _run_dir(tmp_path)
        lines = _log_lines(run_dir)
        state = json.loads((run_dir / "state.json").read_text())

        retained = [tid for tid, v in state["task_integration"].items() if v["status"] == "failed"]
        assert len(retained) == _WORKTREE_RETENTION_WARN_THRESHOLD + 1, (
            f"fixture broken: {len(retained)} retained worktrees cannot cross the threshold "
            f"({_WORKTREE_RETENTION_WARN_THRESHOLD}) more than once"
        )

        warnings = _events(lines, "worktree.retention_high")
        assert len(warnings) == 1, (
            f"expected exactly one retention warning for {len(retained)} retained worktrees, "
            f"got {len(warnings)}"
        )
        (warning,) = warnings
        assert warning["level"] == "WARNING"
        assert warning["threshold"] == _WORKTREE_RETENTION_WARN_THRESHOLD
        assert warning["retained"] >= _WORKTREE_RETENTION_WARN_THRESHOLD
        # S-7 deliberately does NOT hard-cap retention; the warning's whole job is to name
        # the remedy instead of silently deleting the evidence of a systemic failure.
        assert warning["remedy"] == "ao prune --worktrees-only"

    def test_no_retention_warning_when_worktrees_are_never_kept(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`keep_worktrees: "never"` releases every worktree on failure, so nothing
        accumulates and there is nothing to warn about — the warning must stay silent
        rather than pointing an operator at a `ao prune` that would find nothing.
        """
        task_count = _WORKTREE_RETENTION_WARN_THRESHOLD + 1
        _create_test_repo(tmp_path)
        _setup_workflow(
            tmp_path,
            [
                {"id": f"n{i}", "agent": "ag", "outputs": [f"output/n{i}.txt"]}
                for i in range(task_count)
            ],
            integration_config={"sync_checkout": "never", "keep_worktrees": "never"},
        )
        _patch_executor(
            monkeypatch,
            {f"n{i}": {"core": {f"t{i}.txt": "x\n"}} for i in range(task_count)},
            write_outputs=False,
        )

        _invoke_run(tmp_path, max_parallel=str(task_count))
        lines = _log_lines(_run_dir(tmp_path))
        assert _events(lines, "worktree.retention_high") == []
