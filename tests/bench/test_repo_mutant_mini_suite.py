"""The committed `repo-mutant-mini` suite (E-Bn7Dsc T-T2-5Ij6, HLD §4.2 Tier 2b/2c).

Offline half (default tier): the suite and every recipe load/validate, pins are full shas,
hidden-test sources exist, the agent-visible instructions do not leak the mutation or the
hidden test files.

Network half (`@pytest.mark.network`, opt-in via `AO_E2E_NETWORK=1`): the real thing -- clone
the pinned public repos, build an env with `uv`, and prove through the real runner that each
task is *unsolved* with the mutation in place (`noop`) and *solved* once the pristine files
are restored (`FakeSubject copy-reference`) -- the same check recorded in each recipe's
`verified` field.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agent_orchestrator.bench import mutation_provider, pytest_targeted  # noqa: F401
from agent_orchestrator.bench.cli import app
from agent_orchestrator.bench.mutation_provider import load_recipe
from agent_orchestrator.bench.runner import run_suite
from agent_orchestrator.bench.spec import BenchTask, load_suite

_SUITE_PATH = (
    Path(__file__).resolve().parents[2]
    / "benchmarks"
    / "suites"
    / "repo-mutant-mini"
    / "suite.json"
)
_SUITE_DIR = _SUITE_PATH.parent
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_MIN_P2P = 30  # the grader samples up to 30 pass_to_pass ids


def _tasks() -> list[BenchTask]:
    return load_suite(_SUITE_PATH).tasks


def _recipe(task: BenchTask):
    extra = task.source.model_extra or {}  # type: ignore[union-attr]
    return load_recipe(_SUITE_DIR / extra["recipe"]), extra


def test_suite_loads_and_validates_via_the_cli() -> None:
    suite = load_suite(_SUITE_PATH)
    assert suite.id == "repo-mutant-mini" and suite.tier == "large"
    result = CliRunner().invoke(app, ["validate", "--suite", str(_SUITE_PATH)])
    assert result.exit_code == 0, result.output


def test_three_inject_and_one_excise_on_small_public_repos() -> None:
    modes = sorted(_recipe(t)[0].mode for t in _tasks())
    assert modes == ["excise", "inject", "inject", "inject"]
    repos = {(t.source.model_extra or {})["repo"] for t in _tasks()}  # type: ignore[union-attr]
    assert repos <= {
        "https://github.com/pallets/click",
        "https://github.com/python-attrs/attrs",
        "https://github.com/Textualize/rich",
    }


@pytest.mark.parametrize("task", _tasks(), ids=lambda t: t.id)
def test_task_is_pinned_graded_and_recorded_as_verified(task: BenchTask) -> None:
    recipe, extra = _recipe(task)
    assert task.source is not None and task.source.type == "repo_mutation"
    assert _SHA_RE.match(extra["base_commit"]), "base_commit must be a full pinned sha"
    assert task.grader.type == "pytest_targeted"
    assert task.family == "repo-mutation" and task.language == "python"
    assert recipe.install and "uv" in recipe.install
    assert recipe.verified and recipe.verified["date"] and recipe.verified["note"]
    assert recipe.verified.get("network") is True  # authored + verified with real clones
    if recipe.mode == "excise":
        assert recipe.test_targets and recipe.hidden_from_repo
    else:
        assert recipe.fail_to_pass
        assert not set(recipe.fail_to_pass) & set(recipe.pass_to_pass)
    assert len(set(recipe.pass_to_pass)) >= _MIN_P2P
    for edit in recipe.edits:
        assert edit.find != edit.replace


@pytest.mark.parametrize("task", _tasks(), ids=lambda t: t.id)
def test_hidden_sources_exist_and_fail_to_pass_files_are_covered(task: BenchTask) -> None:
    recipe, _ = _recipe(task)
    for source in recipe.hidden.values():
        assert (_SUITE_DIR / source).is_file()
    held_out_files = set(recipe.hidden) | set(recipe.hidden_from_repo)
    # Every hidden/removed test file must actually be one a grading id points at; ids in
    # other files are visible-suite tests the agent can see (rich: test_progress.py).
    graded_files = {i.split("::")[0] for i in recipe.fail_to_pass} | set(recipe.test_targets)
    assert held_out_files <= graded_files


@pytest.mark.parametrize("task", _tasks(), ids=lambda t: t.id)
def test_instruction_does_not_leak_the_mutation_or_hidden_tests(task: BenchTask) -> None:
    recipe, _ = _recipe(task)
    text = (_SUITE_DIR / task.instruction).read_text()
    assert text.startswith("#") and "TODO" not in text
    for edit in recipe.edits:
        assert edit.find not in text
        assert not edit.replace or edit.replace not in text  # excise edits replace with ""
    for hidden_path in [*recipe.hidden, *recipe.hidden_from_repo]:
        assert Path(hidden_path).name not in text
    assert (
        "hidden" not in text.lower().replace("held out", "") or "hidden tests" not in text.lower()
    )


# ------------------------------------------------------------------- network tier


@pytest.mark.network
@pytest.mark.parametrize("task", _tasks(), ids=lambda t: t.id)
@pytest.mark.parametrize(("effect", "solved"), [("noop", False), ("copy-reference", True)])
def test_real_clone_unsolved_with_mutation_and_solved_with_pristine_files(
    task: BenchTask, effect: str, solved: bool, tmp_path: Path
) -> None:
    subject = tmp_path / f"fake-{effect}.json"
    subject.write_text(
        json.dumps(
            {"version": "1.0", "id": f"fake-{effect}", "type": "fake", "scripted_effect": effect}
        )
    )
    record = run_suite(
        _SUITE_PATH, subject, out_dir=tmp_path / "out", task_filter=[task.id], force=True
    )
    (result,) = record.tasks
    assert result.subject_status == "succeeded", result.raw_error
    assert result.solved is solved, result.grader_raw_tail
    assert result.grader_detail["install"]["command"]
