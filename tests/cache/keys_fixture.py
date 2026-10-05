"""Shared setup for the key-builder tests: the GV-1 workspace (HLD 8.2.7) and request factory."""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path
from typing import Any

from agent_orchestrator.artifacts import LocalFsArtifactStore
from agent_orchestrator.cache.constants import CACHE_DIR_PARTS
from agent_orchestrator.cache.types import CacheKey, KeyDeps, KeyRequest
from agent_orchestrator.models import AgentSpec, TaskSpec
from tests.cache.fakes import fake_cli_version_of

GV1_CLI_VERSION = "2.1.278 (Claude Code)"
GV1_HEAD = "0123456789abcdef0123456789abcdef01234567"
GV1_FILES = {
    "specs/instr/summarize.md": "Summarize the inputs.\n",
    "docs/notes.md": "alpha\n",
    ".ao/house-rules.md": "Be concise.\n",
}
GV1_KEY = "6646469e94a695fe1a994d35f54ca74e007262911255552b03c54ce2e5d0319f"
GV1_COMPONENTS = {
    "agent": "ced58570dab6",
    "argv": "315cfbdef1cf",
    "dynamic_inputs": "4f53cda18c2b",
    "executor_fingerprint": "620dc66502c3",
    "general_instructions": "80c58b832f5c",
    "inputs": "6518d7ac7319",
    "instruction": "8f7ad8e7e8d2",
    "key_schema": "6b86b273ff34",
    "outputs": "2789b49e50b4",
    "prompt": "25e61c2662b6",
    "repo_heads": "b1e77f36ac12",
}


def write_files(root: Path, files: dict[str, str] | None = None) -> None:
    for rel, content in (GV1_FILES if files is None else files).items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)


def default_agent(**kw: Any) -> AgentSpec:
    kw.setdefault("executor", "claude_cli")
    if kw["executor"] == "claude_cli":
        kw.setdefault("model", "sonnet")
        kw.setdefault("effort", "medium")
    return AgentSpec(**kw)


def default_task(**kw: Any) -> TaskSpec:
    kw.setdefault("id", "summarize")
    kw.setdefault("agent", "writer")
    kw.setdefault("instruction", "specs/instr/summarize.md")
    kw.setdefault("inputs", ["docs/notes.md"])
    kw.setdefault("outputs", ["out/summary.md"])
    return TaskSpec(**kw)


def make_request(ws: Path, **overrides: Any) -> KeyRequest:
    """The GV-1 request over workspace *ws* (files written by `write_files`)."""
    store = LocalFsArtifactStore(str(ws))
    root = store.root
    base = KeyRequest(
        task=default_task(),
        agent=default_agent(),
        workspace_root=root,
        artifact_store=store,
        cache_root=os.path.join(root, *CACHE_DIR_PARTS),
        general_instruction_paths=(os.path.join(root, ".ao", "house-rules.md"),),
        dynamic_input_paths=(),
        repo_paths={"core": root},
        repo_heads={"core": GV1_HEAD},
        include_repo_heads=True,
        control_paths_abs=frozenset(),
        environ={},
        max_input_bytes=10**9,
        max_input_files=10**6,
    )
    return replace(base, **overrides)


def make_deps(version: str = GV1_CLI_VERSION) -> KeyDeps:
    return KeyDeps(cli_version_of=fake_cli_version_of(version))


def key_for(ws: Path, deps: KeyDeps | None = None, **overrides: Any) -> CacheKey:
    from agent_orchestrator.cache.keys import build_cache_key

    return build_cache_key(make_request(ws, **overrides), deps or make_deps())
