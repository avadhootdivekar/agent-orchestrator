"""Diff survival: how much of the code a run/task added is still present later.

Contract: ``docs-md/usage-signals-hld.md`` "Part 2". Git-only and read-only -- every git call
goes through ``isolation.git.GitRepo`` (hooks disabled, no network, timeout bounded). It is a
*heuristic* (content match, not true blame); see the HLD "Limits" before acting on a number.

Two halves:

* **Recording** (`discover_git_repos`, `record_git_start`, `current_heads`,
  `record_landed_ranges`): called by the engine on the main thread. Cheap (one ``rev-parse`` per
  repo per call) and failure-tolerant -- a missing git, a non-repo or an unborn HEAD is skipped
  with a debug log and can never fail a run.
* **Reporting** (`compute_survival`): pure over a ``RunState`` + the repo on disk. Never raises;
  any git problem becomes an ``unavailable`` reason on the affected row/report.

Attribution, most reliable first (per repo key):

1. ``isolation`` -- ``task_integration[t].landed_ranges`` (integration-head ranges the task's
   landing advanced), else its ``squash_commits`` sha (patch = ``sha^..sha``).
2. ``serial`` -- ``RunState.git_start_heads`` + ``TaskRunState.start_heads/end_heads``. A task
   whose [started_at, ended_at] window overlaps another HEAD-moving task is ``ambiguous``
   (counted at run level only).
3. ``time-window`` -- old runs with nothing recorded: commits in [started_at, updated_at] on the
   workspace repo. Run level only, confidence ``low``, never flagged.
"""

from __future__ import annotations

import logging
import os
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel

from .errors import GitError
from .isolation.git import GitRepo, Runner
from .isolation.worktrees import _repo_key
from .models import RunState, TaskIntegrationState, TaskRunState

logger = logging.getLogger(__name__)

# --- named constants (HLD Part 2) --------------------------------------------------------
MIN_LINES_FOR_FLAG = 10  # below this many added lines a low rate is noise, never flagged
LOW_SURVIVAL_RATE = 0.10  # survival below this (with enough lines) => `likely_worthless`
MIN_LINE_CHARS = 3  # added lines shorter than this (after strip) are trivial -> ignored
MAX_FILE_LINES = 20000  # a file with more added lines than this is skipped + reported
MAX_FILES = 500  # per unit; files beyond are skipped + reported
MAX_COMMITS = 500  # per unit (and per time-window scan); over => skipped + reported
MAX_REVERT_SCAN = 5000  # commits of ref history scanned for "This reverts commit"
EMPTY_TREE_SHA = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"  # parent of a root commit (sha1)
FLAG_LIKELY_WORTHLESS = "likely_worthless"
FLAG_REVERTED = "reverted"
TRUNC_COMMITS = "commits"
TRUNC_FILES = "files"
TRUNC_FILE_LINES = "file_lines"
DEFAULT_REF = "HEAD"
_REVERT_RE = re.compile(r"This reverts commit ([0-9a-fA-F]{7,64})")
_DIFF_PREFIXES = ["--src-prefix=a/", "--dst-prefix=b/"]  # immune to diff.noprefix/mnemonicPrefix
_SINCE_FMT = "%Y-%m-%d %H:%M:%S +0000"

Attribution = Literal["isolation", "serial", "ambiguous", "time-window", "mixed", "none"]
Confidence = Literal["high", "medium", "low"]


# ---------------------------------------------------------------------------------------
# Result models
# ---------------------------------------------------------------------------------------


class TaskSurvival(BaseModel):
    """One row: a task (``task_id`` set) or a whole run (``task_id`` None)."""

    run_id: str
    task_id: str | None = None
    attribution: Attribution = "none"
    confidence: Confidence | None = None
    commits: int = 0
    files: int = 0
    lines_added: int = 0
    lines_survived: int = 0
    survival_rate: float | None = None  # None when there are no countable added lines ("n/a")
    flags: list[str] = []
    truncated: list[str] = []  # e.g. ["files>500", "commits>500"]
    skipped_binary_files: int = 0
    unavailable: str | None = None
    note: str | None = None


class RunSurvival(BaseModel):
    run_id: str
    total: TaskSurvival
    tasks: list[TaskSurvival] = []
    unavailable: str | None = None


class SurvivalReport(BaseModel):
    ref: str | None = None
    runs: list[RunSurvival] = []
    unavailable: str | None = None
    ref_resolved: bool | None = None  # None when no --ref was asked for


def validate_ref(ref: str) -> str | None:
    """Syntax check for a user-supplied ref; returns an error message or None. Rejects
    option-injection (a leading ``-``) and whitespace/control characters."""
    if not ref or ref != ref.strip():
        return "ref must be non-empty with no surrounding whitespace"
    if ref.startswith("-"):
        return "ref must not start with '-'"
    if any(ord(c) < 32 or c == "\x7f" or c.isspace() for c in ref):
        return "ref must not contain whitespace or control characters"
    return None


# ---------------------------------------------------------------------------------------
# Failure-tolerant git access
# ---------------------------------------------------------------------------------------


class _Git:
    """Thin never-raising wrapper over ``GitRepo._run`` (the one choke point for git calls;
    survival needs read-only plumbing GitRepo has no dedicated method for)."""

    def __init__(self, runner: Runner | None = None) -> None:
        self._runner = runner
        self._repo: GitRepo | None = None
        self._failed = False

    def _get(self) -> GitRepo | None:
        if self._failed:
            return None
        if self._repo is None:
            try:
                self._repo = GitRepo(os.getcwd(), runner=self._runner)
            except (OSError, RuntimeError) as exc:
                logger.debug("survival.git_unavailable: %s", exc)
                self._failed = True
        return self._repo

    def run(self, cwd: str, args: list[str]) -> bytes | None:
        """stdout of ``git <args>`` in *cwd*, or None on any failure (non-zero exit, timeout,
        missing git, bad cwd)."""
        repo = self._get()
        if repo is None or not os.path.isdir(cwd):
            return None
        try:
            cp = repo._run(args, cwd=cwd, check=False)
        except (GitError, OSError, ValueError) as exc:
            logger.debug("survival.git_failed: %s", exc)
            return None
        return cp.stdout if cp.returncode == 0 else None

    def text(self, cwd: str, args: list[str]) -> str | None:
        out = self.run(cwd, args)
        return None if out is None else out.decode("utf-8", errors="replace")

    def rev(self, cwd: str, rev: str) -> str | None:
        out = self.text(cwd, ["rev-parse", "--verify", "--quiet", "--end-of-options", rev])
        return out.strip() or None if out is not None else None

    def is_ancestor(self, cwd: str, a: str, b: str) -> bool:
        return self.run(cwd, ["merge-base", "--is-ancestor", a, b]) is not None


# ---------------------------------------------------------------------------------------
# Recording (engine-side)
# ---------------------------------------------------------------------------------------


def discover_git_repos(repo_paths: dict[str, str]) -> dict[str, str]:
    """``{repo_key: toplevel}`` for every distinct non-bare git repo of *repo_paths* (keys match
    ``isolation.worktrees`` repo keys, so they line up with ``squash_commits``). Quiet: a
    non-git path is simply skipped. Deterministic (sorted input, deduped by common dir)."""
    repos: dict[str, str] = {}
    seen: set[str] = set()
    for rid in sorted(repo_paths):
        probe = GitRepo.probe(repo_paths[rid])
        if probe is None or probe.bare:
            continue
        common = os.path.normpath(probe.common_dir)
        if common in seen:
            continue
        seen.add(common)
        repos[_repo_key(os.path.normpath(probe.toplevel), common)] = os.path.normpath(
            probe.toplevel
        )
    return repos


def current_heads(git_repos: dict[str, str], runner: Runner | None = None) -> dict[str, str]:
    """``{repo_key: HEAD sha}``; repos with git missing/unborn HEAD are omitted. Never raises."""
    if not git_repos:
        return {}
    git = _Git(runner)
    heads: dict[str, str] = {}
    for key in sorted(git_repos):
        sha = git.rev(git_repos[key], "HEAD")
        if sha:
            heads[key] = sha
    return heads


def record_git_start(state: RunState, repo_paths: dict[str, str]) -> None:
    """Write ``git_repos``/``git_start_heads`` ONCE (no-op if already recorded). Never raises."""
    if state.git_start_heads or state.git_repos:
        return
    try:
        repos = discover_git_repos(repo_paths)
        state.git_repos = repos
        state.git_start_heads = current_heads(repos)
    except Exception as exc:  # recording must never fail a run
        logger.debug("survival.record_start_failed: %s", exc)


def record_landed_ranges(
    ti: TaskIntegrationState,
    head_from: dict[str, str | None],
    head_to: dict[str, str],
) -> None:
    """Append each repo's (from, to) integration-head range a landing advanced."""
    for key, to in head_to.items():
        frm = head_from.get(key)
        if frm and to and frm != to:
            ti.landed_ranges.setdefault(key, []).append([frm, to])


# ---------------------------------------------------------------------------------------
# Measurement
# ---------------------------------------------------------------------------------------


@dataclass
class _Unit:
    """One patch to measure: ``base..head`` in repo *cwd* (``base`` None => parent of head)."""

    task_id: str | None
    attribution: Attribution
    repo_key: str
    cwd: str
    head: str
    base: str | None = None
    confidence: Confidence = "high"
    note: str | None = None


@dataclass
class _Acc:
    commits: int = 0
    files: int = 0
    added: int = 0
    survived: int = 0
    binary: int = 0
    truncated: set[str] = field(default_factory=set)
    reverted: bool = False
    unavailable: list[str] = field(default_factory=list)
    low_confidence: bool = False
    notes: list[str] = field(default_factory=list)


def _unquote(path: str) -> str:
    """Undo git's C-style quoting of a path in a diff header."""
    if len(path) >= 2 and path.startswith('"') and path.endswith('"'):
        raw = path[1:-1].encode("latin-1", errors="replace")
        out = re.sub(
            rb"\\([0-7]{3}|.)",
            lambda m: (
                bytes([int(m.group(1), 8)])
                if len(m.group(1)) == 3
                else {b"n": b"\n", b"t": b"\t", b'"': b'"', b"\\": b"\\"}.get(
                    m.group(1), m.group(1)
                )
            ),
            raw,
        )
        return out.decode("utf-8", errors="replace")
    return path


def _is_countable(line: str) -> bool:
    s = line.strip()
    return len(s) >= MIN_LINE_CHARS and any(c.isalnum() for c in s)


def _line_counter(text: str) -> Counter[str]:
    return Counter(
        s for ln in text.split("\n") if _is_countable(ln) for s in (ln.rstrip("\r").strip(),)
    )


def parse_added_lines(diff: str) -> tuple[dict[str, Counter[str]], int]:
    """Parse ``git diff -U0`` output into ``{path: Counter(added countable lines)}`` plus the
    number of binary files seen. Pure."""
    added: dict[str, Counter[str]] = {}
    binary = 0
    cur: str | None = None
    in_header = False
    for line in diff.split("\n"):
        if line.startswith("diff --git "):
            in_header, cur = True, None
        elif in_header and line.startswith("+++ "):
            target = _unquote(line[4:].rstrip("\t"))
            cur = target[2:] if target.startswith("b/") else None
            if cur is not None:
                added.setdefault(cur, Counter())
        elif in_header and (line.startswith("Binary files") or line.startswith("GIT binary patch")):
            binary += 1
        elif line.startswith("@@"):
            in_header = False
        elif not in_header and cur is not None and line.startswith("+"):
            if _is_countable(line[1:]):
                added[cur][line[1:].rstrip("\r").strip()] += 1
    return added, binary


def _parse_name_status(raw: str) -> tuple[dict[str, str], set[str]]:
    """``git diff --name-status -z`` -> (renames old->new, deleted paths)."""
    renames: dict[str, str] = {}
    deleted: set[str] = set()
    toks = raw.split("\0")
    i = 0
    while i < len(toks):
        status = toks[i]
        if not status:
            i += 1
            continue
        if status[0] in "RC" and i + 2 < len(toks):
            if status[0] == "R":
                renames[toks[i + 1]] = toks[i + 2]
            i += 3
        elif i + 1 < len(toks):
            if status[0] == "D":
                deleted.add(toks[i + 1])
            i += 2
        else:
            break
    return renames, deleted


class _Measurer:
    """Measures units against one ref; caches per-repo ref resolution, rename maps, reverts."""

    def __init__(self, git: _Git, ref: str | None) -> None:
        self.git = git
        self.ref = ref or DEFAULT_REF
        self._ref_sha: dict[str, str | None] = {}
        self._reverts: dict[tuple[str, str], list[str]] = {}
        self._names: dict[tuple[str, str, str], tuple[dict[str, str], set[str]] | None] = {}

    def ref_sha(self, cwd: str) -> str | None:
        if cwd not in self._ref_sha:
            self._ref_sha[cwd] = self.git.rev(cwd, f"{self.ref}^{{commit}}")
        return self._ref_sha[cwd]

    def reverted_shas(self, cwd: str, ref_sha: str) -> list[str]:
        key = (cwd, ref_sha)
        if key not in self._reverts:
            out = self.git.text(
                cwd,
                [
                    "log",
                    f"--max-count={MAX_REVERT_SCAN}",
                    "--grep=This reverts commit",
                    "--format=%B%x00",
                    ref_sha,
                ],
            )
            self._reverts[key] = [m.lower() for m in _REVERT_RE.findall(out or "")]
        return self._reverts[key]

    def name_status(
        self, cwd: str, head: str, ref_sha: str
    ) -> tuple[dict[str, str], set[str]] | None:
        key = (cwd, head, ref_sha)
        if key not in self._names:
            if head == ref_sha:
                self._names[key] = ({}, set())
            else:
                out = self.git.text(
                    cwd, ["diff", "-M", "--name-status", "-z", "--no-color", head, ref_sha]
                )
                self._names[key] = None if out is None else _parse_name_status(out)
        return self._names[key]

    def measure(self, unit: _Unit, acc: _Acc) -> None:
        cwd, head = unit.cwd, unit.head
        ref_sha = self.ref_sha(cwd)
        if ref_sha is None:
            acc.unavailable.append(f"ref {self.ref!r} not resolvable in {unit.repo_key}")
            return
        base = unit.base
        if base is None:
            base = self.git.rev(cwd, f"{head}^") or EMPTY_TREE_SHA
        commits = self.git.text(
            cwd,
            ["rev-list", f"--max-count={MAX_COMMITS + 1}", "--ancestry-path", f"{base}..{head}"],
        )
        if base == EMPTY_TREE_SHA:
            commit_list = [head]
        elif commits is None:
            acc.unavailable.append(f"cannot list commits {unit.repo_key}")
            return
        else:
            commit_list = [c for c in commits.split() if c]
            if not commit_list:  # not an ancestry-path range (e.g. non-linear) -> fall back
                commits = self.git.text(
                    cwd, ["rev-list", f"--max-count={MAX_COMMITS + 1}", f"{base}..{head}"]
                )
                commit_list = (commits or "").split()
        if len(commit_list) > MAX_COMMITS:
            acc.commits += len(commit_list)
            acc.truncated.add(f"{TRUNC_COMMITS}>{MAX_COMMITS}")
            return
        acc.commits += len(commit_list)
        if any(
            r and c.lower().startswith(r)
            for c in commit_list
            for r in self.reverted_shas(cwd, ref_sha)
        ):
            acc.reverted = True
        if not self.git.is_ancestor(cwd, head, ref_sha):
            # head is rewritten/unreachable from ref: the metric is a best-effort tree compare.
            acc.low_confidence = True
            acc.notes.append(f"{unit.repo_key}: head not an ancestor of ref")

        diff = self.git.text(
            cwd,
            [
                "diff",
                "--no-color",
                "--no-ext-diff",
                "--no-textconv",
                "-M",
                "-U0",
                *_DIFF_PREFIXES,
                base,
                head,
            ],
        )
        if diff is None:
            acc.unavailable.append(f"cannot diff {unit.repo_key}")
            return
        added_by_file, binary = parse_added_lines(diff)
        acc.binary += binary
        ns = self.name_status(cwd, head, ref_sha)
        if ns is None:
            acc.unavailable.append(f"cannot compare {unit.repo_key} to ref")
            return
        renames, deleted = ns
        for n, path in enumerate(sorted(added_by_file)):
            counter = added_by_file[path]
            if n >= MAX_FILES:
                acc.truncated.add(f"{TRUNC_FILES}>{MAX_FILES}")
                break
            total = sum(counter.values())
            if total == 0:
                continue
            if total > MAX_FILE_LINES:
                acc.truncated.add(f"{TRUNC_FILE_LINES}>{MAX_FILE_LINES}")
                continue
            acc.files += 1
            acc.added += total
            if path in deleted:
                continue
            blob = self.git.run(cwd, ["cat-file", "blob", f"{ref_sha}:{renames.get(path, path)}"])
            if blob is None:
                continue
            at_ref = _line_counter(blob.decode("utf-8", errors="replace"))
            acc.survived += sum(min(n_, at_ref.get(line, 0)) for line, n_ in counter.items())


def _row(
    run_id: str,
    task_id: str | None,
    attribution: Attribution,
    acc: _Acc,
    units: list[_Unit],
    *,
    flaggable: bool = True,
) -> TaskSurvival:
    conf: Confidence | None = None
    if units:
        conf = "low" if acc.low_confidence else units[0].confidence
        if any(u.confidence == "low" for u in units):
            conf = "low"
    rate = (acc.survived / acc.added) if acc.added else None
    flags: list[str] = []
    if flaggable and conf != "low":
        if acc.added >= MIN_LINES_FOR_FLAG and rate is not None and rate < LOW_SURVIVAL_RATE:
            flags.append(FLAG_LIKELY_WORTHLESS)
    if flaggable and acc.reverted:
        flags.append(FLAG_REVERTED)
    unavailable = "; ".join(sorted(set(acc.unavailable))) or None
    notes = sorted(set(acc.notes))
    return TaskSurvival(
        run_id=run_id,
        task_id=task_id,
        attribution=attribution,
        confidence=conf,
        commits=acc.commits,
        files=acc.files,
        lines_added=acc.added,
        lines_survived=acc.survived,
        survival_rate=rate,
        flags=flags,
        truncated=sorted(acc.truncated),
        skipped_binary_files=acc.binary,
        unavailable=unavailable,
        note="; ".join(notes) or None,
    )


# ---------------------------------------------------------------------------------------
# Attribution planning
# ---------------------------------------------------------------------------------------


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _overlaps(a: TaskRunState, b: TaskRunState) -> bool:
    """Do two tasks' [started_at, ended_at] windows overlap? Unknown times count as overlap."""
    a0, a1, b0, b1 = (_parse_ts(x) for x in (a.started_at, a.ended_at, b.started_at, b.ended_at))
    if None in (a0, a1, b0, b1):
        return True
    assert a0 and a1 and b0 and b1
    return a0 < b1 and b0 < a1


def _repo_cwd(state: RunState, key: str, workspace_root: str) -> str:
    """Directory to run git in for *key*: recorded toplevel > derived from the integration
    common dir (``<top>/.git`` -> ``<top>``) > workspace root."""
    top = state.git_repos.get(key)
    if top and os.path.isdir(top):
        return top
    common = state.integration.repos.get(key)
    if common:
        base = os.path.dirname(common) if os.path.basename(common) == ".git" else common
        if os.path.isdir(base):
            return base
    return workspace_root


def _plan_isolation(state: RunState, workspace_root: str) -> list[_Unit]:
    units: list[_Unit] = []
    for tid in sorted(state.task_integration):
        ti = state.task_integration[tid]
        keys = sorted(set(ti.landed_ranges) | set(ti.squash_commits))
        for key in keys:
            cwd = _repo_cwd(state, key, workspace_root)
            ranges = ti.landed_ranges.get(key)
            if ranges:  # durable ranges win; several landings (retries) are summed
                for frm, to in ranges:
                    units.append(_Unit(tid, "isolation", key, cwd, to, base=frm))
            elif ti.squash_commits.get(key):
                units.append(_Unit(tid, "isolation", key, cwd, ti.squash_commits[key]))
    return units


def _plan_serial(state: RunState, workspace_root: str) -> list[_Unit]:
    units: list[_Unit] = []
    for key in sorted(state.git_start_heads):
        movers: list[tuple[str, str, str]] = []  # (task_id, base, end)
        prev = state.git_start_heads[key]
        settled = sorted(
            ((tid, ts) for tid, ts in state.tasks.items() if ts.end_heads.get(key)),
            key=lambda p: (p[1].ended_at or "", p[0]),
        )
        for tid, ts in settled:
            end = ts.end_heads[key]
            base = ts.start_heads.get(key) or prev
            if end != base:
                movers.append((tid, base, end))
            prev = end
        cwd = _repo_cwd(state, key, workspace_root)
        for tid, base, end in movers:
            amb = any(
                o != tid and _overlaps(state.tasks[tid], state.tasks[o]) for o, _b, _e in movers
            )
            units.append(
                _Unit(
                    tid,
                    "ambiguous" if amb else "serial",
                    key,
                    cwd,
                    end,
                    base=base,
                    confidence="medium",
                    note="window overlaps another HEAD-moving task" if amb else None,
                )
            )
    return units


def _time_window_units(
    state: RunState, workspace_root: str, git: _Git, ref_sha: str | None
) -> list[_Unit]:
    start, end = _parse_ts(state.started_at), _parse_ts(state.updated_at)
    if ref_sha is None or start is None or end is None:
        return []
    out = git.text(
        workspace_root,
        [
            "log",
            "--no-merges",
            f"--max-count={MAX_COMMITS + 1}",
            f"--since={start.astimezone(UTC).strftime(_SINCE_FMT)}",
            f"--until={end.astimezone(UTC).strftime(_SINCE_FMT)}",
            "--format=%H",
            ref_sha,
        ],
    )
    shas = (out or "").split()
    return [
        _Unit(None, "time-window", "workspace", workspace_root, sha, confidence="low")
        for sha in sorted(shas)
    ]


# ---------------------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------------------


def _survival_for_run(state: RunState, workspace_root: str, m: _Measurer) -> RunSurvival:
    units: list[_Unit] = []
    iso_units = _plan_isolation(state, workspace_root)
    units.extend(iso_units)
    if not state.integration.active and not iso_units:
        units.extend(_plan_serial(state, workspace_root))
    if not units and not state.git_start_heads:
        ws_ref = m.ref_sha(workspace_root)
        if ws_ref is None:
            return RunSurvival(
                run_id=state.run_id,
                total=TaskSurvival(
                    run_id=state.run_id, unavailable="no git repo / ref at workspace"
                ),
            )
        units.extend(_time_window_units(state, workspace_root, m.git, ws_ref))

    by_task: dict[str | None, list[_Unit]] = {}
    for u in units:
        by_task.setdefault(u.task_id, []).append(u)

    total_acc = _Acc()
    rows: list[TaskSurvival] = []
    for tid in sorted(t for t in by_task if t is not None):
        acc = _Acc()
        for u in by_task[tid]:
            m.measure(u, acc)
        kinds = {u.attribution for u in by_task[tid]}
        attribution: Attribution = "ambiguous" if "ambiguous" in kinds else sorted(kinds)[0]
        row = _row(
            state.run_id, tid, attribution, acc, by_task[tid], flaggable=attribution != "ambiguous"
        )
        if attribution == "ambiguous":
            # Counted at run level only: keep the commit count, not the per-task lines.
            row = row.model_copy(
                update={
                    "lines_added": 0,
                    "lines_survived": 0,
                    "survival_rate": None,
                    "files": 0,
                    "note": "ambiguous: window overlaps another task; counted at run level only",
                }
            )
        rows.append(row)
        total_acc.commits += acc.commits
        total_acc.files += acc.files
        total_acc.added += acc.added
        total_acc.survived += acc.survived
        total_acc.binary += acc.binary
        total_acc.truncated |= acc.truncated
        total_acc.reverted |= acc.reverted
        total_acc.unavailable += acc.unavailable
        total_acc.low_confidence |= acc.low_confidence
        total_acc.notes += acc.notes
    for u in by_task.get(None, []):
        m.measure(u, total_acc)

    kinds_all = {u.attribution for u in units}
    attr_total: Attribution = (
        "none" if not kinds_all else next(iter(kinds_all)) if len(kinds_all) == 1 else "mixed"
    )
    total = _row(
        state.run_id,
        None,
        attr_total,
        total_acc,
        units,
        flaggable=attr_total != "time-window",
    )
    tracked = {r.task_id for r in rows}
    # Read-only / commit-less tasks are "n/a" rows (never flagged).
    for tid in sorted(state.tasks):
        if tid not in tracked and state.tasks[tid].status in ("succeeded", "failed"):
            rows.append(TaskSurvival(run_id=state.run_id, task_id=tid, attribution="none"))
    rows.sort(key=lambda r: r.task_id or "")
    return RunSurvival(run_id=state.run_id, total=total, tasks=rows)


def compute_survival(
    states: list[RunState],
    workspace_root: str,
    *,
    ref: str | None = None,
    runner: Runner | None = None,
) -> SurvivalReport:
    """Survival for each run in *states* (given order) measured at *ref* (default: each repo's
    HEAD). Never raises: git problems become ``unavailable`` reasons."""
    report = SurvivalReport(ref=ref)
    if ref is not None:
        err = validate_ref(ref)
        if err:
            report.unavailable = err
            report.ref_resolved = False
            return report
    if GitRepo.version(runner) is None:
        report.unavailable = "git is not available"
        return report
    git = _Git(runner)
    m = _Measurer(git, ref)
    if ref is not None:
        cwds = {workspace_root}
        for st in states:
            cwds.update(st.git_repos.values())
            cwds.update(_repo_cwd(st, k, workspace_root) for k in st.integration.repos)
        report.ref_resolved = any(m.ref_sha(c) for c in sorted(cwds) if os.path.isdir(c))
        if not report.ref_resolved:
            report.unavailable = f"ref {ref!r} does not resolve in any repository"
            return report
    try:
        for st in states:
            report.runs.append(_survival_for_run(st, workspace_root, m))
    except Exception as exc:  # report-time safety net: degrade, never break callers
        logger.debug("survival.failed", exc_info=True)
        report.unavailable = f"survival failed: {exc}"
    return report
