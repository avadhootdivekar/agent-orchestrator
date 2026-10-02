"""Implicit usefulness signals derived on demand from run state + git (E-Us9Kd4 Part 3).

Nothing is stored. Every signal is None/unknown (with a reason) when the data does not
support it, and `implicit_signals` never raises because git is missing or the repo is odd.
Circumstantial evidence only -- see the HLD "Limits" section.

Not implemented on purpose (Non-MVP): re-run-of-similar-prompt (the prompt is not recorded
in RunState). `reverted_commits` is filled by the survival join, not here.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from .isolation.git import GitRepo
from .models import RunState

log = logging.getLogger(__name__)

MAX_FOLLOWUP_COMMITS = 200  # commits scanned per repo after the run's last head
MAX_RUNS_SCANNED = 500  # other runs read to learn which commits ao itself recorded
_RUNS_SUBDIR = ".orchestrator/runs"
_STATE_FILE = "state.json"
_GIT_DIR_SUFFIX = "/.git"
_COMMIT_MARK = "\x01"
BREAKER_PAUSE_ACTION = "pause"
BREAKER_KILL_ACTIONS = ("fail", "stop")

Landed = Literal["landed", "not_landed"]
Confidence = Literal["normal", "low"]


class RunSignals(BaseModel):
    landed: Landed | None = None
    landed_reason: str | None = None
    followup_commits: int | None = None
    followup_confidence: Confidence | None = None
    followup_reason: str | None = None
    reverted_commits: int | None = None  # filled later by the survival join
    run_status: str
    killed: bool = False  # cancelled run
    tripped_breakers: int = 0
    breaker_pauses: int = 0
    breaker_kills: int = 0


def _repo_path(common_dir: str) -> str | None:
    """Working-tree path for a recorded git common dir (`<top>/.git`); None for bare/odd."""
    return common_dir[: -len(_GIT_DIR_SUFFIX)] if common_dir.endswith(_GIT_DIR_SUFFIX) else None


def _workspace_runs(ws_root: str) -> tuple[list[RunState], bool]:
    """Other runs' states (sorted by id, bounded). Second value: scan was truncated/lossy."""
    runs_dir = Path(ws_root) / _RUNS_SUBDIR
    if not runs_dir.is_dir():
        return [], False
    ids = sorted(p.name for p in runs_dir.iterdir() if p.is_dir())
    lossy = len(ids) > MAX_RUNS_SCANNED
    states: list[RunState] = []
    for rid in ids[:MAX_RUNS_SCANNED]:
        try:
            states.append(
                RunState.model_validate_json((runs_dir / rid / _STATE_FILE).read_text("utf-8"))
            )
        except (OSError, ValueError):
            lossy = True
    return states, lossy


def _recorded_shas(states: list[RunState]) -> set[str]:
    shas: set[str] = set()
    for st in states:
        shas.update(st.integration.heads.values())
        for ti in st.task_integration.values():
            shas.update(ti.squash_commits.values())
    return shas


def _lacks_recorded_heads(st: RunState) -> bool:
    return not st.integration.heads and not any(
        ti.squash_commits for ti in st.task_integration.values()
    )


def _parse_log(text: str) -> list[tuple[str, set[str]]]:
    """`--format=\\x01%H --name-only` output -> [(sha, files)]."""
    out: list[tuple[str, set[str]]] = []
    for chunk in text.split(_COMMIT_MARK)[1:]:
        lines = [ln for ln in chunk.splitlines() if ln.strip()]
        if lines:
            out.append((lines[0].strip(), {ln.strip() for ln in lines[1:]}))
    return out


def implicit_signals(state: RunState, ws_root: str) -> RunSignals:
    breakers = state.tripped_breakers
    sig = RunSignals(
        run_status=state.status,
        killed=state.status == "cancelled",
        tripped_breakers=len(breakers),
        breaker_pauses=sum(1 for b in breakers if b.action == BREAKER_PAUSE_ACTION),
        breaker_kills=sum(1 for b in breakers if b.action in BREAKER_KILL_ACTIONS),
    )
    heads = state.integration.heads
    if not heads:
        reason = "no integration head recorded (run did not use isolation)"
        sig.landed_reason = sig.followup_reason = reason
        return sig

    other_runs, lossy = _workspace_runs(ws_root)
    recorded = _recorded_shas(other_runs)
    lossy = lossy or any(_lacks_recorded_heads(s) for s in other_runs)

    landed_states: list[bool] = []
    reasons: list[str] = []
    followups = 0
    followup_computed = False
    for key in sorted(heads):
        head = heads[key]
        path = _repo_path(state.integration.repos.get(key, ""))
        if path is None:
            reasons.append(f"{key}: repo location unknown")
            continue
        try:
            repo = GitRepo(path)
            if repo.rev_parse("HEAD") is None:
                reasons.append(f"{key}: repo HEAD unresolvable")
                continue
            is_landed = repo.is_ancestor(head, "HEAD")
        except Exception as e:  # git missing/timeout/not a repo: unknown, never raise
            log.debug("implicit_signals: git unavailable for %s: %s", key, e)
            reasons.append(f"{key}: git unavailable ({type(e).__name__})")
            continue
        landed_states.append(is_landed)
        if not is_landed:
            reasons.append(f"{key}: integration head not an ancestor of HEAD")
            continue
        base = state.integration.base_heads.get(key)
        if not base:
            reasons.append(f"{key}: no base head recorded; follow-ups unknown")
            continue
        try:
            files = set(repo.diff_names(path, base, head))
            raw = repo._run(  # noqa: SLF001 - read-only log, no public equivalent
                [
                    "log",
                    "--no-merges",
                    f"--max-count={MAX_FOLLOWUP_COMMITS}",
                    f"--format={_COMMIT_MARK}%H",
                    "--name-only",
                    f"{head}..HEAD",
                ],
                cwd=path,
            )
        except Exception as e:
            reasons.append(f"{key}: follow-up scan failed ({type(e).__name__})")
            continue
        commits = _parse_log(raw.stdout.decode("utf-8", "replace"))
        if len(commits) >= MAX_FOLLOWUP_COMMITS:
            lossy = True
        followup_computed = True
        followups += sum(1 for sha, fs in commits if sha not in recorded and fs & files)

    if landed_states:
        sig.landed = "landed" if all(landed_states) else "not_landed"
    else:
        sig.landed_reason = "; ".join(reasons) or "unknown"
    if reasons and sig.landed is not None:
        sig.landed_reason = "; ".join(reasons)
    if followup_computed:
        sig.followup_commits = followups
        sig.followup_confidence = "low" if lossy else "normal"
    else:
        sig.followup_reason = "; ".join(reasons) or "unknown"
    return sig
