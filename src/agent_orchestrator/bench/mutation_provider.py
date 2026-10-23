"""`repo_mutation` workspace provider (HLD §4.2 Tier 2b/2c/2d).

Checks a pinned public repo out into `repo_dir` (shared repo cache, `swebench_provider`'s
helpers), applies a *recipe* -- inject a bug / excise a feature + its tests / describe an
enhancement -- and parks everything the agent must not see under `grading_dir`:

    task.source = {
        "type": "repo_mutation",
        "repo": "https://github.com/owner/name",      # or owner/name, or any clone URL
        "base_commit": "<40-hex sha>",
        "recipe": "tasks/<id>/recipe.json",            # relative to the suite dir
    }

Recipe JSON (`MutationRecipe` below; unknown keys are rejected so typos fail loudly):

    {
      "mode": "inject" | "excise" | "enhance",
      "edits": [{"file": "pkg/x.py", "find": "...", "replace": "...", "count": 1}],
      "remove": ["docs/feature.md"],                   # deleted from repo_dir, not restored
      "hidden": {"tests/test_x.py": "hidden/test_x.py"},   # suite-relative source -> repo path
      "hidden_from_repo": ["tests/test_y.py"],         # taken from the checkout, then removed
      "fail_to_pass": ["tests/test_x.py::test_bug"],
      "pass_to_pass": ["tests/test_x.py::test_ok"],
      "test_targets": [],                              # whole files/dirs (see pytest_targeted)
      "install": "uv venv .venv && uv pip install -e . pytest",
      "test_command": "{python} -m pytest -q -p no:cacheprovider {targets}",
      "verified": {"date": "2026-10-23", "note": "..."}
    }

`hidden` / `hidden_from_repo` land in `grading_dir/hidden/<repo-relative path>` and are
restored into the repo only by the `pytest_targeted` grader at grade time -- they are never
left in `repo_dir`. Files an edit/`remove` touches are also parked (pristine) in
`grading_dir/reference/` so `FakeSubject(scripted_effect="copy-reference")` can prove the
recipe is solvable at zero cost. `recipe.resolved.json` is the provider -> grader contract.

After mutation the tree is re-snapshotted as ONE parentless commit (`tier2_common`
docstring): `git diff` shows only the agent's own work, and neither `git stash` nor
`git checkout .` can resurrect the pristine code. The grader measures collateral damage
against `baseline_commit` from `recipe.resolved.json`.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .errors import SubjectError
from .registries import register_workspace_provider
from .spec import BenchTask
from .tier2_common import (
    HIDDEN_DIRNAME,
    REFERENCE_DIRNAME,
    checkout_pinned,
    resolve_inside,
    safe_relpath,
    snapshot_single_commit,
    write_hidden,
    write_resolved,
)
from .workspace import WorkspaceProvider

REPO_MUTATION_PROVIDER_TYPE = "repo_mutation"
_PROVIDER = "repo_mutation"
_SOURCE_FIELDS = ("repo", "base_commit", "recipe")
_FIND_PREVIEW_CHARS = 60


class MutationEdit(BaseModel):
    """One literal find/replace in one file; `count` is the exact occurrence count expected."""

    model_config = ConfigDict(extra="forbid")

    file: str
    find: str = Field(min_length=1)
    replace: str
    count: int = Field(default=1, ge=1)


class MutationRecipe(BaseModel):
    """Parsed `recipe.json` (module docstring)."""

    model_config = ConfigDict(extra="forbid")

    mode: Literal["inject", "excise", "enhance"]
    description: str | None = None
    edits: list[MutationEdit] = []
    remove: list[str] = []
    hidden: dict[str, str] = {}
    hidden_from_repo: list[str] = []
    fail_to_pass: list[str] = []
    pass_to_pass: list[str] = []
    test_targets: list[str] = []
    install: str | None = None
    test_command: str | None = None
    verified: dict[str, Any] | None = None


def load_recipe(path: Path) -> MutationRecipe:
    """Read + validate a recipe file; every failure is a `SubjectError` naming the file."""
    try:
        data = json.loads(path.read_text())
    except OSError as exc:
        raise SubjectError(f"{_PROVIDER} provider: cannot read recipe {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise SubjectError(f"{_PROVIDER} provider: recipe {path} is not valid JSON: {exc}") from exc
    try:
        recipe = MutationRecipe.model_validate(data)
    except ValidationError as exc:
        raise SubjectError(f"{_PROVIDER} provider: invalid recipe {path}: {exc}") from exc
    _check_mode_requirements(recipe, path)
    return recipe


def _check_mode_requirements(recipe: MutationRecipe, path: Path) -> None:
    """Per-mode structural rules (HLD §4.2): what each mode needs to be gradeable."""
    problems: list[str] = []
    restores = bool(recipe.hidden or recipe.hidden_from_repo)
    if not (recipe.fail_to_pass or recipe.test_targets):
        problems.append("needs fail_to_pass and/or test_targets")
    if recipe.mode == "inject" and not recipe.edits:
        problems.append("inject mode needs at least one edit")
    if recipe.mode == "excise":
        if not (recipe.edits or recipe.remove or recipe.hidden_from_repo):
            problems.append("excise mode must remove something (edits/remove/hidden_from_repo)")
        if not restores:
            problems.append("excise mode must restore tests (hidden/hidden_from_repo)")
    if recipe.mode == "enhance" and not recipe.hidden:
        problems.append("enhance mode needs hidden tests (hidden)")
    dupes = set(recipe.hidden) & set(recipe.hidden_from_repo)
    if dupes:
        problems.append(f"paths in both hidden and hidden_from_repo: {sorted(dupes)}")
    if problems:
        raise SubjectError(f"{_PROVIDER} provider: recipe {path}: " + "; ".join(problems))


def _read_text(path: Path, *, what: str) -> str:
    try:
        return path.read_bytes().decode("utf-8")
    except FileNotFoundError as exc:
        raise SubjectError(f"{_PROVIDER} provider: {what}: file not found: {path}") from exc
    except (OSError, UnicodeDecodeError) as exc:
        raise SubjectError(f"{_PROVIDER} provider: {what}: cannot read {path}: {exc}") from exc


def apply_edit(repo_dir: Path, edit: MutationEdit, *, task_id: str) -> None:
    """Apply one edit; the `find` text must occur exactly `edit.count` times (else
    `SubjectError`) so a recipe pinned to one commit can never silently mis-apply."""
    what = f"task {task_id!r} edit on {edit.file!r}"
    target = resolve_inside(repo_dir, edit.file, what=what)
    text = _read_text(target, what=what)
    found = text.count(edit.find)
    if found != edit.count:
        preview = edit.find[:_FIND_PREVIEW_CHARS]
        raise SubjectError(
            f"{_PROVIDER} provider: {what}: `find` {preview!r} matched {found} time(s), "
            f"expected {edit.count}"
        )
    # bytes round-trip: CRLF and a missing trailing newline survive untouched.
    target.write_bytes(text.replace(edit.find, edit.replace).encode("utf-8"))


def _source_fields(task: BenchTask) -> dict[str, str]:
    if task.source is None or task.source.type != REPO_MUTATION_PROVIDER_TYPE:
        raise SubjectError(
            f"Task {task.id!r}: repo_mutation provider requires source.type == "
            f"{REPO_MUTATION_PROVIDER_TYPE!r}"
        )
    extra = task.source.model_extra or {}
    missing = [k for k in _SOURCE_FIELDS if not extra.get(k)]
    if missing:
        raise SubjectError(f"Task {task.id!r}: source is missing required field(s) {missing}")
    return {k: str(extra[k]) for k in _SOURCE_FIELDS}


def _stash_reference(repo_dir: Path, grading_dir: Path, rel: str) -> None:
    """Copy the pristine *rel* (before any mutation) to `grading_dir/reference/<rel>`."""
    src = repo_dir / rel
    if not src.is_file():
        return  # nothing to restore (e.g. `remove` of an absent path is validated elsewhere)
    dest = resolve_inside(grading_dir / REFERENCE_DIRNAME, rel, what="reference path")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)


def _resolve_hidden_sources(
    recipe: MutationRecipe, suite_base_dir: Path, task_id: str
) -> dict[str, Path]:
    """Validate `hidden` (repo path -> suite-relative source); the source must be a file
    inside the suite dir (a recipe cannot pull arbitrary host files into the grader)."""
    suite_root = suite_base_dir.resolve()
    out: dict[str, Path] = {}
    for repo_rel, source_rel in recipe.hidden.items():
        what = f"task {task_id!r} hidden {repo_rel!r}"
        safe_relpath(repo_rel, what=what)
        source = (suite_base_dir / source_rel).resolve()
        if suite_root not in source.parents:
            raise SubjectError(f"{_PROVIDER} provider: {what}: source {source_rel!r} escapes suite")
        if not source.is_file():
            raise SubjectError(f"{_PROVIDER} provider: {what}: source file not found: {source}")
        out[repo_rel] = source
    return out


class RepoMutationProvider(WorkspaceProvider):
    """Checkout + recipe -> mutated `repo_dir` and a populated `grading_dir`."""

    def prepare(
        self,
        task: BenchTask,
        repo_dir: Path,
        *,
        suite_base_dir: Path,
        subject_base_dir: Path | None = None,
        grading_dir: Path | None = None,
    ) -> None:
        if grading_dir is None:
            raise SubjectError(
                f"Task {task.id!r}: repo_mutation provider requires a grading_dir "
                "(hidden tests must never live in repo_dir)"
            )
        src = _source_fields(task)
        recipe_path = (suite_base_dir / src["recipe"]).resolve()
        recipe = load_recipe(recipe_path)
        hidden_sources = _resolve_hidden_sources(recipe, suite_base_dir, task.id)

        base_commit = checkout_pinned(src["repo"], src["base_commit"], repo_dir, provider=_PROVIDER)

        # Park pristine copies BEFORE mutating: reference overlay + hidden_from_repo.
        for rel in sorted({e.file for e in recipe.edits} | set(recipe.remove)):
            _stash_reference(repo_dir, grading_dir, rel)
        for rel in recipe.hidden_from_repo:
            origin = resolve_inside(repo_dir, rel, what=f"task {task.id!r} hidden_from_repo")
            if not origin.is_file():
                raise SubjectError(
                    f"Task {task.id!r}: hidden_from_repo path not in checkout: {rel!r}"
                )
            write_hidden(grading_dir, rel, origin.read_bytes())
            origin.unlink()
        for repo_rel, source in hidden_sources.items():
            write_hidden(grading_dir, repo_rel, source.read_bytes())

        for edit in recipe.edits:
            apply_edit(repo_dir, edit, task_id=task.id)
        for rel in recipe.remove:
            victim = resolve_inside(repo_dir, rel, what=f"task {task.id!r} remove")
            if not victim.exists():
                raise SubjectError(f"Task {task.id!r}: remove path not in checkout: {rel!r}")
            if victim.is_dir():
                shutil.rmtree(victim)
            else:
                victim.unlink()

        baseline_commit = snapshot_single_commit(repo_dir, provider=_PROVIDER)
        hidden_root = grading_dir / HIDDEN_DIRNAME
        hidden_paths = (
            sorted(
                p.relative_to(hidden_root).as_posix() for p in hidden_root.rglob("*") if p.is_file()
            )
            if hidden_root.is_dir()
            else []
        )
        write_resolved(
            grading_dir,
            {
                "kind": REPO_MUTATION_PROVIDER_TYPE,
                "mode": recipe.mode,
                "repo": src["repo"],
                "base_commit": base_commit,
                "baseline_commit": baseline_commit,
                "recipe": src["recipe"],
                "fail_to_pass": recipe.fail_to_pass,
                "pass_to_pass": recipe.pass_to_pass,
                "test_targets": recipe.test_targets,
                "install": recipe.install,
                "test_command": recipe.test_command,
                "hidden_paths": hidden_paths,
                "edits_applied": [{"file": e.file, "count": e.count} for e in recipe.edits],
                "removed": recipe.remove,
                "verified": recipe.verified,
            },
        )


register_workspace_provider(REPO_MUTATION_PROVIDER_TYPE, RepoMutationProvider)
