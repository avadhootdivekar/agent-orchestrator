"""`repo_mutation` provider + `pytest_targeted` grader, end to end on a local git repo.

Hermetic (fake tier): the origin is a real tiny git repo addressed by `file://`, the clone
cache is redirected into `tmp_path`, the subject is `FakeSubject` and the grader runs a real
pytest subprocess. No network, no LLM.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from agent_orchestrator.bench.errors import GraderError, SubjectError
from agent_orchestrator.bench.registries import GRADER_REGISTRY, WORKSPACE_PROVIDER_REGISTRY
from agent_orchestrator.bench.runner import run_suite
from agent_orchestrator.bench.spec import SubjectSpec, load_suite
from agent_orchestrator.bench.subjects import FakeSubject
from agent_orchestrator.bench.tier2_common import HIDDEN_DIRNAME, RESOLVED_RECIPE_FILENAME

from .tier2_fixtures import (
    CLAMP_BUG,
    HIDDEN_CLAMP,
    INJECT_F2P,
    INJECT_P2P,
    commit_all,
    git,
    grade,
    make_origin,
    materialize,
    mutation_task,
    write_files,
    write_suite,
)

NEW_FEATURE_TEST = (
    "from minipkg.core import double\n\n\ndef test_double():\n    assert double(2) == 4\n"
)

pytestmark = pytest.mark.usefixtures("isolated_repo_cache")


def _inject_recipe(**overrides: Any) -> dict[str, Any]:
    recipe: dict[str, Any] = {
        "mode": "inject",
        "edits": [CLAMP_BUG],
        "hidden": {"tests/test_clamp_hidden.py": "hidden/test_clamp_hidden.py"},
        "fail_to_pass": INJECT_F2P,
        "pass_to_pass": INJECT_P2P,
    }
    recipe.update(overrides)
    return recipe


def _prepare(tmp_path: Path, bench_ws: Path, recipe: dict[str, Any], **grader: Any):
    origin, sha = make_origin(tmp_path)
    suite = write_suite(tmp_path, recipe, {"test_clamp_hidden.py": HIDDEN_CLAMP})
    task = mutation_task(origin, sha, grader or None)
    ctx = materialize(task, suite, bench_ws)
    return task, ctx, origin, sha


def _copy_reference(task: Any, ctx: Any) -> None:
    spec = SubjectSpec(version="1.0", id="fake-ref", type="fake", scripted_effect="copy-reference")
    assert FakeSubject(spec).run(task, ctx).status == "succeeded"


def test_provider_and_grader_registered() -> None:
    assert "repo_mutation" in WORKSPACE_PROVIDER_REGISTRY
    assert "pytest_targeted" in GRADER_REGISTRY


# --------------------------------------------------------------------------- inject


def test_inject_applies_mutation_and_hides_the_tests(tmp_path: Path, bench_ws: Path) -> None:
    task, ctx, _, sha = _prepare(tmp_path, bench_ws, _inject_recipe())
    repo, grading = Path(ctx.repo_dir), Path(ctx.grading_dir or "")

    assert "hi - 1" in (repo / "minipkg/core.py").read_text()  # mutation is in the repo
    # Hidden tests live ONLY in grading_dir -- never in repo_dir.
    assert not (repo / "tests/test_clamp_hidden.py").exists()
    assert (grading / HIDDEN_DIRNAME / "tests/test_clamp_hidden.py").read_text() == HIDDEN_CLAMP
    assert "test_clamp_upper_bound" not in "".join(
        p.read_text(errors="ignore")
        for p in repo.rglob("*")
        if p.is_file() and ".git" not in p.parts
    )
    resolved = json.loads((grading / RESOLVED_RECIPE_FILENAME).read_text())
    assert resolved["base_commit"] == sha and resolved["mode"] == "inject"
    assert resolved["fail_to_pass"] == INJECT_F2P
    assert resolved["hidden_paths"] == ["tests/test_clamp_hidden.py"]
    assert "find" not in json.dumps(resolved["edits_applied"])  # no mutation text in the contract


def test_workspace_git_state_does_not_leak_the_mutation(tmp_path: Path, bench_ws: Path) -> None:
    _, ctx, origin, _ = _prepare(tmp_path, bench_ws, _inject_recipe())
    repo = Path(ctx.repo_dir)
    # One parentless commit: `git diff`/`git stash`/`git log` cannot reveal or undo the bug.
    assert git(repo, "rev-list", "--count", "HEAD") == "1"
    assert git(repo, "status", "--porcelain") == ""  # clean (INSTRUCTION.md is excluded)
    assert git(repo, "remote") == ""
    assert git(repo, "stash", "list") == ""
    # ... and the pristine commit's objects are gone from the workspace repo.
    pristine = git(origin, "rev-parse", "HEAD")
    probe = subprocess.run(
        ["git", "-C", str(repo), "cat-file", "-e", f"{pristine}^{{commit}}"], capture_output=True
    )
    assert probe.returncode != 0


def test_unfixed_mutation_is_unsolved_with_collateral_stats(tmp_path: Path, bench_ws: Path) -> None:
    task, ctx, _, _ = _prepare(tmp_path, bench_ws, _inject_recipe())
    result = grade(task, ctx)

    assert result.solved is False
    d = result.detail
    assert d["fail_to_pass"]["results"] == {INJECT_F2P[0]: "failed"}
    assert d["pass_to_pass"]["passed"] == 2 and d["pass_to_pass"]["total"] == 2
    # 0.7 * 0 + 0.3 * 1
    assert result.score == pytest.approx(0.3)
    assert d["collateral"]["files_touched"] == 0  # agent changed nothing
    assert d["agent_tests_added"] == 0
    assert d["restored_hidden"] == ["tests/test_clamp_hidden.py"]
    assert d["timeouts"]["total_timeout_hit"] is False
    # Grading restores the hidden test into the repo (and only now).
    assert (Path(ctx.repo_dir) / "tests/test_clamp_hidden.py").is_file()


def test_fixed_via_fake_subject_copy_reference_is_solved(tmp_path: Path, bench_ws: Path) -> None:
    task, ctx, _, _ = _prepare(tmp_path, bench_ws, _inject_recipe())
    _copy_reference(task, ctx)  # pristine minipkg/core.py goes back over the mutated one

    result = grade(task, ctx)
    assert result.solved is True
    assert result.score == pytest.approx(1.0)
    c = result.detail["collateral"]
    assert (c["files_touched"], c["lines_added"], c["lines_deleted"]) == (1, 1, 1)


def test_collateral_counts_only_the_agents_own_diff(tmp_path: Path, bench_ws: Path) -> None:
    task, ctx, _, _ = _prepare(tmp_path, bench_ws, _inject_recipe())
    repo = Path(ctx.repo_dir)
    _copy_reference(task, ctx)
    write_files(
        repo,
        {
            "tests/test_mine.py": "def test_mine():\n    assert True\n",  # agent's own test
            "minipkg/extra.py": "X = 1\nY = 2\n",
            ".claude/settings.json": "{}",  # parity bundle: excluded
            "INSTRUCTION.md": "overwritten\n",  # runner-owned: excluded
            "minipkg/__pycache__/junk.pyc": "x",  # cache: excluded
        },
    )
    result = grade(task, ctx)

    c = result.detail["collateral"]
    assert c["files_touched"] == 3  # core.py (fix) + extra.py + test_mine.py
    assert c["lines_added"] == 1 + 2 + 2
    assert result.detail["agent_tests_added"] == 1
    assert result.detail["agent_test_files"] == ["tests/test_mine.py"]
    assert result.solved is True  # collateral is reported, never scored


def test_agent_edits_to_hidden_paths_are_overwritten_and_not_counted(
    tmp_path: Path, bench_ws: Path
) -> None:
    task, ctx, _, _ = _prepare(tmp_path, bench_ws, _inject_recipe())
    repo = Path(ctx.repo_dir)
    _copy_reference(task, ctx)
    # The agent tries to game the hidden test by writing its own version first.
    write_files(repo, {"tests/test_clamp_hidden.py": "def test_clamp_upper_bound():\n    pass\n"})
    result = grade(task, ctx)
    assert result.solved is True
    assert result.detail["collateral"]["files_touched"] == 1  # hidden path excluded
    assert "clamp(99, 0, 10)" in (repo / "tests/test_clamp_hidden.py").read_text()  # restored


# ------------------------------------------------------------------------- excise


def test_excise_removes_tests_from_repo_and_restores_them_at_grade(
    tmp_path: Path, bench_ws: Path
) -> None:
    recipe = {
        "mode": "excise",
        "description": "slugify feature removed",
        "edits": [
            {
                "file": "minipkg/text.py",
                "find": 'def slugify(s):\n    return "-".join(s.lower().split())\n\n\n',
                "replace": "",
            }
        ],
        "remove": ["docs/feature.md"],
        "hidden_from_repo": ["tests/test_text.py"],
        "fail_to_pass": ["tests/test_text.py::test_slugify", "tests/test_text.py::test_shout"],
    }
    origin, sha = make_origin(tmp_path)
    suite = write_suite(tmp_path, recipe)
    task = mutation_task(origin, sha)
    ctx = materialize(task, suite, bench_ws)
    repo = Path(ctx.repo_dir)

    assert not (repo / "tests/test_text.py").exists()  # excised tests are gone ...
    assert not (repo / "docs/feature.md").exists()
    assert "def slugify" not in (repo / "minipkg/text.py").read_text()
    hidden = Path(ctx.grading_dir or "") / HIDDEN_DIRNAME / "tests/test_text.py"
    assert hidden.is_file()  # ... parked in grading_dir

    first = grade(task, ctx)
    assert first.solved is False  # feature missing
    assert (repo / "tests/test_text.py").is_file()  # restored at grade time
    # the restored test module cannot even import `slugify` -> collection error, ids never ran
    assert first.detail["test_run"]["collection_errors"] == ["tests/test_text.py"]
    assert set(first.detail["fail_to_pass"]["results"].values()) == {"missing"}

    _copy_reference(task, ctx)  # the pristine feature code (+ removed docs) come back
    second = grade(task, ctx)
    assert second.solved is True and second.score == pytest.approx(1.0)


# ------------------------------------------------------------------------ enhance


def test_enhance_mode_hidden_tests_only(tmp_path: Path, bench_ws: Path) -> None:
    recipe = {
        "mode": "enhance",
        "hidden": {"tests/test_new.py": "hidden/test_new.py"},
        "fail_to_pass": ["tests/test_new.py::test_double"],
    }
    origin, sha = make_origin(tmp_path)
    suite = write_suite(
        tmp_path,
        recipe,
        {"test_new.py": NEW_FEATURE_TEST},
    )
    task = mutation_task(origin, sha)
    ctx = materialize(task, suite, bench_ws)
    assert grade(task, ctx).solved is False
    write_files(Path(ctx.repo_dir), {"minipkg/core.py": "def double(x):\n    return 2 * x\n"})
    assert grade(task, ctx).solved is True


# -------------------------------------------------------------------- bad recipes


@pytest.mark.parametrize(
    ("edit", "match"),
    [
        ({"file": "minipkg/core.py", "find": "no such text", "replace": "x"}, "matched 0 time"),
        ({"file": "minipkg/core.py", "find": "max(", "replace": "min(", "count": 2}, "matched 1"),
        ({"file": "minipkg/core.py", "find": "a", "replace": "b"}, "matched"),  # many matches
        ({"file": "minipkg/nope.py", "find": "a", "replace": "b"}, "not found"),
        ({"file": "../outside.py", "find": "a", "replace": "b"}, "unsafe"),
        ({"file": "/etc/passwd", "find": "a", "replace": "b"}, "unsafe"),
        ({"file": ".git/config", "find": "a", "replace": "b"}, "unsafe"),
    ],
)
def test_bad_edit_raises_subject_error(
    tmp_path: Path, bench_ws: Path, edit: dict[str, Any], match: str
) -> None:
    origin, sha = make_origin(tmp_path)
    suite = write_suite(
        tmp_path, _inject_recipe(edits=[edit]), {"test_clamp_hidden.py": HIDDEN_CLAMP}
    )
    with pytest.raises(SubjectError, match=match):
        materialize(mutation_task(origin, sha), suite, bench_ws)


def test_missing_find_error_names_file_and_text(tmp_path: Path, bench_ws: Path) -> None:
    origin, sha = make_origin(tmp_path)
    edit = {"file": "minipkg/core.py", "find": "return something_else", "replace": "x"}
    suite = write_suite(
        tmp_path, _inject_recipe(edits=[edit]), {"test_clamp_hidden.py": HIDDEN_CLAMP}
    )
    with pytest.raises(SubjectError, match=r"minipkg/core\.py.*return something_else"):
        materialize(mutation_task(origin, sha), suite, bench_ws)


@pytest.mark.parametrize(
    ("recipe", "match"),
    [
        ({"mode": "inject", "fail_to_pass": ["a::b"]}, "at least one edit"),
        ({"mode": "inject", "edits": [CLAMP_BUG]}, "fail_to_pass"),
        ({"mode": "excise", "fail_to_pass": ["a::b"], "remove": ["x"]}, "restore tests"),
        ({"mode": "enhance", "fail_to_pass": ["a::b"]}, "hidden tests"),
        ({"mode": "mutate", "fail_to_pass": ["a::b"]}, "invalid recipe"),
        ({"mode": "inject", "edits": [CLAMP_BUG], "fail_to_pass": ["a::b"], "typo": 1}, "typo"),
    ],
)
def test_invalid_recipes_are_rejected(
    tmp_path: Path, bench_ws: Path, recipe: dict[str, Any], match: str
) -> None:
    origin, sha = make_origin(tmp_path)
    suite = write_suite(tmp_path, recipe)
    with pytest.raises(SubjectError, match=match):
        materialize(mutation_task(origin, sha), suite, bench_ws)


def test_hidden_source_must_exist_and_stay_inside_the_suite(tmp_path: Path, bench_ws: Path) -> None:
    origin, sha = make_origin(tmp_path)
    suite = write_suite(tmp_path, _inject_recipe(hidden={"tests/x.py": "hidden/missing.py"}))
    with pytest.raises(SubjectError, match="source file not found"):
        materialize(mutation_task(origin, sha), suite, bench_ws)
    suite = write_suite(tmp_path, _inject_recipe(hidden={"tests/x.py": "../origin/pytest.ini"}))
    with pytest.raises(SubjectError, match="escapes suite"):
        materialize(mutation_task(origin, sha), suite, bench_ws)


def test_unpinned_or_missing_source_fields_rejected(tmp_path: Path, bench_ws: Path) -> None:
    origin, _ = make_origin(tmp_path)
    suite = write_suite(tmp_path, _inject_recipe(), {"test_clamp_hidden.py": HIDDEN_CLAMP})
    with pytest.raises(SubjectError, match="40-hex"):
        materialize(mutation_task(origin, "main"), suite, bench_ws)
    task = mutation_task(origin, "a" * 40)
    task.source.model_extra.pop("recipe")  # type: ignore[union-attr]
    with pytest.raises(SubjectError, match="missing required field"):
        materialize(task, suite, bench_ws)


def test_provider_requires_grading_dir(tmp_path: Path) -> None:
    origin, sha = make_origin(tmp_path)
    suite = write_suite(tmp_path, _inject_recipe(), {"test_clamp_hidden.py": HIDDEN_CLAMP})
    provider = WORKSPACE_PROVIDER_REGISTRY["repo_mutation"]()
    with pytest.raises(SubjectError, match="grading_dir"):
        provider.prepare(mutation_task(origin, sha), tmp_path / "r", suite_base_dir=suite)


def test_cache_is_reused_and_refreshed_for_a_newer_commit(tmp_path: Path, bench_ws: Path) -> None:
    origin, sha = make_origin(tmp_path)
    suite = write_suite(tmp_path, _inject_recipe(), {"test_clamp_hidden.py": HIDDEN_CLAMP})
    materialize(mutation_task(origin, sha), suite, bench_ws / "a")
    # New upstream commit AFTER the cache was cloned; a recipe pinned to it must still work.
    (origin / "minipkg/core.py").write_text((origin / "minipkg/core.py").read_text() + "\nZ = 1\n")
    newer = commit_all(origin, "later")
    ctx = materialize(mutation_task(origin, newer), suite, bench_ws / "b")
    assert "Z = 1" in (Path(ctx.repo_dir) / "minipkg/core.py").read_text()


# ---------------------------------------------------------------- grader behaviour


def test_vanished_pass_to_pass_test_is_missing_not_a_crash(tmp_path: Path, bench_ws: Path) -> None:
    task, ctx, _, _ = _prepare(tmp_path, bench_ws, _inject_recipe())
    _copy_reference(task, ctx)
    (Path(ctx.repo_dir) / "tests/test_core.py").unlink()  # agent deleted the visible tests
    result = grade(task, ctx)

    assert result.solved is False
    assert result.detail["fail_to_pass"]["results"] == {INJECT_F2P[0]: "passed"}
    assert set(result.detail["pass_to_pass"]["results"].values()) == {"missing"}
    assert sorted(result.detail["test_run"]["not_found"]) == sorted(INJECT_P2P)
    assert result.score == pytest.approx(0.7)


def test_collection_error_in_one_module_does_not_blank_the_rest(
    tmp_path: Path, bench_ws: Path
) -> None:
    task, ctx, _, _ = _prepare(tmp_path, bench_ws, _inject_recipe())
    _copy_reference(task, ctx)
    write_files(Path(ctx.repo_dir), {"tests/test_broken.py": "import does_not_exist\n"})
    result = grade(task, ctx)
    assert result.solved is True  # targeted tests are unaffected by an unrelated broken module


def test_missing_fail_to_pass_id_is_unsolved(tmp_path: Path, bench_ws: Path) -> None:
    recipe = _inject_recipe(fail_to_pass=[*INJECT_F2P, "tests/test_clamp_hidden.py::test_nope"])
    task, ctx, _, _ = _prepare(tmp_path, bench_ws, recipe)
    _copy_reference(task, ctx)
    result = grade(task, ctx)
    assert result.solved is False
    assert (
        result.detail["fail_to_pass"]["results"]["tests/test_clamp_hidden.py::test_nope"]
        == "missing"
    )
    assert result.score == pytest.approx(0.7 * 0.5 + 0.3)


def test_total_timeout_kills_the_run_and_scores_zero(tmp_path: Path, bench_ws: Path) -> None:
    recipe = {
        "mode": "enhance",
        "hidden": {"tests/test_slow.py": "hidden/test_slow.py"},
        "fail_to_pass": ["tests/test_slow.py::test_slow"],
    }
    origin, sha = make_origin(tmp_path)
    suite = write_suite(
        tmp_path,
        recipe,
        {"test_slow.py": "import time\n\n\ndef test_slow():\n    time.sleep(60)\n"},
    )
    task = mutation_task(origin, sha, {"total_timeout_seconds": 2})
    ctx = materialize(task, suite, bench_ws)
    result = grade(task, ctx)
    assert result.solved is False and result.score == 0.0
    assert result.detail["timeouts"]["total_timeout_hit"] is True
    assert result.detail["fail_to_pass"]["results"] == {"tests/test_slow.py::test_slow": "missing"}


def test_install_runs_once_in_repo_dir(tmp_path: Path, bench_ws: Path) -> None:
    task, ctx, _, _ = _prepare(tmp_path, bench_ws, _inject_recipe(), install="echo ok > installed")
    result = grade(task, ctx)
    assert (Path(ctx.repo_dir) / "installed").read_text().strip() == "ok"
    assert result.detail["install"]["command"] == "echo ok > installed"


def test_install_failure_is_infra_not_a_grade(tmp_path: Path, bench_ws: Path) -> None:
    task, ctx, _, _ = _prepare(
        tmp_path, bench_ws, _inject_recipe(), install="echo boom >&2; exit 3"
    )
    with pytest.raises(GraderError, match=r"install failed \(exit 3\).*boom"):
        grade(task, ctx)


def test_install_default_comes_from_the_recipe(tmp_path: Path, bench_ws: Path) -> None:
    recipe = _inject_recipe(install="echo from-recipe > installed")
    task, ctx, _, _ = _prepare(tmp_path, bench_ws, recipe)
    grade(task, ctx)
    assert (Path(ctx.repo_dir) / "installed").read_text().strip() == "from-recipe"


def test_suite_json_grader_keys_override_the_resolved_recipe(
    tmp_path: Path, bench_ws: Path
) -> None:
    task, ctx, _, _ = _prepare(
        tmp_path,
        bench_ws,
        _inject_recipe(),
        fail_to_pass=["tests/test_core.py::test_add"],
        pass_to_pass=[],
    )
    result = grade(task, ctx)
    assert result.solved is True  # only test_add is required now
    assert result.detail["pass_to_pass"]["total"] == 0
    assert result.score == pytest.approx(1.0)  # empty p2p weight renormalises away


def test_pass_to_pass_sample_is_bounded(tmp_path: Path, bench_ws: Path) -> None:
    task, ctx, _, _ = _prepare(tmp_path, bench_ws, _inject_recipe(), pass_to_pass_sample=1)
    result = grade(task, ctx)
    assert result.detail["pass_to_pass"]["sampled"] == 1
    assert result.detail["pass_to_pass"]["population"] == 2


def test_grader_needs_grading_dir_and_targets(tmp_path: Path, bench_ws: Path) -> None:
    task, ctx, _, _ = _prepare(tmp_path, bench_ws, _inject_recipe())
    no_dir = ctx.model_copy(update={"grading_dir": None})
    with pytest.raises(GraderError, match="grading_dir"):
        grade(task, no_dir)
    (Path(ctx.grading_dir or "") / RESOLVED_RECIPE_FILENAME).unlink()
    with pytest.raises(GraderError, match="neither fail_to_pass nor test_targets"):
        grade(task, ctx)


def test_skipped_fail_to_pass_counts_as_failure_but_skipped_p2p_is_excluded(
    tmp_path: Path, bench_ws: Path
) -> None:
    recipe = {
        "mode": "enhance",
        "hidden": {"tests/test_skip.py": "hidden/test_skip.py"},
        "fail_to_pass": ["tests/test_skip.py::test_must_pass"],
        "pass_to_pass": ["tests/test_skip.py::test_env_dependent"],
    }
    origin, sha = make_origin(tmp_path)
    suite = write_suite(
        tmp_path,
        recipe,
        {
            "test_skip.py": "import pytest\n\n\ndef test_must_pass():\n    pytest.skip('x')\n\n\n"
            "def test_env_dependent():\n    pytest.skip('no dep')\n"
        },
    )
    task = mutation_task(origin, sha)
    ctx = materialize(task, suite, bench_ws)
    result = grade(task, ctx)
    assert result.solved is False  # a skipped fail_to_pass never verified the behaviour
    assert result.detail["pass_to_pass"]["total"] == 0  # env-dependent skip not counted


def test_class_and_parametrised_ids_resolve(tmp_path: Path, bench_ws: Path) -> None:
    body = (
        "import pytest\n\n\n"
        "class TestGroup:\n    def test_a(self):\n        assert True\n\n\n"
        "@pytest.mark.parametrize('x', [1, 'a b'])\ndef test_p(x):\n    assert x\n"
    )
    ids = [
        "tests/test_cls.py::TestGroup::test_a",
        "tests/test_cls.py::test_p[1]",
        "tests/test_cls.py::test_p[a b]",
    ]
    recipe = {
        "mode": "enhance",
        "hidden": {"tests/test_cls.py": "hidden/test_cls.py"},
        "fail_to_pass": ids,
    }
    origin, sha = make_origin(tmp_path)
    suite = write_suite(tmp_path, recipe, {"test_cls.py": body})
    task = mutation_task(origin, sha)
    ctx = materialize(task, suite, bench_ws)
    result = grade(task, ctx)
    assert result.solved is True
    assert set(result.detail["fail_to_pass"]["results"]) == set(ids)


# ------------------------------------------------------------------- runner e2e


def _write_run_suite(tmp_path: Path, origin: Path, sha: str, effect: str) -> tuple[Path, Path]:
    recipe = _inject_recipe()
    suite_dir = write_suite(tmp_path, recipe, {"test_clamp_hidden.py": HIDDEN_CLAMP})
    suite = {
        "version": "1.0",
        "id": "mutant-e2e",
        "domain": "software",
        "tier": "large",
        "tasks": [
            {
                "id": "t1",
                "category": "bugfix",
                "instruction": "tasks/t1/instruction.md",
                "source": {
                    "type": "repo_mutation",
                    "repo": origin.as_uri(),
                    "base_commit": sha,
                    "recipe": "tasks/t1/recipe.json",
                },
                "grader": {"type": "pytest_targeted"},
                "family": "repo-mutation",
                "language": "python",
            }
        ],
    }
    suite_path = suite_dir / "suite.json"
    suite_path.write_text(json.dumps(suite))
    subject_path = tmp_path / f"fake-{effect}.json"
    subject_path.write_text(
        json.dumps(
            {"version": "1.0", "id": f"fake-{effect}", "type": "fake", "scripted_effect": effect}
        )
    )
    return suite_path, subject_path


@pytest.mark.parametrize(("effect", "solved"), [("noop", False), ("copy-reference", True)])
def test_runner_end_to_end(tmp_path: Path, effect: str, solved: bool) -> None:
    origin, sha = make_origin(tmp_path)
    suite_path, subject_path = _write_run_suite(tmp_path, origin, sha, effect)
    assert load_suite(suite_path).tasks[0].family == "repo-mutation"

    record = run_suite(suite_path, subject_path, out_dir=tmp_path / "out")
    (task,) = record.tasks
    assert task.solved is solved
    assert task.grader_detail["collateral"]["files_touched"] == (1 if solved else 0)
    assert task.grader_detail["fail_to_pass"]["total"] == 1
