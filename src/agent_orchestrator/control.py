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
from .models import RunState
from .run_options import OPTIONS_BY_NAME, Option

STATUS_SCHEMA: Final = "ao.ctl.status/v1"
"""``schema`` of ``ao ctl status --json`` (documented in ``docs-md/sentinel-hld.md`` §12.1)."""

ACTOR_PREFIX: Final = "cli:"
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


# -- status snapshot ---------------------------------------------------------------------------


def _pending_inbox(run_dir: Path) -> list[str]:
    inbox = run_dir / sentinel_tick.CONTROL_DIR / sentinel_tick.INBOX_DIR
    if not inbox.is_dir():
        return []
    return sorted(p.stem for p in inbox.iterdir() if p.suffix == ".json" and p.is_file())


def build_status(state: RunState, run_dir: Path) -> dict[str, Any]:
    """The ``ao ctl status --json`` document (``ao.ctl.status/v1``, HLD §12.1).

    Fields owned by later tasks (liveness age, ladder, backlog, LLM generations) are present
    with an empty/null value now, so the schema is stable and consumers need no feature probes.
    """
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
            "running": [{"id": tid, "liveness_age_s": None} for tid in running],
        },
        "ladder": {},
        "backlog": {"held": []},
        "llm": {"generation": None, "spend_usd": None, "paused": bool(sent and sent.llm_paused)},
        "inbox": {
            "pending": _pending_inbox(run_dir),
            "consumed_count": len(sent.inbox_consumed) if sent else 0,
        },
        "recent_actions": [
            {"id": n.id, "kind": "note", "actor": n.actor, "at": n.at, "text": n.text}
            for n in (sent.notes[-RECENT_ACTIONS_MAX:] if sent else [])
        ],
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
