"""Shared rig for the Part 2 hardening suites (T-JCOAsq): integration and adversarial tests.

A real `Orchestrator` drives a real `ResultCache` over a real `LocalFsCacheStore` in a temp
workspace. Dispatches are counted by a `CostlyFakeExecutor` (the cost-reporting executor pattern
of HLD 18: `FakeExecutor` reports no cost). Two flavours of repository state:

* fakes (`FakeRepoHeadReader` / `FakeWorktreeProbe`), the default: fast, fully scripted;
* a REAL git repository (`real_git=True`) with the production `RepoHeadReader` /
  `WorktreeProbe`, for the tests whose subject is a real commit or a real tracked-file edit.

The clock is the deterministic ticking clock of `test_engine_result_cache`: every read advances
one second, so run ids are distinct and every timestamp replays identically.
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from agent_orchestrator.artifacts import LocalFsArtifactStore
from agent_orchestrator.cache import constants as c
from agent_orchestrator.cache.coordinator import ResultCache
from agent_orchestrator.cache.repo_state import RepoHeadReader, WorktreeProbe
from agent_orchestrator.cache.settings import ResultCacheSettings
from agent_orchestrator.cache.store import LocalFsCacheStore
from agent_orchestrator.engine import Orchestrator
from agent_orchestrator.models import AgentSpec, RepoRef, RepoSet, RunState, WorkflowSpec
from agent_orchestrator.runstate import RunStateStore
from tests.cache.fakes import FakeRepoHeadReader, FakeWorktreeProbe, fake_cli_version_of
from tests.cache.test_engine_result_cache import (
    HEAD,
    INSTRUCTION,
    CostlyFakeExecutor,
    SpyCache,
    TickingClock,
)

RUN_LOG_NAME = "run.log"
EVENT_FIELD = "event"
CLI_VERSION = "9.9.9 (Claude Code)"
TRACKED_FILE = "tracked.txt"
TRACKED_BODY = "tracked content\n"
_GIT_IDENTITY = {
    "GIT_AUTHOR_NAME": "rig",
    "GIT_AUTHOR_EMAIL": "rig@example.invalid",
    "GIT_COMMITTER_NAME": "rig",
    "GIT_COMMITTER_EMAIL": "rig@example.invalid",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
}


def git(repo: Path, *args: str) -> str:
    """Run a real git command in *repo* with a fixed identity and no global config."""
    cp = subprocess.run(
        ["git", "-c", "commit.gpgsign=false", *args],
        cwd=repo,
        env={**os.environ, **_GIT_IDENTITY},
        capture_output=True,
        text=True,
        check=True,
    )
    return cp.stdout.strip()


def init_repo(root: Path) -> None:
    """A repository at *root* with one committed tracked file."""
    root.mkdir(parents=True, exist_ok=True)
    git(root, "init", "-q", "-b", "main")
    (root / TRACKED_FILE).write_text(TRACKED_BODY)
    git(root, "add", TRACKED_FILE)
    git(root, "commit", "-q", "-m", "base")


class SteppingClock(TickingClock):
    """The ticking clock with an explicit `jump` (TTL tests) and a configurable start."""

    def __init__(self, start: datetime | None = None) -> None:
        super().__init__()
        if start is not None:
            self._now = start

    def jump(self, delta: timedelta) -> None:
        with self._lock:
            self._now += delta


class HookedExecutor(CostlyFakeExecutor):
    """`CostlyFakeExecutor` plus per-task side effects that run INSIDE the dispatch: `hooks` run
    before the outputs are written, `after` once they are. This is how a test makes the agent
    mutate an input, edit a tracked file or commit (the store-guard scenarios), or makes two
    concurrent dispatches rendezvous."""

    def __init__(
        self,
        hooks: dict[str, Callable[[Any], object]] | None = None,
        after: dict[str, Callable[[Any], object]] | None = None,
        **kw: Any,
    ) -> None:
        super().__init__(**kw)
        self.hooks = hooks or {}
        self.after = after or {}

    def execute(self, ctx: Any) -> Any:
        hook = self.hooks.get(ctx.task_id)
        if hook is not None:
            hook(ctx)
        result = super().execute(ctx)
        hook = self.after.get(ctx.task_id)
        if hook is not None:
            hook(ctx)
        return result


@dataclass
class Rig:
    """One workspace, one cache, any number of runs against them."""

    ws: Path
    mode: str = c.MODE_ON
    ttl_days: int | None = 30
    include_repo_heads: bool = True
    strict: bool = False
    real_git: bool = False  # a real repository + the production head reader / worktree probe
    repo_path: str = "."  # the repository (and `core` repo) location relative to the workspace
    clock: SteppingClock = field(default_factory=SteppingClock)
    heads: FakeRepoHeadReader = field(default_factory=lambda: FakeRepoHeadReader({"core": HEAD}))
    probe: FakeWorktreeProbe = field(default_factory=FakeWorktreeProbe)

    def __post_init__(self) -> None:
        self.ws = self.ws.resolve()
        self.store = LocalFsArtifactStore(str(self.ws))
        self.rs_store = RunStateStore(str(self.ws), self.store, clock=self.clock)
        instr = self.ws / INSTRUCTION
        instr.parent.mkdir(parents=True, exist_ok=True)
        instr.write_text("# stub instruction\n")
        if self.real_git:
            init_repo(self.ws / self.repo_path)
        self.settings = ResultCacheSettings(
            mode=self.mode,
            source=c.SOURCE_ENV,
            max_bytes=10**9,
            max_entry_bytes=10**8,
            ttl_days=self.ttl_days,
            include_repo_heads=self.include_repo_heads,
            max_input_bytes=10**9,
            max_input_files=10**6,
        )
        self.cache_store = LocalFsCacheStore.for_workspace(
            str(self.ws), max_bytes=self.settings.max_bytes, ttl_days=self.ttl_days
        )
        self.coordinator = self._coordinator()
        self.cache = SpyCache(self.coordinator)

    def _coordinator(self) -> ResultCache:
        hooks_dir = self.ws.parent / f"{self.ws.name}-empty-hooks"
        heads: Any = (
            RepoHeadReader(workspace_root=str(self.ws), hooks_dir=hooks_dir)
            if self.real_git
            else self.heads
        )
        probe: Any = WorktreeProbe(hooks_dir=hooks_dir) if self.real_git else self.probe
        return ResultCache(
            self.cache_store,
            self.settings,
            workspace_root=str(self.ws),
            cache_root=self.cache_store.root,
            heads=heads,
            worktree=probe,
            cli_versions=fake_cli_version_of(CLI_VERSION),
            environ={},
            strict=self.strict,
        )

    # -- running -----------------------------------------------------------------------------
    def reposets(self) -> dict[str, RepoSet]:
        return {
            "rs": RepoSet(
                workspace_root=str(self.ws),
                repos=[RepoRef(id="core", path=self.repo_path, role="primary")],
            )
        }

    @staticmethod
    def agents() -> dict[str, AgentSpec]:
        return {"ag": AgentSpec(executor="claude_cli", model="sonnet", effort="medium")}

    def run(
        self,
        wf: WorkflowSpec,
        executor: CostlyFakeExecutor,
        *,
        run_state: RunState | None = None,
        cache: Any = ...,
        **kw: Any,
    ) -> RunState:
        hook = self.cache if cache is ... else cache
        orch = Orchestrator(
            executor, self.store, self.rs_store, clock=self.clock, result_cache=hook, **kw
        )
        return orch.run(wf, self.reposets(), self.agents(), run_state=run_state)

    # -- inspection --------------------------------------------------------------------------
    def out(self, rel: str) -> Path:
        return self.ws / rel

    def delete_outputs(self, *rels: str) -> None:
        for rel in rels:
            self.out(rel).unlink()

    def run_dir(self, state: RunState) -> Path:
        return self.ws / ".orchestrator" / "runs" / state.run_id

    def log_events(self, state: RunState, name: str) -> list[dict[str, Any]]:
        """The structured events named *name* in the run's `run.log` (JSON lines)."""
        path = self.run_dir(state) / RUN_LOG_NAME
        found: list[dict[str, Any]] = []
        for line in path.read_text().splitlines():
            record = json.loads(line)
            if record.get(EVENT_FIELD) == name:
                found.append(record)
        return found

    def status_json(self, state: RunState) -> dict[str, Any]:
        raw = json.loads((self.run_dir(state) / "status.json").read_text())
        assert isinstance(raw, dict)
        return raw

    @property
    def cache_root(self) -> Path:
        return Path(self.cache_store.root)

    def entry_files(self) -> list[Path]:
        """Every entry file under `entries/v1` (never following links)."""
        root = self.cache_root / c.ENTRIES_DIR / c.ENTRIES_VERSION_DIR
        if not root.is_dir():
            return []
        return sorted(p for p in root.glob("*/*" + c.ENTRY_SUFFIX) if p.is_file())

    def blob_files(self) -> list[Path]:
        root = self.cache_root / c.BLOBS_DIR
        if not root.is_dir():
            return []
        return sorted(p for p in root.glob("*/*") if p.is_file())

    def only_entry(self) -> Path:
        files = self.entry_files()
        assert len(files) == 1, files
        return files[0]

    def only_key(self) -> str:
        return self.only_entry().name[: -len(c.ENTRY_SUFFIX)]

    def entry_dir(self, key: str) -> Path:
        return self.cache_root / c.ENTRIES_DIR / c.ENTRIES_VERSION_DIR / key[: c.SHARD_CHARS]
