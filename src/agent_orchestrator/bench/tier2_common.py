"""Shared plumbing for the Tier-2 (brownfield) providers and the `pytest_targeted` grader
(HLD `docs-md/benchmark-discriminating-suites-hld.md` §4.2/§8).

`repo_mutation` and `pr_mined` both: check a pinned commit out of the shared repo cache
(`swebench_provider`'s cache + checkout helpers, reused rather than copied), mutate/prune
the working tree, park agent-hidden assets under `grading_dir/hidden/**`, and write a
`recipe.resolved.json` the `pytest_targeted` grader reads. This module owns the pieces
those three share:

* the on-disk layout constants (`hidden/`, `reference/`, `recipe.resolved.json`);
* `checkout_pinned` -- cache clone + "commit is present" guarantee + checkout;
* `snapshot_single_commit` -- re-snapshot the prepared tree as ONE parentless commit.

Why snapshot: a checkout from the cache carries the whole upstream history. For a mutated
tree, `git diff` / `git stash` / `git checkout .` would reveal (or trivially undo) the
injected bug or excised feature; for a PR-mined tree, `git log --all` / `git fetch origin`
would hand the agent the merge commit -- i.e. the answer. Replacing `.git` with a fresh
single-commit repo keeps `git diff` meaningful (it shows exactly the agent's changes),
removes every pristine/future object, and gives the grader a baseline commit to measure
collateral damage against. (`swebench_provider` has the same history exposure; it is
intentionally left untouched here.)
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any

from .errors import SubjectError
from .swebench_provider import (
    _GIT_CLONE_TIMEOUT_SECONDS,
    _cache_lock,
    _checkout_into,
    _ensure_repo_cache,
    _exclude_instruction_file,
    _run_git,
)

# Layout under the grader-owned `grading_dir` (never visible to the agent).
HIDDEN_DIRNAME = "hidden"  # restored into repo_dir by the grader, at grade time only
REFERENCE_DIRNAME = "reference"  # pristine files; `FakeSubject copy-reference` overlays them
RESOLVED_RECIPE_FILENAME = "recipe.resolved.json"

# Pinned commits must be full 40-hex shas: a branch name/abbreviation could silently drift.
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")

# Directory names that mark a path as a test file, plus the usual file-name conventions.
_TEST_DIR_NAMES = frozenset({"tests", "test", "testing"})

# Identity for the snapshot commit (no global git config may exist on a CI/runner box).
_SNAPSHOT_GIT_CONFIG = (
    "-c",
    "user.name=ao-bench",
    "-c",
    "user.email=ao-bench@localhost",
    "-c",
    "commit.gpgsign=false",
)
_SNAPSHOT_MESSAGE = "bench workspace snapshot"


def is_test_path(path: str) -> bool:
    """True when *path* (repo-relative, posix) looks like a test file or test support
    file: lives under a `tests/`-style dir, is `test_*.py` / `*_test.py`, or a conftest."""
    posix = PurePosixPath(path)
    name = posix.name
    return (
        any(part in _TEST_DIR_NAMES for part in posix.parts[:-1])
        or name.startswith("test_")
        or name.endswith("_test.py")
        or name == "conftest.py"
    )


def is_test_module(path: str) -> bool:
    """True for a pytest-collectable test module (`test_*.py` / `*_test.py`)."""
    name = PurePosixPath(path).name
    return name.endswith(".py") and (name.startswith("test_") or name.endswith("_test.py"))


def safe_relpath(path: str, *, what: str) -> str:
    """Validate a recipe/instance-supplied repo-relative path (no abs path, `..`, or `.git`)."""
    posix = PurePosixPath(path)
    if (
        not path
        or "\\" in path
        or posix.is_absolute()
        or ".." in posix.parts
        or posix.parts[0] == ".git"
    ):
        raise SubjectError(f"{what}: unsafe repo-relative path {path!r}")
    return posix.as_posix()


def resolve_inside(base: Path, rel: str, *, what: str) -> Path:
    """`base / rel` with *rel* validated and its parent confirmed to stay inside *base*
    (a symlinked directory in the checkout must not redirect a write outside it)."""
    target = base / safe_relpath(rel, what=what)
    base_resolved = base.resolve()
    parent = target.parent.resolve()
    if parent != base_resolved and base_resolved not in parent.parents:
        raise SubjectError(f"{what}: path {rel!r} escapes {base}")
    return target


def checkout_pinned(repo: str, commit: str, repo_dir: Path, *, provider: str) -> str:
    """Check *repo* out at exactly *commit* into *repo_dir* via the shared repo cache.

    *repo* is `owner/name`, a GitHub URL or any other clone URL/absolute path. The cache is
    cloned once; if it predates *commit* (a fresh PR, a re-pinned recipe) it is fetched
    before the checkout. Returns the full sha HEAD landed on (== *commit*).
    """
    if not _SHA_RE.match(commit):
        raise SubjectError(f"{provider} provider: commit {commit!r} must be a full 40-hex sha")
    cache_dir = _ensure_repo_cache(repo, provider=provider)
    _ensure_commit(cache_dir, commit, provider=provider)
    _checkout_into(cache_dir, commit, repo_dir, provider=provider)
    head = _run_git(["rev-parse", "HEAD"], cwd=repo_dir, provider=provider).strip()
    if head != commit:
        raise SubjectError(f"{provider} provider: checkout landed on {head!r}, expected {commit!r}")
    return head


def _has_commit(cache_dir: Path, commit: str) -> bool:
    result = subprocess.run(  # noqa: S603 -- fixed argv, no shell
        ["git", "-C", str(cache_dir), "cat-file", "-e", f"{commit}^{{commit}}"],
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


def _ensure_commit(cache_dir: Path, commit: str, *, provider: str) -> None:
    """Fetch into the shared cache iff it does not already contain *commit*."""
    if _has_commit(cache_dir, commit):
        return
    with _cache_lock:
        if _has_commit(cache_dir, commit):
            return
        _run_git(
            ["fetch", "--quiet", "origin"],
            cwd=cache_dir,
            timeout=_GIT_CLONE_TIMEOUT_SECONDS,
            provider=provider,
        )
        if not _has_commit(cache_dir, commit):
            # Some commits are reachable only via a non-default ref; ask for it by sha.
            _run_git(
                ["fetch", "--quiet", "origin", commit],
                cwd=cache_dir,
                timeout=_GIT_CLONE_TIMEOUT_SECONDS,
                provider=provider,
            )
    if not _has_commit(cache_dir, commit):
        raise SubjectError(f"{provider} provider: commit {commit} not found in {cache_dir}")


def read_blob(repo_dir: Path, commit: str, path: str, *, provider: str) -> bytes:
    """Bytes of *path* as of *commit* (`git show <commit>:<path>`); SubjectError if absent."""
    result = subprocess.run(  # noqa: S603 -- fixed argv, no shell
        ["git", "-C", str(repo_dir), "show", f"{commit}:{path}"],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise SubjectError(
            f"{provider} provider: `git show {commit}:{path}` failed: "
            f"{result.stderr.decode(errors='replace').strip()}"
        )
    return result.stdout


def snapshot_single_commit(repo_dir: Path, *, provider: str) -> str:
    """Replace *repo_dir*'s `.git` with a fresh repo holding ONE parentless commit of the
    current working tree (module docstring: why). Returns that commit's sha -- the
    baseline the grader diffs against. Call after every working-tree mutation, before the
    runner copies INSTRUCTION.md / the parity bundle in.
    """
    shutil.rmtree(repo_dir / ".git")
    _run_git(["init", "--quiet"], cwd=repo_dir, provider=provider)
    # --force: tracked-but-gitignored upstream files must land in the snapshot too.
    _run_git(["add", "--all", "--force"], cwd=repo_dir, provider=provider)
    _run_git(
        [*_SNAPSHOT_GIT_CONFIG, "commit", "--quiet", "--allow-empty", "-m", _SNAPSHOT_MESSAGE],
        cwd=repo_dir,
        provider=provider,
    )
    sha = _run_git(["rev-parse", "HEAD"], cwd=repo_dir, provider=provider).strip()
    _exclude_instruction_file(repo_dir)
    return sha


def write_hidden(grading_dir: Path, rel: str, data: bytes) -> None:
    """Park *data* at `grading_dir/hidden/<rel>` (restored into the repo at grade time)."""
    dest = resolve_inside(grading_dir / HIDDEN_DIRNAME, rel, what="hidden path")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)


def write_resolved(grading_dir: Path, resolved: dict[str, Any]) -> Path:
    """Write the provider -> grader contract file (`recipe.resolved.json`)."""
    path = grading_dir / RESOLVED_RECIPE_FILENAME
    path.write_text(json.dumps(resolved, indent=2, sort_keys=True) + "\n")
    return path
