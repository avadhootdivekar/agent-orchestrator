"""`pr_mined` workspace provider (HLD §4.2 Tier 2a) -- STUB, owner: Tier-2 track.

Like `swebench` but over a locally mined `instances.json` (`ao-bench import-prs`): repo at
the pre-PR commit into repo_dir; the PR's test changes into grading_dir as hidden tests.
"""

from __future__ import annotations

from pathlib import Path

from .errors import SubjectError
from .registries import register_workspace_provider
from .spec import BenchTask
from .workspace import WorkspaceProvider

PR_MINED_PROVIDER_TYPE = "pr_mined"


class PrMinedProvider(WorkspaceProvider):
    def prepare(
        self,
        task: BenchTask,
        repo_dir: Path,
        *,
        suite_base_dir: Path,
        subject_base_dir: Path | None = None,
        grading_dir: Path | None = None,
    ) -> None:
        raise SubjectError("pr_mined provider is not implemented yet (E-Bn7Dsc)")


register_workspace_provider(PR_MINED_PROVIDER_TYPE, PrMinedProvider)
