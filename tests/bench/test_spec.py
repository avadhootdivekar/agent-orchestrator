"""Unit tests for bench/spec.py: load_suite / load_subject (AC 1-4 of T-Sc4Hm2)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_orchestrator.bench.errors import SpecValidationError
from agent_orchestrator.bench.spec import (
    KNOWN_GRADER_TYPES,
    KNOWN_SUBJECT_TYPES,
    KNOWN_TIERS,
    load_subject,
    load_suite,
)

from .conftest import SubjectFactory, SuiteFactory

# ---------------------------------------------------------------------------
# Happy path (AC1)
# ---------------------------------------------------------------------------


def test_load_suite_valid(suite_factory: SuiteFactory) -> None:
    suite_path = suite_factory()
    suite = load_suite(suite_path)
    assert suite.id == "dev-core"
    assert suite.version == "1.0"
    assert suite.domain == "software"
    assert len(suite.tasks) == 1
    assert suite.tasks[0].id == "bugfix-off-by-one"
    assert suite.tasks[0].grader.type == "pytest"


def test_load_suite_multiple_tasks_unique_ids(suite_factory: SuiteFactory) -> None:
    tasks = [
        {
            "id": "bugfix-a",
            "category": "bugfix",
            "instruction": "tasks/bugfix-a/instruction.md",
            "fixture": "tasks/bugfix-a/fixture",
            "grader": {"type": "pytest"},
        },
        {
            "id": "feature-b",
            "category": "feature",
            "instruction": "tasks/feature-b/instruction.md",
            "fixture": "tasks/feature-b/fixture",
            "grader": {"type": "command", "command": "true"},
        },
    ]
    suite_path = suite_factory(tasks=tasks)
    suite = load_suite(suite_path)
    assert [t.id for t in suite.tasks] == ["bugfix-a", "feature-b"]
    assert suite.task("feature-b").grader.type == "command"


def test_load_subject_valid_fake(subject_factory: SubjectFactory) -> None:
    subject_path = subject_factory(type_="fake", extra={"scripted_effect": "copy-solution"})
    subject = load_subject(subject_path)
    assert subject.id == "fake-pass"
    assert subject.type == "fake"
    assert subject.scripted_effect == "copy-solution"


def test_load_subject_valid_claude_cli(subject_factory: SubjectFactory) -> None:
    subject_path = subject_factory(
        subject_id="claude-opus",
        type_="claude_cli",
        extra={"model": "claude-opus-4-8", "permission_mode": "bypassPermissions"},
    )
    subject = load_subject(subject_path)
    assert subject.type == "claude_cli"
    assert subject.model == "claude-opus-4-8"


def test_load_subject_valid_ao_workflow(subject_factory: SubjectFactory) -> None:
    subject_path = subject_factory(
        subject_id="ao-epic",
        type_="ao_workflow",
        extra={"workflow": "workflow.json", "reposets": "reposet.json", "agents": "agents.json"},
    )
    subject = load_subject(subject_path)
    assert subject.type == "ao_workflow"
    assert subject.workflow == "workflow.json"


# ---------------------------------------------------------------------------
# Tier field (T-Tr1Km8 AC1-2): optional, defaults to "small", validated against
# KNOWN_TIERS ("small", "medium", "large", "xlarge").
# ---------------------------------------------------------------------------


def test_load_suite_tier_defaults_to_small_when_absent(suite_factory: SuiteFactory) -> None:
    suite_path = suite_factory()
    suite = load_suite(suite_path)
    assert suite.tier == "small"


def test_load_suite_tier_medium_accepted(suite_factory: SuiteFactory) -> None:
    suite_path = suite_factory(extra_top_level={"tier": "medium"})
    suite = load_suite(suite_path)
    assert suite.tier == "medium"


@pytest.mark.parametrize("tier", sorted(KNOWN_TIERS))
def test_load_suite_every_known_tier_accepted(suite_factory: SuiteFactory, tier: str) -> None:
    suite_path = suite_factory(extra_top_level={"tier": tier})
    suite = load_suite(suite_path)
    assert suite.tier == tier


def test_load_suite_unknown_tier_rejected(suite_factory: SuiteFactory) -> None:
    suite_path = suite_factory(extra_top_level={"tier": "gigantic"})
    with pytest.raises(SpecValidationError) as exc_info:
        load_suite(suite_path)
    msg = str(exc_info.value)
    assert "gigantic" in msg
    for known in KNOWN_TIERS:
        assert known in msg


def test_committed_dev_core_suite_has_no_tier_field_and_defaults_to_small() -> None:
    """AC1: the committed dev-core/suite.json (no `tier` field at all -- confirmed by
    `git diff --stat benchmarks/suites/dev-core` staying empty for this task, checked
    separately in CI/verification, not here) must still validate and default to
    `tier == "small"`.
    """
    repo_root = Path(__file__).resolve().parents[2]
    suite_path = repo_root / "benchmarks" / "suites" / "dev-core" / "suite.json"
    raw = json.loads(suite_path.read_text())
    assert "tier" not in raw, "dev-core/suite.json must stay byte-unchanged (no tier field added)"
    suite = load_suite(suite_path)
    assert suite.tier == "small"


# ---------------------------------------------------------------------------
# Malformed JSON / missing file (edge cases, errors.py coverage)
# ---------------------------------------------------------------------------


def test_load_suite_missing_file(tmp_path: Path) -> None:
    with pytest.raises(SpecValidationError, match="not found"):
        load_suite(tmp_path / "nope.json")


def test_load_suite_malformed_json(tmp_path: Path) -> None:
    bad = tmp_path / "suite.json"
    bad.write_text("{not valid json")
    with pytest.raises(SpecValidationError, match="Invalid JSON"):
        load_suite(bad)


def test_load_suite_non_object_top_level(tmp_path: Path) -> None:
    bad = tmp_path / "suite.json"
    bad.write_text("[1, 2, 3]")
    with pytest.raises(SpecValidationError, match="top level"):
        load_suite(bad)


def test_load_subject_missing_file(tmp_path: Path) -> None:
    with pytest.raises(SpecValidationError, match="not found"):
        load_subject(tmp_path / "nope.json")


def test_load_subject_malformed_json(tmp_path: Path) -> None:
    bad = tmp_path / "subject.json"
    bad.write_text("{not valid json")
    with pytest.raises(SpecValidationError, match="Invalid JSON"):
        load_subject(bad)


# ---------------------------------------------------------------------------
# Schema violations: additionalProperties:false + required version (AC2, AC4)
# ---------------------------------------------------------------------------


def test_load_suite_missing_version_rejected(suite_factory: SuiteFactory) -> None:
    suite_path = suite_factory(omit_version=True)
    with pytest.raises(SpecValidationError):
        load_suite(suite_path)


def test_load_suite_unknown_top_level_key_rejected(suite_factory: SuiteFactory) -> None:
    suite_path = suite_factory(extra_top_level={"unexpected_field": True})
    with pytest.raises(SpecValidationError):
        load_suite(suite_path)


def test_load_suite_unknown_task_level_key_rejected(suite_factory: SuiteFactory) -> None:
    task = {
        "id": "bugfix-a",
        "category": "bugfix",
        "instruction": "tasks/bugfix-a/instruction.md",
        "fixture": "tasks/bugfix-a/fixture",
        "grader": {"type": "pytest"},
        "not_a_real_field": 1,
    }
    suite_path = suite_factory(tasks=[task])
    with pytest.raises(SpecValidationError):
        load_suite(suite_path)


def test_load_suite_unknown_grader_level_key_rejected(suite_factory: SuiteFactory) -> None:
    task = {
        "id": "bugfix-a",
        "category": "bugfix",
        "instruction": "tasks/bugfix-a/instruction.md",
        "fixture": "tasks/bugfix-a/fixture",
        "grader": {"type": "pytest", "bogus": "x"},
    }
    suite_path = suite_factory(tasks=[task])
    with pytest.raises(SpecValidationError):
        load_suite(suite_path)


def test_load_subject_missing_version_rejected(subject_factory: SubjectFactory) -> None:
    subject_path = subject_factory(omit_version=True)
    with pytest.raises(SpecValidationError):
        load_subject(subject_path)


def test_load_subject_unknown_key_rejected(subject_factory: SubjectFactory) -> None:
    subject_path = subject_factory(extra={"bogus_field": 1})
    with pytest.raises(SpecValidationError):
        load_subject(subject_path)


# ---------------------------------------------------------------------------
# Lowercase id pattern (AC2)
# ---------------------------------------------------------------------------


def test_load_suite_uppercase_suite_id_rejected(suite_factory: SuiteFactory) -> None:
    suite_path = suite_factory(suite_id="Dev-Core")
    with pytest.raises(SpecValidationError):
        load_suite(suite_path)


def test_load_suite_uppercase_task_id_rejected(suite_factory: SuiteFactory) -> None:
    task = {
        "id": "Bugfix-Off-By-One",
        "category": "bugfix",
        "instruction": "tasks/bugfix-off-by-one/instruction.md",
        "fixture": "tasks/bugfix-off-by-one/fixture",
        "grader": {"type": "pytest"},
    }
    suite_path = suite_factory(tasks=[task])
    with pytest.raises(SpecValidationError) as exc_info:
        load_suite(suite_path)
    assert "Bugfix-Off-By-One" in str(exc_info.value)


def test_load_subject_uppercase_id_rejected(subject_factory: SubjectFactory) -> None:
    subject_path = subject_factory(subject_id="Fake-Pass")
    with pytest.raises(SpecValidationError):
        load_subject(subject_path)


# ---------------------------------------------------------------------------
# Duplicate task id (AC2)
# ---------------------------------------------------------------------------


def test_load_suite_duplicate_task_id_rejected(suite_factory: SuiteFactory) -> None:
    tasks = [
        {
            "id": "bugfix-a",
            "category": "bugfix",
            "instruction": "tasks/bugfix-a/instruction.md",
            "fixture": "tasks/bugfix-a/fixture",
            "grader": {"type": "pytest"},
        },
        {
            "id": "bugfix-a",
            "category": "feature",
            "instruction": "tasks/bugfix-a-2/instruction.md",
            "fixture": "tasks/bugfix-a-2/fixture",
            "grader": {"type": "command", "command": "true"},
        },
    ]
    suite_path = suite_factory(tasks=tasks)
    with pytest.raises(SpecValidationError, match="duplicate task id") as exc_info:
        load_suite(suite_path)
    assert "bugfix-a" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Missing fixture / instruction paths (AC2)
# ---------------------------------------------------------------------------


def test_load_suite_missing_fixture_dir_rejected(suite_factory: SuiteFactory) -> None:
    suite_path = suite_factory(materialize=False)
    # Materialize only the instruction file, leave fixture dir absent.
    (suite_path.parent / "tasks" / "bugfix-off-by-one").mkdir(parents=True)
    (suite_path.parent / "tasks" / "bugfix-off-by-one" / "instruction.md").write_text("# fix\n")
    with pytest.raises(SpecValidationError, match="fixture directory not found"):
        load_suite(suite_path)


def test_load_suite_missing_instruction_file_rejected(suite_factory: SuiteFactory) -> None:
    suite_path = suite_factory(materialize=False)
    fixture_dir = suite_path.parent / "tasks" / "bugfix-off-by-one" / "fixture"
    fixture_dir.mkdir(parents=True)
    with pytest.raises(SpecValidationError, match="instruction not found"):
        load_suite(suite_path)


# ---------------------------------------------------------------------------
# Unknown grader.type / subject.type (AC2, AC3)
# ---------------------------------------------------------------------------


def test_load_suite_unknown_grader_type_rejected(suite_factory: SuiteFactory) -> None:
    task = {
        "id": "bugfix-a",
        "category": "bugfix",
        "instruction": "tasks/bugfix-a/instruction.md",
        "fixture": "tasks/bugfix-a/fixture",
        "grader": {"type": "llm_judge"},
    }
    suite_path = suite_factory(tasks=[task])
    with pytest.raises(SpecValidationError, match="unknown grader.type") as exc_info:
        load_suite(suite_path)
    msg = str(exc_info.value)
    assert "llm_judge" in msg
    for known in KNOWN_GRADER_TYPES:
        assert known in msg


def test_load_subject_unknown_type_rejected(subject_factory: SubjectFactory) -> None:
    subject_path = subject_factory(type_="http_api")
    with pytest.raises(SpecValidationError, match="Unknown subject type") as exc_info:
        load_subject(subject_path)
    msg = str(exc_info.value)
    assert "http_api" in msg
    for known in KNOWN_SUBJECT_TYPES:
        assert known in msg


# ---------------------------------------------------------------------------
# file_assertion grader edge cases (design doc §4.3, checked at load time)
# ---------------------------------------------------------------------------


def test_load_suite_file_assertion_equals_file_missing_golden_field(
    suite_factory: SuiteFactory,
) -> None:
    task = {
        "id": "test-golden",
        "category": "test",
        "instruction": "tasks/test-golden/instruction.md",
        "fixture": "tasks/test-golden/fixture",
        "grader": {
            "type": "file_assertion",
            "assertions": [{"type": "equals_file", "path": "out.txt"}],
        },
    }
    suite_path = suite_factory(tasks=[task])
    with pytest.raises(SpecValidationError, match="requires 'golden'"):
        load_suite(suite_path)


def test_load_suite_file_assertion_golden_file_missing_on_disk(suite_factory: SuiteFactory) -> None:
    task = {
        "id": "test-golden",
        "category": "test",
        "instruction": "tasks/test-golden/instruction.md",
        "fixture": "tasks/test-golden/fixture",
        "grader": {
            "type": "file_assertion",
            "assertions": [{"type": "equals_file", "path": "out.txt", "golden": "golden/out.txt"}],
        },
    }
    suite_path = suite_factory(tasks=[task], materialize_goldens=False)
    with pytest.raises(SpecValidationError, match="golden file not found"):
        load_suite(suite_path)


def test_load_suite_file_assertion_equals_file_golden_present_ok(
    suite_factory: SuiteFactory,
) -> None:
    task = {
        "id": "test-golden",
        "category": "test",
        "instruction": "tasks/test-golden/instruction.md",
        "fixture": "tasks/test-golden/fixture",
        "grader": {
            "type": "file_assertion",
            "assertions": [{"type": "equals_file", "path": "out.txt", "golden": "golden/out.txt"}],
        },
    }
    suite_path = suite_factory(tasks=[task])
    suite = load_suite(suite_path)
    # load_suite rewrites `golden` to an absolute path so graders can resolve it
    # at grade time without the suite's base directory.
    resolved = suite.tasks[0].grader.assertions[0].golden
    assert resolved == str((suite_path.parent / "golden/out.txt").resolve())


def test_load_suite_file_assertion_contains_missing_substring(suite_factory: SuiteFactory) -> None:
    task = {
        "id": "test-contains",
        "category": "test",
        "instruction": "tasks/test-contains/instruction.md",
        "fixture": "tasks/test-contains/fixture",
        "grader": {
            "type": "file_assertion",
            "assertions": [{"type": "contains", "path": "out.txt"}],
        },
    }
    suite_path = suite_factory(tasks=[task])
    with pytest.raises(SpecValidationError, match="requires 'substring'"):
        load_suite(suite_path)


# ---------------------------------------------------------------------------
# YAML support (JSON/YAML declarative specs, mirrors core spec loading)
# ---------------------------------------------------------------------------


def test_load_suite_yaml(tmp_path: Path, suite_factory: SuiteFactory) -> None:
    json_path = suite_factory(dest_name="suite.json")
    data = json.loads(json_path.read_text())
    yaml_path = tmp_path / "suite.yaml"
    import yaml

    yaml_path.write_text(yaml.safe_dump(data))
    suite = load_suite(yaml_path)
    assert suite.id == "dev-core"


def test_load_suite_invalid_yaml(tmp_path: Path) -> None:
    bad = tmp_path / "suite.yaml"
    bad.write_text("{")
    with pytest.raises(SpecValidationError, match="Invalid YAML"):
        load_suite(bad)


# ---------------------------------------------------------------------------
# W1 -- missing/non-packaged schemas dir must fail as a typed SpecValidationError
# (not a raw FileNotFoundError) with an actionable "run from a repo checkout" message.
# ---------------------------------------------------------------------------


def test_load_suite_missing_schemas_dir_raises_typed_error(
    suite_factory: SuiteFactory, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    suite_path = suite_factory()
    monkeypatch.setattr(
        "agent_orchestrator.bench.spec._SCHEMAS_DIR", tmp_path / "does-not-exist-schemas"
    )
    # pytest.raises(SpecValidationError) already proves this is NOT a raw
    # FileNotFoundError escaping -- a FileNotFoundError would propagate uncaught here.
    with pytest.raises(SpecValidationError, match="repo checkout") as exc_info:
        load_suite(suite_path)
    assert "benchmarks/schemas" in str(exc_info.value)


def test_load_subject_missing_schemas_dir_raises_typed_error(
    subject_factory: SubjectFactory, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    subject_path = subject_factory()
    monkeypatch.setattr(
        "agent_orchestrator.bench.spec._SCHEMAS_DIR", tmp_path / "does-not-exist-schemas"
    )
    with pytest.raises(SpecValidationError, match="repo checkout") as exc_info:
        load_subject(subject_path)
    assert "benchmarks/schemas" in str(exc_info.value)
