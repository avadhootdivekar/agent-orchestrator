"""`GitRepo` -- the single, typed, timeout-bounded, exception-safe surface for every git
call the engine issues (E-Wk9Tz3 T-Gt4Pw8, HLD §11 M1).

Nothing else in this codebase shells out to git for isolation work; everything downstream
in the epic (worktree lifecycle, the integrator, mechanical resolvers, ...) calls into
this module instead of `subprocess`. Because it is the single choke point, it also owns
three cross-cutting guarantees the rest of the epic simply inherits (docs-md/task-
isolation-hld.md §11 M1, §14):

1. **No repo-local hook ever fires from an engine-issued git call (S-1).** Every
   invocation built by `_run` sets `core.hooksPath` to an always-empty directory (never
   `/dev/null` -- Windows portability), plus `commit.gpgsign=false` / `core.editor=true`
   so the engine can never block on a passphrase, editor, or credential prompt. All via
   per-invocation `-c`/env -- nothing is ever written to the repo's or the user's git
   config.
2. **No network operation, ever.** Enforced by construction (no clone/fetch/push/pull/
   remote method exists on `GitRepo`) AND by a runtime guard: `_run` raises
   `GitForbiddenCommandError` for any `FORBIDDEN_SUBCOMMANDS` verb, before a subprocess is
   ever spawned.
3. **Pruning and removal are scoped and tolerant.** `prune_worktrees_scoped` (R-6) never
   deregisters a worktree ao did not create; `worktree_remove`/`delete_branch` (R-23)
   return outcomes instead of raising for the "nothing to do" cases, so a lifecycle's
   best-effort `release()` can run on a failure path without its own error being masked.

Design note on `EMPTY_HOOKS_DIR` (per the architect's Phase-2 interface routing, recorded
for the downstream tasks that read this module rather than the ticket): the HLD sketches
it as a value baked into `SAFETY_ARGS` at import time, computed by `isolation/paths.py`'s
`state_dir()` -- but that module is `T-Wk3Nv6`'s, not yet landed, and this module must not
depend on it (HLD §11: M1 has zero dependency on M3). Resolution is instead: (1) a new,
shared `xdg.py` (`resolve_state_dir`, generalized from `service/paths.py`'s own
`default_state_dir()` precedent, which is left untouched -- `T-Wk3Nv6` refactors it onto
this helper later) resolves `$AO_STATE_DIR` > `$XDG_STATE_HOME/ao` > `~/.local/state/ao`;
(2) `resolve_empty_hooks_dir()` joins on `empty-hooks`, creates it (mode 0700) and asserts
it is empty; (3) `GitRepo.__init__` takes an explicit `hooks_dir: Path | None = None`
override so tests inject a `tmp_path`-scoped directory directly, never touching the real
home dir or relying on env monkeypatching. `SAFETY_ARGS` remains a true module constant
for the env-independent flags; the hooks-path `-c` argument is composed alongside it in
`_run` from each instance's resolved directory. This does not change any locked name,
signature, or exception -- only how one non-locked constant's value is produced.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Literal, NoReturn, Protocol

from ..errors import GitError, GitForbiddenCommandError, GitTimeoutError, GitUnavailableError
from ..xdg import resolve_state_dir

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------------------
# Constants (AC-1: named, never literals at call sites)
# ---------------------------------------------------------------------------------------

GIT_DEFAULT_TIMEOUT_SECONDS = 300
# worktree add/remove/prune, update-ref CAS, core.hooksPath (honoured since 2.9+). NOT
# enforced anywhere in THIS module by design (AC-10: `version()` is a never-raising
# probe) -- a caller that requires the minimum compares its own result against this and
# decides whether to degrade or raise (C-11 review note: that caller is
# `WorktreeManager._activate_integration`, `isolation/worktrees.py`, `T-Wk3Nv6`).
GIT_MIN_VERSION = (2, 30)
GIT_MERGE_TREE_MIN_VERSION = (2, 38)  # `merge-tree --write-tree` (optional probe)

# `stderr_tail` truncation bound on every `GitError` (AC-3).
GIT_STDERR_TAIL_BYTES = 4096  # 4 KiB

# `log_name_only`'s own defense-in-depth cap (review C-1): `--since` already bounds a
# windowed query, but an unwindowed one (`since=None`, e.g. `ao hotspots --since-days 0`)
# has no bound at all otherwise -- this keeps every invocation, windowed or not, capped
# at a generous but finite number of commits rather than ever walking a repo's entire
# history unbounded.
LOG_NAME_ONLY_MAX_COMMITS = 10000

# Per-invocation `-c` flags applied to EVERY engine-issued call (S-1). The engine never
# runs `git config`, so nothing here ever persists into the repo's or user's config.
SAFETY_ARGS: list[str] = [
    "-c",
    "commit.gpgsign=false",  # never block on a GPG passphrase prompt
    "-c",
    "core.editor=true",  # never block on an editor
    "-c",
    "gc.auto=0",  # no surprise gc mid-integration
]
# Enabled per-invocation when `rerere=True` (the default) -- a resolution recorded once in
# this repo is replayed for free forever after; never written to the user's git config.
RERERE_ARGS: list[str] = ["-c", "rerere.enabled=true", "-c", "rerere.autoupdate=true"]

# S-1 / network confinement: the engine's porcelain performs NO network operation, ever.
# Enforced by construction (no clone/fetch/push/pull/remote method exists) AND by this
# runtime guard.
FORBIDDEN_SUBCOMMANDS: set[str] = {
    "push",
    "fetch",
    "pull",
    "clone",
    "remote",
    "submodule",
    "request-pull",
    "send-email",
    "svn",
    "p4",
    "daemon",
    "credential",
}

# Child-process env forced on every engine-issued invocation (S-1): never a hook editor,
# passphrase, or credential prompt; always the C locale so porcelain output parses
# deterministically regardless of the operator's own locale (never parse human-readable
# messages -- see module docstring / HLD Risks).
_FORCED_ENV: dict[str, str] = {
    "LC_ALL": "C",
    "GIT_EDITOR": "true",
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_ASKPASS": "",
}

# EMPTY_HOOKS_DIR resolution (see module docstring "Design note"): `resolve_state_dir`
# precedence is `$AO_STATE_DIR` (exact dir) > `$XDG_STATE_HOME/ao` > `~/.local/state/ao`.
_STATE_DIR_ENV = "AO_STATE_DIR"
_STATE_DIR_SUBDIR = "ao"
_EMPTY_HOOKS_DIRNAME = "empty-hooks"
_EMPTY_HOOKS_DIR_MODE = 0o700

_GIT_DIR_SUFFIX = "/.git"

# `commit_tree`'s default identity (C-1 review fix): used only when a caller does not
# inject its own via the method's `author_name`/`author_email` params.
_COMMIT_TREE_DEFAULT_NAME = "ao"
_COMMIT_TREE_DEFAULT_EMAIL = "ao@localhost"

# `grep_conflict_markers` (E-Wk9Tz3 T-Ib5Qy9 review C-3): the exact literal patterns the
# default structural verify check looks for -- a real match against a leftover conflict-
# marker line, never a partial/regex-fuzzy one.
_CONFLICT_MARKER_PATTERNS: tuple[str, str] = ("^<<<<<<< ", "^>>>>>>> ")

# `diff_check` (review C-3): git's standard fatal-error exit code is 128 -- any `git diff
# --check` exit below this (verified empirically: 2 for a real check failure, e.g.
# trailing whitespace or a leftover conflict marker -- NOT 1) means "issues found", not
# "the tool itself failed".
_DIFF_CHECK_FATAL_THRESHOLD: int = 128


# ---------------------------------------------------------------------------------------
# Injectable runner (AC-2)
# ---------------------------------------------------------------------------------------


class Runner(Protocol):
    """Injectable subprocess boundary. Bytes in/out (some git blobs are binary --
    `show_stage` -- so this layer never assumes text)."""

    def __call__(
        self, argv: list[str], *, cwd: str, env: dict[str, str] | None, timeout: float
    ) -> subprocess.CompletedProcess[bytes]: ...


class _SubprocessRunner:
    """Default `Runner`: a real `subprocess.run`, bytes captured, never a shell."""

    def __call__(
        self, argv: list[str], *, cwd: str, env: dict[str, str] | None, timeout: float
    ) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(  # noqa: S603 -- fixed argv built by this module, no shell
            argv,
            cwd=cwd,
            env=env,
            timeout=timeout,
            capture_output=True,
            check=False,
        )


_DEFAULT_RUNNER: Runner = _SubprocessRunner()


# ---------------------------------------------------------------------------------------
# Typed results (locked shapes)
# ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class RepoProbe:
    toplevel: str
    common_dir: str
    bare: bool
    is_worktree: bool


@dataclass(frozen=True)
class WorktreeEntry:
    path: str
    head: str | None
    branch: str | None
    bare: bool
    detached: bool
    locked: bool
    prunable: bool
    admin_dir: str | None  # $GIT_COMMON_DIR/worktrees/<id>; None for the main worktree


WorktreeRemoveOutcome = Literal["removed", "already_absent", "locked", "in_use"]


@dataclass(frozen=True)
class PruneReport:
    mode: Literal["scoped", "global"]
    pruned: list[str] = field(default_factory=list)
    skipped_foreign: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class StatusEntry:
    path: str
    index: str  # single-char index (staged) status code
    worktree: str  # single-char worktree (unstaged) status code


@dataclass(frozen=True)
class RebaseOutcome:
    clean: bool
    paths: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class MergeProbe:
    clean: bool
    paths: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------------------
# Small pure helpers
# ---------------------------------------------------------------------------------------


def _decode(raw: bytes) -> str:
    return raw.decode("utf-8", errors="replace")


def _tail(text: str) -> str:
    """Truncate to the last `GIT_STDERR_TAIL_BYTES` bytes (AC-3), UTF-8 safe."""
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= GIT_STDERR_TAIL_BYTES:
        return text
    return encoded[-GIT_STDERR_TAIL_BYTES:].decode("utf-8", errors="replace")


# Global options `_find_subcommand` knows how to skip over -- this module never issues
# any of these itself, but a forbidden verb could otherwise hide behind one (C-8).
_GLOBAL_OPTIONS_WITH_VALUE = ("-c", "-C", "--git-dir")


def _find_subcommand(args: list[str]) -> str | None:
    """Find the actual git subcommand token in *args*, skipping recognized global
    options and their values (`-c key=value`, `-C <path>`, `--git-dir <path>` or
    `--git-dir=<path>`) -- git's own parsing rule is "the first non-option token after
    global options". `_run`'s forbidden-verb guard checking only `args[0]` would miss a
    forbidden verb placed after such an option (C-8 review fix); this generalizes the
    check without re-scanning every later positional argument (which would risk a false
    positive on, e.g., a branch name that happens to equal a forbidden verb).
    Returns None if *args* contains no subcommand token (e.g. only global options).
    """
    i = 0
    while i < len(args):
        tok = args[i]
        if tok in _GLOBAL_OPTIONS_WITH_VALUE:
            i += 2  # skip the option AND its value
            continue
        if tok.startswith("--git-dir="):
            i += 1
            continue
        if tok.startswith("-"):
            i += 1  # some other flag -- not a subcommand token
            continue
        return tok
    return None


def _forbidden_alias_key(args: list[str]) -> str | None:
    """Return the first `-c alias.<name>=...` key found in *args*, or None (C-8). Any
    `-c` defining a git alias is refused wholesale, regardless of its value: an alias
    can redefine what a later bare token invokes (`-c alias.p=push` then a bare `p`),
    which `_find_subcommand`'s subcommand-position check cannot see through without
    itself parsing arbitrary alias values (which can be shell-like and unparseable).
    """
    for i, tok in enumerate(args):
        if tok == "-c" and i + 1 < len(args):
            key = args[i + 1].split("=", 1)[0]
            if key.startswith("alias."):
                return key
    return None


def _resolve_abs(value: str, cwd: str) -> str:
    """git sometimes prints a path relative to the invocation cwd (e.g. `--git-common-dir`
    in the main worktree prints `.git`) and sometimes absolute (the same flag in a linked
    worktree) -- verified empirically across both cases. Normalizing here is version-safe
    (works from git 2.30 onward) without depending on `--path-format` (2.31+).
    """
    if os.path.isabs(value):
        return os.path.normpath(value)
    return os.path.normpath(os.path.join(cwd, value))


_VERSION_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")


def _parse_git_version(text: str) -> tuple[int, int, int] | None:
    match = _VERSION_RE.search(text)
    if not match:
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def resolve_empty_hooks_dir(hooks_dir: Path | None = None) -> Path:
    """Resolve (and idempotently create, mode 0700) the always-empty directory `_run`
    points `core.hooksPath` at (S-1). A directory, not `/dev/null`, for Windows
    portability. Asserted empty on every call: nothing in this codebase ever writes into
    it, so a non-empty result means the no-hooks guarantee cannot be trusted.

    *hooks_dir* overrides resolution entirely -- `GitRepo.__init__` threads its own
    `hooks_dir` constructor param through to here so tests can inject a `tmp_path`-scoped
    directory directly, without touching the real home dir or monkeypatching env vars.
    When omitted, resolves via `xdg.resolve_state_dir("AO_STATE_DIR", "ao", "ao") /
    "empty-hooks"` (`$AO_STATE_DIR` > `$XDG_STATE_HOME/ao` > `~/.local/state/ao`), read
    fresh on every call -- never cached -- mirroring `service/paths.py`'s own convention
    for its (unrelated) state directory.
    """
    target = (
        hooks_dir
        if hooks_dir is not None
        else resolve_state_dir(_STATE_DIR_ENV, _STATE_DIR_SUBDIR, _STATE_DIR_SUBDIR)
        / _EMPTY_HOOKS_DIRNAME
    )
    target.mkdir(parents=True, exist_ok=True)
    os.chmod(target, _EMPTY_HOOKS_DIR_MODE)
    if any(target.iterdir()):
        raise RuntimeError(
            f"{target} (the S-1 core.hooksPath target) is not empty -- refusing to "
            "proceed since the no-hooks guarantee could not be trusted. Remove its "
            "contents or point AO_STATE_DIR elsewhere."
        )
    return target


# ---------------------------------------------------------------------------------------
# Pure porcelain parser (AC-11)
# ---------------------------------------------------------------------------------------


def parse_worktree_list(text: str, common_dir: str) -> list[WorktreeEntry]:
    """Pure parser over `git worktree list --porcelain` output. Touches neither disk nor
    git; entries are separated by a blank line, each holding a fixed set of tag lines.

    `admin_dir` for every entry after the first (git always lists the main worktree
    first) is computed as `<common_dir>/worktrees/<basename(path)>` -- the common,
    no-collision case. This is a best-effort value: git disambiguates with a numeric
    suffix (`repoA`, `repoA1`, ...) when two linked worktrees share a basename (verified
    empirically), which this text-only parse cannot know. `GitRepo.worktree_list()` (the
    impure method) corrects it against the real `<common_dir>/worktrees/*/gitdir`
    records before it is used for anything destructive (`prune_worktrees_scoped`). The
    main worktree has no admin dir at all and always gets `admin_dir=None`.
    """
    entries: list[WorktreeEntry] = []
    path: str | None = None
    head: str | None = None
    branch: str | None = None
    bare = False
    detached = False
    locked = False
    prunable = False

    def _flush() -> None:
        nonlocal path, head, branch, bare, detached, locked, prunable
        if path is not None:
            is_main = len(entries) == 0
            admin_dir = None if is_main else str(Path(common_dir) / "worktrees" / Path(path).name)
            entries.append(
                WorktreeEntry(
                    path=path,
                    head=head,
                    branch=branch,
                    bare=bare,
                    detached=detached,
                    locked=locked,
                    prunable=prunable,
                    admin_dir=admin_dir,
                )
            )
        path = head = branch = None
        bare = detached = locked = prunable = False

    for line in text.splitlines():
        if line == "":
            _flush()
        elif line.startswith("worktree "):
            _flush()  # defensive: a well-formed block is already blank-line-terminated
            path = line[len("worktree ") :]
        elif line.startswith("HEAD "):
            head = line[len("HEAD ") :]
        elif line.startswith("branch "):
            branch = line[len("branch ") :]
        elif line == "bare":
            bare = True
        elif line == "detached":
            detached = True
        elif line.startswith("locked"):
            locked = True
        elif line.startswith("prunable"):
            prunable = True
        # Unknown lines (future porcelain fields) are ignored -- forward compatible.
    _flush()
    return entries


def _correct_admin_dirs(entries: list[WorktreeEntry], common_dir: str) -> list[WorktreeEntry]:
    """Correct `parse_worktree_list`'s best-effort `admin_dir` guesses against the real
    on-disk `<common_dir>/worktrees/*/gitdir` records (each holds the absolute path to
    `<worktree>/.git`, and survives even when the worktree's own directory is gone --
    exactly the `prunable` case `prune_worktrees_scoped` needs to get right). Falls back
    to the naive guess when no on-disk record matches (defensive; should not happen for
    an entry git itself just listed).
    """
    worktrees_root = Path(common_dir) / "worktrees"
    if not worktrees_root.is_dir():
        return entries
    path_to_admin: dict[str, str] = {}
    for admin_dir in worktrees_root.iterdir():
        try:
            recorded = (admin_dir / "gitdir").read_text().strip()
        except OSError:
            continue
        recorded_path = (
            recorded[: -len(_GIT_DIR_SUFFIX)] if recorded.endswith(_GIT_DIR_SUFFIX) else recorded
        )
        path_to_admin[os.path.normpath(recorded_path)] = str(admin_dir)

    corrected = []
    for entry in entries:
        if entry.admin_dir is None:
            corrected.append(entry)
            continue
        match = path_to_admin.get(os.path.normpath(entry.path))
        corrected.append(entry if match is None else replace(entry, admin_dir=match))
    return corrected


def _parse_status_porcelain_z(raw: bytes) -> list[StatusEntry]:
    """Parse `git status --porcelain -z` output. `-z` is used (never the human `-> `
    rename arrow) so a filename containing arbitrary bytes can never be misparsed --
    each record is `XY <path>\\0`, and a rename/copy record is followed by a second,
    bare `<orig-path>\\0` record that this function consumes and discards (StatusEntry's
    locked shape carries only the new path, per AC-19).
    """
    tokens = _decode(raw).split("\0")
    entries: list[StatusEntry] = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        i += 1
        if not token:
            continue
        index, worktree, path = token[0], token[1], token[3:]
        if index in ("R", "C") or worktree in ("R", "C"):
            i += 1  # skip the paired original-path record
        entries.append(StatusEntry(path=path, index=index, worktree=worktree))
    return entries


# ---------------------------------------------------------------------------------------
# GitRepo
# ---------------------------------------------------------------------------------------


class GitRepo:
    """One typed, timeout-bounded, exception-safe surface for every git call against a
    repo (HLD §11 M1). Constructed against the repo's main working tree (`path`); methods
    that act on a specific task worktree take an explicit `cwd` (refs/branches are shared
    via the common dir regardless of which worktree they are read from, so those methods
    take none).
    """

    def __init__(
        self,
        path: str,
        *,
        timeout: int = GIT_DEFAULT_TIMEOUT_SECONDS,
        rerere: bool = True,
        runner: Runner | None = None,
        env: dict[str, str] | None = None,
        hooks_dir: Path | None = None,
    ) -> None:
        self.path = path
        self.timeout = timeout
        self.rerere = rerere
        self.runner: Runner = runner if runner is not None else _DEFAULT_RUNNER
        self.env = env
        # `hooks_dir` overrides resolution entirely (tests inject a `tmp_path`-scoped
        # directory directly -- see `resolve_empty_hooks_dir`'s docstring).
        self._hooks_dir = resolve_empty_hooks_dir(hooks_dir)

    # --- low level ------------------------------------------------------------------

    def _run(
        self,
        args: list[str],
        *,
        cwd: str | None = None,
        check: bool = True,
        timeout: float | None = None,
        extra_env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        if not args:
            raise ValueError("GitRepo._run: args must be a non-empty subcommand list")
        # S-1 no-network guard (C-8 review fix: structural, not just `args[0]`). Every
        # current caller hardcodes a literal, non-forbidden subcommand as `args[0]`, so
        # this is defense-in-depth against a FUTURE internal caller (or a downstream
        # module calling `_run` directly, which Python does not prevent) smuggling a
        # forbidden verb past a naive `args[0]` check via leading global options
        # (`git -c foo=bar push origin` -> `args[0]` is `"-c"`, not `"push"`) or a git
        # alias (`-c alias.p=push` then a bare `p`). Both are rejected BEFORE argv is
        # built / any subprocess is spawned.
        alias_key = _forbidden_alias_key(args)
        if alias_key is not None:
            raise GitForbiddenCommandError(
                ["git", *args], None, f"forbidden -c {alias_key}=... (git alias definition)"
            )
        subcommand = _find_subcommand(args)
        if subcommand in FORBIDDEN_SUBCOMMANDS:
            raise GitForbiddenCommandError(
                ["git", *args], None, f"forbidden subcommand: {subcommand!r}"
            )

        argv = [
            "git",
            "--no-pager",
            "-c",
            f"core.hooksPath={self._hooks_dir}",
            *SAFETY_ARGS,
            *(RERERE_ARGS if self.rerere else []),
            *args,
        ]
        env = dict(self.env) if self.env is not None else dict(os.environ)
        env.update(_FORCED_ENV)
        if extra_env:
            env.update(extra_env)

        resolved_timeout = timeout if timeout is not None else self.timeout
        resolved_cwd = cwd if cwd is not None else self.path
        try:
            cp = self.runner(argv, cwd=resolved_cwd, env=env, timeout=resolved_timeout)
        except subprocess.TimeoutExpired as exc:
            raise GitTimeoutError(argv, None, _tail(str(exc))) from exc
        except OSError as exc:
            raise GitUnavailableError(argv, None, _tail(str(exc))) from exc

        if check and cp.returncode != 0:
            raise GitError(argv, cp.returncode, _tail(_decode(cp.stderr)))
        return cp

    def _raise(self, args: list[str], cp: subprocess.CompletedProcess[bytes]) -> NoReturn:
        """Shared `GitError` construction (C-5 review fix) for a call site that already
        ran with `check=False`, checked for its own specific tolerated outcomes (CAS
        loss, already-absent, locked, in-use, conflicted), and determined the remaining
        failure is genuinely unexpected. `args` is the bare subcommand (no leading
        `"git"` -- prefixed here for the error message, matching every other raise site).
        """
        raise GitError(["git", *args], cp.returncode, _tail(_decode(cp.stderr)))

    # --- probes (never raise; return None/False) -------------------------------------

    @staticmethod
    def version(runner: Runner | None = None) -> tuple[int, int, int] | None:
        r = runner if runner is not None else _DEFAULT_RUNNER
        try:
            cp = r(
                ["git", "--version"], cwd=os.getcwd(), env=None, timeout=GIT_DEFAULT_TIMEOUT_SECONDS
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if cp.returncode != 0:
            return None
        return _parse_git_version(_decode(cp.stdout))

    @staticmethod
    def probe(path: str, runner: Runner | None = None) -> RepoProbe | None:
        r = runner if runner is not None else _DEFAULT_RUNNER
        try:
            cp = r(
                [
                    "git",
                    "rev-parse",
                    "--is-inside-work-tree",
                    "--is-bare-repository",
                    "--git-common-dir",
                ],
                cwd=path,
                env=None,
                timeout=GIT_DEFAULT_TIMEOUT_SECONDS,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if cp.returncode != 0:
            return None
        lines = _decode(cp.stdout).splitlines()
        if len(lines) < 3:
            return None
        inside_work_tree = lines[0].strip() == "true"
        bare = lines[1].strip() == "true"
        common_dir = _resolve_abs(lines[2].strip(), path)

        if bare:
            toplevel = common_dir
        elif inside_work_tree:
            try:
                cp2 = r(
                    ["git", "rev-parse", "--show-toplevel"],
                    cwd=path,
                    env=None,
                    timeout=GIT_DEFAULT_TIMEOUT_SECONDS,
                )
            except (OSError, subprocess.TimeoutExpired):
                return None
            if cp2.returncode != 0:
                return None
            toplevel = _decode(cp2.stdout).strip()
        else:
            return None

        is_worktree = (not bare) and (
            os.path.normpath(os.path.join(toplevel, ".git")) != os.path.normpath(common_dir)
        )
        return RepoProbe(
            toplevel=toplevel, common_dir=common_dir, bare=bare, is_worktree=is_worktree
        )

    def is_dirty(self) -> bool:
        """Tracked changes only (untracked files do not count as "dirty")."""
        return len(self.status_porcelain(self.path, untracked=False)) > 0

    def current_branch(self) -> str | None:
        cp = self._run(["symbolic-ref", "--short", "-q", "HEAD"], check=False)
        if cp.returncode != 0:
            return None
        name = _decode(cp.stdout).strip()
        return name or None

    def rev_parse(self, ref: str) -> str | None:
        cp = self._run(["rev-parse", "--verify", "--quiet", ref], check=False)
        if cp.returncode != 0:
            return None
        sha = _decode(cp.stdout).strip()
        return sha or None

    def is_ancestor(self, a: str, b: str) -> bool:
        cp = self._run(["merge-base", "--is-ancestor", a, b], check=False)
        return cp.returncode == 0

    def merge_tree_probe(self, a: str, b: str) -> MergeProbe | None:
        version = GitRepo.version(self.runner)
        if version is None or version < GIT_MERGE_TREE_MIN_VERSION:
            return None
        args = ["merge-tree", "--write-tree", "--name-only", a, b]
        cp = self._run(args, check=False)
        if cp.returncode == 0:
            return MergeProbe(clean=True)
        lines = _decode(cp.stdout).splitlines()
        paths: list[str] = []
        if lines:
            rest = lines[1:]
            while rest and rest[0] == "":
                rest.pop(0)
            for line in rest:
                if line == "":
                    break
                paths.append(line)
        if not paths:
            # Non-zero exit with no parseable conflict section -- a real tool error
            # (bad refs, etc.), not a conflict.
            self._raise(args, cp)
        return MergeProbe(clean=False, paths=paths)

    # --- worktrees --------------------------------------------------------------------

    def worktree_add(self, path: str, branch: str, start_point: str) -> None:
        self._run(["worktree", "add", "-b", branch, path, start_point])

    def worktree_attach(self, path: str, branch: str) -> None:
        """`git worktree add <path> <branch>` -- attach an EXISTING branch (not currently
        checked out anywhere) to a new worktree path, preserving its history. Deliberately
        no `-b`: unlike `worktree_add`, this never creates or resets a branch -- git checks
        out `branch` as-is (attached, not detached, since it is a valid local branch name).

        D-ENS (E-Wk9Tz3 T-Wk3Nv6, HLD §11 M3): the re-attach case for a leftover
        `ao/<run>/<task>` branch surviving a crashed run, so `WorktreeManager.ensure()`
        never has to delete a user-visible ref to recover from one -- deletion stays with
        `release()`/`reconcile()`/`gc_run()`, where ownership has already been established.
        """
        self._run(["worktree", "add", path, branch])

    def worktree_list(self) -> list[WorktreeEntry]:
        common_cp = self._run(["rev-parse", "--git-common-dir"])
        common_dir = _resolve_abs(_decode(common_cp.stdout).strip(), self.path)
        cp = self._run(["worktree", "list", "--porcelain"])
        entries = parse_worktree_list(_decode(cp.stdout), common_dir)
        return _correct_admin_dirs(entries, common_dir)

    def worktree_remove(self, path: str, *, force: bool = False) -> WorktreeRemoveOutcome:
        by_path = {os.path.normpath(e.path): e for e in self.worktree_list()}
        entry = by_path.get(os.path.normpath(path))
        if entry is None:
            return "already_absent"
        if entry.locked:
            return "locked"

        args = ["worktree", "remove", *(["--force"] if force else []), path]
        cp = self._run(args, check=False)
        if cp.returncode == 0:
            return "removed"
        # C-6 (review, accepted limitation): same git-stderr-wording dependency as
        # `update_ref_cas` above -- git has no distinct exit code for "dirty worktree,
        # not an error". Pinned by `test_remove_in_use_when_dirty_without_force` against
        # the installed git's real message text.
        stderr = _decode(cp.stderr)
        if not force and ("contains modified" in stderr or "is dirty" in stderr):
            return "in_use"
        self._raise(args, cp)

    def prune_worktrees_scoped(self, path_prefix: str) -> PruneReport:
        """R-6: never deregister a worktree ao did not create. See HLD §11 M1."""
        entries = self.worktree_list()
        prefix = os.path.normpath(path_prefix)

        def _under(p: str) -> bool:
            norm = os.path.normpath(p)
            return norm == prefix or norm.startswith(prefix + os.sep)

        ours = [e for e in entries if _under(e.path)]
        foreign_prunable = [
            e for e in entries if e.prunable and not e.locked and not _under(e.path)
        ]

        if not foreign_prunable:
            # Safe: nothing foreign would be affected by a global prune.
            self._run(["worktree", "prune"])
            return PruneReport(mode="global", pruned=[e.path for e in ours if e.prunable])

        # A foreign worktree is currently unreachable; a global prune would silently
        # deregister it. Remove only our own stale registrations directly.
        pruned: list[str] = []
        for e in ours:
            if e.prunable and e.admin_dir:
                shutil.rmtree(e.admin_dir, ignore_errors=True)
                pruned.append(e.path)
        logger.info(
            "worktree.prune_scoped",
            extra={
                "event": "worktree.prune_scoped",
                "foreign_prunable": len(foreign_prunable),
            },
        )
        return PruneReport(
            mode="scoped", pruned=pruned, skipped_foreign=[e.path for e in foreign_prunable]
        )

    # --- refs, branches, commits --------------------------------------------------------

    def update_ref_cas(self, ref: str, new: str, expected_old: str) -> bool:
        # C-6 (review, accepted limitation): git gives no machine-stable signal (e.g. a
        # distinct exit code) to distinguish a CAS loss from any other `update-ref`
        # failure, so this is the one place in this module that parses git's English
        # stderr wording -- otherwise against this module's own "never parse
        # human-readable messages" principle (TASK.md Risks). `LC_ALL=C` fixes
        # translation but not wording drift across git releases; if a future git
        # version rewords this, `update_ref_cas` would start raising instead of
        # returning `False` for a routine retry. Pinned by
        # `test_update_ref_cas_fails_when_ref_moved` against the installed git's real
        # message text.
        args = ["update-ref", ref, new, expected_old]
        cp = self._run(args, check=False)
        if cp.returncode == 0:
            return True
        stderr = _decode(cp.stderr).lower()
        if "unable to lock" in stderr or "cannot lock ref" in stderr or "is at" in stderr:
            return False  # CAS loss, not an error -- caller may retry
        self._raise(args, cp)

    def create_ref(self, ref: str, sha: str) -> None:
        self._run(["update-ref", ref, sha])

    def delete_ref(self, ref: str) -> None:
        self._run(["update-ref", "-d", ref])

    def branch_exists(self, name: str) -> bool:
        cp = self._run(["show-ref", "--verify", "--quiet", f"refs/heads/{name}"], check=False)
        return cp.returncode == 0

    def delete_branch(self, name: str, *, force: bool = False) -> bool:
        if not self.branch_exists(name):
            return False
        args = ["branch", "-D" if force else "-d", name]
        cp = self._run(args, check=False)
        if cp.returncode == 0:
            return True
        if not self.branch_exists(name):
            return False  # raced away between the check and the delete
        self._raise(args, cp)

    def list_refs(self, prefix: str) -> dict[str, str]:
        cp = self._run(["for-each-ref", "--format=%(refname) %(objectname)", prefix])
        result: dict[str, str] = {}
        for line in _decode(cp.stdout).splitlines():
            if not line:
                continue
            name, _, sha = line.partition(" ")
            if name:
                result[name] = sha
        return result

    def commit_tree(
        self,
        tree_ish: str,
        parent: str,
        message: str,
        *,
        author_name: str | None = None,
        author_email: str | None = None,
        author_date: str | None = None,
        committer_date: str | None = None,
    ) -> str:
        """Deterministic BY CONSTRUCTION (C-1 review fix): plain `git commit-tree` embeds
        the current wall-clock second into both the author and committer lines, so two
        calls with identical (tree_ish, parent, message) could otherwise yield different
        shas if they straddle a one-second boundary -- exactly AC-17's requirement
        ("committing the same tree/parent/message twice yields the same sha") failing
        silently under load rather than by design. `author_date`/`committer_date` default
        to *parent*'s own recorded committer date (already fixed once `parent` is fixed,
        so reading it back is itself deterministic) and `author_name`/`author_email`
        default to fixed module constants -- so this method needs no caller cooperation
        to be pure. All four are still explicitly injectable for a caller that wants a
        different (but still caller-controlled, still deterministic) identity/date.
        """
        name = author_name if author_name is not None else _COMMIT_TREE_DEFAULT_NAME
        email = author_email if author_email is not None else _COMMIT_TREE_DEFAULT_EMAIL
        date = committer_date if committer_date is not None else self._committer_date_of(parent)
        a_date = author_date if author_date is not None else date
        extra_env = {
            "GIT_AUTHOR_NAME": name,
            "GIT_AUTHOR_EMAIL": email,
            "GIT_AUTHOR_DATE": a_date,
            "GIT_COMMITTER_NAME": name,
            "GIT_COMMITTER_EMAIL": email,
            "GIT_COMMITTER_DATE": date,
        }
        cp = self._run(["commit-tree", tree_ish, "-p", parent, "-m", message], extra_env=extra_env)
        return _decode(cp.stdout).strip()

    def _committer_date_of(self, commit_ish: str) -> str:
        """`commit_tree`'s default date source (C-1): *commit_ish*'s own recorded
        committer date, in a format `GIT_COMMITTER_DATE`/`GIT_AUTHOR_DATE` accept.
        """
        cp = self._run(["log", "-1", "--format=%cI", commit_ish])
        return _decode(cp.stdout).strip()

    def add_paths(self, cwd: str, paths: list[str]) -> None:
        if not paths:
            return
        self._run(["add", "--", *paths], cwd=cwd)

    def add_all(self, cwd: str) -> None:
        self._run(["add", "-A"], cwd=cwd)

    def status_porcelain(self, cwd: str, *, untracked: bool = True) -> list[StatusEntry]:
        args = ["status", "--porcelain", "-z"]
        if not untracked:
            args.append("--untracked-files=no")
        cp = self._run(args, cwd=cwd)
        return _parse_status_porcelain_z(cp.stdout)

    def _index_matches_head(self, cwd: str) -> bool:
        """True iff nothing is STAGED (the index equals HEAD's tree) -- C-2 review fix.
        `status_porcelain(untracked=False)` reports every tracked delta from HEAD,
        staged or not, so a tracked-but-unstaged edit would wrongly look like "something
        to commit" there; `diff --cached --quiet` checks the index specifically (exit 0
        <=> nothing staged, works even against an unborn HEAD).
        """
        cp = self._run(["diff", "--cached", "--quiet"], cwd=cwd, check=False)
        return cp.returncode == 0

    def commit(self, cwd: str, message: str, allow_empty: bool = False) -> str | None:
        if not allow_empty and self._index_matches_head(cwd):
            return None  # nothing STAGED; never create an empty commit implicitly
        args = ["commit", "-m", message]
        if allow_empty:
            args.append("--allow-empty")
        self._run(args, cwd=cwd)
        head_cp = self._run(["rev-parse", "HEAD"], cwd=cwd)
        return _decode(head_cp.stdout).strip()

    def reset_hard(self, cwd: str, ref: str) -> None:
        self._run(["reset", "--hard", ref], cwd=cwd)

    def diff_names(self, cwd: str, a: str, b: str) -> list[str]:
        cp = self._run(["diff", "--name-only", a, b], cwd=cwd)
        return [line for line in _decode(cp.stdout).splitlines() if line]

    def grep_conflict_markers(
        self, cwd: str, ref: str, paths: list[str] | None = None
    ) -> list[str]:
        """Paths (at *ref*, optionally restricted to *paths*) containing a leftover
        `<<<<<<< `/`>>>>>>> ` conflict-marker line (E-Wk9Tz3 T-Ib5Qy9 review C-3: the
        default structural verify check's own conflict-marker scan, promoted from a
        private `integrator.py` reach-across into this module's public surface -- NFR-1:
        paths only, never file contents, into Python).

        `-z` NUL-delimits matched entries so a unicode/quoted path round-trips exactly
        (same rationale as `ls_files`/`log_name_only`). Verified empirically against the
        installed git, not assumed: exit 0 = match found (each entry prefixed
        `<ref>:<path>`, stripped below), exit 1 = no match (empty list), anything else is
        a genuine error and raises.
        """
        args = ["grep", "-l", "-z"]
        for pattern in _CONFLICT_MARKER_PATTERNS:
            args += ["-e", pattern]
        args.append(ref)
        if paths:
            args += ["--", *paths]
        cp = self._run(args, cwd=cwd, check=False)
        if cp.returncode == 1:
            return []  # grep: no match -- clean
        if cp.returncode != 0:
            self._raise(args, cp)
        prefix = f"{ref}:"
        return [
            entry[len(prefix) :] if entry.startswith(prefix) else entry
            for entry in _decode(cp.stdout).split("\0")
            if entry
        ]

    def diff_check(self, cwd: str, base: str, head: str) -> list[str]:
        """`git diff --check` (E-Wk9Tz3 T-Ib5Qy9 review C-3): whitespace/leftover-conflict-
        marker check messages between *base* and *head*, or `[]` when clean. Exit-code
        only drives the outcome -- the returned lines are diagnostic text, never used for
        anything but a truthiness check by any current caller (NFR-1: no file content is
        read to produce them; git itself renders the message).

        Verified empirically against the installed git, not assumed: a real check failure
        exits **2**, not 1; a genuine tool error (bad refs, etc.) exits git's standard
        fatal code, 128. `_DIFF_CHECK_FATAL_THRESHOLD` draws that line.
        """
        args = ["diff", "--check", f"{base}..{head}"]
        cp = self._run(args, cwd=cwd, check=False)
        if cp.returncode == 0:
            return []
        if cp.returncode < _DIFF_CHECK_FATAL_THRESHOLD:
            return [line for line in _decode(cp.stdout).splitlines() if line]
        self._raise(args, cp)
        raise AssertionError("unreachable")  # pragma: no cover -- `_raise` is NoReturn

    def is_tracked(self, cwd: str, path: str) -> bool:
        cp = self._run(["ls-files", "--error-unmatch", "--", path], cwd=cwd, check=False)
        return cp.returncode == 0

    def ls_files(self, cwd: str, *, paths: list[str] | None = None) -> set[str]:
        """Every tracked path (optionally restricted to *paths*), as one `git ls-files
        -z` call (review W-1, `T-Ov9Bt5`): `isolation.hotspots.compute_hotspots` used to
        call `is_tracked` once per churned path -- O(N) subprocess spawns for N distinct
        paths in the window -- this gives it the same tracked/untracked answer for any
        number of paths in a single call, checked by in-memory set membership instead.
        `-z` NUL-delimits (unicode-safe, no `core.quotePath` mangling -- same rationale
        as `log_name_only`'s own docstring). `is_tracked` is unchanged and stays the
        right tool for a genuine single-path check elsewhere.
        """
        args = ["ls-files", "-z"]
        if paths:
            args.extend(["--", *paths])
        cp = self._run(args, cwd=cwd)
        return {p for p in _decode(cp.stdout).split("\0") if p}

    def log_name_only(self, cwd: str, *, since: str | None = None, no_merges: bool = True) -> str:
        """Raw ``git log --pretty=format: --name-only -z`` stdout text (HLD §9.2's churn
        source for `isolation/hotspots.py::compute_hotspots`, `T-Ov9Bt5`). Returned as
        decoded text, NUL-delimited (one path per NUL-terminated entry, an extra NUL
        between consecutive commits -- mirrored by `isolation.hotspots.parse_churn`,
        which does the actual parsing; this module stays a thin porcelain surface with no
        domain logic). `-z` is load-bearing, not cosmetic (review C-3): without it, git's
        default `core.quotePath` C-style-octal-escapes and double-quotes any path with a
        non-ASCII or otherwise "unusual" byte (e.g. ``café.rs`` -> ``"caf\\303\\251.rs"``),
        which would silently mismatch every later `is_tracked`/lookup call against that
        same literal path; `-z` (like this module's own `status_porcelain`/
        `_parse_status_porcelain_z` precedent) turns off quoting entirely and gives an
        unambiguous, machine-parseable boundary instead of git's usual blank-line
        commit separator.

        `since` is any `git log --since=<...>` expression (e.g. an ISO date); omitted
        means full history, in which case `LOG_NAME_ONLY_MAX_COMMITS` still bounds the
        walk (review C-1: an unwindowed query must never be genuinely unbounded).
        Tolerates an empty/unborn-HEAD repo (returns "") rather than raising, since "no
        history yet" is a normal, expected input for a freshly-initialized repo, not a
        failure -- callers that want a hard error for a non-git directory should check
        `GitRepo.probe` first.

        Public (not underscore-prefixed): exercised by
        `tests/isolation/test_git.py::TestStructuralNoNetworkSurface::
        test_public_method_surface_never_reaches_a_forbidden_verb`'s exhaustive sweep like
        every other git-invoking method on this class, and routes through `_run`'s S-1
        hardening (hooks-path, forbidden-subcommand guard, forced env, `SAFETY_ARGS`,
        `--no-pager`) exactly like every other method here -- nothing about this call is
        exempt from that surface.
        """
        args = [
            "log",
            "--pretty=format:",
            "--name-only",
            "-z",
            f"--max-count={LOG_NAME_ONLY_MAX_COMMITS}",
        ]
        if no_merges:
            args.append("--no-merges")
        if since:
            args.append(f"--since={since}")
        cp = self._run(args, cwd=cwd, check=False)
        if cp.returncode != 0:
            return ""
        return _decode(cp.stdout)

    def ls_files_untracked_ignored(self, cwd: str, paths: list[str]) -> set[str]:
        if not paths:
            return set()
        cp = self._run(
            ["ls-files", "--others", "--ignored", "--exclude-standard", "--", *paths], cwd=cwd
        )
        return {line for line in _decode(cp.stdout).splitlines() if line}

    # --- rebase -------------------------------------------------------------------------

    def _worktree_git_dir(self, cwd: str) -> Path:
        cp = self._run(["rev-parse", "--git-dir"], cwd=cwd)
        return Path(_resolve_abs(_decode(cp.stdout).strip(), cwd))

    def rebase_in_progress(self, cwd: str) -> bool:
        git_dir = self._worktree_git_dir(cwd)
        return (git_dir / "rebase-merge").exists() or (git_dir / "rebase-apply").exists()

    def conflicted_paths(self, cwd: str) -> list[str]:
        cp = self._run(["diff", "--name-only", "--diff-filter=U"], cwd=cwd)
        return [line for line in _decode(cp.stdout).splitlines() if line]

    def rebase_onto(self, cwd: str, onto: str, upstream: str, branch: str) -> RebaseOutcome:
        args = ["rebase", "--onto", onto, upstream, branch]
        cp = self._run(args, cwd=cwd, check=False)
        if cp.returncode == 0:
            return RebaseOutcome(clean=True)
        if self.rebase_in_progress(cwd):
            return RebaseOutcome(clean=False, paths=self.conflicted_paths(cwd))
        self._raise(args, cp)

    def rebase_continue(self, cwd: str) -> RebaseOutcome:
        # GIT_EDITOR=true is already forced by `_run`'s `_FORCED_ENV` -- never blocks on
        # an editor/TTY even with no TTY attached.
        args = ["rebase", "--continue"]
        cp = self._run(args, cwd=cwd, check=False)
        if cp.returncode == 0:
            return RebaseOutcome(clean=True)
        if self.rebase_in_progress(cwd):
            return RebaseOutcome(clean=False, paths=self.conflicted_paths(cwd))
        self._raise(args, cp)

    def rebase_abort(self, cwd: str) -> None:
        self._run(["rebase", "--abort"], cwd=cwd)

    def show_stage(self, cwd: str, stage: int, path: str) -> bytes | None:
        cp = self._run(["show", f":{stage}:{path}"], cwd=cwd, check=False)
        if cp.returncode != 0:
            return None
        return cp.stdout
