"""Tests for `service.registry`: round-trip, atomic write, and locked read-modify-write
(AC5/AC7/AC9a)."""

from __future__ import annotations

import threading
import time
from pathlib import Path

from agent_orchestrator.service.registry import (
    ServiceRegistry,
    ServiceRegistryFile,
    WorkspaceEntry,
)


class TestLoadSave:
    def test_load_missing_file_returns_empty(self, tmp_path: Path) -> None:
        registry = ServiceRegistry(tmp_path / "service.yaml")
        data = registry.load()
        assert data == ServiceRegistryFile()
        assert data.workspaces == []

    def test_save_load_roundtrip(self, tmp_path: Path) -> None:
        registry = ServiceRegistry(tmp_path / "service.yaml")
        ws_root = tmp_path / "workspace"
        ws_root.mkdir()
        data = ServiceRegistryFile(
            workspaces=[
                WorkspaceEntry(root=str(ws_root), port=8801, autoresume=True),
                WorkspaceEntry(root=str(ws_root.parent), port=None, autoresume=False),
            ]
        )
        registry.save(data)

        loaded = registry.load()
        assert loaded == data

    def test_save_normalizes_relative_root_to_absolute(self, tmp_path: Path) -> None:
        registry = ServiceRegistry(tmp_path / "service.yaml")
        ws_root = tmp_path / "proj"
        ws_root.mkdir()

        # Use a relative-looking path (resolves to the same absolute dir) to prove save()
        # never persists a caller-relative path.
        relative_ish = ws_root / ".." / "proj"
        registry.save(ServiceRegistryFile(workspaces=[WorkspaceEntry(root=str(relative_ish))]))

        loaded = registry.load()
        assert loaded.workspaces[0].root == str(ws_root.resolve())
        assert Path(loaded.workspaces[0].root).is_absolute()

    def test_atomic_write_no_tmp_file_lingers(self, tmp_path: Path) -> None:
        registry_path = tmp_path / "service.yaml"
        registry = ServiceRegistry(registry_path)
        registry.save(ServiceRegistryFile(workspaces=[]))

        assert registry_path.exists()
        leftover_tmp = list(tmp_path.glob("*.tmp"))
        assert leftover_tmp == []


class TestMutate:
    def test_mutate_applies_fn_to_current_disk_state(self, tmp_path: Path) -> None:
        registry = ServiceRegistry(tmp_path / "service.yaml")

        def _add_one(data: ServiceRegistryFile) -> ServiceRegistryFile:
            return ServiceRegistryFile(
                workspaces=[*data.workspaces, WorkspaceEntry(root=str(tmp_path / "a"))]
            )

        result = registry.mutate(_add_one)
        assert len(result.workspaces) == 1
        assert registry.load().workspaces == result.workspaces

    def test_add_and_remove(self, tmp_path: Path) -> None:
        registry = ServiceRegistry(tmp_path / "service.yaml")
        root_a = str(tmp_path / "a")
        root_b = str(tmp_path / "b")

        registry.add(root_a, port=9001)
        registry.add(root_b, port=None, autoresume=False)
        data = registry.load()
        assert {w.root for w in data.workspaces} == {
            str(Path(root_a).resolve()),
            str(Path(root_b).resolve()),
        }

        # Re-adding the same root replaces (not duplicates) the entry.
        registry.add(root_a, port=9002)
        data = registry.load()
        entries_a = [w for w in data.workspaces if w.root == str(Path(root_a).resolve())]
        assert len(entries_a) == 1
        assert entries_a[0].port == 9002

        registry.remove(root_a)
        data = registry.load()
        assert {w.root for w in data.workspaces} == {str(Path(root_b).resolve())}

    def test_remove_missing_root_is_noop(self, tmp_path: Path) -> None:
        registry = ServiceRegistry(tmp_path / "service.yaml")
        registry.add(str(tmp_path / "a"))
        result = registry.remove(str(tmp_path / "does-not-exist"))
        assert len(result.workspaces) == 1


class TestConcurrentMutate:
    """AC9(a): a simulated concurrent writer proves the lost-update case cannot happen."""

    def test_concurrent_writer_blocks_and_neither_update_is_lost(self, tmp_path: Path) -> None:
        registry_path = tmp_path / "service.yaml"
        registry_a = ServiceRegistry(registry_path)
        registry_b = ServiceRegistry(registry_path)

        # A's mutation deliberately widens the window between its read and its write, and
        # signals B to start racing in right in the middle of it. If `mutate()` were a bare
        # load()+save() (no lock), B's write would land on the pre-A snapshot and A's own
        # save() (issued after B's) would clobber B's change -- a lost update.
        b_started = threading.Event()
        let_a_finish = threading.Event()

        def slow_add_a(data: ServiceRegistryFile) -> ServiceRegistryFile:
            b_started.set()
            let_a_finish.wait(timeout=5)
            return ServiceRegistryFile(
                workspaces=[*data.workspaces, WorkspaceEntry(root=str(tmp_path / "a"))]
            )

        def add_b(data: ServiceRegistryFile) -> ServiceRegistryFile:
            return ServiceRegistryFile(
                workspaces=[*data.workspaces, WorkspaceEntry(root=str(tmp_path / "b"))]
            )

        results: dict[str, ServiceRegistryFile] = {}

        def run_a() -> None:
            results["a"] = registry_a.mutate(slow_add_a)

        thread_a = threading.Thread(target=run_a)
        thread_a.start()
        assert b_started.wait(timeout=5), "A never entered its mutate callback"

        b_done = threading.Event()

        def run_b() -> None:
            results["b"] = registry_b.mutate(add_b)
            b_done.set()

        thread_b = threading.Thread(target=run_b)
        thread_b.start()

        # B must genuinely block on the lock while A still holds it -- give it a moment and
        # confirm it has NOT completed yet, proving contention is real, not coincidental.
        time.sleep(0.2)
        assert not b_done.is_set(), "B completed while A still held the lock -- lock not held"

        let_a_finish.set()
        thread_a.join(timeout=5)
        thread_b.join(timeout=5)
        assert b_done.is_set()

        final = registry_a.load()
        roots = {Path(w.root).name for w in final.workspaces}
        assert roots == {"a", "b"}, "a lost update occurred -- one writer's change vanished"

    def test_many_concurrent_adds_none_lost(self, tmp_path: Path) -> None:
        registry_path = tmp_path / "service.yaml"
        num_writers = 8

        def add_worker(i: int) -> None:
            ServiceRegistry(registry_path).add(str(tmp_path / f"ws-{i}"))

        threads = [threading.Thread(target=add_worker, args=(i,)) for i in range(num_writers)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        final = ServiceRegistry(registry_path).load()
        assert len(final.workspaces) == num_writers
