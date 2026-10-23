"""`pr_mined` provider (+ `pytest_targeted` in whole-file `test_targets` mode) on a local
bare repo holding two commits: the PR's base and its merge commit. Hermetic (no network)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from agent_orchestrator.bench.errors import SubjectError
from agent_orchestrator.bench.registries import WORKSPACE_PROVIDER_REGISTRY
from agent_orchestrator.bench.runner import run_suite
from agent_orchestrator.bench.spec import BenchTask, GraderConfig, Source, SubjectSpec, load_suite
from agent_orchestrator.bench.subjects import FakeSubject
from agent_orchestrator.bench.tier2_common import HIDDEN_DIRNAME, RESOLVED_RECIPE_FILENAME

from .tier2_fixtures import commit_all, git, grade, make_origin, materialize, write_files

pytestmark = pytest.mark.usefixtures("isolated_repo_cache")

INSTANCE_ID = "octo__minipkg-7"
PR_BASE_BUG = "def add(a, b):\n    return a - b\n"
NEW_TEST = (
    "from minipkg.core import add\n\n\ndef test_added_behaviour():\n    assert add(2, 2) == 4\n"
)
MERGED_TEST_CORE = (
    "import json\nfrom pathlib import Path\n\nfrom minipkg.core import add\n\n\n"
    "def test_add():\n    assert add(1, 2) == 3\n\n\n"
    "def test_data_file_restored():\n"
    "    data = json.loads((Path(__file__).parent / 'data' / 'sample.json').read_text())\n"
    "    assert data == {'v': 2}\n"
)


def _make_pr_origin(tmp_path: Path) -> tuple[str, str, str]:
    """Bare repo with base + merge commits. Returns (bare_uri, base_sha, merge_sha)."""
    work, _ = make_origin(tmp_path, "work")
    write_files(
        work,
        {
            "minipkg/core.py": PR_BASE_BUG,
            "tests/test_core.py": "def test_add():\n    assert True  # pre-PR placeholder\n",
            "tests/data/sample.json": '{"v": 1}',
        },
    )
    base = commit_all(work, "pr base")
    write_files(
        work,
        {
            "minipkg/core.py": "def add(a, b):\n    return a + b\n",
            "tests/test_added.py": NEW_TEST,
            "tests/test_core.py": MERGED_TEST_CORE,
            "tests/data/sample.json": '{"v": 2}',
        },
    )
    merge = commit_all(work, "merge: fix add")
    bare = tmp_path / "bare.git"
    subprocess.run(
        ["git", "clone", "--bare", str(work), str(bare)], check=True, capture_output=True
    )
    return bare.as_uri(), base, merge


def _write_suite(tmp_path: Path, repo: str, base: str, merge: str, **instance: Any) -> Path:
    suite = tmp_path / "suite"
    (suite / "tasks" / INSTANCE_ID).mkdir(parents=True, exist_ok=True)
    (suite / "tasks" / INSTANCE_ID / "instruction.md").write_text("Fix add().\n")
    record = {
        "instance_id": INSTANCE_ID,
        "repo": repo,
        "pr_number": 7,
        "base_commit": base,
        "merge_commit": merge,
        "title": "Fix add",
        "body": "add() subtracts.",
        "test_files": ["tests/data/sample.json", "tests/test_added.py", "tests/test_core.py"],
        "source_files": ["minipkg/core.py"],
        "merged_at": "2026-07-01T00:00:00Z",
    }
    record.update(instance)
    (suite / "instances.json").write_text(
        json.dumps({"dataset": "pr-mined", "cutoff_date": "2026-06-01", "instances": [record]})
    )
    return suite


def _task(**grader: Any) -> BenchTask:
    return BenchTask(
        id="pr-7",
        category="bugfix",
        instruction=f"tasks/{INSTANCE_ID}/instruction.md",
        source=Source(type="pr_mined", instance_id=INSTANCE_ID),
        grader=GraderConfig(type="pytest_targeted", **grader),
    )


def test_provider_registered() -> None:
    assert "pr_mined" in WORKSPACE_PROVIDER_REGISTRY


def test_hidden_tests_land_in_grading_dir_and_never_in_repo_dir(
    tmp_path: Path, bench_ws: Path
) -> None:
    repo_uri, base, merge = _make_pr_origin(tmp_path)
    suite = _write_suite(tmp_path, repo_uri, base, merge)
    ctx = materialize(_task(), suite, bench_ws)
    repo, grading = Path(ctx.repo_dir), Path(ctx.grading_dir or "")
    hidden = grading / HIDDEN_DIRNAME

    # The PR's tests (merge-commit versions, data files included) are held out ...
    assert (hidden / "tests/test_added.py").read_text() == NEW_TEST
    assert (hidden / "tests/test_core.py").read_text() == MERGED_TEST_CORE
    assert (hidden / "tests/data/sample.json").read_text() == '{"v": 2}'
    # ... and absent from the agent's repo -- including the stale pre-PR versions.
    for rel in ("tests/test_added.py", "tests/test_core.py", "tests/data/sample.json"):
        assert not (repo / rel).exists()
    assert (repo / "tests/test_text.py").is_file()  # unrelated tests stay
    assert "return a - b" in (repo / "minipkg/core.py").read_text()  # pre-PR source


def test_merge_commit_is_unreachable_from_the_workspace(tmp_path: Path, bench_ws: Path) -> None:
    repo_uri, base, merge = _make_pr_origin(tmp_path)
    ctx = materialize(_task(), _write_suite(tmp_path, repo_uri, base, merge), bench_ws)
    repo = Path(ctx.repo_dir)
    assert git(repo, "rev-list", "--all", "--count") == "1"
    assert git(repo, "remote") == ""
    probe = subprocess.run(
        ["git", "-C", str(repo), "cat-file", "-e", f"{merge}^{{commit}}"], capture_output=True
    )
    assert probe.returncode != 0
    assert git(repo, "status", "--porcelain") == ""


def test_resolved_recipe_targets_whole_test_modules(tmp_path: Path, bench_ws: Path) -> None:
    repo_uri, base, merge = _make_pr_origin(tmp_path)
    ctx = materialize(_task(), _write_suite(tmp_path, repo_uri, base, merge), bench_ws)
    resolved = json.loads((Path(ctx.grading_dir or "") / RESOLVED_RECIPE_FILENAME).read_text())
    assert resolved["fail_to_pass"] == []
    assert resolved["test_targets"] == ["tests/test_added.py", "tests/test_core.py"]  # no data file
    assert resolved["base_commit"] == base and resolved["merge_commit"] == merge
    assert resolved["hidden_paths"] == [
        "tests/data/sample.json",
        "tests/test_added.py",
        "tests/test_core.py",
    ]


def test_unsolved_at_base_then_solved_with_the_gold_patch(tmp_path: Path, bench_ws: Path) -> None:
    repo_uri, base, merge = _make_pr_origin(tmp_path)
    task = _task()
    ctx = materialize(task, _write_suite(tmp_path, repo_uri, base, merge), bench_ws)

    before = grade(task, ctx)
    assert before.solved is False
    assert before.detail["mode"] == "test_targets"
    results = before.detail["fail_to_pass"]["results"]
    assert results["tests/test_added.py::test_added_behaviour"] == "failed"
    assert results["tests/test_core.py::test_data_file_restored"] == "passed"  # data restored
    assert 0.0 < before.score < 1.0

    spec = SubjectSpec(version="1.0", id="fake-ref", type="fake", scripted_effect="copy-reference")
    FakeSubject(spec).run(task, ctx)  # merge commit's minipkg/core.py
    after = grade(task, ctx)
    assert after.solved is True and after.score == pytest.approx(1.0)
    assert after.detail["collateral"]["files_touched"] == 1


def test_a_target_module_that_never_collects_is_unsolved(tmp_path: Path, bench_ws: Path) -> None:
    repo_uri, base, merge = _make_pr_origin(tmp_path)
    suite = _write_suite(tmp_path, repo_uri, base, merge)
    task = _task(test_targets=["tests/test_missing.py"])  # grader key overrides the resolved list
    ctx = materialize(task, suite, bench_ws)
    result = grade(task, ctx)
    assert result.solved is False
    assert result.detail["fail_to_pass"]["results"] == {"tests/test_missing.py": "missing"}


def test_instance_lookup_errors(tmp_path: Path, bench_ws: Path) -> None:
    repo_uri, base, merge = _make_pr_origin(tmp_path)
    suite = _write_suite(tmp_path, repo_uri, base, merge)
    unknown = BenchTask(
        id="x",
        category="bugfix",
        instruction=f"tasks/{INSTANCE_ID}/instruction.md",
        source=Source(type="pr_mined", instance_id="nope__nope-1"),
        grader=GraderConfig(type="pytest_targeted"),
    )
    with pytest.raises(SubjectError, match="not found in instances.json"):
        materialize(unknown, suite, bench_ws)

    (suite / "instances.json").unlink()
    with pytest.raises(SubjectError, match="pr_mined provider: instances.json not found"):
        materialize(_task(), suite, bench_ws)

    no_id = BenchTask(
        id="x",
        category="bugfix",
        instruction=f"tasks/{INSTANCE_ID}/instruction.md",
        source=Source(type="pr_mined"),
        grader=GraderConfig(type="pytest_targeted"),
    )
    with pytest.raises(SubjectError, match="instance_id is required"):
        materialize(no_id, suite, bench_ws)


def test_incomplete_instance_and_missing_merge_blob_rejected(
    tmp_path: Path, bench_ws: Path
) -> None:
    repo_uri, base, merge = _make_pr_origin(tmp_path)
    suite = _write_suite(tmp_path, repo_uri, base, merge, merge_commit="")
    with pytest.raises(SubjectError, match="lacks"):
        materialize(_task(), suite, bench_ws)
    suite = _write_suite(tmp_path, repo_uri, base, merge, test_files=["tests/test_ghost.py"])
    with pytest.raises(SubjectError, match="git show"):
        materialize(_task(), suite, bench_ws / "again")


def test_provider_requires_grading_dir(tmp_path: Path) -> None:
    repo_uri, base, merge = _make_pr_origin(tmp_path)
    suite = _write_suite(tmp_path, repo_uri, base, merge)
    provider = WORKSPACE_PROVIDER_REGISTRY["pr_mined"]()
    with pytest.raises(SubjectError, match="grading_dir"):
        provider.prepare(_task(), tmp_path / "r", suite_base_dir=suite)


@pytest.mark.parametrize(("effect", "solved"), [("noop", False), ("copy-reference", True)])
def test_runner_end_to_end(tmp_path: Path, effect: str, solved: bool) -> None:
    repo_uri, base, merge = _make_pr_origin(tmp_path)
    suite = _write_suite(tmp_path, repo_uri, base, merge)
    suite_json = {
        "version": "1.0",
        "id": "pr-mined-e2e",
        "domain": "software",
        "tier": "large",
        "cutoff_date": "2026-06-01",
        "tasks": [
            {
                "id": "pr-7",
                "category": "bugfix",
                "instruction": f"tasks/{INSTANCE_ID}/instruction.md",
                "source": {"type": "pr_mined", "instance_id": INSTANCE_ID},
                "grader": {"type": "pytest_targeted"},
                "family": "pr-mined",
                "language": "python",
            }
        ],
    }
    (suite / "suite.json").write_text(json.dumps(suite_json))
    subject = tmp_path / "subject.json"
    subject.write_text(
        json.dumps({"version": "1.0", "id": "fake", "type": "fake", "scripted_effect": effect})
    )
    assert load_suite(suite / "suite.json").cutoff_date == "2026-06-01"

    record = run_suite(suite / "suite.json", subject, out_dir=tmp_path / "out")
    (task,) = record.tasks
    assert task.solved is solved
    assert task.grader_detail["mode"] == "test_targets"
