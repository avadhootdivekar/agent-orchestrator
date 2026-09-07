"""E2E tests for `ao hotspots` (E-Wk9Tz3 T-Ov9Bt5, HLD §11 M8) via `CliRunner` — the
outermost boundary a user touches, per CLAUDE.md's e2e testing rule.

Drives a real temp git repo with a scripted, pinned-date commit history (raw subprocess,
fixture-building only -- never through `GitRepo`/`ao`) and asserts on the written
`.ao/hotspots.json`, matching AC-10: "assert the top entry is the file touched most, and
that a --since-days window excludes older commits."
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agent_orchestrator.cli import app

runner = CliRunner()

_AUTHOR_ENV = {
    "GIT_AUTHOR_NAME": "ao-test",
    "GIT_AUTHOR_EMAIL": "ao-test@example.invalid",
    "GIT_COMMITTER_NAME": "ao-test",
    "GIT_COMMITTER_EMAIL": "ao-test@example.invalid",
}


@pytest.fixture(autouse=True)
def _isolated_git_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every `GitRepo` `ao hotspots` constructs resolves its hooks dir under this test's
    own `tmp_path` -- never the real `~`/`$AO_STATE_DIR` (mirrors
    `tests/isolation/conftest.py::_isolated_git_env`, which does not apply here since
    this file lives outside `tests/isolation/`)."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("AO_STATE_DIR", str(tmp_path / "ao-state"))
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.delenv("AO_WORKSPACE_ROOT", raising=False)
    monkeypatch.delenv("GIT_CONFIG_GLOBAL", raising=False)
    monkeypatch.delenv("GIT_CONFIG_SYSTEM", raising=False)


def _git(args: list[str], cwd: Path, env: dict[str, str] | None = None) -> None:
    subprocess.run(
        ["git", "-c", "user.name=ao-test", "-c", "user.email=ao-test@example.invalid", *args],
        cwd=cwd,
        env={**os.environ, **(env or {})},
        check=True,
        capture_output=True,
    )


def _commit_file(repo: Path, rel_path: str, content: str, *, date: str, message: str) -> None:
    full = repo / rel_path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content)
    _git(["add", "-A"], cwd=repo)
    _git(
        ["commit", "-q", "-m", message],
        cwd=repo,
        env={**_AUTHOR_ENV, "GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date},
    )


def _scripted_repo(tmp_path: Path) -> Path:
    """A repo whose history is scripted so `hot.rs` (touched 3x) is unambiguously the
    top hotspot and `stale.rs` (touched once, long before the default window) is
    excluded by a tight `--since-days`. Commits are created in date order (oldest
    first) -- git's `--since` traversal prunes early on out-of-order dates, a documented
    git quirk unrelated to this ticket's own code (see `tests/test_hotspots.py`'s same
    note)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init", "-q", "-b", "main"], cwd=repo)
    _commit_file(repo, "stale.rs", "v1\n", date="2022-01-01T00:00:00", message="stale touch")
    _commit_file(repo, "hot.rs", "v1\n", date="2024-01-01T00:00:00", message="hot v1")
    _commit_file(repo, "warm.rs", "v1\n", date="2024-01-02T00:00:00", message="warm v1")
    _commit_file(repo, "hot.rs", "v2\n", date="2024-01-03T00:00:00", message="hot v2")
    _commit_file(repo, "hot.rs", "v3\n", date="2024-01-04T00:00:00", message="hot v3")
    return repo


class TestHotspotsGeneratesFile:
    def test_writes_hotspots_json_with_top_entry_most_touched(self, tmp_path: Path) -> None:
        repo = _scripted_repo(tmp_path)

        result = runner.invoke(app, ["hotspots", "--workspace", str(repo), "--since-days", "0"])

        assert result.exit_code == 0, result.output
        out_path = repo / ".ao" / "hotspots.json"
        assert out_path.exists()
        data = json.loads(out_path.read_text())
        assert data["version"] == "1.0"
        assert "generated_at" in data
        entries = data["repos"]["repo"]["entries"]
        assert entries[0]["path"] == "hot.rs"
        assert entries[0]["churn"] == 3
        assert "top: hot.rs" in result.output

    def test_default_output_path_is_ao_hotspots_json_under_workspace(self, tmp_path: Path) -> None:
        repo = _scripted_repo(tmp_path)
        result = runner.invoke(app, ["hotspots", "--workspace", str(repo), "--since-days", "0"])
        assert result.exit_code == 0, result.output
        assert (repo / ".ao" / "hotspots.json").exists()

    def test_custom_output_path_honoured(self, tmp_path: Path) -> None:
        repo = _scripted_repo(tmp_path)
        custom = tmp_path / "custom-hotspots.json"
        result = runner.invoke(
            app,
            [
                "hotspots",
                "--workspace",
                str(repo),
                "--since-days",
                "0",
                "--output",
                str(custom),
            ],
        )
        assert result.exit_code == 0, result.output
        assert custom.exists()
        assert not (repo / ".ao" / "hotspots.json").exists()

    def test_since_days_window_excludes_older_commits(self, tmp_path: Path) -> None:
        repo = _scripted_repo(tmp_path)
        # A very old fixed history: "now" is real wall-clock time by construction (the
        # CLI has no --now override), so use a since-days window short enough that
        # 2022/2024-dated fixture commits are unambiguously outside it while staying
        # robust to whenever this test actually runs.
        result = runner.invoke(app, ["hotspots", "--workspace", str(repo), "--since-days", "7"])
        assert result.exit_code == 0, result.output
        data = json.loads((repo / ".ao" / "hotspots.json").read_text())
        entries = data["repos"]["repo"]["entries"]
        paths = {e["path"] for e in entries}
        assert "hot.rs" not in paths
        assert "stale.rs" not in paths
        assert entries == []


class TestHotspotsIdempotentReRun:
    def test_rerun_same_repo_unchanged_history_is_idempotent(self, tmp_path: Path) -> None:
        repo = _scripted_repo(tmp_path)
        r1 = runner.invoke(app, ["hotspots", "--workspace", str(repo), "--since-days", "0"])
        assert r1.exit_code == 0, r1.output
        first = json.loads((repo / ".ao" / "hotspots.json").read_text())

        r2 = runner.invoke(app, ["hotspots", "--workspace", str(repo), "--since-days", "0"])
        assert r2.exit_code == 0, r2.output
        second = json.loads((repo / ".ao" / "hotspots.json").read_text())

        first.pop("generated_at")
        second.pop("generated_at")
        assert first == second

    def test_rerun_different_repo_label_accumulates(self, tmp_path: Path) -> None:
        repo = _scripted_repo(tmp_path)
        r1 = runner.invoke(
            app, ["hotspots", "--workspace", str(repo), "--repo", "core", "--since-days", "0"]
        )
        assert r1.exit_code == 0, r1.output
        r2 = runner.invoke(
            app, ["hotspots", "--workspace", str(repo), "--repo", "docs", "--since-days", "0"]
        )
        assert r2.exit_code == 0, r2.output

        data = json.loads((repo / ".ao" / "hotspots.json").read_text())
        assert set(data["repos"]) == {"core", "docs"}


class TestHotspotsAtomicWrite:
    def test_failed_write_leaves_previous_file_intact(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Review C-2: write-then-rename (mirroring runstate.py's own idiom) -- a crash
        # between the tmp-file write and the rename must never corrupt/truncate the
        # PREVIOUS successfully-written file.
        repo = _scripted_repo(tmp_path)
        r1 = runner.invoke(app, ["hotspots", "--workspace", str(repo), "--since-days", "0"])
        assert r1.exit_code == 0, r1.output
        out_path = repo / ".ao" / "hotspots.json"
        original = out_path.read_bytes()

        def _boom(*args: object, **kwargs: object) -> None:
            raise OSError("simulated crash between tmp-write and rename")

        monkeypatch.setattr(os, "replace", _boom)
        r2 = runner.invoke(app, ["hotspots", "--workspace", str(repo), "--since-days", "0"])
        assert r2.exit_code != 0

        assert out_path.read_bytes() == original


class TestHotspotsErrorPaths:
    def test_nonexistent_workspace_exits_1_with_actionable_message(self, tmp_path: Path) -> None:
        missing = tmp_path / "does-not-exist"
        result = runner.invoke(app, ["hotspots", "--workspace", str(missing)])
        assert result.exit_code == 1
        assert "does not exist" in result.output

    def test_non_git_directory_exits_1_with_actionable_message(self, tmp_path: Path) -> None:
        not_git = tmp_path / "not-a-repo"
        not_git.mkdir()
        result = runner.invoke(app, ["hotspots", "--workspace", str(not_git)])
        assert result.exit_code == 1
        assert "not a git repository" in result.output

    def test_empty_repo_no_commits_yet_degrades_to_empty_not_a_crash(self, tmp_path: Path) -> None:
        repo = tmp_path / "empty-repo"
        repo.mkdir()
        _git(["init", "-q", "-b", "main"], cwd=repo)
        result = runner.invoke(app, ["hotspots", "--workspace", str(repo)])
        assert result.exit_code == 0, result.output
        data = json.loads((repo / ".ao" / "hotspots.json").read_text())
        assert data["repos"]["empty-repo"]["entries"] == []
