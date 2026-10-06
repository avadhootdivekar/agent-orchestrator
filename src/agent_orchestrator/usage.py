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

import logging
import posixpath
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import BaseModel, ValidationError

from .artifacts import ArtifactStore, LocalFsArtifactStore, read_control
from .errors import ControlFileError
from .feedback import (
    FeedbackEntry,
    FeedbackError,
    FeedbackFile,
    effective_for_task,
    load_feedback,
    validate_run_id,
)
from .implicit_signals import RunSignals, apply_survival, implicit_signals
from .models import RunState, TaskRunState, TaskSpec
from .survival import (
    FLAG_LIKELY_WORTHLESS,
    TaskSurvival,
    compute_survival,
    validate_ref,
)

log = logging.getLogger(__name__)

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
USER_BAD_FLAG_RATE = 0.5  # share of user-rated tasks rated "bad" (needs MIN_SAMPLE_FOR_FLAG rated)
DISAGREE_FLAG_RATE = 0.5  # share of explicitly-rated reviewed pairs where reviewer and user differ
LOW_SURVIVAL_FLAG_SHARE = 0.5  # share of measured tasks flagged likely_worthless
MAX_REPORT_RUN_IDS = 500  # explicit run ids per report (CLI and dashboard share this cap)
# Only these survival attributions are reliably per-task; "ambiguous"/"time-window"/"none"
# are run-level (or n/a) and never join into groups. Low-confidence rows are excluded too.
SURVIVAL_JOIN_ATTRIBUTIONS = frozenset({"isolation", "serial"})
SURVIVAL_JOIN_EXCLUDED_CONFIDENCE = "low"

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
    # User feedback (effective rating per task: task entry, else run entry). All additive.
    fb_good: int = 0
    fb_ok: int = 0
    fb_bad: int = 0
    fb_unnecessary: int = 0
    fb_rated_tasks: int = 0
    # Reviewer-vs-user candidates: EXPLICIT task-scope ratings only (a run-level rating never
    # creates one). Candidates, not an error rate -- the user may be the one who is wrong.
    false_pass_candidates: int = 0  # reviewer PASS, user explicitly rated bad
    false_fail_candidates: int = 0  # reviewer FAIL, user explicitly rated good
    verdict_rated_pairs: int = 0  # (producer, reviewer) pairs with an explicit task rating
    # Diff survival (only per-task-attributable tasks: isolation/serial, confidence not low).
    lines_added: int = 0
    lines_survived: int = 0
    survival_tasks: int = 0  # tasks with countable added lines that joined
    survival_low_tasks: int = 0  # ...of which flagged likely_worthless
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

    @property
    def survival_rate(self) -> float | None:
        return self.lines_survived / self.lines_added if self.lines_added else None

    @property
    def fb_bad_rate(self) -> float | None:
        return self.fb_bad / self.fb_rated_tasks if self.fb_rated_tasks else None

    @property
    def reviewer_disagreement_rate(self) -> float | None:
        if not self.verdict_rated_pairs:
            return None
        return (self.false_pass_candidates + self.false_fail_candidates) / self.verdict_rated_pairs


class RunSignalRow(BaseModel):
    """Per-run implicit signals (+ survival totals) -- only computed when survival is asked."""

    run_id: str
    signals: RunSignals
    lines_added: int | None = None
    lines_survived: int | None = None
    survival_rate: float | None = None


class ResultCacheUsage(BaseModel):
    """Cross-run result-cache totals over the scanned runs (E-Rc4Hk8 HLD 13.6; the G0 object).

    `saved_*` are estimates of what the HITS avoided (the source run's cost, retries included);
    they are NOT part of any group's `cost_usd`. `would_hits / lookups` over shadow-mode runs is
    the G0 would-hit rate.
    """

    hits: int = 0
    saved_cost_usd: float = 0.0
    saved_tokens: int = 0
    saved_seconds: float = 0.0
    lookups: int = 0
    would_hits: int = 0
    misses: int = 0
    ineligible: int = 0
    avoidable_cost_usd: float = 0.0
    miss_reasons: dict[str, int] = {}
    store_skip_reasons: dict[str, int] = {}


class UsageReport(BaseModel):
    runs_scanned: int
    verdicts_found: int  # verdicts of any kind that parsed
    reviews_seen: int  # tasks that declared a verdict path, any kind (found or not)
    groups: list[GroupUsage]
    outcomes: list[RunOutcome] = []
    # Coverage / provenance of the optional joins (all additive; defaults = "not computed").
    runs_rated: int = 0  # scanned runs with at least one feedback entry
    feedback_errors: int = 0  # runs whose feedback.json was unreadable (skipped, counted)
    skipped: list[str] = []  # runs that could not be loaded, as "run_id: reason"
    survival_available: bool = False
    survival_unavailable_reason: str | None = None
    survival_ref: str | None = None
    run_signals: list[RunSignalRow] = []
    # None unless a scanned run has current result-cache records (E-Rc4Hk8); not in the payload.
    result_cache: ResultCacheUsage | None = None


FeedbackInput = Mapping[str, Sequence[FeedbackEntry] | FeedbackFile]
SurvivalInput = Mapping[tuple[str, str], TaskSurvival]


def _joinable_survival(row: TaskSurvival | None) -> TaskSurvival | None:
    """The row only if it is a reliable per-task measurement with countable lines."""
    if row is None or row.unavailable or row.lines_added <= 0:
        return None
    if row.attribution not in SURVIVAL_JOIN_ATTRIBUTIONS:
        return None
    if row.confidence == SURVIVAL_JOIN_EXCLUDED_CONFIDENCE:
        return None
    return row


def _current_hit_ids(state: RunState) -> frozenset[str]:
    """Task ids whose result-cache record is a current hit (E-Rc4Hk8 D12). Empty, and
    `cache.report` never imported, for a run the cache never touched (D9)."""
    if not state.result_cache:
        return frozenset()
    from .cache.report import current_hit

    return frozenset(tid for tid in state.tasks if current_hit(state, tid))


def _run_counters(state: RunState) -> dict[str, object] | None:
    """One run's contribution to the cross-run `result_cache` object (HLD 13.6)."""
    if not state.result_cache:
        return None
    from .cache.report import usage_counters

    return usage_counters(state)


def _sum_result_cache(parts: Sequence[dict[str, object]]) -> ResultCacheUsage | None:
    """Sum the per-run counters; None when no scanned run has current records."""
    if not parts:
        return None
    total = ResultCacheUsage()
    for c in parts:
        total.hits += cast(int, c["hits"])
        total.saved_cost_usd += cast(float, c["saved_cost_usd"])
        total.saved_tokens += cast(int, c["saved_tokens"])
        total.saved_seconds += cast(float, c["saved_seconds"])
        total.lookups += cast(int, c["lookups"])
        total.would_hits += cast(int, c["would_hits"])
        total.misses += cast(int, c["misses"])
        total.ineligible += cast(int, c["ineligible"])
        total.avoidable_cost_usd += cast(float, c["avoidable_cost_usd"])
        for field in ("miss_reasons", "store_skip_reasons"):
            merged = getattr(total, field)
            for reason, n in cast(dict[str, int], c[field]).items():
                merged[reason] = merged.get(reason, 0) + n
    return total


def _key(ts: TaskRunState) -> tuple[str, str, str]:
    return (ts.agent or UNKNOWN_LABEL, ts.model or UNKNOWN_LABEL, ts.effort or NO_EFFORT_LABEL)


def aggregate_usage(
    states: Iterable[RunState],
    store: ArtifactStore,
    *,
    feedback: FeedbackInput | None = None,
    survival: SurvivalInput | None = None,
) -> UsageReport:
    """Group every settled, dispatched task in *states* by (agent, model, effort).

    Optional joins (keyword-only, so the two-argument call keeps working): *feedback* maps
    run_id -> feedback entries (or a `FeedbackFile`); *survival* maps (run_id, task_id) ->
    `TaskSurvival` (only isolation/serial, non-low-confidence rows are joined)."""
    groups: dict[tuple[str, str, str], GroupUsage] = {}

    def group(ts: TaskRunState) -> GroupUsage:
        agent, model, effort = _key(ts)
        return groups.setdefault(
            (agent, model, effort), GroupUsage(agent=agent, model=model, effort=effort)
        )

    outcomes: list[RunOutcome] = []
    runs = verdicts = reviews = runs_rated = 0
    rc_parts: list[dict[str, object]] = []
    for state in states:
        runs += 1
        raw_fb = (feedback or {}).get(state.run_id)
        entries: list[FeedbackEntry] = (
            list(raw_fb.entries) if isinstance(raw_fb, FeedbackFile) else list(raw_fb or [])
        )
        runs_rated += bool(entries)
        outcome = RunOutcome(run_id=state.run_id)
        has_outcome = False
        rc_hits = _current_hit_ids(state)
        counters = _run_counters(state)
        if counters is not None:
            rc_parts.append(counters)
        for tid, ts in state.tasks.items():
            # Never-dispatched tasks (skipped/not_taken/pending) have no usage to attribute.
            if ts.status not in _SETTLED or ts.dispatch_cycle < 1:
                continue
            if tid in rc_hits:
                # Site A (E-Rc4Hk8 D12): a result-cache hit is not a dispatched task, so it is not
                # counted. Spend carried in from an EARLIER paid attempt (hit-after-spend) is
                # real and still counts; a first-pass hit carries 0 and adds nothing.
                if (
                    ts.cumulative_cost_usd
                    or ts.cumulative_input_tokens
                    or ts.cumulative_output_tokens
                ):
                    hg = group(ts)
                    hg.cost_usd += ts.cumulative_cost_usd
                    hg.input_tokens += ts.cumulative_input_tokens
                    hg.output_tokens += ts.cumulative_output_tokens
            else:
                g = group(ts)
                g.tasks += 1
                g.succeeded += ts.status == "succeeded"
                g.failed += ts.status != "succeeded"
                g.retried += ts.attempts > 1 or ts.dispatch_cycle > 1
                g.cost_usd += ts.cumulative_cost_usd
                g.input_tokens += ts.cumulative_input_tokens
                g.output_tokens += ts.cumulative_output_tokens
                if entries:
                    eff = effective_for_task(entries, tid)
                    if eff is not None:
                        g.fb_rated_tasks += 1
                        setattr(g, f"fb_{eff.rating}", getattr(g, f"fb_{eff.rating}") + 1)
                        g.fb_unnecessary += "unnecessary" in eff.reasons
                joined = _joinable_survival((survival or {}).get((state.run_id, tid)))
                if joined is not None:
                    g.lines_added += joined.lines_added
                    g.lines_survived += joined.lines_survived
                    g.survival_tasks += 1
                    g.survival_low_tasks += FLAG_LIKELY_WORTHLESS in joined.flags

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
                if producer is None or producer.dispatch_cycle < 1 or pid in rc_hits:
                    continue  # never dispatched, or a result-cache hit (Site B, E-Rc4Hk8 D12)
                pg = group(producer)
                pg.reviewed += 1
                pg.review_fail += verdict.verdict == "FAIL"
                pg.critical += verdict.critical
                pg.major += verdict.major
                pg.minor += verdict.minor
                pg.must_fix += verdict.must_fix
                explicit = effective_for_task(entries, pid, explicit_only=True) if entries else None
                if explicit is not None:
                    pg.verdict_rated_pairs += 1
                    pg.false_pass_candidates += (
                        verdict.verdict == "PASS" and explicit.rating == "bad"
                    )
                    pg.false_fail_candidates += (
                        verdict.verdict == "FAIL" and explicit.rating == "good"
                    )
        if has_outcome:
            outcomes.append(outcome)

    for g in groups.values():
        rate = g.review_fail_rate
        if g.reviewed >= MIN_SAMPLE_FOR_FLAG and rate is not None and rate >= REWORK_FLAG_RATE:
            g.flags.append(
                f"high rework: {g.review_fail}/{g.reviewed} reviews FAIL -- consider a stronger "
                "model/effort for this role, or tighter task shapes"
            )
        g.flags.extend(_signal_flags(g))
    ordered = sorted(groups.values(), key=lambda g: (g.agent, g.model, g.effort))
    return UsageReport(
        runs_scanned=runs,
        verdicts_found=verdicts,
        reviews_seen=reviews,
        groups=ordered,
        outcomes=outcomes,
        runs_rated=runs_rated,
        survival_available=survival is not None,
        result_cache=_sum_result_cache(rc_parts),
    )


def _signal_flags(g: GroupUsage) -> list[str]:
    """Feedback/disagreement/survival flags -- each only above the sample floor."""
    flags: list[str] = []
    bad = g.fb_bad_rate
    if g.fb_rated_tasks >= MIN_SAMPLE_FOR_FLAG and bad is not None and bad >= USER_BAD_FLAG_RATE:
        flags.append(f"user-rated bad: {g.fb_bad}/{g.fb_rated_tasks} rated tasks")
    dis = g.reviewer_disagreement_rate
    if (
        g.verdict_rated_pairs >= MIN_SAMPLE_FOR_FLAG
        and dis is not None
        and dis >= DISAGREE_FLAG_RATE
    ):
        n = g.false_pass_candidates + g.false_fail_candidates
        flags.append(
            f"reviewer disagrees with user: {n}/{g.verdict_rated_pairs} rated pairs "
            f"({g.false_pass_candidates} PASS-but-bad, {g.false_fail_candidates} FAIL-but-good; "
            "candidates, not an error rate)"
        )
    if (
        g.survival_tasks >= MIN_SAMPLE_FOR_FLAG
        and g.survival_low_tasks / g.survival_tasks >= LOW_SURVIVAL_FLAG_SHARE
    ):
        flags.append(
            f"low survival: {g.survival_low_tasks}/{g.survival_tasks} tasks have <10% of their "
            "added lines still present"
        )
    return flags


def list_run_ids(workspace_root: str) -> list[str]:
    """Run ids with a persisted state file under *workspace_root*, sorted."""
    root = Path(workspace_root) / ".orchestrator" / "runs"
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir() if (p / "state.json").is_file())


def _load_states(ws_root: str, run_ids: Sequence[str] | None) -> tuple[list[RunState], list[str]]:
    """Load run states like the CLI always did; unreadable/invalid runs are skipped and
    returned as "run_id: reason" strings (never raised)."""
    from .runstate import RunStateStore

    store = LocalFsArtifactStore(ws_root)
    rs_store = RunStateStore(ws_root, store)
    states: list[RunState] = []
    skipped: list[str] = []
    for rid in run_ids if run_ids else list_run_ids(ws_root):
        try:
            validate_run_id(rid)  # explicit ids come from CLI/HTTP: no traversal into other dirs
            states.append(rs_store.load(rid))
        except (FileNotFoundError, ValueError, FeedbackError, OSError) as e:
            skipped.append(f"{rid}: {e}")
    return states, skipped


def _load_feedback_map(
    ws_root: str, states: Sequence[RunState]
) -> tuple[dict[str, list[FeedbackEntry]], int]:
    out: dict[str, list[FeedbackEntry]] = {}
    errors = 0
    for st in states:
        try:
            entries = load_feedback(ws_root, st.run_id).entries
        except FeedbackError as e:
            log.warning("usage: skipping feedback for run %s: %s", st.run_id, e)
            errors += 1
            continue
        if entries:
            out[st.run_id] = entries
    return out, errors


def build_usage_report(
    ws_root: str,
    run_ids: Sequence[str] | None = None,
    *,
    with_survival: bool = False,
    ref: str | None = None,
) -> UsageReport:
    """The single report builder shared by `ao report-usage` and the dashboard API.

    Loads run states (unreadable runs go to `report.skipped`), joins per-run feedback (a corrupt
    `feedback.json` is skipped and counted in `feedback_errors`) and -- only when asked --
    diff survival. Survival never breaks the report: any failure degrades to
    ``survival_available=False`` with a reason. Raises ValueError only for caller errors
    (too many run ids)."""
    if run_ids is not None and len(run_ids) > MAX_REPORT_RUN_IDS:
        raise ValueError(f"too many run ids ({len(run_ids)} > {MAX_REPORT_RUN_IDS})")
    states, skipped = _load_states(ws_root, run_ids)
    fb_map, fb_errors = _load_feedback_map(ws_root, states)

    survival_rows: dict[tuple[str, str], TaskSurvival] | None = None
    run_survivals: dict[str, Any] = {}
    reason: str | None = None
    if with_survival:
        ref_err = validate_ref(ref) if ref is not None else None
        if ref_err:
            reason = f"invalid ref: {ref_err}"
        else:
            try:
                sv = compute_survival(states, ws_root, ref=ref)
            except Exception as e:  # compute_survival promises not to raise; belt and braces
                log.warning("usage: survival failed: %s", e)
                reason = f"survival failed: {e}"
            else:
                reason = sv.unavailable or None
                if reason is None:
                    survival_rows = {}
                    for run in sv.runs:
                        run_survivals[run.run_id] = run
                        for row in run.tasks:
                            if row.task_id is not None:
                                survival_rows[(run.run_id, row.task_id)] = row

    store = LocalFsArtifactStore(ws_root)
    report = aggregate_usage(states, store, feedback=fb_map, survival=survival_rows)
    report.skipped = skipped
    report.feedback_errors = fb_errors
    if with_survival:
        report.survival_available = survival_rows is not None
        report.survival_unavailable_reason = reason
        report.survival_ref = ref
        if survival_rows is not None:
            report.run_signals = _run_signal_rows(states, ws_root, run_survivals)
    return report


def _run_signal_rows(
    states: Sequence[RunState], ws_root: str, run_survivals: Mapping[str, Any]
) -> list[RunSignalRow]:
    rows: list[RunSignalRow] = []
    for st in states:
        rsv = run_survivals.get(st.run_id)
        try:
            sig = implicit_signals(st, ws_root)
        except Exception as e:  # signals are circumstantial extras; never break the report
            log.debug("usage: implicit signals failed for %s: %s", st.run_id, e)
            sig = RunSignals(run_status=st.status)
        total = rsv.total if rsv is not None else None
        if rsv is not None:
            apply_survival(sig, rsv)
        rows.append(
            RunSignalRow(
                run_id=st.run_id,
                signals=sig,
                lines_added=total.lines_added if total and not total.unavailable else None,
                lines_survived=total.lines_survived if total and not total.unavailable else None,
                survival_rate=total.survival_rate if total else None,
            )
        )
    return rows


def usage_report_payload(report: UsageReport) -> dict[str, Any]:
    """JSON-ready dict: the model dump plus each group's computed rates (shared by the CLI
    `--json` and the dashboard API so they cannot drift)."""
    payload = report.model_dump()
    if report.result_cache is None:
        del payload["result_cache"]  # absent when no scanned run has records (byte-identical)
    for row, g in zip(payload["groups"], report.groups, strict=True):
        row["mean_cost_usd"] = g.mean_cost_usd
        row["retry_rate"] = g.retry_rate
        row["review_fail_rate"] = g.review_fail_rate
        row["survival_rate"] = g.survival_rate
        row["fb_bad_rate"] = g.fb_bad_rate
        row["reviewer_disagreement_rate"] = g.reviewer_disagreement_rate
    return payload
