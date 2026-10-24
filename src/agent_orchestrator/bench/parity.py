"""Subject parity bundle (benchmark-discriminating-suites-hld.md §2b).

The harness primarily evaluates `ao` itself, and an `ao` subject carries its own agent
instructions, skills and limits. To keep a comparison apples-to-apples, every subject in
a campaign runs under ONE shared bundle: the same `CLAUDE.md`, the same `.claude/` tree
(skills/commands/settings), the same environment knobs, turn limit and permission mode.
The bundle's content fingerprint is recorded on every run so `build_comparison` can refuse
to compare runs produced under different bundles.

Layout of a bundle dir (`parity.json` may also be pointed at directly):

    parity.json          -- this module's `ParityBundle` fields
    CLAUDE.md            -- `claude_md` (copied to <ws>/CLAUDE.md -- see `apply_parity`)
    claude/              -- `claude_dir` (copied to <repo_dir>/.claude/)

Import direction: read-only from `errors`; nothing in core imports this.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from ..errors import SpecValidationError

PARITY_FILENAME = "parity.json"
# Where the shared operating instructions land. The WORKSPACE (parent of `repo/`), not the
# repo itself: Claude Code loads CLAUDE.md from every ancestor of its cwd, both `claude -p`
# (cwd = repo_dir) and every `ao` agent (working_dir = repo) are rooted one level below, and
# a Tier-2 repo's own tracked CLAUDE.md is left untouched (no diff pollution). The `.claude/`
# tree goes INTO the repo (skills/commands/settings are project-root scoped) and is excluded
# from git via .git/info/exclude when the repo is a checkout.
CLAUDE_MD_FILENAME = "CLAUDE.md"
CLAUDE_DIRNAME = ".claude"
GIT_EXCLUDE_RELPATH = Path(".git") / "info" / "exclude"
_EXCLUDE_MARKER = "# ao-bench parity bundle (auto-added)"


class ParityBundle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = "1.0"
    # Paths relative to `base_dir`; None = that part is not provided.
    claude_dir: str | None = None
    claude_md: str | None = None
    # Applied to every subject subprocess environment (opaque to the harness).
    env: dict[str, str] = {}
    # Applied to every subject unless the CLI overrides it for ALL of them.
    max_turns: int | None = None
    permission_mode: str | None = None
    # Language toolchains every subject gets on its host (`bench/toolchains.py`), e.g.
    # ["go", "node"]; installed under the bench root and exposed via PATH + CLAUDE_ENV_FILE.
    toolchains: list[str] = []
    # Lint budget for subject wrapper prompts (`ao-bench validate --parity`): a wrapper
    # longer than this is treated as smuggling instructions past the bundle.
    wrapper_prompt_max_chars: int = 600
    # Set by `load_parity`; never serialised into the fingerprint.
    base_dir: str | None = None

    def resolved(self, field: str | None) -> Path | None:
        if field is None:
            return None
        base = Path(self.base_dir) if self.base_dir else Path.cwd()
        return (base / field).resolve()


def load_parity(path: str | Path) -> ParityBundle:
    """Load a bundle from a dir (containing `parity.json`) or a `parity.json` path."""
    p = Path(path)
    file = p / PARITY_FILENAME if p.is_dir() else p
    if not file.is_file():
        raise SpecValidationError(f"parity bundle not found: {file}", path=str(file))
    try:
        data: dict[str, Any] = json.loads(file.read_text())
    except json.JSONDecodeError as exc:
        raise SpecValidationError(
            f"invalid JSON in parity bundle {file}: {exc}", path=str(file)
        ) from exc
    try:
        bundle = ParityBundle(**data, base_dir=str(file.parent))
    except (TypeError, ValueError) as exc:
        raise SpecValidationError(f"invalid parity bundle {file}: {exc}", path=str(file)) from exc
    for field in ("claude_dir", "claude_md"):
        resolved = bundle.resolved(getattr(bundle, field))
        if resolved is not None and not resolved.exists():
            raise SpecValidationError(
                f"parity bundle {file}: {field} path not found: {resolved}", path=str(file)
            )
    return bundle


def _iter_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*") if p.is_file())


def parity_fingerprint(bundle: ParityBundle) -> str:
    """sha256 over the bundle's config AND the content of every file it ships, so editing
    a skill or CLAUDE.md changes the fingerprint even though parity.json is unchanged.
    """
    h = hashlib.sha256()
    config = bundle.model_dump(mode="json", exclude={"base_dir"})
    h.update(json.dumps(config, sort_keys=True, separators=(",", ":")).encode())
    claude_md = bundle.resolved(bundle.claude_md)
    if claude_md is not None:
        h.update(b"\0CLAUDE.md\0")
        h.update(claude_md.read_bytes())
    claude_dir = bundle.resolved(bundle.claude_dir)
    if claude_dir is not None:
        for f in _iter_files(claude_dir):
            h.update(b"\0" + str(f.relative_to(claude_dir)).encode() + b"\0")
            h.update(f.read_bytes())
    return h.hexdigest()


def append_git_exclude(repo_dir: Path, rel_paths: list[str]) -> None:
    git_dir = repo_dir / ".git"
    if not git_dir.is_dir():
        return
    exclude = repo_dir / GIT_EXCLUDE_RELPATH
    exclude.parent.mkdir(parents=True, exist_ok=True)
    existing = exclude.read_text() if exclude.exists() else ""
    lines = [p for p in rel_paths if p not in existing.splitlines()]
    if not lines:
        return
    with exclude.open("a") as fh:
        if existing and not existing.endswith("\n"):
            fh.write("\n")
        fh.write(_EXCLUDE_MARKER + "\n" + "\n".join(lines) + "\n")


def apply_parity(bundle: ParityBundle, *, repo_dir: Path, workspace: Path) -> list[Path]:
    """Materialise the bundle for one run. Returns the paths written (for tests/logs).

    - `claude_md` -> `<workspace>/CLAUDE.md` (see `CLAUDE_MD_FILENAME` note above).
    - `claude_dir/**` -> `<repo_dir>/.claude/**` (overlay; bundle wins on conflicts), and
      `.claude/` is added to `.git/info/exclude` when `repo_dir` is a git checkout.
    """
    written: list[Path] = []
    claude_md = bundle.resolved(bundle.claude_md)
    if claude_md is not None:
        dest = workspace / CLAUDE_MD_FILENAME
        shutil.copy2(claude_md, dest)
        written.append(dest)
    claude_dir = bundle.resolved(bundle.claude_dir)
    if claude_dir is not None:
        dest_dir = repo_dir / CLAUDE_DIRNAME
        shutil.copytree(claude_dir, dest_dir, dirs_exist_ok=True)
        written.append(dest_dir)
        append_git_exclude(repo_dir, [f"/{CLAUDE_DIRNAME}/"])
    return written


__all__ = [
    "CLAUDE_DIRNAME",
    "CLAUDE_MD_FILENAME",
    "PARITY_FILENAME",
    "ParityBundle",
    "append_git_exclude",
    "apply_parity",
    "load_parity",
    "parity_fingerprint",
]
