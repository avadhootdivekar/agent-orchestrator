"""`generated` workspace provider (HLD §4.1/§8) -- STUB, owner: generators track.

`source: {"type": "generated", "family": ..., "seed": ..., "difficulty": {...}, "language": ...}`
-> skeleton + SPEC.md into repo_dir; instance.json + probes.json + reference/ into grading_dir.
"""

from __future__ import annotations

from pathlib import Path

from .errors import SubjectError
from .registries import register_workspace_provider
from .spec import BenchTask
from .workspace import WorkspaceProvider

GENERATED_PROVIDER_TYPE = "generated"


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
        raise SubjectError("generated provider is not implemented yet (E-Bn7Dsc)")


register_workspace_provider(GENERATED_PROVIDER_TYPE, GeneratedProvider)
