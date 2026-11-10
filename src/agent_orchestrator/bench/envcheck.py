"""Environment isolation for the agent under test (T-Ws7Iso; HLD §2b, §8, §12).

The parity bundle (`bench/parity.py`) controls what the harness *ships* to every subject.
This module checks what the agent actually *got*, because Claude Code also loads context
the harness never wrote:

1. **Instruction ancestors** -- `find_instruction_ancestors(root)`. Claude Code loads
   `CLAUDE.md` / `CLAUDE.local.md` / `AGENTS.md` / `.claude/` from every ancestor of an
   agent's cwd, so a workspace root inside a checkout leaks that checkout's instructions,
   skills and agents into every subject (the 2026-10-23 finding). `runner.run_suite`
   refuses such a root for agent subjects unless explicitly allowed (then the run is
   stamped `env_contaminated`).
2. **Environment fingerprint** -- `fingerprint_transcripts(...)`. The `system/init` event at
   the top of every stream-json transcript lists what the session loaded (skills, agents,
   slash commands, MCP servers, plugins). It is recorded per task, and
   `results.build_comparison` compares it across subjects and against a per-run baseline
   taken by `probe_env_baseline` (a `claude -p` started in a parity-applied empty
   workspace and killed as soon as it prints its init event -- the dynamic allow-list of
   Claude Code built-ins + user-level config).
3. **User-level config** -- `user_config_fingerprint(env)`. What `~/.claude` (or
   `$CLAUDE_CONFIG_DIR`) contributes that the init event does not show: selected
   `settings.json` keys (`model`, `effortLevel`, `enabledPlugins`, hooks, ...), the user
   `CLAUDE.md`, and the Claude-Code env knobs. Decision (HLD §12, T-Ws7Iso AC4): the bench
   does NOT relocate `CLAUDE_CONFIG_DIR` (that would fork the operator's OAuth credentials);
   it records these instead, and the default parity bundle neutralises the two account-level
   leaks via env (claude.ai connectors, auto-memory).
4. **Hidden-asset audit** -- `audit_grading_access(...)`. A `tool_use` whose input names the
   grading root marks the task suspect (`suspect_reason="grading_access"`); such tasks are
   excluded from capability statistics.

`subject_env` is the single place a subject's child environment is built: the operator's
environment minus the variables that describe the operator's OWN Claude Code session (when
`ao-bench` is launched from inside Claude Code), plus the parity env knobs.

Everything here is best-effort and never raises on malformed transcripts. Import direction:
reads only from sibling `bench/` modules and core's transcript filename constant.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import subprocess
import threading
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from ..executors.claude_cli import TRANSCRIPT_FILE

# ---------------------------------------------------------------------------
# Named constants
# ---------------------------------------------------------------------------

# Files/dirs Claude Code picks up from an ancestor of its cwd (project memory, local memory,
# the cross-tool AGENTS.md, and the project `.claude/` tree with skills/agents/commands).
INSTRUCTION_MARKERS: tuple[str, ...] = ("CLAUDE.md", "CLAUDE.local.md", "AGENTS.md", ".claude")

ENV_CLAUDE_CONFIG_DIR = "CLAUDE_CONFIG_DIR"
DEFAULT_USER_CONFIG_DIRNAME = ".claude"
USER_SETTINGS_FILENAME = "settings.json"
USER_CLAUDE_MD_FILENAME = "CLAUDE.md"

# Variables Claude Code sets for ITS OWN session; inherited by a subject they would make the
# agent under test believe it is a child of the operator's session (CLAUDE_CODE_CHILD_SESSION
# changes session behaviour) or connect it to the operator's messaging socket.
OPERATOR_SESSION_ENV_VARS: frozenset[str] = frozenset(
    {
        "CLAUDECODE",
        "CLAUDE_CODE_ENTRYPOINT",
        "CLAUDE_CODE_CHILD_SESSION",
        "CLAUDE_CODE_EXECPATH",
        "CLAUDE_CODE_SSE_PORT",
        "CLAUDE_CODE_INVOKED_SKILLS",
        "CLAUDE_PID",
        "CLAUDE_EFFORT",
    }
)
OPERATOR_SESSION_ENV_PREFIXES: tuple[str, ...] = (
    "CLAUDE_CODE_SESSION",
    "CLAUDE_CODE_MESSAGING",
    "CLAUDE_CODE_BRIDGE",
)

# init-event fields that make up the compared fingerprint (sets of names).
FINGERPRINT_FIELDS: tuple[str, ...] = (
    "skills",
    "agents",
    "slash_commands",
    "mcp_servers",
    "plugins",
)
_INIT_EVENT_TYPE = "system"
_INIT_EVENT_SUBTYPE = "init"

# User-level settings keys that change agent behaviour for every subject. Scalars are recorded
# verbatim; structured values (which may embed secrets or be large) as a sha256.
USER_SETTINGS_SCALAR_KEYS: tuple[str, ...] = (
    "model",
    "effortLevel",
    "alwaysThinkingEnabled",
    "outputStyle",
    "autoMemoryEnabled",
    "disableClaudeAiConnectors",
)
USER_SETTINGS_DIGEST_KEYS: tuple[str, ...] = ("enabledPlugins", "hooks", "env", "permissions")
# Process env recorded in the user-config fingerprint (after `subject_env` scrubbing).
CLAUDE_ENV_PREFIXES: tuple[str, ...] = (
    "ANTHROPIC_",
    "CLAUDE_",
    "ENABLE_CLAUDEAI_",
    "MAX_THINKING_TOKENS",
)
# A recorded env var whose name contains one of these is stored as "<set>", never its value.
_SECRET_NAME_MARKERS: tuple[str, ...] = ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL", "AUTH")
_SECRET_PLACEHOLDER = "<set>"

# Baseline probe: a throwaway `claude -p` killed as soon as its init event arrives.
PROBE_PROMPT = "Reply with the single word ok."
PROBE_TIMEOUT_SECONDS = 60
PROBE_DIRNAME = "_env-probe"

# Hidden-asset audit.
SUSPECT_REASON_GRADING_ACCESS = "grading_access"
MAX_AUDIT_HITS = 20
AUDIT_EXCERPT_CHARS = 200


# ---------------------------------------------------------------------------
# Models (persisted in run.json via runner.BenchRunRecord / BenchTaskRecord)
# ---------------------------------------------------------------------------


class EnvIsolation(BaseModel):
    """Where a run's workspaces and grading assets lived, and what leaked into them."""

    workspace_root: str
    grading_root: str
    # Instruction files/dirs found in the workspace root or any ancestor (empty = clean).
    instruction_ancestors: list[str] = []
    # True when the operator ran anyway with --allow-contaminated-env.
    allowed_by_flag: bool = False

    @property
    def contaminated(self) -> bool:
        return bool(self.instruction_ancestors)


class EnvFingerprint(BaseModel):
    """Union of what one or more Claude Code sessions loaded (from their init events)."""

    skills: list[str] = []
    agents: list[str] = []
    slash_commands: list[str] = []
    mcp_servers: list[str] = []
    plugins: list[str] = []
    init_events: int = 0
    # The merged sessions did not all load the same sets.
    divergent: bool = False
    claude_code_versions: list[str] = []
    # Some session had auto-memory enabled (init `memory_paths` set).
    auto_memory: bool = False
    digest: str = ""

    def sets(self) -> dict[str, set[str]]:
        return {f: set(getattr(self, f)) for f in FINGERPRINT_FIELDS}


class UserConfigFingerprint(BaseModel):
    """User-level Claude Code config shared by every subject (T-Ws7Iso AC4)."""

    config_dir: str
    settings: dict[str, Any] = {}
    user_claude_md_sha256: str | None = None
    env: dict[str, str] = {}
    digest: str = ""


class GradingAudit(BaseModel):
    """Result of scanning a task's transcripts for references to the grading root."""

    transcripts_scanned: int = 0
    # `<tool name>: <input excerpt>` for each tool_use whose input names the grading root.
    tool_use_hits: list[str] = []
    # tool_result blocks whose output named the grading root (exposure, not access).
    tool_result_mentions: int = 0

    @property
    def accessed(self) -> bool:
        return bool(self.tool_use_hits)


# ---------------------------------------------------------------------------
# 1. Instruction ancestors
# ---------------------------------------------------------------------------


def user_config_dir(env: Mapping[str, str] | None = None) -> Path:
    """`$CLAUDE_CONFIG_DIR` or `~/.claude` -- the user-level config Claude Code reads."""
    source = os.environ if env is None else env
    override = source.get(ENV_CLAUDE_CONFIG_DIR)
    return Path(override).expanduser() if override else Path.home() / DEFAULT_USER_CONFIG_DIRNAME


def find_instruction_ancestors(root: Path, *, config_dir: Path | None = None) -> list[str]:
    """Instruction files/dirs in *root* or any ancestor of it, as absolute path strings.

    The user config dir (`~/.claude` or `$CLAUDE_CONFIG_DIR`) is exempt: Claude Code loads it
    for every session regardless of cwd, and `user_config_fingerprint` records it instead.
    *root* need not exist yet.
    """
    cfg = (config_dir or user_config_dir()).resolve()
    resolved = root.resolve()
    hits: list[str] = []
    for directory in (resolved, *resolved.parents):
        for marker in INSTRUCTION_MARKERS:
            candidate = directory / marker
            try:
                if not candidate.exists():
                    continue
                if marker == DEFAULT_USER_CONFIG_DIRNAME and candidate.resolve() == cfg:
                    continue
            except OSError:
                continue  # unreadable ancestor: nothing Claude Code could load either
            hits.append(str(candidate))
    return hits


def check_isolation(
    workspace_root: Path, grading_root: Path, *, config_dir: Path | None = None
) -> EnvIsolation:
    return EnvIsolation(
        workspace_root=str(workspace_root),
        grading_root=str(grading_root),
        instruction_ancestors=find_instruction_ancestors(workspace_root, config_dir=config_dir),
    )


# ---------------------------------------------------------------------------
# Subject environment
# ---------------------------------------------------------------------------


def _is_operator_session_var(name: str) -> bool:
    return name in OPERATOR_SESSION_ENV_VARS or name.startswith(OPERATOR_SESSION_ENV_PREFIXES)


def subject_env(
    overrides: Mapping[str, str] | None = None, *, base: Mapping[str, str] | None = None
) -> dict[str, str]:
    """Child environment for a subject: *base* (default `os.environ`) minus the operator's
    own Claude Code session variables, then *overrides* (parity env knobs) on top."""
    source = os.environ if base is None else base
    env = {k: v for k, v in source.items() if not _is_operator_session_var(k)}
    env.update(overrides or {})
    return env


# ---------------------------------------------------------------------------
# 2. Environment fingerprint
# ---------------------------------------------------------------------------


def _sha256_json(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def _names(value: Any) -> set[str]:
    """Names out of an init-event list: plain strings, or dicts (plugins: `source` is more
    specific than `name`; MCP servers: `name` only -- their `status` is a connection race)."""
    out: set[str] = set()
    if not isinstance(value, list):
        return out
    for item in value:
        if isinstance(item, str):
            out.add(item)
        elif isinstance(item, dict):
            name = item.get("source") or item.get("name")
            if isinstance(name, str):
                out.add(name)
    return out


def _loads(line: str) -> dict[str, Any] | None:
    line = line.strip()
    if not line.startswith("{"):
        return None
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def _is_init(event: dict[str, Any]) -> bool:
    return event.get("type") == _INIT_EVENT_TYPE and event.get("subtype") == _INIT_EVENT_SUBTYPE


def _iter_events(path: Path) -> Iterable[dict[str, Any]]:
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                event = _loads(line)
                if event is not None:
                    yield event
    except OSError:
        return


def init_events(path: Path) -> list[dict[str, Any]]:
    """Every `system/init` event in a stream-json transcript (one per session)."""
    return [e for e in _iter_events(path) if _is_init(e)]


def _build(
    per_session: list[dict[str, set[str]]], versions: set[str], auto_memory: bool, divergent: bool
) -> EnvFingerprint:
    union = {f: set().union(*(s[f] for s in per_session)) for f in FINGERPRINT_FIELDS}
    sorted_sets = {f: sorted(v) for f, v in union.items()}
    return EnvFingerprint(
        **sorted_sets,
        init_events=len(per_session),
        divergent=divergent or any(s != per_session[0] for s in per_session[1:]),
        claude_code_versions=sorted(versions),
        auto_memory=auto_memory,
        digest=_sha256_json(sorted_sets),
    )


def fingerprint_events(events: list[dict[str, Any]]) -> EnvFingerprint | None:
    """Fingerprint a list of init events; `None` when there are none."""
    if not events:
        return None
    per_session = [{f: _names(e.get(f)) for f in FINGERPRINT_FIELDS} for e in events]
    versions = {v for e in events if isinstance(v := e.get("claude_code_version"), str)}
    auto_memory = any(e.get("memory_paths") for e in events)
    return _build(per_session, versions, auto_memory, divergent=False)


def merge_fingerprints(parts: Iterable[EnvFingerprint | None]) -> EnvFingerprint | None:
    """Union of several fingerprints (a task's transcripts, a run's tasks); `None` if none."""
    present = [p for p in parts if p is not None and p.init_events > 0]
    if not present:
        return None
    merged = _build(
        [p.sets() for p in present],
        {v for p in present for v in p.claude_code_versions},
        any(p.auto_memory for p in present),
        divergent=any(p.divergent for p in present),
    )
    # `_build` counted one "session" per part; report the real number of init events.
    return merged.model_copy(update={"init_events": sum(p.init_events for p in present)})


def find_transcripts(workspace: Path) -> list[Path]:
    """Every stream-json transcript a subject left under its workspace: a bare `claude -p`
    writes `capture/transcript.jsonl`; an `ao` run writes one per agent attempt under
    `.orchestrator/runs/<run>/<task>/attempt-<n>/transcript.jsonl`."""
    found: set[Path] = set()
    capture = workspace / "capture" / TRANSCRIPT_FILE
    if capture.is_file():
        found.add(capture)
    runs = workspace / ".orchestrator" / "runs"
    if runs.is_dir():
        found.update(p for p in runs.rglob(TRANSCRIPT_FILE) if p.is_file())
    return sorted(found)


def fingerprint_transcripts(transcripts: Iterable[Path]) -> EnvFingerprint | None:
    return fingerprint_events([e for t in transcripts for e in init_events(t)])


def foreign_entries(
    observed: EnvFingerprint | None, baseline: EnvFingerprint | None
) -> dict[str, list[str]]:
    """Entries a subject saw that the baseline (built-ins + user config + bundle) did not."""
    if observed is None or baseline is None:
        return {}
    base = baseline.sets()
    out: dict[str, list[str]] = {}
    for field, names in observed.sets().items():
        extra = sorted(names - base[field])
        if extra:
            out[field] = extra
    return out


# ---------------------------------------------------------------------------
# 3. User-level config
# ---------------------------------------------------------------------------


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _is_secret_name(name: str) -> bool:
    upper = name.upper()
    return any(marker in upper for marker in _SECRET_NAME_MARKERS)


def user_config_fingerprint(env: Mapping[str, str]) -> UserConfigFingerprint:
    """Fingerprint the user-level Claude Code config + env a subject will run under.

    *env* is the subject environment (`subject_env(...)`), so parity knobs and the
    operator-session scrub are reflected. The config dir path itself is not hashed (two
    operators' identical settings must compare equal).
    """
    cfg = user_config_dir(env)
    raw = _read_json(cfg / USER_SETTINGS_FILENAME)
    settings: dict[str, Any] = {k: raw[k] for k in USER_SETTINGS_SCALAR_KEYS if k in raw}
    for key in USER_SETTINGS_DIGEST_KEYS:
        if key in raw:
            settings[f"{key}_sha256"] = _sha256_json(raw[key])
    claude_md = cfg / USER_CLAUDE_MD_FILENAME
    md_sha: str | None = None
    try:
        if claude_md.is_file():
            md_sha = hashlib.sha256(claude_md.read_bytes()).hexdigest()
    except OSError:
        md_sha = None
    recorded_env = {
        k: (_SECRET_PLACEHOLDER if _is_secret_name(k) else v)
        for k, v in sorted(env.items())
        if k.startswith(CLAUDE_ENV_PREFIXES) and k != ENV_CLAUDE_CONFIG_DIR
    }
    digest = _sha256_json({"settings": settings, "claude_md": md_sha, "env": recorded_env})
    return UserConfigFingerprint(
        config_dir=str(cfg),
        settings=settings,
        user_claude_md_sha256=md_sha,
        env=recorded_env,
        digest=digest,
    )


# ---------------------------------------------------------------------------
# Baseline probe
# ---------------------------------------------------------------------------


def probe_env_baseline(
    *,
    cwd: Path,
    env: Mapping[str, str],
    model: str | None = None,
    permission_mode: str | None = None,
    timeout_seconds: int = PROBE_TIMEOUT_SECONDS,
) -> EnvFingerprint | None:
    """Start `claude -p` in *cwd*, read its init event, kill it. `None` if `claude` is not on
    PATH, fails to start, or prints no init event within *timeout_seconds*.

    The init event is emitted at session start, before the model answers, so the process is
    killed (whole group) within about a second; at most one tiny request is in flight.
    """
    if shutil.which("claude") is None:
        return None
    argv = [
        "claude",
        "-p",
        PROBE_PROMPT,
        "--output-format",
        "stream-json",
        "--verbose",
        "--max-turns",
        "1",
    ]
    if model:
        argv += ["--model", model]
    if permission_mode:
        argv += ["--permission-mode", permission_mode]
    try:
        proc = subprocess.Popen(  # noqa: S603 -- fixed argv, no shell
            argv,
            cwd=str(cwd),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=dict(env),
            text=True,
            start_new_session=True,
        )
    except OSError:
        return None
    found: list[dict[str, Any]] = []

    def _read() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            event = _loads(line)
            if event is not None and _is_init(event):
                found.append(event)
                return

    reader = threading.Thread(target=_read, daemon=True)
    reader.start()
    try:
        reader.join(timeout_seconds)
    finally:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        proc.wait()
    return fingerprint_events(found)


# ---------------------------------------------------------------------------
# 4. Hidden-asset audit
# ---------------------------------------------------------------------------


def _blocks(event: dict[str, Any]) -> list[dict[str, Any]]:
    message = event.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    return [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []


def _tool_result_text(block: dict[str, Any]) -> str:
    content = block.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            str(c.get("text", "")) for c in content if isinstance(c, dict) and "text" in c
        )
    return ""


def audit_grading_access(transcripts: Iterable[Path], needles: Iterable[str]) -> GradingAudit:
    """Scan *transcripts* for `tool_use` inputs (access) and `tool_result` outputs (exposure)
    that mention any of *needles* (the grading root path / its distinctive dir name)."""
    needle_list = [n for n in needles if n]
    audit = GradingAudit()
    for path in transcripts:
        audit.transcripts_scanned += 1
        for event in _iter_events(path):
            for block in _blocks(event):
                kind = block.get("type")
                if kind == "tool_use":
                    text = json.dumps(block.get("input"), ensure_ascii=False, default=str)
                    if any(n in text for n in needle_list):
                        if len(audit.tool_use_hits) < MAX_AUDIT_HITS:
                            name = block.get("name", "?")
                            audit.tool_use_hits.append(f"{name}: {text[:AUDIT_EXCERPT_CHARS]}")
                elif kind == "tool_result":
                    if any(n in _tool_result_text(block) for n in needle_list):
                        audit.tool_result_mentions += 1
    return audit


__all__ = [
    "FINGERPRINT_FIELDS",
    "INSTRUCTION_MARKERS",
    "PROBE_DIRNAME",
    "SUSPECT_REASON_GRADING_ACCESS",
    "EnvFingerprint",
    "EnvIsolation",
    "GradingAudit",
    "UserConfigFingerprint",
    "audit_grading_access",
    "check_isolation",
    "find_instruction_ancestors",
    "find_transcripts",
    "fingerprint_events",
    "fingerprint_transcripts",
    "foreign_entries",
    "init_events",
    "merge_fingerprints",
    "probe_env_baseline",
    "subject_env",
    "user_config_dir",
    "user_config_fingerprint",
]
