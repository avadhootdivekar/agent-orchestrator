"""Build a validated :class:`Instance` and write it to disk (shared by the provider and the CLI).

``generate_instance`` is the single entry point: family lookup, generation, and a structural check
of the emitted probes.json, so a family bug surfaces as an error *before* any agent sees the task
(HLD §4.1: "a task that the reference fails is a generator bug and is rejected").
"""

from __future__ import annotations

import os
import stat
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath

from .base import FAMILY_REGISTRY, Difficulty, Instance
from .validate import check_probes

SPEC_FILENAME = "SPEC.md"
INSTANCE_FILENAME = "instance.json"
PROBES_FILENAME = "probes.json"
REFERENCE_DIRNAME = "reference"
EXECUTABLE_FILENAMES = frozenset({"run.sh"})
EXECUTABLE_MODE = 0o755


def generate_instance(
    family: str,
    seed: int,
    difficulty: Difficulty | Mapping[str, int] | None,
    language: str,
    allow: Sequence[str] | None = None,
) -> Instance:
    """Generate and validate one instance.  Raises ``ValueError`` for any bad input or a family
    that emits malformed probes."""
    cls = FAMILY_REGISTRY.get(family)
    if cls is None:
        raise ValueError(f"unknown generator family {family!r}; known: {sorted(FAMILY_REGISTRY)}")
    diff = difficulty if isinstance(difficulty, Difficulty) else Difficulty.from_mapping(difficulty)
    instance = cls().generate(seed, diff, language, allow)
    if cls.grader_type != "blackbox":  # objective cases are checked by the family itself
        return instance
    try:
        check_probes(instance.probes)
    except ValueError as exc:
        raise ValueError(f"family {family!r} (seed {seed}) produced invalid probes: {exc}") from exc
    return instance


def _write_files(root: Path, files: Mapping[str, str]) -> None:
    for rel, content in files.items():
        pure = PurePosixPath(rel)
        if pure.is_absolute() or ".." in pure.parts:
            raise ValueError(f"generated path escapes its directory: {rel!r}")
        dest = root / pure
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
        if pure.name in EXECUTABLE_FILENAMES:
            os.chmod(dest, stat.S_IMODE(dest.stat().st_mode) | EXECUTABLE_MODE)


def write_instance(instance: Instance, repo_dir: Path, grading_dir: Path) -> None:
    """Agent-visible files (skeleton + SPEC.md) -> *repo_dir*; grader-owned files
    (instance.json, probes.json, reference/) -> *grading_dir*.  Nothing grader-owned is ever
    written under *repo_dir*."""
    repo_dir.mkdir(parents=True, exist_ok=True)
    _write_files(repo_dir, {**instance.skeleton, SPEC_FILENAME: instance.spec_md})
    grading_dir.mkdir(parents=True, exist_ok=True)
    _write_files(
        grading_dir,
        {
            INSTANCE_FILENAME: instance.instance_json_text(),
            PROBES_FILENAME: instance.probes_json_text(),
            **{f"{REFERENCE_DIRNAME}/{rel}": text for rel, text in instance.reference.items()},
            # A copy next to the reference program: the reference then also works when it is
            # copied elsewhere as a whole (FakeSubject `copy-reference`) without INSTANCE_JSON.
            f"{REFERENCE_DIRNAME}/{INSTANCE_FILENAME}": instance.instance_json_text(),
        },
    )
