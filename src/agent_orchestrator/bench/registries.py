"""Extension-point registries for bench subjects, graders, and workspace providers
(design doc §3.2, §6).

Empty dicts + register helpers only -- this task (T-Sc4Hm2) is the foundation the rest
of `bench/` builds on, and does not define any concrete `Subject`/`Grader`/
`WorkspaceProvider` classes. `T-Sbj9Ka` registers `claude_cli`/`ao_workflow`/`fake` into
`SUBJECT_REGISTRY`; `T-Grd7Vx` registers `pytest`/`command`/`file_assertion`/`fake` into
`GRADER_REGISTRY`; `T-Wp4Nz5` registers `fixture` into `WORKSPACE_PROVIDER_REGISTRY`
(`T-Sw5Hd9` later appends `swebench`).

Until those land, `bench/spec.py` validates `subject.type`/`grader.type`/a task's
`source.type` against its own static `KNOWN_SUBJECT_TYPES`/`KNOWN_GRADER_TYPES`/
`KNOWN_WORKSPACE_PROVIDER_TYPES` closed lists rather than these registries (which are
empty here and would reject every spec) -- see `spec.py` module docstring.
"""

from __future__ import annotations

from typing import Any

# type -> concrete Subject class (a `Protocol`/ABC landing in T-Sbj9Ka's subjects.py).
SUBJECT_REGISTRY: dict[str, type[Any]] = {}

# type -> concrete Grader class (a `Protocol`/ABC landing in T-Grd7Vx's graders.py).
GRADER_REGISTRY: dict[str, type[Any]] = {}

# type -> concrete WorkspaceProvider class (an ABC landing in T-Wp4Nz5's workspace.py).
# Keyed by a task's `source.type`, or "fixture" (the default when a task declares no
# `source` at all).
WORKSPACE_PROVIDER_REGISTRY: dict[str, type[Any]] = {}


def register_subject(type_name: str, cls: type[Any]) -> None:
    """Register a Subject adapter class under `type_name` (a `subject.json` `type` value).

    Raises ValueError on duplicate registration -- two modules registering the same
    type name is a programming error, not a runtime/spec condition.
    """
    if type_name in SUBJECT_REGISTRY:
        raise ValueError(f"Subject type already registered: {type_name!r}")
    SUBJECT_REGISTRY[type_name] = cls


def register_grader(type_name: str, cls: type[Any]) -> None:
    """Register a Grader adapter class under `type_name` (a suite task's `grader.type` value).

    Raises ValueError on duplicate registration -- two modules registering the same
    type name is a programming error, not a runtime/spec condition.
    """
    if type_name in GRADER_REGISTRY:
        raise ValueError(f"Grader type already registered: {type_name!r}")
    GRADER_REGISTRY[type_name] = cls


def register_workspace_provider(type_name: str, cls: type[Any]) -> None:
    """Register a WorkspaceProvider adapter class under `type_name` (a suite task's
    `source.type` value, or "fixture" for the default provider).

    Raises ValueError on duplicate registration -- two modules registering the same
    type name is a programming error, not a runtime/spec condition.
    """
    if type_name in WORKSPACE_PROVIDER_REGISTRY:
        raise ValueError(f"Workspace provider type already registered: {type_name!r}")
    WORKSPACE_PROVIDER_REGISTRY[type_name] = cls
