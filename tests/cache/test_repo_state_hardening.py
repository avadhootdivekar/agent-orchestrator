"""G1a remediation SEC-03 / SEC-04: the environment of every repository read (HLD 8.2.4, 7.7).

An inherited `GIT_DIR` must never redirect the workspace-bounded reads to another repository, and
the repo config that makes `git status` execute a program (`core.fsmonitor`) must be neutralised.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import agent_orchestrator.cache.repo_state as repo_state
from agent_orchestrator.cache.constants import GIT_OPTIONAL_LOCKS_VAR
from tests.cache.fakes import FakeVcsRunner
from tests.cache.test_repo_state import (
    SHA,
    fake_repo,
    git,
    head_reader,
    needs_git,
    needs_posix,
    probe,
    real_repo,
)


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("AO_CACHE", raising=False)
    monkeypatch.setenv("AO_STATE_DIR", str(tmp_path / "ao-state"))


@pytest.fixture
def hooks_dir(tmp_path: Path) -> Path:
    return tmp_path / "empty-hooks"


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    path = tmp_path / "ws"
    path.mkdir()
    return path


INHERITED_REPO_SELECTORS = {
    "GIT_DIR": "/elsewhere/.git",
    "GIT_WORK_TREE": "/elsewhere",
    "GIT_INDEX_FILE": "/elsewhere/index",
    "GIT_COMMON_DIR": "/elsewhere/.git",
    "GIT_OBJECT_DIRECTORY": "/elsewhere/objects",
    "GIT_CEILING_DIRECTORIES": "/",
    "GIT_CONFIG_PARAMETERS": "'core.fsmonitor=evil'",
    "GIT_CONFIG_COUNT": "1",
    "GIT_CONFIG_KEY_0": "core.hooksPath",
    "GIT_CONFIG_VALUE_0": "/evil",
}


def test_git_read_env_scrubs_repo_selecting_variables_and_neutralises_exec_config() -> None:
    base = {
        "PATH": "/bin",
        "HOME": "/h",
        "GIT_CONFIG_GLOBAL": os.devnull,
        **INHERITED_REPO_SELECTORS,
    }
    env = repo_state.git_read_env(base)
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR"):
        assert name not in env
    assert "GIT_CONFIG_PARAMETERS" not in env and "GIT_OBJECT_DIRECTORY" not in env
    assert (env["PATH"], env["HOME"], env["GIT_CONFIG_GLOBAL"]) == ("/bin", "/h", os.devnull)
    assert env[GIT_OPTIONAL_LOCKS_VAR] == "0"
    # the inherited config injection is replaced by exactly our own pairs
    assert env["GIT_CONFIG_COUNT"] == "2"
    pairs = {env[f"GIT_CONFIG_KEY_{i}"]: env[f"GIT_CONFIG_VALUE_{i}"] for i in range(2)}
    assert pairs == {"core.fsmonitor": "false", "core.untrackedCache": "false"}
    assert "GIT_CONFIG_KEY_2" not in env


def test_the_runner_never_sees_an_inherited_repo_selector(
    ws: Path, hooks_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name, value in INHERITED_REPO_SELECTORS.items():
        monkeypatch.setenv(name, value)
    repo = fake_repo(ws / "r")
    runner = FakeVcsRunner().on("rev-parse", "HEAD", stdout=SHA.encode())
    head_reader(ws, hooks_dir, runner).read({"r": str(repo)})
    probe(hooks_dir, runner).snapshot({"r": str(repo)}, str(ws), frozenset())
    assert runner.calls
    for call in runner.calls:
        for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR"):
            assert name not in call["env"]
        assert call["env"]["GIT_CONFIG_COUNT"] == "2"


@needs_posix
@needs_git
def test_an_inherited_git_dir_cannot_redirect_the_head_read(
    ws: Path, hooks_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SEC-04: with GIT_DIR pointing at ANOTHER repository the reader still returns the
    workspace repository's HEAD (otherwise real HEAD movement would never change the key)."""
    other = real_repo(tmp_path / "other")
    (other / "more.txt").write_text("x")
    git(other, "add", "more.txt")
    git(other, "commit", "-q", "-m", "second")
    repo = real_repo(ws / "r")
    expected = git(repo, "rev-parse", "HEAD")  # computed BEFORE the hostile env is exported
    assert git(other, "rev-parse", "HEAD") != expected
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(other))
    assert head_reader(ws, hooks_dir).read({"r": str(repo)})["r"] == expected


@needs_posix
@needs_git
def test_a_hostile_core_fsmonitor_in_the_workspace_repo_is_never_executed(
    ws: Path, hooks_dir: Path
) -> None:
    """SEC-03: `git status` runs `core.fsmonitor` from the (agent-writable) repo config."""
    repo = real_repo(ws / "r")
    marker = ws / "fsmonitor-ran"
    with (repo / ".git" / "config").open("a") as fh:
        fh.write(f'[core]\n\tfsmonitor = "touch {marker}; echo"\n')
    (repo / "tracked.txt").write_text("changed")  # a tracked change, so status really runs
    snapshot = probe(hooks_dir).snapshot({"r": str(repo)}, str(ws), frozenset())
    assert any(entry[1] == "tracked.txt" for entry in snapshot)
    assert not marker.exists()
