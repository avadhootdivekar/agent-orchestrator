"""Workspace materialization for bench subjects (design doc §4.2, §4.5; task T-Sbj9Ka;
`WorkspaceProvider` seam added by T-Wp4Nz5, FR-4).

For each (subject x task) run, `materialize_workspace` builds a fresh, disposable
workspace under the repo-local, gitignored `playground/.tmp/bench/` tree (constraint
C2: workspaces are never `/tmp` -- mirrors `tests/playground/conftest.py`'s
`_REAL_LLM_TMP_BASE` convention, since a spawned `claude`/`ao` subprocess's own sandbox
allow-list is the repo working directory, not the system temp dir):

    ws/                     caller-composed target, e.g.
                            playground/.tmp/bench/<runid>/<subject>/<task>/
      repo/                 mutable repo populated by a `WorkspaceProvider` (never a
                            committed source touched in place -- AC6) + a copied
                            INSTRUCTION.md so every subject (bare `claude -p` via
                            {instruction} in its prompt, or an `ao_workflow` subject
                            whose first task reads the file directly out of its repo)
                            can reach the task instruction the same way, by path.
      capture/              subject-populated transcript/stdout/stderr (T-Sbj9Ka
                            subjects.py writes into this directory; created empty here).

`ws/repo` is populated by dispatching to `WORKSPACE_PROVIDER_REGISTRY[task.source.type
or "fixture"]` (T-Wp4Nz5): the default `fixture` provider copies the task's committed
fixture dir (unchanged behavior -- the copy step that lived directly in
`materialize_workspace` before this task now lives in `FixtureProvider.prepare`, moved
verbatim). A task with a `source` set (e.g. a future `swebench` provider, T-Sw5Hd9)
instead has its provider materialize `ws/repo` however it needs to (a `git` checkout,
for instance); `materialize_workspace`'s own signature/return (`RunContext`) is
unchanged either way, so `runner.py`'s call site needs no edit.

The target `ws` path is path-guarded to stay under `BENCH_WORKSPACE_ROOT` (mirrors
`LocalFsArtifactStore.resolve`'s escape guard) -- a `..` traversal or a symlinked path
component that resolves outside the sandbox raises `SubjectError` rather than silently
writing outside `playground/.tmp/bench/` (AC6). The guard applies to the WORKSPACE
target for every provider: the task's fixture/instruction/source inputs live elsewhere
in the repo (or, for a non-fixture provider, at some external location) and are
read-only inputs, never write targets.
"""

from __future__ import annotations

import shutil
from abc import ABC, abstractmethod
from pathlib import Path

from pydantic import BaseModel

from .errors import SubjectError
from .registries import WORKSPACE_PROVIDER_REGISTRY, register_workspace_provider
from .spec import BenchTask

# Repo root: bench/workspace.py -> bench/ -> agent_orchestrator/ -> src/ -> repo root.
# Mirrors bench/spec.py's `_SCHEMAS_DIR` resolution (same four `.parent` hops) so both
# modules agree on where "the repo" is without hardcoding an absolute path.
REPO_ROOT: Path = Path(__file__).resolve().parent.parent.parent.parent

# Repo-local, gitignored sandbox for every bench workspace (constraint C2). Already
# covered by the existing `playground/.tmp/` gitignore entry.
BENCH_WORKSPACE_ROOT: Path = REPO_ROOT / "playground" / ".tmp" / "bench"

# Named, not magic literals (CLAUDE.md).
REPO_DIRNAME = "repo"
CAPTURE_DIRNAME = "capture"
INSTRUCTION_FILENAME = "INSTRUCTION.md"

# The provider a task without a `source` field dispatches to (T-Wp4Nz5) -- matches
# `bench/spec.py`'s `KNOWN_WORKSPACE_PROVIDER_TYPES` default member.
DEFAULT_WORKSPACE_PROVIDER_TYPE = "fixture"


class RunContext(BaseModel):
    """Resolved paths + run knobs for one (subject, task) execution (design doc §6).

    Produced by `materialize_workspace` and consumed by `Subject.run(task, ctx)`
    (subjects.py). `workflow_json` is populated by the caller for an `ao_workflow`
    subject (points at that subject's own template, resolved relative to its
    subject.json -- see `subject_base_dir` below); `rendered_reposet` is populated by
    `AoWorkflowSubject` itself once it has rendered the reposet template for this task.

    `subject_base_dir` is additive beyond the design doc §6 listing: the directory
    containing the running subject's own `subject.json`, needed so `AoWorkflowSubject`
    can resolve that spec's `workflow`/`reposets`/`agents` fields, which
    `benchmarks/schemas/subject.schema.json` documents as "relative to this
    subject.json" (bench/spec.py's `load_subject` does not resolve them -- see
    subjects.py module docstring). Optional and defaulted so a `RunContext` built from
    exactly the §6 field set still constructs unchanged.
    """

    workspace: str
    repo_dir: str
    instruction_path: str
    capture_dir: str
    timeout_seconds: int
    budget_total: int | None = None
    max_turns: int | None = None
    workflow_json: str | None = None
    rendered_reposet: str | None = None
    subject_base_dir: str | None = None


def _assert_under_bench_root(path: Path) -> Path:
    """Resolve *path* and assert it stays under `BENCH_WORKSPACE_ROOT`.

    Catches both a caller-composed `..` traversal and a symlinked path component that
    would otherwise resolve outside the sandbox (AC6; mirrors
    `LocalFsArtifactStore.resolve`'s path-escape guard in `artifacts.py`). Raises
    `SubjectError` naming both the requested and resolved path -- never silently clamps.
    """
    root = BENCH_WORKSPACE_ROOT.resolve()
    resolved = path.resolve()
    if resolved != root and root not in resolved.parents:
        raise SubjectError(
            f"Bench workspace path escapes the sandbox {root}: {path} -> resolved {resolved}"
        )
    return resolved


class WorkspaceProvider(ABC):
    """Materializes a task's `ws/repo` directory (T-Wp4Nz5, FR-4).

    `materialize_workspace` below resolves the concrete provider for a task --
    `WORKSPACE_PROVIDER_REGISTRY[task.source.type]` when `task.source` is set, else the
    `DEFAULT_WORKSPACE_PROVIDER_TYPE` ("fixture") -- and delegates to it. A provider owns
    everything about how `repo_dir` gets populated (copy a committed fixture, check out
    a real repo at a pinned commit, ...); it must NOT write anywhere else under `ws`
    (`INSTRUCTION.md`/`capture/` are `materialize_workspace`'s own responsibility, common
    to every provider) and must not touch anything outside `repo_dir` (the sandbox guard
    in `materialize_workspace` already confines `ws` itself; a provider that resolves
    its own external inputs, e.g. a checkout cache, is responsible for not writing back
    into them -- see `_assert_under_bench_root`'s module-docstring note that the guard
    only covers the workspace TARGET).
    """

    @abstractmethod
    def prepare(
        self,
        task: BenchTask,
        repo_dir: Path,
        *,
        suite_base_dir: Path,
        subject_base_dir: Path | None = None,
    ) -> None:
        """Create and populate *repo_dir* for *task*.

        `repo_dir` does NOT exist yet when this is called (mirrors `shutil.copytree`'s
        own precondition that its destination must not already exist) -- a provider is
        responsible for creating it, by whatever means (a plain copy, a `git` checkout,
        ...). `suite_base_dir` is the task's owning suite.json's directory
        (fixture/instruction paths, when the task declares them, are relative to this).
        `subject_base_dir` is the directory containing the running subject's own
        subject.json, passed through for a provider that needs it (none do today). Raise
        `SubjectError` for any provider-specific failure (mirrors the
        fixture-not-found/instruction-not-found convention already established by
        `materialize_workspace`).
        """
        raise NotImplementedError


class FixtureProvider(WorkspaceProvider):
    """Default provider (T-Wp4Nz5): copy the task's committed fixture dir into `repo_dir`.

    This is the exact copy step that lived directly in `materialize_workspace` before
    this task -- moved here verbatim so a `source`-less task's behavior stays
    byte-identical (AC1).
    """

    def prepare(
        self,
        task: BenchTask,
        repo_dir: Path,
        *,
        suite_base_dir: Path,
        subject_base_dir: Path | None = None,
    ) -> None:
        if task.fixture is None:
            # Guarded at suite-load time by bench/spec.py's load_suite (a fixture-less,
            # source-less task is rejected there) -- this is defense-in-depth for a
            # BenchTask constructed directly (e.g. in a unit test) that bypasses load_suite.
            raise SubjectError(f"Task {task.id!r}: 'fixture' provider requires task.fixture")
        fixture_src = (suite_base_dir / task.fixture).resolve()
        if not fixture_src.is_dir():
            raise SubjectError(f"Task {task.id!r}: fixture directory not found: {fixture_src}")
        shutil.copytree(fixture_src, repo_dir)


# Registered at import time (design §6, mirrors subjects.py/graders.py's own
# register_subject/register_grader calls) so WORKSPACE_PROVIDER_REGISTRY["fixture"]
# exists as soon as this module is imported (AC2) -- runner.py already imports
# bench.workspace, so no separate wiring is needed for the default provider to be live.
register_workspace_provider(DEFAULT_WORKSPACE_PROVIDER_TYPE, FixtureProvider)


def materialize_workspace(
    task: BenchTask,
    ws: Path | str,
    *,
    suite_base_dir: Path | str,
    timeout_seconds: int,
    budget_total: int | None = None,
    max_turns: int | None = None,
    subject_base_dir: Path | str | None = None,
    workflow_json: str | None = None,
) -> RunContext:
    """Materialize a fresh, disposable workspace for one (subject, task) run.

    - Guards `ws` under `BENCH_WORKSPACE_ROOT` (raises `SubjectError` on escape, AC6).
    - Removes any pre-existing `ws` (fresh workspace per (subject, task) -- ASSUMPTION
      A4: exactly one `ao` run_id must land under this workspace, so a stale prior run
      cannot linger and confuse cost attribution).
    - Dispatches to `WORKSPACE_PROVIDER_REGISTRY[task.source.type or "fixture"]`
      (T-Wp4Nz5) to populate `ws/repo/` -- the default `fixture` provider copies
      `task.fixture` (resolved against `suite_base_dir`, mirrors `spec.py`'s own
      `load_suite` path resolution); the committed fixture itself is never touched
      (AC6), only this copy. A non-default provider (e.g. a future `swebench` checkout)
      populates `ws/repo/` however it needs to.
    - Copies `task.instruction` into `ws/repo/INSTRUCTION.md` so every subject can reach
      the instruction the same way, by path (see module docstring).
    - Creates an empty `ws/capture/` for the subject to populate.

    Raises `SubjectError` for: a `ws` that escapes the bench sandbox, an unregistered
    workspace-provider type (defense-in-depth -- `bench/spec.py`'s `load_suite` already
    rejects this at suite-load time), or a fixture/instruction path that does not exist
    on disk (mirrors `spec.py`'s own missing-path errors, but at materialize time rather
    than suite-load time -- a fixture/instruction could theoretically be removed between
    `load_suite` and a run).
    """
    ws_path = _assert_under_bench_root(Path(ws))
    if ws_path.exists():
        shutil.rmtree(ws_path)
    ws_path.mkdir(parents=True)

    base = Path(suite_base_dir)
    provider_type = task.source.type if task.source is not None else DEFAULT_WORKSPACE_PROVIDER_TYPE
    provider_cls = WORKSPACE_PROVIDER_REGISTRY.get(provider_type)
    if provider_cls is None:
        raise SubjectError(
            f"Task {task.id!r}: unknown workspace provider {provider_type!r}; "
            f"known providers: {sorted(WORKSPACE_PROVIDER_REGISTRY)}"
        )
    resolved_subject_base_dir = Path(subject_base_dir) if subject_base_dir is not None else None

    repo_dir = ws_path / REPO_DIRNAME
    provider_cls().prepare(
        task, repo_dir, suite_base_dir=base, subject_base_dir=resolved_subject_base_dir
    )

    instruction_src = (base / task.instruction).resolve()
    if not instruction_src.is_file():
        raise SubjectError(f"Task {task.id!r}: instruction file not found: {instruction_src}")
    instruction_dest = repo_dir / INSTRUCTION_FILENAME
    shutil.copy2(instruction_src, instruction_dest)

    capture_dir = ws_path / CAPTURE_DIRNAME
    capture_dir.mkdir()

    return RunContext(
        workspace=str(ws_path),
        repo_dir=str(repo_dir),
        instruction_path=str(instruction_dest),
        capture_dir=str(capture_dir),
        timeout_seconds=timeout_seconds,
        budget_total=budget_total,
        max_turns=max_turns,
        workflow_json=workflow_json,
        subject_base_dir=str(resolved_subject_base_dir)
        if resolved_subject_base_dir is not None
        else None,
    )
