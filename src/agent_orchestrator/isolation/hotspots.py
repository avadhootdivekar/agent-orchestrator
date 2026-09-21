"""Hotspot signal: per-path git churn + prior-conflict weights (E-Wk9Tz3 FR-11, HLD §9.2,
§11 M8).

Feeds two consumers: `scheduling.overlap.rank_wave` (via `Hotspots.weights()`, a flat
`path -> weight` map) and the `task-breakdown` agent, which declares `.ao/hotspots.json`
as an input so decomposition can steer away from hot files instead of guessing (HLD
§9.2). Persisted at a workspace-relative path (default `.ao/hotspots.json`,
`models.DEFAULT_HOTSPOTS_PATH`) by `ao hotspots` (`cli.py`).

Unlike `scheduling/overlap.py`, this module does real IO (git subprocess via
`isolation.git.GitRepo`, filesystem reads of prior runs' `state.json`) — that split is
deliberate (HLD §11 M8: `rank_wave` must stay importable and testable without git).

Known limitation, stated rather than hidden (HLD §9.2): raw `--name-only` churn is a
noisy signal on its own — a large, frequently-touched-but-cosmetic file can outrank a
small, semantically hot one, and a file that WAS a hotspot before being deleted is not a
hotspot now. Mitigations here: the conflict-observation term (`CONFLICT_WEIGHT`, ground
truth from actual integration conflicts, not merely commit frequency), a bounded
`since_days` window (default 180), and dropping any path that no longer exists at HEAD.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import BaseModel, ValidationError

from ..errors import GitError
from .git import GitRepo

logger = logging.getLogger(__name__)

# --- named constants (no magic literals downstream) -------------------------------

HOTSPOTS_SCHEMA_VERSION = "1.0"
DEFAULT_SINCE_DAYS = 180
DEFAULT_TOP_K = 40
# HLD §9.2: "a real conflict beats churn" -- an actual observed integration conflict on
# a path is ground truth, weighted well above a single commit touching it.
CONFLICT_WEIGHT = 5

# Directory layout `observed_conflicts` scans (mirrors `runstate.RunStateStore._path`'s
# own `<workspace_root>/.orchestrator/runs/<run_id>/state.json` layout -- duplicated as a
# literal glob rather than imported, since importing `runstate.RunStateStore` here would
# pull in `ArtifactStore` construction this read-only, best-effort scan has no need of).
_RUNS_STATE_GLOB = ".orchestrator/runs/*/state.json"


# ---------------------------------------------------------------------------------
# Persisted shape -- MUST match docs-md/task-isolation-hld.md §11 M8 exactly (shared,
# locked contract with T-Tp7Zs2, which declares this file as a breakdown-agent input).
# ---------------------------------------------------------------------------------


class HotspotEntry(BaseModel):
    path: str
    churn: int = 0
    conflicts: int = 0
    weight: float = 0.0


class RepoHotspots(BaseModel):
    entries: list[HotspotEntry] = []


class Hotspots(BaseModel):
    """In-memory + on-disk representation of `.ao/hotspots.json` (HLD §11 M8).

    `version`/`generated_at`/`window_days`/`repos` are exactly the top-level JSON keys —
    `model_dump_json()` round-trips byte-for-byte with the locked shape, no custom
    (de)serialization needed.
    """

    version: str = HOTSPOTS_SCHEMA_VERSION
    generated_at: str = ""
    window_days: int = DEFAULT_SINCE_DAYS
    repos: dict[str, RepoHotspots] = {}

    def weights(self) -> dict[str, float]:
        """Flatten every repo's entries into the single `path -> weight` map
        `scheduling.overlap.rank_wave`'s `hotspots` argument expects (HLD §9.1's
        `hotspot_weight`). Where the same path appears under more than one repo id, the
        higher weight wins (the more alarming signal should never be shadowed by a
        smaller one for the same literal path).

        This is the exact hand-off `T-En8Hd4`'s engine call site uses: `rank_wave(ready,
        touches, load_hotspots(path).weights(), n)` — see this ticket's STATUS.md "hook
        points" note.
        """
        merged: dict[str, float] = {}
        for repo_hotspots in self.repos.values():
            for entry in repo_hotspots.entries:
                if entry.weight > merged.get(entry.path, 0.0):
                    merged[entry.path] = entry.weight
        return merged


def merge_hotspots(existing: Hotspots, new: Hotspots) -> Hotspots:
    """Merge *new* into *existing*, replacing only the repo ids *new* actually
    populated — so re-running `ao hotspots --repo X` never clobbers a previously
    computed `--repo Y` entry in the same file (idempotent, multi-repo-accumulating
    writes). *new*'s `version`/`generated_at`/`window_days` win (it is the freshest
    computation).
    """
    merged_repos = dict(existing.repos)
    merged_repos.update(new.repos)
    return Hotspots(
        version=new.version,
        generated_at=new.generated_at,
        window_days=new.window_days,
        repos=merged_repos,
    )


def load_hotspots(path: str) -> Hotspots:
    """Load `.ao/hotspots.json` from *path*, tolerating any failure (HLD §11 M8 edge
    case / AC-11): missing file, unreadable file, or schema-invalid JSON all yield an
    EMPTY `Hotspots()` (so `rank_wave` degrades to plain order, never a crash) plus
    exactly one warning naming the reason. A run must never fail because its hotspot
    signal is stale or absent -- this is advisory data, not a gate.
    """
    try:
        text = Path(path).read_text()
    except OSError as exc:
        logger.warning("hotspots: could not read %s (%s); using an empty hotspot map", path, exc)
        return Hotspots()
    try:
        return Hotspots.model_validate_json(text)
    except (ValidationError, ValueError) as exc:
        logger.warning(
            "hotspots: %s is not a valid hotspots file (%s); using an empty hotspot map",
            path,
            exc,
        )
        return Hotspots()


def parse_churn(git_log_text: str) -> Counter[str]:
    """Pure parse of `git log --pretty=format: --name-only -z` stdout
    (`GitRepo.log_name_only`, HLD §9.2) into a `path -> commit-touch-count` map.

    Splits on NUL (`\\0`), not newline (review C-3): without `-z`, git's default
    `core.quotePath` C-style-octal-escapes and double-quotes any path containing a
    non-ASCII or otherwise "unusual" byte, which would corrupt the path string this
    function returns; `-z` disables that quoting entirely and terminates every path
    (and separates consecutive commits) with a literal NUL instead of a newline, so
    splitting on NUL recovers the exact original bytes. `--pretty=format:` (an empty
    format string) means each commit's own output is *nothing but* its changed-file
    entries; git still emits an extra NUL between commits' entries (mirroring the
    blank line it would otherwise use), which yields an empty segment after splitting
    -- dropped exactly like a blank line was before, no per-commit boundary tracking
    needed. `--no-merges` (a flag applied by the CALLER before this text is captured,
    not by this function) already keeps merge commits out of the input entirely, so
    this function does not need to special-case them; it treats every non-empty
    segment identically.
    """
    counts: Counter[str] = Counter()
    for entry in git_log_text.split("\0"):
        path = entry.strip()
        if path:
            counts[path] += 1
    return counts


def observed_conflicts(workspace_root: str) -> Counter[str]:
    """Scan every prior run's `.orchestrator/runs/*/state.json` under *workspace_root*
    and count, per path, how many `TaskIntegrationState.conflicted_paths` entries
    named it (HLD §9.2's `conflicts` term -- ground truth, weighted `CONFLICT_WEIGHT`
    above raw churn in `compute_hotspots`).

    Best-effort and read-only: a missing runs directory, an unreadable or malformed
    `state.json`, or one predating this epic (no `task_integration` key at all) each
    contribute zero rather than raising -- this is an advisory signal layered on top of
    git history, never a hard dependency of it.
    """
    counts: Counter[str] = Counter()
    for state_path in sorted(Path(workspace_root).glob(_RUNS_STATE_GLOB)):
        try:
            data = json.loads(state_path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            logger.debug("hotspots: skipping unreadable run state %s (%s)", state_path, exc)
            continue
        if not isinstance(data, dict):
            continue
        task_integration = data.get("task_integration")
        if not isinstance(task_integration, dict):
            continue
        for entry in task_integration.values():
            if not isinstance(entry, dict):
                continue
            for conflicted_path in entry.get("conflicted_paths") or []:
                if isinstance(conflicted_path, str):
                    counts[conflicted_path] += 1
    return counts


def _utc_now() -> datetime:
    return datetime.now(UTC)


def compute_hotspots(
    repo_id: str,
    git_repo: GitRepo,
    cwd: str,
    *,
    since_days: int = DEFAULT_SINCE_DAYS,
    top_k: int = DEFAULT_TOP_K,
    conflicts: Mapping[str, int] | None = None,
    clock: Callable[[], datetime] | None = None,
) -> Hotspots:
    """Derive a `Hotspots` snapshot for one repo (HLD §9.2's `compute_hotspots`,
    verbatim algorithm; `repo_id` added so the returned object is directly the locked
    `.ao/hotspots.json` shape -- `{"repos": {"<repo_id>": {...}}}` -- rather than the
    pseudocode's abbreviated single-repo sketch, since the persisted file is the shared
    contract with `T-Tp7Zs2`).

    `git_repo`/`cwd` are an already-constructed `GitRepo` and the working-tree path to
    run history queries against -- never constructed internally, so callers (tests, the
    CLI) control the git binary/runner/timeout. `clock` is injectable (never
    `datetime.now()` called directly on this path) so `--since` windowing and
    `generated_at` are deterministic under test.

    Merges raw commit churn with *conflicts* (a path -> occurrence-count map, typically
    `observed_conflicts(workspace_root)`), each conflict occurrence worth
    `CONFLICT_WEIGHT` churn points, then drops any path that is no longer tracked at
    HEAD (a deleted or renamed-away file has nothing left to co-schedule around) BEFORE
    truncating to *top_k* -- so the result always holds the top *top_k* still-existing
    paths, not top_k-then-filtered-to-fewer. The tracked/untracked check is ONE
    `GitRepo.ls_files` call over every candidate path (review W-1), not one `is_tracked`
    subprocess per path.
    """
    now = (clock or _utc_now)()
    since_arg = None
    if since_days > 0:
        since_arg = (now - timedelta(days=since_days)).strftime("%Y-%m-%d")

    try:
        raw_text = git_repo.log_name_only(cwd, since=since_arg, no_merges=True)
    except GitError as exc:
        logger.warning("hotspots: `git log` failed in %s (%s); treating churn as empty", cwd, exc)
        raw_text = ""

    churn = parse_churn(raw_text)
    conflict_counts: dict[str, int] = dict(conflicts or {})

    weight: dict[str, float] = {path: float(count) for path, count in churn.items()}
    for path, count in conflict_counts.items():
        weight[path] = weight.get(path, 0.0) + count * CONFLICT_WEIGHT

    # Review W-1: ONE `ls_files` call for every candidate path, not one `is_tracked`
    # subprocess per path -- O(1) git invocations regardless of how many distinct paths
    # churned in the window. Skipped entirely when there is nothing to check (an empty
    # `weight` would otherwise make `ls_files` list the WHOLE repo via its no-paths-
    # filter default, which is exactly the unbounded cost this fix removes).
    tracked = git_repo.ls_files(cwd, paths=list(weight)) if weight else set()

    ranked_paths = sorted(weight, key=lambda p: (-weight[p], p))
    entries: list[HotspotEntry] = []
    for path in ranked_paths:
        if len(entries) >= top_k:  # checked BEFORE tracked-probe/append: top_k=0 -> []
            break
        if path not in tracked:
            continue  # HLD §9.2 / AC-9: dropped, not merely deprioritized
        entries.append(
            HotspotEntry(
                path=path,
                churn=int(churn.get(path, 0)),
                conflicts=int(conflict_counts.get(path, 0)),
                weight=weight[path],
            )
        )

    return Hotspots(
        version=HOTSPOTS_SCHEMA_VERSION,
        generated_at=now.isoformat(),
        window_days=since_days,
        repos={repo_id: RepoHotspots(entries=entries)},
    )


__all__ = [
    "CONFLICT_WEIGHT",
    "DEFAULT_SINCE_DAYS",
    "DEFAULT_TOP_K",
    "HOTSPOTS_SCHEMA_VERSION",
    "Hotspots",
    "HotspotEntry",
    "RepoHotspots",
    "compute_hotspots",
    "load_hotspots",
    "merge_hotspots",
    "observed_conflicts",
    "parse_churn",
]
