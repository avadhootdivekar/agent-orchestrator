"""Unit tests for `survival.py` (diff survival) using throwaway git repos."""

from __future__ import annotations

import json
import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agent_orchestrator import survival
from agent_orchestrator.models import (
    RunIntegrationState,
    RunState,
    TaskIntegrationState,
    TaskRunState,
)
from agent_orchestrator.survival import (
    FLAG_LIKELY_WORTHLESS,
    FLAG_REVERTED,
    compute_survival,
    current_heads,
    parse_added_lines,
    record_git_start,
    record_landed_ranges,
    validate_ref,
)

KEY = "repo-key"
T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _isolated_git_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The host's git config (global identity, hooks, diff settings) must not leak in."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("AO_STATE_DIR", str(tmp_path / "ao-state"))


def git(repo: Path, *args: str, env: dict[str, str] | None = None) -> str:
    cp = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        check=False,
        env={**os.environ, **(env or {})},
    )
    assert cp.returncode == 0, cp.stderr.decode()
    return cp.stdout.decode().strip()


def make_repo(tmp_path: Path, name: str = "repo") -> Path:
    repo = tmp_path / name
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.name", "ao-test")
    git(repo, "config", "user.email", "ao-test@example.invalid")
    (repo / "README.md").write_text("base readme\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "base")
    return repo


def commit(repo: Path, files: dict[str, str | bytes | None], msg: str = "c", when: str = "") -> str:
    """Write (or delete, value None) files and commit; returns the new HEAD sha."""
    for rel, content in files.items():
        p = repo / rel
        if content is None:
            git(repo, "rm", "-q", "-f", rel)
            continue
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            p.write_bytes(content)
        else:
            p.write_text(content)
        git(repo, "add", rel)
    env = {"GIT_COMMITTER_DATE": when, "GIT_AUTHOR_DATE": when} if when else None
    git(repo, "commit", "-q", "-m", msg, env=env)
    return git(repo, "rev-parse", "HEAD")


def lines(prefix: str, n: int) -> str:
    return "".join(f"{prefix} statement number {i}\n" for i in range(n))


def iso_state(repo: Path, sha: str, tid: str = "t1") -> RunState:
    return RunState(
        run_id="r1",
        workflow_id="wf",
        repo_set="rs",
        started_at=T0.isoformat(),
        updated_at=(T0 + timedelta(hours=1)).isoformat(),
        git_repos={KEY: str(repo)},
        integration=RunIntegrationState(active=True),
        task_integration={
            tid: TaskIntegrationState(isolation="worktree", squash_commits={KEY: sha})
        },
    )


def one_task(report, tid: str = "t1"):
    run = report.runs[0]
    return next(t for t in run.tasks if t.task_id == tid)


class TestMetric:
    def test_fully_survives(self, tmp_path: Path) -> None:
        repo = make_repo(tmp_path)
        sha = commit(repo, {"src/a.py": lines("alpha", 12)})
        row = one_task(compute_survival([iso_state(repo, sha)], str(repo)))
        assert (row.attribution, row.commits, row.files) == ("isolation", 1, 1)
        assert (row.lines_added, row.lines_survived, row.survival_rate) == (12, 12, 1.0)
        assert row.flags == []

    def test_overwritten_is_flagged_likely_worthless(self, tmp_path: Path) -> None:
        repo = make_repo(tmp_path)
        sha = commit(repo, {"src/a.py": lines("alpha", 12)})
        commit(repo, {"src/a.py": lines("beta", 12)})
        row = one_task(compute_survival([iso_state(repo, sha)], str(repo)))
        assert row.lines_survived == 0 and row.survival_rate == 0.0
        assert row.flags == [FLAG_LIKELY_WORTHLESS]

    def test_small_change_is_never_flagged(self, tmp_path: Path) -> None:
        repo = make_repo(tmp_path)
        sha = commit(repo, {"a.py": lines("alpha", 3)})
        commit(repo, {"a.py": lines("beta", 3)})
        row = one_task(compute_survival([iso_state(repo, sha)], str(repo)))
        assert row.survival_rate == 0.0 and row.flags == []

    def test_partial_survival(self, tmp_path: Path) -> None:
        repo = make_repo(tmp_path)
        sha = commit(repo, {"a.py": lines("alpha", 20)})
        commit(repo, {"a.py": lines("alpha", 10) + lines("gamma", 10)})
        row = one_task(compute_survival([iso_state(repo, sha)], str(repo)))
        assert (row.lines_added, row.lines_survived) == (20, 10)
        assert row.survival_rate == 0.5 and row.flags == []

    def test_reverted_via_git_revert(self, tmp_path: Path) -> None:
        repo = make_repo(tmp_path)
        sha = commit(repo, {"a.py": lines("alpha", 12)})
        git(repo, "revert", "--no-edit", sha)
        row = one_task(compute_survival([iso_state(repo, sha)], str(repo)))
        assert FLAG_REVERTED in row.flags and FLAG_LIKELY_WORTHLESS in row.flags
        assert row.lines_survived == 0

    def test_rename_is_followed(self, tmp_path: Path) -> None:
        repo = make_repo(tmp_path)
        sha = commit(repo, {"old/a.py": lines("alpha", 12)})
        git(repo, "mv", "old/a.py", "new_a.py")
        git(repo, "commit", "-q", "-m", "rename")
        row = one_task(compute_survival([iso_state(repo, sha)], str(repo)))
        assert row.survival_rate == 1.0

    def test_deleted_file_scores_zero(self, tmp_path: Path) -> None:
        repo = make_repo(tmp_path)
        sha = commit(repo, {"a.py": lines("alpha", 12)})
        commit(repo, {"a.py": None})
        row = one_task(compute_survival([iso_state(repo, sha)], str(repo)))
        assert row.lines_added == 12 and row.lines_survived == 0

    def test_binary_files_are_skipped_and_reported(self, tmp_path: Path) -> None:
        repo = make_repo(tmp_path)
        sha = commit(repo, {"blob.bin": b"\x00\x01\x02binary\x00", "a.py": lines("alpha", 12)})
        row = one_task(compute_survival([iso_state(repo, sha)], str(repo)))
        assert row.skipped_binary_files == 1
        assert (row.files, row.lines_added) == (1, 12)

    def test_measured_at_explicit_ref(self, tmp_path: Path) -> None:
        repo = make_repo(tmp_path)
        sha = commit(repo, {"a.py": lines("alpha", 12)})
        commit(repo, {"a.py": None})
        rep = compute_survival([iso_state(repo, sha)], str(repo), ref=sha)
        assert rep.ref_resolved is True
        assert one_task(rep).survival_rate == 1.0

    def test_truncation_is_reported(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(survival, "MAX_FILES", 1)
        repo = make_repo(tmp_path)
        sha = commit(repo, {"a.py": lines("alpha", 12), "b.py": lines("beta", 12)})
        row = one_task(compute_survival([iso_state(repo, sha)], str(repo)))
        assert row.truncated == ["files>1"] and row.files == 1

    def test_oversized_file_is_skipped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(survival, "MAX_FILE_LINES", 5)
        repo = make_repo(tmp_path)
        sha = commit(repo, {"a.py": lines("alpha", 12)})
        row = one_task(compute_survival([iso_state(repo, sha)], str(repo)))
        assert row.truncated == ["file_lines>5"] and row.lines_added == 0

    def test_too_many_commits_is_skipped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(survival, "MAX_COMMITS", 1)
        repo = make_repo(tmp_path)
        base = git(repo, "rev-parse", "HEAD")
        commit(repo, {"a.py": lines("alpha", 12)})
        head = commit(repo, {"b.py": lines("beta", 12)})
        st = iso_state(repo, head)
        st.task_integration["t1"].landed_ranges = {KEY: [[base, head]]}
        row = one_task(compute_survival([st], str(repo)))
        assert row.truncated == ["commits>1"] and row.lines_added == 0


class TestParseAddedLines:
    def test_trivial_lines_are_ignored(self) -> None:
        diff = (
            "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -0,0 +6 @@\n"
            "+\n+}\n+  \n+ab\n+----\n+real_code_line()\n+++not a header\n"
        )
        added, binary = parse_added_lines(diff)
        assert binary == 0
        assert dict(added["x.py"]) == {"real_code_line()": 1, "++not a header": 1}

    def test_binary_marker_counted(self) -> None:
        diff = (
            "diff --git a/b.bin b/b.bin\nnew file mode 100644\n"
            "Binary files /dev/null and b/b.bin differ\n"
        )
        assert parse_added_lines(diff)[1] == 1


class TestAttribution:
    def test_isolation_landed_ranges_sum_retries(self, tmp_path: Path) -> None:
        repo = make_repo(tmp_path)
        b0 = git(repo, "rev-parse", "HEAD")
        h1 = commit(repo, {"a.py": lines("alpha", 6)})
        h2 = commit(repo, {"b.py": lines("beta", 6)})
        st = iso_state(repo, "deadbeef")  # bogus squash: ranges must win
        st.task_integration["t1"].landed_ranges = {KEY: [[b0, h1], [h1, h2]]}
        row = one_task(compute_survival([st], str(repo)))
        assert (row.commits, row.lines_added, row.lines_survived) == (2, 12, 12)

    def test_unreachable_head_lowers_confidence_and_blocks_flag(self, tmp_path: Path) -> None:
        repo = make_repo(tmp_path)
        git(repo, "branch", "side")
        git(repo, "checkout", "-q", "-b", "feature")
        sha = commit(repo, {"a.py": lines("alpha", 12)})
        git(repo, "checkout", "-q", "main")
        rep = compute_survival([iso_state(repo, sha)], str(repo), ref="main")
        row = one_task(rep)
        assert row.confidence == "low" and row.lines_survived == 0
        assert row.flags == [] and "ancestor" in (row.note or "")

    def test_repo_cwd_derived_from_integration_common_dir(self, tmp_path: Path) -> None:
        repo = make_repo(tmp_path)
        sha = commit(repo, {"a.py": lines("alpha", 12)})
        st = iso_state(repo, sha)
        st.git_repos = {}
        st.integration.repos = {KEY: str(repo / ".git")}
        other = tmp_path / "elsewhere"
        other.mkdir()
        assert one_task(compute_survival([st], str(other))).lines_added == 12

    @staticmethod
    def _serial_state(repo: Path, start: str, ends: dict[str, str], windows) -> RunState:
        tasks = {}
        prev = start
        for tid, end in ends.items():
            s, e = windows[tid]
            tasks[tid] = TaskRunState(
                status="succeeded",
                started_at=s.isoformat(),
                ended_at=e.isoformat(),
                start_heads={KEY: prev},
                end_heads={KEY: end},
            )
            prev = end
        return RunState(
            run_id="r1",
            workflow_id="wf",
            repo_set="rs",
            started_at=T0.isoformat(),
            updated_at=(T0 + timedelta(hours=1)).isoformat(),
            git_repos={KEY: str(repo)},
            git_start_heads={KEY: start},
            tasks=tasks,
        )

    def test_serial_attribution_per_task(self, tmp_path: Path) -> None:
        repo = make_repo(tmp_path)
        start = git(repo, "rev-parse", "HEAD")
        h1 = commit(repo, {"a.py": lines("alpha", 12)})
        h2 = commit(repo, {"b.py": lines("beta", 12)})
        m = timedelta(minutes=1)
        st = self._serial_state(
            repo,
            start,
            {"t1": h1, "t2": h2, "readonly": h2},
            {
                "t1": (T0, T0 + m),
                "t2": (T0 + 2 * m, T0 + 3 * m),
                "readonly": (T0 + 4 * m, T0 + 5 * m),
            },
        )
        # a read-only task did not move HEAD: it must be n/a and unflagged
        rep = compute_survival([st], str(repo))
        t1, t2, ro = (one_task(rep, t) for t in ("t1", "t2", "readonly"))
        assert (t1.attribution, t1.lines_added, t1.survival_rate) == ("serial", 12, 1.0)
        assert (t2.attribution, t2.lines_added) == ("serial", 12)
        assert (ro.attribution, ro.survival_rate, ro.flags) == ("none", None, [])
        assert rep.runs[0].total.lines_added == 24

    def test_overlapping_windows_are_ambiguous(self, tmp_path: Path) -> None:
        repo = make_repo(tmp_path)
        start = git(repo, "rev-parse", "HEAD")
        h1 = commit(repo, {"a.py": lines("alpha", 12)})
        h2 = commit(repo, {"b.py": lines("beta", 12)})
        m = timedelta(minutes=1)
        st = self._serial_state(
            repo,
            start,
            {"t1": h1, "t2": h2},
            {"t1": (T0, T0 + 3 * m), "t2": (T0 + m, T0 + 4 * m)},  # overlap
        )
        rep = compute_survival([st], str(repo))
        for tid in ("t1", "t2"):
            row = one_task(rep, tid)
            assert row.attribution == "ambiguous" and row.commits == 1
            assert row.lines_added == 0 and row.flags == []
        assert rep.runs[0].total.lines_added == 24  # still counted at run level

    def test_time_window_fallback_is_run_level_low_confidence(self, tmp_path: Path) -> None:
        repo = make_repo(tmp_path)
        before = commit(repo, {"old.py": lines("old", 12)}, when="2025-12-01T00:00:00+0000")
        inside = commit(repo, {"a.py": lines("alpha", 12)}, when="2026-01-01T12:30:00+0000")
        commit(repo, {"a.py": lines("beta", 12)}, when="2026-01-02T00:00:00+0000")  # after
        assert before != inside
        st = RunState(
            run_id="old",
            workflow_id="wf",
            repo_set="rs",
            started_at=T0.isoformat(),
            updated_at=(T0 + timedelta(hours=1)).isoformat(),
            tasks={"t1": TaskRunState(status="succeeded")},
        )
        rep = compute_survival([st], str(repo))
        total = rep.runs[0].total
        assert (total.attribution, total.confidence, total.commits) == ("time-window", "low", 1)
        assert total.lines_added == 12 and total.lines_survived == 0
        assert total.flags == []  # low confidence never flags
        assert one_task(rep).attribution == "none"  # nothing per task

    def test_no_git_workspace_is_unavailable_not_an_error(self, tmp_path: Path) -> None:
        st = RunState(
            run_id="r",
            workflow_id="wf",
            repo_set="rs",
            started_at=T0.isoformat(),
            updated_at=T0.isoformat(),
        )
        rep = compute_survival([st], str(tmp_path))
        assert rep.runs[0].total.unavailable

    def test_missing_workspace_dir_never_raises(self, tmp_path: Path) -> None:
        st = RunState(run_id="r", workflow_id="w", repo_set="s", started_at="x", updated_at="y")
        rep = compute_survival([st], str(tmp_path / "nope"))
        assert rep.runs[0].total.unavailable


class TestRef:
    @pytest.mark.parametrize("bad", ["-x", "--output=/tmp/x", "a b", "", " main", "a\nb"])
    def test_validate_rejects(self, bad: str) -> None:
        assert validate_ref(bad)

    def test_validate_accepts(self) -> None:
        assert validate_ref("origin/main~2") is None

    def test_unresolvable_ref(self, tmp_path: Path) -> None:
        repo = make_repo(tmp_path)
        sha = commit(repo, {"a.py": lines("alpha", 12)})
        rep = compute_survival([iso_state(repo, sha)], str(repo), ref="no-such-branch")
        assert rep.ref_resolved is False and rep.runs == [] and rep.unavailable

    def test_option_like_ref_is_refused_before_git(self, tmp_path: Path) -> None:
        rep = compute_survival([], str(tmp_path), ref="--output=/tmp/x")
        assert rep.ref_resolved is False and "'-'" in (rep.unavailable or "")


class TestRecording:
    def test_old_state_json_loads_with_defaults(self) -> None:
        old = {
            "run_id": "r",
            "workflow_id": "w",
            "repo_set": "s",
            "started_at": "a",
            "updated_at": "b",
            "tasks": {"t": {"status": "succeeded"}},
            "task_integration": {"t": {"isolation": "worktree"}},
        }
        st = RunState.model_validate(json.loads(json.dumps(old)))
        assert st.git_repos == {} and st.git_start_heads == {}
        assert st.tasks["t"].end_heads == {} and st.tasks["t"].start_heads == {}
        assert st.task_integration["t"].landed_ranges == {}

    def test_record_start_once_and_never_overwritten(self, tmp_path: Path) -> None:
        repo = make_repo(tmp_path)
        st = RunState(run_id="r", workflow_id="w", repo_set="s", started_at="a", updated_at="b")
        record_git_start(st, {"core": str(repo), "docs": str(repo / "sub")})
        assert len(st.git_repos) == 1  # one key per real repo
        (key,) = st.git_repos
        first = st.git_start_heads[key]
        assert first == git(repo, "rev-parse", "HEAD")
        commit(repo, {"a.py": lines("alpha", 3)})
        record_git_start(st, {"core": str(repo)})
        assert st.git_start_heads[key] == first
        assert current_heads(st.git_repos)[key] == git(repo, "rev-parse", "HEAD")

    def test_non_git_and_unborn_are_skipped_silently(self, tmp_path: Path) -> None:
        plain = tmp_path / "plain"
        plain.mkdir()
        unborn = tmp_path / "unborn"
        unborn.mkdir()
        git(unborn, "init", "-q")
        st = RunState(run_id="r", workflow_id="w", repo_set="s", started_at="a", updated_at="b")
        record_git_start(st, {"p": str(plain), "u": str(unborn), "gone": str(tmp_path / "x")})
        assert st.git_start_heads == {}
        assert current_heads({"k": str(plain)}) == {}
        assert current_heads({}) == {}

    def test_record_landed_ranges(self) -> None:
        ti = TaskIntegrationState()
        record_landed_ranges(
            ti, {"k": "a", "n": None, "same": "z"}, {"k": "b", "n": "c", "same": "z"}
        )
        record_landed_ranges(ti, {"k": "b"}, {"k": "d"})
        assert ti.landed_ranges == {"k": [["a", "b"], ["b", "d"]]}
