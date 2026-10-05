"""Live run summary for expensive runs (engine-owned, not user-configurable).

Once a run's cumulative ACTUAL cost crosses ``SUMMARY_COST_THRESHOLD_USD`` the engine
asks a small, cheap model (Haiku) to keep a short human-readable digest of the run
current: what is done, what is in flight, what is next, and what looks risky. The digest
is refreshed every ``SUMMARY_REFRESH_EVERY_SETTLED_TASKS`` newly settled tasks and once
more when the run ends, and lives at ``<run_dir>/summary/summary.md`` (+ ``summary.json``
metadata) where ``ao summary`` and the dashboard read it.

Design constraints:

* **Not configurable.** The threshold, cadence, model and cost cap are module constants,
  deliberately absent from workflow/project config (product decision: a fixed safety-net
  view, not a knob).
* **Never affects the run.** Every failure (executor error, missing output, thread
  crash) is swallowed and logged; the summary is advisory and can never fail, delay or
  re-route a task. Refreshes run on a daemon thread so the scheduler never waits on them.
* **Marginal cost.** Refreshes stop once summary spend reaches
  ``SUMMARY_MAX_COST_FRACTION`` of the run's cost (the final summary is exempt), and the
  context handed to the model is a compact digest, not transcripts.
* **NFR-1.** The engine never reads the summary body: the model reads its own previous
  summary as an input artifact and writes the next one; the engine only moves files and
  records metadata.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .executors.base import Executor
from .models import AgentSpec, RunState, TaskContext, compute_run_usage_totals

logger = logging.getLogger(__name__)

# --- Fixed, engine-owned policy (intentionally NOT exposed through any config) ---------
SUMMARY_COST_THRESHOLD_USD = 5.0
SUMMARY_REFRESH_EVERY_SETTLED_TASKS = 5
# Alias = always the latest Haiku (repo convention: prefer aliases over pinned ids).
SUMMARY_MODEL = "haiku"
SUMMARY_MAX_TURNS = 4
SUMMARY_TIMEOUT_SECONDS = 180
# Interim refreshes pause once summary spend reaches this share of the run's cost.
SUMMARY_MAX_COST_FRACTION = 0.02

SUMMARY_DIR_NAME = "summary"
SUMMARY_FILE = "summary.md"
SUMMARY_META_FILE = "summary.json"
_NEXT_FILE = "summary-next.md"
_PREVIOUS_FILE = "previous.md"
_CONTEXT_FILE = "context.json"
_INSTRUCTION_FILE = "instruction.md"

# Statuses after which a task will not change again within this run session.
_SETTLED_STATUSES = frozenset(
    {"succeeded", "failed", "skipped", "cancelled", "timed_out", "not_taken"}
)
_MAX_LISTED_PENDING = 40

_INSTRUCTION = """\
You maintain a live status digest of a long, expensive multi-agent workflow run.

Inputs: `context.json` (the run's current state: totals, per-task status and cost, the
tasks still to come) and, when it exists, `previous.md` (your previous digest).

Write the new digest as Markdown to the single output path you were given. Keep it under
~40 lines, plain and skimmable, with exactly these sections:

## Done so far
## In progress
## Next (roadmap)
## Risks & anomalies

Rules: be factual - use only what is in the inputs, never invent results. Prefer the
previous digest's wording where nothing changed. Mention cost (run total, and any single
task that dominates it) in one line at the top. Do not read or modify any other file.
"""


def build_summary_agent() -> AgentSpec:
    """The built-in cheap-model agent used for every summary call (never from agents.json)."""
    return AgentSpec(
        executor="claude_cli",
        command_template=["claude", "--dangerously-skip-permissions", "-p", "{prompt}"],
        model=SUMMARY_MODEL,
        max_turns=SUMMARY_MAX_TURNS,
        # Read + Write of the digest is all it needs; no shell, network or sub-agents.
        forced_disallowed_tools=[
            "Bash",
            "BashOutput",
            "KillShell",
            "KillBash",
            "WebFetch",
            "WebSearch",
            "Task",
            "Agent",
            "ScheduleWakeup",
            "CronCreate",
            "Monitor",
        ],
    )


class RunSummarizer:
    """Refreshes ``<run_dir>/summary/summary.md`` for a run above the cost threshold."""

    def __init__(
        self,
        executor: Executor,
        *,
        agent: AgentSpec | None = None,
        threshold_usd: float = SUMMARY_COST_THRESHOLD_USD,
        refresh_every: int = SUMMARY_REFRESH_EVERY_SETTLED_TASKS,
    ) -> None:
        # threshold_usd / refresh_every are constructor args ONLY so tests can use small
        # numbers; no production caller (CLI, config, env) passes them.
        self._executor = executor
        self._agent = agent or build_summary_agent()
        self._threshold_usd = threshold_usd
        self._refresh_every = max(1, refresh_every)
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._last_settled = 0
        self._summary_cost_usd = 0.0
        self._calls = 0

    # -- public API (called from the engine's main thread) ----------------------------

    def on_task_settled(self, state: RunState, run_dir: str, pending_ids: list[str]) -> None:
        """Maybe start a background refresh. Cheap no-op below threshold / off-cadence."""
        try:
            self._maybe_start(state, run_dir, pending_ids, final=False)
        except Exception:  # advisory feature: never let it touch the run
            logger.exception("run summary: scheduling failed")

    def finalize(self, state: RunState, run_dir: str, pending_ids: list[str]) -> None:
        """Wait for any in-flight refresh, then write the end-of-run summary (blocking)."""
        try:
            self._join()
            self._maybe_start(state, run_dir, pending_ids, final=True)
            self._join()
        except Exception:
            logger.exception("run summary: final refresh failed")

    # -- internals --------------------------------------------------------------------

    def _join(self) -> None:
        thread = self._thread
        if thread is not None:
            thread.join(timeout=SUMMARY_TIMEOUT_SECONDS + 30)

    def _maybe_start(
        self, state: RunState, run_dir: str, pending_ids: list[str], *, final: bool
    ) -> None:
        run_cost = compute_run_usage_totals(state).cost_usd
        summary_dir = Path(run_dir) / SUMMARY_DIR_NAME
        # Latch: once a summary exists (e.g. before a resume) the run is "expensive" for
        # good, even if a resumed session's totals restart lower.
        if run_cost < self._threshold_usd and not (summary_dir / SUMMARY_FILE).exists():
            return
        settled = sum(1 for t in state.tasks.values() if t.status in _SETTLED_STATUSES)
        if not final:
            if settled - self._last_settled < self._refresh_every:
                return
            if self._summary_cost_usd >= SUMMARY_MAX_COST_FRACTION * run_cost:
                return
        elif settled == self._last_settled and (summary_dir / SUMMARY_FILE).exists():
            # Nothing settled since the last refresh -- but the end-of-run status may
            # still differ, so only skip when the stored one already says final.
            if self._stored_final(summary_dir):
                return
        if not self._lock.acquire(blocking=False):
            return  # single-flight: a refresh is already running
        self._last_settled = settled
        context = self._build_context(state, run_cost, pending_ids, final)
        args = (run_dir, context, run_cost, settled, final)
        if final:
            try:
                self._refresh(*args)
            finally:
                self._lock.release()
            return
        self._thread = threading.Thread(
            target=self._refresh_and_release, args=args, name="ao-run-summary", daemon=True
        )
        self._thread.start()

    @staticmethod
    def _stored_final(summary_dir: Path) -> bool:
        try:
            meta = json.loads((summary_dir / SUMMARY_META_FILE).read_text(encoding="utf-8"))
            return bool(meta.get("final"))
        except (OSError, ValueError):
            return False

    def _refresh_and_release(self, *args: Any) -> None:
        try:
            self._refresh(*args)
        finally:
            self._lock.release()

    @staticmethod
    def _build_context(
        state: RunState, run_cost: float, pending_ids: list[str], final: bool
    ) -> dict[str, Any]:
        totals = compute_run_usage_totals(state)
        tasks = []
        for tid, ts in state.tasks.items():
            if ts.status == "pending":
                continue  # listed compactly under `pending` instead
            entry: dict[str, Any] = {
                "id": tid,
                "status": ts.status,
                "attempts": ts.attempts,
                "cost_usd": round(ts.cumulative_cost_usd, 4),
            }
            if ts.route:
                entry["route"] = ts.route
            tasks.append(entry)
        return {
            "run_id": state.run_id,
            "run_status": state.status,
            "final": final,
            "run_cost_usd": round(run_cost, 4),
            "usage_totals": totals.model_dump(),
            "tasks": tasks,
            "pending": pending_ids[:_MAX_LISTED_PENDING],
            "pending_total": len(pending_ids),
        }

    def _refresh(
        self, run_dir: str, context: dict[str, Any], run_cost: float, settled: int, final: bool
    ) -> None:
        try:
            summary_dir = Path(run_dir) / SUMMARY_DIR_NAME
            summary_dir.mkdir(parents=True, exist_ok=True)
            current = summary_dir / SUMMARY_FILE
            previous = summary_dir / _PREVIOUS_FILE
            next_path = summary_dir / _NEXT_FILE
            context_path = summary_dir / _CONTEXT_FILE
            instruction_path = summary_dir / _INSTRUCTION_FILE
            capture_dir = summary_dir / "capture"

            inputs = [str(context_path)]
            if current.exists():
                shutil.copyfile(current, previous)
                inputs.append(str(previous))
            context_path.write_text(json.dumps(context, indent=2), encoding="utf-8")
            instruction_path.write_text(_INSTRUCTION, encoding="utf-8")
            next_path.unlink(missing_ok=True)

            ctx = TaskContext(
                run_id=context["run_id"],
                task_id="run-summary",
                agent=self._agent,
                instruction_path=str(instruction_path),
                input_paths=inputs,
                output_paths=[str(next_path)],
                repo_paths={},
                timeout_seconds=SUMMARY_TIMEOUT_SECONDS,
                cwd=str(summary_dir),
                output_dir=str(capture_dir),
            )
            result = self._executor.execute(ctx)
            self._summary_cost_usd += result.cost_usd or 0.0
            self._calls += 1
            if result.status == "succeeded" and next_path.exists():
                os.replace(next_path, current)
                self._write_meta(summary_dir, run_cost, settled, final)
            else:
                logger.warning("run summary: refresh produced no output (%s)", result.status)
        except Exception:
            logger.exception("run summary: refresh failed")

    def _write_meta(self, summary_dir: Path, run_cost: float, settled: int, final: bool) -> None:
        meta = {
            "updated_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "final": final,
            "model": SUMMARY_MODEL,
            "run_cost_usd": round(run_cost, 4),
            "summary_cost_usd": round(self._summary_cost_usd, 4),
            "calls": self._calls,
            "settled_tasks": settled,
            "threshold_usd": self._threshold_usd,
        }
        tmp = summary_dir / (SUMMARY_META_FILE + ".tmp")
        tmp.write_text(json.dumps(meta, indent=2), encoding="utf-8")
        os.replace(tmp, summary_dir / SUMMARY_META_FILE)


def read_summary(run_dir: str | Path) -> dict[str, Any]:
    """Read-side helper for ``ao summary`` and the dashboard: ``{available, text, meta}``.

    Reading is deliberately outside ``RunSummarizer`` (the engine never reads the body).
    A missing/garbled file yields ``available: False`` rather than raising -- most runs
    never cross the threshold and so never have a summary.
    """
    summary_dir = Path(run_dir) / SUMMARY_DIR_NAME
    try:
        text = (summary_dir / SUMMARY_FILE).read_text(encoding="utf-8")
    except OSError:
        return {"available": False, "text": "", "meta": None}
    try:
        meta = json.loads((summary_dir / SUMMARY_META_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        meta = None
    return {"available": True, "text": text, "meta": meta}
