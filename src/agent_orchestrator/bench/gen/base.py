"""Shared types for generated-task families (HLD §4.1, bench-probe-dsl.md §5).

A *family* turns ``(seed, difficulty, language)`` into an :class:`Instance`: the agent-visible
spec + skeleton, and the grader-owned probes + reference implementation.  The generation is a
pure function of its inputs -- one ``random.Random(seed)`` per ``generate`` call, no wall clock,
no ``set`` iteration in anything that is emitted -- so the same inputs are byte-identical across
runs and machines (the reference/probes are regenerated at materialise time, never stored in the
suite).
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

INSTANCE_VERSION = "1.0"
PROBES_VERSION = "1.0"

# Difficulty axes (HLD §4.1 "Difficulty vector"): each 0-3.
DIFFICULTY_MIN = 0
DIFFICULTY_MAX = 3
DIFFICULTY_AXES: tuple[str, ...] = ("entities", "traps", "concurrency", "scale")

# Languages the reference implementation is written in (DSL §5 `reference.language`).
REFERENCE_LANGUAGE = "python"
REFERENCE_ENTRY = "reference/run.sh"

Contract = Literal["http", "jsonl", "cli"]


class Difficulty(BaseModel):
    """Difficulty vector; defaults match ``ao-bench gen``'s default (entities=2,traps=2,...)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    entities: int = Field(2, ge=DIFFICULTY_MIN, le=DIFFICULTY_MAX)
    traps: int = Field(2, ge=DIFFICULTY_MIN, le=DIFFICULTY_MAX)
    concurrency: int = Field(1, ge=DIFFICULTY_MIN, le=DIFFICULTY_MAX)
    scale: int = Field(1, ge=DIFFICULTY_MIN, le=DIFFICULTY_MAX)

    def as_dict(self) -> dict[str, int]:
        return {axis: getattr(self, axis) for axis in DIFFICULTY_AXES}

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any] | None) -> Difficulty:
        """Strict parse: unknown axes, non-int (incl. bool) and out-of-range values are errors."""
        data = dict(raw or {})
        unknown = sorted(set(data) - set(DIFFICULTY_AXES))
        if unknown:
            raise ValueError(f"unknown difficulty axes {unknown}; known: {list(DIFFICULTY_AXES)}")
        for axis, value in data.items():
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"difficulty.{axis} must be an integer, got {value!r}")
            if not DIFFICULTY_MIN <= value <= DIFFICULTY_MAX:
                raise ValueError(
                    f"difficulty.{axis}={value} out of range {DIFFICULTY_MIN}..{DIFFICULTY_MAX}"
                )
        return cls(**data)


class Instance(BaseModel):
    """One concrete generated task.

    The first block mirrors ``instance.json`` (DSL §5); the second block is what the provider
    writes elsewhere (``spec_md``/``skeleton`` -> ``repo_dir``; ``probes``/``reference`` ->
    ``grading_dir``).  ``reference`` maps a path relative to ``<grading_dir>/reference/`` to its
    content (``run.sh`` + the Python program); the ``reference`` *key* of ``instance.json`` is the
    small descriptor produced by :meth:`instance_json`.
    """

    model_config = ConfigDict(extra="forbid")

    version: str = INSTANCE_VERSION
    family: str
    seed: int
    difficulty: Difficulty
    language: str
    contract: Contract
    entities: list[dict[str, Any]] = []
    traps: list[str]
    limits: dict[str, Any]
    allow: list[str]

    spec_md: str
    skeleton: dict[str, str]
    probes: dict[str, Any]
    reference: dict[str, str]

    def instance_json(self) -> dict[str, Any]:
        """The grader-owned ``instance.json`` body (DSL §5); the reference reads it at startup."""
        return {
            "version": self.version,
            "family": self.family,
            "seed": self.seed,
            "difficulty": self.difficulty.as_dict(),
            "language": self.language,
            "contract": self.contract,
            "entities": self.entities,
            "traps": self.traps,
            "limits": self.limits,
            "allow": self.allow,
            "reference": {"language": REFERENCE_LANGUAGE, "entry": REFERENCE_ENTRY},
        }

    def instance_json_text(self) -> str:
        return dump_json(self.instance_json())

    def probes_json_text(self) -> str:
        return dump_json(self.probes)


def dump_json(data: Any) -> str:
    """Canonical JSON text for every file the generators write (stable bytes)."""
    return json.dumps(data, indent=2, sort_keys=False, ensure_ascii=True) + "\n"


class Family(ABC):
    """A generator family: ``(seed, difficulty, language) -> Instance`` (pure, deterministic)."""

    name: ClassVar[str]
    contract: ClassVar[Contract]
    # Which task instruction the suite gives: "build" (implement SPEC.md from a skeleton) or
    # "debug" (the repo already implements SPEC.md, with planted faults to find and fix).
    instruction_kind: ClassVar[str] = "build"
    # Part of `ao-bench gen`'s default family set (families added later are opt-in via
    # `--family`, so the default output -- and committed suites -- stay byte-identical).
    in_default_set: ClassVar[bool] = True
    # Grader the suite gives this family's tasks: "blackbox" (probe DSL in probes.json) or
    # "objective" (open-scale cases + reference ladder in probes.json, T-Os5Lad).
    grader_type: ClassVar[str] = "blackbox"

    @abstractmethod
    def generate(
        self,
        seed: int,
        difficulty: Difficulty,
        language: str,
        allow: Sequence[str] | None = None,
    ) -> Instance:
        """Build the instance.  ``allow`` overrides the per-language dependency allow-list
        (the suite ``stack.<language>.allow`` entries); ``None`` = the defaults."""


FAMILY_REGISTRY: dict[str, type[Family]] = {}


def register_family(cls: type[Family]) -> type[Family]:
    """Class decorator: register *cls* under ``cls.name``.  Duplicates are a programming error."""
    if cls.name in FAMILY_REGISTRY:
        raise ValueError(f"Generator family already registered: {cls.name!r}")
    FAMILY_REGISTRY[cls.name] = cls
    return cls
