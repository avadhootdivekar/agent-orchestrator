"""`pr_mined` workspace provider (HLD §4.2 Tier 2a).

Like `swebench`, but over a locally mined `instances.json` (`ao-bench import-prs`):

    task.source = {"type": "pr_mined", "instance_id": "owner__name-1234"}

`prepare()`:
  1. checks the repo out at the instance's `base_commit` (pre-PR state);
  2. reads each of the PR's `test_files` as of `merge_commit` (`git show <merge>:<path>`)
     into `grading_dir/hidden/<path>` -- the hidden tests -- and deletes those paths from
     `repo_dir` (their pre-PR versions would otherwise hint at, or contradict, the change);
  3. parks the merge commit's version of each changed source file in
     `grading_dir/reference/` (the "gold patch": `FakeSubject copy-reference` can then prove an
     instance is solvable at zero cost; never visible to the agent);
  4. re-snapshots the tree as one parentless commit so neither `git log --all` nor
     `git fetch origin` can reach the merge commit (`tier2_common` docstring);
  5. writes `recipe.resolved.json` with `fail_to_pass: []` and `test_targets` = the PR's test
     modules: node ids are unknown without running the tests at both commits, so the
     `pytest_targeted` grader runs those whole files and `solved` = every test in them passes.

Hidden tests never touch `repo_dir`; only the grader restores them (at grade time).
"""

from __future__ import annotations

from pathlib import Path

from .errors import SubjectError
from .registries import register_workspace_provider
from .spec import BenchTask
from .swebench_provider import _load_instances_index
from .tier2_common import (
    REFERENCE_DIRNAME,
    checkout_pinned,
    is_test_module,
    read_blob,
    resolve_inside,
    snapshot_single_commit,
    write_hidden,
    write_resolved,
)
from .workspace import WorkspaceProvider

PR_MINED_PROVIDER_TYPE = "pr_mined"
_PROVIDER = "pr_mined"
_REQUIRED_INSTANCE_FIELDS = ("repo", "base_commit", "merge_commit", "test_files")


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
        if task.source is None or task.source.type != PR_MINED_PROVIDER_TYPE:
            raise SubjectError(
                f"Task {task.id!r}: pr_mined provider requires source.type == "
                f"{PR_MINED_PROVIDER_TYPE!r}"
            )
        if grading_dir is None:
            raise SubjectError(
                f"Task {task.id!r}: pr_mined provider requires a grading_dir "
                "(hidden tests must never live in repo_dir)"
            )
        instance_id = (task.source.model_extra or {}).get("instance_id")
        if not instance_id:
            raise SubjectError(f"Task {task.id!r}: source.instance_id is required for pr_mined")
        index = _load_instances_index(
            suite_base_dir, provider=_PROVIDER, importer="ao-bench import-prs"
        )
        meta = index.get(instance_id)
        if meta is None:
            raise SubjectError(
                f"Task {task.id!r}: instance_id {instance_id!r} not found in instances.json"
            )
        missing = [f for f in _REQUIRED_INSTANCE_FIELDS if not meta.get(f)]
        if missing:
            raise SubjectError(f"Task {task.id!r}: instance {instance_id!r} lacks {missing}")

        base_commit = checkout_pinned(
            meta["repo"], meta["base_commit"], repo_dir, provider=_PROVIDER
        )
        test_files: list[str] = list(meta["test_files"])
        for rel in test_files:
            # Hidden content comes from the merge commit; objects are in the checkout's
            # object store until the snapshot below discards them.
            data = read_blob(repo_dir, meta["merge_commit"], rel, provider=_PROVIDER)
            write_hidden(grading_dir, rel, data)
            stale = resolve_inside(repo_dir, rel, what=f"task {task.id!r} test file")
            if stale.is_file() or stale.is_symlink():
                stale.unlink()

        for rel in meta.get("source_files", []):
            gold = resolve_inside(grading_dir / REFERENCE_DIRNAME, rel, what="reference path")
            gold.parent.mkdir(parents=True, exist_ok=True)
            gold.write_bytes(read_blob(repo_dir, meta["merge_commit"], rel, provider=_PROVIDER))

        baseline_commit = snapshot_single_commit(repo_dir, provider=_PROVIDER)
        write_resolved(
            grading_dir,
            {
                "kind": PR_MINED_PROVIDER_TYPE,
                "mode": "pr_mined",
                "instance_id": instance_id,
                "repo": meta["repo"],
                "base_commit": base_commit,
                "baseline_commit": baseline_commit,
                "merge_commit": meta["merge_commit"],
                "fail_to_pass": [],
                "pass_to_pass": [],
                "test_targets": [f for f in test_files if is_test_module(f)],
                "hidden_paths": sorted(test_files),
            },
        )


register_workspace_provider(PR_MINED_PROVIDER_TYPE, PrMinedProvider)
