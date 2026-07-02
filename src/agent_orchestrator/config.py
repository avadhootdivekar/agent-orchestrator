"""Load and validate reposet and agents config files."""

from __future__ import annotations

import json
import os
from pathlib import Path

import jsonschema

from .errors import ConfigError, SpecValidationError
from .models import AgentSpec, RepoSet

# Schemas live in specs/ at the repo root (three levels up from this file's package dir).
SCHEMAS_DIR = Path(__file__).parent.parent.parent / "specs"


def _load_file(path: str | Path) -> dict:
    p = Path(path)
    if not p.exists():
        raise ConfigError(f"File not found: {path}")
    text = p.read_text()
    if p.suffix in (".yaml", ".yml"):
        import yaml

        return yaml.safe_load(text)
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Invalid JSON in {path}: {exc}") from exc


def _validate_against_schema(data: dict, schema_file: str) -> None:
    schema_path = SCHEMAS_DIR / schema_file
    if schema_path.exists():
        schema = json.loads(schema_path.read_text())
        try:
            jsonschema.validate(data, schema)
        except jsonschema.ValidationError as e:
            raise SpecValidationError(str(e.message), path=e.json_path) from e


def load_reposets(path: str | Path) -> dict[str, RepoSet]:
    """Load and validate a reposets config file."""
    data = _load_file(path)
    _validate_against_schema(data, "reposet.schema.json")
    return {k: RepoSet(**v) for k, v in data["repo_sets"].items()}


def load_agents(path: str | Path) -> dict[str, AgentSpec]:
    """Load and validate an agents config file."""
    data = _load_file(path)
    _validate_against_schema(data, "agents.schema.json")
    return {k: AgentSpec(**v) for k, v in data["agents"].items()}


def get_schemas_dir() -> Path:
    """Return the resolved path to the schemas directory (respects AO_SCHEMAS_DIR env override)."""
    env_override = os.environ.get("AO_SCHEMAS_DIR")
    if env_override:
        return Path(env_override)
    return SCHEMAS_DIR
