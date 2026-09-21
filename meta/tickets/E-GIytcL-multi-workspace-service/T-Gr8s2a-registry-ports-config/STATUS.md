# STATUS

- ID: `T-Gr8s2a-registry-ports-config`
- Updated At: 2026-08-28
- State: Done
- Owner: developer agent

## This update
- Implemented the foundation layer for the epic: `service/paths.py` (XDG/env-overridable
  registry+state path resolution), `service/registry.py` (`WorkspaceEntry`/
  `ServiceRegistryFile` pydantic models, atomic write-then-rename `load`/`save`, plus a
  locked `mutate()` read-lock-merge-write primitive and `add`/`remove` built on it),
  `service/ports.py` (`resolve_ports`/`persist_resolution`, P1>P2>P3 with tier-first,
  registry-order-second conflict tie-break), and `ProjectConfig.ui: UIConfig` (`port: int |
  None`, additive, `extra="ignore"` confirmed unchanged in this file — no explicit
  `model_config` override found).
- Both mandatory early-gate items (AC7-9) implemented as core requirements, not
  afterthoughts:
  - **AC7 (file-locked registry writes)**: `ServiceRegistry.mutate(fn)` acquires an
    exclusive `flock` on a companion `<registry>.lock` file (never the payload file itself,
    since that gets replaced via `os.replace` on every save — a lock on a path whose inode
    can be swapped out from under a waiter is not reliable), re-reads the current on-disk
    state under the lock, applies `fn`, saves, releases. `add`/`remove` both go through it.
  - **AC8 (tier-first conflict tie-break)**: `resolve_ports` processes workspaces in
    global tier order (all P1 candidates before any P2 is committed, all P2 before any P3),
    batching same-tier candidates and breaking ties within a batch by registry order. A
    losing candidate is bumped to the next tier in *that workspace's own* fallback chain
    (P1 loses → its own P2 value if set, else P3; P2 loses → P3). This makes a
    later-registered workspace's P1 pin correctly beat an earlier-registered workspace's P2
    pin — the bug the original registry-order-only draft had.

## Evidence
- Files created: `src/agent_orchestrator/service/__init__.py`,
  `src/agent_orchestrator/service/paths.py`, `src/agent_orchestrator/service/registry.py`,
  `src/agent_orchestrator/service/ports.py`, `tests/service/__init__.py`,
  `tests/service/test_registry.py`, `tests/service/test_ports.py`,
  `tests/service/test_project_config_ui.py`.
- Files edited: `src/agent_orchestrator/project_config.py` (added `UIConfig` class + `ui:
  UIConfig = UIConfig()` field only — diff is a clean two-block addition, nothing else in
  the file touched; confirmed via `git diff`).
- `uv run pytest -q tests/service/`: **24 passed**, 0 failed (0 skipped). Includes AC9(a)
  (`TestConcurrentMutate` — a deterministic thread-synchronized test proving a second
  `mutate()` call genuinely blocks on the lock and neither writer's change is lost, plus an
  8-thread stress variant) and AC9(b) (`test_cross_tier_conflict_p1_beats_earlier_p2` — an
  earlier-registered P2 pin loses to a later-registered P1 pin on the same port, not the
  reverse).
- `uv run pytest -q` (full suite, before vs after): repo baseline was already green;
  full-suite run with this task's changes in place: **1841 passed, 7 skipped** (pre-existing
  opt-in `real_llm`/`swebench` markers), **0 failed** — 24 of the 1841 are new in this task,
  zero regressions elsewhere.
- `uv run ruff check src/agent_orchestrator/service/ src/agent_orchestrator/project_config.py
  tests/service/`: All checks passed.
- `uv run ruff format --check` on the same paths: all files already formatted (one line
  wrapped for E501 in `service/__init__.py`; `ruff format` applied to `ports.py` and
  `test_project_config_ui.py` for minor wrapping, both re-verified clean after).
- `uv run mypy src` (whole tree, per instructions — mypy is whole-package): **4 errors**,
  all in `src/agent_orchestrator/_version.py` (generated file, pre-existing baseline per
  task instructions) — **0 new errors** introduced by this task's files. Before/after count
  is identical (4 → 4).

## Deviations / judgment calls (flagged per task instructions)
1. **`PortResolution` gained a `tiers: dict[str, str]` field** beyond the `{root: port}` +
   `conflicts` shape the ticket describes — not a locked name (only `ServiceRegistry`,
   `resolve_ports`, `persist_resolution` are locked per the ticket's Schemas/Interface
   Notes), and necessary so `persist_resolution` can tell a P1-sourced port from a
   P3-sourced one without re-deriving P1 itself (which would duplicate the best-effort
   config lookup and its exception handling). Downstream tasks reading `PortResolution`
   should treat `tiers` as available, additive data.
2. **`ServiceRegistry.add`/`remove` convenience methods** were added on top of the required
   `mutate()` primitive. The ticket's own Description line ("pure data/logic + the three
   registry-mutating CLI commands") and Handoff Boundary ("CLI `add`/`remove`/`list` call
   `ServiceRegistry`") both point at `ServiceRegistry` needing to expose this; `cli.py`
   itself (out of scope here) can now call `registry.add(...)`/`registry.remove(...)`
   directly rather than hand-rolling a `mutate` closure. No `service/cli.py` file was
   created or touched.
3. **P1 lookup uses `find_project_config(start=root)` (directory walk-up), exactly as the
   ticket's AC3 specifies** ("via `find_project_config`/`load_project_config`") — this
   means a workspace root with no `.ao/config.yaml` of its own could, in principle, inherit
   an *ancestor* directory's config if one exists between the workspace root and a `.git`
   boundary/filesystem root. Verified this repo's own sandbox has no `.ao/config.yaml`
   anywhere between `/tmp` and `/`, so test hermeticity holds; tests additionally stamp a
   `.git` marker at each test's `tmp_path` root to bound the walk explicitly regardless of
   environment (per the ticket's own Risks note).
4. **Conflict `reason` strings are free text**, not an enum — the ticket didn't lock exact
   wording, so these are implementation-owned and covered by content-shape (not
   exact-string) assertions where tests check them.

## Interface confirmation for downstream tasks
- `service.registry.ServiceRegistry` — `load()`, `save(data)`, `mutate(fn)`, `add(root,
  port=None, autoresume=True)`, `remove(root)`, `.path` property. Names unchanged from the
  ticket's lock.
- `service.ports.resolve_ports(registry: ServiceRegistryFile) -> PortResolution` and
  `service.ports.persist_resolution(registry, resolution) -> ServiceRegistryFile`. Names
  unchanged from the ticket's lock; `persist_resolution` writes back only P3-sourced picks.
- `ProjectConfig.ui.port: int | None`.

## Risks / Blockers
- None blocking handoff. Carried, documented (not fixed, per ticket Risks): `bind(0)`
  TOCTOU between P3 port selection and the real service's later bind — the spawn-time
  EADDRINUSE backstop is `T-Sv9d4k`'s job.

## Next actions
1. `T-Sv9d4k-supervisor-boot-resume` imports `service.registry.ServiceRegistry` and
   `service.ports.resolve_ports`/`persist_resolution` at boot; persists via
   `ServiceRegistry.mutate`, not a bare `save()`.
2. `T-Hb3x7q-hub-systemd-cli`'s CLI `add`/`remove`/`list` call `ServiceRegistry` directly
   (see judgment call #2 above for the convenience methods now available).

---
- By: claude · Role: developer · Date: 2026-08-28 · Comment: `T-Gr8s2a-registry-ports-config`
  done — registry/ports/`ProjectConfig.ui` foundation layer shipped with both early-gate
  items (locked registry writes, tier-first port conflict tie-break) implemented and
  dedicated-tested (AC7-9). 24 new tests, full suite 1841 passed/7 skipped/0 failed, ruff
  clean, mypy 4 (baseline, unchanged, all `_version.py`). See Evidence above for full detail.
