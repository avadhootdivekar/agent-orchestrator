"""`repo_mutation` workspace provider (HLD §4.2 Tier 2b/2c/2d) -- STUB, owner: Tier-2 track.

Clones a pinned public repo (shared bare-clone cache, as swebench_provider does), applies a
recipe (inject a bug / excise a feature + its tests / describe an enhancement) into
repo_dir, and copies the hidden tests into grading_dir for the `pytest_targeted` grader.
"""

from __future__ import annotations

from pathlib import Path

from .errors import SubjectError
from .registries import register_workspace_provider
from .spec import BenchTask
from .workspace import WorkspaceProvider

REPO_MUTATION_PROVIDER_TYPE = "repo_mutation"


class RepoMutationProvider(WorkspaceProvider):
    def prepare(
        self,
        task: BenchTask,
        repo_dir: Path,
        *,
        suite_base_dir: Path,
        subject_base_dir: Path | None = None,
        grading_dir: Path | None = None,
    ) -> None:
        raise SubjectError("repo_mutation provider is not implemented yet (E-Bn7Dsc)")


register_workspace_provider(REPO_MUTATION_PROVIDER_TYPE, RepoMutationProvider)
