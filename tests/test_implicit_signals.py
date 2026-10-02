"""Tests for implicit_signals.py (E-Us9Kd4 T-Fb3Ef4), against real temp git repos."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from agent_orchestrator.implicit_signals import implicit_signals
from agent_orchestrator.models import (
    RunIntegrationState,
    RunState,
    TaskIntegrationState,
    TrippedBreaker,
)


@pytest.fixture(autouse=True)
def _git_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", os.devnull)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _commit(repo: Path, name: str, text: str) -> str:
    (repo / name).write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", f"edit {name}")
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    return r


def _state(repo: Path, base: str, head: str, run_id: str = "r1", **kw) -> RunState:  # type: ignore[no-untyped-def]
    return RunState(
        run_id=run_id,
        workflow_id="wf",
        repo_set="rs",
        started_at="t",
        updated_at="t",
        integration=RunIntegrationState(
            active=True,
            repos={"main": str(repo / ".git")},
            heads={"main": head},
            base_heads={"main": base},
        ),
        task_integration={"t": TaskIntegrationState(squash_commits={"main": head})},
        **kw,
    )


def _save(ws: Path, st: RunState) -> None:
    d = ws / ".orchestrator" / "runs" / st.run_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "state.json").write_text(st.model_dump_json())


def test_landed_and_not_landed(repo: Path, tmp_path: Path) -> None:
    base = _commit(repo, "a.txt", "1")
    _git(repo, "checkout", "-q", "-b", "side")
    side = _commit(repo, "b.txt", "x")
    _git(repo, "checkout", "-q", "main")
    assert implicit_signals(_state(repo, base, side), str(tmp_path)).landed == "not_landed"
    _git(repo, "merge", "-q", "--ff-only", "side")
    sig = implicit_signals(_state(repo, base, side), str(tmp_path))
    assert sig.landed == "landed" and sig.followup_commits == 0
    assert sig.reverted_commits is None


def test_human_followup_counted_ao_commits_excluded(repo: Path, tmp_path: Path) -> None:
    base = _commit(repo, "a.txt", "1")
    head = _commit(repo, "a.txt", "2")  # the run's change
    ao_follow = _commit(repo, "a.txt", "3")  # recorded by a later ao run
    _commit(repo, "a.txt", "4")  # human edit, same file
    _commit(repo, "unrelated.txt", "z")  # human edit, other file
    st = _state(repo, base, head)
    later = _state(repo, head, ao_follow, run_id="r2")
    ws = tmp_path / "ws"
    _save(ws, st)
    _save(ws, later)
    sig = implicit_signals(st, str(ws))
    assert sig.landed == "landed"
    assert sig.followup_commits == 1
    assert sig.followup_confidence == "normal"


def test_low_confidence_when_other_run_lacks_heads(repo: Path, tmp_path: Path) -> None:
    base = _commit(repo, "a.txt", "1")
    head = _commit(repo, "a.txt", "2")
    st = _state(repo, base, head)
    ws = tmp_path / "ws"
    _save(ws, st)
    _save(ws, RunState(run_id="old", workflow_id="w", repo_set="r", started_at="t", updated_at="t"))
    assert implicit_signals(st, str(ws)).followup_confidence == "low"


def test_no_git_is_unknown_never_raises(tmp_path: Path) -> None:
    nogit = tmp_path / "nogit"
    nogit.mkdir()
    st = _state(nogit, "a" * 40, "b" * 40)
    sig = implicit_signals(st, str(tmp_path))
    assert sig.landed is None and sig.landed_reason
    assert sig.followup_commits is None and sig.followup_reason


def test_no_isolation_unknown_and_status_breakers(tmp_path: Path) -> None:
    st = RunState(
        run_id="r",
        workflow_id="w",
        repo_set="r",
        started_at="t",
        updated_at="t",
        status="cancelled",
        tripped_breakers=[
            TrippedBreaker(id="a", condition="c", action="pause", at="t"),
            TrippedBreaker(id="b", condition="c", action="stop", at="t"),
        ],
    )
    sig = implicit_signals(st, str(tmp_path))
    assert sig.landed is None and sig.followup_commits is None
    assert sig.killed and sig.run_status == "cancelled"
    assert (sig.tripped_breakers, sig.breaker_pauses, sig.breaker_kills) == (2, 1, 1)
    json.dumps(sig.model_dump())
