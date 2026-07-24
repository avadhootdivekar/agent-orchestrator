"""ClaudeCliExecutor — runs tasks by invoking the `claude` CLI subprocess.

Full multi-turn capture (observability & traceability)
------------------------------------------------------
The CLI is invoked with ``--output-format stream-json --verbose`` so it emits
**one JSON object per event** (a JSONL stream) rather than a single collapsed
result blob.  Every turn — the ``system`` init event, each ``assistant`` message
(text + tool_use), each ``user`` message (tool_result), and the final
``result`` event — is streamed live to ``transcript.jsonl`` in the task's output
directory.  This means output/errors from *all* turns are preserved, not just
the final turn (which is all ``--output-format json`` ever exposed).

Because stdout is redirected straight to the transcript file at the OS level,
a task that runs for a long time and then times out still leaves a *partial*
transcript on disk instead of losing everything.

Captured artifacts per task (all under ``ctx.output_dir``):
    transcript.jsonl  raw per-turn event stream (machine-readable, all turns)
    stdout.txt        human-readable rendering of the transcript (greppable)
    stderr.txt        raw stderr
    result.json       the final ``type:"result"`` event (final text + usage)
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

from ..models import TaskContext, TaskResult
from .base import Executor
from .prompt import build_prompt

# --- Output-format flags (stream-json is required to capture every turn) ------
# stream-json emits one JSON event per line; --verbose is mandatory for
# stream-json under --print (headless) mode, else the CLI errors out.
_OUTPUT_FORMAT_FLAG = "--output-format"
_OUTPUT_FORMAT_VALUE = "stream-json"
_VERBOSE_FLAG = "--verbose"

# --- Headless tool policy (`claude -p`) ---------------------------------------
# Default posture is ALLOW ALL: this executor injects no tool restriction unless
# an agent opts in via `AgentSpec.disallowed_tools` (or sets its own policy flag
# in command_template/extra_args). Web search, TodoWrite, and subagent spawning
# stay enabled by default — disabling is opt-in, per agent.
#
# The one class worth knowing about: background-shell tooling under `claude -p`.
# `claude -p` runs the agent loop and exits the moment the model ends a turn with
# no pending tool calls — there is NO persistent session left alive to observe a
# `run_in_background` shell finishing or to be re-invoked when it does, so the
# shell is torn down with the process (or orphaned, unobserved) and its monitors
# read nothing. `RECOMMENDED_HEADLESS_DISALLOWED_TOOLS` names that set so opting
# out is copy-paste; see ADR-0005 for the full rationale and other candidates.
#
# `--disallowedTools` matches tool names EXACTLY; an unknown name is a harmless
# no-op. `KillBash` was renamed `KillShell` in Claude Code v2, so BOTH are listed
# to keep the rule correct across CLI versions (v2.1.209 still ships both names).
_TOOL_POLICY_FLAGS: frozenset[str] = frozenset(
    {
        "--disallowedTools",
        "--disallowed-tools",
        "--allowedTools",
        "--allowed-tools",
        "--tools",
    }
)
_DISALLOWED_TOOLS_FLAG = "--disallowedTools"
# Convenience set an agent MAY opt into (not applied by default) — the tools that
# are meaningless/unobservable under headless `claude -p`. Copy into an agent's
# `disallowed_tools` to silence the background-shell trap.
RECOMMENDED_HEADLESS_DISALLOWED_TOOLS: tuple[str, ...] = (
    "BashOutput",
    "KillShell",
    "KillBash",
)

# --- Capture artifact filenames (named, not magic literals; FR-4/FR-5) --------
TRANSCRIPT_FILE = "transcript.jsonl"
STDOUT_FILE = "stdout.txt"
STDERR_FILE = "stderr.txt"
RESULT_FILE = "result.json"

# Cap per-block text written into the *human-readable* stdout.txt render so a
# single huge tool result doesn't make it unreadable.  The full, untruncated
# content always remains in transcript.jsonl — no data is lost.
_RENDER_TRUNCATE_CHARS = 2000

# *** SINGLE SOURCE OF TRUTH for Claude usage-quota exhaustion detection ***
# Edit ONLY this pattern when the exact quota message changes.
# Matches: "You've hit your daily limit", "You've hit your hourly limit",
#          "You hit your weekly limit", "you've hit your monthly limit", etc.
# Group (?:'ve?)? makes the contraction fully optional so both "you've hit"
# and "you hit" are covered.
_CLAUDE_QUOTA_PATTERN: re.Pattern[str] = re.compile(
    r"you(?:'ve?)?\s+hit\s+your\s+\w+\s+limit", re.IGNORECASE
)


def _selected_output_format(argv: list[str]) -> str | None:
    """Return the value of ``--output-format`` in argv (two-arg or =form), or None."""
    for i, arg in enumerate(argv):
        if arg == _OUTPUT_FORMAT_FLAG:
            return argv[i + 1] if i + 1 < len(argv) else None
        if arg.startswith(f"{_OUTPUT_FORMAT_FLAG}="):
            return arg.split("=", 1)[1]
    return None


def _ensure_stream_capture_flags(argv: list[str]) -> list[str]:
    """Return argv set up to emit a per-turn JSONL stream we can capture in full.

    - Appends ``--output-format stream-json`` unless the caller already set an
      ``--output-format`` (any value is honoured — caller override wins).
    - Ensures ``--verbose`` is present whenever the effective output format is
      ``stream-json`` (the CLI requires it under ``--print``).

    Idempotent and non-mutating (returns a new list).
    """
    result = list(argv)
    if _selected_output_format(result) is None:
        result += [_OUTPUT_FORMAT_FLAG, _OUTPUT_FORMAT_VALUE]
    if _selected_output_format(result) == _OUTPUT_FORMAT_VALUE and _VERBOSE_FLAG not in result:
        result += [_VERBOSE_FLAG]
    return result


def _has_tool_policy(argv: list[str]) -> bool:
    """True if argv already carries a tool-selection flag (either spelling / ``=`` form)."""
    return any(arg.split("=", 1)[0] in _TOOL_POLICY_FLAGS for arg in argv)


def _ensure_disallowed_tools(argv: list[str], tools: tuple[str, ...]) -> list[str]:
    """Append ``--disallowedTools <tools>`` for an agent's opt-in disable list.

    Default posture is allow-all: with an empty ``tools`` (the AgentSpec default)
    this is a no-op. When ``tools`` is non-empty we inject the flag UNLESS argv
    already sets its own policy (``--disallowedTools`` / ``--allowedTools`` /
    ``--tools``, either spelling) — an explicit command_template/extra_args flag
    wins, so the agent keeps full control. Non-mutating; returns a new list.

    ``--disallowedTools`` is variadic (``<tools...>``), so this MUST be injected
    before a terminating ``--flag`` (the stream-capture flags do this) or the CLI
    parser keeps consuming following tokens as tool names.
    """
    if not tools or _has_tool_policy(argv):
        return list(argv)
    return list(argv) + [_DISALLOWED_TOOLS_FLAG, *tools]


def _parse_iso_to_epoch(value: str) -> float | None:
    """Parse an ISO 8601 datetime string to a Unix epoch float.  Returns None on error."""
    try:
        # Python 3.7+ fromisoformat doesn't handle trailing 'Z'; replace it.
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.timestamp()
    except (ValueError, TypeError):
        return None


def parse_transcript_events(text: str) -> list[dict]:
    """Parse a captured stdout stream into a list of event dicts.

    Handles both the JSONL stream (``--output-format stream-json``: one JSON
    object per line) and a single JSON object (``--output-format json`` or any
    caller that emits one blob).  Lines that are blank or not valid JSON objects
    are skipped — parsing is best-effort and never raises on malformed input.
    """
    text = text.strip()
    if not text:
        return []
    # Fast path: the whole payload is a single JSON object (legacy json format).
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return [obj]
    except (json.JSONDecodeError, ValueError):
        pass
    # JSONL path: one event per line.
    events: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(obj, dict):
            events.append(obj)
    return events


def extract_result_event(text: str) -> dict | None:
    """Return the terminal ``result`` event from a captured stream, or None.

    Prefers an event with ``type == "result"`` (the CLI's final summary event
    carrying aggregate ``usage`` and the final text).  Falls back to the last
    dict seen when no explicit result event is present (e.g. legacy json blob
    or a crash before the result event was emitted).
    """
    events = parse_transcript_events(text)
    if not events:
        return None
    for event in reversed(events):
        if event.get("type") == "result":
            return event
    return events[-1]


def _truncate(value: str, limit: int = _RENDER_TRUNCATE_CHARS) -> str:
    """Truncate ``value`` to ``limit`` chars with an explicit elision marker."""
    if len(value) <= limit:
        return value
    return value[:limit] + f"… [+{len(value) - limit} chars, see {TRANSCRIPT_FILE}]"


def _stringify_block_content(content: object) -> str:
    """Best-effort flatten of a message-block ``content`` field to text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and "text" in item:
                parts.append(str(item.get("text", "")))
            else:
                parts.append(json.dumps(item, ensure_ascii=False))
        return "\n".join(parts)
    return json.dumps(content, ensure_ascii=False)


def render_transcript(events: list[dict]) -> str:
    """Render event dicts into a human-readable, greppable transcript.

    Best-effort: unknown event/block shapes are summarised as compact JSON and
    never cause an exception.  The authoritative, untruncated record stays in
    ``transcript.jsonl``; this is the convenience view.
    """
    lines: list[str] = []
    for event in events:
        etype = event.get("type")
        if etype == "system":
            subtype = event.get("subtype", "")
            model = event.get("model", "")
            lines.append(f"[system] {subtype} {model}".rstrip())
        elif etype in ("assistant", "user"):
            role = etype
            message = event.get("message", {})
            content = message.get("content", []) if isinstance(message, dict) else []
            if not isinstance(content, list):
                content = [content]
            for block in content:
                if not isinstance(block, dict):
                    lines.append(f"[{role}] {_truncate(str(block))}")
                    continue
                btype = block.get("type")
                if btype == "text":
                    lines.append(f"[{role}] {_truncate(str(block.get('text', '')))}")
                elif btype == "thinking":
                    lines.append(f"[thinking] {_truncate(str(block.get('thinking', '')))}")
                elif btype == "tool_use":
                    tool = block.get("name", "?")
                    tool_input = json.dumps(block.get("input", {}), ensure_ascii=False)
                    lines.append(f"[tool_use] {tool} {_truncate(tool_input)}")
                elif btype == "tool_result":
                    body = _stringify_block_content(block.get("content", ""))
                    is_err = block.get("is_error")
                    tag = "tool_error" if is_err else "tool_result"
                    lines.append(f"[{tag}] {_truncate(body)}")
                else:
                    lines.append(f"[{role}] {_truncate(json.dumps(block, ensure_ascii=False))}")
        elif etype == "result":
            final = event.get("result", "")
            if final:
                lines.append(f"[result] {_truncate(str(final))}")
    return "\n".join(lines) + ("\n" if lines else "")


def parse_usage_and_429(
    stdout: str,
    stderr: str,
    returncode: int,
    now_epoch: float,  # noqa: ARG001 — reserved for future relative retry_after
) -> dict:
    """Parse token usage and rate-limit/quota signals from a ``claude`` CLI run.

    Pure function — no side effects, fully testable in isolation.  Accepts both
    the JSONL stream (stream-json) and a single JSON object (legacy json); the
    terminal ``result`` event / object is used for usage and error extraction.

    Parameters
    ----------
    stdout:
        Raw stdout text (JSONL stream or single JSON object).
    stderr:
        Raw stderr text from the subprocess.
    returncode:
        Process exit code.
    now_epoch:
        Current time as Unix epoch float (injected for determinism in tests).

    Returns
    -------
    dict with keys:
        actuals_available, provider_rate_limited, provider_retry_after_epoch,
        claude_quota_exhausted,
        input_tokens, output_tokens, cache_creation_input_tokens,
        cache_read_input_tokens, cost_usd
    """
    result: dict = {
        "actuals_available": False,
        "provider_rate_limited": False,
        "provider_retry_after_epoch": None,
        "claude_quota_exhausted": False,
        "input_tokens": None,
        "output_tokens": None,
        "cache_creation_input_tokens": None,
        "cache_read_input_tokens": None,
        "cost_usd": None,
    }

    combined_text = stdout + "\n" + stderr

    # --- Claude usage-quota exhaustion (checked first, distinct from provider 429) ---
    # Pattern lives in _CLAUDE_QUOTA_PATTERN — edit only there.
    if _CLAUDE_QUOTA_PATTERN.search(combined_text):
        result["claude_quota_exhausted"] = True
        # Quota exhaustion is categorically different from a provider rate limit;
        # don't attempt JSON parse or 429 detection for this case.
        return result

    # --- 429 detection from process-level signals (no JSON needed) ---
    combined_lower = combined_text.lower()
    if returncode != 0 and (
        "429" in combined_lower or "rate_limit" in combined_lower or "rate limit" in combined_lower
    ):
        result["provider_rate_limited"] = True
        # Still attempt to extract retry_after from the structured payload below.

    # --- Terminal result event / object ---
    parsed = extract_result_event(stdout)
    if not isinstance(parsed, dict):
        return result

    # --- Usage extraction ---
    usage = parsed.get("usage")
    if isinstance(usage, dict):
        in_tok = usage.get("input_tokens")
        out_tok = usage.get("output_tokens")
        cache_create = usage.get("cache_creation_input_tokens")
        cache_read = usage.get("cache_read_input_tokens")

        if in_tok is not None or out_tok is not None:
            result["actuals_available"] = True
            result["input_tokens"] = in_tok
            result["output_tokens"] = out_tok
            result["cache_creation_input_tokens"] = cache_create
            result["cache_read_input_tokens"] = cache_read

    # --- Cost extraction: top-level `total_cost_usd`, sibling of `usage` (confirmed
    # field name, not `cost_usd` — see tests/fixtures/claude_usage.json / E-9h3m7k FR-1) ---
    cost = parsed.get("total_cost_usd")
    if isinstance(cost, (int, float)):
        result["cost_usd"] = float(cost)

    # --- 429 detection from JSON payload ---
    error_obj = parsed.get("error")
    if isinstance(error_obj, dict):
        err_type = str(error_obj.get("type", "")).lower()
        err_msg = str(error_obj.get("message", "")).lower()
        if "rate_limit" in err_type or ("rate" in err_msg and "limit" in err_msg):
            result["provider_rate_limited"] = True

        # Retry-after from JSON error: prefer retry_after (epoch float), fallback reset_at (ISO)
        retry_after_raw = error_obj.get("retry_after")
        if retry_after_raw is not None:
            try:
                result["provider_retry_after_epoch"] = float(retry_after_raw)
            except (ValueError, TypeError):
                pass

        if result["provider_retry_after_epoch"] is None:
            reset_at = error_obj.get("reset_at")
            if isinstance(reset_at, str):
                result["provider_retry_after_epoch"] = _parse_iso_to_epoch(reset_at)

    return result


def _write_derived_capture(output_dir: str, transcript_text: str) -> None:
    """Derive and write ``result.json`` + ``stdout.txt`` from the raw transcript.

    ``transcript.jsonl`` and ``stderr.txt`` are written live by the child process
    (OS-level redirect); this fills in the two derived, human-facing artifacts
    after the run completes.  Best-effort — a parse failure still yields an empty
    stdout.txt rather than raising.
    """
    events = parse_transcript_events(transcript_text)
    Path(os.path.join(output_dir, STDOUT_FILE)).write_text(
        render_transcript(events), encoding="utf-8"
    )
    result_event = extract_result_event(transcript_text)
    if result_event is not None:
        Path(os.path.join(output_dir, RESULT_FILE)).write_text(
            json.dumps(result_event, ensure_ascii=False, indent=2), encoding="utf-8"
        )


class ClaudeCliExecutor(Executor):
    """Executes tasks by spawning a `claude` CLI subprocess.

    The prompt is assembled from paths only (NFR-1 invariant):
    - instruction_path: path to the instruction markdown file
    - input_paths: paths to input artifacts
    - output_paths: paths the agent should write outputs to
    - repo_paths: id=path mappings for repos in the RepoSet

    Output from every turn is streamed live to ``transcript.jsonl`` under
    ``ctx.output_dir`` (see module docstring), with a human-readable
    ``stdout.txt`` render, raw ``stderr.txt``, and the terminal ``result.json``.
    The result carries ``output_artifact_path`` pointing to that directory
    (FR-5).  Token usage is extracted from the terminal ``result`` event.
    """

    def execute(self, ctx: TaskContext) -> TaskResult:
        prompt = build_prompt(ctx)
        argv = [
            (arg.replace("{prompt}", prompt) if "{prompt}" in arg else arg)
            for arg in ctx.agent.command_template
        ] + ctx.agent.extra_args

        # Inject --model if specified in agent spec and not already in argv.
        if ctx.agent.model and "--model" not in argv and "-m" not in argv:
            argv = argv + ["--model", ctx.agent.model]

        # Inject --max-turns unless the command already sets it. Explicit
        # agent.max_turns wins; otherwise derive from effort. (agent.max_turns is
        # the escape hatch a run-level --max-turns override sets on every agent.)
        if "--max-turns" not in argv:
            max_turns: int | None = None
            if ctx.agent.max_turns is not None:
                max_turns = ctx.agent.max_turns
            elif ctx.agent.effort:
                from ..models import EFFORT_MAX_TURNS

                max_turns = EFFORT_MAX_TURNS[ctx.agent.effort]
            if max_turns is not None:
                argv = argv + ["--max-turns", str(max_turns)]

        # Apply the agent's opt-in tool-disable list (default: none → allow all)
        # BEFORE the stream flags, so the variadic --disallowedTools list is
        # terminated by --output-format rather than swallowing it.
        argv = _ensure_disallowed_tools(argv, tuple(ctx.agent.disallowed_tools))

        # Emit a per-turn JSONL stream so all turns are captured (not just the last).
        argv = _ensure_stream_capture_flags(argv)

        os.makedirs(ctx.output_dir, exist_ok=True)
        transcript_path = os.path.join(ctx.output_dir, TRANSCRIPT_FILE)
        stderr_path = os.path.join(ctx.output_dir, STDERR_FILE)

        timed_out = False
        # Redirect the child's stdout/stderr straight to files (OS-level): the
        # full per-turn stream lands on disk live, so a long run that later times
        # out or crashes still leaves a partial transcript instead of nothing.
        with (
            open(transcript_path, "w", encoding="utf-8") as tf,
            open(stderr_path, "w", encoding="utf-8") as ef,
        ):
            proc = subprocess.Popen(
                argv,
                cwd=ctx.cwd or None,
                stdin=subprocess.DEVNULL,
                stdout=tf,
                stderr=ef,
            )
            try:
                proc.wait(timeout=ctx.timeout_seconds)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
                timed_out = True

        returncode = proc.returncode
        transcript_text = Path(transcript_path).read_text(encoding="utf-8", errors="replace")
        stderr_text = Path(stderr_path).read_text(encoding="utf-8", errors="replace")

        # Derive the human-readable stdout.txt + result.json from the captured stream.
        _write_derived_capture(ctx.output_dir, transcript_text)

        usage_info = parse_usage_and_429(
            stdout=transcript_text,
            stderr=stderr_text,
            returncode=returncode,
            now_epoch=time.time(),
        )
        token_fields = {
            "input_tokens": usage_info["input_tokens"],
            "output_tokens": usage_info["output_tokens"],
            "cache_creation_input_tokens": usage_info["cache_creation_input_tokens"],
            "cache_read_input_tokens": usage_info["cache_read_input_tokens"],
            "cost_usd": usage_info["cost_usd"],
            "actuals_available": usage_info["actuals_available"],
            "provider_rate_limited": usage_info["provider_rate_limited"],
            "provider_retry_after_epoch": usage_info["provider_retry_after_epoch"],
            "claude_quota_exhausted": usage_info["claude_quota_exhausted"],
        }

        if timed_out:
            return TaskResult(
                task_id=ctx.task_id,
                status="timed_out",
                attempts=1,
                error="timeout",
                output_artifact_path=ctx.output_dir,
                **token_fields,
            )

        if returncode == 0:
            return TaskResult(
                task_id=ctx.task_id,
                status="succeeded",
                attempts=1,
                exit_code=0,
                output_artifact_path=ctx.output_dir,
                **token_fields,
            )

        err_tail = (stderr_text or transcript_text or "")[-500:]
        return TaskResult(
            task_id=ctx.task_id,
            status="failed",
            attempts=1,
            exit_code=returncode,
            error=err_tail,
            output_artifact_path=ctx.output_dir,
            **token_fields,
        )
