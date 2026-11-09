"""Sentinel LLM powers: policy gate, directives, planner trigger (E-Sn7tL2 T-Sn9Pow).

HLD §8.4 / §9 / ADR-0024. Everything here is policy over the existing control inbox and the shared
graph checker (``graph_check``): a proposal by an LLM generation (actor ``sentinel-gNN``) is
checked by :class:`PowerGate` *before* the tick applies it; a rejection is quarantined like any
other (rule id ``PWR-*``) and counts toward the consecutive-rejection auto-pause.

Pure over ``RunState`` + an injected ``now``; the only I/O is two append-only files under the run
directory (``sentinel/powers.jsonl``: per-hour rate accounting, ``control/directives.jsonl``: the
directives a checkpoint must acknowledge). No ``time``/``datetime.now`` calls.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any, Final

from . import graph_check
from .models import SentinelLlmSpec, TaskRunState

POWERS_FILE: Final = "powers.jsonl"
DIRECTIVES_FILE: Final = "directives.jsonl"
SENTINEL_DIR: Final = "sentinel"
CONTROL_DIR: Final = "control"
HOUR_SECONDS: Final = 3600

# Rule ids (inbox-rejected sidecar ``rule_id``).
RULE_ADVISE: Final = "PWR-ADVISE"
RULE_KIND: Final = "PWR-KIND"
RULE_NO_KILL: Final = "PWR-KILL-DISABLED"
RULE_KILL_RATIONALE: Final = "PWR-KILL-RATIONALE"
RULE_KILL_YOUNG: Final = "PWR-KILL-MIN-RUNNING"
RULE_KILL_RATE: Final = "PWR-KILL-RATE"
RULE_KILL_PROTECTED: Final = "PWR-KILL-PROTECTED"
RULE_KILL_TARGET: Final = "PWR-KILL-TARGET"
RULE_INJECT_RATE: Final = "PWR-INJECT-RATE"
RULE_DIRECTIVE: Final = "PWR-DIRECTIVE"

KILL_RATIONALE_MIN_CHARS: Final = 40
DIRECTIVE_SEVERITIES: Final = ("info", "warn", "alarm")
DIRECTIVE_TEXT_MAX: Final = 4000

ADVISE_KINDS: Final = frozenset({"note", "directive", "request_hold", "request_cancel"})
"""Kinds an LLM generation may propose with ``powers: advise`` (HLD §8.4)."""
ACT_KINDS: Final = ADVISE_KINDS | {"inject", "edit_pending", "kill"}
"""``act`` adds the graph/kill powers; everything else (set, resume_llm, release_backlog,
cancel, pause_llm) is operator-only."""
GRAPH_EDIT_KINDS: Final = frozenset({"inject", "edit_pending"})

# Planner (routed-runner) trigger id: kept here, consumed by ``sentinel_gen``.
TRIGGER_IDLE_SLOTS: Final = "idle_slots_no_backlog"

PLANNER_CONTRACT_SECTION: Final = (
    "\n## Planner role (routed-runner, no checkpoints)\n"
    "This run has no overseer checkpoints: **you are the L2 planner**. You are woken by "
    "`idle_slots_no_backlog` when slots sit idle with nothing ready and no backlog. Read "
    "`digest.json` (task counts, slots, utilisation) and `charter.json` if present, decide whether "
    "more independent work would genuinely advance the user's prompt, and if so propose it as "
    "`inject` entries (one `ao.ctl/v1` file per proposal, `actor` = `sentinel-gNN`). Injected "
    "tasks carry a sentinel origin. Rules: at most `max_injections_per_hour` injections per hour; "
    "new tasks must depend only on existing task ids, must not re-do finished work, and must "
    "reuse the agents already in this workflow. If nothing useful remains, write a handoff that "
    "says so and propose nothing - an idle slot is cheaper than redundant work.\n"
)


def is_llm_actor(actor: object) -> bool:
    """``sentinel-gNN`` actors are generations; everything else is a human/CLI/dashboard writer."""
    from .models import SENTINEL_GEN_TASK_PREFIX

    return isinstance(actor, str) and actor.startswith(SENTINEL_GEN_TASK_PREFIX)


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        return None


def _running_seconds(ts: TaskRunState, now: datetime) -> float:
    if ts.running_seconds is not None:
        return ts.running_seconds
    started = _parse(ts.started_at)
    return max((now - started).total_seconds(), 0.0) if started is not None else 0.0


class PowerGate:
    """Per-run policy for LLM proposals. ``__call__`` raises ``graph_check.EditRejected``.

    *protected* answers "is this task a barrier or an emitter?" (the engine owns that knowledge);
    kills of such tasks are never allowed from the LLM. Rate accounting is a bounded read of
    ``sentinel/powers.jsonl`` so it survives resume and is independent of in-memory state.
    """

    def __init__(
        self,
        spec: SentinelLlmSpec,
        run_dir: Path,
        protected: Callable[[str], bool],
    ) -> None:
        self.spec = spec
        self.sdir = run_dir / SENTINEL_DIR
        self.protected = protected

    # -- rate accounting ---------------------------------------------------------------------

    def _recent(self, kind: str, now: datetime) -> int:
        try:
            lines = (self.sdir / POWERS_FILE).read_text(encoding="utf-8").splitlines()
        except OSError:
            return 0
        count = 0
        for line in lines:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            at = _parse(rec.get("at")) if isinstance(rec, dict) else None
            if (
                at is not None
                and rec.get("kind") == kind
                and 0 <= (now - at).total_seconds() < HOUR_SECONDS
            ):
                count += int(rec.get("n", 1))
        return count

    def _record(self, kind: str, entry: dict[str, Any], now: datetime, n: int = 1) -> None:
        self.sdir.mkdir(parents=True, exist_ok=True)
        rec = {
            "at": now.isoformat(),
            "kind": kind,
            "n": n,
            "id": entry.get("id"),
            "actor": entry.get("actor"),
            "task_id": (entry.get("payload") or {}).get("task_id"),
        }
        with (self.sdir / POWERS_FILE).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, sort_keys=True) + "\n")

    # -- the gate ----------------------------------------------------------------------------

    def __call__(self, entry: dict[str, Any], state: Any, now: datetime) -> None:
        if not is_llm_actor(entry.get("actor")):
            return
        kind = entry["kind"]
        allowed = ACT_KINDS if self.spec.powers == "act" else ADVISE_KINDS
        if kind not in allowed:
            if kind in ACT_KINDS:
                raise graph_check.EditRejected(
                    RULE_ADVISE, f"{kind!r} needs sentinel.llm.powers=act (is advise)"
                )
            raise graph_check.EditRejected(RULE_KIND, f"{kind!r} is not an LLM-proposable kind")
        if kind == "kill":
            self._check_kill(entry, state, now)
        elif kind in GRAPH_EDIT_KINDS:
            if not self.spec.allow_graph_edits:
                raise graph_check.EditRejected(
                    graph_check.RULE_DISABLED, "sentinel.llm.allow_graph_edits is false"
                )
            if kind == "inject":
                self._check_inject(entry, now)
            else:
                self._record("edit_pending", entry, now)
        elif kind == "directive":
            check_directive_entry(entry)

    def _check_inject(self, entry: dict[str, Any], now: datetime) -> None:
        tasks = (entry.get("payload") or {}).get("tasks")
        n = len(tasks) if isinstance(tasks, list) and tasks else 1
        if self._recent("inject", now) + n > self.spec.max_injections_per_hour:
            raise graph_check.EditRejected(
                RULE_INJECT_RATE,
                f"max_injections_per_hour={self.spec.max_injections_per_hour} reached",
            )
        self._record("inject", entry, now, n)

    def _check_kill(self, entry: dict[str, Any], state: Any, now: datetime) -> None:
        spec = self.spec
        if not spec.allow_kill:
            raise graph_check.EditRejected(RULE_NO_KILL, "sentinel.llm.allow_kill is false")
        rationale = entry.get("rationale")
        if not isinstance(rationale, str) or len(rationale.strip()) < KILL_RATIONALE_MIN_CHARS:
            raise graph_check.EditRejected(
                RULE_KILL_RATIONALE,
                f"an LLM kill needs a rationale of >= {KILL_RATIONALE_MIN_CHARS} chars",
            )
        tid = (entry.get("payload") or {}).get("task_id")
        ts = state.tasks.get(tid) if isinstance(tid, str) else None
        if not isinstance(tid, str) or ts is None or ts.status != "running":
            raise graph_check.EditRejected(RULE_KILL_TARGET, f"task {tid!r} is not running")
        if self.protected(tid):
            raise graph_check.EditRejected(
                RULE_KILL_PROTECTED, f"task {tid!r} is a barrier/emitter: never LLM-killed"
            )
        if _running_seconds(ts, now) < spec.llm_kill_min_running_minutes * 60:
            raise graph_check.EditRejected(
                RULE_KILL_YOUNG,
                f"task {tid!r} has run < {spec.llm_kill_min_running_minutes} min",
            )
        if self._recent("kill", now) >= spec.max_llm_kills_per_hour:
            raise graph_check.EditRejected(
                RULE_KILL_RATE, f"max_llm_kills_per_hour={spec.max_llm_kills_per_hour} reached"
            )
        self._record("kill", entry, now)


# ---------------------------------------------------------------------------------------------
# Directives (FR-16): raised by the sentinel (LLM or operator), acknowledged by the checkpoint
# ---------------------------------------------------------------------------------------------


def check_directive_entry(entry: dict[str, Any]) -> None:
    payload = entry.get("payload") or {}
    text = payload.get("text")
    if not isinstance(text, str) or not text.strip() or len(text) > DIRECTIVE_TEXT_MAX:
        raise graph_check.EditRejected(
            RULE_DIRECTIVE, f"directive requires payload.text (1..{DIRECTIVE_TEXT_MAX} chars)"
        )
    sev = payload.get("severity", "info")
    if sev not in DIRECTIVE_SEVERITIES:
        raise graph_check.EditRejected(
            RULE_DIRECTIVE, f"directive severity must be one of {list(DIRECTIVE_SEVERITIES)}"
        )
    rationale = entry.get("rationale")
    if not isinstance(rationale, str) or not rationale.strip():
        raise graph_check.EditRejected(RULE_DIRECTIVE, "directive requires a rationale")


def append_directive(control_dir: Path, entry: dict[str, Any], at: str) -> None:
    """Record a directive (append-only JSONL; the entry id is the directive id)."""
    check_directive_entry(entry)
    payload = entry.get("payload") or {}
    control_dir.mkdir(parents=True, exist_ok=True)
    rec = {
        "id": entry["id"],
        "at": at,
        "actor": entry["actor"],
        "severity": payload.get("severity", "info"),
        "text": payload["text"],
        "rationale": entry["rationale"],
    }
    with (control_dir / DIRECTIVES_FILE).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, sort_keys=True) + "\n")


def read_directives(control_dir: Path) -> list[dict[str, Any]]:
    """All recorded directives in file order (malformed lines are skipped)."""
    try:
        lines = (control_dir / DIRECTIVES_FILE).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for line in lines:
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict) and isinstance(rec.get("id"), str) and rec["id"] not in seen:
            seen.add(rec["id"])
            out.append(rec)
    return out


# ---------------------------------------------------------------------------------------------
# Planner trigger (routed-runner L2)
# ---------------------------------------------------------------------------------------------


def backlog_is_empty(run_dir: Path) -> bool:
    """True when ``control/backlog.json`` is absent or lists no entries (T-Sn7Bkl owns the file)."""
    try:
        data = json.loads((run_dir / CONTROL_DIR / "backlog.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return True
    items = data.get("items") if isinstance(data, dict) else data
    return not items
