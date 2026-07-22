"""Orchestration loop -- deterministic, resumable, bounded (design doc §4.5, §8).

`run_suite(suite_path, subject_path, ...)` is the integration point that ties the
already-landed pieces together for one (suite x subject) run:

    load_suite/load_subject (T-Sc4Hm2, bench/spec.py)
        -> materialize_workspace (T-Sbj9Ka, bench/workspace.py)
        -> SUBJECT_REGISTRY[subject.type](...).run(...) (T-Sbj9Ka, bench/subjects.py)
        -> GRADER_REGISTRY[task.grader.type]().grade(...) (T-Grd7Vx, bench/graders.py)
        -> build_task_metric / aggregate (T-Grd7Vx, bench/metrics.py)

For each task (sorted by id, C6 determinism): materialize a fresh, path-guarded
workspace, run the subject, grade the result, and PERSIST the accumulated run record
to ``run.json`` (write-temp + atomic rename, mirrors `runstate.RunStateStore.save`) --
so an interrupted run always leaves a valid, resumable file on disk (design §4.5 risk).
A subsequent call with the same suite/subject (same UTC date, so the same
`bench_run_id`) skips every task already present in that file unless `force=True`.
One task raising is isolated to that task (`subject_status="error"`) and never aborts
the run (mandatory integration note recorded in the epic STATUS.md by T-Sbj9Ka/
T-Grd7Vx: both `Subject.run()` and `Grader.grade()` are wrapped by a SINGLE per-task
try/except spanning both calls -- catches `BenchError` subclasses (`SubjectError`,
`GraderError`) as well as any other unexpected exception).

`max_parallel` (T-Pl3Rx7, ADR-0009 D4) opts into bounded task-level parallelism via a
`ThreadPoolExecutor`. `max_parallel<=1` (the default) walks `tasks_to_consider` in a
plain `for` loop -- BYTE-IDENTICAL to the pre-parallelism serial path (same order, same
log events, same persist cadence); `max_parallel>1` dispatches the same per-task body
(`_run_one`, a nested closure so it can share `run_suite`'s locals) across a bounded
pool. Tasks are independent (no DAG, unlike the core engine's ADR-0007 wave/barrier
scheduler, deliberately not reused here -- SI-1) and per-task workspaces are already
disjoint (`bench/workspace.py`), so the ONLY shared mutable state is `tasks_dict`, the
running-cost total, and the `run.json` persist -- all three (plus the budget
check-before-schedule) are guarded by one `threading.Lock` (`_dispatch_or_skip`/
`_run_one` below). Overshoot on the USD cap is bounded to at most `max_parallel`
in-flight tasks (documented on `cost_budget_usd` below); persisted `tasks` stays
id-sorted regardless of completion order (`_build_record`, unchanged).

File-ownership note for `T-Rpt3Wq` (results.py, not yet landed): this task's mandate
is the machine-readable ``run.json`` -- the pydantic run-record model lives here
(`BenchRunRecord`/`BenchTaskRecord`) and IS persisted to disk by this module. Design
§4.5's pseudocode also calls a `write_summary_md(...)` at the end of `run_suite`; that
function (and the human-readable `summary.md` + cross-subject `comparison.*` writers)
does not exist yet, so `run_suite` does not call it -- `T-Rpt3Wq` builds those writers
in `results.py` ON TOP of the `run.json` shape defined here (reads it back in, e.g. via
`BenchRunRecord.model_validate`), rather than this module reaching forward into a
sibling file it does not own.

`BenchTaskRecord` extends `TaskMetric` (bench/metrics.py, owned by T-Grd7Vx) with
`raw_error` / `grader_detail` / `grader_raw_tail` -- fields this task's contract
requires per-task ("per-task subject_status/raw_error/grader detail") that
`TaskMetric` itself does not carry. Added here as a local subclass rather than editing
`metrics.py`, which is outside this task's owned files.

Import direction (SI-1): reads only from core (`_version`, `logging_setup`) and
sibling `bench/` modules; nothing in core or `bench/{spec,subjects,graders,metrics}.py`
imports this module.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import threading
import time
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel

from .._version import get_version_string
from ..logging_setup import attach_run_handler, detach_run_handler, get_run_logger
from .errors import BenchError
from .graders import GradeResult
from .metrics import Aggregate, SubjectResultLike, TaskMetric, aggregate, build_task_metric
from .registries import GRADER_REGISTRY, SUBJECT_REGISTRY
from .spec import BenchSuite, BenchTask, SubjectSpec, load_subject, load_suite
from .subjects import SubjectResult, _budget_skipped_result
from .workspace import BENCH_WORKSPACE_ROOT, CAPTURE_DIRNAME, REPO_ROOT, materialize_workspace

# ---------------------------------------------------------------------------
# Named constants (CLAUDE.md: no magic literals)
# ---------------------------------------------------------------------------

RUN_RECORD_SCHEMA_VERSION = "1.0"
RUN_JSON_FILENAME = "run.json"
BENCH_RUN_LOG_FILENAME = "bench.log"

# `benchmarks/results/` is COMMITTED (design §9); workspaces stay under the
# gitignored `BENCH_WORKSPACE_ROOT` (bench/workspace.py, C2).
BENCH_RESULTS_ROOT: Path = REPO_ROOT / "benchmarks" / "results"

# Last-resort per-task bound when neither the task, a run-level override, nor the
# suite's own `defaults.timeout_seconds` supply one -- mirrors the `1800` example in
# design doc §5.1 so an unbounded suite is never possible (NFR-2/G7).
DEFAULT_TASK_TIMEOUT_SECONDS = 1800

# `bench_run_id` date component (design §4.5: "<utc-date>-<suite.id>-<subject.id>").
_BENCH_RUN_ID_DATE_FORMAT = "%Y-%m-%d"

# Bounded, best-effort environment probes (design §4.6 `env.claude_version`/`git_sha`)
# -- never allowed to slow down or fail a run; mirrors bench/cli.py's
# `_CLAUDE_PROBE_TIMEOUT_SECONDS` probe pattern.
_CLAUDE_VERSION_PROBE_TIMEOUT_SECONDS = 5
_GIT_SHA_PROBE_TIMEOUT_SECONDS = 5


def _utc_now() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# Run-record models (the persisted `run.json` shape -- design §4.6, owned by this task)
# ---------------------------------------------------------------------------


class BenchTaskRecord(TaskMetric):
    """One persisted task row: `TaskMetric` (design §4.4/§6) plus per-task
    observability fields this task's contract requires that `TaskMetric` itself does
    not carry. See module docstring for why this is a local subclass.
    """

    raw_error: str | None = None
    grader_detail: dict[str, Any] = {}
    grader_raw_tail: str = ""


class BenchRunSubjectInfo(BaseModel):
    """Design §4.6 `subject:{id,type,model,resolved_config}`."""

    id: str
    type: str
    model: str | None = None
    resolved_config: dict[str, Any]


class BenchRunEnv(BaseModel):
    """Design §4.6 `env:{ao_version, claude_version, os, git_sha}` -- all best-effort
    except `ao_version`/`os`, which are always cheaply available in-process.
    """

    ao_version: str
    claude_version: str | None = None
    os: str
    git_sha: str | None = None


class BenchRunRecord(BaseModel):
    """The persisted `run.json` shape (design §4.6). `config_fingerprint` here is the
    RUN-level fingerprint (suite id/version + subject spec + relevant overrides +
    ao/claude versions) -- distinct from each `BenchTaskRecord.config_fingerprint`
    (a per-task fingerprint over that task + subject + ao_version, design §4.5/AC6).
    """

    schema_version: str = RUN_RECORD_SCHEMA_VERSION
    bench_run_id: str
    suite_id: str
    domain: str
    subject: BenchRunSubjectInfo
    config_fingerprint: str
    started_at: str
    # None only for the one in-memory-only, never-persisted case: a brand new run
    # whose task_filter matched zero tasks (nothing ran, nothing to write) -- see
    # `run_suite`'s trailing fallback.
    ended_at: str | None = None
    env: BenchRunEnv
    tasks: list[BenchTaskRecord] = []
    aggregate: Aggregate


# ---------------------------------------------------------------------------
# Best-effort environment probes
# ---------------------------------------------------------------------------


def _ao_version() -> str:
    """`ao --version`'s own string (core `_version.py`) -- commit/dirty-aware in a
    dev/editable checkout (ASSUMPTION A2/A6), so a bench `run.json` is directly
    comparable to what `uv run ao --version` reports at the time it ran.
    """
    return get_version_string()


def _probe_claude_version() -> str | None:
    """Best-effort `claude --version` (ASSUMPTION A1). Never raises: `None` if
    `claude` is missing/errors/times out (mirrors `bench/cli.py`'s
    `_probe_claude_cli`, but returns the string for `run.json` rather than echoing a
    warning -- this module's job is to *record* the environment, not gate on it).
    """
    if shutil.which("claude") is None:
        return None
    try:
        result = subprocess.run(  # noqa: S603 -- fixed argv, no shell, bounded timeout
            ["claude", "--version"],
            capture_output=True,
            text=True,
            timeout=_CLAUDE_VERSION_PROBE_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def _git_sha() -> str | None:
    """Best-effort short git SHA of the repo `run_suite` is executing from. Never
    raises: `None` outside a git checkout, on a missing `git` binary, or on timeout.
    """
    try:
        result = subprocess.run(  # noqa: S603 -- fixed argv, no shell, bounded timeout
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=_GIT_SHA_PROBE_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


# ---------------------------------------------------------------------------
# Fingerprints (design §4.5/§4.6, AC6: stable for identical inputs, changes with any)
# ---------------------------------------------------------------------------


def _sha256_of(payload: dict[str, Any]) -> str:
    """Stable sha256 over *payload* -- `sort_keys=True` so key ORDER never affects the
    hash (only content does), `default=str` so any non-JSON-native value (there
    shouldn't be one from a pydantic `model_dump(mode="json")`) degrades to its repr
    rather than raising.
    """
    canonical = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _relativize_golden(golden: str, suite_dir: Path) -> str:
    """Relativize an absolute `assertions[*].golden` path against *suite_dir* for
    fingerprinting only (W2 fix).

    `spec.py::_check_assertions` rewrites `golden` to an ABSOLUTE path at `load_suite`
    time (needed for grade time, since the suite's base dir isn't otherwise carried
    through to the grading context) -- but hashing that absolute path means a
    byte-identical suite checked out at two different paths hashes to two different
    `config_fingerprint`s. Falls back to the basename if *golden* isn't under
    *suite_dir* at all (an unusual absolute reference elsewhere on disk).
    """
    p = Path(golden)
    if not p.is_absolute():
        return golden
    try:
        return p.relative_to(suite_dir).as_posix()
    except ValueError:
        return p.name


def _task_fingerprint_payload(task: BenchTask, suite_dir: Path) -> dict[str, Any]:
    """`task.model_dump(mode="json")` with any absolute `grader.assertions[*].golden`
    path relativized against *suite_dir* (see `_relativize_golden`) before hashing --
    grading behavior itself is unchanged, this only affects what gets fingerprinted.
    """
    payload = task.model_dump(mode="json")
    for assertion in payload.get("grader", {}).get("assertions", []):
        golden = assertion.get("golden")
        if golden:
            assertion["golden"] = _relativize_golden(golden, suite_dir)
    return payload


def _task_config_fingerprint(
    task: BenchTask, subject_spec: SubjectSpec, ao_version: str, suite_dir: Path
) -> str:
    """Per-task fingerprint (design §4.5: `sha256(canonical(task)+canonical(subject)+
    ao_version())`) -- what `BenchTaskRecord.config_fingerprint` (inherited from
    `TaskMetric`) records for AC6. *suite_dir* (the suite file's parent dir) is used
    only to make an `equals_file` assertion's `golden` path checkout-independent (W2
    fix) -- it is not itself part of the hashed payload.
    """
    return _sha256_of(
        {
            "task": _task_fingerprint_payload(task, suite_dir),
            "subject": subject_spec.model_dump(mode="json"),
            "ao_version": ao_version,
        }
    )


def _run_config_fingerprint(
    suite: BenchSuite,
    subject_spec: SubjectSpec,
    overrides: dict[str, Any],
    ao_version: str,
    claude_version: str | None,
) -> str:
    """Run-level fingerprint (assigning-message contract: "stable hash over suite
    id/version + subject spec + relevant overrides + ao/claude versions"). Only the
    overrides that affect what a subject actually DOES (`budget_total`, `max_turns`,
    `default_timeout`) are included -- `force`/`task_filter` are control-flow, not
    "config that produced these metric values".
    """
    return _sha256_of(
        {
            "suite_id": suite.id,
            "suite_version": suite.version,
            "subject": subject_spec.model_dump(mode="json"),
            "overrides": overrides,
            "ao_version": ao_version,
            "claude_version": claude_version,
        }
    )


# ---------------------------------------------------------------------------
# run.json persistence (write-temp + atomic rename, mirrors runstate.RunStateStore)
# ---------------------------------------------------------------------------


def _load_existing_record(path: Path) -> BenchRunRecord | None:
    """`None` if *path* does not exist yet (first run for this `bench_run_id`).
    Raises `BenchError` for a corrupt/schema-mismatched file: the always-valid-JSON
    persist contract (write-temp + atomic rename, below) means this should never
    happen from a normal interrupted run -- a loud failure here beats silently
    discarding history from a tampered/foreign file.
    """
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise BenchError(f"Failed to read existing run record at {path}: {exc}") from exc
    try:
        return BenchRunRecord.model_validate(data)
    except Exception as exc:  # pydantic.ValidationError et al -- see docstring
        raise BenchError(
            f"Existing run record at {path} does not match the expected schema: {exc}"
        ) from exc


def _persist_record(path: Path, record: BenchRunRecord) -> None:
    """Write-temp + atomic-rename (mirrors `runstate.RunStateStore.save`) so a crash
    mid-write can never leave a corrupt/partial `run.json` -- the resume contract
    (design §4.5 risk) depends on this file always being valid JSON.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(record.model_dump_json(indent=2))
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Small pure helpers
# ---------------------------------------------------------------------------


def compute_bench_run_id(
    suite: BenchSuite, subject_spec: SubjectSpec, clock: Callable[[], datetime]
) -> str:
    """`<utc-date>-<suite.id>-<subject.id>` (design §4.5) -- stable within a day so a
    same-day re-run resumes into the same result dir/record; a new day mints a fresh
    one (ASSUMPTION A5: only the harness scaffolding is deterministic, not scores).
    Exposed (not `_`-prefixed) so `T-Cli8Nf`'s CLI can predict/report the result dir
    without duplicating this derivation.
    """
    return f"{clock().strftime(_BENCH_RUN_ID_DATE_FORMAT)}-{suite.id}-{subject_spec.id}"


def resolve_result_dir(bench_run_id: str, *, out_dir: str | Path | None = None) -> Path:
    """`benchmarks/results/<bench_run_id>/` (or `out_dir/<bench_run_id>` override --
    used by tests and any future `--out-dir` CLI flag). Exposed for the same reason as
    `compute_bench_run_id`.
    """
    root = Path(out_dir) if out_dir is not None else BENCH_RESULTS_ROOT
    return root / bench_run_id


def _effective_timeout(task: BenchTask, suite: BenchSuite, default_timeout: int | None) -> int:
    """Per-task bound. Design §4.5's pseudocode is `task.timeout_seconds or
    opts.default_timeout`; extended here with the suite's own
    `defaults.timeout_seconds` as a middle tier (so that schema field, otherwise
    unconsumed by anything in the landed `bench/` modules, is not dead weight) and a
    builtin last resort so a task can never run unbounded.
    """
    return (
        task.timeout_seconds
        or default_timeout
        or suite.defaults.timeout_seconds
        or DEFAULT_TASK_TIMEOUT_SECONDS
    )


def _errored_result(
    ws_path: Path, wall_clock_seconds: float, raw_error: str
) -> tuple[SubjectResult, GradeResult]:
    """Design §4.5: `EXCEPT BenchError as e: sr=SubjectResult("error",...);
    gr=GradeResult(False,0.0,...,str(e))`. One shared error path for a `Subject.run`
    OR `Grader.grade` failure -- the mandatory integration note (epic STATUS.md) is a
    SINGLE try/except spanning both calls, so either one failing marks the whole task
    as errored rather than keeping a possibly-misleading partial subject result.
    """
    capture_dir = str(ws_path / CAPTURE_DIRNAME)
    sr = SubjectResult(
        status="error",
        wall_clock_seconds=wall_clock_seconds,
        capture_dir=capture_dir,
        raw_error=raw_error,
    )
    gr = GradeResult(solved=False, score=0.0, detail={}, raw_tail=raw_error)
    return sr, gr


# ---------------------------------------------------------------------------
# The runner
# ---------------------------------------------------------------------------


def run_suite(
    suite_path: str | Path,
    subject_path: str | Path,
    *,
    out_dir: str | Path | None = None,
    force: bool = False,
    task_filter: Iterable[str] | None = None,
    budget_total: int | None = None,
    max_turns: int | None = None,
    default_timeout: int | None = None,
    cost_budget_usd: float | None = None,
    max_parallel: int = 1,
    clock: Callable[[], datetime] | None = None,
) -> BenchRunRecord:
    """Run *suite* against *subject* end to end (design §4.5).

    Deterministic id-sorted task order; resumable (a task already present in an
    existing `run.json` for this `bench_run_id` is skipped unless `force=True`);
    bounded per task (`_effective_timeout`); one task's crash never aborts the run;
    `run.json` is persisted (write-temp + atomic rename) after every task that
    actually runs, so an interrupted run leaves a resumable file. Structured
    `bench.run.start` / `bench.task.start` / `bench.task.end` / `bench.run.end` log
    events (design §8) are emitted via the reused core `logging_setup` (a per-run log
    file under the GITIGNORED bench workspace, never inside the committed results
    dir).

    Parameters
    ----------
    suite_path, subject_path:
        Paths to a `suite.json`/`subject.json` (or `.yaml`) -- loaded and validated
        via `bench.spec.load_suite`/`load_subject` (propagates `SpecValidationError`
        as-is on a malformed/invalid spec -- a fail-fast top-level error, not a
        per-task concern).
    out_dir:
        Override for the results root (default `benchmarks/results/`, committed).
        Used by tests to avoid writing into the real committed tree.
    force:
        Re-run every considered task even if already recorded (default: skip).
    task_filter:
        Restrict this call to a subset of task ids (e.g. a CLI `--task` flag).
        Previously recorded tasks NOT in the filter are left untouched in the record.
    budget_total, max_turns, default_timeout:
        Run-level overrides threaded onto each task's `RunContext`
        (`budget_total`/`max_turns`) or into `_effective_timeout`
        (`default_timeout`) -- CLI/run-level, beating a subject's own spec default
        but losing to a task's own explicit `timeout_seconds`.
    cost_budget_usd:
        Hard USD cost cap for this (suite x subject) run (ADR-0009 D3), DISTINCT from
        the pre-existing token `budget_total` above. `None` (the default) means
        unlimited -- no budget check at all -- so every pre-existing caller of
        `run_suite` that does not pass this is unaffected. The CLI (`bench/cli.py`)
        always resolves a concrete cap before calling in here (explicit
        `--cost-budget-usd` > the suite's tier default via `bench/tiers.py`); `None` is
        for direct callers (tests, or a future caller) that explicitly want no cap.
        Before scheduling each task, if the cumulative `cost_usd` of already-recorded,
        non-`skipped_budget` tasks THIS run has reached the cap, every remaining task is
        recorded with `subject_status="skipped_budget"` (solved=False, score=0,
        cost=0) instead of being materialized/run -- check-before-schedule, so overshoot
        is bounded to at most the one task that was already in flight when the cap was
        crossed. A `skipped_budget` record is NEVER a harness failure (`bench/cli.py`'s
        `_HARNESS_FAILURE_STATUSES` excludes it) and is excluded from `config_fingerprint`
        (control-flow, not subject-affecting config) -- so raising the cap between two
        same-day runs never trips the W4 mismatch guard. On resume, a task already
        recorded as `skipped_budget` is the ONE exception to "already recorded -> skip":
        it is re-attempted under the current cap (every other recorded status still
        skips), so raising the cap and re-running finishes a previously budget-capped
        suite.
    max_parallel:
        Bounded task-level parallelism (ADR-0009 D4). Default `1` = strictly serial,
        BYTE-IDENTICAL to the pre-parallelism behavior (every pre-existing caller that
        does not pass this is unaffected). `>1` runs up to `max_parallel` tasks
        concurrently via a `ThreadPoolExecutor` (tasks are independent -- no DAG --
        and per-task workspaces are already disjoint, so this is safe); the budget
        check-before-schedule, `tasks_dict` mutation, running-cost accumulation, and
        `run.json` persist are all guarded by one lock so they stay correct under
        concurrency. Persisted `tasks` order is always id-sorted regardless of
        completion order. Overshoot on `cost_budget_usd` is bounded to at most
        `max_parallel` tasks that were already in flight (past the check, not yet
        added to the running total) when the cap was crossed -- i.e. cumulative
        recorded cost lands in `[cap, cap + max_parallel * max_task_cost)`, not
        exactly at the cap. Deliberately excluded from `config_fingerprint` (like
        `cost_budget_usd`): it is control-flow (how fast a run executes), not config
        that changes what a subject DOES per task, so resuming with a different
        `max_parallel` never trips the W4 mismatch guard. The CLI's own default
        resolution (`--max-parallel` > the suite's tier `default_max_parallel` via
        `bench/tiers.py`) is a caller concern, not this function's.
    clock:
        Injectable UTC clock (CLAUDE.md determinism rule) -- defaults to
        `datetime.now(UTC)`.

    Raises
    ------
    SpecValidationError
        *suite_path*/*subject_path* fails to load/validate.
    BenchError
        *subject_path*'s `type` has no registered `Subject` class, an existing
        `run.json` at the resume path is corrupt/schema-mismatched, or (W4,
        `force=False` only) an existing `run.json`'s `config_fingerprint` no longer
        matches the freshly computed one -- the subject/suite config changed since
        the completed tasks on record were produced.
    """
    resolved_clock = clock or _utc_now

    suite = load_suite(suite_path)
    subject_spec = load_subject(subject_path)
    if subject_spec.type not in SUBJECT_REGISTRY:
        raise BenchError(
            f"No Subject registered for type {subject_spec.type!r} "
            f"(known: {sorted(SUBJECT_REGISTRY)})"
        )

    suite_base_dir = Path(suite_path).resolve().parent
    subject_base_dir = Path(subject_path).resolve().parent

    bench_run_id = compute_bench_run_id(suite, subject_spec, resolved_clock)
    result_path = resolve_result_dir(bench_run_id, out_dir=out_dir) / RUN_JSON_FILENAME
    # Mirrors design §4.5's own `ws_root/subject_spec.id/task.id` layout (the subject
    # id also already appears inside `bench_run_id` -- kept as specified so downstream
    # tooling/consumers expecting this exact shape are not surprised).
    run_workspace_root = BENCH_WORKSPACE_ROOT / bench_run_id
    subject_ws_root = run_workspace_root / subject_spec.id

    attach_run_handler(bench_run_id, str(run_workspace_root / BENCH_RUN_LOG_FILENAME))
    run_log = get_run_logger(bench_run_id)
    try:
        existing_record = _load_existing_record(result_path)
        tasks_dict: dict[str, BenchTaskRecord]
        started_at: str
        if existing_record is not None:
            started_at = existing_record.started_at
            tasks_dict = {t.task_id: t for t in existing_record.tasks}
        else:
            started_at = resolved_clock().isoformat()
            tasks_dict = {}

        # ADR-0009 D3: cumulative USD spend for THIS run, recomputed (not merely
        # carried over) from already-recorded, non-`skipped_budget` tasks -- a
        # `skipped_budget` record never actually ran and cost $0, so counting it here
        # would double-penalize a resumed run. `cost_usd is None` (AC6, e.g. a missing
        # `state.json`) is treated as $0 for this running total: an unknown cost must
        # never itself look like unbounded spend that starves every later task.
        #
        # ADR-0009 D4: this running total, the check-before-schedule read of it, and
        # the `tasks_dict[...] = ...` + `running_cost += ...` writes after each task
        # all happen under `_run_lock` (defined below, right before it is first used)
        # -- the SAME lock across all three -- so a `running_cost >= cap` check and
        # the following schedule/append are atomic across worker threads. At
        # `max_parallel<=1` there is only ever one in-flight task, so the lock is
        # uncontended and this is a plain float in practice (byte-identical to the
        # pre-parallelism behavior).
        running_cost: float = sum(
            (t.cost_usd or 0.0) for t in tasks_dict.values() if t.subject_status != "skipped_budget"
        )

        tasks_to_consider = sorted(suite.tasks, key=lambda t: t.id)
        if task_filter is not None:
            filter_set = set(task_filter)
            tasks_to_consider = [t for t in tasks_to_consider if t.id in filter_set]

        ao_version = _ao_version()
        claude_version = _probe_claude_version()
        # `cost_budget_usd` and `max_parallel` (ADR-0009 D3/D4) are DELIBERATELY ABSENT
        # from `overrides` (and thus from `run_fingerprint`): both are control-flow
        # ("how many tasks run" / "how fast", like `force`/`task_filter`), not config
        # that changes what a subject DOES per task -- so changing either between two
        # same-day runs must never trip the W4 "config changed" resume guard.
        overrides: dict[str, Any] = {
            "budget_total": budget_total,
            "max_turns": max_turns,
            "default_timeout": default_timeout,
        }
        run_fingerprint = _run_config_fingerprint(
            suite, subject_spec, overrides, ao_version, claude_version
        )

        # W4: a same-day resume (same bench_run_id) whose suite/subject config was
        # mutated since the tasks already on `existing_record` were produced must not
        # silently overwrite the run-level fingerprint with the new one -- that would
        # misrepresent completed tasks as having been produced under today's config.
        # `force=True` means "re-run everything under the new config", so the mismatch
        # is moot there (every task_id in tasks_dict is about to be overwritten below).
        if (
            existing_record is not None
            and not force
            and existing_record.config_fingerprint != run_fingerprint
        ):
            raise BenchError(
                f"Config changed since bench_run_id {bench_run_id!r} was last run: "
                f"existing config_fingerprint={existing_record.config_fingerprint!r}, "
                f"current={run_fingerprint!r}. The subject or suite config was mutated "
                "mid-run, so resuming would mix tasks produced under two different "
                "configs. Re-run with force=True (--force) to re-run every task under "
                "the new config, or restore the previous subject/suite config to "
                "resume as-is."
            )

        subject_info = BenchRunSubjectInfo(
            id=subject_spec.id,
            type=subject_spec.type,
            model=subject_spec.model,
            resolved_config=subject_spec.model_dump(mode="json"),
        )
        env = BenchRunEnv(
            ao_version=ao_version,
            claude_version=claude_version,
            os=platform.platform(),
            git_sha=_git_sha(),
        )

        def _build_record(ended_at: str | None) -> BenchRunRecord:
            ordered_tasks = [tasks_dict[tid] for tid in sorted(tasks_dict)]
            return BenchRunRecord(
                bench_run_id=bench_run_id,
                suite_id=suite.id,
                domain=suite.domain,
                subject=subject_info,
                config_fingerprint=run_fingerprint,
                started_at=started_at,
                ended_at=ended_at,
                env=env,
                tasks=ordered_tasks,
                # `TaskMetric`-typed by `aggregate`'s signature; `BenchTaskRecord` IS
                # one (adds fields, drops none) -- safe covariant read-only usage,
                # `list` invariance is the only reason mypy needs the cast.
                aggregate=aggregate(cast("list[TaskMetric]", ordered_tasks)),
            )

        run_log.info(
            "bench.run.start",
            extra={
                "event": "bench.run.start",
                "bench_run_id": bench_run_id,
                "suite": suite.id,
                "subject": subject_spec.id,
                "task_count": len(tasks_to_consider),
                "ao_version": ao_version,
                "claude_version": claude_version,
                "max_parallel": max_parallel,
            },
        )

        record: BenchRunRecord | None = None

        # ADR-0009 D4: the ONE lock guarding every piece of state shared across
        # concurrently-dispatched tasks -- the budget check-before-schedule, the
        # `tasks_dict` mutation, the running-cost accumulation, and the `run.json`
        # persist (`_dispatch_or_skip`/`_run_one` below, both nested closures so they
        # can reach `run_suite`'s locals without threading a dozen params through).
        # At `max_parallel<=1` this lock is never contended (one task in flight at a
        # time), so acquiring/releasing it changes nothing observable -- byte-identical
        # to the pre-parallelism serial path.
        _run_lock = threading.Lock()

        def _dispatch_or_skip(task: BenchTask) -> bool:
            """Returns True iff *task* should be materialized/run/graded (by the
            caller, OUTSIDE this lock -- kept short here so holding it never blocks
            another worker's actual subject.run/grader.grade). A "no" is either
            "already recorded, nothing to do" (resume skip) or "budget cap already
            reached" (recorded directly as `skipped_budget` and persisted, right here
            under the lock, so that write is atomic with the check that produced it).
            """
            nonlocal record
            with _run_lock:
                if task.id in tasks_dict and not force:
                    # ADR-0009 D3 resume rule: a recorded `skipped_budget` task is the
                    # ONE exception to "already recorded -> skip" -- it never actually
                    # ran, so it is treated as not-yet-run and falls through to be
                    # re-attempted (subject to today's budget check, below). Every
                    # OTHER recorded status still means "already done", unchanged.
                    if tasks_dict[task.id].subject_status != "skipped_budget":
                        run_log.info(
                            "bench.task.skip",
                            extra={"event": "bench.task.skip", "task_id": task.id},
                        )
                        return False

                if cost_budget_usd is not None and running_cost >= cost_budget_usd:
                    # Check-before-schedule (ADR-0009 D3), now concurrency-correct
                    # (D4): the read of `running_cost` and this skip-record+persist
                    # happen atomically with every other worker's identical check, so
                    # two workers can never both pass the check on the same "last
                    # dollar" of headroom. Overshoot is still possible -- but only from
                    # tasks that passed this check EARLIER and are still in flight
                    # (materializing/running/grading OUTSIDE the lock, cost not yet
                    # added) -- bounded to at most `max_parallel` such tasks. No
                    # workspace is materialized and no subprocess is spawned for this
                    # task: it is recorded directly as `skipped_budget` (solved=False,
                    # score=0, cost=0) so every remaining task also gets a record -- a
                    # clean, resumable, NON-failing outcome (never a harness failure;
                    # see bench/cli.py's `_HARNESS_FAILURE_STATUSES`).
                    budget_skip_at = resolved_clock().isoformat()
                    skip_sr = _budget_skipped_result()
                    skip_gr = GradeResult(solved=False, score=0.0, detail={}, raw_tail="")
                    skip_fingerprint = _task_config_fingerprint(
                        task, subject_spec, ao_version, suite_base_dir
                    )
                    skip_metric = build_task_metric(
                        subject_spec.id,
                        task,
                        suite.domain,
                        cast(SubjectResultLike, skip_sr),
                        skip_gr,
                        skip_fingerprint,
                        budget_skip_at,
                        budget_skip_at,
                    )
                    tasks_dict[task.id] = BenchTaskRecord(
                        **skip_metric.model_dump(),
                        raw_error=None,
                        grader_detail=skip_gr.detail,
                        grader_raw_tail=skip_gr.raw_tail,
                    )
                    # `running_cost` is unchanged: a skipped_budget task always costs $0.
                    record = _build_record(budget_skip_at)
                    _persist_record(result_path, record)
                    run_log.info(
                        "bench.task.skip_budget",
                        extra={
                            "event": "bench.task.skip_budget",
                            "task_id": task.id,
                            "running_cost": running_cost,
                            "cap": cost_budget_usd,
                        },
                    )
                    return False

                return True

        def _run_one(task: BenchTask) -> None:
            """The full per-task body: dispatch decision, then (if scheduled)
            materialize/run/grade OUTSIDE the lock -- the long-running, per-task-
            isolated work -- then write the result back and persist INSIDE the lock.
            Safe to call from multiple worker threads concurrently: every access to
            shared state (`tasks_dict`, `running_cost`, `record`, `result_path` on
            disk) is confined to the two `with _run_lock:` sections.
            """
            nonlocal running_cost, record
            if not _dispatch_or_skip(task):
                return

            task_log = get_run_logger(bench_run_id, task_id=task.id)
            ws_path = subject_ws_root / task.id
            task_started_at = resolved_clock().isoformat()
            t0 = time.monotonic()
            task_log.info(
                "bench.task.start",
                extra={
                    "event": "bench.task.start",
                    "task_id": task.id,
                    "category": task.category,
                    "workspace": str(ws_path),
                },
            )

            sr: SubjectResult
            gr: GradeResult
            try:
                ctx = materialize_workspace(
                    task,
                    ws_path,
                    suite_base_dir=suite_base_dir,
                    timeout_seconds=_effective_timeout(task, suite, default_timeout),
                    budget_total=budget_total,
                    max_turns=max_turns,
                    subject_base_dir=subject_base_dir,
                )
                subject_cls = SUBJECT_REGISTRY[subject_spec.type]
                sr = subject_cls(subject_spec).run(task, ctx)
                grader_cls = GRADER_REGISTRY[task.grader.type]
                gr = grader_cls().grade(task.grader, ctx)
            except BenchError as exc:
                sr, gr = _errored_result(ws_path, time.monotonic() - t0, str(exc))
            except Exception as exc:  # noqa: BLE001 -- per-task isolation boundary (§4.5)
                task_log.error(
                    "bench.task.unexpected_error",
                    extra={"event": "bench.task.unexpected_error", "task_id": task.id},
                    exc_info=True,
                )
                sr, gr = _errored_result(
                    ws_path, time.monotonic() - t0, f"unexpected error: {exc!r}"
                )

            task_ended_at = resolved_clock().isoformat()
            fingerprint = _task_config_fingerprint(task, subject_spec, ao_version, suite_base_dir)
            metric = build_task_metric(
                subject_spec.id,
                task,
                suite.domain,
                # `SubjectResult.status` is a `Literal[...]` (subjects.py) while the
                # `SubjectResultLike` Protocol (metrics.py) declares `status: str` --
                # a real subtype, but mypy's structural Protocol matching treats plain
                # attributes as invariant, so it flags this without a cast.
                cast(SubjectResultLike, sr),
                gr,
                fingerprint,
                task_started_at,
                task_ended_at,
            )
            task_record = BenchTaskRecord(
                **metric.model_dump(),
                raw_error=sr.raw_error,
                grader_detail=gr.detail,
                grader_raw_tail=gr.raw_tail,
            )

            with _run_lock:
                tasks_dict[task.id] = task_record
                # ADR-0009 D3/D4 accounting: a `None` cost (AC6, e.g. a missing
                # state.json) counts as $0 toward the running total, same convention
                # as its initial computation above. Guarded by the same lock as the
                # budget check in `_dispatch_or_skip` so the two can never race.
                running_cost += sr.cost_usd or 0.0
                record = _build_record(task_ended_at)
                _persist_record(result_path, record)

            task_log.info(
                "bench.task.end",
                extra={
                    "event": "bench.task.end",
                    "task_id": task.id,
                    "solved": gr.solved,
                    "score": gr.score,
                    "cost_usd": sr.cost_usd,
                    "wall_clock_seconds": sr.wall_clock_seconds,
                    "subject_status": sr.status,
                },
            )

        if max_parallel <= 1:
            # Exact pre-parallelism serial path: a plain for-loop, one task fully
            # completing (including its persist + log events) before the next starts.
            for task in tasks_to_consider:
                _run_one(task)
        else:
            # Bounded concurrency (ADR-0009 D4): `ThreadPoolExecutor.map` submits every
            # task up front (queued FIFO, `max_parallel` running at a time) and
            # `list(...)` blocks until all have completed -- exceptions from a single
            # task never surface here (isolated inside `_run_one`'s own try/except),
            # so one crashing task can never abort the pool.
            with ThreadPoolExecutor(max_workers=max_parallel) as executor:
                list(executor.map(_run_one, tasks_to_consider))

        if record is not None:
            final_record = record
        elif existing_record is not None:
            final_record = existing_record
        else:
            # Nothing existed AND nothing ran this call (e.g. task_filter matched no
            # suite tasks) -- an in-memory-only record, never written to disk.
            final_record = _build_record(None)

        run_log.info(
            "bench.run.end",
            extra={
                "event": "bench.run.end",
                "bench_run_id": bench_run_id,
                "solved": final_record.aggregate.solved,
                "total": final_record.aggregate.total,
                "solve_rate": final_record.aggregate.solve_rate,
                "total_cost_usd": final_record.aggregate.total_cost_usd,
            },
        )
        return final_record
    finally:
        detach_run_handler(bench_run_id)
