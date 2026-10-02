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
VERDICT_BASENAME = "verdict.json"  # a declared output with this name IS the verdict
VERDICT_SUFFIX = "-verdict.json"
# declared-output basename -> sibling verdict sidecar basename
SIBLING_VERDICTS = {
    REVIEW_OUTPUT_BASENAME: REVIEW_VERDICT_BASENAME,
    "verify.md": "verify-verdict.json",
}

KIND_REVIEW = "review"
KIND_CHECKPOINT = "checkpoint"
KIND_FINAL_VERIFY = "final_verify"
CHECKPOINT_SCHEMA = "ao.overseer.verdict/v1"
FINAL_VERIFY_SCHEMA = "ao.overseer.final-verify/v1"
CRITERIA_STATUSES = ("met", "unmet", "deferred")
FINAL_VERIFY_VERDICTS = ("met", "partial", "not_met")

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


def _is_loose_name(base: str) -> bool:
    """True for names matched by the declared-output rule (the file itself is the verdict)."""
    return base == VERDICT_BASENAME or base.endswith(VERDICT_SUFFIX)


def verdict_path_for(task: TaskSpec) -> str | None:
    """Verdict sidecar path for *task* (first match wins): explicit `verdict_path`; a declared
    output named `verdict.json` / `*-verdict.json` (the file itself); a sibling of a declared
    `review.md` / `verify.md`. None if none applies."""
    if task.verdict_path:
        return task.verdict_path
    for out in task.outputs:
        if _is_loose_name(posixpath.basename(out)):
            return out
    for out in task.outputs:
        sibling = SIBLING_VERDICTS.get(posixpath.basename(out))
        if sibling:
            return posixpath.join(posixpath.dirname(out), sibling)
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
        "verdict_path": verdict_path_for(task),
    }


def read_review_verdict(store: ArtifactStore, path: str | None) -> ReviewVerdict | None:
    """Best-effort sidecar read: None when absent, oversized, malformed, or unrecognised."""
    if not path:
        return None
    try:
        return ReviewVerdict.from_control(read_control(store, path))
    except (ControlFileError, ValidationError, ValueError, OSError):
        return None


class CheckpointVerdict(BaseModel):
    """Overseer checkpoint `verdict.json` (`ao.overseer.verdict/v1`), reduced to counts."""

    decision: str | None = None
    met: int = 0
    unmet: int = 0
    deferred: int = 0
    alignment: dict[str, int] = {}


class FinalVerify(BaseModel):
    """Overseer `verify-verdict.json` (`ao.overseer.final-verify/v1`): per-ask counts."""

    met: int = 0
    partial: int = 0
    not_met: int = 0


Verdict = tuple[str, ReviewVerdict | CheckpointVerdict | FinalVerify]


def _list_of_dicts(value: object) -> list[dict]:
    return [x for x in value if isinstance(x, dict)] if isinstance(value, list) else []


def _norm(value: object) -> str:
    return value.strip().lower() if isinstance(value, str) else ""


def parse_verdict(data: dict) -> Verdict | None:
    """Classify a control object by content and reduce it; None when unrecognised.

    final_verify: schema `ao.overseer.final-verify/v1` (or an `asks` list); checkpoint: schema
    `ao.overseer.verdict/v1` (or a `decision` field); otherwise a review verdict (PASS/FAIL)."""
    schema = data.get("schema")
    if schema == FINAL_VERIFY_SCHEMA or "asks" in data:
        fv = FinalVerify()
        for ask in _list_of_dicts(data.get("asks")):
            v = _norm(ask.get("verdict"))
            if v in FINAL_VERIFY_VERDICTS:
                setattr(fv, v, getattr(fv, v) + 1)
        return KIND_FINAL_VERIFY, fv
    if schema == CHECKPOINT_SCHEMA or "decision" in data:
        ck = CheckpointVerdict(decision=_norm(data.get("decision")) or None)
        for crit in _list_of_dicts(data.get("criteria")):
            st = _norm(crit.get("status"))
            if st in CRITERIA_STATUSES:
                setattr(ck, st, getattr(ck, st) + 1)
        for al in _list_of_dicts(data.get("alignment")):
            st = _norm(al.get("status"))
            if st:
                ck.alignment[st] = ck.alignment.get(st, 0) + 1
        return KIND_CHECKPOINT, ck
    try:
        return KIND_REVIEW, ReviewVerdict.from_control(data)
    except (ValidationError, ValueError):
        return None


def read_verdict(store: ArtifactStore, path: str | None) -> Verdict | None:
    """Best-effort read + `parse_verdict`: None when absent, oversized, malformed, unknown."""
    if not path:
        return None
    try:
        return parse_verdict(read_control(store, path))
    except (ControlFileError, ValueError, OSError):
        return None


class RunOutcome(BaseModel):
    """Per-run outcome vs the charter, from overseer checkpoint / final-verify verdicts."""

    run_id: str
    checkpoints_seen: int = 0  # checkpoint tasks that declared a verdict path
    checkpoints_found: int = 0  # ...whose verdict parsed
    last_decision: str | None = None
    criteria_met: int = 0  # criteria/alignment counts are from the LAST checkpoint only
    criteria_unmet: int = 0
    criteria_deferred: int = 0
    alignment: dict[str, int] = {}
    final_verify: FinalVerify | None = None


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
    verdicts_found: int  # verdicts of any kind that parsed
    reviews_seen: int  # tasks that declared a verdict path, any kind (found or not)
    groups: list[GroupUsage]
    outcomes: list[RunOutcome] = []


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

    outcomes: list[RunOutcome] = []
    runs = verdicts = reviews = 0
    for state in states:
        runs += 1
        outcome = RunOutcome(run_id=state.run_id)
        has_outcome = False
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

            vpath = ts.verdict_path or ts.review_verdict_path
            if vpath is None:
                continue
            base = posixpath.basename(vpath)
            is_ck_path = base == VERDICT_BASENAME
            parsed = read_verdict(store, vpath)
            if parsed is None and _is_loose_name(base) and store.exists(vpath):
                # A `*verdict.json` file of unknown shape is not necessarily ours: neither a
                # verdict nor a missed one, so it stays out of the coverage counts.
                continue
            reviews += 1
            if is_ck_path:
                outcome.checkpoints_seen += 1
                has_outcome = True
            if parsed is None:
                continue
            verdicts += 1
            _, verdict = parsed
            if isinstance(verdict, CheckpointVerdict):
                if not is_ck_path:  # custom-named checkpoint sidecar
                    outcome.checkpoints_seen += 1
                outcome.checkpoints_found += 1
                # Tasks iterate in state order, so the last parsed checkpoint wins.
                outcome.last_decision = verdict.decision
                outcome.criteria_met = verdict.met
                outcome.criteria_unmet = verdict.unmet
                outcome.criteria_deferred = verdict.deferred
                outcome.alignment = dict(verdict.alignment)
                has_outcome = True
                continue
            if isinstance(verdict, FinalVerify):
                outcome.final_verify = verdict
                has_outcome = True
                continue
            # Attribute the judgement to the model(s) whose work was reviewed -- not the
            # reviewer. Each reviewer task contributes once per producer, so a producer
            # reviewed by several tasks (retries aside, which share one TaskRunState) counts
            # each reviewer's final verdict once. A producer never dispatched this run is skipped.
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
        if has_outcome:
            outcomes.append(outcome)

    for g in groups.values():
        rate = g.review_fail_rate
        if g.reviewed >= MIN_SAMPLE_FOR_FLAG and rate is not None and rate >= REWORK_FLAG_RATE:
            g.flags.append(
                f"high rework: {g.review_fail}/{g.reviewed} reviews FAIL -- consider a stronger "
                "model/effort for this role, or tighter task shapes"
            )
    ordered = sorted(groups.values(), key=lambda g: (g.agent, g.model, g.effort))
    return UsageReport(
        runs_scanned=runs,
        verdicts_found=verdicts,
        reviews_seen=reviews,
        groups=ordered,
        outcomes=outcomes,
    )


def list_run_ids(workspace_root: str) -> list[str]:
    """Run ids with a persisted state file under *workspace_root*, sorted."""
    root = Path(workspace_root) / ".orchestrator" / "runs"
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir() if (p / "state.json").is_file())
