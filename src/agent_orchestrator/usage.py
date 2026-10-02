"""Cross-run usage analytics: which (agent, model, effort) configurations cost what, and how
often their work needed rework (`ao report-usage`).

Two read-only halves, both derived from persisted state -- nothing here writes to `RunState`:

- **Provenance capture** (`dispatch_provenance`): called by the engine at each dispatch to
  record the agent/model/effort that actually ran, the producers of the task's inputs, and
  the path of its review-verdict sidecar. Pure over the task specs; no I/O.
- **Aggregation** (`collect_usage`/`aggregate_usage`): groups settled tasks by
  (agent, model, effort) and, for review tasks, attributes the structured verdict
  (`review-verdict.json`, written by the reviewer next to `review.md`) to the tasks whose
  work was reviewed. The sidecar is read through `artifacts.read_control` -- the one bounded
  control-file reader -- and a missing/malformed sidecar is simply "no verdict", never an
  error: a reviewer forgetting it must not break reporting.

Honest limits: counts are observational, not an A/B test -- different models usually get
different tasks, so compare groups with similar task mixes, and treat small samples
(`MIN_SAMPLE_FOR_FLAG`) as anecdotes.
"""

from __future__ import annotations

import posixpath
from collections.abc import Iterable
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ValidationError

from .artifacts import ArtifactStore, read_control
from .errors import ControlFileError
from .models import RunState, TaskRunState, TaskSpec

# A review task is recognised by declaring this output; its verdict sidecar sits beside it.
REVIEW_OUTPUT_BASENAME = "review.md"
REVIEW_VERDICT_BASENAME = "review-verdict.json"

UNKNOWN_LABEL = "(default)"  # model/effort the CLI/agent default supplied (nothing recorded)
NO_EFFORT_LABEL = "-"

# A group is flagged only once it has this many reviewed tasks, and when at least this share
# of them were judged FAIL -- below the sample floor a rate is noise, not a signal.
MIN_SAMPLE_FOR_FLAG = 5
REWORK_FLAG_RATE = 0.5

_SETTLED = frozenset({"succeeded", "failed", "timed_out", "cancelled"})


class ReviewVerdict(BaseModel):
    """Structured review result a reviewer writes as `review-verdict.json`.

    Shape: ``{"verdict": "PASS"|"FAIL", "findings": {"critical": n, "major": n, "minor": n},
    "must_fix": n}``. Every count is optional (defaults 0) so a terse sidecar still parses.
    """

    verdict: Literal["PASS", "FAIL"]
    critical: int = 0
    major: int = 0
    minor: int = 0
    must_fix: int = 0

    @classmethod
    def from_control(cls, data: dict) -> ReviewVerdict:
        findings = data.get("findings") or {}
        if not isinstance(findings, dict):
            findings = {}
        verdict = str(data.get("verdict", "")).strip().upper()
        return cls(
            verdict=verdict,  # type: ignore[arg-type]  # validated by pydantic below
            critical=_count(findings.get("critical")),
            major=_count(findings.get("major")),
            minor=_count(findings.get("minor")),
            must_fix=_count(data.get("must_fix")),
        )


def _count(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def review_verdict_path_for(task: TaskSpec) -> str | None:
    """Sidecar path for *task*, or None if it declares no `review.md` output."""
    for out in task.outputs:
        if posixpath.basename(out) == REVIEW_OUTPUT_BASENAME:
            return posixpath.join(posixpath.dirname(out), REVIEW_VERDICT_BASENAME)
    return None


def dispatch_provenance(
    task: TaskSpec,
    effective_model: str | None,
    effective_effort: str | None,
    all_tasks: Iterable[TaskSpec],
) -> dict[str, object]:
    """Fields to record on `TaskRunState` at dispatch (see its provenance block)."""
    inputs = set(task.inputs)
    producers = (
        sorted(t.id for t in all_tasks if t.id != task.id and inputs.intersection(t.outputs))
        if inputs
        else []
    )
    return {
        "agent": task.agent,
        "model": effective_model,
        "effort": effective_effort,
        "upstream_producers": producers,
        "review_verdict_path": review_verdict_path_for(task),
    }


def read_review_verdict(store: ArtifactStore, path: str | None) -> ReviewVerdict | None:
    """Best-effort sidecar read: None when absent, oversized, malformed, or unrecognised."""
    if not path:
        return None
    try:
        return ReviewVerdict.from_control(read_control(store, path))
    except (ControlFileError, ValidationError, ValueError, OSError):
        return None


class GroupUsage(BaseModel):
    """Rollup for one (agent, model, effort) configuration across the scanned runs."""

    agent: str
    model: str
    effort: str
    tasks: int = 0
    succeeded: int = 0
    failed: int = 0
    retried: int = 0  # tasks needing >1 in-call attempt or >1 dispatch cycle
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    reviewed: int = 0  # tasks whose work a structured verdict judged
    review_fail: int = 0
    critical: int = 0
    major: int = 0
    minor: int = 0
    must_fix: int = 0
    flags: list[str] = []

    @property
    def mean_cost_usd(self) -> float | None:
        return self.cost_usd / self.tasks if self.tasks else None

    @property
    def retry_rate(self) -> float | None:
        return self.retried / self.tasks if self.tasks else None

    @property
    def review_fail_rate(self) -> float | None:
        return self.review_fail / self.reviewed if self.reviewed else None


class UsageReport(BaseModel):
    runs_scanned: int
    verdicts_found: int
    reviews_seen: int  # review tasks that declared a sidecar path (found or not)
    groups: list[GroupUsage]


def _key(ts: TaskRunState) -> tuple[str, str, str]:
    return (ts.agent or UNKNOWN_LABEL, ts.model or UNKNOWN_LABEL, ts.effort or NO_EFFORT_LABEL)


def aggregate_usage(states: Iterable[RunState], store: ArtifactStore) -> UsageReport:
    """Group every settled, dispatched task in *states* by (agent, model, effort)."""
    groups: dict[tuple[str, str, str], GroupUsage] = {}

    def group(ts: TaskRunState) -> GroupUsage:
        agent, model, effort = _key(ts)
        return groups.setdefault(
            (agent, model, effort), GroupUsage(agent=agent, model=model, effort=effort)
        )

    runs = verdicts = reviews = 0
    for state in states:
        runs += 1
        for ts in state.tasks.values():
            # Never-dispatched tasks (skipped/not_taken/pending) have no usage to attribute.
            if ts.status not in _SETTLED or ts.dispatch_cycle < 1:
                continue
            g = group(ts)
            g.tasks += 1
            g.succeeded += ts.status == "succeeded"
            g.failed += ts.status != "succeeded"
            g.retried += ts.attempts > 1 or ts.dispatch_cycle > 1
            g.cost_usd += ts.cumulative_cost_usd
            g.input_tokens += ts.cumulative_input_tokens
            g.output_tokens += ts.cumulative_output_tokens

            if ts.review_verdict_path is None:
                continue
            reviews += 1
            verdict = read_review_verdict(store, ts.review_verdict_path)
            if verdict is None:
                continue
            verdicts += 1
            # Attribute the judgement to the model(s) whose work was reviewed -- not the
            # reviewer. A producer that was never dispatched this run has nothing to credit.
            for pid in ts.upstream_producers:
                producer = state.tasks.get(pid)
                if producer is None or producer.dispatch_cycle < 1:
                    continue
                pg = group(producer)
                pg.reviewed += 1
                pg.review_fail += verdict.verdict == "FAIL"
                pg.critical += verdict.critical
                pg.major += verdict.major
                pg.minor += verdict.minor
                pg.must_fix += verdict.must_fix

    for g in groups.values():
        rate = g.review_fail_rate
        if g.reviewed >= MIN_SAMPLE_FOR_FLAG and rate is not None and rate >= REWORK_FLAG_RATE:
            g.flags.append(
                f"high rework: {g.review_fail}/{g.reviewed} reviews FAIL -- consider a stronger "
                "model/effort for this role, or tighter task shapes"
            )
    ordered = sorted(groups.values(), key=lambda g: (g.agent, g.model, g.effort))
    return UsageReport(
        runs_scanned=runs, verdicts_found=verdicts, reviews_seen=reviews, groups=ordered
    )


def list_run_ids(workspace_root: str) -> list[str]:
    """Run ids with a persisted state file under *workspace_root*, sorted."""
    root = Path(workspace_root) / ".orchestrator" / "runs"
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir() if (p / "state.json").is_file())
