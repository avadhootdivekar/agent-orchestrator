# TASK: T-Wp4Nz5-workspace-provider-seam

## Metadata
- Task ID: `T-Wp4Nz5-workspace-provider-seam`
- Epic ID: `E-Bt4Xk9-complex-benchmark-tiers`
- Owner: developer agent
- Created: 2026-07-22
- Last Updated: 2026-07-22
- Status: Draft
- Estimate: 2.0 days

## Requirements Mapping
- FR-4 (WorkspaceProvider ABC + registry; `fixture` default provider; optional task `source` field)

## Description
Generalize how a task's `ws/repo` is produced so the large tier can materialize a repo checkout instead of copying a committed fixture. Introduce a `WorkspaceProvider` ABC + `WORKSPACE_PROVIDER_REGISTRY` (mirroring `SUBJECT_REGISTRY`/`GRADER_REGISTRY`). Refactor today's fixture-copy behavior into a default `fixture` provider — with **no behavior change** when a task has no `source`. Add an optional task-level `source` object (`{type: "...", ...}`) to the suite schema; when present, `materialize_workspace` dispatches to `WORKSPACE_PROVIDER_REGISTRY[source.type]` instead of copying `task.fixture`, and `fixture` becomes optional. Keep `materialize_workspace`'s call signature backward-compatible so the runner (T-Bg2Wq4/T-Pl3Rx7) needs no edit.

## File ownership (exclusive)
- `src/agent_orchestrator/bench/workspace.py` — `WorkspaceProvider` ABC, `FixtureProvider` (extract current copytree logic), dispatch in `materialize_workspace`. (No other task edits workspace.py except read-only.)
- `src/agent_orchestrator/bench/registries.py` — add `WORKSPACE_PROVIDER_REGISTRY` + `register_workspace_provider`. (T-Sw5Hd9/T-Sg6Jf2 append registrations later — sequence after this.)
- `src/agent_orchestrator/bench/spec.py` — add optional `Source` model + `BenchTask.source`; make `fixture` optional-when-`source`-present; validate `source.type`. (Sequenced AFTER T-Tr1Km8; both edit spec.py.)
- `benchmarks/schemas/benchmark-suite.schema.json` — add optional `source` object to the task def; relax `fixture` from required to conditionally-required. (Sequenced AFTER T-Tr1Km8.)
- (read-only) `bench/runner.py` — confirm the unchanged `materialize_workspace(...)` call still works.

## Inputs / Outputs
- Inputs: a `BenchTask` (with `fixture` OR `source`), a target `ws` path.
- Outputs: a materialized `RunContext` — identical to today for `fixture`; a provider-materialized `ws/repo` otherwise.

## Acceptance Criteria
1. **Given** a `dev-core` task (no `source`) **When** `materialize_workspace` runs **Then** the result is byte-identical to today (fixture copytree + `INSTRUCTION.md`); existing workspace tests pass unedited.
2. `WORKSPACE_PROVIDER_REGISTRY` contains `"fixture"` at import time; `register_workspace_provider` raises on duplicate (mirrors `register_subject`).
3. **Given** a task with `source: {type: "unknown"}` **When** `load_suite` runs **Then** `SpecValidationError` naming the task + listing `KNOWN_WORKSPACE_PROVIDER_TYPES`.
4. **Given** a task with `source` present and no `fixture` **When** loaded **Then** OK (fixture not required); **Given** neither `source` nor `fixture` **Then** `SpecValidationError`.
5. A `WorkspaceProvider.prepare(task, ws_repo_dir, ctx_meta)` contract is defined; `FixtureProvider.prepare` reproduces the current copytree + guard behavior; the sandbox path-escape guard (`_assert_under_bench_root`) still applies to the workspace target for every provider.
6. SI-1 unaffected (grep + import test).

## Risks
- Backward-compatibility: the refactor must not change the fixture path resolution (relative to `suite_base_dir`) or the `INSTRUCTION.md` copy step. Diff the RunContext fields before/after on a `dev-core` task in a test.
- `KNOWN_WORKSPACE_PROVIDER_TYPES` in spec.py mirrors the `KNOWN_GRADER_TYPES`/`KNOWN_SUBJECT_TYPES` closed-list pattern (schema leaves `type` open; the loader enforces the closed list) — the `swebench` provider adds its type there in T-Sw5Hd9.

## Pseudocode / Algorithm
```text
# registries.py
WORKSPACE_PROVIDER_REGISTRY: dict[str, type] = {}
def register_workspace_provider(name, cls): ...  # duplicate -> ValueError

# spec.py
class Source(BaseModel):
    type: str
    # provider-specific fields (open; the swebench provider reads instance_id/dataset/revision)
    model_config = ConfigDict(extra="allow")     # provider fields validated by the provider, not here
class BenchTask(BaseModel):
    ...
    fixture: str | None = None                    # now optional
    source: Source | None = None
KNOWN_WORKSPACE_PROVIDER_TYPES = frozenset({"fixture"})   # T-Sw5Hd9 adds "swebench"
# in load_suite per-task:
IF t.source is None and t.fixture is None: RAISE SpecValidationError("task needs fixture or source")
IF t.source is not None and t.source.type not in KNOWN_WORKSPACE_PROVIDER_TYPES: RAISE ...
# fixture existence check only when source is None (a source provides its own repo)

# workspace.py
class WorkspaceProvider(ABC):
    @abstractmethod
    def prepare(self, task, repo_dir: Path, *, suite_base_dir, subject_base_dir) -> None: ...
class FixtureProvider(WorkspaceProvider):
    def prepare(self, task, repo_dir, *, suite_base_dir, **_):
        src = (suite_base_dir / task.fixture).resolve()
        copytree(src, repo_dir)                   # exactly today's logic
register_workspace_provider("fixture", FixtureProvider)
def materialize_workspace(task, ws, *, suite_base_dir, ...):
    ws_path = _assert_under_bench_root(Path(ws)); reset dir
    repo_dir = ws_path / "repo"
    provider = WORKSPACE_PROVIDER_REGISTRY[(task.source.type if task.source else "fixture")]()
    provider.prepare(task, repo_dir, suite_base_dir=suite_base_dir, subject_base_dir=subject_base_dir)
    copy INSTRUCTION.md (unchanged); mkdir capture; return RunContext(...)   # signature unchanged
```

## Schemas / Interface Notes
- `PROTOCOL WorkspaceProvider: prepare(task, repo_dir, *, suite_base_dir, subject_base_dir) -> None`.
- Schema: task gains optional `source: {type, ...}`; `fixture` conditionally required. `additionalProperties:false` retained (add `source` explicitly).
- Triggers/events: N/A. Artifacts: `ws/repo/` produced by the provider.

## Handoff Boundary
- Upstream: T-Tr1Km8 (spec.py/schema sequencing).
- Downstream: T-Sw5Hd9 registers the `swebench` provider + adds its type to `KNOWN_WORKSPACE_PROVIDER_TYPES`; T-Sg6Jf2's grader reads the same `ws/repo`. Do NOT implement the swebench provider here — only the seam + default fixture provider.

## Artifacts
- Docs/comments: this folder. Large outputs: none.
