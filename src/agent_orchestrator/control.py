"""Control-inbox *writer* side: ``ao.ctl/v1`` entry builder, run lookup, wait, status snapshot.

E-Sn7tL2 T-Sn3Ctl (``docs-md/sentinel-hld.md`` §4, §12). Shared by ``ao ctl`` (cli.py) and, later,
the dashboard control API (T-Sn5Ui1): both only ever *write one inbox file* -- the engine's tick
(``sentinel_tick.drain_inbox``) is the single place that mutates state.

Pure helpers with injectable clock / randomness / sleep so tests are deterministic. Imports only
stdlib + light model/registry modules (``cli.py`` imports this lazily inside command bodies).
"""

from __future__ import annotations

import getpass
import json
import os
import re
import secrets
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from . import sentinel_tick
from .models import RunState, SentinelState, sentinel_generation_spend_usd
from .run_options import OPTIONS_BY_NAME, Option

STATUS_SCHEMA: Final = "ao.ctl.status/v1"
"""``schema`` of ``ao ctl status --json`` (documented in ``docs-md/sentinel-hld.md`` §12.1)."""

ACTOR_PREFIX: Final = "cli:"
DASHBOARD_ACTOR_PREFIX: Final = "dashboard:"
DASHBOARD_ACTOR_ANON: Final = "local"
"""Actor suffix when the dashboard runs without auth (no principal to attribute)."""
RUNS_SUBPATH: Final = (".orchestrator", "runs")
STATE_FILE: Final = "state.json"
DEFAULT_WAIT_SECONDS: Final = 60.0
WAIT_POLL_SECONDS: Final = 0.1
RECENT_ACTIONS_MAX: Final = 20
NOT_ENABLED_YET: Final = sentinel_tick.RULE_NOT_ENABLED

# CLI subcommand -> inbox ``kind`` (HLD §12). ``status`` is read-only and has no entry kind.
KIND_BY_COMMAND: Final = {
    "set": "set",
    "pause-llm": "pause_llm",
    "resume-llm": "resume_llm",
    "kill": "kill",
    "note": "note",
    "release": "release_backlog",
    "cancel": "request_cancel",
}

# Kinds the operator surfaces (CLI + dashboard) may submit -- a subset of the engine's
# ``KINDS_ALL``: ``inject`` / ``edit_pending`` / ``directive`` are LLM-lane proposals and
# ``request_hold`` is engine-internal. ``cancel`` stays a CLI-only alias of ``request_cancel``.
CONTROL_KINDS: Final = frozenset(KIND_BY_COMMAND.values())
KILL_REASON_MAX: Final = 200
RELEASE_IDS_MAX: Final = 200
ENTRY_ID_RE: Final = re.compile(r"^[a-z0-9][a-z0-9-]{4,95}$")
"""Shape of ids minted by :func:`new_entry_id` (also keeps a path segment from escaping)."""

_ID_SAFE = re.compile(r"[^a-z0-9]+")


class ControlError(Exception):
    """User-facing failure (bad run id, invalid key/value, unwritable inbox); message is final."""


# -- run resolution ---------------------------------------------------------------------------


def runs_root(workspace_root: str | os.PathLike[str]) -> Path:
    return Path(workspace_root).joinpath(*RUNS_SUBPATH)


def resolve_run_dir(workspace_root: str | os.PathLike[str], run_prefix: str) -> Path:
    """Directory of the run whose id equals *run_prefix*, else the unique run it prefixes.

    An exact match always wins (so ``wf-1`` stays addressable next to ``wf-10``); an ambiguous
    prefix lists the candidates rather than guessing.
    """
    root = runs_root(workspace_root)
    if not run_prefix or "/" in run_prefix or run_prefix in {".", ".."}:
        raise ControlError(f"invalid run id {run_prefix!r}")
    if not root.is_dir():
        raise ControlError(f"no runs under {root} (is --workspace right?)")
    if (root / run_prefix / STATE_FILE).is_file():
        return root / run_prefix
    matches = sorted(
        p.name
        for p in root.iterdir()
        if p.name.startswith(run_prefix) and (p / STATE_FILE).is_file()
    )
    if not matches:
        raise ControlError(f"no run matching {run_prefix!r} under {root}")
    if len(matches) > 1:
        raise ControlError(
            f"run id prefix {run_prefix!r} is ambiguous: {', '.join(matches[:10])}"
            + (" ..." if len(matches) > 10 else "")
        )
    return root / matches[0]


def load_state(run_dir: Path) -> RunState:
    try:
        return RunState.model_validate_json((run_dir / STATE_FILE).read_text())
    except (OSError, ValueError) as exc:
        raise ControlError(f"cannot read {run_dir / STATE_FILE}: {exc}") from exc


# -- entry building / writing -----------------------------------------------------------------


def default_actor() -> str:
    """``cli:<os user>`` -- recorded on every entry (audit trail)."""
    try:
        user = getpass.getuser()
    except Exception:  # noqa: BLE001 -- no passwd entry / odd container: never block a control action
        user = os.environ.get("USER") or os.environ.get("LOGNAME") or "unknown"
    return f"{ACTOR_PREFIX}{user}"


def dashboard_actor(username: str | None) -> str:
    """``dashboard:<principal>`` (``dashboard:local`` when auth is off): the entry audit trail."""
    name = (username or "").strip()
    return f"{DASHBOARD_ACTOR_PREFIX}{name or DASHBOARD_ACTOR_ANON}"


def new_entry_id(
    kind: str,
    now: datetime,
    rand: Callable[[int], str] = secrets.token_hex,
) -> str:
    """``<kind>-<utc ts>-<rand>``: lowercase, ``[a-z0-9-]`` only (passes the engine's id check) and
    sorts chronologically within a tick (HLD §4.2)."""
    slug = _ID_SAFE.sub("-", kind.lower()).strip("-") or "entry"
    return f"{slug}-{now.strftime('%Y%m%dt%H%M%S')}-{rand(4)}"


def build_entry(
    kind: str,
    payload: Mapping[str, Any] | None = None,
    *,
    actor: str | None = None,
    rationale: str | None = None,
    now: datetime | None = None,
    entry_id: str | None = None,
    rand: Callable[[int], str] = secrets.token_hex,
) -> dict[str, Any]:
    """One ``ao.ctl/v1`` entry (HLD App. A.1). Shared by ``ao ctl`` and the dashboard API."""
    when = now or datetime.now(UTC)
    entry: dict[str, Any] = {
        "schema": sentinel_tick.CTL_SCHEMA,
        "id": entry_id or new_entry_id(kind, when, rand),
        "kind": kind,
        "actor": actor or default_actor(),
        "created_at": when.isoformat(),
        "payload": dict(payload or {}),
    }
    if rationale:
        entry["rationale"] = rationale
    return entry


def write_entry(run_dir: Path, entry: Mapping[str, Any]) -> Path:
    """Atomically publish *entry*: write ``<id>.json.tmp`` then rename (the engine ignores
    ``*.tmp``, so it can never read a half-written file)."""
    inbox = run_dir / sentinel_tick.CONTROL_DIR / sentinel_tick.INBOX_DIR
    final = inbox / f"{entry['id']}.json"
    tmp = inbox / f"{entry['id']}.json.tmp"
    try:
        inbox.mkdir(parents=True, exist_ok=True)
        with tmp.open("w") as fh:
            json.dump(entry, fh, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        tmp.replace(final)
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        raise ControlError(f"cannot write control entry {final}: {exc}") from exc
    return final


# -- client-side validation (same registry the server side keys off) ---------------------------


def _registry_option(key: str) -> Option:
    opt = OPTIONS_BY_NAME.get(key) or OPTIONS_BY_NAME.get(f"sentinel.{key}")
    if opt is None:
        raise ControlError(f"unknown setting {key!r}")
    return opt


def _coerce(opt: Option, key: str, raw: str) -> Any:
    try:
        if opt.type == "int":
            return int(raw)
        if opt.type == "float":
            return float(raw)
        if opt.type == "bool":
            low = raw.strip().lower()
            if low in {"1", "true", "yes", "on"}:
                return True
            if low in {"0", "false", "no", "off"}:
                return False
            raise ValueError("expected true/false")
        if opt.type == "enum" and raw not in opt.choices:
            raise ValueError(f"expected one of {', '.join(opt.choices)}")
        return raw
    except ValueError as exc:
        raise ControlError(f"invalid value {raw!r} for {key!r}: {exc}") from exc


def parse_set_items(items: list[str], *, ceiling: int | None = None) -> dict[str, Any]:
    """``k=v`` pairs -> typed values, rejected *before* anything is written.

    Mirrors ``sentinel_tick._apply_set``: only :data:`sentinel_tick.OVERRIDABLE_KEYS` are
    accepted, typed through the run-options registry, ``max_parallel >= 1`` and (when known)
    ``<= ceiling``. The engine re-validates everything (the inbox is not trusted).
    """
    if not items:
        raise ControlError("set requires at least one key=value")
    out: dict[str, Any] = {}
    for item in items:
        key, sep, raw = item.partition("=")
        key = key.strip()
        if not sep or not key:
            raise ControlError(f"expected key=value, got {item!r}")
        if key not in sentinel_tick.OVERRIDABLE_KEYS:
            raise ControlError(
                f"{key!r} is not live-overridable; allowed: "
                f"{', '.join(sorted(sentinel_tick.OVERRIDABLE_KEYS))}"
            )
        if key in out:
            raise ControlError(f"duplicate key {key!r}")
        value = _coerce(_registry_option(key), key, raw.strip())
        if key == "max_parallel":
            if value < 1:
                raise ControlError("max_parallel must be an integer >= 1")
            if ceiling is not None and value > ceiling:
                raise ControlError(f"max_parallel {value} exceeds the run's ceiling {ceiling}")
        out[key] = value
    return out


def run_ceiling(state: RunState) -> int | None:
    """The run's ``sentinel.max_parallel_ceiling`` when the snapshot records it, else ``None``."""
    eff = state.effective_settings.get("sentinel.max_parallel_ceiling")
    value = eff.value if eff is not None else None
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _scalar_text(value: Any) -> str:
    """A JSON scalar as the ``k=v`` text :func:`parse_set_items` expects (bools lower-cased)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float | str):
        return str(value)
    raise ControlError(f"unsupported value {value!r}: expected a number, string or boolean")


def validate_request(
    kind: str,
    payload: Mapping[str, Any] | None,
    rationale: str | None,
    state: RunState,
) -> tuple[dict[str, Any], str | None]:
    """Validate one operator control request against *state* and return the clean
    ``(payload, rationale)`` to put into the entry.

    The dashboard API's counterpart of :func:`parse_set_items` (same ceiling / typing rules; the
    engine still re-validates, the inbox is not trusted). Raises :class:`ControlError`.
    """
    if kind not in CONTROL_KINDS:
        raise ControlError(
            f"unsupported control kind {kind!r}; allowed: {', '.join(sorted(CONTROL_KINDS))}"
        )
    data = dict(payload or {})
    why = (rationale or "").strip() or None
    out: dict[str, Any]
    if kind == "set":
        values = data.get("values")
        if not isinstance(values, Mapping) or not values:
            raise ControlError("set requires payload.values (a non-empty object)")
        items = [f"{k}={_scalar_text(v)}" for k, v in values.items()]
        out = {"values": parse_set_items(items, ceiling=run_ceiling(state))}
    elif kind == "kill":
        task_id = data.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            raise ControlError("kill requires payload.task_id")
        task = state.tasks.get(task_id)
        if task is None or task.status != "running":
            raise ControlError(f"task {task_id!r} is not running")
        if why is None:
            raise ControlError("kill requires a reason")
        no_retry = data.get("no_retry", False)
        if not isinstance(no_retry, bool):
            raise ControlError("kill payload.no_retry must be a boolean")
        out = {"task_id": task_id, "no_retry": no_retry}
    elif kind == "note":
        text = data.get("text")
        text = text.strip() if isinstance(text, str) else ""
        if not text or len(text) > sentinel_tick.NOTE_TEXT_MAX:
            raise ControlError(f"note text must be 1..{sentinel_tick.NOTE_TEXT_MAX} chars")
        out = {"text": text}
    elif kind == "release_backlog":
        ids = data.get("ids", [])
        if not isinstance(ids, list) or not all(isinstance(i, str) and i for i in ids):
            raise ControlError("release_backlog payload.ids must be a list of ids")
        if len(ids) > RELEASE_IDS_MAX:
            raise ControlError(f"release_backlog accepts at most {RELEASE_IDS_MAX} ids")
        out = {"ids": ids}
    else:  # pause_llm / resume_llm / request_cancel carry no payload
        out = {}
    if why is not None and len(why) > KILL_REASON_MAX:
        raise ControlError(f"reason must be at most {KILL_REASON_MAX} characters")
    return out, why


# -- waiting for the engine's verdict ----------------------------------------------------------


def wait_outcome(
    run_dir: Path,
    entry_id: str,
    *,
    timeout: float = DEFAULT_WAIT_SECONDS,
    poll: float = WAIT_POLL_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """Block until the engine consumed or quarantined *entry_id*.

    Returns ``{"outcome": "consumed"}``, ``{"outcome": "rejected", "rule_id", "detail"}`` or
    ``{"outcome": "timeout"}``. Reads only the two result directories (no state load).
    """
    control = run_dir / sentinel_tick.CONTROL_DIR
    done = control / sentinel_tick.PROCESSED_DIR / f"{entry_id}.json"
    reason = control / sentinel_tick.REJECTED_DIR / f"{entry_id}.reason.json"
    rejected = control / sentinel_tick.REJECTED_DIR / f"{entry_id}.json"
    deadline = monotonic() + timeout
    while True:
        # Rejection first: the sidecar is written before the entry is moved (see _quarantine).
        if reason.is_file() or rejected.is_file():
            info: dict[str, Any] = {}
            try:
                info = json.loads(reason.read_text())
            except (OSError, ValueError):
                pass
            return {
                "outcome": "rejected",
                "rule_id": info.get("rule_id", "unknown"),
                "detail": info.get("detail", ""),
            }
        if done.is_file():
            return {"outcome": "consumed"}
        if monotonic() >= deadline:
            return {"outcome": "timeout"}
        sleep(poll)


def entry_outcome(run_dir: Path, entry_id: str) -> dict[str, Any]:
    """Non-blocking verdict of one entry: ``consumed`` / ``rejected`` (+ rule) / ``pending``.

    Unlike :func:`wait_outcome` a missing verdict is ``pending`` (the dashboard polls); the id is
    shape-checked because it reaches a filesystem path from an HTTP segment.
    """
    if not ENTRY_ID_RE.fullmatch(entry_id):
        raise ControlError(f"invalid control entry id {entry_id!r}")
    result = wait_outcome(run_dir, entry_id, timeout=0.0, poll=0.0)
    return {"outcome": "pending"} if result["outcome"] == "timeout" else result


# -- status snapshot ---------------------------------------------------------------------------


def _pending_inbox(run_dir: Path) -> list[str]:
    inbox = run_dir / sentinel_tick.CONTROL_DIR / sentinel_tick.INBOX_DIR
    if not inbox.is_dir():
        return []
    return sorted(p.stem for p in inbox.iterdir() if p.suffix == ".json" and p.is_file())


def _age_seconds(iso: str | None, now: datetime) -> float | None:
    if not iso:
        return None
    try:
        when = datetime.fromisoformat(iso)
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return round(max((now - when).total_seconds(), 0.0), 1)


def _effective(state: RunState, key: str) -> Any:
    eff = state.effective_settings.get(key)
    return eff.value if eff is not None else None


def _recent_actions(sent: SentinelState | None) -> list[dict[str, Any]]:
    """Operator notes and recorded kills, oldest first, bounded to :data:`RECENT_ACTIONS_MAX`."""
    if sent is None:
        return []
    rows = [
        {"id": n.id, "kind": "note", "actor": n.actor, "at": n.at, "text": n.text}
        for n in sent.notes[-RECENT_ACTIONS_MAX:]
    ] + [
        {
            "id": f"kill-{k.task_id}-{k.at}",
            "kind": "kill",
            "actor": k.source,
            "at": k.at,
            "text": f"{k.task_id}: {k.reason}",
        }
        for k in sent.kill_log[-RECENT_ACTIONS_MAX:]
    ]
    rows.sort(key=lambda r: r["at"])
    return rows[-RECENT_ACTIONS_MAX:]


def build_status(state: RunState, run_dir: Path, now: datetime | None = None) -> dict[str, Any]:
    """The ``ao ctl status --json`` document (``ao.ctl.status/v1``, HLD §12.1).

    Fields owned by later tasks (backlog) are present with an empty value now, so the schema is
    stable and consumers need no feature probes. *now* is injectable for deterministic ages.
    """
    now = now or datetime.now(UTC)
    sent = state.sentinel
    metrics = state.metrics
    ceiling = run_ceiling(state)
    live_limit = None
    eff_limit = state.effective_settings.get("sentinel.max_parallel")
    if eff_limit is None:
        eff_limit = state.effective_settings.get("max_parallel")
    if eff_limit is not None and isinstance(eff_limit.value, int):
        live_limit = eff_limit.value
    running = sorted(tid for tid, ts in state.tasks.items() if ts.status == "running")
    counts: dict[str, int] = {}
    for ts in state.tasks.values():
        counts[ts.status] = counts.get(ts.status, 0) + 1
    return {
        "schema": STATUS_SCHEMA,
        "run_id": state.run_id,
        "run_status": state.status,
        "updated_at": state.updated_at,
        "sentinel": {
            "enabled": bool(sent and sent.enabled),
            "disabled_reason": sent.disabled_reason if sent else None,
            "last_tick_at": sent.last_tick_at if sent else None,
            "llm_paused": bool(sent and sent.llm_paused),
            "hold_requested": bool(sent and sent.hold_requested),
            "systemic_alarm": bool(sent and sent.systemic_alarm),
        },
        "concurrency": {
            "running": len(running),
            "limit": live_limit,
            "ceiling": ceiling,
            "overrides": {
                k: {"value": v.value, "actor": v.actor, "at": v.at}
                for k, v in sorted((sent.overrides if sent else {}).items())
            },
        },
        "utilisation": {
            "pct": metrics.utilisation_pct if metrics else None,
            "slot_seconds_busy": metrics.slot_seconds_busy if metrics else None,
            "slot_seconds_idle_with_ready": metrics.slot_seconds_idle_with_ready
            if metrics
            else None,
            "slot_seconds_idle_no_ready": metrics.slot_seconds_idle_no_ready if metrics else None,
        },
        "tasks": {
            "counts": dict(sorted(counts.items())),
            "running": [
                {"id": tid, "liveness_age_s": _age_seconds(state.tasks[tid].last_activity_at, now)}
                for tid in running
            ],
        },
        "ladder": {
            tid: {
                "kills": rec.kills,
                "last_reason": rec.last_reason,
                "last_source": rec.last_source,
                "escalation_step": rec.escalation_step,
                "no_retry": rec.no_retry,
            }
            for tid, ts in sorted(state.tasks.items())
            if (rec := ts.recovery) is not None and rec.kills > 0
        },
        "backlog": {"held": []},
        "llm": {
            "generation": (sent.generation or None) if sent else None,
            "spend_usd": round(sentinel_generation_spend_usd(state), 6)
            if sent and sent.generation
            else None,
            "paused": bool(sent and sent.llm_paused),
            "pause_reason": sent.llm_pause_reason if sent else None,
            "budget_exhausted": bool(sent and sent.llm_budget_exhausted),
            "enabled": _effective(state, "sentinel.llm.enabled"),
            "powers": _effective(state, "sentinel.llm.powers"),
        },
        "inbox": {
            "pending": _pending_inbox(run_dir),
            "consumed_count": len(sent.inbox_consumed) if sent else 0,
        },
        "recent_actions": _recent_actions(sent),
    }


def format_status(doc: Mapping[str, Any]) -> str:
    """Human-readable rendering of :func:`build_status`."""
    sent = doc["sentinel"]
    conc = doc["concurrency"]
    util = doc["utilisation"]
    lines = [
        f"Run:        {doc['run_id']}  ({doc['run_status']})",
        f"Sentinel:   {'enabled' if sent['enabled'] else 'off'}"
        + (f"  last tick {sent['last_tick_at']}" if sent["last_tick_at"] else "")
        + (f"  [disabled: {sent['disabled_reason']}]" if sent["disabled_reason"] else ""),
        f"Slots:      running={conc['running']} limit={conc['limit']} ceiling={conc['ceiling']}",
    ]
    if util["pct"] is not None:
        lines.append(f"Utilisation: {util['pct']:.1f}%")
    lines.append(
        "Tasks:      " + (", ".join(f"{k}={v}" for k, v in doc["tasks"]["counts"].items()) or "-")
    )
    for row in doc["tasks"]["running"]:
        age = row["liveness_age_s"]
        lines.append(
            f"  running   {row['id']}" + (f"  quiet {age:.0f}s" if age is not None else "")
        )
    lines.append(f"LLM:        {'paused' if doc['llm']['paused'] else 'active'}")
    pending = doc["inbox"]["pending"]
    lines.append(
        f"Inbox:      {len(pending)} pending" + (f" ({', '.join(pending[:5])})" if pending else "")
    )
    for act in doc["recent_actions"][-5:]:
        lines.append(f"  {act['at']}  {act['actor']}  {act['kind']}: {act['text']}")
    return "\n".join(lines)
