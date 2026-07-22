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
import time
from collections.abc import Callable, Iterable
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
from .subjects import SubjectResult
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


def _task_config_fingerprint(task: BenchTask, subject_spec: SubjectSpec, ao_version: str) -> str:
    """Per-task fingerprint (design §4.5: `sha256(canonical(task)+canonical(subject)+
    ao_version())`) -- what `BenchTaskRecord.config_fingerprint` (inherited from
    `TaskMetric`) records for AC6.
    """
    return _sha256_of(
        {
            "task": task.model_dump(mode="json"),
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
    clock:
        Injectable UTC clock (CLAUDE.md determinism rule) -- defaults to
        `datetime.now(UTC)`.

    Raises
    ------
    SpecValidationError
        *suite_path*/*subject_path* fails to load/validate.
    BenchError
        *subject_path*'s `type` has no registered `Subject` class, or an existing
        `run.json` at the resume path is corrupt/schema-mismatched.
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

        tasks_to_consider = sorted(suite.tasks, key=lambda t: t.id)
        if task_filter is not None:
            filter_set = set(task_filter)
            tasks_to_consider = [t for t in tasks_to_consider if t.id in filter_set]

        ao_version = _ao_version()
        claude_version = _probe_claude_version()
        overrides: dict[str, Any] = {
            "budget_total": budget_total,
            "max_turns": max_turns,
            "default_timeout": default_timeout,
        }
        run_fingerprint = _run_config_fingerprint(
            suite, subject_spec, overrides, ao_version, claude_version
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
            },
        )

        record: BenchRunRecord | None = None
        for task in tasks_to_consider:
            if task.id in tasks_dict and not force:
                run_log.info(
                    "bench.task.skip",
                    extra={"event": "bench.task.skip", "task_id": task.id},
                )
                continue

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
            fingerprint = _task_config_fingerprint(task, subject_spec, ao_version)
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
            tasks_dict[task.id] = BenchTaskRecord(
                **metric.model_dump(),
                raw_error=sr.raw_error,
                grader_detail=gr.detail,
                grader_raw_tail=gr.raw_tail,
            )

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
