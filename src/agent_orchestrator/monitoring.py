"""Pluggable agent-based monitoring & self-healing (epic E-XyfjuZ).

Mirrors the `Breaker`/`Executor`/`BudgetManager` dependency-injection style already used
across the engine: a small ABC (`Monitor`) plus two concrete implementations, consulted by
`engine.py` at two boundaries:

  - **Consult Point A** (at the existing `evaluate_breakers` boundary, breakers.py): when
    every breaker that newly tripped at one boundary is declared `mode: "recommend"`
    (`CircuitBreakerSpec.mode`, default `"hard"`), the engine asks the Monitor, per tripped
    breaker in declared order, whether to `extend` (bounded, reuses
    `breakers.apply_breaker_extension`) or `halt`.
  - **Consult Point B** (task settle, opt-in via `monitoring.self_heal` config): when a task
    exhausts its `RetryPolicy` and settles `"failed"`, the engine asks the Monitor whether to
    `retry` (bounded, one extra requeue) or `accept_failure`.

Design invariants (see `docs-md/ai-epics/E-XyfjuZ-agent-monitoring-self-healing.md` Design
Decisions D1-D9 for the full rationale):

  - Every DTO here is shallow — ids/conditions/counts/bounded text only, never a full
    transcript or artifact/instruction payload (NFR-1-style). `TaskFailureSummary` introduces
    NO new file-content read at all: `terminal_reason`/`errors`/`stderr_tail` are all derived
    from the executor's own already-bounded `TaskResult.error` (<=500 chars) — an early-gate
    reviewer pass confirmed a separate `stderr.txt` re-read would be redundant, since the
    executor (`executors/claude_cli.py`) already slices the tail of the same file into
    `TaskResult.error` (Design Decision D5, revised post-review).
  - `RuleBasedMonitor` is pure/deterministic/zero-cost: no I/O, no clock, no randomness —
    every decision is a pure function of the passed-in summary DTO (which itself carries
    `prior_extensions`/`prior_heal_retries` so a stateless monitor can still implement
    "once, then stop" policies without keeping its own mutable state).
  - `AgentMonitor` invokes the EXISTING `Executor`/`TaskContext` machinery — the identical
    contract every regular workflow task uses — and treats ANY non-`"succeeded"` executor
    result, missing/oversized/malformed verdict file, or a verdict failing strict shape
    validation as an invalid verdict: it falls back to the safe default
    (`SAFE_DEFAULT_BREAKER_VERDICT` / `SAFE_DEFAULT_HEAL_VERDICT`) and never raises. It makes
    exactly ONE `executor.execute()` call per consult — no internal retry loop, no
    quota-wait loop — so a quota-exhausted, rate-limited, or otherwise-failing monitor
    invocation returns immediately (falls back to the safe default) rather than hanging or
    blocking the run (early-gate architect finding, confirmed structurally safe by design).
  - Known MVP limitation (early-gate architect finding, documented not silently absorbed):
    `AgentMonitor`'s `executor.execute()` call is OUT-OF-BAND relative to
    `Orchestrator._run_with_retries` — it never touches `RunState.tasks`/`injected_tasks`
    (verified: `Executor.execute()` is fully decoupled from `RunState`, which only the
    engine's own dispatch loop ever mutates), which is exactly what makes it safe to call
    synchronously from a `Monitor` implementation without corrupting breaker/usage math —
    but it ALSO means a real agent-based monitor's token/dollar cost is NOT gated by the
    run's token budget and NOT counted in `run_cost_usd`/`budget_counters`. Mitigated in
    MVP by `max_monitor_calls_per_run` (bounds the *count* of real invocations even though
    not their cost); full budget-system integration for monitor calls is explicitly Non-MVP.
  - Safety BOUNDS (`max_extensions_per_breaker`, `max_heal_retries_per_task`,
    `max_monitor_calls_per_run`) are NOT enforced here — they are enforced centrally in
    `engine.py` so a misbehaving/compromised `Monitor` implementation cannot exceed them
    (mirrors `breakers.record_trip`'s own "records, does not decide whether to record" split).
  - Instruction text for `AgentMonitor` is a baked Python string constant, not a
    `specs/examples/*.md` path: `pyproject.toml`'s wheel packaging
    (`packages = ["src/agent_orchestrator"]`) does not ship `specs/`, so a repo-relative
    instruction path would silently break for any `uv tool install`ed `ao` binary whose
    `workspace_root` isn't the repo itself.
"""

from __future__ import annotations

import json
import os
import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from .artifacts import ArtifactStore, read_control
from .errors import ControlFileError
from .executors.base import Executor
from .models import AgentSpec, TaskContext, TaskResult

# ---------------------------------------------------------------------------
# Tunable defaults (named constants -- no magic literals per CLAUDE.md)
# ---------------------------------------------------------------------------

DEFAULT_HEAL_WAIT_SECONDS: float = 30.0
DEFAULT_MONITOR_TIMEOUT_SECONDS: int = 300
# Bound on the compact stderr tail included in a TaskFailureSummary (brief-specified cap).
STDERR_TAIL_MAX_CHARS: int = 2000

# Regex patterns (case-insensitive, OR-joined) classifying a task failure as "transient"
# for RuleBasedMonitor's self-heal policy -- covers network/timeout/connection-reset/5xx/
# JSON-decode failures per the epic brief. A workflow's `monitoring.transient_patterns`
# config ADDS to this list (never replaces it) -- see project_config.MonitoringConfig.
DEFAULT_TRANSIENT_PATTERNS: list[str] = [
    r"connection\s+reset",
    r"connection\s+refused",
    r"connection\s+aborted",
    r"econnreset",
    r"network\s+is\s+unreachable",
    r"time(?:d)?\s*out",
    r"\btimeout\b",
    r"\b5\d{2}\b",  # 5xx HTTP status codes
    r"bad\s+gateway",
    r"service\s+unavailable",
    r"gateway\s+time-?out",
    r"json\s*decode",
    r"jsondecodeerror",
    r"temporary\s+failure",
    r"broken\s+pipe",
]


# ---------------------------------------------------------------------------
# Ephemeral DTOs (never persisted directly; the engine mirrors the relevant bits into
# RunState.monitor_decisions as a MonitorDecisionRecord -- see models.py)
# ---------------------------------------------------------------------------


class BreakerTripSummary(BaseModel):
    """Shallow, id/count-only summary of ONE newly-tripped `mode: "recommend"` breaker
    (Consult Point A). Never carries transcript or artifact payload content."""

    breaker_id: str
    condition: str
    action: str
    detail: dict = {}
    # How many times THIS breaker id has already been extended by a monitor this run
    # (engine-tracked via RunState.monitor_breaker_extensions) -- lets a stateless Monitor
    # implement "extend once then halt" without keeping its own mutable state.
    prior_extensions: int = 0


class BreakerVerdict(BaseModel):
    """A Monitor's answer to a `BreakerTripSummary` consult."""

    decision: Literal["extend", "halt"]
    # None => the caller (engine.py) applies apply_breaker_extension(..., extend_by_same=True)
    # -- extend by the breaker's own original threshold amount (the common "same" case).
    # A positive number => extend_by_seconds explicitly (extend_by_same=False).
    extend_by_seconds: float | None = None
    reason: str = ""


class TaskFailureSummary(BaseModel):
    """Compact, shallow summary of a task that exhausted its retries and settled
    `"failed"` (Consult Point B). `terminal_reason`/`errors`/`stderr_tail` all derive from
    the executor's own already-bounded `TaskResult.error` (<=500 chars) — NO new
    file-content read is introduced anywhere in this module (revised post early-gate
    review: `executors/claude_cli.py` already slices the tail of `stderr.txt` into
    `TaskResult.error`, so a second, separate re-read of the same file would be pure
    redundancy). `stderr_tail` keeps its own name/bound (<=2000 chars, see
    `STDERR_TAIL_MAX_CHARS`) purely so the DTO's shape matches the compact-summary
    contract even though today it never exceeds `TaskResult.error`'s own, tighter bound."""

    task_id: str
    attempt_count: int
    terminal_reason: str | None = None
    errors: list[str] = []
    stderr_tail: str = ""
    exit_code: int | None = None
    # How many heal retries have already been used for this task id this run (engine-
    # tracked via RunState.monitor_heal_retries) -- same stateless-policy pattern as
    # BreakerTripSummary.prior_extensions.
    prior_heal_retries: int = 0


class HealVerdict(BaseModel):
    """A Monitor's answer to a `TaskFailureSummary` consult."""

    decision: Literal["retry", "accept_failure"]
    wait_seconds: float = 0.0
    reason: str = ""


# ---------------------------------------------------------------------------
# Monitor ABC
# ---------------------------------------------------------------------------


class Monitor(ABC):
    """Pluggable monitor (mirrors the `Breaker`/`Executor`/`BudgetManager` DI style).

    Implementations SHOULD never raise out of either method — an internal error should be
    caught and translated into the safe-default verdict. `engine.py`'s consult call sites
    additionally wrap every call in a defensive `try/except` (NFR-2: a monitor bug must
    never make a run less safe than today), but implementations should not rely on that as
    their only safety net.
    """

    name: str

    @abstractmethod
    def decide_breaker_trip(self, trip: BreakerTripSummary, *, run_id: str) -> BreakerVerdict:
        """Decide whether to extend or halt on ONE newly-tripped recommend-mode breaker."""
        ...

    @abstractmethod
    def decide_task_failure(self, summary: TaskFailureSummary, *, run_id: str) -> HealVerdict:
        """Decide whether to retry or accept a task's failure after retries are exhausted."""
        ...


# Safe-default verdicts — the fallback used whenever a monitor's answer is unavailable or
# invalid (AgentMonitor internally; engine.py also reuses these for its own defensive
# try/except around every monitor call, so there is exactly one definition of "safe").
SAFE_DEFAULT_BREAKER_VERDICT = BreakerVerdict(
    decision="halt",
    reason="monitor unavailable or invalid verdict; falling back to the safe default",
)
SAFE_DEFAULT_HEAL_VERDICT = HealVerdict(
    decision="accept_failure",
    reason="monitor unavailable or invalid verdict; falling back to the safe default",
)


# ---------------------------------------------------------------------------
# RuleBasedMonitor — deterministic, zero-cost default
# ---------------------------------------------------------------------------


class RuleBasedMonitor(Monitor):
    """Deterministic, zero-cost default monitor: no I/O, no clock, no randomness.

    Breaker policy: extend once (by the breaker's own original threshold amount) then
    halt. Failure policy: retry once, after `wait_seconds`, when the failure text matches a
    transient pattern; otherwise accept the failure.
    """

    name = "rules"

    def __init__(
        self,
        *,
        wait_seconds: float = DEFAULT_HEAL_WAIT_SECONDS,
        extra_transient_patterns: list[str] | None = None,
    ) -> None:
        self._wait_seconds = wait_seconds
        patterns = [*DEFAULT_TRANSIENT_PATTERNS, *(extra_transient_patterns or [])]
        self._transient_re = re.compile("|".join(patterns), re.IGNORECASE)

    def decide_breaker_trip(self, trip: BreakerTripSummary, *, run_id: str) -> BreakerVerdict:
        del run_id  # unused -- pure/stateless by design
        if trip.prior_extensions == 0:
            return BreakerVerdict(
                decision="extend",
                extend_by_seconds=None,  # "same" -- extend by the original threshold amount
                reason="first extension for this breaker id this run",
            )
        return BreakerVerdict(
            decision="halt",
            reason="this breaker id has already been extended once this run",
        )

    def decide_task_failure(self, summary: TaskFailureSummary, *, run_id: str) -> HealVerdict:
        del run_id  # unused -- pure/stateless by design
        text = " ".join([summary.terminal_reason or "", *summary.errors, summary.stderr_tail])
        if summary.prior_heal_retries == 0 and self._transient_re.search(text):
            return HealVerdict(
                decision="retry",
                wait_seconds=self._wait_seconds,
                reason="failure text matched a transient pattern",
            )
        reason = (
            "this task has already used its heal retry this run"
            if summary.prior_heal_retries > 0
            else "no transient pattern matched"
        )
        return HealVerdict(decision="accept_failure", reason=reason)


# ---------------------------------------------------------------------------
# AgentMonitor — the "agent-based" headline implementation
# ---------------------------------------------------------------------------

# Baked instruction templates (Design Decision D6 in the epic doc) -- never a
# specs/examples/*.md path, since the packaged wheel does not ship specs/.
_BREAKER_TRIP_INSTRUCTION = """\
# Monitor task: circuit-breaker trip guardrail decision

A workflow run's circuit breaker has tripped. You are a SHALLOW supervisor -- you do not
need to understand the underlying workflow, its code, or its artifacts. You only need to
read the compact JSON context file and decide whether continuing is reasonable.

Read the context JSON at the input path. It contains: breaker_id, condition, action,
detail (counts/thresholds only), and prior_extensions (how many times this SAME breaker
has already been extended this run).

Decide exactly one of:
  - "extend": it is reasonable to raise this breaker's limit once and let the run continue.
    Only recommend this if prior_extensions is 0 -- a breaker that has already been
    extended once should generally halt rather than be extended indefinitely.
  - "halt": the run should stop here.

Write EXACTLY one JSON object to the output path, and nothing else:
  {"decision": "extend" | "halt", "extend_by_seconds": <number or null>, "reason": "<short string>"}

extend_by_seconds: use null to extend by the same amount as the breaker's original
threshold (the common case), or a specific positive number for a different amount.
"""

_TASK_FAILURE_INSTRUCTION = """\
# Monitor task: task-failure self-healing decision

A workflow task exhausted its retry attempts and failed. You are a SHALLOW supervisor --
you do not need to understand the task's own instructions or read its outputs. You only
need to read the compact JSON failure summary and decide whether one more retry is likely
to help.

Read the context JSON at the input path. It contains: task_id, attempt_count,
terminal_reason, errors, a bounded stderr_tail, exit_code, and prior_heal_retries (how many
extra heal retries this SAME task has already used this run).

Decide exactly one of:
  - "retry": the failure looks transient (e.g. a network error, timeout, connection reset,
    a 5xx response, or a malformed/partial API response) and one more attempt is likely to
    succeed. Only recommend this if prior_heal_retries is 0.
  - "accept_failure": the failure looks like a real, persistent problem, or this task has
    already used its heal retry -- do not retry again.

Write EXACTLY one JSON object to the output path, and nothing else:
  {"decision": "retry" | "accept_failure", "wait_seconds": <number>, "reason": "<short string>"}

wait_seconds: how long to wait before retrying (ignored when decision is accept_failure).
"""


class AgentMonitor(Monitor):
    """Invokes a named `agents.json` agent through the EXISTING `Executor`/`TaskContext`
    machinery (the identical contract every regular workflow task uses) and expects a
    strict JSON verdict file back.

    Every consult writes a baked instruction file + the compact context JSON under
    ``.orchestrator/runs/<run_id>/monitor/<call_subject>/`` and reads back
    ``verdict.json`` from the same directory via the shared, bounded
    ``artifacts.read_control`` reader -- the same audited surface loop gates/routers/
    verdict-breakers already use. ANY of the following is treated as an invalid verdict
    and falls back to the safe default (never raises): a non-``"succeeded"`` executor
    result, a missing/oversized/malformed verdict file, or a verdict whose shape fails
    validation (unknown ``decision`` value, wrong field types).
    """

    def __init__(
        self,
        agent: AgentSpec,
        executor: Executor,
        store: ArtifactStore,
        *,
        name: str,
        timeout_seconds: int = DEFAULT_MONITOR_TIMEOUT_SECONDS,
    ) -> None:
        self.name = name
        self._agent = agent
        self._executor = executor
        self._store = store
        self._timeout_seconds = timeout_seconds
        # Disambiguates concurrent calls to the same subject within one process lifetime
        # (e.g. two separate breaker trips for the same breaker id across the run) so
        # capture directories never clobber each other; audit trail is never deleted
        # (repo convention: no automatic artifact removal).
        self._call_counts: dict[str, int] = {}

    def decide_breaker_trip(self, trip: BreakerTripSummary, *, run_id: str) -> BreakerVerdict:
        raw = self._consult(
            run_id=run_id,
            subject=f"breaker-{trip.breaker_id}",
            instruction_text=_BREAKER_TRIP_INSTRUCTION,
            context=trip.model_dump(),
        )
        if raw is None:
            return SAFE_DEFAULT_BREAKER_VERDICT
        try:
            decision = raw["decision"]
            if decision not in ("extend", "halt"):
                raise ValueError(f"unknown decision {decision!r}")
            extend_by = raw.get("extend_by_seconds")
            if extend_by is not None and not isinstance(extend_by, (int, float)):
                raise ValueError("extend_by_seconds must be a number or null")
            reason = str(raw.get("reason", ""))
            return BreakerVerdict(
                decision=decision,
                extend_by_seconds=float(extend_by) if extend_by is not None else None,
                reason=reason,
            )
        except (KeyError, ValueError, TypeError):
            return SAFE_DEFAULT_BREAKER_VERDICT

    def decide_task_failure(self, summary: TaskFailureSummary, *, run_id: str) -> HealVerdict:
        raw = self._consult(
            run_id=run_id,
            subject=f"heal-{summary.task_id}",
            instruction_text=_TASK_FAILURE_INSTRUCTION,
            context=summary.model_dump(),
        )
        if raw is None:
            return SAFE_DEFAULT_HEAL_VERDICT
        try:
            decision = raw["decision"]
            if decision not in ("retry", "accept_failure"):
                raise ValueError(f"unknown decision {decision!r}")
            wait_seconds = float(raw.get("wait_seconds", 0.0))
            reason = str(raw.get("reason", ""))
            return HealVerdict(decision=decision, wait_seconds=wait_seconds, reason=reason)
        except (KeyError, ValueError, TypeError):
            return SAFE_DEFAULT_HEAL_VERDICT

    def _consult(
        self, *, run_id: str, subject: str, instruction_text: str, context: dict
    ) -> dict | None:
        """Write instruction+context, invoke the executor, read back the verdict JSON.

        Returns the raw parsed dict, or ``None`` on ANY failure (executor exception,
        non-succeeded result, missing/oversized/malformed verdict file) -- callers
        translate ``None`` into their own safe-default verdict.
        """
        n = self._call_counts.get(subject, 0)
        self._call_counts[subject] = n + 1
        call_dir_rel = f".orchestrator/runs/{run_id}/monitor/call-{n}-{subject}"
        call_dir = self._store.resolve(call_dir_rel)
        Path(call_dir).mkdir(parents=True, exist_ok=True)

        instruction_path = os.path.join(call_dir, "instruction.md")
        context_path = os.path.join(call_dir, "context.json")
        verdict_path = os.path.join(call_dir, "verdict.json")
        capture_dir = os.path.join(call_dir, "capture")

        Path(instruction_path).write_text(instruction_text, encoding="utf-8")
        Path(context_path).write_text(json.dumps(context, indent=2), encoding="utf-8")

        ctx = TaskContext(
            run_id=run_id,
            task_id=f"monitor-{subject}",
            agent=self._agent,
            instruction_path=instruction_path,
            input_paths=[context_path],
            output_paths=[verdict_path],
            repo_paths={},
            timeout_seconds=self._timeout_seconds,
            cwd=self._store.resolve(self._agent.working_dir or "."),
            output_dir=capture_dir,
        )
        try:
            result = self._executor.execute(ctx)
        except Exception:
            return None
        if result.status != "succeeded":
            return None
        if not self._store.exists(verdict_path):
            return None
        try:
            return read_control(self._store, verdict_path)
        except ControlFileError:
            return None


# ---------------------------------------------------------------------------
# Failure-summary construction (Design Decision D5, revised post early-gate review)
# ---------------------------------------------------------------------------


def build_task_failure_summary(
    result: TaskResult, *, prior_heal_retries: int
) -> TaskFailureSummary:
    """Build a compact, shallow failure summary from an already-settled `TaskResult`.

    Reuses `TaskResult.error` (already an executor-produced, <=500-char bounded string --
    `executors/claude_cli.py` already slices the tail of the task's `stderr.txt`/
    `transcript.jsonl` into this field) for `terminal_reason`, the sole entry of `errors`,
    AND `stderr_tail`. This function introduces NO new file-content read: an early-gate
    reviewer pass confirmed a second, separate re-read of `stderr.txt` would be pure
    redundancy given the executor already captured and bounded the same tail. No new
    engine-side `result.json` re-parsing is introduced either.
    """
    tail = (result.error or "")[-STDERR_TAIL_MAX_CHARS:]
    return TaskFailureSummary(
        task_id=result.task_id,
        attempt_count=result.attempts,
        terminal_reason=result.error,
        errors=[result.error] if result.error else [],
        stderr_tail=tail,
        exit_code=result.exit_code,
        prior_heal_retries=prior_heal_retries,
    )
