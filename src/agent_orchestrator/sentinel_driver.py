"""Sentinel LLM driver boundary (E-Sn7tL2 T-Sn8Gen, HLD §8.1).

A generation is a bounded LLM process that reads a deterministic *pack* directory and writes
files only (inbox proposals, ``report.md``, ``handoff.json``, ``memory-append.md``). The engine
talks to it
through :class:`SentinelDriver` (``start`` / ``poll`` / ``stop``) so tests can substitute a scripted
fake and other providers can plug in. ``poll``/``stop`` never block (the main-thread tick calls
them).
``start`` may block briefly (process spawn) and therefore runs on the 1-worker lane executor.
"""

from __future__ import annotations

import json
import os
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from .executors.claude_cli import (
    build_claude_argv,
    parse_usage_and_429,
    terminate_process_group,
)
from .models import AgentSpec

TRANSCRIPT_FILE: Final = "transcript.jsonl"
STDERR_FILE: Final = "stderr.txt"
PACK_ENTRYPOINT: Final = "README.md"
"""The file inside the pack the generation prompt points the model at first."""
STOP_GRACE_SECONDS: Final = 10
DEFAULT_SENTINEL_MAX_TURNS: Final = 40

# Least-privilege permission set for the default (no ``llm.agent``) generation (T-Rk4Wn7 D4).
# A bare ``claude -p`` runs in permission mode ``default`` and every Write/Bash is refused in
# non-interactive mode. Instead of ``--dangerously-skip-permissions`` the default spawn gets
# ``acceptEdits`` (file edits auto-approved) scoped by ``--add-dir`` to the generation directory
# and the inbox only, plus an explicit tool allow-list: read-only tools, Write/Edit and
# ``mv`` (the atomic write-*.tmp-then-rename the prompt asks for). No other Bash command is
# pre-approved, so a prompt-injected generation cannot run arbitrary shell. Caveat (T-Rk4Wn7 L2):
# ``--add-dir`` scopes Write/Edit only; ``Bash(mv:*)`` is NOT path-scoped, so a prompt-injected
# generation could ``mv`` files outside those directories. Accepted residual risk for now.
LANE_PERMISSION_MODE: Final = "acceptEdits"
LANE_ALLOWED_TOOLS: Final = "Read,Glob,Grep,Write,Edit,Bash(mv:*)"
_PERMISSION_FLAGS: Final = frozenset(
    {
        "--dangerously-skip-permissions",
        "--permission-mode",
        "--allowedTools",
        "--allowed-tools",
        "--permission-prompt-tool",
    }
)


def has_permission_flags(argv: list[str]) -> bool:
    """True when *argv* already carries any permission-related ``claude`` flag."""
    return any(a.split("=", 1)[0] in _PERMISSION_FLAGS for a in argv)


def lane_permission_args(gen_dir: str, inbox_dir: str) -> list[str]:
    """Pure: the least-privilege flags that let a generation write *gen_dir* and *inbox_dir*."""
    out = ["--permission-mode", LANE_PERMISSION_MODE, "--allowedTools", LANE_ALLOWED_TOOLS]
    for d in (gen_dir, inbox_dir):
        if d:
            out += ["--add-dir", d]
    return out


def _invokes_claude(argv: list[str]) -> bool:
    """True when *argv* runs ``claude`` directly or wrapped (``env``/``npx``/path-qualified)."""
    for tok in argv:
        base = os.path.basename(tok)
        if base in ("claude", "claude-code") or tok.startswith("@anthropic-ai/claude-code"):
            return True
    return False


def lane_permission_warnings(
    powers: str, agent: AgentSpec | None, agent_name: str | None = None
) -> list[str]:
    """Warnings for an LLM lane whose spawned session could not write its outputs.

    Only an explicit ``llm.agent`` can be at fault (the default spawn carries the flags above):
    with ``powers: act`` and no permission flag in its argv, ``claude -p`` refuses every write.
    """
    if powers != "act" or agent is None:
        return []
    argv = list(agent.command_template) + list(agent.extra_args)
    if not _invokes_claude(argv) or has_permission_flags(argv):
        return []
    return [
        f"sentinel.llm.agent {agent_name or '<agent>'!r} has no permission flag "
        "(--dangerously-skip-permissions / --permission-mode / --allowedTools) and "
        "sentinel.llm.powers is 'act': the generation cannot write report.md, handoff.json or "
        "inbox proposals and will end sentinel_ungraceful. Add a permission flag to the agent "
        "or unset sentinel.llm.agent to use the default least-privilege spawn."
    ]


def detect_permission_denial(transcript: str) -> str | None:
    """Return a short description when the stream-json *transcript* shows refused tool calls."""
    for line in transcript.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if not isinstance(ev, dict):
            continue
        denials = ev.get("permission_denials")
        if ev.get("type") == "result" and isinstance(denials, list) and denials:
            names = sorted({str(d.get("tool_name", "?")) for d in denials if isinstance(d, dict)})
            return f"{len(denials)} tool call(s) denied: {', '.join(names)}"
    return None


@dataclass(frozen=True)
class GenerationLimits:
    """Bounds a driver must respect for one generation."""

    ttl_seconds: int
    max_tokens: int
    max_budget_usd: float | None = None
    generation: int = 0
    inbox_dir: str = ""
    """Absolute path the generation writes ``ao.ctl/v1`` proposals into."""


@dataclass
class DriverStatus:
    """One non-blocking poll of a handle."""

    alive: bool
    returncode: int | None = None
    tokens_used: int = 0
    """Best-effort tokens so far (0 when the driver cannot tell before exit)."""
    cost_usd: float | None = None
    """Final cost, available once ``alive`` is False (None = unknown)."""
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_creation_input_tokens: int | None = None
    cache_read_input_tokens: int | None = None
    error: str | None = None
    permission_denied: str | None = None
    """Set (description) when the session had tool calls refused for lack of permission."""


@dataclass
class Handle:
    """Opaque per-generation handle; drivers stash whatever they need in ``data``."""

    generation: int
    pack_dir: str
    data: dict[str, Any] = field(default_factory=dict)


class SentinelDriver(ABC):
    """Starts, polls and stops one LLM generation."""

    @abstractmethod
    def start(self, pack_dir: str, limits: GenerationLimits) -> Handle:
        """Spawn the generation reading *pack_dir*; return immediately with a handle."""

    @abstractmethod
    def poll(self, handle: Handle) -> DriverStatus:
        """Non-blocking status; once ``alive`` is False the status carries final usage."""

    @abstractmethod
    def stop(self, handle: Handle) -> None:
        """Terminate the generation (idempotent); afterwards ``poll`` reports not alive."""


def render_prompt(pack_dir: str, limits: GenerationLimits) -> str:
    """The (path-only, NFR-1) prompt handed to the model."""
    return (
        f"You are sentinel generation g{limits.generation:02d}, a bounded run overseer. "
        f"Read {os.path.join(pack_dir, PACK_ENTRYPOINT)} and sentinel-contract.md in the same "
        f"directory first, then follow them exactly. Write proposals only as ao.ctl/v1 JSON files "
        f"into {limits.inbox_dir} (atomic: write *.tmp then rename), and write report.md, "
        f"handoff.json and memory-append.md next to the pack directory. Finish within "
        f"{limits.ttl_seconds // 60} minutes."
    )


class ClaudeCliSentinelDriver(SentinelDriver):
    """Runs a generation as a ``claude -p`` subprocess (own process group, files for output)."""

    def __init__(self, agent: AgentSpec | None = None, *, cwd: str | None = None) -> None:
        base = agent or AgentSpec(executor="claude_cli", max_turns=DEFAULT_SENTINEL_MAX_TURNS)
        self._agent = base
        # Only the default spawn gets the lane flags; an explicit ``llm.agent`` is never altered
        # (``lane_permission_warnings`` tells the operator when it lacks permission flags).
        self._default_agent = agent is None
        self._cwd = cwd

    def start(self, pack_dir: str, limits: GenerationLimits) -> Handle:
        gen_dir = Path(pack_dir).parent
        argv = build_claude_argv(self._agent, render_prompt(pack_dir, limits))
        if self._default_agent and not has_permission_flags(argv):
            argv += lane_permission_args(str(gen_dir), limits.inbox_dir)
        if limits.max_budget_usd is not None and "--max-budget-usd" not in argv:
            argv += ["--max-budget-usd", f"{limits.max_budget_usd:.4f}"]
        transcript = gen_dir / TRANSCRIPT_FILE
        stderr = gen_dir / STDERR_FILE
        with (
            open(transcript, "w", encoding="utf-8") as tf,
            open(stderr, "w", encoding="utf-8") as ef,
        ):
            proc = subprocess.Popen(  # noqa: S603 -- argv built from the agent spec, no shell
                argv,
                cwd=self._cwd or str(gen_dir),
                stdin=subprocess.DEVNULL,
                stdout=tf,
                stderr=ef,
                start_new_session=True,
            )
        return Handle(
            generation=limits.generation,
            pack_dir=pack_dir,
            data={"proc": proc, "transcript": str(transcript), "stderr": str(stderr)},
        )

    def poll(self, handle: Handle) -> DriverStatus:
        proc: subprocess.Popen[Any] = handle.data["proc"]
        rc = proc.poll()
        if rc is None:
            return DriverStatus(alive=True)
        text = Path(handle.data["transcript"]).read_text(encoding="utf-8", errors="replace")
        err = Path(handle.data["stderr"]).read_text(encoding="utf-8", errors="replace")
        # Same usage/cost parsing as ClaudeCliExecutor (one parser, no parallel implementation).
        usage = parse_usage_and_429(text, err, rc, 0.0)
        in_tok, out_tok = usage["input_tokens"], usage["output_tokens"]
        return DriverStatus(
            alive=False,
            returncode=rc,
            tokens_used=(in_tok or 0) + (out_tok or 0),
            cost_usd=usage["cost_usd"],
            input_tokens=in_tok,
            output_tokens=out_tok,
            cache_creation_input_tokens=usage["cache_creation_input_tokens"],
            cache_read_input_tokens=usage["cache_read_input_tokens"],
            error=None if rc == 0 else (err or text)[-300:] or f"exit {rc}",
            permission_denied=detect_permission_denial(text),
        )

    def stop(self, handle: Handle) -> None:
        proc: subprocess.Popen[Any] = handle.data["proc"]
        if proc.poll() is None:
            terminate_process_group(proc, STOP_GRACE_SECONDS)


def make_driver(name: str, agent: AgentSpec | None, cwd: str | None) -> SentinelDriver:
    """Driver factory keyed by ``sentinel.llm.driver`` (only ``claude-cli`` exists today)."""
    if name == "claude-cli":
        return ClaudeCliSentinelDriver(agent, cwd=cwd)
    raise ValueError(f"unknown sentinel driver {name!r}; supported: claude-cli")
