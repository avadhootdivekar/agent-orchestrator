# TASK: T-Gr8s2a-registry-ports-config

## Metadata
- Task ID: `T-Gr8s2a-registry-ports-config`
- Epic ID: `E-GIytcL-multi-workspace-service`
- Owner: developer agent
- Created: 2026-08-28
- Last Updated: 2026-08-28
- Status: Done
- Estimate: < 2 days

## Requirements Mapping
- Requirement IDs: FR-1, FR-2, FR-7 (see `../EPIC.md`)

## Description
Foundation layer for the whole epic — registry file + port resolution + the
`ProjectConfig` addition everything else builds on. No daemon, no subprocess spawning, no
HTTP server in this task; pure data/logic + the three registry-mutating CLI commands.

Files you own (create/edit freely):
- `src/agent_orchestrator/service/__init__.py` (new package)
- `src/agent_orchestrator/service/paths.py` (new)
- `src/agent_orchestrator/service/registry.py` (new)
- `src/agent_orchestrator/service/ports.py` (new)
- `src/agent_orchestrator/project_config.py` (edit — add `UIConfig` + `ui` field only)
- `tests/service/test_registry.py`, `tests/service/test_ports.py`,
  `tests/service/test_project_config_ui.py` (new)

Do NOT touch: `service/supervisor.py`, `service/boot_resume.py`, `service/hub.py`,
`service/systemd.py`, `service/cli.py` (owned by later tasks in this epic — leave stubs
alone or absent), `models.py`, `executors/`, `spec.py`, `validate.py`,
`specs/*.schema.json`, `templates/`, `cli.py` (no edits in this task).

## Acceptance Criteria
1. `service/paths.py`: `default_registry_path() -> Path` respects `AO_SERVICE_CONFIG`
   (exact file path override) first, else `$XDG_CONFIG_HOME/ao/service.yaml` else
   `~/.config/ao/service.yaml`. `default_state_dir() -> Path` respects
   `AO_SERVICE_STATE_DIR` first, else `$XDG_STATE_HOME/ao/service` else
   `~/.local/state/ao/service`. Both are plain functions reading `os.environ` at call
   time (no caching) so tests can monkeypatch/env-set per-test.
2. `service/registry.py`: pydantic `WorkspaceEntry(root: str, port: int | None = None,
   autoresume: bool = True)` and `ServiceRegistryFile(workspaces: list[WorkspaceEntry] =
   [])`. `class ServiceRegistry` wraps a registry file path with `load() ->
   ServiceRegistryFile` (missing file → empty `ServiceRegistryFile()`, not an error) and
   `save(data: ServiceRegistryFile) -> None` using the same atomic write-then-rename
   pattern as `runstate.py`/`ui/processes.py` (`tmp` file + `os.replace`), YAML-encoded.
   `root` is always stored as an absolute, resolved path string (normalize on save/add,
   never trust caller-relative paths downstream).
3. `service/ports.py`: `resolve_ports(registry: ServiceRegistryFile) -> PortResolution`
   where `PortResolution` carries `{root: port}` for every workspace plus a list of
   `PortConflict(root, requested_port, reason, fallback_port)`. Implements P1
   (`.ao/config.yaml` `ui.port` via `find_project_config`/`load_project_config`, best-
   effort — a workspace with no/unreadable config just has no P1 opinion) > P2 (registry
   `port`) > P3 (bind `("127.0.0.1", 0)`, read back the OS-assigned port). Conflict rule:
   process workspaces in registry order; the first to claim a numeric port keeps it,
   every later workspace resolving to an already-claimed port is re-resolved via P3 and
   recorded as a `PortConflict`. Also treat "P1/P2 port is not actually bindable right
   now" as a conflict the same way (probe with a real bind to the specific port,
   `SO_REUSEADDR` off so a genuinely-busy port is caught). `resolve_ports` does **not**
   mutate the registry — a separate `persist_resolution(registry, resolution) ->
   ServiceRegistryFile` returns an updated `ServiceRegistryFile` with every P3-picked
   port written into the matching `WorkspaceEntry.port` (caller is responsible for
   calling `ServiceRegistry.save` with it — keeps this module side-effect-free/testable).
4. `project_config.py`: add
   ```python
   class UIConfig(BaseModel):
       port: int | None = None
   ```
   and `ui: UIConfig = UIConfig()` on `ProjectConfig`, with a one-line docstring
   referencing this epic. No other field/behavior in this file changes. Confirm (by
   reading, do not just assume) that `ProjectConfig` has no explicit `model_config`
   setting `extra="forbid"` — pydantic v2's BaseModel default is `extra="ignore"`, which
   is what makes this addition byte-compatible both directions; if you find an explicit
   override anywhere in this file that changes that default, stop and flag it in your
   handoff rather than silently changing security-relevant validation behavior.
5. Tests (deterministic, no real `~`, no network):
   - Registry round-trip: save → load → equal; missing file → empty; atomic-write
     (`.tmp` file does not linger after a successful save).
   - Port resolution: P1 wins over P2/P3 (construct a temp workspace dir with
     `.ao/config.yaml` containing `ui: {port: N}`); P2 wins over P3 when P1 absent; P3
     picks a free port when neither set (assert it's actually bindable at resolution
     time); a forced conflict (two registry entries both pinned to the same port) — first
     keeps it, second gets a different, free, non-conflicting port and appears in
     `conflicts`; a P1/P2 port that's already bound by another socket at resolution time
     also produces a conflict + fallback.
   - `ProjectConfig`: loading a config with no `ui:` key yields `UIConfig()` defaults;
     loading one with `ui: {port: 8899}` yields `cfg.ui.port == 8899`.
6. `uv run pytest -q tests/service/` green. `uv run ruff check
   src/agent_orchestrator/service/ src/agent_orchestrator/project_config.py` and
   `ruff format --check` on the same clean. `uv run mypy src` introduces zero new errors
   (run it on the whole `src` tree, since mypy is whole-package; report the full
   before/after error count in your handoff, not just "no new errors in my files").

## Acceptance Criteria — early-gate additions (2026-08-28, see HLD §5.1/§5.2/§6.2, ADR-0012 "Early-gate corrections")
A `reviewer`+`architect` early-gate pass (run before this task started) found two gaps in
the original plan below; both are now required, not optional:

7. `ServiceRegistry` writes are **not** bare load-then-save. Add file locking (`flock` on
   the registry path, or a companion `.lock` file — pick whichever is simpler to make
   correct, document the choice) and a `mutate(fn: Callable[[ServiceRegistryFile],
   ServiceRegistryFile]) -> ServiceRegistryFile` method (or equivalent) that: acquires the
   lock, re-reads the current file from disk (not a caller-cached copy), applies `fn`,
   saves, releases. Every registry-mutating call site — `add`/`remove` (this task's own
   CLI-adjacent code, if any lands here) and `T-Sv9d4k`'s port-persistence step — must go
   through this, not `load()`+`save()` called separately, or a concurrent `ao service add`
   racing the supervisor's own port-persistence at boot can silently lose one writer's
   change (atomic write-then-rename prevents corruption, not a lost update).
8. `resolve_ports`'s conflict tie-break is **tier-first, registry-order second**: among
   workspaces landing on the same port, the highest-precedence source (P1 beats P2 beats
   P3) keeps it; only a same-tier collision falls back to registry order. A workspace that
   loses falls back one tier from where it lost (a beaten P2 tries P3, not a re-check of
   P2). The originally-drafted "first in registry order always wins" rule is WRONG — it
   let a P3 random pick registered earlier beat a later workspace's explicit P1 config pin,
   contradicting the whole point of the precedence order. Get this right the first time;
   it is covered by a dedicated test (AC9).
9. New tests: (a) a simulated concurrent writer (e.g. two `ServiceRegistry` instances
   against the same path, one calling `mutate` while the other's stale read would otherwise
   clobber it) proving the lost-update case cannot happen; (b) a forced cross-tier
   conflict — one workspace pinned via P1 (`.ao/config.yaml`), a different, earlier-in-
   registry-order workspace pinned via P2 to the same numeric port — asserting the P1
   workspace keeps the port and the P2 workspace is re-resolved via P3, not the reverse.

## Risks
- `find_project_config`/`load_project_config` walk up to a `.git` boundary — a temp test
  workspace under `tmp_path` has no `.git`, so it will walk all the way to filesystem
  root unless the test workspace itself contains `.ao/config.yaml` directly (which is the
  intended case for P1) or you pass a workspace root explicitly rather than relying on
  cwd-based discovery. Prefer calling `load_project_config` directly against a known
  `.ao/config.yaml` path you construct in the test, not the cwd-searching
  `find_project_config`, to keep the test hermetic regardless of where pytest runs from.
- Binding `("127.0.0.1", 0)` and then closing the socket before the real service binds it
  is inherently a TOCTOU race in production (someone else could grab the port in
  between) — acceptable for this MVP (documented as an accepted limitation, not silently
  ignored) since the conflict-detection path in the actual spawn sequence (owned by
  `T-Sv9d4k-supervisor-boot-resume`) is the real backstop; this task's job is the
  resolution *algorithm*, not eliminating every race.

## Dependencies
- None (first task; everything else in the epic imports from this one).

## Schemas / Interface Notes
- Interface: `service.registry.ServiceRegistry`, `service.ports.resolve_ports` /
  `persist_resolution` — these are the exact names `T-Sv9d4k` and `T-Hb3x7q` will import.
  Do not rename without updating this ticket (they were locked in the HLD's module-layout
  table, §4).
- Spec/data schema: `~/.config/ao/service.yaml` shape per HLD §5.1.
- Artifacts: registry file at the resolved path; no other artifacts from this task.

## Handoff Boundary
- Upstream: HLD (`docs-md/multi-workspace-service-hld.md` §4, §5.1, §6.2), ADR-0012 D3.
- Downstream: `T-Sv9d4k-supervisor-boot-resume` (supervisor calls `resolve_ports` +
  `persist_resolution` at boot) and `T-Hb3x7q-hub-systemd-cli` (CLI `add`/`remove`/`list`
  call `ServiceRegistry`; `status` reads `port_resolution.json` your port module's
  *caller* writes — writing that file is `T-Sv9d4k`'s job, not this task's).

## Artifacts
- Docs/comments: `meta/tickets/E-GIytcL-multi-workspace-service/T-Gr8s2a-registry-ports-config/`
- Large outputs: N/A
