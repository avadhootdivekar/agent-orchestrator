"""Port resolution for the multi-workspace service: P1 (workspace config) > P2 (registry
pin) > P3 (random free port), tier-first conflict detection (HLD §6.2, ADR-0012 D3 +
early-gate correction #1).

Precedence per workspace
-------------------------
- **P1** -- the workspace's own ``.ao/config.yaml`` (``ui.port``), discovered/loaded via
  ``project_config.find_project_config``/``load_project_config``, best-effort: a workspace
  with no config, an unreadable config, or a config that fails to parse simply has "no P1
  opinion" and falls through -- one workspace's bad config must never abort resolution for
  the whole registry.
- **P2** -- else the registry entry's pinned ``port``, if set.
- **P3** -- else a random free port (``bind(("127.0.0.1", 0))``), persisted back into the
  registry (via ``persist_resolution`` + the caller's ``ServiceRegistry.mutate``) so the URL
  is stable across restarts.

Conflict tie-break: tier-first, registry-order second (AC8 / early-gate correction #1)
-----------------------------------------------------------------------------------------
Among workspaces landing on the same port, the highest-precedence source (P1 beats P2 beats
P3) keeps it -- a lower-precedence pick must never displace a higher-precedence one merely
because it was resolved earlier. Only a same-tier collision falls back to registry order
(first keeps it). A workspace that loses falls back one tier from where it lost (a beaten P2
tries P3, not a re-check of P2). A P1/P2 port that is not actually bindable right now (probed
with a real bind to the *specific* port, ``SO_REUSEADDR`` left at its OS default/off so a
genuinely-busy port is caught) is treated the same as a collision.

This module never mutates the registry or touches disk -- ``resolve_ports`` is pure/
side-effect-free (besides the inherent, documented TOCTOU of probing ports via real binds),
and ``persist_resolution`` returns a new ``ServiceRegistryFile`` for the caller to save.
"""

from __future__ import annotations

import socket
from pathlib import Path

from pydantic import BaseModel

from ..errors import ConfigError
from ..project_config import find_project_config, load_project_config
from .registry import ServiceRegistryFile, WorkspaceEntry

# Precedence order, highest first. Also used as the tier-rank for the tie-break rule.
_TIERS = ("P1", "P2", "P3")

# Bounded retries for a P3 pick landing on a port already claimed by a higher-precedence
# resolution in this same pass (astronomically unlikely -- ephemeral OS-assigned ports vs.
# the low, operator-chosen ports P1/P2 typically pin -- but bounded rather than infinite).
MAX_FREE_PORT_ATTEMPTS = 20


class PortConflict(BaseModel):
    """One workspace's loss at one precedence tier during resolution (AC3/AC8)."""

    root: str
    requested_port: int
    reason: str
    fallback_port: int
    """The port this workspace was FINALLY assigned once resolution completed (which may
    itself be the result of falling back through more than one tier)."""


class PortResolution(BaseModel):
    """The result of one `resolve_ports` pass."""

    ports: dict[str, int] = {}
    """Final resolved port per workspace root."""

    tiers: dict[str, str] = {}
    """Which precedence tier ("P1"/"P2"/"P3") each workspace's final port came from --
    consulted by `persist_resolution` to decide what to write back to the registry."""

    conflicts: list[PortConflict] = []


def _p1_port(root: Path) -> int | None:
    """Best-effort P1 read: the workspace's own `.ao/config.yaml` `ui.port`.

    A workspace with no config, an unreadable config, or a config that fails schema
    validation simply has no P1 opinion -- this must never raise.
    """
    config_path = find_project_config(start=root)
    if config_path is None:
        return None
    try:
        cfg = load_project_config(config_path)
    except ConfigError:
        return None
    return cfg.ui.port


def _port_is_bindable(port: int) -> bool:
    """Probe whether *port* is free right now via a real bind to that specific port.

    `SO_REUSEADDR` is deliberately left unset (the OS default) so a genuinely-busy port is
    caught rather than masked.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
        return True


def pick_free_port(exclude: set[int]) -> int:
    """Bind `("127.0.0.1", 0)` and read back the OS-assigned free port, retrying (bounded)
    if it collides with a port already claimed by the caller.

    Shared by this module's own P3 resolution pass AND `supervisor.py`'s same-boot
    EADDRINUSE reassignment (HLD §6.3) -- one implementation, two callers, rather than each
    keeping its own bind-loop copy.

    TOCTOU note: the probing socket is closed immediately after reading the port back, which
    is an inherent, documented race against another process grabbing the same port before the
    real service binds it (accepted MVP limitation -- see ticket Risks / HLD §6.2; the real
    backstop is the spawn-time EADDRINUSE handling in `supervisor.py`).
    """
    last_port: int | None = None
    for _ in range(MAX_FREE_PORT_ATTEMPTS):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        if port not in exclude:
            return port
        last_port = port
    raise RuntimeError(
        f"could not find a free port outside {sorted(exclude)!r} after "
        f"{MAX_FREE_PORT_ATTEMPTS} attempts (last candidate: {last_port})"
    )


def _build_chain(entry: WorkspaceEntry, p1_port: int | None) -> list[tuple[str, int | None]]:
    """Ordered fallback chain of (tier, port) candidates for one workspace, best first.

    P3 always terminates the chain with a `None` port placeholder (materialized lazily by
    `pick_free_port` only if every higher-tier candidate loses) since it is always
    eventually resolvable and never itself collides (by construction).
    """
    chain: list[tuple[str, int | None]] = []
    if p1_port is not None:
        chain.append(("P1", p1_port))
    if entry.port is not None:
        chain.append(("P2", entry.port))
    chain.append(("P3", None))
    return chain


def resolve_ports(registry: ServiceRegistryFile) -> PortResolution:
    """Resolve a port per workspace with tier-first, registry-order-second conflict
    tie-break (AC3/AC8). Pure -- does not mutate *registry*.
    """
    entries = list(registry.workspaces)
    order = {entry.root: idx for idx, entry in enumerate(entries)}
    chains: dict[str, list[tuple[str, int | None]]] = {
        entry.root: _build_chain(entry, _p1_port(Path(entry.root))) for entry in entries
    }
    cursor = {root: 0 for root in chains}
    claimed: dict[int, str] = {}
    resolved: dict[str, int] = {}
    tiers: dict[str, str] = {}
    # (root, requested_port, reason) -- fallback_port is filled in once every workspace has
    # a final resolution, since a loser's ultimate port isn't known until later tiers run.
    losses: list[tuple[str, int, str]] = []

    pending = set(chains)
    while pending:

        def _tier_rank(root: str) -> int:
            return _TIERS.index(chains[root][cursor[root]][0])

        min_rank = min(_tier_rank(root) for root in pending)
        # Tier-first: nothing at a lower-precedence tier is even attempted while any
        # higher-precedence candidate remains unresolved. Registry-order second: within the
        # same tier, process (and therefore win ties) in registry order.
        batch = sorted(
            (root for root in pending if _tier_rank(root) == min_rank),
            key=lambda root: order[root],
        )
        for root in batch:
            tier_name, candidate_port = chains[root][cursor[root]]

            if candidate_port is None:
                # P3 slot: materialize now, excluding every port already claimed.
                port = pick_free_port(exclude=set(claimed))
                claimed[port] = root
                resolved[root] = port
                tiers[root] = tier_name
                pending.discard(root)
                continue

            if candidate_port in claimed:
                losses.append(
                    (
                        root,
                        candidate_port,
                        f"port {candidate_port} already claimed by workspace "
                        f"{claimed[candidate_port]!r} at a higher-or-equal-precedence tier",
                    )
                )
                cursor[root] += 1
                continue

            if not _port_is_bindable(candidate_port):
                losses.append(
                    (root, candidate_port, f"port {candidate_port} is not currently bindable")
                )
                cursor[root] += 1
                continue

            claimed[candidate_port] = root
            resolved[root] = candidate_port
            tiers[root] = tier_name
            pending.discard(root)

    conflicts = [
        PortConflict(
            root=root, requested_port=req_port, reason=reason, fallback_port=resolved[root]
        )
        for root, req_port, reason in losses
    ]
    return PortResolution(ports=resolved, tiers=tiers, conflicts=conflicts)


def persist_resolution(
    registry: ServiceRegistryFile, resolution: PortResolution
) -> ServiceRegistryFile:
    """Return an updated `ServiceRegistryFile` with every P3-picked port written into the
    matching `WorkspaceEntry.port`.

    Only P3-sourced picks are persisted: a P1 pick came from the workspace's own
    `.ao/config.yaml` and must never be echoed into the registry (that would misattribute a
    workspace-owned setting as a registry-level pin, and would go stale the moment the
    workspace's own config changes); a P2 pick already IS the registry value, so leaving it
    alone is a no-op either way. Side-effect-free -- the caller is responsible for persisting
    the result via `ServiceRegistry.mutate`/`save` (AC3).
    """
    updated_workspaces = []
    for entry in registry.workspaces:
        tier = resolution.tiers.get(entry.root)
        port = resolution.ports.get(entry.root)
        if tier == "P3" and port is not None:
            updated_workspaces.append(entry.model_copy(update={"port": port}))
        else:
            updated_workspaces.append(entry)
    return ServiceRegistryFile(workspaces=updated_workspaces)
