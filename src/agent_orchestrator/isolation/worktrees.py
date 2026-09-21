"""Repo grouping and the idempotent per-task worktree lifecycle (E-Wk9Tz3 T-Wk3Nv6,
HLD §7.1/§11 M3).

Everything here is impure (git subprocess calls via `isolation/git.py`'s `GitRepo`,
filesystem directory creation/removal) -- the pure naming/path rules it builds on live in
`isolation/paths.py`. This module never shells out to git directly; every git call is routed
through `GitRepo` (S-1's single choke point), and every worktree deregistration is routed
through `GitRepo.prune_worktrees_scoped` -- never a global `git worktree prune` (R-6).
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from ..errors import GitError, TaskIdCollisionError, WorktreeCollisionError
from . import paths
from .git import GitRepo, RepoProbe, Runner, WorktreeEntry

logger = logging.getLogger(__name__)

# S-9: every directory this module creates under $AO_STATE_DIR (worktree parents included)
# is mode 0700, in case an operator points AO_STATE_DIR at a shared-host location.
_DIR_MODE = 0o700

# HLD §7.1 group_repos: repo key hash length (matches paths.workspace_key's own
# sha256-prefix convention, but 8 chars here per the HLD's own pseudocode).
_REPO_KEY_HASH_LEN = 8

IntegrationOutcome = Literal["integrated", "failed"]
KeepWorktreesPolicy = Literal["never", "on_failure", "always"]

_KEEP_ALWAYS: KeepWorktreesPolicy = "always"
_KEEP_ON_FAILURE: KeepWorktreesPolicy = "on_failure"
_OUTCOME_FAILED: IntegrationOutcome = "failed"


# ---------------------------------------------------------------------------------------
# Repo grouping (HLD §7.1)
# ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class RepoMember:
    """One `RepoRef` that resolved into a given `IsolatedRepo`'s git repository."""

    repo_id: str
    rel: str  # "" for the repo's own toplevel


@dataclass(frozen=True)
class IsolatedRepo:
    """One real git repository, possibly covering >1 `RepoRef` (HLD §7.1: a reposet can list
    several RepoRefs that resolve into the same repo, e.g. `core=./fin_plan`,
    `docs=./fin_plan/docs-md`).
    """

    key: str
    toplevel: str
    common_dir: str
    submodule: bool = False
    members: list[RepoMember] = field(default_factory=list)


# `.git` file marker git writes for a linked worktree OR a submodule -- both point elsewhere
# via a `gitdir: <path>` line. Only a submodule's target path contains this segment (a linked
# worktree's target contains `/worktrees/<id>` instead), so this is how the two are told
# apart without a dedicated GitRepo method (git.py is not touched by this task).
_SUBMODULE_GITDIR_MARKER = "/.git/modules/"


def _is_submodule(toplevel: str) -> bool:
    git_entry = Path(toplevel) / ".git"
    if not git_entry.is_file():
        return False
    try:
        content = git_entry.read_text().strip()
    except OSError:
        return False
    return content.startswith("gitdir:") and _SUBMODULE_GITDIR_MARKER in content


def _repo_key(toplevel: str, common_dir: str) -> str:
    digest = hashlib.sha256(common_dir.encode("utf-8")).hexdigest()[:_REPO_KEY_HASH_LEN]
    basename = paths.sanitize_ref_component(os.path.basename(toplevel) or "repo")
    return f"{basename}-{digest}"


def group_repos(
    repo_paths: dict[str, str],
    *,
    probe: Callable[[str], RepoProbe | None] = GitRepo.probe,
) -> tuple[list[IsolatedRepo], list[str]]:
    """HLD §7.1: group `{repo_id: abs_path}` by real git repository (`--git-common-dir`), so
    two `RepoRef`s inside one repo (the real consumer's `core`/`docs` shape) produce ONE
    `IsolatedRepo` with two members, never two worktrees of the same repo.

    Returns `(groups, skipped_repo_ids)` -- *skipped_repo_ids* are `repo_id`s whose path is
    not a git repository at all (`probe` returned `None`); the caller decides whether that is
    a warning or a fallback to `isolation: none` for those paths.

    Deterministic by construction: `repo_paths` is walked in sorted-key order and the
    returned list is sorted by `IsolatedRepo.key`, so the result is stable regardless of the
    input dict's iteration order.
    """
    groups: dict[str, IsolatedRepo] = {}
    skipped: list[str] = []
    for repo_id, abs_path in sorted(repo_paths.items()):
        probed = probe(abs_path)
        if probed is None:
            skipped.append(repo_id)
            logger.warning(
                "worktree.non_git_repo",
                extra={"event": "worktree.non_git_repo", "repo_id": repo_id, "path": abs_path},
            )
            continue

        toplevel = os.path.normpath(probed.toplevel)
        common_dir = os.path.normpath(probed.common_dir)
        existing = groups.get(common_dir)
        if existing is None:
            submodule = _is_submodule(toplevel)
            if submodule:
                logger.warning(
                    "worktree.submodule_unsupported",
                    extra={
                        "event": "worktree.submodule_unsupported",
                        "repo_id": repo_id,
                        "path": toplevel,
                    },
                )
            existing = IsolatedRepo(
                key=_repo_key(toplevel, common_dir),
                toplevel=toplevel,
                common_dir=common_dir,
                submodule=submodule,
                members=[],
            )
            groups[common_dir] = existing

        rel = os.path.relpath(os.path.normpath(abs_path), toplevel)
        existing.members.append(RepoMember(repo_id=repo_id, rel="" if rel == "." else rel))

    ordered = sorted(groups.values(), key=lambda g: g.key)
    return ordered, skipped


# ---------------------------------------------------------------------------------------
# TaskIsolation / RepoIsolation (R-8, AC-19: no reference to mutable run state)
# ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class RepoIsolation:
    """One repo's resolved isolation for one task: the worktree it runs against."""

    key: str
    toplevel: str
    common_dir: str
    worktree_root: str
    branch: str
    base: str
    members: list[RepoMember] = field(default_factory=list)


@dataclass
class TaskIsolation:
    """`WorktreeManager.ensure()`'s return value -- everything a caller needs to remap a
    task's paths (via `paths.effective_path`) and everything the Integrator needs to decide
    "Empty" and do the untracked-output copy-back (R-8), without ever holding a reference to
    a `TaskSpec` or `RunState` (R-20/NFR-3, AC-19).
    """

    task_id: str
    cycle: int
    declared_outputs: list[str]
    workspace_root: str
    repos: list[RepoIsolation] = field(default_factory=list)


@dataclass(frozen=True)
class ReconcileReport:
    removed_worktrees: list[str] = field(default_factory=list)
    deleted_refs: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------------------
# Directory creation helper (S-9: 0700 on every directory this module creates)
# ---------------------------------------------------------------------------------------


def _mkdir_0700(path: Path) -> None:
    """Create *path* and any missing parents, chmod 0700 on each newly created level.

    `Path.mkdir(parents=True, mode=...)` only applies `mode` to the deepest directory --
    intermediate parents get whatever the process umask leaves them (stdlib documented
    behaviour) -- so each level is created and chmod'd individually here instead.
    """
    to_create: list[Path] = []
    current = path
    while not current.exists():
        to_create.append(current)
        if current.parent == current:  # filesystem root guard
            break
        current = current.parent
    for p in reversed(to_create):
        p.mkdir(exist_ok=True)
        os.chmod(p, _DIR_MODE)


# ---------------------------------------------------------------------------------------
# WorktreeManager (HLD §11 M3)
# ---------------------------------------------------------------------------------------


class WorktreeManager:
    """Owns the create/reuse/release/reconcile/gc lifecycle of every task worktree for one
    run, across every `IsolatedRepo` the run touches.

    `runner`/`hooks_dir` are threaded straight into each `GitRepo` this manager constructs,
    so tests inject a `RecordingFakeRunner`/`tmp_path`-scoped hooks dir without touching real
    git or the real home dir -- the same convention `isolation/git.py`'s own tests use.
    """

    def __init__(
        self,
        workspace_root: str,
        run_id: str,
        repos: list[IsolatedRepo],
        integration_heads: dict[str, str],
        *,
        runner: Runner | None = None,
        hooks_dir: Path | None = None,
    ) -> None:
        self.workspace_root = workspace_root
        self.run_id = run_id
        self.repos = repos
        # repo_key -> current integration head sha. A plain dict (not RunIntegrationState)
        # keeps this module decoupled from RunState (R-20/NFR-3 -- see TaskIsolation above).
        self.integration_heads = integration_heads
        self._runner = runner
        self._hooks_dir = hooks_dir
        self._git_repos: dict[str, GitRepo] = {}
        # M-2 (review, 2026-09-07): sanitized task component -> the raw task id that claimed
        # it. `paths.sanitize_ref_component` is non-injective, so two DISTINCT ids can name
        # one worktree path and one branch; `ensure()` refuses the second rather than taking
        # its "reuse" path and silently running two tasks in one worktree. Populated on the
        # `ensure()` path only -- `release()`/`reconcile()`/`gc_run()` work off what is on
        # disk and need no claim.
        self._claimed_components: dict[str, str] = {}

    def _git_for(self, repo: IsolatedRepo) -> GitRepo:
        git = self._git_repos.get(repo.key)
        if git is None:
            git = GitRepo(repo.toplevel, runner=self._runner, hooks_dir=self._hooks_dir)
            self._git_repos[repo.key] = git
        return git

    def _prefix(self, run_id: str | None = None) -> Path:
        return paths.worktree_root_prefix_for(self.workspace_root, run_id or self.run_id)

    # --- ensure ------------------------------------------------------------------------

    def _claim_component(self, task_id: str, branch: str) -> None:
        """M-2: claim *task_id*'s sanitized ref/path component for this run, or fail loudly.

        `paths.sanitize_ref_component` maps every character outside `[A-Za-z0-9._-]` onto
        `-`, so `svc/api` and `svc-api` produce one component -- hence one worktree directory
        and one `ao/<run>/<task>` branch. Without this claim the second `ensure()` finds a
        registered worktree already on the expected branch and takes the *reuse* path, so two
        tasks that the workflow declared as isolated share a checkout and a branch, with no
        error anywhere. That is a silent isolation failure, so it is refused.

        Refusing, rather than disambiguating with a hash suffix on every component, is
        deliberate: the suffix would rename every branch and worktree path of every run (a
        user-visible change to a shipped naming scheme, for a condition that is always an
        authoring mistake), while `spec.validate_isolation`'s V13 rule already turns the same
        condition into a validate-time error on both reachable paths -- `ao validate`/spec
        load, and `emit_tasks` manifest injection. This is the runtime backstop for a caller
        that reaches `ensure()` without having run that validation.

        Re-entrant for the SAME id: `ensure()` is called once per dispatch cycle (retries,
        rerun-on-fresh-base), and re-claiming one's own component is a no-op.
        """
        component = paths.sanitize_ref_component(task_id)
        claimant = self._claimed_components.setdefault(component, task_id)
        if claimant != task_id:
            raise TaskIdCollisionError(
                task_id=task_id,
                other_task_id=claimant,
                component=component,
                branch=branch,
                worktree_path=str(self._prefix() / component),
            )

    def ensure(self, task_id: str, cycle: int, declared_outputs: Sequence[str]) -> TaskIsolation:
        """Create (or idempotently reuse) this task's worktree in every repo (AC-9, AC-10).

        S-9: every directory created under `$AO_STATE_DIR` is mode 0700. R-6: the only
        pruning this method (or any method in this module) performs is
        `GitRepo.prune_worktrees_scoped`, scoped to this run's own worktree prefix -- never
        a global `git worktree prune`.

        M-2 (review, 2026-09-07): raises `TaskIdCollisionError` when *task_id* sanitizes to a
        component another task of this run already claimed -- see `_claim_component`.
        """
        branch = paths.task_branch(self.run_id, task_id)
        self._claim_component(task_id, branch)
        iso = TaskIsolation(
            task_id=task_id,
            cycle=cycle,
            declared_outputs=list(declared_outputs),
            workspace_root=self.workspace_root,
            repos=[],
        )
        prefix = str(self._prefix())
        for repo in self.repos:
            git = self._git_for(repo)
            path = paths.worktree_root(self.workspace_root, self.run_id, task_id, repo.key)
            head = self.integration_heads[repo.key]
            expected_ref = f"refs/heads/{branch}"

            # D-ENS (HLD §11 M3, review 2026-09-07: resolves the deviation-4 contradiction
            # between the old pseudocode's unconditional `delete_ref` and this section's own
            # "hard error, never silently reuse" prose). `ensure()` NEVER deletes a
            # user-visible ref -- that stays with `release()`/`reconcile()`/`gc_run()`, where
            # ownership has already been established. R-6: the scoped prune below clears an
            # AC-10b registered-but-directory-deleted entry BEFORE the decision below is
            # made, so a crash-recovered phantom registration can never be mistaken for a
            # live occupant of `path` or a live holder of `branch`.
            git.prune_worktrees_scoped(prefix)

            entry = _find_worktree_entry(git, path)
            if path.exists() and entry is None:
                # AC-10a: a stale, unregistered plain directory (never a git worktree at
                # all -- prune_worktrees_scoped only clears registrations, not arbitrary
                # dirs) is removed outright before any decision below.
                shutil.rmtree(path, ignore_errors=True)

            if entry is not None and git.rebase_in_progress(str(path)):
                # AC-10c: mid-rebase, git reports this worktree `detached` (no `branch`
                # line at all -- verified empirically), which would otherwise misread as a
                # D-ENS branch collision. Abort first and re-read the entry: `rebase
                # --abort` restores the original branch attachment, so the reuse-vs-
                # collision decision below sees the real branch, not the mid-rebase
                # detached state.
                git.rebase_abort(str(path))
                entry = _find_worktree_entry(git, path)

            if entry is not None:
                if entry.branch == expected_ref and entry.head is not None:
                    # Reuse (AC-9).
                    base = head
                    logger.info(
                        "worktree.reused",
                        extra={
                            "event": "worktree.reused",
                            "task_id": task_id,
                            "repo": repo.key,
                            "path": str(path),
                        },
                    )
                else:
                    # D-ENS: a DIFFERENT worktree already occupies our expected path (on a
                    # different branch, or with no readable HEAD). Hard error, never a
                    # silent rmtree+recreate -- that would (or could) destroy work.
                    raise WorktreeCollisionError(
                        expected_ref,
                        str(path),
                        remedy=(
                            "run `ao prune --worktrees-only`, or "
                            f"`git worktree remove {path}` if you are certain it is safe."
                        ),
                        found_branch=entry.branch,
                    )
            elif git.branch_exists(branch):
                # No worktree registered at our path, but the branch already exists.
                holder = _find_entry_holding_branch(git, expected_ref)
                if holder is not None:
                    # D-ENS: the branch is checked out by an entirely different worktree.
                    raise WorktreeCollisionError(
                        expected_ref,
                        holder.path,
                        remedy=(
                            "run `ao prune --worktrees-only`, or "
                            f"`git worktree remove {holder.path}` if you are certain it is "
                            "safe."
                        ),
                        found_branch=holder.branch,
                    )
                # D-ENS: re-attach to the leftover branch (a crashed run's own worktree
                # never got recreated), preserving its commits -- never `-b`, never a
                # `delete_ref`. The integrator's squash+rebase lands whatever is there.
                _mkdir_0700(path.parent)
                git.worktree_attach(str(path), branch)
                os.chmod(path, _DIR_MODE)
                base = head
                logger.info(
                    "worktree.branch_reattached",
                    extra={
                        "event": "worktree.branch_reattached",
                        "task_id": task_id,
                        "repo": repo.key,
                        "branch": branch,
                        "tip": git.rev_parse(branch),
                    },
                )
            else:
                # Fresh create: no worktree at our path, branch does not exist yet.
                _mkdir_0700(path.parent)
                git.worktree_add(str(path), branch, head)
                os.chmod(path, _DIR_MODE)
                base = head
                logger.info(
                    "worktree.created",
                    extra={
                        "event": "worktree.created",
                        "task_id": task_id,
                        "repo": repo.key,
                        "branch": branch,
                        "base": head,
                    },
                )

            iso.repos.append(
                RepoIsolation(
                    key=repo.key,
                    toplevel=repo.toplevel,
                    common_dir=repo.common_dir,
                    worktree_root=str(path),
                    branch=branch,
                    base=base,
                    members=repo.members,
                )
            )
        return iso

    # --- release -----------------------------------------------------------------------

    def release(
        self, task_id: str, outcome: IntegrationOutcome, policy: KeepWorktreesPolicy
    ) -> None:
        """Remove this task's worktrees per *policy* (AC-11). R-23: NEVER raises -- a
        `GitError` from `worktree_remove` (this module's only git-error surface here) is
        caught, logged, and the next repo's removal still runs.
        """
        if policy == _KEEP_ALWAYS:
            return
        if policy == _KEEP_ON_FAILURE and outcome == _OUTCOME_FAILED:
            return
        for repo in self.repos:
            git = self._git_for(repo)
            path = paths.worktree_root(self.workspace_root, self.run_id, task_id, repo.key)
            try:
                result = git.worktree_remove(str(path), force=True)
            except GitError as exc:
                logger.warning(
                    "worktree.remove_failed",
                    extra={
                        "event": "worktree.remove_failed",
                        "task_id": task_id,
                        "repo": repo.key,
                        "path": str(path),
                        "error": str(exc),
                    },
                )
                continue
            # C-5 (review, 2026-09-07): the event NAME (not just the `outcome` field) must
            # reflect whether the worktree actually went away -- a reader grepping/alerting
            # on `worktree.removed` should never count a "locked"/"in_use" non-removal.
            if result in ("removed", "already_absent"):
                logger.info(
                    "worktree.removed",
                    extra={
                        "event": "worktree.removed",
                        "task_id": task_id,
                        "repo": repo.key,
                        "path": str(path),
                        "outcome": result,
                    },
                )
            else:
                logger.warning(
                    "worktree.remove_skipped",
                    extra={
                        "event": "worktree.remove_skipped",
                        "task_id": task_id,
                        "repo": repo.key,
                        "path": str(path),
                        "outcome": result,
                    },
                )

    # --- reconcile ---------------------------------------------------------------------

    def reconcile(self, known_task_ids: set[str]) -> ReconcileReport:
        """Reap worktrees/refs whose task id is not in *known_task_ids* (run start + resume).
        Idempotent: a second call on an already-clean state changes nothing (AC-12).

        C-4 (review, 2026-09-07): *known_task_ids* are RAW ids, while a worktree directory
        name and a ref's task component are both already SANITIZED -- they came out of
        `paths.worktree_root`/`paths.task_branch`. Comparing the two directly judged every
        task whose id is not sanitize-identity (any id holding a `:`, `/`, a space, most
        punctuation) to be unknown, and then force-removed its worktree and deleted its
        branch -- destroying the uncommitted, unlanded work of a task that is live and
        declared. On the resume path that ran BEFORE the task did, so a resumed run silently
        lost completed-but-unlanded work. Both comparisons below now go through
        `paths.group_by_ref_component`, which maps each sanitized component back to the raw
        ids that produced it, so like is compared with like exactly once per call.
        """
        removed_worktrees: list[str] = []
        deleted_refs: list[str] = []
        prefix = str(self._prefix())
        san_run = paths.sanitize_ref_component(self.run_id)
        ref_prefix = f"refs/heads/{paths.AO_REF_NAMESPACE}/{san_run}/"
        known_components = paths.group_by_ref_component(sorted(known_task_ids))

        for repo in self.repos:
            git = self._git_for(repo)
            git.prune_worktrees_scoped(prefix)  # R-6

            remaining_branches: set[str] = set()
            for entry in git.worktree_list():
                norm = os.path.normpath(entry.path)
                under_prefix = norm == prefix or norm.startswith(prefix + os.sep)
                if under_prefix:
                    rel = os.path.relpath(norm, prefix)
                    # C-4: the directory component is the SANITIZED task id, so it is only
                    # ever compared against `known_components`, never against the raw ids.
                    task_component = rel.split(os.sep)[0]
                    if task_component not in known_components:
                        try:
                            git.worktree_remove(norm, force=True)
                            removed_worktrees.append(norm)
                            logger.info(
                                "worktree.orphan_reaped",
                                extra={
                                    "event": "worktree.orphan_reaped",
                                    "task_id": task_component,
                                    "path": norm,
                                },
                            )
                        except GitError as exc:
                            logger.warning(
                                "worktree.remove_failed",
                                extra={
                                    "event": "worktree.remove_failed",
                                    "task_id": task_component,
                                    "path": norm,
                                    "error": str(exc),
                                },
                            )
                            if entry.branch:
                                remaining_branches.add(entry.branch)
                        continue
                if entry.branch:
                    remaining_branches.add(entry.branch)

            for ref_name in git.list_refs(ref_prefix):
                task_component = ref_name[len(ref_prefix) :]
                if task_component in paths.RESERVED_BRANCH_COMPONENTS:
                    continue  # ao/<run>/integration -- run-scoped, not task-scoped
                # C-4: `ref_name` came from `paths.task_branch`, so its task component is
                # already sanitized -- compare it against the sanitized known ids.
                if task_component in known_components:
                    continue
                # C-2/C-8 (review, 2026-09-07): `remaining_branches` holds `WorktreeEntry.
                # branch`, which is always the FULL `refs/heads/...` form (verified against
                # real `git worktree list --porcelain` output) -- `ref_name` from
                # `list_refs` is ALSO the full form (`%(refname)`). Comparing the two
                # directly (never stripping either side first) is what makes this guard
                # real: an earlier version stripped `ref_name` to its short form before
                # comparing against the full-form set, so the guard below could never
                # match anything -- a worktree whose removal had just failed one loop
                # above would still lose its branch ref immediately after.
                if ref_name in remaining_branches:
                    continue
                git.delete_ref(ref_name)
                deleted_refs.append(ref_name)

        return ReconcileReport(removed_worktrees=removed_worktrees, deleted_refs=deleted_refs)

    # --- gc_run --------------------------------------------------------------------------

    def gc_run(self, run_id: str) -> None:
        """Remove every worktree/ref this run created (used by `ao prune`, AC-13). Takes an
        explicit *run_id* (rather than always `self.run_id`) so one manager can GC any past
        run over the same `self.repos`.

        C-2 (review, 2026-09-07): mirrors `reconcile()`'s `remaining_branches` guard for the
        task-branch loop -- a branch ref is deleted only when its worktree is actually gone
        (`worktree_remove` returned `"removed"`/`"already_absent"`, or the entry was never
        under our prefix to begin with); on `"locked"`/`"in_use"`, or a raised `GitError`,
        the branch is kept and reported, never silently orphaned from a directory that is
        still there. Squash refs (`refs/ao/runs/<run>/*`) are NOT worktree-branch refs --
        `reconcile()` never touches them either -- so `gc_run` (an explicit, operator-
        invoked full purge via `ao prune`) still deletes them unconditionally, per AC-13.
        """
        prefix = str(self._prefix(run_id))
        san_run = paths.sanitize_ref_component(run_id)
        ref_prefix = f"refs/heads/{paths.AO_REF_NAMESPACE}/{san_run}/"

        for repo in self.repos:
            git = self._git_for(repo)
            remaining_branches: set[str] = set()
            for entry in git.worktree_list():
                norm = os.path.normpath(entry.path)
                if not (norm == prefix or norm.startswith(prefix + os.sep)):
                    continue
                try:
                    outcome = git.worktree_remove(norm, force=True)
                except GitError as exc:
                    logger.warning(
                        "worktree.remove_failed",
                        extra={
                            "event": "worktree.remove_failed",
                            "run_id": run_id,
                            "path": norm,
                            "error": str(exc),
                        },
                    )
                    if entry.branch:
                        remaining_branches.add(entry.branch)
                    continue
                if outcome in ("locked", "in_use"):
                    logger.warning(
                        "worktree.remove_failed",
                        extra={
                            "event": "worktree.remove_failed",
                            "run_id": run_id,
                            "path": norm,
                            "outcome": outcome,
                        },
                    )
                    if entry.branch:
                        remaining_branches.add(entry.branch)
                # "removed"/"already_absent": the worktree is gone; its branch ref may go.

            git.prune_worktrees_scoped(prefix)  # R-6
            for ref_name in list(git.list_refs(ref_prefix)):
                if ref_name in remaining_branches:
                    continue
                git.delete_ref(ref_name)
            for ref_name in list(git.list_refs(f"refs/{paths.AO_REF_NAMESPACE}/runs/{san_run}/")):
                git.delete_ref(ref_name)

        prefix_path = Path(prefix)
        if prefix_path.exists():
            shutil.rmtree(prefix_path, ignore_errors=True)


def _find_worktree_entry(git: GitRepo, path: Path) -> WorktreeEntry | None:
    target = os.path.normpath(str(path))
    for entry in git.worktree_list():
        if os.path.normpath(entry.path) == target:
            return entry
    return None


def _find_entry_holding_branch(git: GitRepo, expected_ref: str) -> WorktreeEntry | None:
    """D-ENS: is `expected_ref` (full `refs/heads/...` form) already checked out by ANY
    registered worktree of this repo? Used only in the "no worktree at our own path, but
    the branch exists" state -- if some OTHER worktree holds it, `ensure()` hard-errors
    rather than guessing which side is stale.
    """
    for entry in git.worktree_list():
        if entry.branch == expected_ref:
            return entry
    return None
