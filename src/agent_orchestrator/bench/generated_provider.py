"""`generated` workspace provider (HLD §4.1/§8).

``source: {"type": "generated", "family": ..., "seed": ..., "difficulty": {...},
"language": ..., "deps": [...]?}`` ->

* skeleton (``run.sh``, manifest, stub entry) + ``SPEC.md`` into ``repo_dir`` (agent-visible);
* ``instance.json`` + ``probes.json`` + ``reference/`` into ``grading_dir`` (grader-owned).

Everything is a pure function of the ``source`` block, so nothing hidden is stored in the suite:
the probes and reference are regenerated, identically, every time the task is materialised.
``deps`` (optional) is the third-party dependency list written by ``ao-bench gen`` from the suite's
``stack.<language>.deps``; the allow-list stated in SPEC.md and written to the manifest is the
language built-ins plus these.  Absent => the per-language defaults.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .errors import SubjectError
from .gen.base import Difficulty
from .gen.materialize import generate_instance, write_instance
from .gen.skeleton import allow_from_deps
from .registries import register_workspace_provider
from .spec import GENERATED_LANGUAGES, BenchTask
from .workspace import WorkspaceProvider

GENERATED_PROVIDER_TYPE = "generated"
SOURCE_KEYS = frozenset({"type", "family", "seed", "difficulty", "language", "deps"})


def _parse_source(task: BenchTask) -> tuple[str, int, Difficulty, str, list[str] | None]:
    """Validate ``task.source`` -> (family, seed, difficulty, language, allow-list or None)."""
    if task.source is None:
        raise SubjectError(f"Task {task.id!r}: 'generated' provider requires task.source")
    src: dict[str, Any] = task.source.model_dump()
    unknown = sorted(set(src) - SOURCE_KEYS)
    if unknown:
        raise SubjectError(f"Task {task.id!r}: unknown generated-source keys {unknown}")
    family = src.get("family")
    if not isinstance(family, str) or not family:
        raise SubjectError(f"Task {task.id!r}: source.family must be a non-empty string")
    seed = src.get("seed")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise SubjectError(f"Task {task.id!r}: source.seed must be an integer, got {seed!r}")
    language = src.get("language")
    if language not in GENERATED_LANGUAGES:
        raise SubjectError(
            f"Task {task.id!r}: source.language {language!r} not in {sorted(GENERATED_LANGUAGES)}"
        )
    raw_diff = src.get("difficulty", {})
    if not isinstance(raw_diff, dict):
        raise SubjectError(f"Task {task.id!r}: source.difficulty must be an object")
    try:
        difficulty = Difficulty.from_mapping(raw_diff)
    except ValueError as exc:
        raise SubjectError(f"Task {task.id!r}: {exc}") from exc
    deps = src.get("deps")
    if deps is not None and not (
        isinstance(deps, list) and all(isinstance(d, str) and d for d in deps)
    ):
        raise SubjectError(f"Task {task.id!r}: source.deps must be a list of non-empty strings")
    allow = allow_from_deps(language, deps) if deps is not None else None
    # The task-level metadata (used for reporting) must not contradict the authoritative source.
    for field, source_value in (("family", family), ("language", language)):
        declared = getattr(task, field)
        if declared is not None and declared != source_value:
            raise SubjectError(
                f"Task {task.id!r}: task.{field}={declared!r} contradicts source.{field}="
                f"{source_value!r}"
            )
    if task.difficulty is not None:
        try:
            declared_difficulty = Difficulty.from_mapping(task.difficulty)
        except ValueError as exc:
            raise SubjectError(f"Task {task.id!r}: task.difficulty: {exc}") from exc
        if declared_difficulty != difficulty:
            raise SubjectError(f"Task {task.id!r}: task.difficulty contradicts source.difficulty")
    return family, seed, difficulty, language, allow


class GeneratedProvider(WorkspaceProvider):
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
                f"Task {task.id!r}: 'generated' provider needs a grading_dir for its hidden "
                "probes/reference (caller passed none)"
            )
        family, seed, difficulty, language, allow = _parse_source(task)
        try:
            instance = generate_instance(family, seed, difficulty, language, allow)
            write_instance(instance, repo_dir, grading_dir)
        except (ValueError, OSError) as exc:
            raise SubjectError(f"Task {task.id!r}: generating {family!r} failed: {exc}") from exc


register_workspace_provider(GENERATED_PROVIDER_TYPE, GeneratedProvider)
