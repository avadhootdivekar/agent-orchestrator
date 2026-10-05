"""Executor fingerprint and the shared path guards of the cache key (HLD 8.2.5, 8.2.6).

The fingerprint captures what, outside the argv and the prompt, can change what `claude` does:
its version, a small closed allowlist of environment variables, and the project context files it
reads on its own (`CLAUDE.md`, `.mcp.json`, `.claude/...`) from the workspace root down to the
agent's working directory. Anything that cannot be fingerprinted safely makes the task
uncacheable (fail-closed).

`guarded_resolve` / `guarded_abs` live HERE (keys.py imports them; this module must never import
keys.py, so the import graph stays acyclic).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from agent_orchestrator.cache.constants import (
    CLI_VERSION_TIMEOUT_SECONDS,
    KIND_ABSENT,
    MAX_CLI_VERSION_CHARS,
    REASON_EXECUTOR_FINGERPRINT_UNAVAILABLE,
    REASON_PATH_IN_CACHE_DIR,
    REASON_PATH_REJECTED,
)
from agent_orchestrator.cache.hashing import HashBudget, digest_path
from agent_orchestrator.cache.safeio import posix_rel
from agent_orchestrator.cache.types import KeyDeps, KeyRequest, UncacheableError
from agent_orchestrator.errors import ArtifactPathError

if TYPE_CHECKING:
    from agent_orchestrator.artifacts import ArtifactStore

# Deliberately SMALL and CLOSED (A-9): adding a name needs review. NEVER add a secret (API keys,
# tokens). These change which model runs or how much it may emit, so they must change the key.
CLAUDE_FINGERPRINT_ENV_VARS = (
    "ANTHROPIC_MODEL",
    "ANTHROPIC_SMALL_FAST_MODEL",
    "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    "CLAUDE_CODE_SUBAGENT_MODEL",
    "CLAUDE_CODE_MAX_OUTPUT_TOKENS",
    "MAX_THINKING_TOKENS",
    "CLAUDE_CONFIG_DIR",
)
# Project context `claude` loads by itself, relative to each directory from the workspace root
# down to the agent cwd. Fixed order: the key must not depend on directory listing order.
CLAUDE_CONTEXT_PATHS = (
    "CLAUDE.md",
    "CLAUDE.local.md",
    ".mcp.json",
    ".claude/settings.json",
    ".claude/settings.local.json",
    ".claude/agents",
    ".claude/commands",
    ".claude/skills",
)

# Errors `Path.resolve` / the artifact store can raise for a hostile path: traversal, a symlink
# loop (RuntimeError on older Pythons, OSError on newer), a NUL byte (ValueError).
RESOLVE_FAILURES = (ArtifactPathError, OSError, RuntimeError, ValueError)


def _in_cache_root(path: str, cache_root: str) -> bool:
    return path == cache_root or path.startswith(cache_root + os.sep)


def try_resolve(store: ArtifactStore, raw: str) -> str | None:
    """`store.resolve(raw)`, or None when the path is hostile or unresolvable."""
    try:
        return store.resolve(raw)
    except RESOLVE_FAILURES:
        return None


def guarded_resolve(req: KeyRequest, raw: str) -> str:
    """Resolve *raw* through the engine's own path guard; refuse the cache directory itself."""
    try:
        resolved = req.artifact_store.resolve(raw)
    except RESOLVE_FAILURES as e:
        raise UncacheableError(REASON_PATH_REJECTED, raw) from e
    if _in_cache_root(resolved, req.cache_root):
        raise UncacheableError(REASON_PATH_IN_CACHE_DIR, raw)
    return resolved


def guarded_abs(req: KeyRequest, abs_path: str) -> str:
    """Containment guard for an ALREADY-absolute path (general instructions, repo paths)."""
    ws = req.workspace_root
    if abs_path != ws and not abs_path.startswith(ws + os.sep):
        raise UncacheableError(REASON_PATH_REJECTED, abs_path)
    if _in_cache_root(abs_path, req.cache_root):
        raise UncacheableError(REASON_PATH_IN_CACHE_DIR, abs_path)
    return abs_path


class CliVersionReader:
    """`claude --version`, memoized per binary IDENTITY: (realpath, mtime_ns, size).

    An auto-updated binary changes its mtime / size, so it is re-read and the key changes.
    """

    def __init__(self) -> None:
        self._memo: dict[tuple[str, int, int], str] = {}

    def version(self, binary: str) -> str:
        path = shutil.which(binary)
        if path is None:
            raise UncacheableError(REASON_EXECUTOR_FINGERPRINT_UNAVAILABLE, binary)
        try:
            real = os.path.realpath(path)
            st = os.stat(real)
        except OSError as e:
            raise UncacheableError(REASON_EXECUTOR_FINGERPRINT_UNAVAILABLE, binary) from e
        ident = (real, st.st_mtime_ns, st.st_size)
        if ident not in self._memo:
            try:
                cp = subprocess.run(  # noqa: S603 -- fixed argv, shell=False
                    [real, "--version"],
                    capture_output=True,
                    timeout=CLI_VERSION_TIMEOUT_SECONDS,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as e:
                raise UncacheableError(REASON_EXECUTOR_FINGERPRINT_UNAVAILABLE, binary) from e
            out = cp.stdout.decode("utf-8", "replace").strip()
            if cp.returncode != 0 or not out:
                raise UncacheableError(REASON_EXECUTOR_FINGERPRINT_UNAVAILABLE, binary)
            self._memo[ident] = out[:MAX_CLI_VERSION_CHARS]
        return self._memo[ident]


def _context_dirs(workspace_root: str, cwd_abs: str) -> list[str]:
    """The workspace root plus every directory strictly below it on the way down to *cwd_abs*."""
    dirs = [workspace_root]
    rel = posix_rel(cwd_abs, workspace_root)
    if rel != ".":
        current = workspace_root
        for part in rel.split("/"):
            current = os.path.join(current, part)
            dirs.append(current)
    return dirs


def claude_cli_fingerprint(req: KeyRequest, deps: KeyDeps, budget: HashBudget) -> dict[str, Any]:
    """`{cli_version, env, context_files}` for a claude_cli agent (`executor_fingerprint`)."""
    if not req.agent.command_template:
        raise UncacheableError(REASON_EXECUTOR_FINGERPRINT_UNAVAILABLE, "empty command_template")
    cli_version = deps.cli_version_of(req.agent.command_template[0])
    env = _allowlisted_env(req.environ)
    # The same resolution the dispatch uses for the agent's cwd.
    cwd_abs = guarded_resolve(req, req.agent.working_dir or ".")
    context: list[dict[str, Any]] = []
    for directory in _context_dirs(req.workspace_root, cwd_abs):
        for name in CLAUDE_CONTEXT_PATHS:
            raw = posix_rel(os.path.join(directory, name), req.workspace_root)
            if not os.path.lexists(os.path.join(directory, name)):
                context.append({"path": raw, "kind": KIND_ABSENT})
                continue
            # A symlinked CLAUDE.md is followed ONLY inside the workspace (the guard rejects
            # anything else); a symlink INSIDE a context directory makes the task uncacheable.
            digest = digest_path(guarded_resolve(req, raw), budget)
            context.append(
                {"path": raw, "kind": digest.kind, "sha256": digest.sha256, "size": digest.size}
            )
    return {"cli_version": cli_version, "env": env, "context_files": context}


def _allowlisted_env(environ: Mapping[str, str]) -> dict[str, str]:
    return {name: environ[name] for name in CLAUDE_FINGERPRINT_ENV_VARS if name in environ}
