"""Load and validate benchmark suite & subject spec files (design doc §4.1, §5, §6).

Mirrors core `agent_orchestrator.spec`/`agent_orchestrator.config`'s load pattern
(read JSON/YAML -> jsonschema-validate -> pydantic-parse -> semantic cross-checks) but
is deliberately self-contained: `bench/` is a separate module outside the engine import
graph (SI-1) with its own schemas directory (`benchmarks/schemas/`, not `specs/`), so it
does not import core `config.py`'s private helpers.

`grader.type` / `subject.type` are validated against the static `KNOWN_GRADER_TYPES` /
`KNOWN_SUBJECT_TYPES` closed lists below, NOT against `registries.GRADER_REGISTRY` /
`SUBJECT_REGISTRY` -- those registries are empty until `T-Grd7Vx`/`T-Sbj9Ka` register
concrete classes, and checking against an empty registry would reject every suite/
subject. Downstream tasks extend these sets alongside registering their classes (see
`registries.py` module docstring).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Literal

import jsonschema
from pydantic import BaseModel

from .errors import SpecValidationError

# Schemas live in benchmarks/schemas/ at the repo root (four levels up from this file's
# package dir: bench/ -> agent_orchestrator/ -> src/ -> repo root).
_SCHEMAS_DIR = Path(__file__).resolve().parent.parent.parent.parent / "benchmarks" / "schemas"
_SUITE_SCHEMA_FILE = "benchmark-suite.schema.json"
_SUBJECT_SCHEMA_FILE = "subject.schema.json"

# Suite/task/subject id pattern -- lowercase kebab, mirrors core workflow.schema.json /
# spec.py's `^[a-z0-9][a-z0-9-_]*$` convention exactly (CLAUDE.md hard rule). The JSON
# schemas already enforce this via `pattern` (first line of defense, rejected before a
# BenchSuite/SubjectSpec is even constructed); this regex is re-checked in Python for
# defense-in-depth and a clearer, bench-specific error message, mirroring core
# `spec.py`'s `_ROUTING_ID_PATTERN`/`_check_reserved_id` precedent for ids the schema
# can't fully police on its own.
_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-_]*$")

# Closed lists of implemented types (design doc §6). The JSON schemas leave `type` as an
# open string (see benchmark-suite.schema.json/subject.schema.json) precisely so these
# sets -- not a schema edit -- are the single place that grows as T-Grd7Vx/T-Sbj9Ka land.
KNOWN_GRADER_TYPES: frozenset[str] = frozenset({"pytest", "command", "file_assertion", "fake"})
KNOWN_SUBJECT_TYPES: frozenset[str] = frozenset({"claude_cli", "ao_workflow", "fake"})


# ---------------------------------------------------------------------------
# Pydantic models (design doc §6)
# ---------------------------------------------------------------------------


class Assertion(BaseModel):
    """One check evaluated by a `file_assertion` grader (T-Grd7Vx) over the mutated repo."""

    type: Literal["exists", "contains", "equals_file"]
    path: str  # relative to the task's mutated repo
    substring: str | None = None  # required for type="contains"
    golden: str | None = None  # required for type="equals_file"; relative to the suite.json


class GraderConfig(BaseModel):
    type: str  # validated against KNOWN_GRADER_TYPES in load_suite (registry is empty here)
    command: str | None = None
    cwd: str | None = None
    timeout_seconds: int | None = None
    pass_threshold: float | None = None
    assertions: list[Assertion] = []


class BenchTask(BaseModel):
    id: str
    category: Literal["bugfix", "feature", "refactor", "test", "other"]
    instruction: str  # PATH, relative to the suite.json
    fixture: str  # PATH to a tiny repo dir, relative to the suite.json
    grader: GraderConfig
    timeout_seconds: int | None = None
    tags: list[str] = []


class BenchSuiteDefaults(BaseModel):
    timeout_seconds: int | None = None


class BenchSuite(BaseModel):
    version: str
    id: str
    domain: str
    description: str = ""
    defaults: BenchSuiteDefaults = BenchSuiteDefaults()
    tasks: list[BenchTask]

    def task(self, task_id: str) -> BenchTask:
        for t in self.tasks:
            if t.id == task_id:
                return t
        raise KeyError(task_id)


class SubjectSpec(BaseModel):
    version: str
    id: str
    type: str  # validated against KNOWN_SUBJECT_TYPES in load_subject (registry is empty here)
    model: str | None = None
    permission_mode: str | None = None
    max_turns: int | None = None
    prompt_template: str | None = None
    extra_args: list[str] = []
    # ao_workflow subject template paths, relative to the subject.json.
    workflow: str | None = None
    reposets: str | None = None
    agents: str | None = None
    max_parallel: int | None = None
    budget_total: int | None = None
    # fake subject (test-only).
    scripted_effect: str | None = None
    fake_cost: float | None = None
    fake_tokens: dict[str, int] | None = None


# ---------------------------------------------------------------------------
# File IO + schema validation helpers
# ---------------------------------------------------------------------------


def _read_spec_file(path: str | Path) -> dict:
    """Read a suite/subject spec file (JSON or YAML) into a dict.

    Raises SpecValidationError (naming the file) for a missing file, malformed
    JSON/YAML, or a non-object top-level value.
    """
    p = Path(path)
    if not p.exists():
        raise SpecValidationError(f"Spec file not found: {p}", path=str(p))
    text = p.read_text()
    if p.suffix in (".yaml", ".yml"):
        import yaml

        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise SpecValidationError(f"Invalid YAML in {p}: {exc}", path=str(p)) from exc
    else:
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise SpecValidationError(f"Invalid JSON in {p}: {exc}", path=str(p)) from exc
    if not isinstance(data, dict):
        raise SpecValidationError(
            f"Spec file {p} must contain a JSON/YAML object at the top level", path=str(p)
        )
    return data


def _validate_against_schema(data: dict, schema_file: str) -> None:
    schema_path = _SCHEMAS_DIR / schema_file
    schema = json.loads(schema_path.read_text())
    try:
        jsonschema.validate(data, schema)
    except jsonschema.ValidationError as exc:
        raise SpecValidationError(str(exc.message), path=exc.json_path) from exc


def _check_id_pattern(value: str, path: str) -> None:
    if not _ID_PATTERN.match(value):
        raise SpecValidationError(
            f"{path}: id {value!r} must be lowercase, matching pattern {_ID_PATTERN.pattern!r}",
            path=path,
        )


# ---------------------------------------------------------------------------
# Loaders (design doc §4.1 pseudocode)
# ---------------------------------------------------------------------------


def load_suite(path: str | Path) -> BenchSuite:
    """Load a suite JSON/YAML file, validate it, and return a `BenchSuite`.

    Raises SpecValidationError for: malformed JSON/YAML, a schema violation (including
    a missing `version` or an uppercase/malformed id -- both required-field/`pattern`
    checks in the schema), a duplicate task id, an unknown `grader.type`, a missing
    `instruction` file, a missing `fixture` directory, or (for `file_assertion` graders)
    a missing/malformed `equals_file`/`contains` assertion.
    """
    data = _read_spec_file(path)
    _validate_against_schema(data, _SUITE_SCHEMA_FILE)
    try:
        suite = BenchSuite(**data)
    except Exception as exc:
        raise SpecValidationError(f"Suite model error: {exc}", path=str(path)) from exc

    _check_id_pattern(suite.id, "id")

    base = Path(path).resolve().parent
    seen_task_ids: set[str] = set()
    for t in suite.tasks:
        _check_id_pattern(t.id, f"tasks.{t.id}.id")

        if t.id in seen_task_ids:
            raise SpecValidationError(
                f"Suite {suite.id!r}: duplicate task id {t.id!r}",
                path=f"tasks.{t.id}.id",
            )
        seen_task_ids.add(t.id)

        instruction_path = base / t.instruction
        if not instruction_path.exists():
            raise SpecValidationError(
                f"Task {t.id!r}: instruction not found: {instruction_path}",
                path=f"tasks.{t.id}.instruction",
            )
        fixture_path = base / t.fixture
        if not fixture_path.is_dir():
            raise SpecValidationError(
                f"Task {t.id!r}: fixture directory not found: {fixture_path}",
                path=f"tasks.{t.id}.fixture",
            )

        if t.grader.type not in KNOWN_GRADER_TYPES:
            raise SpecValidationError(
                f"Task {t.id!r}: unknown grader.type {t.grader.type!r}; "
                f"known types: {sorted(KNOWN_GRADER_TYPES)}",
                path=f"tasks.{t.id}.grader.type",
            )

        _check_assertions(t, base)

    return suite


def _check_assertions(task: BenchTask, base: Path) -> None:
    """`file_assertion` grader edge case (design doc §4.3): an `equals_file` assertion's
    `golden` reference must exist at LOAD time, not at grade time; a `contains`
    assertion must declare its `substring`. Both are structural spec errors, not a
    runtime grading outcome.
    """
    for i, assertion in enumerate(task.grader.assertions):
        item_path = f"tasks.{task.id}.grader.assertions[{i}]"
        if assertion.type == "equals_file":
            if not assertion.golden:
                raise SpecValidationError(
                    f"Task {task.id!r}: assertion[{i}] type=equals_file requires 'golden'",
                    path=f"{item_path}.golden",
                )
            golden_path = base / assertion.golden
            if not golden_path.is_file():
                raise SpecValidationError(
                    f"Task {task.id!r}: golden file not found: {golden_path}",
                    path=f"{item_path}.golden",
                )
        elif assertion.type == "contains" and not assertion.substring:
            raise SpecValidationError(
                f"Task {task.id!r}: assertion[{i}] type=contains requires 'substring'",
                path=f"{item_path}.substring",
            )


def load_subject(path: str | Path) -> SubjectSpec:
    """Load a subject JSON/YAML file, validate it, and return a `SubjectSpec`.

    Raises SpecValidationError for: malformed JSON/YAML, a schema violation (including a
    missing `version` or an uppercase/malformed id), or an unknown `type` (message lists
    `KNOWN_SUBJECT_TYPES`).
    """
    data = _read_spec_file(path)
    _validate_against_schema(data, _SUBJECT_SCHEMA_FILE)
    try:
        subject = SubjectSpec(**data)
    except Exception as exc:
        raise SpecValidationError(f"Subject model error: {exc}", path=str(path)) from exc

    _check_id_pattern(subject.id, "id")

    if subject.type not in KNOWN_SUBJECT_TYPES:
        raise SpecValidationError(
            f"Unknown subject type {subject.type!r}; known types: {sorted(KNOWN_SUBJECT_TYPES)}",
            path="type",
        )

    return subject
