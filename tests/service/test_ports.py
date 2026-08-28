"""Tests for `service.ports`: P1 > P2 > P3 resolution, tier-first conflict tie-break
(AC5/AC8/AC9b), and persistence."""

from __future__ import annotations

import socket
from pathlib import Path

import yaml

from agent_orchestrator.service.ports import persist_resolution, resolve_host, resolve_ports
from agent_orchestrator.service.registry import ServiceRegistryFile, WorkspaceEntry


def _make_workspace(tmp_path: Path, name: str, ui_port: int | None = None) -> Path:
    """A workspace directory, optionally with its own `.ao/config.yaml` `ui.port` pin
    (P1). `tmp_path` itself gets a `.git` marker (idempotent across calls) so
    `find_project_config`'s walk-up never escapes the test's temp dir even for a workspace
    with no config of its own (Risks note in T-Gr8s2a's ticket)."""
    (tmp_path / ".git").mkdir(exist_ok=True)
    root = tmp_path / name
    root.mkdir()
    if ui_port is not None:
        ao_dir = root / ".ao"
        ao_dir.mkdir()
        (ao_dir / "config.yaml").write_text(yaml.safe_dump({"ui": {"port": ui_port}}))
    return root


def _bind_ephemeral() -> tuple[socket.socket, int]:
    """Bind an OS-assigned free port and keep the socket open (simulating a port already
    held by something else)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    return sock, sock.getsockname()[1]


def _assert_bindable(port: int) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", port))


class TestPrecedence:
    def test_p1_wins_over_p2_and_p3(self, tmp_path: Path) -> None:
        root = _make_workspace(tmp_path, "proj", ui_port=9101)
        registry = ServiceRegistryFile(
            workspaces=[WorkspaceEntry(root=str(root), port=9202)]  # P2 present too
        )

        resolution = resolve_ports(registry)

        assert resolution.ports[str(root)] == 9101
        assert resolution.tiers[str(root)] == "P1"
        assert resolution.conflicts == []

    def test_p2_wins_over_p3(self, tmp_path: Path) -> None:
        root = _make_workspace(tmp_path, "proj")  # no .ao/config.yaml
        registry = ServiceRegistryFile(workspaces=[WorkspaceEntry(root=str(root), port=9303)])

        resolution = resolve_ports(registry)

        assert resolution.ports[str(root)] == 9303
        assert resolution.tiers[str(root)] == "P2"
        assert resolution.conflicts == []

    def test_p3_picks_free_port_when_neither_set(self, tmp_path: Path) -> None:
        root = _make_workspace(tmp_path, "proj")
        registry = ServiceRegistryFile(workspaces=[WorkspaceEntry(root=str(root), port=None)])

        resolution = resolve_ports(registry)

        port = resolution.ports[str(root)]
        assert resolution.tiers[str(root)] == "P3"
        assert resolution.conflicts == []
        _assert_bindable(port)  # actually bindable at resolution time


class TestConflicts:
    def test_same_tier_collision_first_in_registry_order_wins(self, tmp_path: Path) -> None:
        root_first = _make_workspace(tmp_path, "first")
        root_second = _make_workspace(tmp_path, "second")
        registry = ServiceRegistryFile(
            workspaces=[
                WorkspaceEntry(root=str(root_first), port=9404),
                WorkspaceEntry(root=str(root_second), port=9404),
            ]
        )

        resolution = resolve_ports(registry)

        assert resolution.ports[str(root_first)] == 9404
        assert resolution.tiers[str(root_first)] == "P2"
        assert resolution.ports[str(root_second)] != 9404
        assert resolution.tiers[str(root_second)] == "P3"
        _assert_bindable(resolution.ports[str(root_second)])

        assert len(resolution.conflicts) == 1
        conflict = resolution.conflicts[0]
        assert conflict.root == str(root_second)
        assert conflict.requested_port == 9404
        assert conflict.fallback_port == resolution.ports[str(root_second)]

    def test_pinned_port_already_bound_falls_back_with_conflict(self, tmp_path: Path) -> None:
        holder, busy_port = _bind_ephemeral()
        try:
            root = _make_workspace(tmp_path, "proj", ui_port=busy_port)
            registry = ServiceRegistryFile(workspaces=[WorkspaceEntry(root=str(root))])

            resolution = resolve_ports(registry)

            assert resolution.ports[str(root)] != busy_port
            assert resolution.tiers[str(root)] == "P3"
            assert len(resolution.conflicts) == 1
            assert resolution.conflicts[0].requested_port == busy_port
            assert resolution.conflicts[0].fallback_port == resolution.ports[str(root)]
            _assert_bindable(resolution.ports[str(root)])
        finally:
            holder.close()

    def test_cross_tier_conflict_p1_beats_earlier_p2(self, tmp_path: Path) -> None:
        """AC9(b): P2 registered earlier must still lose to a later workspace's P1 pin."""
        contested_port = 9505
        root_p2 = _make_workspace(tmp_path, "earlier-p2")  # registry order 0
        root_p1 = _make_workspace(tmp_path, "later-p1", ui_port=contested_port)  # order 1
        registry = ServiceRegistryFile(
            workspaces=[
                WorkspaceEntry(root=str(root_p2), port=contested_port),  # earlier, P2
                WorkspaceEntry(root=str(root_p1), port=None),  # later, P1
            ]
        )

        resolution = resolve_ports(registry)

        # The later, higher-precedence (P1) workspace keeps the contested port...
        assert resolution.ports[str(root_p1)] == contested_port
        assert resolution.tiers[str(root_p1)] == "P1"

        # ...and the earlier, lower-precedence (P2) workspace is the one bumped to P3, not
        # the reverse (the original registry-order-only rule would have gotten this
        # backwards -- see ADR-0012 early-gate correction #1).
        assert resolution.ports[str(root_p2)] != contested_port
        assert resolution.tiers[str(root_p2)] == "P3"
        _assert_bindable(resolution.ports[str(root_p2)])

        assert len(resolution.conflicts) == 1
        assert resolution.conflicts[0].root == str(root_p2)
        assert resolution.conflicts[0].requested_port == contested_port


class TestPersistResolution:
    def test_persists_only_p3_picks(self, tmp_path: Path) -> None:
        root_p1 = _make_workspace(tmp_path, "p1-proj", ui_port=9601)
        root_p3 = _make_workspace(tmp_path, "p3-proj")
        registry = ServiceRegistryFile(
            workspaces=[
                WorkspaceEntry(root=str(root_p1), port=None),
                WorkspaceEntry(root=str(root_p3), port=None),
            ]
        )

        resolution = resolve_ports(registry)
        updated = persist_resolution(registry, resolution)

        entry_p1 = next(w for w in updated.workspaces if w.root == str(root_p1))
        entry_p3 = next(w for w in updated.workspaces if w.root == str(root_p3))

        # P1-sourced port must NOT be echoed into the registry entry.
        assert entry_p1.port is None
        # P3-sourced pick IS persisted so it's stable across restarts.
        assert entry_p3.port == resolution.ports[str(root_p3)]

    def test_does_not_mutate_input_registry(self, tmp_path: Path) -> None:
        root = _make_workspace(tmp_path, "proj")
        registry = ServiceRegistryFile(workspaces=[WorkspaceEntry(root=str(root), port=None)])
        resolution = resolve_ports(registry)

        persist_resolution(registry, resolution)

        assert registry.workspaces[0].port is None  # original object untouched


class TestEmptyRegistry:
    def test_resolve_empty_registry(self) -> None:
        resolution = resolve_ports(ServiceRegistryFile(workspaces=[]))
        assert resolution.ports == {}
        assert resolution.conflicts == []


class TestResolveHost:
    """`resolve_host` precedence (per-workspace bind host): P1 `ui.host` > P2 registry
    `host` > loopback default. No random tier, nothing persisted."""

    @staticmethod
    def _workspace_with_host(tmp_path: Path, name: str, ui_host: str | None) -> Path:
        (tmp_path / ".git").mkdir(exist_ok=True)
        root = tmp_path / name
        root.mkdir()
        if ui_host is not None:
            ao_dir = root / ".ao"
            ao_dir.mkdir()
            (ao_dir / "config.yaml").write_text(yaml.safe_dump({"ui": {"host": ui_host}}))
        return root

    def test_default_is_loopback(self, tmp_path: Path) -> None:
        root = self._workspace_with_host(tmp_path, "plain", None)
        assert resolve_host(WorkspaceEntry(root=str(root))) == "127.0.0.1"

    def test_p2_registry_host_wins_over_default(self, tmp_path: Path) -> None:
        root = self._workspace_with_host(tmp_path, "p2", None)
        entry = WorkspaceEntry(root=str(root), host="0.0.0.0")
        assert resolve_host(entry) == "0.0.0.0"

    def test_p1_config_host_wins_over_p2(self, tmp_path: Path) -> None:
        root = self._workspace_with_host(tmp_path, "p1", "0.0.0.0")
        entry = WorkspaceEntry(root=str(root), host="192.168.1.50")
        assert resolve_host(entry) == "0.0.0.0"
