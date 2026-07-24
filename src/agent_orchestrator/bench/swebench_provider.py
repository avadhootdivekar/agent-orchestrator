"""`swebench` WorkspaceProvider (E-Bt4Xk9 T-Sw5Hd9, FR-5, ADR-0009 D2).

Checks out a SWE-bench instance's repo at exactly `base_commit` into a task's
`ws/repo` (the `repo_dir` argument `materialize_workspace`/`WorkspaceProvider.prepare`
pass in -- `bench/workspace.py`, T-Wp4Nz5). Only ever imports `git` via `subprocess`
-- never `datasets`/`swebench` (those are the importer's (`swebench_import.py`) and
the grader's (T-Sg6Jf2) concern respectively) -- because every task's `source` is
just `{"type": "swebench", "instance_id": ...}`; the `repo`/`base_commit` this
provider needs are looked up from the suite's own committed, suite-relative
`instances.json` (written by `swebench_import.py`), never re-fetched from HuggingFace
at run time. This keeps `ao-bench run` on the large tier network-dependent on GitHub
only (for the one-time-per-repo clone below), not on HuggingFace, and keeps this
module's import graph free of the optional `swebench` extra entirely (SI-1 / NFR-1:
this module never needs that extra to *run*, only `git`).

Checkout strategy (documented for T-Sg6Jf2, the downstream grader, per this task's
Handoff Boundary): `repo_dir` ends up as a REAL, independent, non-bare git checkout
with its own `.git`, HEAD detached at exactly `base_commit`, and a clean working tree
(nothing staged/modified). `git -C repo_dir diff` therefore captures exactly an
agent's file mutations against that baseline -- no extra `git init`/`commit` step is
needed, and `git -C repo_dir rev-parse HEAD == base_commit` holds immediately after
`prepare()` returns (AC3). This is achieved via a two-step LOCAL clone chain:

  1. `_ensure_repo_cache(repo)` -- a persistent, shared, FULL clone of
     `https://github.com/<repo>.git`, ONE per repo, reused across every instance of
     that repo. This is the only step that ever needs the network, and only the
     first time a given repo is requested. (An earlier draft used a blobless
     `--filter=blob:none` partial clone here to save disk -- reverted: chaining a
     second local partial clone off of it during step 2 hit
     "filtering not recognized by server" / promisor-fetch failures against the
     local `file://`-style transport in this environment, i.e. real, verified
     breakage, not a theoretical concern. A full cache clone trades some one-time-
     per-repo disk/bandwidth for a checkout step that just works.)
  2. `_checkout_into(cache_dir, base_commit, repo_dir)` -- a further LOCAL, FULL
     clone from the cache into `repo_dir` (same-filesystem, network-free -- `git`
     hardlinks objects rather than copying them when source and destination share a
     filesystem, so this is fast despite being "full") followed by
     `git checkout <base_commit>`.

Cache location: `playground/.tmp/swebench-repo-cache/<owner>__<name>/` -- a SIBLING of
`bench/workspace.py`'s `BENCH_WORKSPACE_ROOT` (`playground/.tmp/bench/`), not nested
inside it (T-Wp4Nz5 STATUS.md forward note: a checkout cache must live outside the
per-run `ws` tree so `materialize_workspace`'s per-run `shutil.rmtree(ws_path)` can
never touch it). Still repo-local + gitignored (constraint C2 -- mirrors
`playground/.tmp/`'s existing gitignore entry).
"""

from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path

from .errors import SubjectError
from .registries import register_workspace_provider
from .spec import BenchTask
from .workspace import INSTRUCTION_FILENAME, REPO_ROOT, WorkspaceProvider

SWEBENCH_PROVIDER_TYPE = "swebench"

# Suite-relative, written by `swebench_import.py` alongside `suite.json`.
INSTANCES_FILENAME = "instances.json"

# Sibling of workspace.py's BENCH_WORKSPACE_ROOT (playground/.tmp/bench/) -- see
# module docstring for why this must NOT be nested inside it.
SWEBENCH_REPO_CACHE_ROOT: Path = REPO_ROOT / "playground" / ".tmp" / "swebench-repo-cache"

# Generous bound for a real GitHub clone of a mature OSS repo (django/sympy-sized).
_GIT_CLONE_TIMEOUT_SECONDS = 900
_GIT_CHECKOUT_TIMEOUT_SECONDS = 120

# Serializes cache clone/create across concurrently-scheduled tasks (D4's bounded
# thread-pool runner): two tasks for the SAME repo racing `_ensure_repo_cache` could
# otherwise both see "not cloned yet" and clone twice into the same path. A single
# process-wide lock is fine here -- the clone is a rare, one-time-per-repo event; every
# other call is a fast existence check or a per-task local clone (independent dirs,
# no shared mutable state to race on).
_cache_lock = threading.Lock()


def _run_git(
    args: list[str], *, cwd: Path | None = None, timeout: int = _GIT_CHECKOUT_TIMEOUT_SECONDS
) -> str:
    """Run a `git` subcommand, raising `SubjectError` (never a raw exception) on any
    failure -- mirrors `FixtureProvider`'s own `SubjectError`-on-failure convention
    (bench/workspace.py).
    """
    try:
        result = subprocess.run(  # noqa: S603 -- fixed argv, no shell, bounded timeout
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        joined = " ".join(args)
        raise SubjectError(f"swebench provider: `git {joined}` failed to run: {exc}") from exc
    if result.returncode != 0:
        raise SubjectError(
            f"swebench provider: `git {' '.join(args)}` failed (exit {result.returncode}): "
            f"{result.stderr.strip()}"
        )
    return result.stdout


def _load_instances_index(suite_base_dir: Path) -> dict[str, dict]:
    """Load `<suite_base_dir>/instances.json` and index it by `instance_id`."""
    path = suite_base_dir / INSTANCES_FILENAME
    if not path.is_file():
        raise SubjectError(
            f"swebench provider: {INSTANCES_FILENAME} not found at {path} "
            "(expected alongside suite.json; generate it with `ao-bench import-swebench`)"
        )
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise SubjectError(f"swebench provider: failed to read {path}: {exc}") from exc
    instances = data.get("instances")
    if not isinstance(instances, list):
        raise SubjectError(f"swebench provider: malformed {path}: missing 'instances' list")
    return {inst["instance_id"]: inst for inst in instances if "instance_id" in inst}


def _repo_clone_url(repo: str) -> str:
    """The URL `_ensure_repo_cache` clones *repo* from. A separate, trivially
    monkeypatchable function (rather than inlined into `_ensure_repo_cache`) purely so
    a test can redirect it at a local `file://` fake origin -- a real GitHub network
    clone is never exercised by the unit test suite (only the opt-in `swebench`-marked
    real test does).
    """
    return f"https://github.com/{repo}.git"


def _ensure_repo_cache(repo: str, *, cache_root: Path | None = None) -> Path:
    """Clone (once) or reuse a persistent local git cache of *repo*.

    *cache_root* defaults to the module-level `SWEBENCH_REPO_CACHE_ROOT`, read via a
    plain global lookup (not a bound default-parameter value) so a test that
    monkeypatches the module attribute affects this function without needing to pass
    an explicit override on every call -- mirrors `bench/workspace.py`'s own
    `BENCH_WORKSPACE_ROOT` convention (a plain module constant, real repo-local
    `playground/.tmp/` tree used directly by tests too, per that module's test file).
    """
    root = cache_root if cache_root is not None else SWEBENCH_REPO_CACHE_ROOT
    cache_dir = root / repo.replace("/", "__")
    with _cache_lock:
        if not (cache_dir / ".git").exists():
            cache_dir.parent.mkdir(parents=True, exist_ok=True)
            _run_git(
                ["clone", _repo_clone_url(repo), str(cache_dir)], timeout=_GIT_CLONE_TIMEOUT_SECONDS
            )
    return cache_dir


def _checkout_into(cache_dir: Path, base_commit: str, repo_dir: Path) -> None:
    """Local (network-free), independent clone from *cache_dir* into *repo_dir*,
    checked out with a clean working tree at exactly *base_commit* (module docstring).
    *repo_dir* must not already exist (mirrors `FixtureProvider`/`shutil.copytree`'s
    own precondition -- `materialize_workspace` never calls a provider with an
    existing `repo_dir`).
    """
    _run_git(
        ["clone", "--no-checkout", str(cache_dir), str(repo_dir)],
        timeout=_GIT_CLONE_TIMEOUT_SECONDS,
    )
    _run_git(["checkout", "--quiet", base_commit], cwd=repo_dir)
    _exclude_instruction_file(repo_dir)


def _exclude_instruction_file(repo_dir: Path) -> None:
    """Add `INSTRUCTION.md` to `repo_dir`'s LOCAL, never-committed
    `.git/info/exclude` (git's per-clone gitignore-equivalent -- distinct from a
    tracked `.gitignore`, which would itself show up as a spurious change).

    `materialize_workspace` (bench/workspace.py) copies the task instruction into
    `repo_dir/INSTRUCTION.md` AFTER this provider's `prepare()` returns, so the file
    does not exist yet here -- this only pre-registers the exclude pattern. Written
    for T-Sg6Jf2 (the downstream grader): a plain `git diff` already only reports
    tracked-file changes and would naturally skip an untracked INSTRUCTION.md, but
    this exclude entry keeps `git status --porcelain` / `git add -A` clean too, in
    case the grader's patch-extraction ever uses either.
    """
    exclude_path = repo_dir / ".git" / "info" / "exclude"
    exclude_path.parent.mkdir(parents=True, exist_ok=True)
    with exclude_path.open("a") as f:
        f.write(f"{INSTRUCTION_FILENAME}\n")


class SweBenchWorkspaceProvider(WorkspaceProvider):
    """Checks out an instance's repo at `base_commit` into `repo_dir` (T-Sw5Hd9, FR-5)."""

    def prepare(
        self,
        task: BenchTask,
        repo_dir: Path,
        *,
        suite_base_dir: Path,
        subject_base_dir: Path | None = None,
    ) -> None:
        if task.source is None or task.source.type != SWEBENCH_PROVIDER_TYPE:
            raise SubjectError(
                f"Task {task.id!r}: swebench provider requires source.type == "
                f"{SWEBENCH_PROVIDER_TYPE!r}"
            )
        extra = task.source.model_extra or {}
        instance_id = extra.get("instance_id")
        if not instance_id:
            raise SubjectError(
                f"Task {task.id!r}: source.instance_id is required for the swebench provider"
            )

        index = _load_instances_index(suite_base_dir)
        meta = index.get(instance_id)
        if meta is None:
            raise SubjectError(
                f"Task {task.id!r}: instance_id {instance_id!r} not found in "
                f"{suite_base_dir / INSTANCES_FILENAME}"
            )
        repo = meta.get("repo")
        base_commit = meta.get("base_commit")
        if not repo or not base_commit:
            raise SubjectError(
                f"Task {task.id!r}: instance {instance_id!r} is missing 'repo'/'base_commit' "
                f"in {INSTANCES_FILENAME}"
            )

        cache_dir = _ensure_repo_cache(repo)
        _checkout_into(cache_dir, base_commit, repo_dir)

        head = _run_git(["rev-parse", "HEAD"], cwd=repo_dir).strip()
        if head != base_commit:
            raise SubjectError(
                f"Task {task.id!r}: checkout landed on {head!r}, expected base_commit "
                f"{base_commit!r}"
            )


# Registered at import time (mirrors workspace.py's own FixtureProvider registration).
# Requires the CORRESPONDING one-line addition of "swebench" to bench/spec.py's
# KNOWN_WORKSPACE_PROVIDER_TYPES -- registering the class alone is not sufficient,
# `load_suite` would still reject a task's `source.type="swebench"` (T-Wp4Nz5 learning,
# see that module's forward notes).
register_workspace_provider(SWEBENCH_PROVIDER_TYPE, SweBenchWorkspaceProvider)
