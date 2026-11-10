"""Sentinel LLM generations: pack, handoff, memory, rotation, ledger (E-Sn7tL2 T-Sn8Gen).

HLD §8 / ADR-0024. Everything is a pure function of ``RunState`` + files + an injected clock, except
the driver boundary (:mod:`.sentinel_driver`). The main-thread tick calls ``GenerationLane.tick``;
the lane never raises into the engine's run path: the engine's sentinel fail-safe wraps the call.

Directory layout under ``<run_dir>/sentinel/``::

    memory.md                     tool-owned, compacted (<= MEMORY_MAX_BYTES)
    ledger.jsonl                  hash-chained event log (tamper-evident, not tamper-proof)
    sentinel-contract.md          rendered from the schemas below
    gNN/pack/                     deterministic input pack (<= PACK_MAX_BYTES)
    gNN/{handoff.json,report.md,memory-append.md}   written by the generation
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic import ValidationError as PydanticValidationError

from .models import (
    SENTINEL_GEN_TASK_PREFIX,
    RunState,
    SentinelLlmSpec,
    SentinelState,
    TaskRunState,
    is_sentinel_generation_task,
    sentinel_generation_spend_usd,
)
from .sentinel_driver import (
    DriverStatus,
    GenerationLimits,
    Handle,
    SentinelDriver,
)
from .sentinel_powers import PLANNER_CONTRACT_SECTION, TRIGGER_IDLE_SLOTS, backlog_is_empty

SENTINEL_DIR: Final = "sentinel"
MEMORY_FILE: Final = "memory.md"
LEDGER_FILE: Final = "ledger.jsonl"
CONTRACT_FILE: Final = "sentinel-contract.md"
HANDOFF_FILE: Final = "handoff.json"
REPORT_FILE: Final = "report.md"
MEMORY_APPEND_FILE: Final = "memory-append.md"
TRIGGERS_FILE: Final = "triggers.jsonl"
PACK_DIR: Final = "pack"
HANDOFF_SCHEMA_ID: Final = "ao.sentinel.handoff/v1"
PACK_SCHEMA_ID: Final = "ao.sentinel.pack/v1"

PACK_MAX_BYTES: Final = 120 * 1024
MEMORY_MAX_BYTES: Final = 16 * 1024
EVENTS_LAST_K: Final = 40
FILE_CAPS: Final = {
    "README.md": 3 * 1024,
    CONTRACT_FILE: 12 * 1024,
    "charter.json": 16 * 1024,
    "digest.json": 24 * 1024,
    "events.jsonl": 24 * 1024,
    "backlog.json": 8 * 1024,
    "memory.md": MEMORY_MAX_BYTES,
    "previous-handoff.json": 8 * 1024,
    "manifest.json": 4 * 1024,
}
CHARTER_REL: Final = "outputs/charter.json"
BACKLOG_REL: Final = "control/backlog.json"
MEMORY_APPEND_MAX_BYTES: Final = 64 * 1024
MEMORY_APPEND_MAX_LINES: Final = 50
MEMORY_LINE_MAX_CHARS: Final = 500
HANDOFF_MAX_BYTES: Final = 64 * 1024
DIGEST_NOTES: Final = 10
HOUR_SECONDS: Final = 3600
HANDOFF_GRACE_SECONDS: Final = 30
"""A generation that wrote a valid handoff is given this long to exit by itself (so its final
usage/cost is reported) before the tick stops it."""
TRUNC_MARKER: Final = "\n[... truncated by pack assembler: {n} bytes dropped ...]\n"

TRIGGER_HEARTBEAT: Final = "heartbeat"
TRIGGER_HUMAN_NOTE: Final = "human_note"
TRIGGER_UNIT_FAILED: Final = "unit_failed"
TRIGGER_STUCK: Final = "stuck_task"
TRIGGER_ORDER: Final = (
    TRIGGER_STUCK,
    TRIGGER_UNIT_FAILED,
    TRIGGER_HUMAN_NOTE,
    TRIGGER_IDLE_SLOTS,
    TRIGGER_HEARTBEAT,
)

END_TTL: Final = "ttl"
END_MAX_TRIGGERS: Final = "max_triggers"
END_MAX_TOKENS: Final = "max_tokens"
END_HANDOFF: Final = "explicit_handoff"
END_BUDGET: Final = "budget"

EndedReason = Literal["ttl", "max_triggers", "max_tokens", "explicit_handoff", "budget"]


# ---------------------------------------------------------------------------------------------
# Handoff (ao.sentinel.handoff/v1)
# ---------------------------------------------------------------------------------------------


class WatchItem(BaseModel):
    task_id: str
    concern: str
    check_after: str | None = None


class SentinelHandoff(BaseModel):
    """``ao.sentinel.handoff/v1`` (HLD App. A.2). Unknown top-level keys are rejected."""

    model_config = ConfigDict(extra="forbid")

    schema_: Literal["ao.sentinel.handoff/v1"] = Field(alias="schema")
    generation: int = Field(ge=1)
    ended_reason: EndedReason
    summary: str = Field(max_length=4000)
    watch_list: list[WatchItem]
    do_not_repeat: list[str]
    open_directives: list[str]
    proposals_submitted: list[str] = []
    next_suggested_triggers: list[str] = []


HANDOFF_JSON_SCHEMA: Final = {
    "$id": HANDOFF_SCHEMA_ID,
    "type": "object",
    "required": [
        "schema",
        "generation",
        "ended_reason",
        "summary",
        "watch_list",
        "do_not_repeat",
        "open_directives",
    ],
    "additionalProperties": False,
    "properties": {
        "schema": {"const": HANDOFF_SCHEMA_ID},
        "generation": {"type": "integer", "minimum": 1},
        "ended_reason": {
            "enum": [END_TTL, END_MAX_TRIGGERS, END_MAX_TOKENS, END_HANDOFF, END_BUDGET]
        },
        "summary": {"type": "string", "maxLength": 4000},
        "watch_list": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["task_id", "concern"],
                "properties": {
                    "task_id": {"type": "string"},
                    "concern": {"type": "string"},
                    "check_after": {"type": "string", "format": "date-time"},
                },
            },
        },
        "do_not_repeat": {"type": "array", "items": {"type": "string"}},
        "open_directives": {"type": "array", "items": {"type": "string"}},
        "proposals_submitted": {"type": "array", "items": {"type": "string"}},
        "next_suggested_triggers": {"type": "array", "items": {"type": "string"}},
    },
}


@dataclass
class HandoffResult:
    handoff: SentinelHandoff | None
    error: str | None = None


def validate_handoff(path: Path, generation: int) -> HandoffResult:
    """Parse + validate a generation's ``handoff.json``; never raises."""
    try:
        size = path.stat().st_size
    except OSError:
        return HandoffResult(None, "missing")
    if size > HANDOFF_MAX_BYTES:
        return HandoffResult(None, f"too large ({size} bytes)")
    try:
        raw = json.loads(path.read_bytes())
        handoff = SentinelHandoff.model_validate(raw)
    except (OSError, ValueError, PydanticValidationError) as exc:
        return HandoffResult(None, f"invalid: {str(exc)[:200]}")
    if handoff.generation != generation:
        return HandoffResult(None, f"generation {handoff.generation} != {generation}")
    return HandoffResult(handoff)


# ---------------------------------------------------------------------------------------------
# Memory (tool-owned)
# ---------------------------------------------------------------------------------------------

_ITEM_RE: Final = re.compile(r"^- \[g(\d+)\]((?:\[[^\]\n]+\])*) (.*)$")
_TAG_RE: Final = re.compile(r"\[([^\]\n]+)\]")
_EXP_ABS_RE: Final = re.compile(r"^exp:g(\d+)$")
_EXP_REL_RE: Final = re.compile(r"^exp:\+(\d+)$")


@dataclass(frozen=True)
class MemoryItem:
    seq: int
    """Generation that appended the item (the seq tag)."""
    text: str
    pinned: bool = False
    expires: int | None = None
    """Last generation the item is kept for (inclusive); None = no expiry."""

    def render(self) -> str:
        tags = ("[pin]" if self.pinned else "") + (
            f"[exp:g{self.expires}]" if self.expires is not None else ""
        )
        return f"- [g{self.seq:02d}]{tags} {self.text}"


def _norm(text: str) -> str:
    return " ".join(text.casefold().split())


def parse_memory(text: str) -> list[MemoryItem]:
    """Parse ``memory.md`` (tool-written format); unknown lines are ignored."""
    items: list[MemoryItem] = []
    for line in text.splitlines():
        m = _ITEM_RE.match(line.rstrip())
        if m is None:
            continue
        pinned, expires = False, None
        for tag in _TAG_RE.findall(m.group(2)):
            if tag == "pin":
                pinned = True
            elif (em := _EXP_ABS_RE.match(tag)) is not None:
                expires = int(em.group(1))
        items.append(MemoryItem(int(m.group(1)), m.group(3), pinned, expires))
    return items


def parse_memory_append(text: str, generation: int) -> list[MemoryItem]:
    """Items from a generation's ``memory-append.md`` (``- [pin][exp:+3] text`` bullets).

    Bounded: at most ``MEMORY_APPEND_MAX_LINES`` bullets of ``MEMORY_LINE_MAX_CHARS`` chars.
    """
    items: list[MemoryItem] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith(("- ", "* ")):
            continue
        body = stripped[2:].strip()
        pinned, expires = False, None
        while (tm := re.match(r"^\[([^\]\n]+)\]\s*", body)) is not None:
            tag = tm.group(1)
            if tag == "pin":
                pinned = True
            elif (a := _EXP_ABS_RE.match(tag)) is not None:
                expires = int(a.group(1))
            elif (r := _EXP_REL_RE.match(tag)) is not None:
                expires = generation + int(r.group(1))
            body = body[tm.end() :]
        body = " ".join(body.split())[:MEMORY_LINE_MAX_CHARS]
        if body:
            items.append(MemoryItem(generation, body, pinned, expires))
        if len(items) >= MEMORY_APPEND_MAX_LINES:
            break
    return items


def render_memory(items: list[MemoryItem]) -> str:
    return "".join(f"{i.render()}\n" for i in items)


def merge_memory(
    existing: str,
    append: str,
    generation: int,
    *,
    max_bytes: int = MEMORY_MAX_BYTES,
) -> str:
    """Tool-owned memory merge: expire, dedupe, append (seq-tagged), then cap.

    * items whose ``expires`` generation is below *generation* are dropped;
    * an appended item replaces an existing one with the same normalised text (pin is sticky);
    * over the byte cap the oldest unpinned items go first, pinned ones only as a last resort.
    Deterministic: a pure function of its inputs.
    """
    items = [i for i in parse_memory(existing) if i.expires is None or i.expires >= generation]
    for new in parse_memory_append(append, generation):
        for idx, old in enumerate(items):
            if _norm(old.text) == _norm(new.text):
                items[idx] = MemoryItem(
                    new.seq, new.text, new.pinned or old.pinned, new.expires or old.expires
                )
                break
        else:
            items.append(new)
    while len(render_memory(items).encode()) > max_bytes and items:
        victims = [i for i, it in enumerate(items) if not it.pinned] or list(range(len(items)))
        oldest = min(victims, key=lambda i: (items[i].seq, i))
        del items[oldest]
    return render_memory(items)


# ---------------------------------------------------------------------------------------------
# Contract + pack
# ---------------------------------------------------------------------------------------------


CTL_ENVELOPE_SECTION = """\
## `ao.ctl/v1` proposal envelope
Exactly these top-level fields; **unknown top-level fields are rejected** (CTL-SCHEMA) and the
whole proposal is quarantined. Put anything else inside `payload`.
- `schema`: `"ao.ctl/v1"` (required)
- `id`: unique id, also the file name stem (`<id>.json`) (required)
- `kind`: one of the accepted kinds above (required)
- `actor`: `sentinel-gNN` (required)
- `created_at`: ISO-8601 UTC timestamp string (required; not `ts`, not `time`)
- `rationale`: short string, at most 4000 chars (optional; not `reason`)
- `payload`: object with the kind-specific fields (optional; target ids such as `task_id` go
  here, not at top level)

Note (`payload.text` required):
```json
{"schema": "ao.ctl/v1", "id": "sentinel-g02-note-001", "kind": "note", "actor": "sentinel-g02",
 "created_at": "2026-11-10T14:47:00Z", "rationale": "why this matters",
 "payload": {"text": "t-build has been idle for 40 minutes"}}
```
Kill (`payload.task_id` of a running task required; `no_retry` optional boolean):
```json
{"schema": "ao.ctl/v1", "id": "sentinel-g02-kill-001", "kind": "kill", "actor": "sentinel-g02",
 "created_at": "2026-11-10T14:48:00Z", "rationale": "stuck: no output for 40 minutes",
 "payload": {"task_id": "t-build", "no_retry": false}}
```

"""


def render_contract(
    enabled_kinds: frozenset[str], limits: dict[str, Any], *, planner: bool = False
) -> str:
    """``sentinel-contract.md``: what a generation may read, write and propose (from schemas)."""
    kinds = ", ".join(f"`{k}`" for k in sorted(enabled_kinds))
    lim = "\n".join(f"- `{k}`: {v}" for k, v in sorted(limits.items()))
    schema = json.dumps(HANDOFF_JSON_SCHEMA, indent=2, sort_keys=True)
    return (
        "# Sentinel generation contract\n\n"
        "You are a bounded run overseer. You act **only through files**.\n\n"
        "## Inputs\n"
        "Everything you may read is in `pack/` next to this file (see `manifest.json`). Do not "
        "assume anything not in the pack.\n\n"
        "## Outputs (in the generation directory, the parent of `pack/`)\n"
        f"- `{HANDOFF_FILE}` — written LAST, atomically (`*.tmp` then rename); schema below.\n"
        f"- `{REPORT_FILE}` — short human-readable report.\n"
        f"- `{MEMORY_APPEND_FILE}` — bullets `- [pin][exp:+N] text` to remember (tool-merged;"
        " `[pin]` keeps it, `[exp:+N]` expires after N generations).\n"
        "- Proposals: one `ao.ctl/v1` JSON file per proposal in the run's `control/inbox/`, id "
        "unique and `actor` = `sentinel-gNN` (your generation). Atomic write (`*.tmp` then rename)."
        f" Currently accepted kinds: {kinds}.\n\n"
        f"{CTL_ENVELOPE_SECTION}"
        "## Limits\n"
        f"{lim}\n\n"
        "Rejected proposals are quarantined; repeated rejections pause the LLM lane. Do not repeat "
        "anything listed under `do_not_repeat` in the previous handoff.\n\n"
        f"{PLANNER_CONTRACT_SECTION if planner else ''}"
        f"## `{HANDOFF_SCHEMA_ID}`\n```json\n{schema}\n```\n"
    )


def _cut(data: bytes, cap: int, keep: Literal["head", "tail"]) -> tuple[bytes, bool]:
    """Deterministically bound *data* to *cap* bytes on a UTF-8 boundary, with a marker."""
    if len(data) <= cap:
        return data, False
    dropped = len(data) - cap
    marker = TRUNC_MARKER.format(n=dropped).encode()
    room = max(cap - len(marker), 0)
    if keep == "head":
        body = data[:room].decode("utf-8", "ignore").encode()
        return (body + marker)[:cap], True
    body = data[len(data) - room :].decode("utf-8", "ignore").encode() if room else b""
    return (marker + body)[:cap], True


@dataclass
class PackInputs:
    """Everything pack assembly needs besides the run dir (all values, no clock calls)."""

    generation: int
    now: datetime
    triggers: list[str]
    contract: str
    run_dir: Path


def _digest(state: RunState, sent: SentinelState, inp: PackInputs) -> dict[str, Any]:
    work = {t: s for t, s in state.tasks.items() if not is_sentinel_generation_task(t)}
    counts: dict[str, int] = {}
    for ts in work.values():
        counts[ts.status] = counts.get(ts.status, 0) + 1
    running = [
        {
            "task_id": tid,
            "running_seconds": ts.running_seconds,
            "last_activity_at": ts.last_activity_at,
            "kills": ts.recovery.kills if ts.recovery else 0,
        }
        for tid, ts in sorted(work.items())
        if ts.status == "running"
    ]
    failed = sorted(t for t, s in work.items() if s.status in ("failed", "timed_out"))
    live = state.effective_settings.get("sentinel.max_parallel")
    metrics = state.metrics
    return {
        "generation": inp.generation,
        "now": inp.now.isoformat(),
        "run_id": state.run_id,
        "run_status": state.status,
        "triggers": list(inp.triggers),
        "slots": {"running": len(running), "limit": live.value if live else None},
        "task_counts": dict(sorted(counts.items())),
        "running": running,
        "failed": failed,
        "recent_kills": [k.model_dump() for k in sent.kill_log[-10:]],
        "systemic_alarm": sent.systemic_alarm,
        "notes": [n.model_dump() for n in sent.notes[-DIGEST_NOTES:]],
        "utilisation_pct": metrics.utilisation_pct if metrics else None,
        "llm": {
            "paused": sent.llm_paused,
            "generations_started": sent.generation,
            "ungraceful_count": sent.ungraceful_count,
            "last_gen_ended_at": sent.last_gen_ended_at,
            "spend_usd": round(generation_spend(state), 6),
        },
    }


def _read_capped(path: Path, cap: int, keep: Literal["head", "tail"] = "head") -> bytes | None:
    try:
        data = path.read_bytes()
    except OSError:
        return None
    return _cut(data, cap, keep)[0] if len(data) > cap else data


def _last_events(run_dir: Path, k: int) -> bytes:
    """Last *k* structured events of ``run.log`` (lines that parse as JSON with an ``event``)."""
    try:
        lines = (run_dir / "run.log").read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return b""
    picked: list[str] = []
    for line in reversed(lines):
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict) and "event" in rec:
            picked.append(line)
            if len(picked) >= k:
                break
    return "".join(f"{ln}\n" for ln in reversed(picked)).encode()


def build_pack(state: RunState, sent: SentinelState, inp: PackInputs) -> dict[str, bytes]:
    """Assemble the pack as ``name -> bytes`` (deterministic; every file capped; total capped).

    Same state + same files + same ``now`` => byte-identical result. Truncation is recorded in the
    manifest (``truncated: true``), never silent.
    """
    sdir = inp.run_dir / SENTINEL_DIR
    files: dict[str, bytes] = {}
    trunc: dict[str, bool] = {}

    def put(name: str, data: bytes | None, keep: Literal["head", "tail"] = "head") -> None:
        if data is None:
            return
        cut, was = _cut(data, FILE_CAPS[name], keep)
        files[name], trunc[name] = cut, was

    readme = (
        f"# Sentinel generation g{inp.generation:02d}\n\n"
        f"Triggers: {', '.join(inp.triggers) or 'none'}.\n"
        f"Read `{CONTRACT_FILE}` first, then `digest.json`, `memory.md`, `previous-handoff.json` "
        "(if present), `events.jsonl`, `charter.json` and `backlog.json` (if present). "
        "`manifest.json` lists every file with its size and truncation flag.\n"
    )
    put("README.md", readme.encode())
    put(CONTRACT_FILE, inp.contract.encode())
    put("charter.json", _read_capped(inp.run_dir / CHARTER_REL, 10**9))
    put(
        "digest.json",
        (json.dumps(_digest(state, sent, inp), indent=2, sort_keys=True) + "\n").encode(),
    )
    put("events.jsonl", _last_events(inp.run_dir, EVENTS_LAST_K), keep="tail")
    put("backlog.json", _read_capped(inp.run_dir / BACKLOG_REL, 10**9))
    put("memory.md", _read_capped(sdir / MEMORY_FILE, 10**9, keep="tail") or b"")
    if sent.last_handoff_path:
        put("previous-handoff.json", _read_capped(inp.run_dir / sent.last_handoff_path, 10**9))
    # Charter/backlog/handoff are opaque payloads: a truncated JSON is flagged in the manifest.
    manifest = {
        "schema": PACK_SCHEMA_ID,
        "generation": inp.generation,
        "cap_bytes": PACK_MAX_BYTES,
        "files": [
            {
                "name": n,
                "bytes": len(files[n]),
                "sha256": hashlib.sha256(files[n]).hexdigest(),
                "truncated": trunc[n],
            }
            for n in sorted(files)
        ],
    }
    put("manifest.json", (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode())
    total = sum(len(v) for v in files.values())
    if total > PACK_MAX_BYTES:  # unreachable while the per-file caps sum below the total cap
        raise ValueError(f"pack is {total} bytes, over the {PACK_MAX_BYTES} cap")
    return dict(sorted(files.items()))


def write_pack(pack_dir: Path, files: dict[str, bytes]) -> None:
    """Write the pack to a fresh directory (stale files from a crashed attempt are removed)."""
    pack_dir.mkdir(parents=True, exist_ok=True)
    for stale in pack_dir.iterdir():
        if stale.is_file() and stale.name not in files:
            stale.unlink()
    for name, data in files.items():
        (pack_dir / name).write_bytes(data)


# ---------------------------------------------------------------------------------------------
# Ledger (hash chain)
# ---------------------------------------------------------------------------------------------


def append_ledger(sdir: Path, at: str, event: str, data: dict[str, Any]) -> None:
    """Append one hash-chained record to ``ledger.jsonl`` (tamper-evident, not tamper-proof)."""
    sdir.mkdir(parents=True, exist_ok=True)
    path = sdir / LEDGER_FILE
    prev, seq = "", 0
    try:
        last = path.read_text(encoding="utf-8").splitlines()[-1]
        rec = json.loads(last)
        prev, seq = rec["hash"], rec["seq"] + 1
    except (OSError, IndexError, ValueError, KeyError):
        pass
    body = {"seq": seq, "at": at, "event": event, "data": data, "prev": prev}
    digest = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({**body, "hash": digest}, sort_keys=True) + "\n")


# ---------------------------------------------------------------------------------------------
# Accounting
# ---------------------------------------------------------------------------------------------


generation_spend = sentinel_generation_spend_usd


def budget_cap_usd(spec: SentinelLlmSpec, run_cost_cap_usd: float | None) -> float | None:
    """Sentinel budget: ``budget_usd`` wins; else ``budget_pct`` of the run cost cap (if any)."""
    if spec.budget_usd is not None:
        return spec.budget_usd
    if spec.budget_pct is not None and run_cost_cap_usd:
        return run_cost_cap_usd * spec.budget_pct / 100.0
    return None


def gen_task_id(generation: int) -> str:
    return f"{SENTINEL_GEN_TASK_PREFIX}{generation:02d}"


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        return None


# ---------------------------------------------------------------------------------------------
# Lane
# ---------------------------------------------------------------------------------------------


class GenerationLane:
    """Owns the driver, the 1-worker lane executor and the generation lifecycle.

    The lane executor is separate from the engine's task pool, so a generation never occupies a
    ``max_parallel`` slot. ``driver.start`` runs on it; ``poll``/``stop`` are non-blocking and run
    on the main-thread tick.
    """

    def __init__(
        self,
        *,
        spec: SentinelLlmSpec,
        driver: SentinelDriver,
        run_dir: Path,
        enabled_kinds: frozenset[str],
        run_cost_cap_usd: float | None,
        log: logging.Logger | logging.LoggerAdapter[Any],
        heartbeat_enabled: bool = True,
    ) -> None:
        self.spec = spec
        self.driver = driver
        self.run_dir = run_dir
        self.sdir = run_dir / SENTINEL_DIR
        self.enabled_kinds = enabled_kinds
        self.run_cost_cap_usd = run_cost_cap_usd
        self.log = log
        self.heartbeat_enabled = heartbeat_enabled
        self._pool: ThreadPoolExecutor | None = None
        self._starting: Future[Handle] | None = None
        self._handle: Handle | None = None
        self._stop_reason: str | None = None
        self._handoff_seen_at: datetime | None = None
        self._idle_mark: float | None = None
        """``slot_seconds_idle_no_ready`` at the last planner trigger/baseline (in-memory)."""

    def _planner_on(self) -> bool:
        return self.spec.planner and self.spec.powers == "act"

    # -- lifecycle ---------------------------------------------------------------------------

    def _lane(self) -> ThreadPoolExecutor:
        if self._pool is None:
            self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sentinel-lane")
        return self._pool

    def close(self) -> None:
        if self._pool is not None:
            self._pool.shutdown(wait=True)
            self._pool = None

    def reconcile_on_start(self, state: RunState, now: datetime) -> bool:
        """Resume: a generation recorded as live died with the previous process (ungraceful)."""
        sent = state.sentinel
        if sent is None or sent.active_generation is None:
            return False
        tid = sent.active_generation
        ts = state.tasks.get(tid)
        gen = sent.generation
        if ts is not None and ts.status == "running":
            ts.status = "failed"
            ts.ended_at = now.isoformat()
            ts.not_taken_reason = None
        sent.active_generation = None
        sent.gen_triggers = 0
        sent.ungraceful_count += 1
        sent.last_gen_ended_at = now.isoformat()
        self._merge_memory(state, sent, gen, now)
        append_ledger(
            self.sdir,
            now.isoformat(),
            "sentinel_ungraceful",
            {"generation": gen, "reason": "engine_restart"},
        )
        self.log.warning(
            "sentinel_ungraceful: generation %d lost with the previous engine process",
            gen,
            extra={"event": "sentinel_ungraceful", "generation": gen, "reason": "engine_restart"},
        )
        return True

    # -- triggers ----------------------------------------------------------------------------

    def _fresh(self, ts_iso: str | None, kind: str, sent: SentinelState) -> bool:
        at, last = _parse(ts_iso), _parse(sent.trigger_last.get(kind))
        return at is not None and (last is None or at > last)

    def pending_triggers(self, state: RunState, sent: SentinelState, now: datetime) -> list[str]:
        """Trigger kinds that currently hold (per-kind cooldown honoured, deterministic order)."""
        cooldown = self.spec.trigger_cooldown_minutes * 60
        out: list[str] = []
        work = {t: s for t, s in state.tasks.items() if not is_sentinel_generation_task(t)}
        for kind in TRIGGER_ORDER:
            last = _parse(sent.trigger_last.get(kind))
            if last is not None and (now - last).total_seconds() < cooldown:
                continue
            if kind == TRIGGER_STUCK:
                hit = any(
                    s.status == "failed"
                    and s.recovery is not None
                    and s.recovery.kills > 0
                    and self._fresh(s.ended_at, kind, sent)
                    for s in work.values()
                )
            elif kind == TRIGGER_UNIT_FAILED:
                hit = any(
                    s.status in ("failed", "timed_out") and self._fresh(s.ended_at, kind, sent)
                    for s in work.values()
                )
            elif kind == TRIGGER_HUMAN_NOTE:
                hit = any(self._fresh(n.at, kind, sent) for n in sent.notes)
            elif kind == TRIGGER_IDLE_SLOTS:
                hit = self._planner_idle_hit(state)
            else:
                ref = _parse(sent.last_gen_ended_at) or _parse(state.started_at) or now
                hit = self.heartbeat_enabled and (
                    (now - ref).total_seconds() >= self.spec.heartbeat_minutes * 60
                    and (
                        last is None
                        or (now - last).total_seconds() >= self.spec.heartbeat_minutes * 60
                    )
                )
            if hit:
                out.append(kind)
        return out

    def _planner_idle_hit(self, state: RunState) -> bool:
        """routed-runner L2: idle-with-nothing-ready slot-seconds grew enough, and no backlog.

        The baseline is taken on first sight (and after each fire), so resume never fires on the
        whole pre-resume history. Only while at least one work task is still unfinished.
        """
        if not self._planner_on() or state.metrics is None:
            return False
        cur = state.metrics.slot_seconds_idle_no_ready
        if self._idle_mark is None:
            self._idle_mark = cur
            return False
        unfinished = any(
            s.status in ("pending", "running", "killed")
            for t, s in state.tasks.items()
            if not is_sentinel_generation_task(t)
        )
        if not unfinished or not backlog_is_empty(self.run_dir):
            return False
        if cur - self._idle_mark >= self.spec.planner_idle_seconds:
            self._idle_mark = cur
            return True
        return False

    # -- the tick ----------------------------------------------------------------------------

    def tick(self, state: RunState, now: datetime) -> bool:
        """Advance the lane one step. Returns True when ``state`` changed (caller saves)."""
        sent = state.sentinel
        if sent is None:
            return False
        if sent.active_generation is not None:
            return self._tick_active(state, sent, now)
        return self._maybe_start(state, sent, now)

    def _limits(self, sent: SentinelState, state: RunState) -> GenerationLimits:
        cap = budget_cap_usd(self.spec, self.run_cost_cap_usd)
        remaining = None if cap is None else max(cap - generation_spend(state), 0.0)
        return GenerationLimits(
            ttl_seconds=self.spec.generation_ttl_minutes * 60,
            max_tokens=self.spec.max_tokens_per_generation,
            max_budget_usd=remaining,
            generation=sent.generation,
            inbox_dir=str(self.run_dir / "control" / "inbox"),
        )

    def _maybe_start(self, state: RunState, sent: SentinelState, now: datetime) -> bool:
        if sent.llm_paused:
            return False
        cap = budget_cap_usd(self.spec, self.run_cost_cap_usd)
        changed = False
        if cap is not None and generation_spend(state) >= cap:
            if not sent.llm_budget_exhausted:
                sent.llm_budget_exhausted = True
                append_ledger(
                    self.sdir,
                    now.isoformat(),
                    "budget_exhausted",
                    {"cap_usd": cap, "spend_usd": generation_spend(state)},
                )
                self.log.warning(
                    "sentinel.llm.budget_exhausted: spend %.4f >= cap %.4f; no new generations",
                    generation_spend(state),
                    cap,
                    extra={"event": "sentinel.llm.budget_exhausted"},
                )
                changed = True
            return changed
        triggers = self.pending_triggers(state, sent, now)
        if not triggers:
            return False
        recent = [
            s
            for s in sent.gen_starts
            if (a := _parse(s)) is not None and (now - a).total_seconds() < HOUR_SECONDS
        ]
        if len(recent) >= self.spec.max_generations_per_hour:
            return False
        return self._start(state, sent, now, triggers, recent)

    def _start(
        self,
        state: RunState,
        sent: SentinelState,
        now: datetime,
        triggers: list[str],
        recent: list[str],
    ) -> bool:
        gen = sent.generation + 1
        sent.generation = gen
        gdir = self.sdir / f"g{gen:02d}"
        pack = build_pack(
            state,
            sent,
            PackInputs(
                generation=gen,
                now=now,
                triggers=triggers,
                contract=render_contract(
                    self.enabled_kinds, self._contract_limits(), planner=self._planner_on()
                ),
                run_dir=self.run_dir,
            ),
        )
        write_pack(gdir / PACK_DIR, pack)
        (gdir / TRIGGERS_FILE).write_text(
            "".join(json.dumps({"at": now.isoformat(), "kind": k}) + "\n" for k in triggers)
        )
        tid = gen_task_id(gen)
        state.tasks[tid] = TaskRunState(
            status="running",
            attempts=1,
            dispatch_cycle=1,  # counts as dispatched for `ao usage`
            started_at=now.isoformat(),
            agent=self.spec.agent or "sentinel",
        )
        sent.active_generation = tid
        sent.gen_started_at = now.isoformat()
        sent.gen_triggers = len(triggers)
        sent.gen_starts = [*recent, now.isoformat()]
        for kind in triggers:
            sent.trigger_last[kind] = now.isoformat()
        self._stop_reason = None
        self._handoff_seen_at = None
        self._handle = None
        self._starting = self._lane().submit(
            self.driver.start, str(gdir / PACK_DIR), self._limits(sent, state)
        )
        append_ledger(
            self.sdir,
            now.isoformat(),
            "generation_started",
            {"generation": gen, "triggers": triggers},
        )
        self.log.info(
            "sentinel.llm.start: g%02d triggers=%s",
            gen,
            ",".join(triggers),
            extra={"event": "sentinel.llm.start", "generation": gen, "triggers": triggers},
        )
        return True

    def _contract_limits(self) -> dict[str, Any]:
        s = self.spec
        return {
            "generation_ttl_minutes": s.generation_ttl_minutes,
            "max_tokens_per_generation": s.max_tokens_per_generation,
            "max_triggers_per_generation": s.max_triggers_per_generation,
            "max_consecutive_rejections": s.max_consecutive_rejections,
            "powers": s.powers,
        }

    def _tick_active(self, state: RunState, sent: SentinelState, now: datetime) -> bool:
        assert sent.active_generation is not None
        if self._handle is None:
            if self._starting is None:  # live in state but no process of ours: treat as lost
                return self.reconcile_on_start(state, now)
            if not self._starting.done():
                return False
            try:
                self._handle = self._starting.result()
            except Exception as exc:  # noqa: BLE001 -- driver boundary: record, never raise
                self._starting = None
                return self._finalize(
                    state,
                    sent,
                    now,
                    DriverStatus(alive=False, returncode=None, error=repr(exc)[:300]),
                    forced=None,
                    detail="start_failed",
                )
            self._starting = None
        handle = self._handle
        status = self.driver.poll(handle)
        if not status.alive:
            return self._finalize(state, sent, now, status, forced=self._stop_reason, detail=None)
        changed = self._count_triggers(state, sent, now)
        reason = self._stop_cause(sent, status, now)
        if reason is not None:
            final = self._stop_and_poll(handle)
            return self._finalize(state, sent, now, final, forced=reason, detail=None) or changed
        return changed

    def _stop_and_poll(self, handle: Handle) -> DriverStatus:
        """Stop the generation and read its final status; a driver error never escapes here."""
        try:
            self.driver.stop(handle)
            return self.driver.poll(handle)
        except Exception as exc:  # noqa: BLE001 -- driver boundary: record as an ungraceful end
            return DriverStatus(alive=False, returncode=None, error=f"driver error: {exc!r}"[:300])

    def _count_triggers(self, state: RunState, sent: SentinelState, now: datetime) -> bool:
        new = self.pending_triggers(state, sent, now)
        # heartbeat is for starting a generation; a live one is, by definition, already awake.
        new = [k for k in new if k != TRIGGER_HEARTBEAT]
        if not new:
            return False
        gdir = self.sdir / f"g{sent.generation:02d}"
        with (gdir / TRIGGERS_FILE).open("a", encoding="utf-8") as fh:
            for kind in new:
                fh.write(json.dumps({"at": now.isoformat(), "kind": kind}) + "\n")
                sent.trigger_last[kind] = now.isoformat()
        sent.gen_triggers += len(new)
        return True

    def _permission_alarm(self, sent: SentinelState, gen: int, detail: str, now: datetime) -> None:
        """Distinct alarm (AC5): the generation was refused tool permissions, not merely crashed.

        Deterministic, so the LLM lane is paused rather than burning budget on identical retries.
        """
        from .sentinel_tick import CONTROL_DIR, append_alarm

        sent.llm_paused = True
        append_ledger(
            self.sdir,
            now.isoformat(),
            "sentinel_permission_denied",
            {"generation": gen, "detail": detail},
        )
        self.log.error(
            "sentinel.alarm: llm_permission_denied — g%02d: %s; LLM lane paused",
            gen,
            detail,
            extra={"event": "sentinel.alarm", "kind": "llm_permission_denied", "generation": gen},
        )
        record = {
            "at": now.isoformat(),
            "kind": "llm_permission_denied",
            "generation": gen,
            "detail": detail,
        }
        append_alarm(self.run_dir / CONTROL_DIR, self.log, record)

    def _stop_cause(self, sent: SentinelState, status: DriverStatus, now: datetime) -> str | None:
        gen = sent.generation
        gdir = self.sdir / f"g{gen:02d}"
        if validate_handoff(gdir / HANDOFF_FILE, gen).handoff is not None:
            if self._handoff_seen_at is None:
                self._handoff_seen_at = now
            if (now - self._handoff_seen_at).total_seconds() >= HANDOFF_GRACE_SECONDS:
                return END_HANDOFF
        started = _parse(sent.gen_started_at)
        if (
            started is not None
            and (now - started).total_seconds() >= self.spec.generation_ttl_minutes * 60
        ):
            return END_TTL
        if status.tokens_used >= self.spec.max_tokens_per_generation:
            return END_MAX_TOKENS
        if sent.gen_triggers >= self.spec.max_triggers_per_generation:
            return END_MAX_TRIGGERS
        return None

    def _finalize(
        self,
        state: RunState,
        sent: SentinelState,
        now: datetime,
        status: DriverStatus,
        *,
        forced: str | None,
        detail: str | None,
    ) -> bool:
        gen = sent.generation
        tid = gen_task_id(gen)
        gdir = self.sdir / f"g{gen:02d}"
        res = validate_handoff(gdir / HANDOFF_FILE, gen)
        ts = state.tasks.get(tid)
        graceful = res.handoff is not None and (
            status.returncode in (0, None) or forced is not None
        )
        if ts is not None:
            ts.status = "succeeded" if graceful else "failed"
            ts.ended_at = now.isoformat()
            ts.cumulative_cost_usd += status.cost_usd or 0.0
            ts.cumulative_input_tokens += status.input_tokens or 0
            ts.cumulative_output_tokens += status.output_tokens or 0
            ts.cumulative_cache_creation_input_tokens += status.cache_creation_input_tokens or 0
            ts.cumulative_cache_read_input_tokens += status.cache_read_input_tokens or 0
        if res.handoff is not None:
            sent.last_handoff_path = f"{SENTINEL_DIR}/g{gen:02d}/{HANDOFF_FILE}"
        if not graceful:
            sent.ungraceful_count += 1
            why = (
                detail
                or forced
                or (
                    f"exit {status.returncode}" if status.returncode not in (0, None) else res.error
                )
            )
            append_ledger(
                self.sdir,
                now.isoformat(),
                "sentinel_ungraceful",
                {
                    "generation": gen,
                    "reason": why,
                    "handoff_error": res.error,
                    "error": status.error,
                },
            )
            if status.permission_denied:
                self._permission_alarm(sent, gen, status.permission_denied, now)
            self.log.warning(
                "sentinel_ungraceful: g%02d (%s)",
                gen,
                why,
                extra={"event": "sentinel_ungraceful", "generation": gen, "reason": why},
            )
        self._merge_memory(state, sent, gen, now)
        append_ledger(
            self.sdir,
            now.isoformat(),
            "generation_ended",
            {
                "generation": gen,
                "graceful": graceful,
                "forced": forced,
                "ended_reason": res.handoff.ended_reason if res.handoff else None,
                "cost_usd": status.cost_usd,
                "tokens": status.tokens_used,
            },
        )
        sent.active_generation = None
        sent.gen_triggers = 0
        sent.last_gen_ended_at = now.isoformat()
        self._handle = None
        self._stop_reason = None
        return True

    def _merge_memory(self, state: RunState, sent: SentinelState, gen: int, now: datetime) -> None:
        del state
        gdir = self.sdir / f"g{gen:02d}"
        try:
            append = _read_capped(gdir / MEMORY_APPEND_FILE, MEMORY_APPEND_MAX_BYTES)
        except OSError:
            append = None
        if append is None:
            return
        mem_path = self.sdir / MEMORY_FILE
        try:
            existing = mem_path.read_text(encoding="utf-8")
        except OSError:
            existing = ""
        merged = merge_memory(existing, append.decode("utf-8", "replace"), gen)
        self.sdir.mkdir(parents=True, exist_ok=True)
        tmp = mem_path.with_suffix(".md.tmp")
        tmp.write_text(merged, encoding="utf-8")
        tmp.replace(mem_path)
        sent.memory_seq += 1

    def shutdown(self, state: RunState, now: datetime) -> bool:
        """Run end: stop a live generation and settle its row; release the lane executor."""
        sent = state.sentinel
        changed = False
        try:
            if sent is not None and sent.active_generation is not None:
                if self._starting is not None:
                    try:
                        self._handle = self._starting.result(timeout=30)
                    except Exception:  # noqa: BLE001 -- best effort at shutdown
                        self._handle = None
                    self._starting = None
                if self._handle is not None:
                    final = self._stop_and_poll(self._handle)
                    changed = self._finalize(state, sent, now, final, forced="run_end", detail=None)
                else:
                    changed = self.reconcile_on_start(state, now)
        finally:
            self.close()
        return changed


# Type of the factory the engine hands to _start_sentinel (tests inject a scripted driver).
DriverFactory = Callable[[SentinelLlmSpec], SentinelDriver]
