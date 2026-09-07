"""Tests for T-Ov9Bt5 — the hotspot signal (E-Wk9Tz3 FR-11, HLD §9.2,
`isolation/hotspots.py`).

Real git repos are built via `tests/isolation/conftest.py::make_repo` (a **locked**
interface per that module's own docstring — imported here, never re-derived, and that
conftest is not modified by this ticket). This file lives under `tests/`, not
`tests/isolation/`, so `tests/isolation/conftest.py`'s autouse `_isolated_git_env`
fixture does not apply automatically here; `_hotspots_isolated_git_env` below mirrors it
locally (same HOME/AO_STATE_DIR redirection under `tmp_path`) so no test here ever
touches the real `~`/`$AO_STATE_DIR`.

Covers: AC-9 (`parse_churn` purity over a fixture file, blank-line handling,
`compute_hotspots`'s conflict-merge with the named `CONFLICT_WEIGHT` constant, dropping
paths absent at HEAD), AC-11 (load-with-fallback -- three tolerance tests), plus
`observed_conflicts` and `Hotspots.weights()`/`merge_hotspots` coverage.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agent_orchestrator.errors import GitError
from agent_orchestrator.isolation.git import GitRepo
from agent_orchestrator.isolation.hotspots import (
    CONFLICT_WEIGHT,
    HOTSPOTS_SCHEMA_VERSION,
    HotspotEntry,
    Hotspots,
    RepoHotspots,
    compute_hotspots,
    load_hotspots,
    merge_hotspots,
    observed_conflicts,
    parse_churn,
)
from tests.isolation.conftest import make_repo

_FIXTURE_DIR = Path(__file__).parent / "fixtures"
_GIT_LOG_FIXTURE = _FIXTURE_DIR / "git_log_name_only.txt"

_TEST_AUTHOR_ENV = {
    "GIT_AUTHOR_NAME": "ao-test",
    "GIT_AUTHOR_EMAIL": "ao-test@example.invalid",
    "GIT_COMMITTER_NAME": "ao-test",
    "GIT_COMMITTER_EMAIL": "ao-test@example.invalid",
}


@pytest.fixture(autouse=True)
def _hotspots_isolated_git_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Mirrors `tests/isolation/conftest.py::_isolated_git_env` (not importable as a
    fixture across the package boundary, and that conftest is off-limits to edit for
    this ticket) so every `GitRepo` constructed here resolves its hooks dir under this
    test's own `tmp_path`, never the real home/state dir.
    """
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("AO_STATE_DIR", str(tmp_path / "ao-state"))
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.delenv("GIT_CONFIG_GLOBAL", raising=False)
    monkeypatch.delenv("GIT_CONFIG_SYSTEM", raising=False)


def _commit_file(repo: Path, rel_path: str, content: str, *, date: str, message: str) -> None:
    """Raw (non-`GitRepo`) commit with a PINNED author/committer date -- fixture-building
    only, mirroring `tests/isolation/conftest.py::_git`'s own documented convention
    ("used only to BUILD fixtures -- never the thing under test").
    """
    full = repo / rel_path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content)
    env = {**os.environ, **_TEST_AUTHOR_ENV, "GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date}
    subprocess.run(
        ["git", "-c", "user.name=ao-test", "-c", "user.email=ao-test@example.invalid", "add", "-A"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=ao-test",
            "-c",
            "user.email=ao-test@example.invalid",
            "commit",
            "-q",
            "-m",
            message,
        ],
        cwd=repo,
        env=env,
        check=True,
        capture_output=True,
    )


def _delete_and_commit(repo: Path, rel_path: str, *, date: str, message: str) -> None:
    (repo / rel_path).unlink()
    env = {**os.environ, **_TEST_AUTHOR_ENV, "GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date}
    subprocess.run(
        ["git", "-c", "user.name=ao-test", "-c", "user.email=ao-test@example.invalid", "add", "-A"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=ao-test",
            "-c",
            "user.email=ao-test@example.invalid",
            "commit",
            "-q",
            "-m",
            message,
        ],
        cwd=repo,
        env=env,
        check=True,
        capture_output=True,
    )


# ---------------------------------------------------------------------------
# AC-9: parse_churn -- pure, fixture-driven
# ---------------------------------------------------------------------------


class TestParseChurn:
    def test_fixture_file(self) -> None:
        # Real `git log --pretty=format: --name-only -z --no-merges` output, captured
        # from a scratch repo with 4 commits (review C-3: includes a non-ASCII filename,
        # `src/café.rs`, to prove NUL-delimited parsing survives it unmangled).
        text = _GIT_LOG_FIXTURE.read_text()
        counts = parse_churn(text)
        assert counts == {
            "src/api/accounts.rs": 3,
            "src/api/mod.rs": 1,
            "docs/readme.md": 1,
            "src/ui/app.tsx": 1,
            "src/café.rs": 1,
        }

    def test_empty_text_yields_empty_counter(self) -> None:
        assert parse_churn("") == {}

    def test_nul_separator_entries_are_dropped(self) -> None:
        # `-z` output: each path NUL-terminated, an extra NUL between commits (mirrors
        # git's blank-line commit separator in the non-`-z` form) -- verified against
        # real git output in `_GIT_LOG_FIXTURE`; this is the minimal synthetic case.
        text = "a.txt\0\0\0b.txt\0\0"
        counts = parse_churn(text)
        assert counts == {"a.txt": 1, "b.txt": 1}

    def test_pure_no_side_effects_repeated_call(self) -> None:
        text = "a.txt\0a.txt\0"
        assert parse_churn(text) == parse_churn(text) == {"a.txt": 2}

    def test_non_ascii_filename_survives_unmangled(self) -> None:
        # Without `-z`, git's default `core.quotePath` would emit this as the literal
        # quoted+octal-escaped string `"caf\303\251.rs"` -- `-z` disables that entirely
        # (review C-3), so the raw UTF-8 path passes through parse_churn untouched.
        text = "src/café.rs\0"
        assert parse_churn(text) == {"src/café.rs": 1}


# ---------------------------------------------------------------------------
# compute_hotspots -- real git repo, pinned dates
# ---------------------------------------------------------------------------


class TestComputeHotspots:
    def test_top_entry_is_the_most_touched_file(self, tmp_path: Path) -> None:
        repo = make_repo(tmp_path, files={"README.md": "hello\n"})
        _commit_file(repo, "hot.rs", "v1\n", date="2024-01-01T00:00:00", message="hot v1")
        _commit_file(repo, "hot.rs", "v2\n", date="2024-01-02T00:00:00", message="hot v2")
        _commit_file(repo, "cold.rs", "v1\n", date="2024-01-03T00:00:00", message="cold v1")

        git_repo = GitRepo(str(repo))
        clock = lambda: datetime(2024, 6, 1, tzinfo=UTC)  # noqa: E731
        hotspots = compute_hotspots(
            "core", git_repo, str(repo), since_days=0, top_k=40, clock=clock
        )
        entries = hotspots.repos["core"].entries
        assert entries[0].path == "hot.rs"
        assert entries[0].churn == 2

    def test_non_ascii_filename_survives_is_tracked_and_is_not_dropped(
        self, tmp_path: Path
    ) -> None:
        # Review C-3: before `-z`, git's default quoting would make this path mismatch
        # `is_tracked`'s literal lookup and get silently dropped by the same "no longer
        # at HEAD" path AC-9 uses for genuinely-deleted files -- proving the two cases
        # are no longer conflated.
        repo = make_repo(tmp_path, files={"README.md": "hello\n"})
        _commit_file(repo, "src/café.rs", "v1\n", date="2024-01-01T00:00:00", message="unicode")

        git_repo = GitRepo(str(repo))
        clock = lambda: datetime(2024, 6, 1, tzinfo=UTC)  # noqa: E731
        hotspots = compute_hotspots(
            "core", git_repo, str(repo), since_days=0, top_k=40, clock=clock
        )
        paths = {e.path for e in hotspots.repos["core"].entries}
        assert "src/café.rs" in paths
        assert git_repo.is_tracked(str(repo), "src/café.rs")

    def test_since_days_window_excludes_older_commits(self, tmp_path: Path) -> None:
        repo = make_repo(tmp_path, files={"README.md": "hello\n"})
        # Commit-creation order must match date order (old first, then recent) -- `git
        # log --since` prunes its traversal as soon as it walks past a too-old commit,
        # assuming history is chronologically monotonic along parents; committing out of
        # date order (a child dated EARLIER than its parent) is not a realistic history
        # shape and defeats that assumption, which is a git quirk, not something this
        # ticket's code needs to compensate for.
        _commit_file(repo, "old.rs", "v1\n", date="2022-01-01T00:00:00", message="old")
        _commit_file(repo, "recent.rs", "v1\n", date="2024-05-20T00:00:00", message="recent")

        git_repo = GitRepo(str(repo))
        clock = lambda: datetime(2024, 6, 1, tzinfo=UTC)  # noqa: E731

        windowed = compute_hotspots(
            "core", git_repo, str(repo), since_days=30, top_k=40, clock=clock
        )
        paths = {e.path for e in windowed.repos["core"].entries}
        assert "recent.rs" in paths
        assert "old.rs" not in paths

        full_history = compute_hotspots(
            "core", git_repo, str(repo), since_days=0, top_k=40, clock=clock
        )
        full_paths = {e.path for e in full_history.repos["core"].entries}
        assert "recent.rs" in full_paths
        assert "old.rs" in full_paths

    def test_conflicts_merged_with_named_conflict_weight(self, tmp_path: Path) -> None:
        repo = make_repo(tmp_path, files={"README.md": "hello\n"})
        _commit_file(repo, "risky.rs", "v1\n", date="2024-01-01T00:00:00", message="touch")

        git_repo = GitRepo(str(repo))
        clock = lambda: datetime(2024, 6, 1, tzinfo=UTC)  # noqa: E731
        hotspots = compute_hotspots(
            "core",
            git_repo,
            str(repo),
            since_days=0,
            top_k=40,
            conflicts={"risky.rs": 2},
            clock=clock,
        )
        entry = next(e for e in hotspots.repos["core"].entries if e.path == "risky.rs")
        assert entry.churn == 1
        assert entry.conflicts == 2
        assert entry.weight == 1 + 2 * CONFLICT_WEIGHT
        assert CONFLICT_WEIGHT == 5  # AC-9: named constant, pinned value

    def test_drops_paths_no_longer_at_head(self, tmp_path: Path) -> None:
        repo = make_repo(tmp_path, files={"README.md": "hello\n"})
        _commit_file(repo, "gone.rs", "v1\n", date="2024-01-01T00:00:00", message="add")
        _delete_and_commit(repo, "gone.rs", date="2024-01-02T00:00:00", message="remove")

        git_repo = GitRepo(str(repo))
        clock = lambda: datetime(2024, 6, 1, tzinfo=UTC)  # noqa: E731
        hotspots = compute_hotspots(
            "core", git_repo, str(repo), since_days=0, top_k=40, clock=clock
        )
        paths = {e.path for e in hotspots.repos["core"].entries}
        assert "gone.rs" not in paths

    def test_top_k_truncates(self, tmp_path: Path) -> None:
        repo = make_repo(tmp_path, files={"README.md": "hello\n"})
        for i in range(5):
            _commit_file(repo, f"f{i}.rs", "v\n", date="2024-01-01T00:00:00", message=f"add f{i}")
        git_repo = GitRepo(str(repo))
        clock = lambda: datetime(2024, 6, 1, tzinfo=UTC)  # noqa: E731
        hotspots = compute_hotspots("core", git_repo, str(repo), since_days=0, top_k=2, clock=clock)
        assert len(hotspots.repos["core"].entries) == 2

    def test_top_k_zero_yields_no_entries(self, tmp_path: Path) -> None:
        repo = make_repo(tmp_path, files={"README.md": "hello\n"})
        git_repo = GitRepo(str(repo))
        clock = lambda: datetime(2024, 6, 1, tzinfo=UTC)  # noqa: E731
        hotspots = compute_hotspots("core", git_repo, str(repo), since_days=0, top_k=0, clock=clock)
        assert hotspots.repos["core"].entries == []

    def test_generated_at_uses_injected_clock_not_wall_clock(self, tmp_path: Path) -> None:
        repo = make_repo(tmp_path, files={"README.md": "hello\n"})
        git_repo = GitRepo(str(repo))
        fixed = datetime(2024, 6, 1, tzinfo=UTC)
        hotspots = compute_hotspots(
            "core", git_repo, str(repo), since_days=0, top_k=1, clock=lambda: fixed
        )
        assert hotspots.generated_at == fixed.isoformat()
        assert hotspots.window_days == 0
        assert hotspots.version == HOTSPOTS_SCHEMA_VERSION

    def test_degrades_to_empty_churn_on_git_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        repo = make_repo(tmp_path, files={"README.md": "hello\n"})
        git_repo = GitRepo(str(repo))

        def _boom(*args: object, **kwargs: object) -> str:
            raise GitError(["git", "log"], 128, "simulated failure")

        monkeypatch.setattr(git_repo, "log_name_only", _boom)
        clock = lambda: datetime(2024, 6, 1, tzinfo=UTC)  # noqa: E731
        with caplog.at_level(logging.WARNING):
            hotspots = compute_hotspots(
                "core", git_repo, str(repo), since_days=0, top_k=10, clock=clock
            )
        assert hotspots.repos["core"].entries == []
        assert any("git log" in rec.message for rec in caplog.records)


# ---------------------------------------------------------------------------
# observed_conflicts -- prior runs' state.json
# ---------------------------------------------------------------------------


class TestObservedConflicts:
    def _write_state(self, runs_dir: Path, run_id: str, task_integration: dict) -> None:
        run_dir = runs_dir / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "state.json").write_text(json.dumps({"task_integration": task_integration}))

    def test_counts_conflicted_paths_across_runs(self, tmp_path: Path) -> None:
        runs_dir = tmp_path / ".orchestrator" / "runs"
        self._write_state(
            runs_dir,
            "run-1",
            {"t1": {"conflicted_paths": ["a.rs", "b.rs"]}},
        )
        self._write_state(
            runs_dir,
            "run-2",
            {"t1": {"conflicted_paths": ["a.rs"]}, "t2": {"conflicted_paths": ["a.rs"]}},
        )
        counts = observed_conflicts(str(tmp_path))
        assert counts == {"a.rs": 3, "b.rs": 1}

    def test_missing_runs_dir_yields_empty(self, tmp_path: Path) -> None:
        assert observed_conflicts(str(tmp_path)) == {}

    def test_malformed_state_json_is_skipped_not_raised(self, tmp_path: Path) -> None:
        runs_dir = tmp_path / ".orchestrator" / "runs" / "run-1"
        runs_dir.mkdir(parents=True)
        (runs_dir / "state.json").write_text("{not json")
        assert observed_conflicts(str(tmp_path)) == {}

    def test_pre_epic_state_json_with_no_task_integration_key(self, tmp_path: Path) -> None:
        runs_dir = tmp_path / ".orchestrator" / "runs" / "run-1"
        runs_dir.mkdir(parents=True)
        (runs_dir / "state.json").write_text(json.dumps({"status": "completed"}))
        assert observed_conflicts(str(tmp_path)) == {}

    def test_non_dict_json_root_is_skipped(self, tmp_path: Path) -> None:
        runs_dir = tmp_path / ".orchestrator" / "runs" / "run-1"
        runs_dir.mkdir(parents=True)
        (runs_dir / "state.json").write_text(json.dumps(["not", "a", "dict"]))
        assert observed_conflicts(str(tmp_path)) == {}

    def test_non_dict_task_integration_entry_is_skipped(self, tmp_path: Path) -> None:
        runs_dir = tmp_path / ".orchestrator" / "runs" / "run-1"
        runs_dir.mkdir(parents=True)
        (runs_dir / "state.json").write_text(json.dumps({"task_integration": {"t1": "not-a-dict"}}))
        assert observed_conflicts(str(tmp_path)) == {}


# ---------------------------------------------------------------------------
# AC-11: load_hotspots -- load-with-fallback (three tolerance tests)
# ---------------------------------------------------------------------------


class TestLoadHotspots:
    def test_missing_file_yields_empty_with_one_warning(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING):
            result = load_hotspots(str(tmp_path / "does-not-exist.json"))
        assert result == Hotspots()
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1

    def test_unreadable_path_yields_empty_with_one_warning(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        # A directory where a file is expected is "unreadable" as a file -- read_text()
        # raises OSError (IsADirectoryError), the same family a real permission error
        # would raise.
        bogus = tmp_path / "hotspots.json"
        bogus.mkdir()
        with caplog.at_level(logging.WARNING):
            result = load_hotspots(str(bogus))
        assert result == Hotspots()
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1

    def test_schema_invalid_json_yields_empty_with_one_warning(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        bad = tmp_path / "hotspots.json"
        bad.write_text('{"version": "1.0", "repos": "not-a-mapping"}')
        with caplog.at_level(logging.WARNING):
            result = load_hotspots(str(bad))
        assert result == Hotspots()
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1

    def test_invalid_json_syntax_yields_empty_with_one_warning(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        bad = tmp_path / "hotspots.json"
        bad.write_text("{not json at all")
        with caplog.at_level(logging.WARNING):
            result = load_hotspots(str(bad))
        assert result == Hotspots()
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1

    def test_valid_file_round_trips(self, tmp_path: Path) -> None:
        original = Hotspots(
            version=HOTSPOTS_SCHEMA_VERSION,
            generated_at="2024-06-01T00:00:00+00:00",
            window_days=180,
            repos={"core": RepoHotspots(entries=[HotspotEntry(path="a.rs", churn=3, weight=3.0)])},
        )
        path = tmp_path / "hotspots.json"
        path.write_text(original.model_dump_json())
        loaded = load_hotspots(str(path))
        assert loaded == original


# ---------------------------------------------------------------------------
# Hotspots.weights() / merge_hotspots
# ---------------------------------------------------------------------------


class TestHotspotsWeightsAndMerge:
    def test_weights_flattens_across_repos_max_wins(self) -> None:
        hotspots = Hotspots(
            repos={
                "core": RepoHotspots(entries=[HotspotEntry(path="shared.rs", weight=3.0)]),
                "docs": RepoHotspots(entries=[HotspotEntry(path="shared.rs", weight=7.0)]),
            }
        )
        assert hotspots.weights() == {"shared.rs": 7.0}

    def test_weights_on_empty_hotspots_is_empty_dict(self) -> None:
        assert Hotspots().weights() == {}

    def test_merge_hotspots_accumulates_repos(self) -> None:
        existing = Hotspots(repos={"core": RepoHotspots(entries=[HotspotEntry(path="a.rs")])})
        new = Hotspots(
            generated_at="later",
            repos={"docs": RepoHotspots(entries=[HotspotEntry(path="b.rs")])},
        )
        merged = merge_hotspots(existing, new)
        assert set(merged.repos) == {"core", "docs"}
        assert merged.generated_at == "later"

    def test_merge_hotspots_replaces_same_repo_id(self) -> None:
        existing = Hotspots(repos={"core": RepoHotspots(entries=[HotspotEntry(path="old.rs")])})
        new = Hotspots(repos={"core": RepoHotspots(entries=[HotspotEntry(path="new.rs")])})
        merged = merge_hotspots(existing, new)
        assert [e.path for e in merged.repos["core"].entries] == ["new.rs"]
