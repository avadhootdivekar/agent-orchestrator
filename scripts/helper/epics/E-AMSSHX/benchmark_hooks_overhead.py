#!/usr/bin/env python3
"""AC-6: Best-effort timing micro-benchmark for hook dispatch overhead.

This script is ILLUSTRATIVE, not a pytest assertion (per CLAUDE.md §8 determinism rule).
It measures the _run_with_retries call overhead with vs without hooks declared,
using a minimal test setup. Results are recorded in the ticket STATUS.md as evidence.

Run: python scripts/helper/epics/E-AMSSHX/benchmark_hooks_overhead.py
"""

from __future__ import annotations

import time
import tempfile
from pathlib import Path

from agent_orchestrator.artifacts import LocalFsArtifactStore
from agent_orchestrator.engine import Orchestrator
from agent_orchestrator.executors.fake import FakeExecutor
from agent_orchestrator.models import (
    AgentSpec,
    HookRef,
    HookSpec,
    RepoRef,
    RepoSet,
    TaskSpec,
    WorkflowDefaults,
    WorkflowSpec,
)
from agent_orchestrator.monitoring import Monitor
from agent_orchestrator.runstate import RunStateStore


class _NoOpMonitor(Monitor):
    """Stub monitor."""

    name = "noop"

    def decide_breaker_trip(self, trip, *, run_id):  # type: ignore[no-untyped-def]
        raise AssertionError("not used")

    def decide_task_failure(self, summary, *, run_id):  # type: ignore[no-untyped-def]
        return self.AcceptanceDecision(action="accept_failure")


def _make_workspace(tmp_path: str) -> tuple[LocalFsArtifactStore, RunStateStore]:
    store = LocalFsArtifactStore(tmp_path)
    rs_store = RunStateStore(tmp_path, store)
    return store, rs_store


def _fake_agents() -> dict:
    return {"ag": AgentSpec(executor="fake")}


def _fake_reposets(workspace: str) -> dict:
    return {
        "rs": RepoSet(
            workspace_root=workspace,
            repos=[RepoRef(id="core", path=".", role="primary")],
        )
    }


def benchmark_without_hooks(iterations: int = 5) -> float:
    """Time _run_with_retries dispatch with no hooks declared."""
    with tempfile.TemporaryDirectory() as tmp_path:
        store, rs_store = _make_workspace(tmp_path)

        task = TaskSpec(
            id="t1",
            agent="ag",
            instruction="specs/examples/instructions/design.md",
            outputs=["output/t1.txt"],
            # No hooks
        )

        wf = WorkflowSpec(
            version="1.0",
            id="wf",
            repo_set="rs",
            tasks=[task],
            defaults=WorkflowDefaults(),
        )

        executor = FakeExecutor(behaviors={"t1": "succeed"})
        orch = Orchestrator(executor, store, rs_store, monitor=_NoOpMonitor())

        start = time.monotonic()
        for _ in range(iterations):
            orch.run(wf, _fake_reposets(tmp_path), _fake_agents())
        elapsed = time.monotonic() - start
        return elapsed / iterations


def benchmark_with_hooks(iterations: int = 5) -> float:
    """Time _run_with_retries dispatch with hooks declared (instant-pass hooks)."""
    with tempfile.TemporaryDirectory() as tmp_path:
        store, rs_store = _make_workspace(tmp_path)

        # Create a tiny no-op hook script
        hook_script = Path(tmp_path) / "hook.py"
        hook_script.write_text("exit(0)")
        hook_script.chmod(0o755)

        hooks = {
            "check": HookSpec(command=["python3", str(hook_script)]),
            "grade": HookSpec(command=["python3", str(hook_script)]),
        }

        task = TaskSpec(
            id="t1",
            agent="ag",
            instruction="specs/examples/instructions/design.md",
            outputs=["output/t1.txt"],
            pre_hook=HookRef(use="check"),
            post_hook=HookRef(use="grade"),
        )

        wf = WorkflowSpec(
            version="1.0",
            id="wf",
            repo_set="rs",
            tasks=[task],
            hooks=hooks,
            defaults=WorkflowDefaults(),
        )

        executor = FakeExecutor(behaviors={"t1": "succeed"})
        orch = Orchestrator(executor, store, rs_store, monitor=_NoOpMonitor())

        start = time.monotonic()
        for _ in range(iterations):
            orch.run(wf, _fake_reposets(tmp_path), _fake_agents())
        elapsed = time.monotonic() - start
        return elapsed / iterations


def main() -> None:
    """Run benchmark and report results."""
    print("=" * 70)
    print("AC-6 Benchmark: _run_with_retries hook dispatch overhead")
    print("=" * 70)
    print()

    iterations = 3
    print(f"Warming up ({iterations} iterations each)...")

    # Warm up
    benchmark_without_hooks(iterations=1)
    benchmark_with_hooks(iterations=1)

    print(f"\nRunning benchmark ({iterations} iterations each)...")

    time_without = benchmark_without_hooks(iterations=iterations)
    time_with = benchmark_with_hooks(iterations=iterations)

    overhead_ms = (time_with - time_without) * 1000
    overhead_pct = (overhead_ms / (time_without * 1000)) * 100 if time_without > 0 else 0

    print()
    print(f"Average time WITHOUT hooks: {time_without*1000:.2f} ms")
    print(f"Average time WITH hooks:    {time_with*1000:.2f} ms")
    print(f"Overhead (absolute):        {overhead_ms:.2f} ms")
    print(f"Overhead (relative):        {overhead_pct:.1f}%")
    print()
    print("Note: This is illustrative only (not a CI assertion).")
    print("Hook overhead is primarily subprocess startup + capture (fast, <100ms each).")
    print("=" * 70)


if __name__ == "__main__":
    main()
