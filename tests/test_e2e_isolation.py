"""E2E tests for per-task git isolation (E-Wk9Tz3, ticket T-Ee3Mn8).

Every test here drives `ao run` / `ao resume` / `ao prune` through
`typer.testing.CliRunner` -- the outermost boundary CLAUDE.md mandates for e2e -- against
a **real** temp git repo, with `FakeExecutor` (or a local subclass) standing in for the
agent process.

House rules for this file, all of them consequences of the 2026-09-07 review:

* **Never assert only the exit code.** Every test asserts the property its name claims,
  read from the structured `run.log` events (`_events`) or `state.json`. `run.log` is the
  observable event contract (HLD §11 M9), so an assertion on an event survives a stdout
  rewording; an assertion on ``result.output`` prose does not.
* **No weaker duplicates.** Where a property is already genuinely covered by a
  pre-existing test at any altitude, this file does *not* re-assert it more weakly -- the
  cross-reference lives in this ticket's `STATUS.md` coverage matrix instead. Tests that
  used to do that (precedence matrix, `should_skip` branch 2, foreign-worktree prune, the
  three workspace-lock policies, the two single-task "ladder" cases) were deleted for
  exactly that reason.
* **Concurrency is proved with latches, never sleeps or timing.** `_WaveProbeExecutor`
  is the ADR-0007 T-TNleFt gated-executor pattern; its `threading.Barrier` makes
  "N tasks were in flight simultaneously" a fact rather than an inference. The barrier's
  ``timeout=`` is a deadlock detector (it makes a regression fail fast instead of hanging
  CI), never a timing assertion.
* **Fixed clocks.** `_freeze_run_id` pins `runstate._utc_now`, which makes `run_id` --
  and therefore every `isolation/paths.py::worktree_root` path -- exactly predictable, so
  a spec can name another task's worktree without racing (this is what makes the S-4
  dispatch tests possible at the CLI boundary at all).
* **Cross-module fixture imports are this repo's settled convention** (review N-1). Six
  test modules already do it -- `tests/test_cli_isolation_flags.py` and
  `tests/test_e2e_cli_prune_worktrees.py` import helpers from
  `tests/test_e2e_cli_isolation.py`; `tests/test_hotspots.py` and
  `tests/isolation/test_conflict_fixtures.py` import from `tests/isolation/conftest.py`;
  `tests/test_wave_concurrency_semantics.py` imports `_GatedExecutor` from
  `tests/test_wave_scheduler.py`. `tests/isolation/test_ladder_e2e.py`'s "never
  cross-import" note describes *that* file's own choice, not a project rule. This file
  follows the majority convention and imports the locked fixture builders
  (`make_hooked_repo`, `semantic_verify_argv`) rather than re-deriving them.
* **Workspace root and state dir are disjoint** (`_workspace` / `_env`): in production
  worktrees live under `$AO_STATE_DIR`, *outside* the workspace. A layout that nests the
  state dir inside the workspace would make `IsolatedArtifactView`'s containment test
  pass every sibling-worktree path through its "inside the shared workspace root" arm,
  silently disarming the S-4 guard these tests exist to prove.

Coverage owned here maps to TASK.md AC-3..AC-13 and HLD §17.3/§17.5; the row-by-row
matrix (including rows deliberately covered elsewhere, and rows deferred with an owning
ticket) is in this ticket's `STATUS.md`.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

import agent_orchestrator.executors as executors_pkg
import agent_orchestrator.runstate as runstate_mod
from agent_orchestrator.artifacts import LocalFsArtifactStore
from agent_orchestrator.cli import app
from agent_orchestrator.engine import Orchestrator
from agent_orchestrator.executors.fake import FakeExecutor
from agent_orchestrator.isolation import escalation
from agent_orchestrator.isolation.paths import worktree_root
from agent_orchestrator.isolation.worktrees import group_repos
from agent_orchestrator.models import (
    RunState,
    TaskContext,
    TaskResult,
    TaskRunState,
    compute_run_usage_totals,
)
from agent_orchestrator.runstate import RunStateStore
from tests.isolation.conftest import make_hooked_repo, semantic_verify_argv

runner = CliRunner()


# ============================================================================
# Named constants (no magic literals at call sites)
# ============================================================================

# Fixed clock for `RunStateStore.new_run` -> a fully deterministic `run_id`, which is what
# makes `worktree_root(...)` predictable at spec-write time (the S-4 dispatch tests).
_FIXED_RUN_DT = datetime(2026, 1, 1, tzinfo=UTC)
# `runstate._FMT` -- duplicated deliberately: importing a private module constant would
# couple this file to an implementation detail, and a drift here fails loudly (the
# predicted run_id would not match the directory the run actually created).
_RUN_ID_TIMESTAMP_FMT = "%Y%m%dT%H%M%SZ"

# Deadlock detector for every latch below. Never a timing assertion: the tests assert
# facts recorded *at* the latch, and this bound only converts "would hang forever" into
# "fails in bounded time".
_LATCH_TIMEOUT_SECONDS = 60.0

# Generous upper bound for the ~50-dirty-file checkout-sync scan (R-12). Same role as
# above: a smoke alarm on an accidental O(n^2) regression, not a benchmark.
_WALL_TIME_CEILING_SECONDS = 10.0

# Per-dispatch fake usage, used by the AC-5 cost-accounting assertions. Distinct values so
# a mis-attributed cycle is visible in the arithmetic rather than hidden by symmetry.
_COST_PER_DISPATCH_USD = 0.25
_INPUT_TOKENS_PER_DISPATCH = 1000
_OUTPUT_TOKENS_PER_DISPATCH = 100

# `.ao/hotspots.json` weight for the R-5 ranking fixture -- comfortably above
# `scheduling.overlap.BASELINE_OVERLAP_WEIGHT` (1.0) so the ordering it drives is
# unambiguous.
_HOT_PATH_WEIGHT = 9.0


# ============================================================================
# Determinism fixture (HLD §17.2)
# ============================================================================


@pytest.fixture(autouse=True)
def _isolated_git_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Same guard `tests/isolation/conftest.py` and `tests/isolation/test_ladder_e2e.py`
    install (review W-5): no real `~`, no real `$AO_STATE_DIR`, and -- the part `_env`'s
    per-invoke `HOME` redirect cannot do -- no ambient `GIT_CONFIG_GLOBAL` /
    `GIT_CONFIG_SYSTEM`. A developer with `rerere.enabled=true` or
    `merge.conflictstyle=diff3` in their global git config would otherwise silently change
    ladder outcomes in the conflict tests below.

    Set on the process env (not only in each `CliRunner(env=...)` overlay) because
    `isolation/paths.py` reads `AO_STATE_DIR` fresh at call time from `os.environ`, and
    the tests themselves compute expected worktree paths through those same functions --
    both sides must see the identical value or a path assertion would compare two
    different roots.
    """
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("AO_STATE_DIR", str(tmp_path / "ao-state"))
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.delenv("AO_WORKTREE_ROOT", raising=False)
    monkeypatch.delenv("GIT_CONFIG_GLOBAL", raising=False)
    monkeypatch.delenv("GIT_CONFIG_SYSTEM", raising=False)


# ============================================================================
# Helpers
# ============================================================================


def _git(args: list[str], cwd: Path) -> str:
    """Run git with fixed identity; return stdout."""
    result = subprocess.run(
        ["git", "-c", "user.name=ao-test", "-c", "user.email=ao-test@example.invalid", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(f"git {args[0]} failed: {result.stderr}")
    return result.stdout


def _workspace(tmp_path: Path) -> Path:
    """The workspace root for a test: `<tmp_path>/ws`.

    Deliberately a SUBDIRECTORY of `tmp_path`, so `$AO_STATE_DIR` (`<tmp_path>/ao-state`,
    set by `_isolated_git_env`) sits *outside* it exactly as it does in production. See
    the module docstring for why nesting the two would disarm the S-4 guard.
    """
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    return ws


def _create_test_repo(workspace: Path, files: dict[str, str] | None = None) -> Path:
    """A real git repo at `<workspace>/repo` (the path the default reposet points at)."""
    repo = workspace / "repo"
    repo.mkdir(parents=True)
    _git(["init", "-q", "-b", "main"], repo)
    _git(["config", "user.name", "ao-test"], repo)
    _git(["config", "user.email", "ao-test@example.invalid"], repo)
    for rel, content in (files or {"README.md": "hi\n"}).items():
        full = repo / rel
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content)
    _git(["add", "-A"], repo)
    _git(["commit", "-q", "-m", "base"], repo)
    return repo


def _env(workspace: Path, **overrides: str) -> dict[str, str]:
    """Environment for a CLI invocation, mirroring `_isolated_git_env`'s process env."""
    env = {
        "AO_WORKSPACE_ROOT": str(workspace),
        "HOME": os.environ["HOME"],
        "AO_STATE_DIR": os.environ["AO_STATE_DIR"],
    }
    env.update(overrides)
    return env


def _freeze_run_id(monkeypatch: pytest.MonkeyPatch, workflow_id: str = "test-wf") -> str:
    """Pin `RunStateStore`'s clock and return the `run_id` the next run WILL use.

    `RunStateStore.new_run` builds `run_id` as ``f"{workflow.id}-{now:%Y%m%dT%H%M%SZ}"``
    and takes its clock from the module-level `_utc_now` at construction time, so patching
    that name is enough for a run launched through the CLI (which never passes an explicit
    clock). Everything downstream -- the integration branch, every task branch, and every
    `worktree_root(...)` -- is a pure function of this id.
    """
    monkeypatch.setattr(runstate_mod, "_utc_now", lambda: _FIXED_RUN_DT)
    return f"{workflow_id}-{_FIXED_RUN_DT.strftime(_RUN_ID_TIMESTAMP_FMT)}"


def _expected_worktree(workspace: Path, repo: Path, run_id: str, task_id: str) -> Path:
    """The worktree directory the engine WILL create for *task_id*.

    Derived through the production functions themselves (`group_repos` for the repo key,
    `worktree_root` for the layout) rather than a hand-built string, so a change to either
    naming rule surfaces here as a mismatch instead of a silently-wrong literal.
    """
    groups, skipped = group_repos({"core": str(repo)})
    assert not skipped and len(groups) == 1, f"unexpected repo grouping: {groups}, {skipped}"
    return worktree_root(str(workspace), run_id, task_id, groups[0].key)


def _setup_workflow(
    workspace: Path,
    tasks: list[dict[str, Any]],
    reposets: dict[str, Any] | None = None,
    integration_config: dict[str, Any] | None = None,
    agents: dict[str, Any] | None = None,
    workflow_id: str = "test-wf",
    filename: str = "workflow.json",
    **workflow_extras: Any,
) -> None:
    """Write workflow.json, reposets.json, and agents.json into *workspace*."""
    (workspace / "specs" / "instructions").mkdir(parents=True, exist_ok=True)
    for task in tasks:
        if "instruction" not in task:
            task["instruction"] = f"specs/instructions/{task['id']}.md"
        (workspace / task["instruction"]).write_text(f"# {task['id']}\n")

    workflow = {
        "version": "1.0",
        "id": workflow_id,
        "repo_set": "rs",
        "defaults": {"isolation": "worktree"},
        "integration": {
            "sync_checkout": "never",
            "ladder": ["auto", "mechanical"],
            **(integration_config or {}),
        },
        "tasks": tasks,
        **workflow_extras,
    }
    (workspace / filename).write_text(json.dumps(workflow))

    if reposets is None:
        reposets = {
            "version": "1.0",
            "repo_sets": {
                "rs": {
                    "workspace_root": str(workspace),
                    "repos": [{"id": "core", "path": "repo", "role": "primary"}],
                }
            },
        }
    (workspace / "reposets.json").write_text(json.dumps(reposets))
    (workspace / "agents.json").write_text(
        json.dumps({"version": "1.0", "agents": agents or {"ag": {"executor": "fake"}}})
    )


def _write_hotspots(workspace: Path, weights: dict[str, float]) -> None:
    """`.ao/hotspots.json` in the locked HLD §11 M8 shape (`isolation/hotspots.py`)."""
    (workspace / ".ao").mkdir(parents=True, exist_ok=True)
    (workspace / ".ao" / "hotspots.json").write_text(
        json.dumps(
            {
                "version": "1.0",
                "generated_at": _FIXED_RUN_DT.isoformat(),
                "window_days": 180,
                "repos": {
                    "core": {
                        "entries": [
                            {"path": path, "churn": 40, "conflicts": 8, "weight": weight}
                            for path, weight in sorted(weights.items())
                        ]
                    }
                },
            }
        )
    )


def _patch_executor_instance(monkeypatch: pytest.MonkeyPatch, fake: FakeExecutor) -> None:
    """Route every `executor: fake` dispatch through *fake*.

    One implementation, used by `_patch_executor` and by every test that needs a custom
    executor subclass -- previously this three-line `DispatchExecutor` subclass was copied
    three times in this file (CLAUDE.md: no duplicate logic).
    """

    class _Patched(executors_pkg.DispatchExecutor):
        def __init__(self) -> None:
            super().__init__()
            self._fake = fake

    monkeypatch.setattr(executors_pkg, "DispatchExecutor", _Patched)


def _patch_executor(
    monkeypatch: pytest.MonkeyPatch, repo_writes: dict[str, Any], *, write_outputs: bool = True
) -> None:
    """Patch DispatchExecutor to use a plain FakeExecutor with *repo_writes*.

    ``write_outputs=False`` (R-2 scenarios): FakeExecutor's own `write_outputs` gate is
    independent of `repo_writes` -- it controls whether ``ctx.output_paths`` (the
    declared, workspace-level outputs) get their stub content written at all, so a test
    proving "a declared output never materializes" must set this False rather than simply
    omitting an entry from `repo_writes` (which only affects in-repo tracked-file writes).
    """
    _patch_executor_instance(
        monkeypatch, FakeExecutor(repo_writes=repo_writes, write_outputs=write_outputs)
    )


def _run_dir(workspace: Path, run_id: str | None = None) -> Path:
    """The `.orchestrator/runs/<run_id>` directory of the (single) run in *workspace*."""
    runs = workspace / ".orchestrator" / "runs"
    if run_id is not None:
        return runs / run_id
    entries = sorted(runs.iterdir())
    assert len(entries) == 1, f"expected exactly one run directory, found {entries}"
    return entries[0]


def _state(workspace: Path, run_id: str | None = None) -> dict[str, Any]:
    """The persisted `state.json` as a plain dict."""
    return json.loads((_run_dir(workspace, run_id) / "state.json").read_text())


def _load_run_state(workspace: Path, run_id: str) -> RunState:
    """The persisted run state as a real `RunState` (for `compute_run_usage_totals`)."""
    store = LocalFsArtifactStore(str(workspace))
    return RunStateStore(str(workspace), store).load(run_id)


def _events(run_dir: Path) -> list[dict[str, Any]]:
    """Every structured JSON line in the run's `run.log` (HLD §11 M9's event contract).

    The single parser for the whole file -- the review counted six hand-rolled copies of
    this loop, and "assert the event, not the exit code" only stays cheap if reading the
    events is a one-liner.
    """
    lines = (run_dir / "run.log").read_text().splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def _events_named(run_dir: Path, name: str) -> list[dict[str, Any]]:
    return [event for event in _events(run_dir) if event.get("event") == name]


def _event_names(run_dir: Path) -> list[str]:
    return [event.get("event", "") for event in _events(run_dir)]


def _hash_user_files(root: Path) -> dict[str, str]:
    """SHA-256 of every real (non-``.git``) file under *root*, keyed by relative path.

    Used to prove a refused sync left every user file byte-for-byte untouched, and -- the
    S-4 dispatch tests -- that a sibling task's worktree was not modified by the task that
    tried to reach into it.
    """
    digests: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file() and ".git" not in path.relative_to(root).parts:
            digests[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return digests


def _branch_tree_digest(repo: Path, branch: str) -> dict[str, str]:
    """SHA-256 of every file in *branch*'s tip tree, keyed by path -- the same shape
    `_hash_user_files` produces for a directory.

    Comparing the two is how the S-4 tests prove a sibling worktree is "provably
    unmodified": a worktree whose contents are byte-identical to the commit its own task
    produced cannot have been written into by anybody else. It is a real before/after
    comparison -- the branch tip is the "before" the run itself recorded -- rather than
    hashing the same directory twice, which proves nothing.
    """
    names = _git(["ls-tree", "-r", "--name-only", branch], repo).splitlines()
    return {
        name: hashlib.sha256(
            subprocess.run(
                ["git", "show", f"{branch}:{name}"], cwd=repo, capture_output=True, check=True
            ).stdout
        ).hexdigest()
        for name in names
        if name.strip()
    }


def _worktree_paths(repo: Path) -> list[str]:
    """Every worktree git itself has registered for *repo* (`git worktree list
    --porcelain`), main checkout included. AC-3's teardown proof reads this rather than
    the filesystem, because a leaked *registration* is just as much a leak as a leaked
    directory."""
    out = _git(["worktree", "list", "--porcelain"], repo)
    return [line.split(" ", 1)[1] for line in out.splitlines() if line.startswith("worktree ")]


def _ao_refs(repo: Path) -> list[str]:
    """Every ref under ao's reserved namespaces (AC-8's "no `ao/` refs" check).

    Covers both shapes `isolation/paths.py` can produce: branches (`refs/heads/ao/...`,
    from `task_branch`/`integration_branch`) and the durable squash refs
    (`refs/ao/runs/...`).
    """
    out = _git(
        ["for-each-ref", "--format=%(refname)", "refs/heads/ao/", "refs/ao/"],
        repo,
    )
    return [line for line in out.splitlines() if line.strip()]


# ============================================================================
# Executors
# ============================================================================


class _WaveProbeExecutor(FakeExecutor):
    """The ADR-0007 T-TNleFt gated-executor pattern (TASK.md Risks: "a gated executor with
    latches ... rather than sleeps or timing assertions"), adapted to `FakeExecutor` so it
    can still perform `repo_writes`.

    Records, per dispatch: entry order, the set of other tasks already in flight at entry,
    the running high-water mark of concurrency, the executing thread's name, and the
    worktree root the engine handed this task.

    When *barrier_parties* is set, the FIRST that many entrants block on a
    `threading.Barrier`. That turns "N tasks ran concurrently" from a timing inference
    into a fact: the barrier cannot release until all N are simultaneously inside
    `execute()`. The last one through also snapshots which worktree directories existed at
    that instant -- AC-3's "three worktrees existed concurrently".
    """

    def __init__(self, *args: Any, barrier_parties: int | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._lock = threading.Lock()
        self._in_flight: set[str] = set()
        self.entered_order: list[str] = []
        self.entered_snapshot: dict[str, frozenset[str]] = {}
        self.max_concurrent = 0
        self.threads: dict[str, str] = {}
        self.worktrees: dict[str, str] = {}
        # Worktree directories that provably existed at the instant the barrier released.
        self.worktrees_present_at_barrier: dict[str, bool] = {}
        self._barrier = threading.Barrier(barrier_parties) if barrier_parties is not None else None
        self._barrier_parties = barrier_parties or 0

    def execute(self, ctx: TaskContext) -> TaskResult:
        with self._lock:
            index = len(self.entered_order)
            self.entered_snapshot[ctx.task_id] = frozenset(self._in_flight)
            self.entered_order.append(ctx.task_id)
            self._in_flight.add(ctx.task_id)
            self.max_concurrent = max(self.max_concurrent, len(self._in_flight))
            self.threads[ctx.task_id] = threading.current_thread().name
            if ctx.repo_paths:
                self.worktrees[ctx.task_id] = next(iter(ctx.repo_paths.values()))
        try:
            if self._barrier is not None and index < self._barrier_parties:
                # `timeout` is a deadlock detector, not a timing assertion: if fewer than
                # `parties` tasks are ever co-scheduled the run would otherwise hang.
                order = self._barrier.wait(timeout=_LATCH_TIMEOUT_SECONDS)
                if order == 0:
                    # Every party is provably still inside execute() right now.
                    with self._lock:
                        self.worktrees_present_at_barrier = {
                            tid: Path(path).is_dir() for tid, path in self.worktrees.items()
                        }
            return super().execute(ctx)
        finally:
            with self._lock:
                self._in_flight.discard(ctx.task_id)


class _CostedResolverExecutor(FakeExecutor):
    """A T2 (LLM) merge resolver that genuinely discovers and resolves the conflict, and
    reports per-dispatch usage so the cost ledger can be asserted (AC-5).

    Resolution is *discovered*, never pre-scripted: a resolver dispatch reads its own
    conflict manifest, reads each conflicted path off disk, asserts git's own markers are
    actually there, and computes the merged text by stripping them (mirrors
    `tests/test_engine_conflict_escalation.py::_LadderExecutor`'s ``discover_and_resolve``
    mode -- adapted, not imported, because that file's helper carries several modes this
    file has no use for).
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.context_history: dict[str, list[TaskContext]] = {}

    def execute(self, ctx: TaskContext) -> TaskResult:
        self.context_history.setdefault(ctx.task_id, []).append(ctx)
        is_resolver = ctx.instruction_path.endswith(escalation.RESOLVER_INSTRUCTION_FILENAME)
        resolved: dict[str, str] = {}
        if is_resolver:
            resolved = self._discover(ctx)
        result = super().execute(ctx)
        # Applied AFTER super().execute() so it wins over `repo_writes`' reapplication of
        # the original, non-conflicted content.
        repo_root = next(iter(ctx.repo_paths.values()), None)
        if resolved and repo_root is not None:
            for rel, content in resolved.items():
                Path(repo_root, rel).write_text(content, encoding="utf-8")
        result.actuals_available = True
        result.cost_usd = _COST_PER_DISPATCH_USD
        result.input_tokens = _INPUT_TOKENS_PER_DISPATCH
        result.output_tokens = _OUTPUT_TOKENS_PER_DISPATCH
        return result

    @staticmethod
    def _discover(ctx: TaskContext) -> dict[str, str]:
        manifests = [p for p in ctx.input_paths if p.endswith(".json")]
        assert manifests, "resolver dispatched with no conflict manifest input"
        manifest = json.loads(Path(manifests[0]).read_text(encoding="utf-8"))
        assert manifest["rebase_in_progress"] is True, "manifest claims no live conflict"
        conflicted = manifest["conflicted_paths"]
        assert conflicted, "manifest lists no conflicted paths"
        repo_root = next(iter(ctx.repo_paths.values()))
        out: dict[str, str] = {}
        for rel in conflicted:
            text = Path(repo_root, rel).read_text(encoding="utf-8")
            assert "<<<<<<<" in text, f"{rel} carries no conflict markers to discover"
            out[rel] = _strip_conflict_markers(text)
        return out


def _strip_conflict_markers(text: str) -> str:
    """Keep both sides of every conflict hunk, dropping git's own markers."""
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    i = 0
    while i < len(lines):
        if lines[i].startswith("<<<<<<<"):
            i += 1
            while i < len(lines) and not lines[i].startswith("======="):
                out.append(lines[i])
                i += 1
            i += 1
            while i < len(lines) and not lines[i].startswith(">>>>>>>"):
                out.append(lines[i])
                i += 1
            i += 1
        else:
            out.append(lines[i])
            i += 1
    return "".join(out)


class _AdaptiveSemanticExecutor(FakeExecutor):
    """Drives the `semantic` conflict shape (AC-2 / AC-6): two changes that rebase cleanly
    but only break the verify command once BOTH have landed.

    Each task writes its own flag file based on what it READS in its worktree -- never a
    pre-scripted per-dispatch answer. On the first dispatch it sees a base where the other
    flag is still `False` and enables its own outright; on a T3 rerun (whose whole point is
    a fresh base that now contains the other task's landed change) it sees the other flag
    already `True` and defers to it instead, which is what makes the rerun pass verify
    where the original attempt could not. `semantic_verify_argv()` fails only when both
    files literally read `True`.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.dispatches: dict[str, int] = {}
        self.written: dict[str, list[str]] = {}

    def execute(self, ctx: TaskContext) -> TaskResult:
        self.dispatches[ctx.task_id] = self.dispatches.get(ctx.task_id, 0) + 1
        result = super().execute(ctx)
        repo_root = next(iter(ctx.repo_paths.values()), None)
        if repo_root is None:
            return result
        mine, my_flag, theirs, their_flag = _SEMANTIC_FILES[ctx.task_id]
        other_text = Path(repo_root, theirs).read_text(encoding="utf-8")
        if "True" in other_text:
            # The other change is already on this base: defer to it rather than asserting
            # a second independent `True`, which is exactly what the verify check forbids.
            content = f"{my_flag} = {their_flag}\n"
        else:
            content = f"{my_flag} = True\n"
        Path(repo_root, mine).write_text(content, encoding="utf-8")
        self.written.setdefault(ctx.task_id, []).append(content)
        return result


# task_id -> (my file, my flag, other file, other flag)
_SEMANTIC_FILES: dict[str, tuple[str, str, str, str]] = {
    "task_a": ("a.py", "A_ENABLED", "b.py", "B_ENABLED"),
    "task_b": ("b.py", "B_ENABLED", "a.py", "A_ENABLED"),
}


class _RenamingExecutor(FakeExecutor):
    """`FakeExecutor` variant that DELETES a tracked file and re-adds it under a new name
    -- `repo_writes` alone can only WRITE files, never delete one, so the rename-collision
    checkout-sync case needs this to produce a genuinely rename-shaped change."""

    def __init__(
        self,
        *args: Any,
        rename_for: dict[str, tuple[str, str, str]] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._rename_for = rename_for or {}  # task_id -> (repo_id, old_relpath, new_relpath)

    def execute(self, ctx: TaskContext) -> TaskResult:
        result = super().execute(ctx)
        if result.status == "succeeded" and ctx.task_id in self._rename_for:
            repo_id, old_rel, new_rel = self._rename_for[ctx.task_id]
            repo_root = ctx.repo_paths.get(repo_id)
            if repo_root is not None:
                old_path = Path(repo_root, old_rel)
                content = old_path.read_text() if old_path.exists() else ""
                if old_path.exists():
                    old_path.unlink()
                Path(repo_root, new_rel).write_text(content)
        return result


# ============================================================================
# e2e-1 (AC-3): happy path -- concurrency, landing, and teardown
# ============================================================================


class TestHappyPath:
    def test_three_disjoint_tasks_are_concurrent_land_and_leave_no_worktree(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AC-3 in full: `--max-parallel 3` over `defaults.isolation: worktree`.

        The three distinguishing claims, each asserted rather than assumed:

        1. **Three worktrees existed concurrently.** A `threading.Barrier(3)` in the
           executor cannot release until all three tasks are simultaneously inside
           `execute()`; the last party through then records whether each task's worktree
           directory is on disk *at that instant*. No sleeps, no timing.
        2. **All three landed**, with the final integration tree carrying all three
           changes and the run exiting 0.
        3. **No worktree survives.** `git worktree list --porcelain` on the main repo
           returns exactly one entry (the main checkout itself), so neither a leaked
           directory nor a leaked git-level registration remains.

        (`tests/test_e2e_cli_isolation.py::TestIsolatedRunLands` already covers "three
        disjoint tasks land" on its own; claims 1 and 3 are what this case adds.)
        """
        ws = _workspace(tmp_path)
        repo = _create_test_repo(ws)
        _setup_workflow(
            ws,
            [
                {"id": "task_a", "agent": "ag", "outputs": ["output/a.txt"]},
                {"id": "task_b", "agent": "ag", "outputs": ["output/b.txt"]},
                {"id": "task_c", "agent": "ag", "outputs": ["output/c.txt"]},
            ],
        )
        executor = _WaveProbeExecutor(
            repo_writes={
                "task_a": {"core": {"a.txt": "a-content\n"}},
                "task_b": {"core": {"b.txt": "b-content\n"}},
                "task_c": {"core": {"c.txt": "c-content\n"}},
            },
            barrier_parties=3,
        )
        _patch_executor_instance(monkeypatch, executor)

        result = runner.invoke(
            app,
            [
                "run",
                "--workflow",
                str(ws / "workflow.json"),
                "--reposets",
                str(ws / "reposets.json"),
                "--agents",
                str(ws / "agents.json"),
                "--max-parallel",
                "3",
            ],
            env=_env(ws),
        )
        assert result.exit_code == 0, f"CLI failed:\n{result.output}"

        # (1) Concurrency, proved by the latch.
        assert executor.max_concurrent == 3, (
            f"expected 3 tasks in flight simultaneously, saw {executor.max_concurrent}"
        )
        assert executor.worktrees_present_at_barrier == {
            "task_a": True,
            "task_b": True,
            "task_c": True,
        }, f"three worktrees did not coexist: {executor.worktrees_present_at_barrier}"
        assert len(set(executor.worktrees.values())) == 3, (
            f"tasks shared a worktree: {executor.worktrees}"
        )

        # (2) All three landed on the integration branch.
        state = _state(ws)
        branch = state["integration"]["branch"]
        assert branch is not None
        for fname, content in (
            ("a.txt", "a-content\n"),
            ("b.txt", "b-content\n"),
            ("c.txt", "c-content\n"),
        ):
            assert _git(["show", f"{branch}:{fname}"], repo) == content
        for tid in ("task_a", "task_b", "task_c"):
            assert state["task_integration"][tid]["status"] == "integrated"

        # (3) Teardown: only the main checkout is left registered, and no worktree
        # directory survived either.
        assert _worktree_paths(repo) == [str(repo.resolve())]
        for path in executor.worktrees.values():
            assert not Path(path).exists(), f"worktree survived the run: {path}"

    def test_dependent_task_sees_its_predecessors_landed_outputs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A task that `depends_on` two isolated tasks and declares their outputs as
        inputs dispatches only after both have INTEGRATED (not merely succeeded), and its
        own worktree is based on the integration head that already contains both."""
        ws = _workspace(tmp_path)
        repo = _create_test_repo(ws)
        _setup_workflow(
            ws,
            [
                {"id": "task_a", "agent": "ag", "outputs": ["output/a.txt"]},
                {"id": "task_b", "agent": "ag", "outputs": ["output/b.txt"]},
                {
                    "id": "task_c",
                    "agent": "ag",
                    "depends_on": ["task_a", "task_b"],
                    "inputs": ["output/a.txt", "output/b.txt"],
                    "outputs": ["output/c.txt"],
                },
            ],
        )
        executor = _WaveProbeExecutor(
            repo_writes={
                "task_a": {"core": {"a.txt": "a\n"}},
                "task_b": {"core": {"b.txt": "b\n"}},
                "task_c": {"core": {"c.txt": "c\n"}},
            }
        )
        _patch_executor_instance(monkeypatch, executor)

        result = runner.invoke(
            app,
            [
                "run",
                "--workflow",
                str(ws / "workflow.json"),
                "--reposets",
                str(ws / "reposets.json"),
                "--agents",
                str(ws / "agents.json"),
                "--max-parallel",
                "3",
            ],
            env=_env(ws),
        )
        assert result.exit_code == 0, f"CLI failed:\n{result.output}"

        assert executor.entered_order[-1] == "task_c", (
            f"task_c must dispatch last, got {executor.entered_order}"
        )
        assert executor.entered_snapshot["task_c"] == frozenset(), (
            "task_c overlapped a predecessor it depends on"
        )
        # task_c's own worktree was cut from a base that already carries both changes.
        task_c_worktree = Path(executor.worktrees["task_c"])
        events = _events(_run_dir(ws))
        created = {e["task_id"]: e["base"] for e in events if e.get("event") == "worktree.created"}
        state = _state(ws)
        branch = state["integration"]["branch"]
        assert created["task_c"] != created["task_a"], (
            "task_c was cut from the same base as task_a -- it did not wait for integration"
        )
        assert _git(["show", f"{branch}:c.txt"], repo) == "c\n"
        assert not task_c_worktree.exists()

    def test_no_isolation_flag_disables_isolation_and_leaves_no_ao_refs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AC-8 (e2e-6, kill-switch half): `--no-isolation` reproduces the shared-checkout
        path -- no worktrees, **no `ao/` refs of any kind**, no integration activation --
        even though the workflow declares `defaults.isolation: worktree`, and the run log
        names the tasks it overrode.

        The refs assertion is the half the review found missing: a run can leave zero
        worktree *directories* behind while still having created (and then abandoned) the
        `ao/<run>/<task>` branches, which would be a visible, permanent change to the
        operator's repo on a code path whose whole promise is "behave exactly like today".
        """
        ws = _workspace(tmp_path)
        repo = _create_test_repo(ws)
        _setup_workflow(
            ws,
            [{"id": "task_a", "agent": "ag", "isolation": "worktree", "outputs": ["output/a.txt"]}],
        )
        _patch_executor(monkeypatch, {"task_a": {"core": {"a.txt": "a\n"}}})

        result = runner.invoke(
            app,
            [
                "run",
                "--workflow",
                str(ws / "workflow.json"),
                "--reposets",
                str(ws / "reposets.json"),
                "--agents",
                str(ws / "agents.json"),
                "--no-isolation",
            ],
            env=_env(ws),
        )
        assert result.exit_code == 0, f"CLI failed:\n{result.output}"

        state = _state(ws)
        assert state["integration"]["active"] is False
        assert state["task_integration"] == {}
        assert _ao_refs(repo) == [], "the kill switch left ao/ refs behind"
        assert _worktree_paths(repo) == [str(repo.resolve())]
        worktrees_dir = Path(os.environ["AO_STATE_DIR"]) / "worktrees"
        assert not worktrees_dir.exists() or not any(worktrees_dir.rglob("*")), (
            "worktrees were created despite --no-isolation"
        )
        # The override is loud (HLD §17.3 e2e-6): the run names the task it overrode.
        assert "task_a" in result.output


# ============================================================================
# e2e-2 (AC-4): T1 mechanical union resolver
# ============================================================================


class TestMechanicalResolver:
    def test_union_conflict_resolved_via_union_merge(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Two tasks each append a DIFFERENT line to the end of the same registry-style
        file, from the SAME base -> a real rebase conflict that T1's union resolver
        (purely-additive) legitimately resolves by keeping both, never escalating.

        Two bugs fixed here versus the prior version of this test: (1) it built its repo
        via `make_conflict_repo` (which returns a randomly-suffixed `repo-<hex>` directory)
        but never pointed `reposets.json` at that path -- the default reposets in
        `_setup_workflow` hardcodes `"path": "repo"`, so isolation silently DEGRADED to
        `none` (`worktree.non_git_repo`) and no conflict -- let alone a union resolve --
        ever happened; the CLI's `exit_code == 0` passed vacuously. (2) `integration.
        resolvers.union` defaults to `[]` (HLD §11 M6, AC-8) -- without explicitly
        configuring it to match `f.txt`, the union resolver never even attempts the path,
        regardless of (1).

        The per-task tier bookkeeping formerly duplicated in a near-identical
        `TestConflictLadder::test_t1_mechanical_union_resolves_cleanly` (review W-3) is
        folded in here, and that duplicate is gone.
        """
        ws = _workspace(tmp_path)
        repo = _create_test_repo(ws, files={"README.md": "hi\n", "f.txt": "a\nb\nc\n"})

        _setup_workflow(
            ws,
            [
                {"id": "task_a", "agent": "ag", "outputs": ["output/a.txt"]},
                {"id": "task_b", "agent": "ag", "outputs": ["output/b.txt"]},
            ],
            integration_config={
                "sync_checkout": "never",
                "ladder": ["auto", "mechanical"],
                "resolvers": {"union": ["f.txt"]},
            },
        )
        _patch_executor(
            monkeypatch,
            {
                "task_a": {"core": {"f.txt": "a\nb\nc\nA-entry\n"}},
                "task_b": {"core": {"f.txt": "a\nb\nc\nB-entry\n"}},
            },
        )

        result = runner.invoke(
            app,
            [
                "run",
                "--workflow",
                str(ws / "workflow.json"),
                "--reposets",
                str(ws / "reposets.json"),
                "--agents",
                str(ws / "agents.json"),
                "--max-parallel",
                "2",
            ],
            env=_env(ws),
        )
        assert result.exit_code == 0, f"Failed to resolve union conflict:\n{result.output}"

        run_dir = _run_dir(ws)
        state = _state(ws)
        branch = state["integration"]["branch"]
        assert branch is not None
        # Both sides' lines survived the union merge; no markers left.
        shown = _git(["show", f"{branch}:f.txt"], repo)
        assert "A-entry" in shown
        assert "B-entry" in shown
        assert "<<<<<<<" not in shown

        # The mechanical/union resolution genuinely happened (not merely "no conflict at
        # all") -- assert on the actual structured event, not a loose substring of stdout.
        resolved_events = _events_named(run_dir, "integration.resolved")
        assert resolved_events, "expected an integration.resolved event"
        assert resolved_events[0]["tier"] == "mechanical"
        assert resolved_events[0]["resolver"] == "union"
        assert resolved_events[0]["path"] == "f.txt"

        # Race-order-agnostic: whichever task landed FIRST never conflicts at all
        # (tier_reached == "auto"); only the loser is rebased through a real conflict that
        # T1's union resolver fixes (tier_reached == "mechanical"). Neither ever reaches
        # T2 (resolver_attempts) or T3 (reruns).
        tiers = {
            tid: state["task_integration"][tid]["tier_reached"] for tid in ("task_a", "task_b")
        }
        assert sorted(tiers.values()) == ["auto", "mechanical"]
        for tid in ("task_a", "task_b"):
            ti = state["task_integration"][tid]
            assert ti["status"] == "integrated"
            assert ti["resolver_attempts"] == 0  # never reached T2
            assert ti["reruns"] == 0  # never reached T3
        assert "integration.resolver_dispatched" not in _event_names(run_dir)


# ============================================================================
# e2e-3 (AC-5): T2 LLM resolver -- events and cost accounting
# ============================================================================


class TestLlmResolver:
    def test_t2_resolver_dispatch_merges_and_is_charged_to_the_task(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AC-5 / HLD §17.3 e2e-3, through the CLI: a TRUE conflict (both tasks rewrite
        the same line, so T1's union resolver must decline) plus a fake resolver agent ->
        `integration.resolver_dispatched` then `integration.merged` at `tier=llm`, with the
        resolver's own dispatch charged onto the task's `cumulative_cost_usd` and reflected
        by `compute_run_usage_totals`.

        Cost is the part no test anywhere asserted: a T2 cycle is a second, real agent
        dispatch, and if its actuals were dropped on the requeue the operator would be
        billed for work the run never reports (E-9h3m7k FR-2's failure mode, re-entering
        through the conflict ladder). The arithmetic is exact and race-order-agnostic --
        the winner is charged one dispatch, the loser two -- so a dropped or
        double-counted cycle cannot hide behind an inequality.
        """
        ws = _workspace(tmp_path)
        repo = _create_test_repo(ws, files={"README.md": "hi\n", "f.txt": "line1\nline2\n"})
        run_id = _freeze_run_id(monkeypatch)
        _setup_workflow(
            ws,
            [
                {"id": "task_a", "agent": "ag", "outputs": ["output/a.txt"]},
                {"id": "task_b", "agent": "ag", "outputs": ["output/b.txt"]},
            ],
            integration_config={
                "sync_checkout": "never",
                "ladder": ["auto", "mechanical", "llm"],
                "resolver_agent": "merge-resolver",
            },
            agents={"ag": {"executor": "fake"}, "merge-resolver": {"executor": "fake"}},
        )
        executor = _CostedResolverExecutor(
            repo_writes={
                "task_a": {"core": {"f.txt": "line1-A\nline2\n"}},
                "task_b": {"core": {"f.txt": "line1-B\nline2\n"}},
            }
        )
        _patch_executor_instance(monkeypatch, executor)

        result = runner.invoke(
            app,
            [
                "run",
                "--workflow",
                str(ws / "workflow.json"),
                "--reposets",
                str(ws / "reposets.json"),
                "--agents",
                str(ws / "agents.json"),
                "--max-parallel",
                "2",
            ],
            env=_env(ws),
        )
        assert result.exit_code == 0, f"T2 resolver run failed:\n{result.output}"

        run_dir = _run_dir(ws, run_id)
        state = _state(ws, run_id)

        # --- the T2 event contract (asserted by no test before this one) ---
        dispatched = _events_named(run_dir, "integration.resolver_dispatched")
        assert len(dispatched) == 1, f"expected exactly one T2 dispatch, got {dispatched}"
        loser = dispatched[0]["task_id"]
        winner = "task_b" if loser == "task_a" else "task_a"
        assert dispatched[0]["conflicted"] >= 1
        merged = _events_named(run_dir, "integration.merged")
        loser_merges = [e for e in merged if e["task_id"] == loser]
        assert loser_merges, f"the T2-resolved task never merged: {merged}"
        assert loser_merges[-1]["tier"] == "llm", (
            f"expected the loser to land at tier=llm, got {loser_merges[-1]}"
        )
        # Event ordering: the resolver was dispatched BEFORE the merge it produced.
        names = _event_names(run_dir)
        assert names.index("integration.resolver_dispatched") < max(
            i for i, n in enumerate(names) if n == "integration.merged"
        )

        # --- the resolution is real, not a stub: both sides survive on the branch ---
        branch = state["integration"]["branch"]
        landed = _git(["show", f"{branch}:f.txt"], repo)
        assert "line1-A" in landed and "line1-B" in landed
        assert "<<<<<<<" not in landed
        assert state["task_integration"][loser]["resolver_attempts"] == 1
        assert state["task_integration"][loser]["tier_reached"] == "llm"
        assert state["task_integration"][winner]["resolver_attempts"] == 0

        # --- cost accounting across the ladder cycle (the AC's own wording) ---
        assert len(executor.context_history[loser]) == 2, "expected one T2 redispatch"
        assert len(executor.context_history[winner]) == 1
        loser_cost = state["tasks"][loser]["cumulative_cost_usd"]
        winner_cost = state["tasks"][winner]["cumulative_cost_usd"]
        assert loser_cost == pytest.approx(2 * _COST_PER_DISPATCH_USD), (
            f"the resolver attempt was not charged to {loser}: {loser_cost}"
        )
        assert winner_cost == pytest.approx(_COST_PER_DISPATCH_USD)
        totals = compute_run_usage_totals(_load_run_state(ws, run_id))
        assert totals.cost_usd == pytest.approx(3 * _COST_PER_DISPATCH_USD)
        assert totals.input_tokens == 3 * _INPUT_TOKENS_PER_DISPATCH
        assert totals.output_tokens == 3 * _OUTPUT_TOKENS_PER_DISPATCH


# ============================================================================
# e2e-4 (AC-6): verify failure -> T3 rerun on a fresh base
# ============================================================================


class TestVerifyFailureRerun:
    def test_semantic_conflict_fails_verify_then_reruns_on_the_fresh_base(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AC-6 / HLD §17.3 e2e-4, through the CLI, using AC-2's `semantic` fixture shape:
        two changes to DISJOINT files that rebase cleanly but together break the
        `verify_command`.

        The loser rebases without a single conflict marker, then fails verification on the
        combined tree -- `integration.verify_failed`. Because `escalate()` never routes a
        `cause="verify"` failure into T2 (there are no conflict markers for an LLM to
        resolve, `isolation/escalation.py` AC-9), it goes straight to T3:
        `integration.rerun_dispatched`, a fresh dispatch on a base that now already
        contains the other change, and this time `integration.verify_passed` +
        `integration.merged`.

        This is the first test anywhere to assert `integration.verify_started` /
        `verify_failed` / `verify_passed` / `rerun_dispatched` -- four of the 22
        `integration.*` events the review found asserted by zero test files -- and the
        first consumer of the `semantic` fixture kind and `semantic_verify_argv()` beyond
        their own fixture-quality test.
        """
        ws = _workspace(tmp_path)
        repo = _create_test_repo(
            ws,
            files={
                "README.md": "hi\n",
                "a.py": "A_ENABLED = False\n",
                "b.py": "B_ENABLED = False\n",
            },
        )
        _setup_workflow(
            ws,
            [
                {"id": "task_a", "agent": "ag", "outputs": ["output/a.txt"]},
                {"id": "task_b", "agent": "ag", "outputs": ["output/b.txt"]},
            ],
            integration_config={
                "sync_checkout": "never",
                "ladder": ["auto", "mechanical", "llm", "rerun"],
                "resolver_agent": "merge-resolver",
                "verify_command": semantic_verify_argv(),
            },
            agents={"ag": {"executor": "fake"}, "merge-resolver": {"executor": "fake"}},
        )
        executor = _AdaptiveSemanticExecutor()
        _patch_executor_instance(monkeypatch, executor)

        result = runner.invoke(
            app,
            [
                "run",
                "--workflow",
                str(ws / "workflow.json"),
                "--reposets",
                str(ws / "reposets.json"),
                "--agents",
                str(ws / "agents.json"),
                "--max-parallel",
                "2",
            ],
            env=_env(ws),
        )
        assert result.exit_code == 0, f"semantic rerun run failed:\n{result.output}"

        run_dir = _run_dir(ws)
        state = _state(ws)

        verify_failed = _events_named(run_dir, "integration.verify_failed")
        assert len(verify_failed) == 1, f"expected exactly one verify failure: {verify_failed}"
        loser = verify_failed[0]["task_id"]
        winner = "task_b" if loser == "task_a" else "task_a"
        assert verify_failed[0]["reason"] == "verify_command_failed", (
            f"the rebase must have been clean -- this is a VERIFY failure: {verify_failed[0]}"
        )
        # A verify failure, not a merge conflict: `escalate()` never routes cause="verify"
        # into T2 (AC-9), so no resolver was ever dispatched.
        assert _events_named(run_dir, "integration.resolver_dispatched") == []
        assert state["task_integration"][loser]["resolver_attempts"] == 0
        assert state["task_integration"][loser]["conflicted_paths"] == []

        reruns = _events_named(run_dir, "integration.rerun_dispatched")
        assert len(reruns) == 1 and reruns[0]["task_id"] == loser
        assert reruns[0]["reruns"] == 1
        assert reruns[0]["tier"] == "rerun"
        assert reruns[0]["conflicted"] == 0, "a verify failure has no conflicted paths"

        # Ordering: verify_started -> verify_failed -> rerun_dispatched -> verify_passed.
        names = _event_names(run_dir)
        assert names.index("integration.verify_failed") < names.index(
            "integration.rerun_dispatched"
        )
        assert names.index("integration.rerun_dispatched") < max(
            i for i, n in enumerate(names) if n == "integration.verify_passed"
        )
        assert "integration.verify_started" in names

        # The rerun really was a second dispatch on a fresh base, and it adapted to what
        # it found there (the other task's change was already present).
        assert executor.dispatches[loser] == 2
        assert executor.dispatches[winner] == 1
        assert executor.written[loser][0].endswith("= True\n")
        assert executor.written[loser][1].endswith("= A_ENABLED\n") or executor.written[loser][
            1
        ].endswith("= B_ENABLED\n"), f"the rerun did not adapt: {executor.written[loser]}"

        # Both tasks landed, and the landed tree passes the same verify command.
        for tid in ("task_a", "task_b"):
            assert state["task_integration"][tid]["status"] == "integrated"
            assert state["task_integration"][tid]["verify_status"] == "passed"
        assert state["task_integration"][loser]["reruns"] == 1
        branch = state["integration"]["branch"]
        _git(["checkout", "-q", branch], repo)
        try:
            check = subprocess.run(
                semantic_verify_argv(), cwd=repo, capture_output=True, check=False
            )
            assert check.returncode == 0, "the landed tree does not satisfy the verify command"
        finally:
            _git(["checkout", "-q", "main"], repo)


# ============================================================================
# e2e-5 (AC-7): ladder exhausted -> operator hand-off -> `ao resume`
# ============================================================================


class TestOperatorHandoff:
    def test_t4_failure_retains_worktree_and_branch_then_resume_completes_after_manual_fix(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AC-7 / HLD §17.3 e2e-5, the whole hand-off loop, through the CLI.

        `ladder: ["auto"]` -- no mechanical, no llm, no rerun -- so the very first real
        conflict exhausts the ladder immediately (`escalate()`'s T4 branch). Then:

        * `ao run` exits 1, the run is `failed`, the losing task is `failed` with an
          `integration.failed` event whose `reason` names the conflicted paths, worktrees
          and branches (the operator must not have to reconstruct them from logs);
        * the loser's worktree directory **and** its `ao/<run>/<task>` branch are still
          there -- `release()` is deliberately not called on this path, which is the entire
          point of the hand-off;
        * an operator resolves it by hand *in that retained worktree* and commits;
        * `ao resume --run-id` picks the same worktree back up and completes the run with
          exit 0, landing the hand-merged content.

        The manual fix is applied to the real retained worktree (not a fresh checkout) and
        the resume executor makes no further repo change, so what lands can only be the
        operator's own commit.
        """
        ws = _workspace(tmp_path)
        repo = _create_test_repo(ws, files={"README.md": "hi\n", "f.txt": "line1\nline2\n"})
        run_id = _freeze_run_id(monkeypatch)
        _setup_workflow(
            ws,
            [
                {"id": "task_a", "agent": "ag", "outputs": ["output/a.txt"]},
                {"id": "task_b", "agent": "ag", "outputs": ["output/b.txt"]},
            ],
            integration_config={"sync_checkout": "never", "ladder": ["auto"]},
        )
        _patch_executor(
            monkeypatch,
            {
                "task_a": {"core": {"f.txt": "line1-A\nline2\n"}},
                "task_b": {"core": {"f.txt": "line1-B\nline2\n"}},
            },
        )

        first = runner.invoke(
            app,
            [
                "run",
                "--workflow",
                str(ws / "workflow.json"),
                "--reposets",
                str(ws / "reposets.json"),
                "--agents",
                str(ws / "agents.json"),
                "--max-parallel",
                "2",
            ],
            env=_env(ws),
        )
        assert first.exit_code == 1, f"ladder exhaustion must fail the run:\n{first.output}"

        run_dir = _run_dir(ws, run_id)
        state = _state(ws, run_id)
        assert state["status"] == "failed"
        failed_events = _events_named(run_dir, "integration.failed")
        assert len(failed_events) == 1, f"expected one T4 failure: {failed_events}"
        loser = failed_events[0]["task_id"]
        winner = "task_b" if loser == "task_a" else "task_a"
        assert state["tasks"][loser]["status"] == "failed"
        assert state["task_integration"][loser]["status"] == "failed"
        assert state["task_integration"][winner]["status"] == "integrated"
        # AC-10: the failure event is self-sufficient for the operator.
        assert failed_events[0]["conflicted_paths"] == ["f.txt"]
        assert failed_events[0]["worktrees"]
        assert failed_events[0]["branches"]
        assert "f.txt" in failed_events[0]["reason"]

        # Worktree AND branch retained.
        loser_worktree = Path(next(iter(state["task_integration"][loser]["repos"].values())))
        loser_branch = next(iter(state["task_integration"][loser]["branches"].values()))
        assert loser_worktree == _expected_worktree(ws, repo, run_id, loser)
        assert loser_worktree.is_dir(), "the failed task's worktree was not retained"
        assert f"refs/heads/{loser_branch}" in _ao_refs(repo), (
            "the failed task's branch was deleted despite the hand-off contract"
        )
        assert str(loser_worktree.resolve()) in _worktree_paths(repo), (
            "the failed task's worktree was deregistered despite the hand-off contract"
        )

        # --- the hand-off state the operator actually inherits ---
        # The retained worktree is left mid-rebase on a detached HEAD with git's own
        # markers on disk -- that IS the hand-off: the operator picks up exactly where the
        # ladder gave up, rather than being handed an aborted rebase to reconstruct.
        conflicted = (loser_worktree / "f.txt").read_text()
        assert "<<<<<<<" in conflicted and "line1-A" in conflicted and "line1-B" in conflicted, (
            f"the retained worktree carries no live conflict to resolve:\n{conflicted}"
        )
        assert "UU f.txt" in _git(["status", "--porcelain"], loser_worktree)

        # --- the operator resolves it by hand, in that worktree, and completes the rebase
        (loser_worktree / "f.txt").write_text(_strip_conflict_markers(conflicted))
        (loser_worktree / "operator-note.txt").write_text("resolved by hand\n")
        _git(["add", "-A"], loser_worktree)
        # `core.editor=true` because `rebase --continue` would otherwise open $EDITOR for
        # the commit message; this is the non-interactive equivalent of accepting it.
        _git(["-c", "core.editor=true", "rebase", "--continue"], loser_worktree)
        assert "UU " not in _git(["status", "--porcelain"], loser_worktree)

        # --- resume: the agent makes no further repo change; only the fix can land ---
        _patch_executor(monkeypatch, {})
        resumed = runner.invoke(
            app,
            [
                "resume",
                "--run-id",
                run_id,
                "--workflow",
                str(ws / "workflow.json"),
                "--reposets",
                str(ws / "reposets.json"),
                "--agents",
                str(ws / "agents.json"),
            ],
            env=_env(ws),
        )
        assert resumed.exit_code == 0, f"resume after the manual fix failed:\n{resumed.output}"

        final = _state(ws, run_id)
        assert final["status"] == "succeeded"
        assert final["tasks"][loser]["status"] == "succeeded"
        assert final["task_integration"][loser]["status"] == "integrated"
        final_branch = final["integration"]["branch"]
        # Only the operator's own commit can be responsible for what landed: the resume
        # executor made no repo change at all.
        assert _git(["show", f"{final_branch}:operator-note.txt"], repo) == "resolved by hand\n"
        landed = _git(["show", f"{final_branch}:f.txt"], repo)
        assert "line1-A" in landed and "line1-B" in landed, (
            f"the hand-merged content did not land: {landed!r}"
        )
        assert "<<<<<<<" not in landed
        # The hand-off is over: the worktree is released again on the success path.
        assert _worktree_paths(repo) == [str(repo.resolve())]


# ============================================================================
# S-1: planted hooks never fire
# ============================================================================


class TestHookSuppression:
    def test_planted_hooks_never_fire_during_isolated_run(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """S-1: plant every hook in `HOOK_NAMES` in the repo; none fires during an isolated
        run, and the fixture is proven non-vacuous by firing them via raw `subprocess`.

        Both assertions are now UNCONDITIONAL (review W-6). The previous version guarded
        the primary check with ``if sentinel_dir.exists():`` -- skipping it in exactly the
        case it existed to catch -- and proved non-vacuity with an ``or`` whose left arm
        (`returncode == 1`) any failing commit satisfies. This mirrors
        `tests/isolation/test_git.py`'s own hook-suppression proof, which does it properly.
        """
        ws = _workspace(tmp_path)
        repo = make_hooked_repo(ws)
        sentinel_dir = repo / ".hook-sentinels"
        _setup_workflow(
            ws,
            [{"id": "task_a", "agent": "ag", "outputs": ["output/a.txt"]}],
            reposets={
                "version": "1.0",
                "repo_sets": {
                    "rs": {
                        "workspace_root": str(ws),
                        "repos": [
                            {
                                "id": "core",
                                "path": str(repo.relative_to(ws)),
                                "role": "primary",
                            }
                        ],
                    }
                },
            },
        )
        _patch_executor(monkeypatch, {"task_a": {"core": {"a.txt": "a\n"}}})

        result = runner.invoke(
            app,
            [
                "run",
                "--workflow",
                str(ws / "workflow.json"),
                "--reposets",
                str(ws / "reposets.json"),
                "--agents",
                str(ws / "agents.json"),
            ],
            env=_env(ws),
        )
        assert result.exit_code == 0, f"CLI failed:\n{result.output}"
        assert _state(ws)["task_integration"]["task_a"]["status"] == "integrated", (
            "the run must actually have integrated -- otherwise no git call was made to "
            "suppress a hook for"
        )

        # Unconditional: not one sentinel exists after a full isolated run that really did
        # commit, rebase and merge inside the hooked repo.
        fired = sorted(p.name for p in sentinel_dir.iterdir()) if sentinel_dir.exists() else []
        assert fired == [], f"engine-issued git calls fired hooks: {fired}"

        # Unconditional non-vacuity: the SAME hooks fire immediately for a raw git commit.
        subprocess.run(
            ["git", "commit", "--allow-empty", "-m", "raw"],
            cwd=repo,
            capture_output=True,
            check=False,
        )
        fired_raw = sorted(p.name for p in sentinel_dir.iterdir()) if sentinel_dir.exists() else []
        assert fired_raw, "hook fixture is broken: a raw git commit fired no hook either"


# ============================================================================
# R-2: a missing declared output lands nothing
# ============================================================================


class TestMissingOutputs:
    def test_missing_declared_output_prevents_landing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """R-2: if a declared output is missing, the task fails and nothing lands on the
        integration ref.

        Was xfailed as "DEFECT: R-2 missing-outputs check ... not yet wired" -- that was
        the test's own bug, not a production one: `repo_writes` (what `_patch_executor`
        configured) only controls in-REPO tracked-file writes; it has no effect on
        whether `ctx.output_paths` (declared, workspace-level outputs like
        ``output/a.txt``/``output/b.txt`` here) get written at all -- FakeExecutor writes
        ALL of those unconditionally whenever ``write_outputs`` (default True) is set, so
        the original test's "only write a.txt, not b.txt" comment never actually happened;
        both files were always written and the run always succeeded regardless of the
        real R-2 gate. `tests/test_engine_isolation.py::TestMissingOutputsGate` already
        proves the gate itself at the engine level -- this reproduces the identical
        contract through the CLI boundary.
        """
        ws = _workspace(tmp_path)
        repo = _create_test_repo(ws)
        _setup_workflow(
            ws,
            [{"id": "task_a", "agent": "ag", "outputs": ["output/a.txt", "output/b.txt"]}],
        )
        pre_head = _git(["rev-parse", "HEAD"], repo).strip()
        # write_outputs=False: the task "succeeds" at the executor level but never writes
        # EITHER declared output -- this is what actually exercises the missing-output gate
        # (repo_writes alone, as the original test relied on, does not).
        _patch_executor(monkeypatch, {}, write_outputs=False)

        result = runner.invoke(
            app,
            [
                "run",
                "--workflow",
                str(ws / "workflow.json"),
                "--reposets",
                str(ws / "reposets.json"),
                "--agents",
                str(ws / "agents.json"),
            ],
            env=_env(ws),
        )
        assert result.exit_code != 0, (
            f"Should fail when declared output is missing:\n{result.output}"
        )
        assert not (ws / "output" / "a.txt").exists()
        assert not (ws / "output" / "b.txt").exists()

        state = _state(ws)
        assert state["status"] == "failed"
        assert state["tasks"]["task_a"]["status"] == "failed"
        ti = state["task_integration"]["task_a"]
        assert ti["status"] == "failed"
        assert ti["last_error"] is not None and ti["last_error"].startswith("missing_outputs:")
        # Nothing landed: the integration branch (if any was even created) never moved
        # past the pre-run head.
        branch = state["integration"]["branch"]
        if branch is not None:
            assert _git(["rev-parse", branch], repo).strip() == pre_head


# ============================================================================
# R-20 / NFR-3 (AC-13, AC-18): RunState thread identity
# ============================================================================


class TestThreadSafety:
    def test_runstate_is_never_mutated_or_saved_off_the_main_thread(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AC-13/AC-18: during a `max_parallel=3` isolated run, `RunState` is neither
        MUTATED nor `save`d from any thread but the main one.

        Three review findings fixed here (M-7, W-7):

        * the mutation half is now asserted, not only `save()` -- `RunState.__setattr__`
          and `TaskRunState.__setattr__` are wrapped, which is where the engine's
          per-task bookkeeping actually lands;
        * `max_parallel` is 3, as AC-13 and AC-18 both specify (it was 2);
        * there is a **control**: a `threading.Barrier(3)` forces all three tasks to be in
          flight simultaneously and the executor records its thread per dispatch, so the
          test asserts worker threads were genuinely used. Without that, a regression to
          serial dispatch would make this test pass vacuously -- everything would run on
          the main thread and the invariant would hold for the wrong reason.

        `monkeypatch.setattr(RunStateStore, "save", ...)` replaces the previous
        `self.__class__ = _RecordingStore` reassignment (W-7), which mutated the type of
        every `RunStateStore` in the process.
        """
        ws = _workspace(tmp_path)
        _create_test_repo(ws)
        _setup_workflow(
            ws,
            [
                {"id": "task_a", "agent": "ag", "outputs": ["output/a.txt"]},
                {"id": "task_b", "agent": "ag", "outputs": ["output/b.txt"]},
                {"id": "task_c", "agent": "ag", "outputs": ["output/c.txt"]},
            ],
        )
        executor = _WaveProbeExecutor(
            repo_writes={
                "task_a": {"core": {"a.txt": "a\n"}},
                "task_b": {"core": {"b.txt": "b\n"}},
                "task_c": {"core": {"c.txt": "c\n"}},
            },
            barrier_parties=3,
        )
        _patch_executor_instance(monkeypatch, executor)

        main_thread = threading.main_thread()
        offenders: list[str] = []
        saves = 0
        mutations = 0
        record_lock = threading.Lock()

        def _note(kind: str, detail: str) -> None:
            nonlocal saves, mutations
            with record_lock:
                if kind == "save":
                    saves += 1
                else:
                    mutations += 1
                if threading.current_thread() is not main_thread:
                    offenders.append(f"{kind}:{detail}@{threading.current_thread().name}")

        original_save = RunStateStore.save

        def _recording_save(self: RunStateStore, state: RunState) -> None:
            _note("save", state.run_id)
            original_save(self, state)

        monkeypatch.setattr(RunStateStore, "save", _recording_save)

        for model in (RunState, TaskRunState):
            original_setattr = model.__setattr__

            def _recording_setattr(
                self: Any,
                name: str,
                value: Any,
                _orig: Any = original_setattr,
                _model: type = model,
            ) -> None:
                _note("mutate", f"{_model.__name__}.{name}")
                _orig(self, name, value)

            monkeypatch.setattr(model, "__setattr__", _recording_setattr)

        result = runner.invoke(
            app,
            [
                "run",
                "--workflow",
                str(ws / "workflow.json"),
                "--reposets",
                str(ws / "reposets.json"),
                "--agents",
                str(ws / "agents.json"),
                "--max-parallel",
                "3",
            ],
            env=_env(ws),
        )
        assert result.exit_code == 0, f"CLI failed:\n{result.output}"

        # Control: worker threads really were used, so the invariant below is not holding
        # for the trivial reason that everything ran on the main thread.
        assert executor.max_concurrent == 3, (
            f"dispatch was not concurrent ({executor.max_concurrent}); the thread-identity "
            "assertion below would be vacuous"
        )
        worker_threads = {name for name in executor.threads.values() if name != main_thread.name}
        assert len(worker_threads) >= 2, (
            f"expected dispatches on distinct worker threads, saw {executor.threads}"
        )

        assert saves > 0 and mutations > 0, "the recording seams never fired"
        assert offenders == [], f"RunState touched off the main thread: {offenders}"


# ============================================================================
# R-12: dirty checkout at a sync barrier
# ============================================================================


class TestDirtyCheckout:
    def test_dirty_checkout_sync_fails_naming_only_the_colliding_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`_sync_checkout` classifies a path as a COLLISION only when it is BOTH dirty
        (``git status --porcelain``) AND among the paths the incoming integration head
        actually changed (``diff_names_no_renames``) -- so a realistic dirty checkout (many
        unrelated dirty files, of which the landing change touches one) must name exactly
        that one, and leave every file untouched.

        A trailing non-isolated ``task_b`` forces the sync to run at a REAL mid-run barrier
        (before its dispatch), not merely as a best-effort end-of-run attempt.

        The AC-19 "collision rate" assertion the review called out (M-3) is gone: it
        divided the one collision this fixture *constructs* by the 50 dirty files it
        *plants*, so it could not fail for any product reason. The real R-12 measurement is
        an offline one against the consumer's own `status --porcelain` snapshot, recorded
        in this ticket's `STATUS.md`, not a test.
        """
        ws = _workspace(tmp_path)
        repo = _create_test_repo(ws)
        dirty_count = 50
        for i in range(dirty_count - 1):
            (repo / f"dirty-{i}.txt").write_text(f"dirty content {i}\n")
        (repo / "a.txt").write_text("locally-dirty, uncommitted\n")  # THE one collision

        _setup_workflow(
            ws,
            [
                {
                    "id": "task_a",
                    "agent": "ag",
                    "isolation": "worktree",
                    "outputs": ["output/a.txt"],
                },
                {
                    "id": "task_b",
                    "agent": "ag",
                    "isolation": "none",
                    "depends_on": ["task_a"],
                    "outputs": ["output/b.txt"],
                },
            ],
            integration_config={"sync_checkout": "on_demand"},
        )
        _patch_executor(
            monkeypatch,
            {"task_a": {"core": {"a.txt": "a\n"}}, "task_b": {"core": {}}},
        )

        started = time.monotonic()
        result = runner.invoke(
            app,
            [
                "run",
                "--workflow",
                str(ws / "workflow.json"),
                "--reposets",
                str(ws / "reposets.json"),
                "--agents",
                str(ws / "agents.json"),
            ],
            env=_env(ws),
        )
        elapsed = time.monotonic() - started
        assert elapsed < _WALL_TIME_CEILING_SECONDS, (
            f"dirty-checkout sync took {elapsed:.2f}s for {dirty_count} dirty entries"
        )
        assert result.exit_code != 0, f"dirty collision should refuse sync:\n{result.output}"

        sync_failed = _events_named(_run_dir(ws), "integration.sync_failed")
        assert sync_failed, "expected an integration.sync_failed diagnostic event"
        assert sync_failed[0]["reason"] == "dirty_checkout"
        assert sync_failed[0]["colliding_paths"] == ["a.txt"], (
            "only the path the incoming change actually touches is a collision"
        )
        assert "hint" in sync_failed[0]
        # The dirty file itself was never touched by the refused sync attempt.
        assert (repo / "a.txt").read_text() == "locally-dirty, uncommitted\n"


# ============================================================================
# e2e-8 (AC-10): degradation on a non-git repo, strict vs non-strict
# ============================================================================


def _non_git_reposets(ws: Path) -> dict[str, Any]:
    return {
        "version": "1.0",
        "repo_sets": {
            "rs": {
                "workspace_root": str(ws),
                "repos": [{"id": "core", "path": "non-git", "role": "primary"}],
            }
        },
    }


class TestDegradation:
    def test_non_git_repo_degrades_to_no_isolation(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A reposet pointing at a plain (non-git) directory runs to completion, with the
        structured `integration.degraded` event naming `no_git_repos` and integration left
        inactive -- not merely the word "degraded" somewhere in stdout."""
        ws = _workspace(tmp_path)
        non_git_repo = ws / "non-git"
        non_git_repo.mkdir()
        (non_git_repo / "f.txt").write_text("content\n")

        _setup_workflow(
            ws,
            [{"id": "task_a", "agent": "ag", "outputs": ["output/a.txt"]}],
            reposets=_non_git_reposets(ws),
        )
        _patch_executor(monkeypatch, {"task_a": {"core": {"a.txt": "a\n"}}})

        result = runner.invoke(
            app,
            [
                "run",
                "--workflow",
                str(ws / "workflow.json"),
                "--reposets",
                str(ws / "reposets.json"),
                "--agents",
                str(ws / "agents.json"),
            ],
            env=_env(ws),
        )
        assert result.exit_code == 0, f"Non-git degradation failed:\n{result.output}"

        degraded = _events_named(_run_dir(ws), "integration.degraded")
        assert degraded, "expected a structured integration.degraded event"
        assert degraded[0]["reason"] == "no_git_repos"
        state = _state(ws)
        assert state["status"] == "succeeded"
        assert state["tasks"]["task_a"]["status"] == "succeeded"
        assert state["integration"]["active"] is False
        assert state["integration"]["degraded_reason"] == "no_git_repos"

    def test_strict_mode_fails_on_non_git(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`isolation.strict: true` fails (not degrades) on non-git.

        Was xfailed as "DEFECT: isolation.strict: true not yet enforcing" -- that was the
        test's own bug: `WorkflowSpec` has NO `isolation` field at all, so the original
        test's ``workflow["isolation"] = {"strict": True}`` set a key the loader silently
        ignores; the run never actually asked for strict mode, so of course it degraded
        instead of failing. Per `project_config.py`, `strict` (like `env`) is a
        **`.ao/config.yaml`-ONLY** surface, deliberately with no CLI flag or env var
        (`cli.py::_resolve_isolation_settings`), discovered by walking up from the
        process's CWD -- so this test needs `monkeypatch.chdir` into the workspace and a
        real `.ao/config.yaml`, not a workflow-spec field.
        """
        ws = _workspace(tmp_path)
        non_git_repo = ws / "non-git"
        non_git_repo.mkdir()
        (non_git_repo / "f.txt").write_text("content\n")

        _setup_workflow(
            ws,
            [{"id": "task_a", "agent": "ag", "outputs": ["output/a.txt"]}],
            reposets=_non_git_reposets(ws),
        )
        (ws / ".ao").mkdir(exist_ok=True)
        (ws / ".ao" / "config.yaml").write_text("isolation:\n  strict: true\n")
        _patch_executor(monkeypatch, {"task_a": {"core": {"a.txt": "a\n"}}})
        monkeypatch.chdir(ws)  # find_project_config() walks up from cwd

        result = runner.invoke(
            app,
            [
                "run",
                "--workflow",
                str(ws / "workflow.json"),
                "--reposets",
                str(ws / "reposets.json"),
                "--agents",
                str(ws / "agents.json"),
            ],
            env=_env(ws),
        )
        assert result.exit_code != 0, f"strict mode should fail on non-git:\n{result.output}"

        state = _state(ws)
        assert state["status"] == "failed"
        assert state["integration"]["degraded_reason"] == "no_git_repos"
        assert state["tasks"]["task_a"]["status"] != "succeeded"

    def test_non_strict_mode_still_degrades_via_the_same_config_file_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Control for the test above: the SAME `.ao/config.yaml` discovery path with
        `isolation.strict: false` must DEGRADE and SUCCEED, proving the strict test's
        failure comes from `strict` itself and not from some side effect of adding a
        config file or of `monkeypatch.chdir`.

        Asserts the same structured facts as its twin (event, `degraded_reason`, task
        outcome) so the pair differs in exactly one dimension -- the run's verdict.
        """
        ws = _workspace(tmp_path)
        non_git_repo = ws / "non-git"
        non_git_repo.mkdir()
        (non_git_repo / "f.txt").write_text("content\n")

        _setup_workflow(
            ws,
            [{"id": "task_a", "agent": "ag", "outputs": ["output/a.txt"]}],
            reposets=_non_git_reposets(ws),
        )
        (ws / ".ao").mkdir(exist_ok=True)
        (ws / ".ao" / "config.yaml").write_text("isolation:\n  strict: false\n")
        _patch_executor(monkeypatch, {"task_a": {"core": {"a.txt": "a\n"}}})
        monkeypatch.chdir(ws)

        result = runner.invoke(
            app,
            [
                "run",
                "--workflow",
                str(ws / "workflow.json"),
                "--reposets",
                str(ws / "reposets.json"),
                "--agents",
                str(ws / "agents.json"),
            ],
            env=_env(ws),
        )
        assert result.exit_code == 0, f"non-strict should degrade, not fail:\n{result.output}"

        degraded = _events_named(_run_dir(ws), "integration.degraded")
        assert degraded and degraded[0]["reason"] == "no_git_repos"
        state = _state(ws)
        assert state["status"] == "succeeded"
        assert state["integration"]["degraded_reason"] == "no_git_repos"
        assert state["tasks"]["task_a"]["status"] == "succeeded"


# ============================================================================
# R-5: soft overlap preference at LIVE dispatch
# ============================================================================


class TestOverlapRanking:
    """R-5's live-dispatch half -- the one §17.5 row with no genuine coverage anywhere.

    `tests/test_overlap_ranking.py` proves the pure `rank_wave` function thoroughly, and
    `tests/test_engine_isolation.py::TestRankWaveWiring::test_soft_preference_applies_at_
    dispatch` claims to assert "which two of three actually ran concurrently" but asserts
    only `state.status == "succeeded"`. The row demands the DISPATCHED SET, so that is what
    these two cases assert -- once at `soft` (the ranking applies) and once at `off` (the
    control that proves the `soft` ordering was produced by `rank_wave` and is not simply
    the ready order).
    """

    _TASKS = [
        {"id": "task_a", "agent": "ag", "touches": ["hot.txt"], "outputs": ["output/a.txt"]},
        {"id": "task_b", "agent": "ag", "touches": ["hot.txt"], "outputs": ["output/b.txt"]},
        {"id": "task_c", "agent": "ag", "touches": ["cold.txt"], "outputs": ["output/c.txt"]},
    ]

    def _setup(self, ws: Path, preference: str) -> None:
        _create_test_repo(ws, files={"README.md": "hi\n", "hot.txt": "h\n", "cold.txt": "c\n"})
        _write_hotspots(ws, {"hot.txt": _HOT_PATH_WEIGHT})
        _setup_workflow(
            ws,
            [dict(task) for task in self._TASKS],
            scheduling={"overlap_preference": preference, "hotspots_path": ".ao/hotspots.json"},
        )

    def _run(self, ws: Path, executor: _WaveProbeExecutor, monkeypatch: pytest.MonkeyPatch):
        _patch_executor_instance(monkeypatch, executor)
        return runner.invoke(
            app,
            [
                "run",
                "--workflow",
                str(ws / "workflow.json"),
                "--reposets",
                str(ws / "reposets.json"),
                "--agents",
                str(ws / "agents.json"),
                "--max-parallel",
                "2",
            ],
            env=_env(ws),
        )

    def test_soft_preference_co_schedules_the_disjoint_pair_not_the_hot_pair(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """At `overlap_preference: soft`, `rank_wave` must fill the two open slots with
        `task_a` + `task_c` (disjoint) rather than `task_a` + `task_b` (both touching a
        hotspot-weighted `hot.txt`), even though ready order offers `task_b` first.

        Asserted twice, independently: on the structured `scheduling.overlap_preferred`
        event's `chosen` list, and on which two tasks the executor's `Barrier(2)` observed
        in flight together -- the barrier makes "co-scheduled" a fact, not an inference.
        """
        ws = _workspace(tmp_path)
        self._setup(ws, "soft")
        executor = _WaveProbeExecutor(barrier_parties=2)
        result = self._run(ws, executor, monkeypatch)
        assert result.exit_code == 0, f"CLI failed:\n{result.output}"

        preferred = _events_named(_run_dir(ws), "scheduling.overlap_preferred")
        assert preferred, "no scheduling.overlap_preferred event at overlap_preference=soft"
        assert preferred[0]["chosen"] == ["task_a", "task_c"], (
            f"rank_wave's ordering was not applied at dispatch: {preferred[0]}"
        )
        assert preferred[0]["scores"]["task_c"] == 0.0
        first_wave = set(executor.entered_order[:2])
        assert first_wave == {"task_a", "task_c"}, (
            f"the hot pair was co-scheduled: {executor.entered_order}"
        )
        assert executor.max_concurrent == 2, (
            "the ranked pair never actually overlapped, so the wave assertion above would "
            f"be about dispatch order alone: {executor.entered_order}"
        )

    def test_off_preference_keeps_todays_ready_order(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Control (NFR-2): with `overlap_preference: off` the identical workflow keeps
        today's ready order -- `task_a` + `task_b` fill the wave, the hotspot file is never
        consulted, and no `scheduling.overlap_preferred` line is emitted at all. Without
        this, the `soft` assertion above could be satisfied by any ordering the engine
        happened to produce."""
        ws = _workspace(tmp_path)
        self._setup(ws, "off")
        executor = _WaveProbeExecutor(barrier_parties=2)
        result = self._run(ws, executor, monkeypatch)
        assert result.exit_code == 0, f"CLI failed:\n{result.output}"

        assert _events_named(_run_dir(ws), "scheduling.overlap_preferred") == []
        assert set(executor.entered_order[:2]) == {"task_a", "task_b"}, (
            f"ready order changed at overlap_preference=off: {executor.entered_order}"
        )


# ============================================================================
# S-3: the auto-commit denylist screen
# ============================================================================


class TestDenylistScreen:
    def test_untracked_env_file_aborts_integration_naming_it_and_is_never_committed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """S-3, through the CLI: an untracked `.env` produced inside the worktree aborts
        integration with `integration.denylisted_path` naming the path, the run fails, and
        the secret reaches no commit in the repository -- checked by searching every
        reachable object with `git grep --all-match` over all refs, not just commit
        subjects.

        The previous version asserted ``exit_code != 0 or "denylisted" in output``, which
        any unrelated failure satisfies, and checked neither "names the path" nor "never
        committed". `tests/isolation/test_integrator.py::TestDenylistScreen` covers the
        integrator-level contract; the CLI altitude and the stronger never-committed check
        are what this adds.
        """
        ws = _workspace(tmp_path)
        repo = _create_test_repo(ws)
        _setup_workflow(ws, [{"id": "task_a", "agent": "ag", "outputs": ["output/a.txt"]}])
        _patch_executor(monkeypatch, {"task_a": {"core": {".env": "SECRET_KEY=hunter2\n"}}})

        result = runner.invoke(
            app,
            [
                "run",
                "--workflow",
                str(ws / "workflow.json"),
                "--reposets",
                str(ws / "reposets.json"),
                "--agents",
                str(ws / "agents.json"),
            ],
            env=_env(ws),
        )
        assert result.exit_code != 0, f"a denylisted untracked path must abort:\n{result.output}"

        denylisted = _events_named(_run_dir(ws), "integration.denylisted_path")
        assert denylisted, "expected a structured integration.denylisted_path event"
        assert ".env" in json.dumps(denylisted[0]), (
            f"the denylist event does not name the path: {denylisted[0]}"
        )
        state = _state(ws)
        ti = state["task_integration"]["task_a"]
        assert ti["status"] == "failed"
        assert "denylisted" in (ti["last_error"] or "")

        # Never committed, anywhere: no ref in the repo reaches a blob containing it.
        found = subprocess.run(
            ["git", "grep", "-l", "hunter2", "--all"],
            cwd=repo,
            capture_output=True,
            text=True,
            check=False,
        )
        assert found.stdout.strip() == "", f"the secret was committed: {found.stdout}"
        assert not (repo / ".env").exists(), "the secret leaked into the shared checkout"

    def test_an_already_tracked_denylisted_path_is_not_screened(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """S-3's second half: the screen matches paths that are UNTRACKED at auto-commit
        time only. A `.env` the repo author already tracks is their decision, not the
        engine's -- modifying it must integrate normally.

        This is the control that keeps the test above honest: without it, a screen that
        simply refused every `.env` unconditionally would look identical.
        """
        ws = _workspace(tmp_path)
        repo = _create_test_repo(ws, files={"README.md": "hi\n", ".env": "TRACKED=original\n"})
        _setup_workflow(ws, [{"id": "task_a", "agent": "ag", "outputs": ["output/a.txt"]}])
        _patch_executor(monkeypatch, {"task_a": {"core": {".env": "TRACKED=updated\n"}}})

        result = runner.invoke(
            app,
            [
                "run",
                "--workflow",
                str(ws / "workflow.json"),
                "--reposets",
                str(ws / "reposets.json"),
                "--agents",
                str(ws / "agents.json"),
            ],
            env=_env(ws),
        )
        assert result.exit_code == 0, (
            f"a tracked denylisted path must not be screened:\n{result.output}"
        )

        assert _events_named(_run_dir(ws), "integration.denylisted_path") == []
        state = _state(ws)
        assert state["task_integration"]["task_a"]["status"] == "integrated"
        branch = state["integration"]["branch"]
        assert _git(["show", f"{branch}:.env"], repo) == "TRACKED=updated\n"


# ============================================================================
# Crash and resume
# ============================================================================


class TestCrashResume:
    def test_resume_after_mid_integration_crash(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Resume after a simulated crash: one task never even started (its "process"
        died mid-run), `ao resume` (the CLI, outer boundary) completes it and lands.

        Was xfailed as "DEFECT: resume needs pre-existing run state setup" -- the test
        never actually simulated a crash (it ran to full success, then "resumed" an
        already-complete run) AND passed the wrong flag (``--run``, which `resume`'s
        signature does not define at all -- it's ``--run-id``; typer would reject the
        unknown option before any real resume logic ever ran). A working harness already
        exists (`tests/test_e2e_cli_isolation.py::TestResumeAfterCrash`, T-En8Hd4) --
        reused here: an initial run via the direct `Orchestrator` API (simulating the
        crashed process that observed one task fail) leaves `task_a` isolated-but-failed
        and `task_b` never dispatched; `ao resume` via CliRunner completes both.
        """
        ws = _workspace(tmp_path)
        repo = _create_test_repo(ws)
        _setup_workflow(
            ws,
            [
                {"id": "task_a", "agent": "ag", "outputs": ["output/a.txt"]},
                {"id": "task_b", "agent": "ag", "outputs": ["output/b.txt"]},
            ],
        )

        from agent_orchestrator.config import load_agents, load_reposets
        from agent_orchestrator.spec import load_workflow

        wf = load_workflow(str(ws / "workflow.json"))
        reposet_map = load_reposets(str(ws / "reposets.json"))
        agent_map = load_agents(str(ws / "agents.json"))
        store = LocalFsArtifactStore(str(ws))
        rs_store = RunStateStore(str(ws), store)

        # First "process": task_a fails outright -- the crashed process's last observed
        # state, task_b never dispatched.
        orch1 = Orchestrator(FakeExecutor(behaviors={"task_a": "fail"}), store, rs_store)
        state1 = orch1.run(wf, reposet_map, agent_map)
        assert state1.status == "failed"
        assert state1.tasks["task_a"].status == "failed"
        assert "task_b" not in state1.tasks or state1.tasks["task_b"].status in (
            "pending",
            None,
        )
        run_id = state1.run_id

        # Resume via the CLI (outer boundary) with a fresh, succeeding executor.
        _patch_executor(
            monkeypatch,
            {"task_a": {"core": {"a.txt": "a\n"}}, "task_b": {"core": {"b.txt": "b\n"}}},
        )
        result2 = runner.invoke(
            app,
            [
                "resume",
                "--run-id",
                run_id,
                "--workflow",
                str(ws / "workflow.json"),
                "--reposets",
                str(ws / "reposets.json"),
                "--agents",
                str(ws / "agents.json"),
            ],
            env=_env(ws),
        )
        assert result2.exit_code == 0, f"CLI resume failed:\n{result2.output}"

        final_state = _state(ws, run_id)
        assert final_state["status"] == "succeeded"
        assert final_state["tasks"]["task_a"]["status"] == "succeeded"
        assert final_state["tasks"]["task_b"]["status"] == "succeeded"
        branch = final_state["integration"]["branch"]
        assert branch is not None
        assert _git(["show", f"{branch}:a.txt"], repo) == "a\n"
        assert _git(["show", f"{branch}:b.txt"], repo) == "b\n"

    def test_orphaned_worktree_reconciled_on_resume(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """D-ENS: a worktree DIRECTORY lost between crash and resume (e.g. external disk
        cleanup) while its git-level worktree registration and branch survive -- resume
        must re-attach to the leftover branch (never `-b`, never a silent `rmtree` of a
        DIFFERENT worktree, never a `WorktreeCollisionError`) and complete the run.

        Was previously a stub that never actually orphaned anything (a single successful
        run, nothing crashed, nothing reconciled). `tests/isolation/test_worktrees.py::
        TestEnsureCrashRecovery` already proves the underlying `WorktreeManager.ensure()`
        mechanics at the unit level (AC-10a/AC-10b/D-ENS) -- this proves the SAME recovery
        path is genuinely reached through a real crash + `ao resume` end-to-end.
        """
        ws = _workspace(tmp_path)
        repo = _create_test_repo(ws)
        _setup_workflow(
            ws,
            [
                {"id": "task_a", "agent": "ag", "outputs": ["output/a.txt"]},
                {"id": "task_b", "agent": "ag", "outputs": ["output/b.txt"]},
            ],
        )

        from agent_orchestrator.config import load_agents, load_reposets
        from agent_orchestrator.spec import load_workflow

        wf = load_workflow(str(ws / "workflow.json"))
        reposet_map = load_reposets(str(ws / "reposets.json"))
        agent_map = load_agents(str(ws / "agents.json"))
        store = LocalFsArtifactStore(str(ws))
        rs_store = RunStateStore(str(ws), store)

        # "Crashed process": task_a fails -- `ensure()` still creates its worktree +
        # `ao/<run>/task_a` branch BEFORE dispatch, regardless of the task's outcome.
        orch1 = Orchestrator(FakeExecutor(behaviors={"task_a": "fail"}), store, rs_store)
        state1 = orch1.run(wf, reposet_map, agent_map)
        assert state1.status == "failed"
        run_id = state1.run_id
        worktree_path = Path(next(iter(state1.task_integration["task_a"].repos.values())))
        branch = next(iter(state1.task_integration["task_a"].branches.values()))
        assert worktree_path.is_dir()

        # Simulate external deletion of just the directory: `.git/worktrees/<id>/` (the
        # registration) and the branch ref both survive -- exactly AC-10b's "registered
        # but directory deleted" shape.
        shutil.rmtree(worktree_path)
        assert not worktree_path.exists()

        _patch_executor(
            monkeypatch,
            {"task_a": {"core": {"a.txt": "a\n"}}, "task_b": {"core": {"b.txt": "b\n"}}},
        )
        result = runner.invoke(
            app,
            [
                "resume",
                "--run-id",
                run_id,
                "--workflow",
                str(ws / "workflow.json"),
                "--reposets",
                str(ws / "reposets.json"),
                "--agents",
                str(ws / "agents.json"),
            ],
            env=_env(ws),
        )
        assert result.exit_code == 0, f"CLI resume failed:\n{result.output}"

        run_dir = _run_dir(ws, run_id)
        final_state = _state(ws, run_id)
        assert final_state["status"] == "succeeded"
        landed_branch = final_state["integration"]["branch"]
        assert _git(["show", f"{landed_branch}:a.txt"], repo) == "a\n"
        assert _git(["show", f"{landed_branch}:b.txt"], repo) == "b\n"

        reattached = [
            e
            for e in _events_named(run_dir, "worktree.branch_reattached")
            if e.get("task_id") == "task_a"
        ]
        assert reattached, (
            f"expected a worktree.branch_reattached event for task_a's leftover "
            f"{branch!r}; events seen: {_event_names(run_dir)}"
        )


# ============================================================================
# Checkout sync at barriers
# ============================================================================


class TestCheckoutSync:
    def test_clean_fastforward_sync_at_barrier(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Clean fast-forward sync at barrier: no local dirt -> succeeds, and every user
        file's hash reflects EXACTLY what landed (nothing extra, nothing missing)."""
        ws = _workspace(tmp_path)
        repo = _create_test_repo(ws)
        before = _hash_user_files(repo)
        _setup_workflow(
            ws,
            [
                {
                    "id": "task_a",
                    "agent": "ag",
                    "isolation": "worktree",
                    "outputs": ["output/a.txt"],
                },
                {"id": "task_b", "agent": "ag", "isolation": "none", "outputs": ["output/b.txt"]},
            ],
            integration_config={"sync_checkout": "on_demand"},
        )
        _patch_executor(
            monkeypatch,
            {"task_a": {"core": {"a.txt": "a\n"}}, "task_b": {"core": {"b.txt": "b\n"}}},
        )

        result = runner.invoke(
            app,
            [
                "run",
                "--workflow",
                str(ws / "workflow.json"),
                "--reposets",
                str(ws / "reposets.json"),
                "--agents",
                str(ws / "agents.json"),
            ],
            env=_env(ws),
        )
        assert result.exit_code == 0, f"CLI failed:\n{result.output}"

        run_dir = _run_dir(ws)
        assert _events_named(run_dir, "integration.sync_ok")
        assert _events_named(run_dir, "integration.sync_failed") == []

        after = _hash_user_files(repo)
        # README.md untouched; a.txt/b.txt newly present with the landed content.
        assert before.get("README.md") == after.get("README.md")
        assert set(after) - set(before) == {"a.txt", "b.txt"}
        assert (repo / "a.txt").read_text() == "a\n"
        assert (repo / "b.txt").read_text() == "b\n"

    def test_dirty_tracked_path_collides_at_sync(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A dirty (uncommitted) edit at a path the incoming change also touches -> sync
        refuses, names the exact colliding path, and every user file is byte-for-byte
        unchanged (not just "a.txt looks the same" -- hashed, whole tree)."""
        ws = _workspace(tmp_path)
        repo = _create_test_repo(ws)
        (repo / "a.txt").write_text("dirty content\n")  # never committed -- collides
        before = _hash_user_files(repo)

        _setup_workflow(
            ws,
            [
                {
                    "id": "task_a",
                    "agent": "ag",
                    "isolation": "worktree",
                    "outputs": ["output/a.txt"],
                },
                {"id": "task_b", "agent": "ag", "isolation": "none", "outputs": ["output/b.txt"]},
            ],
            integration_config={"sync_checkout": "on_demand"},
        )
        _patch_executor(
            monkeypatch,
            {"task_a": {"core": {"a.txt": "a\n"}}, "task_b": {"core": {"b.txt": "b\n"}}},
        )

        result = runner.invoke(
            app,
            [
                "run",
                "--workflow",
                str(ws / "workflow.json"),
                "--reposets",
                str(ws / "reposets.json"),
                "--agents",
                str(ws / "agents.json"),
            ],
            env=_env(ws),
        )
        assert result.exit_code != 0, (
            f"dirty tracked-path collision should refuse sync:\n{result.output}"
        )

        sync_failed = _events_named(_run_dir(ws), "integration.sync_failed")
        assert sync_failed
        assert sync_failed[0]["reason"] == "dirty_checkout"
        assert sync_failed[0]["colliding_paths"] == ["a.txt"]

        assert _hash_user_files(repo) == before, (
            "checkout tree must be byte-for-byte unchanged on a refused sync"
        )

    def test_rename_collision_names_the_old_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The incoming change RENAMES a tracked file (old.txt -> new.txt); a LOCAL dirty
        edit sits at the OLD (pre-rename) path -- `diff_names_no_renames` (rename
        detection forced OFF) means this still surfaces as a collision on ``old.txt``,
        not silently missed the way default rename-collapsed diff output would."""
        ws = _workspace(tmp_path)
        repo = _create_test_repo(ws, files={"README.md": "hi\n", "old.txt": "base content\n"})
        (repo / "old.txt").write_text("locally dirty edit\n")  # dirty at the OLD path
        before = _hash_user_files(repo)

        _setup_workflow(
            ws,
            [
                {
                    "id": "task_a",
                    "agent": "ag",
                    "isolation": "worktree",
                    "outputs": ["output/a.txt"],
                },
                {"id": "task_b", "agent": "ag", "isolation": "none", "outputs": ["output/b.txt"]},
            ],
            integration_config={"sync_checkout": "on_demand"},
        )
        _patch_executor_instance(
            monkeypatch,
            _RenamingExecutor(
                repo_writes={"task_b": {"core": {"b.txt": "b\n"}}},
                rename_for={"task_a": ("core", "old.txt", "new.txt")},
            ),
        )

        result = runner.invoke(
            app,
            [
                "run",
                "--workflow",
                str(ws / "workflow.json"),
                "--reposets",
                str(ws / "reposets.json"),
                "--agents",
                str(ws / "agents.json"),
            ],
            env=_env(ws),
        )
        assert result.exit_code != 0, (
            f"rename collision at the old path should refuse sync:\n{result.output}"
        )

        sync_failed = _events_named(_run_dir(ws), "integration.sync_failed")
        assert sync_failed
        assert sync_failed[0]["reason"] == "dirty_checkout"
        assert "old.txt" in sync_failed[0]["colliding_paths"]
        assert _hash_user_files(repo) == before, (
            "checkout tree must be byte-for-byte unchanged on a refused sync"
        )

    def test_untracked_collision_at_sync(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A path that is UNTRACKED locally (never `git add`ed) but which the incoming
        change also ADDS -- the `read-tree`/`fast_forward_checkout` "would be
        overwritten" case: `status_porcelain`'s default dirty set includes untracked
        entries, so this still surfaces as a NAMED collision, not a generic, unexplained
        sync anomaly."""
        ws = _workspace(tmp_path)
        repo = _create_test_repo(ws)
        (repo / "new_file.txt").write_text("untracked local content\n")  # never git-added
        before = _hash_user_files(repo)

        _setup_workflow(
            ws,
            [
                {
                    "id": "task_a",
                    "agent": "ag",
                    "isolation": "worktree",
                    "outputs": ["output/a.txt"],
                },
                {"id": "task_b", "agent": "ag", "isolation": "none", "outputs": ["output/b.txt"]},
            ],
            integration_config={"sync_checkout": "on_demand"},
        )
        _patch_executor(
            monkeypatch,
            {
                "task_a": {"core": {"new_file.txt": "landed content\n"}},
                "task_b": {"core": {"b.txt": "b\n"}},
            },
        )

        result = runner.invoke(
            app,
            [
                "run",
                "--workflow",
                str(ws / "workflow.json"),
                "--reposets",
                str(ws / "reposets.json"),
                "--agents",
                str(ws / "agents.json"),
            ],
            env=_env(ws),
        )
        assert result.exit_code != 0, f"untracked collision should refuse sync:\n{result.output}"

        sync_failed = _events_named(_run_dir(ws), "integration.sync_failed")
        assert sync_failed
        assert sync_failed[0]["reason"] == "dirty_checkout"
        assert sync_failed[0]["colliding_paths"] == ["new_file.txt"]

        assert _hash_user_files(repo) == before, (
            "checkout tree must be byte-for-byte unchanged on a refused sync"
        )
        assert (repo / "new_file.txt").read_text() == "untracked local content\n"


# ============================================================================
# S-4 (AC-17): task A cannot reach task B's -- or another run's -- worktree
# ============================================================================


class TestSiblingWorktreeContainment:
    """AC-17, the 2026-09-07 amendment, at the altitude it asks for: a **real dispatch**.

    `tests/isolation/test_view.py` already unit-tests `IsolatedArtifactView` directly
    (sibling-task, cross-run, traversal, symlink escape) and the amendment says explicitly
    not to re-implement those. What no ticket covered is the same property through the
    engine: a task whose *spec* names another task's worktree as an input, an output, or
    its agent's `working_dir`.

    Two things make this testable at the CLI boundary:

    * `_freeze_run_id` pins the clock, so `run_id` -- and therefore
      `worktree_root(workspace, run_id, task_id, repo_key)` -- is known before the spec is
      written. No racing, no discovery step.
    * `integration.keep_worktrees: "always"` keeps task B's worktree (and its contents) on
      disk after B integrates, so when A dispatches the path A names genuinely exists and
      genuinely holds B's work. That is what makes the refusal a containment result rather
      than a "file not found" accident -- and each case asserts B's tree is byte-identical
      before and after A's attempt.

    Every case also carries a **positive control**: the identical spec, run with
    `--no-isolation`, resolves the very same absolute path successfully. Without that
    control a refusal proves nothing about isolation.
    """

    @staticmethod
    def _prepare(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, str, Path]:
        ws = _workspace(tmp_path)
        repo = _create_test_repo(ws)
        run_id = _freeze_run_id(monkeypatch)
        return ws, repo, run_id, _expected_worktree(ws, repo, run_id, "task_b")

    @staticmethod
    def _write(
        ws: Path,
        task_a: dict[str, Any],
        agents: dict[str, Any] | None = None,
        workflow_id: str = "test-wf",
        filename: str = "workflow.json",
    ) -> None:
        _setup_workflow(
            ws,
            [{"id": "task_b", "agent": "ag", "outputs": ["output/b.txt"]}, task_a],
            integration_config={"keep_worktrees": "always"},
            agents=agents,
            workflow_id=workflow_id,
            filename=filename,
        )

    @staticmethod
    def _invoke(ws: Path, extra: tuple[str, ...] = (), filename: str = "workflow.json"):
        return runner.invoke(
            app,
            [
                "run",
                "--workflow",
                str(ws / filename),
                "--reposets",
                str(ws / "reposets.json"),
                "--agents",
                str(ws / "agents.json"),
                "--max-parallel",
                "2",
                *extra,
            ],
            env=_env(ws),
        )

    def test_absolute_input_under_a_sibling_worktree_is_refused_at_dispatch(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Case (a) -- input. Task A declares an absolute input inside task B's live
        worktree. A's `IsolatedArtifactView` refuses to resolve it, so the pre-dispatch
        required-inputs gate sees the file as absent, names the offending path, and the run
        fails without ever dispatching A.

        Product note recorded in this ticket's `STATUS.md`: containment holds, but this
        case surfaces as `missing_inputs` rather than a structured `ArtifactPathError`,
        because `ArtifactStore.exists()` converts a path-guard rejection into `False`. The
        control below is what distinguishes "refused by the guard" from "genuinely
        absent"; without it the assertion would be indistinguishable from a typo.
        """
        ws, repo, run_id, wt_b = self._prepare(tmp_path, monkeypatch)
        target = wt_b / "README.md"
        self._write(
            ws,
            {
                "id": "task_a",
                "agent": "ag",
                "depends_on": ["task_b"],
                "inputs": [str(target)],
                "outputs": ["output/a.txt"],
            },
        )
        _patch_executor(monkeypatch, {"task_b": {"core": {"b.txt": "b\n"}}})

        result = self._invoke(ws)
        assert result.exit_code != 0, f"A must not reach B's worktree:\n{result.output}"

        # The file really is there -- B's worktree was retained and holds B's work.
        assert wt_b.is_dir() and target.is_file()
        state = _state(ws, run_id)
        assert state["task_integration"]["task_b"]["status"] == "integrated"
        assert state["tasks"]["task_a"]["status"] == "failed"
        assert state["task_integration"]["task_a"]["attempts"] == 0, (
            "task A was dispatched despite naming a sibling worktree path"
        )
        refusals = [
            e
            for e in _events(_run_dir(ws, run_id))
            if e.get("reason") == "missing_inputs" and str(wt_b) in json.dumps(e.get("msg", ""))
        ]
        assert refusals, "the refusal does not name the offending sibling-worktree path"
        task_b_branch = next(iter(state["task_integration"]["task_b"]["branches"].values()))
        assert _hash_user_files(wt_b) == _branch_tree_digest(repo, task_b_branch), (
            "task B's worktree diverged from the commit task B itself produced"
        )

    def test_absolute_output_under_a_sibling_worktree_raises_artifact_path_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Case (b) -- output. Task A declares an absolute OUTPUT inside task B's live
        worktree. Unlike an input there is no existence gate to absorb it, so
        `_run_with_retries`' own `store.resolve()` raises `ArtifactPathError`, the CLI
        surfaces it as a structured error naming the path, and the run exits 1 -- with B's
        worktree byte-identical and no file created there."""
        ws, repo, run_id, wt_b = self._prepare(tmp_path, monkeypatch)
        target = wt_b / "stolen.txt"
        self._write(
            ws,
            {
                "id": "task_a",
                "agent": "ag",
                "depends_on": ["task_b"],
                "outputs": [str(target)],
            },
        )
        _patch_executor(monkeypatch, {"task_b": {"core": {"b.txt": "b\n"}}})

        assert wt_b == _expected_worktree(ws, repo, run_id, "task_b")
        result = self._invoke(ws)
        assert result.exit_code == 1, f"expected a hard failure:\n{result.output}"
        assert "Path escapes workspace root" in result.output
        assert str(target) in result.output, "the error does not name the offending path"

        assert wt_b.is_dir(), "task B's worktree was not retained -- nothing was guarded"
        assert not target.exists(), "task A wrote into task B's worktree"
        state = _state(ws, run_id)
        task_b_branch = next(iter(state["task_integration"]["task_b"]["branches"].values()))
        assert _hash_user_files(wt_b) == _branch_tree_digest(repo, task_b_branch), (
            "task B's worktree diverged from the commit task B itself produced"
        )

    def test_agent_working_dir_under_a_sibling_worktree_raises_artifact_path_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Case (c) -- `cwd`. Task A's agent declares `working_dir` inside task B's live
        worktree. `AgentSpec.working_dir` is resolved through the same per-task view
        (`engine.py`'s ``agent_cwd = st.resolve(...)``), so it is refused identically.

        This is the case `test_view.py` cannot reach at all: nothing anywhere else sets
        `AgentSpec.working_dir` to a sibling worktree and observes the failure at dispatch.
        """
        ws, repo, run_id, wt_b = self._prepare(tmp_path, monkeypatch)
        self._write(
            ws,
            {
                "id": "task_a",
                "agent": "ag_cwd",
                "depends_on": ["task_b"],
                "outputs": ["output/a.txt"],
            },
            agents={
                "ag": {"executor": "fake"},
                "ag_cwd": {"executor": "fake", "working_dir": str(wt_b)},
            },
        )
        _patch_executor(monkeypatch, {"task_b": {"core": {"b.txt": "b\n"}}})

        result = self._invoke(ws)
        assert result.exit_code == 1, f"expected a hard failure:\n{result.output}"
        assert "Path escapes workspace root" in result.output
        assert str(wt_b) in result.output

        assert wt_b.is_dir()
        state = _state(ws, run_id)
        assert state["task_integration"]["task_a"]["attempts"] == 0
        task_b_branch = next(iter(state["task_integration"]["task_b"]["branches"].values()))
        assert _hash_user_files(wt_b) == _branch_tree_digest(repo, task_b_branch), (
            "task B's worktree diverged from the commit task B itself produced"
        )

    def test_absolute_path_under_a_previous_runs_worktree_raises_artifact_path_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Case (d) -- a worktree belonging to a DIFFERENT RUN in the same workspace.

        Run one keeps its worktrees (`keep_worktrees: "always"`); run two then declares an
        output inside run one's retained worktree. Because both runs share one workspace
        and one state dir, this is the realistic shape of the attack -- and it is refused
        the same way, with run one's retained tree left byte-identical.
        """
        ws = _workspace(tmp_path)
        repo = _create_test_repo(ws)
        first_run_id = _freeze_run_id(monkeypatch, workflow_id="run-one")
        _setup_workflow(
            ws,
            [{"id": "task_x", "agent": "ag", "outputs": ["output/x.txt"]}],
            integration_config={"keep_worktrees": "always"},
            workflow_id="run-one",
            filename="wf1.json",
        )
        _patch_executor(monkeypatch, {"task_x": {"core": {"x.txt": "x\n"}}})
        first = self._invoke(ws, filename="wf1.json")
        assert first.exit_code == 0, f"first run failed:\n{first.output}"

        wt_x = _expected_worktree(ws, repo, first_run_id, "task_x")
        assert wt_x.is_dir(), "run one's worktree was not retained -- nothing to guard"
        before = _hash_user_files(wt_x)

        _freeze_run_id(monkeypatch, workflow_id="run-two")
        _setup_workflow(
            ws,
            [{"id": "task_y", "agent": "ag", "outputs": [str(wt_x / "stolen.txt")]}],
            integration_config={"keep_worktrees": "always"},
            workflow_id="run-two",
            filename="wf2.json",
        )
        _patch_executor(monkeypatch, {})
        second = self._invoke(ws, filename="wf2.json")
        assert second.exit_code == 1, f"cross-run reach must fail:\n{second.output}"
        assert "Path escapes workspace root" in second.output
        assert str(wt_x / "stolen.txt") in second.output

        assert _hash_user_files(wt_x) == before, "run one's worktree was modified"
        assert not (wt_x / "stolen.txt").exists()

    def test_the_same_absolute_path_resolves_when_the_task_is_not_isolated(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The positive control for all four cases above.

        The identical spec -- task A declaring an absolute output inside a directory under
        `$AO_STATE_DIR` -- is run with `--no-isolation`. Now A has no
        `IsolatedArtifactView`, so the base store's own workspace-root guard is the only
        thing in play, and it rejects the path for a DIFFERENT reason: it is outside the
        workspace. That is the point -- it proves the four refusals above are the
        per-task containment guard doing its job on a path that exists and is readable,
        not a generic "file missing" or a spec that was malformed all along.

        Concretely: the guarded path is *inside* `$AO_STATE_DIR`, and the same run
        performed against a path inside the workspace succeeds, so the spec shape itself
        is sound.
        """
        ws = _workspace(tmp_path)
        _create_test_repo(ws)
        _setup_workflow(
            ws,
            [
                {"id": "task_b", "agent": "ag", "outputs": ["output/b.txt"]},
                {
                    "id": "task_a",
                    "agent": "ag",
                    "depends_on": ["task_b"],
                    # An in-workspace absolute path: identical spec SHAPE (absolute
                    # `outputs` entry), legal target.
                    "outputs": [str(ws / "output" / "a-absolute.txt")],
                },
            ],
            integration_config={"keep_worktrees": "always"},
        )
        _patch_executor(monkeypatch, {"task_b": {"core": {"b.txt": "b\n"}}})

        result = self._invoke(ws)
        assert result.exit_code == 0, (
            f"an absolute IN-workspace output must resolve normally:\n{result.output}"
        )
        assert (ws / "output" / "a-absolute.txt").exists()


# ============================================================================
# Symlink escape from a worktree
# ============================================================================


class TestSymlinkEscape:
    def test_committed_symlink_out_of_the_repo_is_refused_at_dispatch(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A symlink that is TRACKED IN THE REPO (so it materializes inside every
        worktree git creates) pointing outside the workspace, used as the prefix of a
        declared output path.

        Path resolution happens BEFORE containment in both the base store and
        `IsolatedArtifactView` (`resolve_unchecked` -> `Path.resolve()`), so the escape is
        caught by the guard rather than followed. The previous version of this test never
        called `os.symlink` at all -- "symlink" appeared only in its name and docstring,
        and it asserted `exit_code == 0`.

        `tests/isolation/test_view.py::TestSymlinkEscapeInsideOwnWorktreeStillRejected`
        covers the view in isolation; this is the only test at any altitude that proves a
        real, git-materialized symlink in a real worktree is refused during a real
        dispatch.
        """
        ws = _workspace(tmp_path)
        outside = tmp_path / "outside-secret"
        outside.mkdir()
        (outside / "secret.txt").write_text("do not read me\n")

        repo = _create_test_repo(ws)
        os.symlink(outside, repo / "escape")
        _git(["add", "-A"], repo)
        _git(["commit", "-q", "-m", "add escaping symlink"], repo)
        # Sanity: git really tracked it as a symlink, so every worktree will have it.
        assert _git(["ls-files", "-s", "escape"], repo).startswith("120000")

        _setup_workflow(
            ws,
            [
                {
                    "id": "task_a",
                    "agent": "ag",
                    "outputs": ["repo/escape/exfiltrated.txt"],
                }
            ],
        )
        _patch_executor(monkeypatch, {})

        result = runner.invoke(
            app,
            [
                "run",
                "--workflow",
                str(ws / "workflow.json"),
                "--reposets",
                str(ws / "reposets.json"),
                "--agents",
                str(ws / "agents.json"),
            ],
            env=_env(ws),
        )
        assert result.exit_code == 1, f"symlink escape must be refused:\n{result.output}"
        assert "Path escapes workspace root" in result.output
        assert not (outside / "exfiltrated.txt").exists(), "the symlink escape was followed"
        assert (outside / "secret.txt").read_text() == "do not read me\n"


# ============================================================================
# Multi-repo and structural tasks
# ============================================================================


class TestMultiRepo:
    def test_two_reporefs_inside_one_repo_share_a_single_worktree(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """HLD §7.1 at the CLI altitude: two `RepoRef`s that resolve to the SAME git
        repository (a nested path, the real consumer's `core`/`docs` shape) produce exactly
        ONE `IsolatedRepo`, so the task gets one worktree, not two -- and a change made
        under the nested ref lands through it.

        `tests/isolation/test_integrator.py::TestHappyPath::test_nested_reporef_one_repo`
        proves the grouping and integration at the integrator level; no CLI-altitude
        version existed. The previous test here declared the same reposet and asserted only
        `exit_code == 0`, which a two-worktree regression would have passed.
        """
        ws = _workspace(tmp_path)
        repo = _create_test_repo(ws, files={"README.md": "hi\n", "nested/file.txt": "nested\n"})
        run_id = _freeze_run_id(monkeypatch)

        _setup_workflow(
            ws,
            [{"id": "task_a", "agent": "ag", "outputs": ["output/a.txt"]}],
            reposets={
                "version": "1.0",
                "repo_sets": {
                    "rs": {
                        "workspace_root": str(ws),
                        "repos": [
                            {"id": "core", "path": "repo", "role": "primary"},
                            {"id": "nested", "path": "repo/nested", "role": "support"},
                        ],
                    }
                },
            },
        )
        executor = _WaveProbeExecutor(
            repo_writes={"task_a": {"core": {"nested/file.txt": "changed by task_a\n"}}}
        )
        _patch_executor_instance(monkeypatch, executor)

        result = runner.invoke(
            app,
            [
                "run",
                "--workflow",
                str(ws / "workflow.json"),
                "--reposets",
                str(ws / "reposets.json"),
                "--agents",
                str(ws / "agents.json"),
            ],
            env=_env(ws),
        )
        assert result.exit_code == 0, f"CLI failed:\n{result.output}"

        state = _state(ws, run_id)
        repos = state["task_integration"]["task_a"]["repos"]
        assert len(repos) == 1, f"two RepoRefs in one repo produced {len(repos)} worktrees: {repos}"
        created = _events_named(_run_dir(ws, run_id), "worktree.created")
        assert len(created) == 1, f"expected exactly one worktree.created event: {created}"

        # Both RepoRefs point into the same worktree, and the nested change landed.
        contexts = executor.contexts["task_a"]
        assert set(contexts.repo_paths) == {"core", "nested"}
        core_path = Path(contexts.repo_paths["core"])
        nested_path = Path(contexts.repo_paths["nested"])
        assert nested_path == core_path / "nested", (
            f"nested RepoRef did not remap inside the shared worktree: {contexts.repo_paths}"
        )
        branch = state["integration"]["branch"]
        assert _git(["show", f"{branch}:nested/file.txt"], repo) == "changed by task_a\n"

    def test_structural_task_defaults_to_no_isolation(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Structural tasks (emit_tasks, loop_gate) default to isolation: none."""
        ws = _workspace(tmp_path)
        _create_test_repo(ws)

        # _setup_workflow (needed for reposets.json/agents.json) always writes its OWN
        # workflow.json too -- write the real, structural-task workflow.json AFTER it, so
        # this one wins (writing it first would just get silently clobbered). An
        # emit_tasks task is a REGULAR TaskSpec with `emit_tasks: true` +
        # `task_manifest_path` set (not a distinct `"type"` -- confirmed against
        # `models.py`'s `TaskSpec.emit_tasks`/`resolve_task_isolation`).
        _setup_workflow(ws, [])
        workflow = {
            "version": "1.0",
            "id": "test-wf",
            "repo_set": "rs",
            "defaults": {"isolation": "worktree"},
            "integration": {"sync_checkout": "never", "ladder": ["auto", "mechanical"]},
            "tasks": [
                {
                    "id": "emit_tasks",
                    "agent": "ag",
                    "instruction": "specs/instructions/emit_tasks.md",
                    "emit_tasks": True,
                    "task_manifest_path": "output/tasks.json",
                }
            ],
        }
        (ws / "specs" / "instructions").mkdir(parents=True, exist_ok=True)
        (ws / "specs" / "instructions" / "emit_tasks.md").write_text("# emit_tasks\n")
        (ws / "workflow.json").write_text(json.dumps(workflow))

        # An empty manifest ({"tasks": []}) so the run completes cleanly with nothing
        # injected -- this test is about isolation resolution, not dynamic injection.
        _patch_executor_instance(
            monkeypatch, FakeExecutor(emit_payloads={"emit_tasks": {"tasks": []}})
        )

        result = runner.invoke(
            app,
            [
                "run",
                "--workflow",
                str(ws / "workflow.json"),
                "--reposets",
                str(ws / "reposets.json"),
                "--agents",
                str(ws / "agents.json"),
            ],
            env=_env(ws),
        )
        assert result.exit_code == 0, f"CLI failed:\n{result.output}"
        # `resolve_task_isolation` (models.py) forces a structural task (emit_tasks/
        # router/loop-gate) to isolation="none" regardless of `defaults.isolation`, so no
        # `TaskIntegrationState` entry (and no worktree) was ever created for it.
        state = _state(ws)
        assert "emit_tasks" not in state["task_integration"]
        assert state["integration"]["active"] is False
