"""Load and validate benchmark suite & subject spec files (design doc §4.1, §5, §6).

Mirrors core `agent_orchestrator.spec`/`agent_orchestrator.config`'s load pattern
(read JSON/YAML -> jsonschema-validate -> pydantic-parse -> semantic cross-checks) but
is deliberately self-contained: `bench/` is a separate module outside the engine import
graph (SI-1) with its own schemas directory (`benchmarks/schemas/`, not `specs/`), so it
does not import core `config.py`'s private helpers.

`grader.type` / `subject.type` / a task's `source.type` are validated against the static
`KNOWN_GRADER_TYPES` / `KNOWN_SUBJECT_TYPES` / `KNOWN_WORKSPACE_PROVIDER_TYPES` closed
lists below, NOT against `registries.GRADER_REGISTRY` / `SUBJECT_REGISTRY` /
`WORKSPACE_PROVIDER_REGISTRY` -- checking against a registry populated by another
module's import-time side effect would make this module's validation order-dependent on
import order. Downstream tasks extend these sets alongside registering their classes
(see `registries.py` module docstring).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Literal

import jsonschema
from pydantic import BaseModel, ConfigDict

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

# Closed list of benchmark tiers (design doc / E-Bt4Xk9 T-Tr1Km8). The JSON schema's
# `enum` already rejects an unknown tier before a BenchSuite is even constructed; this
# set is re-checked in load_suite() for a clearer, bench-specific error message and
# defense-in-depth, mirroring the KNOWN_GRADER_TYPES/KNOWN_SUBJECT_TYPES/`_ID_PATTERN`
# precedent above. Per-tier defaults (budget caps, parallelism, timeout) live in
# `benchmarks/tiers.json`, loaded by `bench/tiers.py` -- this module only validates the
# tier NAME, it does not know about tier defaults.
KNOWN_TIERS: frozenset[str] = frozenset({"small", "medium", "large", "xlarge"})

# Closed list of workspace-provider types (design doc / E-Bt4Xk9 T-Wp4Nz5). A task's
# optional `source.type` is validated against this set in load_suite() rather than
# against `registries.WORKSPACE_PROVIDER_REGISTRY` directly, mirroring the
# KNOWN_GRADER_TYPES/KNOWN_SUBJECT_TYPES precedent above -- the registry is populated by
# `bench/workspace.py` at import time (only "fixture" today), and checking against it
# here would create an import-order dependency this module deliberately avoids (module
# docstring). T-Sw5Hd9 adds "swebench" to this set alongside registering its provider.
KNOWN_WORKSPACE_PROVIDER_TYPES: frozenset[str] = frozenset({"fixture"})


# ---------------------------------------------------------------------------
# Pydantic models (design doc §6)
# ---------------------------------------------------------------------------


class Assertion(BaseModel):
    """One check evaluated by a `file_assertion` grader (T-Grd7Vx) over the mutated repo."""

    type: Literal["exists", "contains", "equals_file"]
    path: str  # relative to the task's mutated repo
    substring: str | None = None  # required for type="contains"
    # Required for type="equals_file". Authored relative to the suite.json; load_suite
    # rewrites it to an absolute path so graders can resolve it at grade time (the
    # suite's base directory is not carried through to the grading context).
    golden: str | None = None


class GraderConfig(BaseModel):
    type: str  # validated against KNOWN_GRADER_TYPES in load_suite (registry is empty here)
    command: str | None = None
    cwd: str | None = None
    timeout_seconds: int | None = None
    pass_threshold: float | None = None
    assertions: list[Assertion] = []


class Source(BaseModel):
    """Optional task-level workspace-provider directive (FR-4, T-Wp4Nz5).

    An alternative to `BenchTask.fixture`: when present, `materialize_workspace`
    (bench/workspace.py) dispatches to `WORKSPACE_PROVIDER_REGISTRY[type]` instead of
    copying `fixture` into `ws/repo`. `type` is validated against the closed
    `KNOWN_WORKSPACE_PROVIDER_TYPES` list in `load_suite` below (mirrors
    `GraderConfig.type`/`SubjectSpec.type` -- the JSON schema deliberately leaves this
    open, see benchmark-suite.schema.json). Provider-specific fields (e.g. the
    `swebench` provider's `instance_id`/`dataset`/`revision`, T-Sw5Hd9) are NOT modeled
    here: `extra="allow"` passes them through unvalidated at this layer -- each provider
    is responsible for validating its own fields.
    """

    model_config = ConfigDict(extra="allow")

    type: str


class BenchTask(BaseModel):
    id: str
    category: Literal["bugfix", "feature", "refactor", "test", "other"]
    instruction: str  # PATH, relative to the suite.json
    # PATH to a tiny repo dir, relative to the suite.json. Optional when `source` is
    # set instead (T-Wp4Nz5) -- load_suite requires at least one of the two.
    fixture: str | None = None
    # Alternative to `fixture`: directs a non-default WorkspaceProvider (T-Wp4Nz5).
    source: Source | None = None
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
    # Defaults to "small" so `dev-core` (no `tier` field) stays byte-unchanged and still
    # validates -- see load_suite()'s KNOWN_TIERS check below for the closed-list gate.
    tier: str = "small"
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
    """Load *schema_file* from `_SCHEMAS_DIR` and validate *data* against it.

    `_SCHEMAS_DIR` is derived from `__file__` (module docstring), so it only resolves
    on a repo checkout -- a non-editable/packaged install has no `benchmarks/schemas/`
    on disk at all. Both that "dir missing" case and a merely missing/unreadable
    individual schema file are guarded here and turned into a typed
    `SpecValidationError` (caught by the CLI's existing `(SpecValidationError,
    BenchError)` handlers) with an actionable message, instead of letting a raw
    `FileNotFoundError` escape as an unhandled traceback (W1).
    """
    schema_path = _SCHEMAS_DIR / schema_file
    if not schema_path.is_file():
        raise SpecValidationError(
            "ao-bench must run from a repo checkout (schemas under benchmarks/schemas "
            f"are not packaged); missing schema file: {schema_path}",
            path=str(schema_path),
        )
    try:
        schema = json.loads(schema_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise SpecValidationError(
            f"Failed to read bench schema {schema_path}: {exc}", path=str(schema_path)
        ) from exc
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
    checks in the schema), an unknown `tier` (defaults to "small" when absent), a
    duplicate task id, an unknown `grader.type`, a missing `instruction` file, a task
    declaring neither `fixture` nor `source`, an unknown `source.type` (T-Wp4Nz5), a
    missing `fixture` directory (only checked when `source` is absent), or (for
    `file_assertion` graders) a missing/malformed `equals_file`/`contains` assertion.
    """
    data = _read_spec_file(path)
    _validate_against_schema(data, _SUITE_SCHEMA_FILE)
    try:
        suite = BenchSuite(**data)
    except Exception as exc:
        raise SpecValidationError(f"Suite model error: {exc}", path=str(path)) from exc

    _check_id_pattern(suite.id, "id")

    if suite.tier not in KNOWN_TIERS:
        raise SpecValidationError(
            f"Suite {suite.id!r}: unknown tier {suite.tier!r}; known tiers: {sorted(KNOWN_TIERS)}",
            path="tier",
        )

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

        # T-Wp4Nz5: a task declares its workspace via `fixture` (default `fixture`
        # provider) OR `source` (a non-default WorkspaceProvider, e.g. a SWE-bench
        # checkout) -- never neither. The fixture existence check only applies when
        # `source` is absent: a source provider materializes its own repo at run time
        # (bench/workspace.py), so there is nothing on disk to check here.
        if t.source is not None:
            if t.source.type not in KNOWN_WORKSPACE_PROVIDER_TYPES:
                raise SpecValidationError(
                    f"Task {t.id!r}: unknown source.type {t.source.type!r}; "
                    f"known types: {sorted(KNOWN_WORKSPACE_PROVIDER_TYPES)}",
                    path=f"tasks.{t.id}.source.type",
                )
        else:
            if t.fixture is None:
                raise SpecValidationError(
                    f"Task {t.id!r}: must declare either 'fixture' or 'source'",
                    path=f"tasks.{t.id}",
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
            # Rewrite to absolute in place: the grading context has no suite base dir,
            # so a still-relative golden would be unresolvable at grade time.
            assertion.golden = str(golden_path.resolve())
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
