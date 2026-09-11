"""Shared fixtures for `tests/isolation/` (E-Wk9Tz3 T-Gt4Pw8).

`make_repo`/`make_conflict_repo`/`make_hooked_repo` are a **locked interface** (TASK.md
AC-23): later isolation tasks (`T-Wk3Nv6`, `T-Ib5Qy9`, `T-Rm2Lx7`, ...) import these
directly rather than re-deriving their own real-git fixtures. Their names and return
shapes must not change without updating every importer.

Every git invocation these helpers make pins `-c user.name`/`-c user.email`,
`core.autocrlf=false`, and fixed `GIT_AUTHOR_DATE`/`GIT_COMMITTER_DATE` so commit hashes
are stable wherever a test asserts on one (AC-23, AC-17's commit-tree determinism test).

The autouse `_isolated_git_env` fixture keeps every test in this package off the real
`~`/`$AO_STATE_DIR`: `HOME` and `AO_STATE_DIR` are redirected under `tmp_path` before any
`GitRepo` is constructed (its `EMPTY_HOOKS_DIR` resolution reads `AO_STATE_DIR` fresh at
construction time -- see `isolation/git.py`'s module docstring "Design note").
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

GIT_AUTHOR_NAME = "ao-test"
GIT_AUTHOR_EMAIL = "ao-test@example.invalid"
_FIXED_DATE = "2020-01-01T00:00:00Z"

# Kinds `make_conflict_repo` understands that produce a REAL git conflict when rebased
# (locked, AC-23, plus additive "lock" -- T-Ee3Mn8 AC-2's `mechanical_lock`). Appended,
# not inserted; every existing kind's branch-building logic below is untouched, so no
# importer of the original six is affected. Kept conflict-only (deliberately) because at
# least one existing importer (`tests/isolation/test_integrator.py`'s own
# `_CONFLICTING_KINDS = [k for k in CONFLICT_KINDS if k != "clean"]`) assumes every
# non-"clean" member of this tuple genuinely conflicts -- "semantic" (below) does NOT
# (by design) and is deliberately kept OUT of this tuple so that assumption stays true.
CONFLICT_KINDS = (
    "clean",
    "union",
    "true_conflict",
    "add_add",
    "delete_modify",
    "binary",
    "lock",
)

# Additive (T-Ee3Mn8 AC-2): a `make_conflict_repo` kind that produces NO git conflict at
# all by design (disjoint files, clean rebase) -- see `semantic_verify_argv()` below.
# Deliberately NOT in `CONFLICT_KINDS` (see that tuple's own docstring comment).
SEMANTIC_KIND = "semantic"


@pytest.fixture(autouse=True)
def _isolated_git_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No real `~`, no real `$AO_STATE_DIR` (TASK.md AC-24): every `GitRepo` constructed
    in a test resolves its `EMPTY_HOOKS_DIR` under this test's own `tmp_path`.
    """
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("AO_STATE_DIR", str(tmp_path / "ao-state"))
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.delenv("GIT_CONFIG_GLOBAL", raising=False)
    monkeypatch.delenv("GIT_CONFIG_SYSTEM", raising=False)


def _commit_env() -> dict[str, str]:
    return {
        "GIT_AUTHOR_NAME": GIT_AUTHOR_NAME,
        "GIT_AUTHOR_EMAIL": GIT_AUTHOR_EMAIL,
        "GIT_COMMITTER_NAME": GIT_AUTHOR_NAME,
        "GIT_COMMITTER_EMAIL": GIT_AUTHOR_EMAIL,
        "GIT_AUTHOR_DATE": _FIXED_DATE,
        "GIT_COMMITTER_DATE": _FIXED_DATE,
    }


def _git(args: list[str], cwd: Path, env: dict[str, str] | None = None) -> str:
    """Raw (non-`GitRepo`) git invocation used only to BUILD fixtures -- never the thing
    under test. Pins identity/autocrlf per-invocation, same as this repo's existing
    `tests/bench/test_swebench_provider.py::_git` precedent.
    """
    full_env = {**os.environ, **(env or {})}
    result = subprocess.run(
        [
            "git",
            "-c",
            f"user.name={GIT_AUTHOR_NAME}",
            "-c",
            f"user.email={GIT_AUTHOR_EMAIL}",
            "-c",
            "core.autocrlf=false",
            *args,
        ],
        cwd=cwd,
        capture_output=True,
        text=True,
        env=full_env,
        check=False,
    )
    assert result.returncode == 0, f"git {args} failed (exit {result.returncode}): {result.stderr}"
    return result.stdout


def make_repo(tmp_path: Path, files: dict[str, str] | None = None) -> Path:
    """A real `git init` repo under *tmp_path* with one commit containing *files*
    (default: a single `f.txt`). Returns the repo's toplevel path.
    """
    repo = tmp_path / f"repo-{uuid.uuid4().hex[:8]}"
    repo.mkdir(parents=True)
    _git(["init", "-q", "-b", "main"], cwd=repo)
    for rel_path, content in (files or {"f.txt": "a\nb\nc\n"}).items():
        full = repo / rel_path
        full.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            full.write_bytes(content)
        else:
            full.write_text(content)
    _git(["add", "-A"], cwd=repo)
    _git(["commit", "-q", "-m", "base"], cwd=repo, env=_commit_env())
    return repo


def make_conflict_repo(tmp_path: Path, kind: str) -> tuple[Path, str, str, str]:
    """A repo with two branches (`ours`/`theirs`) diverging from a common base, shaped to
    produce exactly one conflict *kind* when one is rebased onto the other:

    - ``clean``: disjoint hunks in the same file -- rebases without conflict.
    - ``union``: both branches append a *different* line at the same position (an
      append-only-registration shape) -- conflicts, but a union merge legitimately keeps
      both.
    - ``true_conflict``: both branches rewrite the *same* line to contradictory content --
      conflicts, and a union merge would be semantically wrong (mixes half of each edit).
    - ``add_add``: both branches add a new file of the same name with different content.
    - ``delete_modify``: one branch deletes a file the other modifies.
    - ``binary``: both branches modify the same binary file differently.

    Returns ``(repo, base_sha, ours_branch, theirs_branch)``; leaves ``main`` checked out.
    """
    if kind not in CONFLICT_KINDS and kind != SEMANTIC_KIND:
        raise ValueError(
            f"unknown conflict kind: {kind!r} (expected one of {(*CONFLICT_KINDS, SEMANTIC_KIND)})"
        )

    repo = make_repo(tmp_path, files={"f.txt": "a\nb\nc\n"})
    ours_branch, theirs_branch = "ours", "theirs"

    if kind == "binary":
        (repo / "bin.dat").write_bytes(bytes(range(16)))
        _git(["add", "-A"], cwd=repo)
        _git(["commit", "-q", "-m", "add base binary"], cwd=repo, env=_commit_env())
    elif kind == "lock":
        (repo / "lock.json").write_text('{\n  "a": 1,\n  "b": 2\n}\n')
        _git(["add", "-A"], cwd=repo)
        _git(["commit", "-q", "-m", "add base lockfile"], cwd=repo, env=_commit_env())
    elif kind == SEMANTIC_KIND:
        (repo / "a.py").write_text("A_ENABLED = False\n")
        (repo / "b.py").write_text("B_ENABLED = False\n")
        _git(["add", "-A"], cwd=repo)
        _git(["commit", "-q", "-m", "add semantic base files"], cwd=repo, env=_commit_env())

    base_sha = _git(["rev-parse", "HEAD"], cwd=repo).strip()

    def _commit(message: str) -> None:
        _git(["add", "-A"], cwd=repo)
        _git(["commit", "-q", "-m", message], cwd=repo, env=_commit_env())

    def _branch_from_main(name: str) -> None:
        _git(["checkout", "-q", "main"], cwd=repo)
        _git(["checkout", "-q", "-b", name], cwd=repo)

    if kind == "clean":
        _branch_from_main(ours_branch)
        (repo / "f.txt").write_text("a-ours\nb\nc\n")
        _commit("ours: edit line 1")
        _branch_from_main(theirs_branch)
        (repo / "f.txt").write_text("a\nb\nc-theirs\n")
        _commit("theirs: edit line 3")
    elif kind == "true_conflict":
        _branch_from_main(ours_branch)
        (repo / "f.txt").write_text("a-ours\nb\nc\n")
        _commit("ours: edit line 1")
        _branch_from_main(theirs_branch)
        (repo / "f.txt").write_text("a-theirs\nb\nc\n")
        _commit("theirs: edit line 1 differently")
    elif kind == "union":
        _branch_from_main(ours_branch)
        (repo / "f.txt").write_text("a\nb\nc\nours-entry\n")
        _commit("ours: append entry")
        _branch_from_main(theirs_branch)
        (repo / "f.txt").write_text("a\nb\nc\ntheirs-entry\n")
        _commit("theirs: append entry")
    elif kind == "add_add":
        _branch_from_main(ours_branch)
        (repo / "new.txt").write_text("ours-content\n")
        _commit("ours: add new.txt")
        _branch_from_main(theirs_branch)
        (repo / "new.txt").write_text("theirs-content\n")
        _commit("theirs: add new.txt")
    elif kind == "delete_modify":
        _branch_from_main(ours_branch)
        (repo / "f.txt").unlink()
        _commit("ours: delete f.txt")
        _branch_from_main(theirs_branch)
        (repo / "f.txt").write_text("a\nb\nc-theirs\n")
        _commit("theirs: modify f.txt")
    elif kind == "binary":
        _branch_from_main(ours_branch)
        (repo / "bin.dat").write_bytes(bytes(range(16, 32)))
        _commit("ours: modify binary")
        _branch_from_main(theirs_branch)
        (repo / "bin.dat").write_bytes(bytes(range(32, 48)))
        _commit("theirs: modify binary")
    elif kind == "lock":
        # Non-additive: both branches overwrite the SAME key -- a lockfile-regeneration
        # shape (`ResolverConfig.regenerate`'s intended target), deliberately NOT
        # union-resolvable (union only ever accepts a purely-additive hunk, HLD §11 M6).
        _branch_from_main(ours_branch)
        (repo / "lock.json").write_text('{\n  "a": 3,\n  "b": 2\n}\n')
        _commit("ours: bump a to 3")
        _branch_from_main(theirs_branch)
        (repo / "lock.json").write_text('{\n  "a": 4,\n  "b": 2\n}\n')
        _commit("theirs: bump a to 4")
    elif kind == SEMANTIC_KIND:
        # Disjoint files -- rebases cleanly, no git conflict at all. Pair with
        # `semantic_verify_argv()` (below): the two INDIVIDUALLY-clean changes only break
        # a verify check once BOTH have landed together.
        _branch_from_main(ours_branch)
        (repo / "a.py").write_text("A_ENABLED = True\n")
        _commit("ours: enable A")
        _branch_from_main(theirs_branch)
        (repo / "b.py").write_text("B_ENABLED = True\n")
        _commit("theirs: enable B")

    _git(["checkout", "-q", "main"], cwd=repo)
    return repo, base_sha, ours_branch, theirs_branch


def semantic_verify_argv() -> list[str]:
    """Argv for a `verify_command`-shaped check paired with `make_conflict_repo`'s
    ``"semantic"`` kind (T-Ee3Mn8 AC-2): reads ``a.py``/``b.py`` from the CURRENT working
    directory and exits 1 (fail) only when BOTH ``A_ENABLED`` and ``B_ENABLED`` read
    ``True`` -- i.e. only once both branches' individually-clean, individually-passing
    changes have landed together. Exits 0 (pass) for either alone, or neither.
    """
    script = (
        "import pathlib, sys\n"
        "a = pathlib.Path('a.py').read_text() if pathlib.Path('a.py').exists() else ''\n"
        "b = pathlib.Path('b.py').read_text() if pathlib.Path('b.py').exists() else ''\n"
        "sys.exit(1 if ('True' in a and 'True' in b) else 0)\n"
    )
    return [sys.executable, "-c", script]


def make_rerere_taught_repo(tmp_path: Path) -> tuple[Path, str, str, str]:
    """A repo shaped exactly like `make_conflict_repo`'s ``"true_conflict"`` kind, with
    git's rerere cache PRE-TAUGHT the resolution via a throwaway branch that is rebased,
    resolved, and COMPLETED (never aborted -- rerere only remembers a completed
    resolution) before being deleted -- T-Ee3Mn8 AC-2's ``rerere_repeat`` kind.

    A caller driving the returned ``ours``/``theirs`` rebase with rerere enabled (``-c
    rerere.enabled=true -c rerere.autoupdate=true``, the exact flags `isolation/git.py`'s
    `RERERE_ARGS` always passes) sees git auto-replay the taught resolution and stage it
    without ever stopping; the IDENTICAL rebase with rerere disabled stops with a genuine,
    unresolved conflict -- both outcomes are what this fixture's own quality test proves
    (`tests/isolation/test_conflict_fixtures.py`).

    Returns ``(repo, base_sha, ours_branch, theirs_branch)`` -- same shape as
    `make_conflict_repo`; the rerere cache is a side effect already present in
    ``repo/.git/rr-cache`` by the time this returns. Never reuses `make_conflict_repo`
    itself: teaching needs to drive an actual conflicting rebase to completion, which that
    function's raw-file-write branch builders don't do.
    """
    repo = make_repo(tmp_path, files={"f.txt": "a\nb\nc\n"})
    base_sha = _git(["rev-parse", "HEAD"], cwd=repo).strip()
    ours_branch, theirs_branch = "ours", "theirs"

    def _commit(message: str) -> None:
        _git(["add", "-A"], cwd=repo)
        _git(["commit", "-q", "-m", message], cwd=repo, env=_commit_env())

    def _branch_from(name: str, start: str) -> None:
        _git(["checkout", "-q", start], cwd=repo)
        _git(["checkout", "-q", "-b", name], cwd=repo)

    _branch_from(ours_branch, "main")
    (repo / "f.txt").write_text("a-ours\nb\nc\n")
    _commit("ours: edit line 1")
    _branch_from(theirs_branch, "main")
    (repo / "f.txt").write_text("a-theirs\nb\nc\n")
    _commit("theirs: edit line 1 differently")

    # Teach: a throwaway branch off `ours`, rebased onto `theirs` WITH rerere enabled,
    # resolved by hand, and completed (raw subprocess -- `_git` asserts returncode == 0,
    # which the intentionally-conflicting rebase call must NOT satisfy).
    rerere_cfg = ["-c", "rerere.enabled=true", "-c", "rerere.autoupdate=true"]
    _branch_from("teach", ours_branch)
    conflict = subprocess.run(
        ["git", *rerere_cfg, "rebase", theirs_branch], cwd=repo, capture_output=True, text=True
    )
    assert conflict.returncode != 0, "teach rebase should conflict (fixture is broken)"
    (repo / "f.txt").write_text("a-resolved\nb\nc\n")
    _git([*rerere_cfg, "add", "f.txt"], cwd=repo)
    finish = subprocess.run(
        ["git", *rerere_cfg, "rebase", "--continue"],
        cwd=repo,
        capture_output=True,
        text=True,
        env={**os.environ, **_commit_env()},
    )
    assert finish.returncode == 0, f"teach rebase --continue failed: {finish.stderr}"
    _git(["checkout", "-q", "main"], cwd=repo)
    _git(["branch", "-D", "teach"], cwd=repo)

    return repo, base_sha, ours_branch, theirs_branch


# Hooks planted by `make_hooked_repo` (TASK.md AC-5 / the S-1 gate). `post-commit`'s exit
# code is ignored by git itself (it runs after the commit already succeeded), so it is
# the one made to exit non-zero -- proving a failing hook doesn't get to veto anything,
# without also blocking the raw-subprocess non-vacuousness proof (a failing `pre-commit`
# would abort that proof's own commit before `commit-msg`/`post-commit` ever ran).
HOOK_NAMES = (
    "pre-commit",
    "post-checkout",
    "commit-msg",
    "post-commit",
    "post-rewrite",
    "pre-rebase",
)
_FAILING_HOOK = "post-commit"


def make_hooked_repo(tmp_path: Path) -> Path:
    """A real repo whose `.git/hooks/` has all of `HOOK_NAMES` installed, each writing a
    distinct sentinel file under `<repo>/.hook-sentinels/<hook-name>` when it fires.
    """
    repo = make_repo(tmp_path, files={"f.txt": "a\nb\nc\n"})
    hooks_dir = repo / ".git" / "hooks"
    sentinel_dir = repo / ".hook-sentinels"
    for name in HOOK_NAMES:
        exit_code = 1 if name == _FAILING_HOOK else 0
        script = hooks_dir / name
        script.write_text(
            "#!/bin/sh\n"
            f"mkdir -p '{sentinel_dir}'\n"
            f"touch '{sentinel_dir}/{name}'\n"
            f"exit {exit_code}\n"
        )
        script.chmod(0o755)
    return repo


class RecordingFakeRunner:
    """Test double for `agent_orchestrator.isolation.git.Runner` (TASK.md AC-2): records
    every invoked argv/cwd/env/timeout for later assertion and returns (or raises) a
    scripted response per call, FIFO, falling back to a canned success once the queue is
    empty. This is the pattern downstream tasks are expected to reuse to unit-test their
    own `GitRepo` usage without real git.
    """

    def __init__(
        self,
        responses: list[subprocess.CompletedProcess | BaseException] | None = None,
        default: subprocess.CompletedProcess | None = None,
    ) -> None:
        self.calls: list[dict[str, object]] = []
        self._responses = list(responses) if responses is not None else []
        self._default = (
            default
            if default is not None
            else subprocess.CompletedProcess(args=[], returncode=0, stdout=b"", stderr=b"")
        )

    def __call__(
        self, argv: list[str], *, cwd: str, env: dict[str, str] | None, timeout: float
    ) -> subprocess.CompletedProcess:
        self.calls.append({"argv": list(argv), "cwd": cwd, "env": env, "timeout": timeout})
        item = self._responses.pop(0) if self._responses else self._default
        if isinstance(item, BaseException):
            raise item
        return item

    def argvs(self) -> list[list[str]]:
        return [call["argv"] for call in self.calls]  # type: ignore[misc]


def ok(
    stdout: bytes = b"", stderr: bytes = b"", returncode: int = 0
) -> subprocess.CompletedProcess:
    """Shorthand for a scripted success/failure `CompletedProcess` fed to
    `RecordingFakeRunner`."""
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)
