"""`ao migrate` Typer sub-app. Currently one migration: ``effort-keys`` (T-f0d7gm).

``effort`` historically meant the max-turn tier; the canonical name is now ``turn_budget`` (and
``effort`` will later mean thinking effort, T-9ymKja). ``ao migrate effort-keys`` renames the key in
a workspace's spec/config files. Dry-run (unified diff) by default; ``--apply`` writes. It is a
textual key rename (formatting/comments preserved) and idempotent: a second run finds nothing.
``thinking_effort`` is never touched, and a mapping that already holds BOTH keys is reported as a
conflict and left alone.
"""

from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import typer
import yaml

migrate_app = typer.Typer(name="migrate", no_args_is_help=True, help="Workspace migrations.")

LEGACY_KEY = "effort"
CANONICAL_KEY = "turn_budget"
# Globs (relative to the workspace root) of files that may carry the legacy key.
TARGET_GLOBS: tuple[str, ...] = (
    ".ao/config.yaml",
    ".ao/overlays/**/*",
    "specs/**/*",
    "workflows/**/*",
)
TARGET_SUFFIXES: tuple[str, ...] = (".json", ".yaml", ".yml", ".tmpl")
SKIP_DIR_NAMES = frozenset({".git", "node_modules", ".orchestrator", "outputs", "__pycache__"})

# `"effort":` (JSON key position) / `effort:` at the start of a YAML line. Exact-name anchored,
# so `thinking_effort` and `overseer_effort` never match; commented-out YAML lines are skipped.
_JSON_KEY = re.compile(r'"effort"(\s*:)')
_YAML_KEY = re.compile(r"^(\s*(?:-\s+)?)effort(\s*:)", re.M)


@dataclass
class FileChange:
    path: Path
    old: str
    new: str


@dataclass
class MigrationResult:
    changes: list[FileChange] = field(default_factory=list)
    conflicts: list[Path] = field(default_factory=list)


def _has_both_keys(node: Any) -> bool:
    if isinstance(node, dict):
        if LEGACY_KEY in node and CANONICAL_KEY in node:
            return True
        return any(_has_both_keys(v) for v in node.values())
    if isinstance(node, list):
        return any(_has_both_keys(v) for v in node)
    return False


def _parsed(path: Path, text: str) -> Any:
    """Parsed document, or None when the file is a template / unparseable (best effort)."""
    name = path.name
    try:
        if ".json" in name:
            return json.loads(text)
        if ".yaml" in name or ".yml" in name:
            return yaml.safe_load(text)
    except (ValueError, yaml.YAMLError):
        return None
    return None


def rewrite_text(path: Path, text: str) -> str:
    """Rename the legacy key in *text* (JSON or YAML flavour by file name)."""
    if ".json" in path.name:
        return _JSON_KEY.sub(rf'"{CANONICAL_KEY}"\1', text)
    return _YAML_KEY.sub(rf"\g<1>{CANONICAL_KEY}\g<2>", text)


def _iter_targets(root: Path) -> list[Path]:
    seen: dict[Path, None] = {}
    for pattern in TARGET_GLOBS:
        for p in sorted(root.glob(pattern)):
            rel = p.relative_to(root)
            if not p.is_file() or SKIP_DIR_NAMES.intersection(rel.parts[:-1]):
                continue
            name = p.name
            if not any(s in name for s in TARGET_SUFFIXES):
                continue
            seen[p] = None
    return list(seen)


def plan_effort_keys(root: Path) -> MigrationResult:
    result = MigrationResult()
    for path in _iter_targets(root):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        new = rewrite_text(path, text)
        if new == text:
            continue
        if _has_both_keys(_parsed(path, text)):
            result.conflicts.append(path)
            continue
        result.changes.append(FileChange(path, text, new))
    return result


@migrate_app.command("effort-keys")
def effort_keys(
    workspace: Path = typer.Option(
        Path("."), "--workspace", "-w", help="Workspace root (default: current directory)."
    ),
    apply: bool = typer.Option(
        False, "--apply", help="Write the changes (default: dry-run, print a diff only)."
    ),
) -> None:
    """Rename the deprecated `effort` key to `turn_budget` (specs, workflows, .ao/config.yaml)."""
    root = workspace.resolve()
    if not root.is_dir():
        typer.echo(f"ERROR: workspace {root} is not a directory", err=True)
        raise typer.Exit(1)
    result = plan_effort_keys(root)
    for ch in result.changes:
        rel = ch.path.relative_to(root)
        if apply:
            ch.path.write_text(ch.new, encoding="utf-8")
            typer.echo(f"rewrote {rel}")
        else:
            typer.echo(
                "".join(
                    difflib.unified_diff(
                        ch.old.splitlines(keepends=True),
                        ch.new.splitlines(keepends=True),
                        fromfile=f"a/{rel}",
                        tofile=f"b/{rel}",
                    )
                )
            )
    for path in result.conflicts:
        typer.echo(
            f"CONFLICT {path.relative_to(root)}: a mapping has both 'effort' and 'turn_budget'; "
            "left unchanged -- resolve by hand",
            err=True,
        )
    if not result.changes and not result.conflicts:
        typer.echo("nothing to migrate")
    elif not apply and result.changes:
        typer.echo(f"{len(result.changes)} file(s) would change; re-run with --apply to write.")
    elif apply:
        typer.echo(f"{len(result.changes)} file(s) migrated.")
    if result.conflicts:
        raise typer.Exit(1)
