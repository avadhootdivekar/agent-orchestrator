#!/usr/bin/env python3
"""overseer_tool.py -- overseer-runner template's deterministic governance tool.

Stdlib only, Python >= 3.11 (NFR-5). Ships as a `files` entry in this template's
`template.yaml` (T-eGXqXH, later task); `templates/__init__.py::_render` runs over every
`files` entry's content (not just its target path), substituting any double-curly-brace
placeholder that wraps a bare identifier. This source must never contain such a placeholder
(one would break rendering with an "unknown template variable" error) -- it avoids literal
doubled braces entirely, so a plain grep for two open/close curly braces in a row stays a
safe, sufficient check on this file.

Design: `docs-md/overseer-runner-hld.md` S8.2 (budget stage machine), S8.3 (loop/integrity
model), S8.4 module M1 (this module's pseudocode), S13.1 (template params), S13.4 (artifact
schemas). Ticket: `meta/tickets/E-YAAGhk-overseer-runner-template/`
`T-ABDjSj-tool-state-ledger-budget/`.

Module map (kept additive on purpose -- three LATER tasks add to this same file):
  M1  Config/state/ledger/budget/hold/charter-lock/unit-gate/request-closeout.
  M2  Signal detectors (T-C6uQJW, THIS task): `detect_period`/`detect_mirror`/`detect_signals`/
      `compute_progress`, plus `update_path_history`'s repo_heads/git-diff/trim extensions.
  M3  Contract checkers `intake-check`/`ckpt-check`/`expander-check` (T-HPJcc6/T-tAKBBB) --
      NOT implemented here; a future task adds new subcommands + dispatch entries only.

Section order mirrors ADR-0016 D4 (extraction readiness): every PURE function (no file I/O,
no clock, no subprocess -- budget math, cadence math, chain hashing, path classification)
lives in one block so it can be lifted into a real package module later without touching
anything else.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import math
import os
import re
import statistics
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Final

# =============================================================================================
# ===== Constants (HLD S8.2/S8.3/S8.4 + ticket "Pseudocode / Algorithm" constants list) ======
# =============================================================================================

JSON_MAX_BYTES: Final[int] = 1_048_576  # 1 MiB -- every AGENT-authored JSON the tool reads (NFR-6).
STATE_MAX_BYTES: Final[int] = 16 * 1024 * 1024  # state.json is a full RunState dump (A-2).
MAX_TRACKED_PATHS: Final[int] = 200  # NFR-6 / HLD S8.3.
HASH_SKIP_BYTES: Final[int] = 50 * 1024 * 1024  # NFR-6: skip content-hashing files > 50 MiB.
GIT_TIMEOUT_S: Final[int] = 30  # Reserved for T-C6uQJW's bounded `git diff`/`git status` calls.
MAX_CHANGED_PATHS_PER_UNIT: Final[int] = 50  # Mirrors the breadcrumb schema's own cap (S13.4).

TAIL_TASKS_WITH_FINAL_PUSH: Final[int] = 3  # final-verify + closeout + final-push (S8.2).
TAIL_TASKS_WITHOUT_FINAL_PUSH: Final[int] = 2  # final-verify + closeout only.

# A first checkpoint (or a fake executor reporting no durations at all) must not starve the
# first wave -- "no data" (an empty list) gets a large default; ZERO durations/costs are real
# data and are used as-is (HLD S8.4 M1 edge cases; ticket step 7).
DEFAULT_TIME_CAP_TASKS: Final[int] = 1_000_000

STAGE_ORDER: Final[tuple[str, ...]] = ("explore", "converge", "stabilize", "closeout")
SETTLED_TASK_STATUSES: Final[frozenset[str]] = frozenset({"succeeded", "failed", "skipped"})
DEFAULT_RUNS_ROOT: Final[str] = ".orchestrator/runs"
RESULT_FILENAME: Final[str] = "prep-result.json"

CONFIG_SCHEMA: Final[str] = "ao.overseer.config/v1"
LEDGER_SCHEMA: Final[str] = "ao.overseer.ledger/v1"
DIGEST_SCHEMA: Final[str] = "ao.overseer.digest/v1"
BREADCRUMB_SCHEMA: Final[str] = "ao.overseer.breadcrumb/v1"
# Not one of HLD S13.4's frozen artifact schemas -- this tool's own result-file convention
# (mirrors the engine's generic hook-result idea, HLD S14, but is a domain-specific file this
# tool additionally writes into its own instance-dir output tree for human/agent debugging).
PREP_RESULT_SCHEMA: Final[str] = "ao.overseer.prep-result/v1"

_ALLOWED_DECISIONS_BY_STAGE: Final[dict[str, tuple[str, ...]]] = {
    # R11's "existing-items-only" restriction on converge's continue/redirect is a CHECKER
    # concern (M3, later task) -- this table only fixes the *set* of allowed decisions per
    # the HLD S8.2 table, exactly as this task's ticket AC1(k) asks for.
    "explore": ("continue", "redirect", "hold", "stabilize", "closeout"),
    "converge": ("continue", "redirect", "hold", "stabilize", "closeout"),
    "stabilize": ("stabilize", "closeout", "hold"),
    "closeout": ("closeout",),
}

_OUTPUT_SUBDIRS: Final[tuple[str, ...]] = (
    "outputs/overseer",
    "outputs/manifests",
    "outputs/progress",
    "outputs/waves",
    "outputs/checkpoints",
    "outputs/final",
)

_WAVE_UNIT_PREFIX_RE: Final[re.Pattern[str]] = re.compile(r"^w(\d{2})-\d{2}-")
_CK_TASK_ID_RE: Final[re.Pattern[str]] = re.compile(r"^ck-(\d+)$")


# =============================================================================================
# ===== Exceptions =====
# =============================================================================================


class ToolError(Exception):
    """Internal/environment error (exit 1) -- never a data-contract rule violation.

    Reserved for wiring problems this design doesn't assign a rule id to (a missing hook
    context, an out-of-workspace `--instance-dir`, an unparseable checkpoint task id). `main`
    catches this via the generic `except Exception` branch and prints
    "overseer_tool internal error: <repr>" per the M1 pseudocode's `main()` skeleton.
    """


class OversizeInputError(ToolError):
    """Raised by `read_json_bounded` when a file exceeds its byte cap (NFR-6)."""

    def __init__(self, path: Path, size: int, max_bytes: int) -> None:
        super().__init__(f"{path} is {size} bytes, exceeds the {max_bytes} byte cap")
        self.path = path
        self.size = size
        self.max_bytes = max_bytes


class Violation(ToolError):
    """A rule-id-carrying contract violation (exit 2).

    HLD S13.3: rule ids are namespaced with an `OV-` prefix when printed, so they never
    collide with the engine's own V1-V13/R-21 ids. That default formatting is used for every
    rule id EXCEPT `HOLD`/`BUDGET`/`FANOUT`, whose exact leading text
    ("HOLD:"/"BUDGET:"/"FANOUT:") is mandated verbatim by this task's own instructions (a
    later e2e test greps stderr for that literal prefix) -- those three call sites pass
    `message=` explicitly instead of relying on the `OV-<id>` default.
    """

    def __init__(
        self, rule_id: str, message: str | None = None, *, detail: str | None = None
    ) -> None:
        self.rule_id = rule_id
        self.detail = detail
        if message is not None:
            self.message = message
        else:
            self.message = f"OV-{rule_id}" + (f": {detail}" if detail else "")
        super().__init__(self.message)

    def __str__(self) -> str:  # pragma: no cover -- trivial
        return self.message


# =============================================================================================
# ===== PURE FUNCTIONS -- no I/O, no clock, no subprocess (ADR-0016 D4) ======================
# ===== Budget stage machine (HLD S8.2), cadence math, chain hashing, path classification =====
# =============================================================================================


def canonical_json(obj: Any) -> str:
    """Deterministic JSON serialization used for ledger chain hashing (HLD S8.4)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def stage_of(
    pct_used: float, converge_pct: float, stabilize_pct: float, closeout_pct: float
) -> str:
    """HLD S8.2 `stage_of`: which stage a % of budget used falls into."""
    if pct_used < converge_pct:
        return "explore"
    if pct_used < stabilize_pct:
        return "converge"
    if pct_used < closeout_pct:
        return "stabilize"
    return "closeout"


def stage_rank(stage: str) -> int:
    return STAGE_ORDER.index(stage)


def max_stage(a: str, b: str) -> str:
    """The latch (HLD S8.2): `stage = max(stage_proj, prev_stage)`, monotonic."""
    return a if stage_rank(a) >= stage_rank(b) else b


def next_threshold(
    stage: str, converge_pct: float, stabilize_pct: float, closeout_pct: float
) -> float:
    return {
        "explore": converge_pct,
        "converge": stabilize_pct,
        "stabilize": closeout_pct,
        "closeout": 100.0,
    }[stage]


def tail_tasks_count(final_push: bool) -> int:
    """`TAIL_TASKS` (HLD S8.2): final-verify + closeout (+ final-push if enabled)."""
    return TAIL_TASKS_WITH_FINAL_PUSH if final_push else TAIL_TASKS_WITHOUT_FINAL_PUSH


def median_or_default(values: list[float], default: float) -> float:
    """Median of *values*, or *default* only when *values* is truly empty.

    Zeros are real data (a fake executor reporting $0 cost, or an instant unit), never
    treated as "no data" -- only an empty list counts as missing (HLD S8.4 M1 edge cases).
    """
    return statistics.median(values) if values else default


def time_cap_from_median_duration(median_duration_s: float | None, wave_max_minutes: float) -> int:
    """Task-count time budget for the next wave (HLD S8.4 ticket step 7).

    `None` (no settled-unit duration data at all) or a zero median (durations that exist but
    are literally instantaneous) both mean "no time constraint" -- the first case because
    there is nothing to compute from, the second because zero-duration units impose none.
    """
    if median_duration_s is None or median_duration_s <= 0:
        return DEFAULT_TIME_CAP_TASKS
    return math.floor(wave_max_minutes * 60.0 / median_duration_s)


def budget_cap_for_threshold(
    spent: float,
    est_unit_cost_usd: float,
    est_ckpt_cost_usd: float,
    reserve_tail_usd: float,
    run_budget_usd: float,
    threshold_pct: float,
    wave_size: int,
) -> int:
    """Largest ``n`` in ``[0, wave_size]`` such that
    ``spent + n*est_unit + est_ckpt + reserve_tail <= run_budget_usd * threshold_pct/100``
    (HLD S8.2 `budget_cap`).
    """
    limit = run_budget_usd * threshold_pct / 100.0
    for n in range(wave_size, -1, -1):
        if spent + n * est_unit_cost_usd + est_ckpt_cost_usd + reserve_tail_usd <= limit:
            return n
    return 0


def compute_allowed_wave_size(
    stage: str,
    wave_size: int,
    stabilize_wave_size: int,
    time_cap: int,
    budget_cap: int,
    budget_cap_to_100pct: int,
) -> int:
    """HLD S8.2 `allowed_wave_size` table."""
    if stage in ("explore", "converge"):
        return int(max(1, min(wave_size, time_cap, budget_cap)))
    if stage == "stabilize":
        return int(max(1, min(stabilize_wave_size, budget_cap_to_100pct)))
    return 0  # closeout


def compute_must_close(
    stage: str,
    k: int,
    max_waves: int,
    stabilize_passes: int,
    max_stabilize_passes: int,
    forced_closeout: bool,
) -> tuple[bool, list[str]]:
    """HLD S8.2 `must_close`. Returns ``(must_close, reasons)`` -- every applicable reason is
    kept (not just the first) so the digest can show all of them at once (NFR-9 legibility).
    """
    reasons: list[str] = []
    if stage == "closeout":
        reasons.append("stage=closeout")
    if k >= max_waves:
        reasons.append(f"K>={max_waves} (max_waves)")
    if stabilize_passes >= max_stabilize_passes:
        reasons.append(f"stabilize_passes>={max_stabilize_passes} (max_stabilize_passes)")
    if forced_closeout:
        reasons.append("forced_closeout event present")
    return (len(reasons) > 0, reasons)


def allowed_decisions_for_stage(stage: str, must_close: bool) -> list[str]:
    if must_close:
        return ["closeout"]
    return list(_ALLOWED_DECISIONS_BY_STAGE[stage])


@dataclass(frozen=True)
class BudgetResult:
    """Pure output of `derive_budget` -- everything `compute_budget` (the I/O orchestrator)
    needs to assemble the digest's `budget`/`cadence`/`must_close*`/`allowed_decisions` pieces.
    """

    stage_raw: str
    stage_projected: str
    stage: str
    n_plan: int
    reserve_tail_usd: float
    budget_cap: int
    budget_cap_to_100pct: int
    allowed_wave_size: int
    must_close: bool
    must_close_reasons: list[str]
    allowed_decisions: list[str]
    pct_used: float


def derive_budget(
    *,
    spent: float,
    run_budget_usd: float,
    wave_size: int,
    time_cap: int,
    est_unit_cost_usd: float,
    est_ckpt_cost_usd: float,
    final_push: bool,
    prev_stage: str,
    converge_pct: float,
    stabilize_pct: float,
    closeout_pct: float,
    stabilize_wave_size: int,
    k: int,
    max_waves: int,
    stabilize_passes_before: int,
    max_stabilize_passes: int,
    forced_closeout: bool,
) -> BudgetResult:
    """HLD S8.2's stage machine end to end, as one pure function -- the table-driven unit
    tests (ticket AC1/AC2) exercise this directly, with no ledger/digest files on disk at all.
    """
    reserve_tail_usd = tail_tasks_count(final_push) * est_unit_cost_usd
    pct_used = 100.0 * spent / run_budget_usd
    stage_raw = stage_of(pct_used, converge_pct, stabilize_pct, closeout_pct)

    n_plan = min(wave_size, time_cap)
    projection = spent + n_plan * est_unit_cost_usd + est_ckpt_cost_usd + reserve_tail_usd
    stage_projected = stage_of(
        100.0 * projection / run_budget_usd, converge_pct, stabilize_pct, closeout_pct
    )
    stage = max_stage(stage_projected, prev_stage)

    threshold = next_threshold(stage, converge_pct, stabilize_pct, closeout_pct)
    budget_cap = budget_cap_for_threshold(
        spent,
        est_unit_cost_usd,
        est_ckpt_cost_usd,
        reserve_tail_usd,
        run_budget_usd,
        threshold,
        wave_size,
    )
    budget_cap_100 = budget_cap_for_threshold(
        spent,
        est_unit_cost_usd,
        est_ckpt_cost_usd,
        reserve_tail_usd,
        run_budget_usd,
        100.0,
        wave_size,
    )
    allowed_wave_size = compute_allowed_wave_size(
        stage, wave_size, stabilize_wave_size, time_cap, budget_cap, budget_cap_100
    )

    stabilize_passes = stabilize_passes_before + (1 if stage == "stabilize" else 0)
    must_close, reasons = compute_must_close(
        stage, k, max_waves, stabilize_passes, max_stabilize_passes, forced_closeout
    )
    allowed_decisions = allowed_decisions_for_stage(stage, must_close)

    return BudgetResult(
        stage_raw=stage_raw,
        stage_projected=stage_projected,
        stage=stage,
        n_plan=n_plan,
        reserve_tail_usd=reserve_tail_usd,
        budget_cap=budget_cap,
        budget_cap_to_100pct=budget_cap_100,
        allowed_wave_size=allowed_wave_size,
        must_close=must_close,
        must_close_reasons=reasons,
        allowed_decisions=allowed_decisions,
        pct_used=pct_used,
    )


@dataclass(frozen=True)
class PathClassification:
    """Result of `classify_path_entry` (AC6). Never raises -- a bad entry is DATA for the
    future `breadcrumb_integrity` signal (T-C6uQJW), not a fail-closed error (HLD S8.3: "one
    LLM path typo must not halt the run").
    """

    ok: bool
    repo_id: str | None
    rel_path: str | None
    abs_path: str | None
    reason: str | None


def classify_path_entry(entry: str, repo_paths: dict[str, str]) -> PathClassification:
    """Classify one breadcrumb `changed_paths` entry, `"<repo_id>:<rel path>"` (HLD S13.4).

    Rejects (NFR-1 mirror): an unknown `repo_id`, an absolute `rel_path`, a `rel_path`
    containing a `..` segment, or a `rel_path` that resolves (following symlinks) outside its
    repo root. The symlink check is the one syscall in this otherwise-pure function
    (`Path.resolve()`, read-only) -- kept here per ADR-0016 D4 since it is the single
    deterministic classification later signal work depends on.
    """
    if ":" not in entry:
        return PathClassification(
            False, None, None, None, "malformed entry (missing ':' separator)"
        )
    repo_id, rel_path = entry.split(":", 1)
    if repo_id not in repo_paths:
        return PathClassification(False, repo_id, rel_path, None, "unknown repo_id")
    posix_rel = PurePosixPath(rel_path)
    if posix_rel.is_absolute():
        return PathClassification(False, repo_id, rel_path, None, "absolute path")
    if ".." in posix_rel.parts:
        return PathClassification(False, repo_id, rel_path, None, "path traversal ('..')")
    repo_root = Path(repo_paths[repo_id]).resolve()
    candidate = (repo_root / rel_path).resolve()
    if candidate != repo_root and not candidate.is_relative_to(repo_root):
        return PathClassification(False, repo_id, rel_path, str(candidate), "escapes repo root")
    return PathClassification(True, repo_id, rel_path, str(candidate), None)


# =============================================================================================
# ===== I/O helpers: bounded read, atomic write, hook context, path confinement ==============
# =============================================================================================


def read_json_bounded(path: Path, max_bytes: int) -> Any:
    """Bounded JSON read (NFR-6): size-checked via `stat` BEFORE reading content.

    Raises `FileNotFoundError` (missing), `OversizeInputError` (too big), or
    `json.JSONDecodeError` (malformed) -- callers map each to the rule id that fits their
    context (CFG-0/ST-1/ST-2/etc.), since the same low-level reader backs several artifacts.
    """
    if not path.is_file():
        raise FileNotFoundError(str(path))
    size = path.stat().st_size
    if size > max_bytes:
        raise OversizeInputError(path, size, max_bytes)
    return json.loads(path.read_text(encoding="utf-8"))


def write_json_atomic(path: Path, data: Any) -> None:
    """Write *data* to *path* atomically: temp file in the same directory, then `os.replace`
    (atomic on POSIX). Confined to the same directory as *path* so the replace is never
    cross-filesystem.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=".tmp-", suffix=path.suffix or ".json", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
            fh.write("\n")
        os.replace(tmp_name, str(path))
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise


def confine_workspace(workspace_root: str, instance_dir: str) -> tuple[Path, Path]:
    """Resolve `--workspace-root`/`--instance-dir` and reject an instance dir that escapes the
    workspace root, following symlinks (NFR-1, mirrors `artifacts.py::LocalFsArtifactStore`'s
    own `resolve()` containment check, reimplemented standalone since this tool has zero
    repo-internal imports).
    """
    ws = Path(workspace_root).resolve()
    inst = (ws / instance_dir).resolve()
    if inst != ws and not inst.is_relative_to(ws):
        raise ToolError(
            f"--instance-dir {instance_dir!r} escapes --workspace-root {workspace_root!r}"
        )
    return ws, inst


def parse_now(value: str | None) -> datetime:
    """NFR-2 determinism: `--now` is the ONLY clock this tool ever reads from; absent it,
    falls back to the real clock (never called on any path a byte-reproducibility test uses).
    """
    if value is None:
        return datetime.now(UTC)
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def now_iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat()


@dataclass(frozen=True)
class HookContext:
    run_id: str
    task_id: str
    repo_paths: dict[str, str]
    output_paths: list[str]


def read_hook_context(args: argparse.Namespace) -> HookContext:
    """Reads `$AO_HOOK_CONTEXT_PATH` (`hooks.py::_hook_context_fields` /
    `engine.py::_hook_context_fields`, confirmed field names: `run_id`, `task_id`,
    `repo_paths` (dict[repo_id, abs path]), `output_paths`, plus other engine-only fields this
    tool ignores). Under `--dry-run`, `--task-id` substitutes for the context file's `task_id`
    (HLD S14: "the tool takes --task-id explicitly instead") -- everything else still comes
    from the context file, since `--dry-run` is meant to be run from within an already-hook-
    invoked session (e.g. an agent iterating on a not-yet-emitted checkpoint).
    """
    ctx_path = os.environ.get("AO_HOOK_CONTEXT_PATH")
    data: dict[str, Any] = {}
    if ctx_path:
        loaded = read_json_bounded(Path(ctx_path), JSON_MAX_BYTES)
        if isinstance(loaded, dict):
            data = loaded
    elif not args.dry_run:
        raise ToolError("AO_HOOK_CONTEXT_PATH is not set (only --dry-run may omit it)")

    run_id = data.get("run_id")
    if run_id is None:
        raise ToolError("hook context is missing run_id (AO_HOOK_CONTEXT_PATH)")

    task_id = data.get("task_id")
    if args.dry_run and getattr(args, "task_id", None):
        task_id = args.task_id
    if task_id is None:
        raise ToolError("hook context is missing task_id; pass --task-id under --dry-run")

    repo_paths = data.get("repo_paths") or {}
    output_paths = data.get("output_paths") or []
    return HookContext(
        run_id=str(run_id),
        task_id=str(task_id),
        repo_paths={str(k): str(v) for k, v in dict(repo_paths).items()},
        output_paths=[str(p) for p in output_paths],
    )


# =============================================================================================
# ===== Config (overseer-config.json, ao.overseer.config/v1) =================================
# =============================================================================================


@dataclass(frozen=True)
class Config:
    converge_pct: float
    stabilize_pct: float
    closeout_pct: float
    wave_size: int
    max_waves: int
    wave_max_minutes: float
    max_attempts_per_item: int
    max_expanders_per_wave: int
    max_injected_tasks: int
    final_push: bool
    overseer_effort: str
    overseer_model: str
    python_bin: str
    run_budget_usd: float
    task_budget_usd: float
    stall_waves: int
    stabilize_wave_size: int
    max_stabilize_passes: int
    sub_wave_size: int
    default_unit_cost_usd: float
    default_ckpt_cost_usd: float
    runs_root: str
    contract_version: int
    kind_map: dict[str, Any] = field(default_factory=dict)


def _require_number(data: dict[str, Any], field_name: str) -> float:
    """CFG-0 covers both a malformed config FILE and a single non-numeric rendered param
    (HLD S8.4 M1 edge cases: "non-numeric rendered param (JSON parse error -> CFG-0)") --
    both mean "this config cannot be trusted at all", so they share one rule id.
    """
    value = data.get(field_name)
    if isinstance(value, bool) or value is None:
        raise Violation("CFG-0", detail=f"{field_name}={value!r} is not numeric")
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError as exc:
            raise Violation("CFG-0", detail=f"{field_name}={value!r} is not numeric") from exc
    raise Violation("CFG-0", detail=f"{field_name}={value!r} is not numeric")


def load_config(inst: Path) -> Config:
    """HLD S8.4 M1 `load_config`. Validates CFG-1 (percent ordering), CFG-2 (positive
    sizes/budget), CFG-3 (fan-out headroom); a malformed file or a non-numeric field is CFG-0.
    """
    path = inst / "overseer-config.json"
    try:
        data = read_json_bounded(path, JSON_MAX_BYTES)
    except FileNotFoundError as exc:
        raise Violation("CFG-0", detail=f"{path} not found") from exc
    except OversizeInputError as exc:
        raise Violation("CFG-0", detail=str(exc)) from exc
    except json.JSONDecodeError as exc:
        raise Violation("CFG-0", detail=f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise Violation("CFG-0", detail=f"{path} root must be a JSON object")

    converge_pct = _require_number(data, "converge_pct")
    stabilize_pct = _require_number(data, "stabilize_pct")
    closeout_pct = _require_number(data, "closeout_pct")
    wave_size = int(_require_number(data, "wave_size"))
    max_waves = int(_require_number(data, "max_waves"))
    run_budget_usd = _require_number(data, "run_budget_usd")
    max_expanders_per_wave = int(_require_number(data, "max_expanders_per_wave"))
    sub_wave_size = int(_require_number(data, "sub_wave_size"))
    max_injected_tasks = int(_require_number(data, "max_injected_tasks"))

    if not (0 < converge_pct < stabilize_pct < closeout_pct < 100):
        raise Violation(
            "CFG-1",
            detail=(
                f"require 0 < converge_pct < stabilize_pct < closeout_pct < 100; got "
                f"converge_pct={converge_pct} stabilize_pct={stabilize_pct} "
                f"closeout_pct={closeout_pct}"
            ),
        )
    if not (wave_size >= 1 and max_waves >= 1 and run_budget_usd > 0):
        raise Violation(
            "CFG-2",
            detail=f"wave_size={wave_size} max_waves={max_waves} run_budget_usd={run_budget_usd}",
        )
    need = max_waves * (wave_size + 1 + max_expanders_per_wave * (sub_wave_size + 1)) + 3
    if max_injected_tasks < need:
        raise Violation("CFG-3", detail=f"max_injected_tasks={max_injected_tasks} < need={need}")

    return Config(
        converge_pct=converge_pct,
        stabilize_pct=stabilize_pct,
        closeout_pct=closeout_pct,
        wave_size=wave_size,
        max_waves=max_waves,
        wave_max_minutes=_require_number(data, "wave_max_minutes"),
        max_attempts_per_item=int(_require_number(data, "max_attempts_per_item")),
        max_expanders_per_wave=max_expanders_per_wave,
        max_injected_tasks=max_injected_tasks,
        final_push=bool(data.get("final_push", True)),
        overseer_effort=str(data.get("overseer_effort", "high")),
        overseer_model=str(data.get("overseer_model", "")),
        python_bin=str(data.get("python_bin", "python3")),
        run_budget_usd=run_budget_usd,
        task_budget_usd=_require_number(data, "task_budget_usd"),
        stall_waves=int(_require_number(data, "stall_waves")),
        stabilize_wave_size=int(_require_number(data, "stabilize_wave_size")),
        max_stabilize_passes=int(_require_number(data, "max_stabilize_passes")),
        sub_wave_size=sub_wave_size,
        default_unit_cost_usd=_require_number(data, "default_unit_cost_usd"),
        default_ckpt_cost_usd=_require_number(data, "default_ckpt_cost_usd"),
        runs_root=str(data.get("runs_root", DEFAULT_RUNS_ROOT)),
        contract_version=int(_require_number(data, "contract_version")),
        kind_map=dict(data.get("kind_map", {})),
    )


# =============================================================================================
# ===== State (state.json, A-2 -- the full RunState dump, NOT status.json) ===================
# =============================================================================================


@dataclass(frozen=True)
class TaskInfo:
    status: str
    attempts: int
    started_at: str | None
    ended_at: str | None
    cumulative_cost_usd: float


@dataclass(frozen=True)
class State:
    tasks: dict[str, TaskInfo]
    injected_ids: list[str]
    breaker_overrides: dict[str, float]
    spent: float
    status: str


def state_path(workspace_root: Path, cfg: Config, run_id: str) -> Path:
    return workspace_root / cfg.runs_root / run_id / "state.json"


def load_state(path: Path) -> State:
    """HLD S8.4 M1 `load_state` -- a tolerant reader over `RunState`'s JSON dump.

    Missing file is ST-1; a missing/wrong-shaped REQUIRED field is ST-2 (fail-closed, field
    named in the message). `spent` sums `cumulative_cost_usd` exactly like
    `models.compute_run_usage_totals` (AC8's contract test pins this).
    """
    try:
        data = read_json_bounded(path, STATE_MAX_BYTES)
    except FileNotFoundError as exc:
        raise Violation("ST-1", detail=str(path)) from exc
    except OversizeInputError as exc:
        raise Violation("ST-2", detail=str(exc)) from exc
    except json.JSONDecodeError as exc:
        raise Violation("ST-2", detail=f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise Violation("ST-2", detail="state.json root must be a JSON object")

    raw_tasks = data.get("tasks")
    if not isinstance(raw_tasks, dict):
        raise Violation("ST-2", detail="field=tasks")

    tasks: dict[str, TaskInfo] = {}
    spent = 0.0
    for task_id, raw in raw_tasks.items():
        if not isinstance(raw, dict) or "status" not in raw:
            raise Violation("ST-2", detail=f"field=tasks.{task_id}.status")
        cost = raw.get("cumulative_cost_usd", 0.0)
        if cost is None:
            cost = 0.0
        if isinstance(cost, bool) or not isinstance(cost, int | float):
            raise Violation("ST-2", detail=f"field=tasks.{task_id}.cumulative_cost_usd")
        cost = float(cost)
        tasks[task_id] = TaskInfo(
            status=str(raw["status"]),
            attempts=int(raw.get("attempts", 0) or 0),
            started_at=raw.get("started_at"),
            ended_at=raw.get("ended_at"),
            cumulative_cost_usd=cost,
        )
        spent += cost

    raw_injected = data.get("injected_tasks", [])
    injected_ids: list[str] = []
    if isinstance(raw_injected, list):
        for item in raw_injected:
            if isinstance(item, dict) and "id" in item:
                injected_ids.append(str(item["id"]))
            else:
                raise Violation("ST-2", detail="field=injected_tasks[].id")
    else:
        raise Violation("ST-2", detail="field=injected_tasks")

    raw_overrides = data.get("breaker_overrides") or {}
    if not isinstance(raw_overrides, dict):
        raise Violation("ST-2", detail="field=breaker_overrides")
    breaker_overrides = {str(k): float(v) for k, v in raw_overrides.items()}

    status = str(data.get("status", "running"))
    return State(
        tasks=tasks,
        injected_ids=injected_ids,
        breaker_overrides=breaker_overrides,
        spent=spent,
        status=status,
    )


def wave_units(state: State, k: int) -> list[str]:
    # TODO(T-zLHc7Q): also match "<unit-id>--NN-<slug>" expander leaves and the fixed
    # "<unit-id>--done" sub-aggregator id (FR-15 depth-2 sub-DAGs). Below the MVP cut line.
    # Built via concatenation, not an f-string with doubled braces, so this source file's
    # text never contains a literal "{{"/"}}" pair (NFR-5: it is rendered through `_render`).
    pattern = re.compile("^w" + f"{k:02d}" + r"-\d{2}-")
    return sorted(tid for tid in state.tasks if pattern.match(tid))


# =============================================================================================
# ===== Ledger (outputs/ledger.jsonl, ao.overseer.ledger/v1) -- hash-chained, append-only ====
# =============================================================================================


def ledger_path(inst: Path) -> Path:
    return inst / "outputs" / "ledger.jsonl"


def read_ledger_lines(inst: Path) -> list[dict[str, Any]]:
    path = ledger_path(inst)
    if not path.is_file():
        return []
    lines: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line_no, raw in enumerate(fh, start=1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise Violation("INT-3", detail=f"line_no={line_no} unreadable: {exc}") from exc
            if not isinstance(parsed, dict):
                raise Violation("INT-3", detail=f"line_no={line_no} is not a JSON object")
            lines.append(parsed)
    return lines


def append_chained(
    inst: Path, line: dict[str, Any], now: datetime, *, dry_run: bool = False
) -> dict[str, Any]:
    """HLD S8.4 M1 `append_chained`: sets `prev_sha256` to the canonical-JSON sha256 of the
    previous line ("GENESIS" for the first), then appends. Under `--dry-run`, computes and
    returns the would-be line without writing it (so `unit-gate`/preview callers never touch
    disk).
    """
    existing = read_ledger_lines(inst)
    prev_sha256 = "GENESIS" if not existing else sha256_hex(canonical_json(existing[-1]))
    finalized = dict(line)
    finalized.setdefault("schema", LEDGER_SCHEMA)
    finalized["seq"] = len(existing) + 1
    finalized["prev_sha256"] = prev_sha256
    finalized.setdefault("recorded_at", now_iso(now))
    if not dry_run:
        path = ledger_path(inst)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(canonical_json(finalized))
            fh.write("\n")
    return finalized


def verify_ledger_chain(inst: Path) -> None:
    """HLD S8.4 M1 `verify_ledger_chain`: walk every line, recompute, fail closed (INT-3) on
    any break (a byte edited, a line deleted/reordered, or unparseable JSON).
    """
    lines = read_ledger_lines(inst)
    prev_line: dict[str, Any] | None = None
    for line_no, line in enumerate(lines, start=1):
        expected = "GENESIS" if prev_line is None else sha256_hex(canonical_json(prev_line))
        if line.get("prev_sha256") != expected:
            raise Violation("INT-3", detail=f"line_no={line_no} chain hash mismatch")
        prev_line = line


def brief_path(inst: Path, k: int, unit_id: str) -> Path:
    return inst / "outputs" / "waves" / f"w{k:02d}" / "briefs" / f"{unit_id}.json"


def breadcrumb_path(inst: Path, unit_id: str) -> Path:
    return inst / "outputs" / "progress" / f"{unit_id}.json"


def read_brief(inst: Path, k: int, unit_id: str) -> dict[str, Any]:
    path = brief_path(inst, k, unit_id)
    try:
        data = read_json_bounded(path, JSON_MAX_BYTES)
    except FileNotFoundError as exc:
        raise Violation("BR-1", detail=f"unit_id={unit_id} brief missing: {path}") from exc
    except (OversizeInputError, json.JSONDecodeError) as exc:
        raise Violation("BR-1", detail=f"unit_id={unit_id} brief unreadable: {exc}") from exc
    if not isinstance(data, dict):
        raise Violation("BR-1", detail=f"unit_id={unit_id} brief root must be an object")
    return data


def read_breadcrumb(inst: Path, unit_id: str) -> dict[str, Any] | None:
    """Returns `None` when the breadcrumb file is simply absent (the caller decides whether
    that's tolerable); raises BC-1 only for a present-but-malformed file.
    """
    path = breadcrumb_path(inst, unit_id)
    if not path.is_file():
        return None
    try:
        data = read_json_bounded(path, JSON_MAX_BYTES)
    except (OversizeInputError, json.JSONDecodeError) as exc:
        raise Violation("BC-1", detail=f"unit_id={unit_id} breadcrumb unreadable: {exc}") from exc
    if not isinstance(data, dict):
        raise Violation("BC-1", detail=f"unit_id={unit_id} breadcrumb root must be an object")
    return data


def _duration_seconds(started_at: str | None, ended_at: str | None) -> float | None:
    if not started_at or not ended_at:
        return None
    try:
        start = datetime.fromisoformat(started_at)
        end = datetime.fromisoformat(ended_at)
    except ValueError:
        return None
    return (end - start).total_seconds()


def ingest_ledger(
    inst: Path, units: list[str], state: State, k: int, now: datetime, *, dry_run: bool = False
) -> int:
    """HLD S8.4 M1 `ingest_ledger`: idempotent (keyed by `unit_id`), folds each newly-settled
    wave unit's brief + breadcrumb into one `type:"unit"` ledger line.

    A missing breadcrumb is synthesized ONLY for a `failed` unit (the engine halts the run on
    failure, so a failed unit genuinely never got to write one, per this task's own build
    instructions -- narrower than the M1 pseudocode's unconditional synthesis, since the
    engine's own missing-output check already guarantees a succeeded/skipped unit's
    breadcrumb exists). A missing breadcrumb for any OTHER settled status is a genuine
    contract break (`BC-2`, this task's own extension of the `BC-*` bucket -- not in the HLD's
    normative rule-id list, but needed to fail closed per NFR-4 rather than silently
    fabricating data for a unit that should have written its own breadcrumb).
    """
    existing = read_ledger_lines(inst)
    have = {line["unit_id"] for line in existing if line.get("type") == "unit"}
    appended = 0
    for unit_id in sorted(units):
        if unit_id in have:
            continue
        task_info = state.tasks.get(unit_id)
        if task_info is None or task_info.status not in SETTLED_TASK_STATUSES:
            continue

        brief = read_brief(inst, k, unit_id)
        crumb = read_breadcrumb(inst, unit_id)
        if crumb is None:
            if task_info.status != "failed":
                raise Violation(
                    "BC-2",
                    detail=f"unit_id={unit_id} missing breadcrumb for status={task_info.status}",
                )
            crumb = {
                "unit_id": unit_id,
                "outcome": "failed",
                "verdict": "fail",
                "changed_paths": [],
                "needs_input": False,
            }
        if crumb.get("unit_id") != unit_id:
            raise Violation(
                "BC-1", detail=f"unit_id={unit_id} breadcrumb.unit_id={crumb.get('unit_id')!r}"
            )

        line = {
            "type": "unit",
            "wave": k,
            "unit_id": unit_id,
            "ask_ids": brief.get("ask_ids", []),
            "work_item": brief.get("work_item"),
            "kind": brief.get("kind"),
            "attempt_no": task_info.attempts,
            "outcome": crumb.get("outcome"),
            "verdict": crumb.get("verdict"),
            "engine_status": task_info.status,
            "cost_usd": task_info.cumulative_cost_usd,
            "duration_s": _duration_seconds(task_info.started_at, task_info.ended_at),
            "changed_paths": list(crumb.get("changed_paths", []))[:MAX_CHANGED_PATHS_PER_UNIT],
            "needs_input": bool(crumb.get("needs_input", False)),
        }
        append_chained(inst, line, now, dry_run=dry_run)
        have.add(unit_id)
        appended += 1
    return appended


# =============================================================================================
# ===== Path history (outputs/overseer/path-history.json) -- MINIMAL M1 scope ===============
# =============================================================================================


def _hash_path(path: Path) -> str:
    """Content hash of a tracked path, or a sentinel for "absent"/"too large" (NFR-6, HLD S8.3
    M2 edge cases: a deleted path is a valid, hashable state, `"<absent>"`).
    """
    if not path.is_file():
        return "<absent>"
    if path.stat().st_size > HASH_SKIP_BYTES:
        return "<skipped:too-large>"
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def path_history_path(inst: Path) -> Path:
    return inst / "outputs" / "overseer" / "path-history.json"


def update_path_history(
    inst: Path,
    k: int,
    repo_paths: dict[str, str],
    units: list[str],
    now: datetime,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """HLD S8.3/S13.4 path history: hashes the UNION of breadcrumb-reported `changed_paths`
    (classified via `classify_path_entry`, AC6) and git-derived tracked paths (T-C6uQJW --
    `git diff --name-only <head_at_ck(K-1)>` plus `git status --porcelain` per repo, both
    bounded), so a unit that omits a reverted path from its own `changed_paths` can no longer
    hide it (HLD S8.3 Rev 2 reviewer #6).

    Rejected breadcrumb entries are recorded (not hashed) for the `breadcrumb_integrity`
    signal. `repo_heads` (this checkpoint's `git rev-parse HEAD` per repo) is stored so the
    NEXT checkpoint's git-diff base is known.
    """
    accepted: list[str] = []
    rejected: list[dict[str, Any]] = []
    for unit_id in sorted(units):
        crumb = read_breadcrumb(inst, unit_id)
        if crumb is None:
            continue
        for raw_entry in list(crumb.get("changed_paths", []))[:MAX_CHANGED_PATHS_PER_UNIT]:
            classification = classify_path_entry(str(raw_entry), repo_paths)
            if classification.ok and classification.abs_path is not None:
                accepted.append(classification.abs_path)
            else:
                rejected.append(
                    {"unit_id": unit_id, "entry": raw_entry, "reason": classification.reason}
                )

    path = path_history_path(inst)
    history: dict[str, Any] = {}
    if path.is_file():
        loaded = read_json_bounded(path, JSON_MAX_BYTES)
        if isinstance(loaded, dict):
            history = loaded

    # The PREVIOUS checkpoint's recorded repo_heads is the git-diff base (skip the diff for a
    # repo with no prior head -- e.g. the first checkpoint).
    prev_entry = history.get(f"{k - 1:02d}") if k >= 2 else None
    prev_repo_heads = (prev_entry.get("repo_heads") if isinstance(prev_entry, dict) else None) or {}

    repo_heads: dict[str, str] = {}
    git_derived: list[str] = []
    for repo_id, repo_path_str in sorted(repo_paths.items()):
        repo_root = Path(repo_path_str).resolve()
        head = _git_rev_parse_head(str(repo_root))
        if head is not None:
            repo_heads[repo_id] = head
        prev_head = prev_repo_heads.get(repo_id) if isinstance(prev_repo_heads, dict) else None
        for rel_path in _git_changed_paths(str(repo_root), prev_head):
            confined = _confine_repo_relative(repo_root, rel_path)
            if confined is not None:
                git_derived.append(confined)

    # Union, de-duplicated, GIT-DERIVED paths first (reviewer finding, T-C6uQJW review): if a
    # busy wave's breadcrumb-declared paths alone reach MAX_TRACKED_PATHS (plausible --
    # wave_size * MAX_CHANGED_PATHS_PER_UNIT can exceed it), a declared-first order would let
    # the trim below silently drop exactly the git-derived, UNDECLARED paths this whole
    # feature exists to catch (HLD S8.3 Rev 2 reviewer #6: "a unit that omits a reverted path
    # can't hide it"). Prioritizing git-derived paths means the trim sacrifices paths a unit
    # already truthfully declared before it ever sacrifices ones it didn't.
    seen: set[str] = set()
    all_current: list[str] = []
    for abs_path in git_derived + accepted:
        if abs_path not in seen:
            seen.add(abs_path)
            all_current.append(abs_path)

    trimmed, dropped_count = _trim_tracked_paths(history, k, all_current, seen)
    entries = {abs_path: _hash_path(Path(abs_path)) for abs_path in trimmed}

    entry_key = f"{k:02d}"
    new_entry: dict[str, Any] = {
        "generated_at": now_iso(now),
        "paths": entries,
        "rejected": rejected,
        "repo_heads": repo_heads,
    }
    if dropped_count:
        # Judgment call (T-C6uQJW, see task report): no existing "info" channel elsewhere in
        # this file, so a simple list field on the checkpoint's own path-history entry is the
        # most visible, lowest-risk place for this note (digest assembly can surface it later).
        new_entry["info"] = [
            f"path-history trim dropped {dropped_count} path(s) beyond "
            f"MAX_TRACKED_PATHS={MAX_TRACKED_PATHS}"
        ]
    history[entry_key] = new_entry
    if not dry_run:
        write_json_atomic(path, history)
    result: dict[str, Any] = history[entry_key]
    return result


# =============================================================================================
# ===== Budget orchestration (I/O: ledger + previous digests + control/budget-override.json) =
# =============================================================================================


def checkpoint_dir(inst: Path, k: int) -> Path:
    return inst / "outputs" / "checkpoints" / f"ck-{k:02d}"


def digest_path(inst: Path, k: int) -> Path:
    return checkpoint_dir(inst, k) / "digest.json"


def _read_prev_digest(inst: Path, k: int) -> dict[str, Any] | None:
    if k <= 1:
        return None
    path = digest_path(inst, k - 1)
    if not path.is_file():
        return None
    data = read_json_bounded(path, JSON_MAX_BYTES)
    return data if isinstance(data, dict) else None


def read_prev_stage(inst: Path, k: int) -> str:
    prev = _read_prev_digest(inst, k)
    if prev is None:
        return "explore"
    budget = prev.get("budget")
    if isinstance(budget, dict) and isinstance(budget.get("stage"), str):
        return str(budget["stage"])
    return "explore"


def count_prior_stabilize_passes(inst: Path, k: int) -> int:
    count = 0
    for j in range(1, k):
        path = digest_path(inst, j)
        if not path.is_file():
            continue
        data = read_json_bounded(path, JSON_MAX_BYTES)
        if isinstance(data, dict):
            budget = data.get("budget")
            if isinstance(budget, dict) and budget.get("stage") == "stabilize":
                count += 1
    return count


def effective_budget(
    cfg: Config,
    state: State,
    inst: Path,
    now: datetime,
    *,
    record: bool = True,
    dry_run: bool = False,
) -> tuple[float, dict[str, Any]]:
    """HLD S8.4 M1 `effective_budget` (FR-16).

    `record=False` is used by `unit-gate` (FR-18): that hook must be fast/$0 AND write zero
    files even when an override happens to be freshly honorable -- recording the
    `budget_override` ledger event is `ckpt-prep`'s job exclusively (`compute_budget` always
    calls this with `record=True`). `dry_run=True` (threaded from `ckpt-prep --dry-run`)
    computes the same `newly_honored` verdict without persisting it, mirroring
    `append_chained`'s own dry-run contract.
    """
    override_path = inst / "control" / "budget-override.json"
    if not override_path.is_file():
        return cfg.run_budget_usd, {
            "honored": False,
            "run_budget_usd": None,
            "reason": None,
            "newly_honored": False,
        }

    try:
        override = read_json_bounded(override_path, JSON_MAX_BYTES)
    except (OversizeInputError, json.JSONDecodeError):
        return cfg.run_budget_usd, {
            "honored": False,
            "run_budget_usd": None,
            "reason": "control/budget-override.json is malformed",
            "newly_honored": False,
        }
    if not isinstance(override, dict):
        return cfg.run_budget_usd, {
            "honored": False,
            "run_budget_usd": None,
            "reason": "control/budget-override.json root must be a JSON object",
            "newly_honored": False,
        }

    requested = override.get("run_budget_usd")
    reason = override.get("reason")
    if isinstance(requested, bool) or not isinstance(requested, int | float):
        return cfg.run_budget_usd, {
            "honored": False,
            "run_budget_usd": requested,
            "reason": "control/budget-override.json.run_budget_usd is not numeric",
            "newly_honored": False,
        }

    extension = state.breaker_overrides.get("run-budget-backstop")
    if extension is None or extension < requested:
        return cfg.run_budget_usd, {
            "honored": False,
            "run_budget_usd": requested,
            "reason": "no matching --extend-breaker run-budget-backstop",
            "newly_honored": False,
        }

    already_recorded = any(
        line.get("type") == "event"
        and line.get("event") == "budget_override"
        and line.get("run_budget_usd") == requested
        and line.get("reason") == reason
        for line in read_ledger_lines(inst)
    )
    newly_honored = not already_recorded
    if newly_honored and record:
        append_chained(
            inst,
            {
                "type": "event",
                "event": "budget_override",
                "run_budget_usd": requested,
                "reason": reason,
            },
            now,
            dry_run=dry_run,
        )
    return float(requested), {
        "honored": True,
        "run_budget_usd": requested,
        "reason": reason,
        "newly_honored": newly_honored,
    }


def compute_budget(
    cfg: Config, state: State, inst: Path, k: int, now: datetime, *, dry_run: bool = False
) -> dict[str, Any]:
    """I/O orchestrator: gathers scalar inputs from the ledger/config/prior digests and hands
    them to the pure `derive_budget`, then assembles the digest's budget/cadence pieces
    (HLD S13.4). `dry_run` threads through to `effective_budget` so a `ckpt-prep --dry-run`
    never persists a `budget_override` ledger event even when one would newly apply.
    """
    run_budget_usd, override_info = effective_budget(
        cfg, state, inst, now, record=True, dry_run=dry_run
    )

    ledger_lines = read_ledger_lines(inst)
    unit_lines = [line for line in ledger_lines if line.get("type") == "unit"]
    waves_present = sorted(
        {
            line["wave"]
            for line in unit_lines
            if isinstance(line.get("wave"), int) and line["wave"] <= k
        },
        reverse=True,
    )[:2]
    recent = [line for line in unit_lines if line.get("wave") in waves_present]
    costs = [float(line["cost_usd"]) for line in recent if line.get("cost_usd") is not None]
    durations = [float(line["duration_s"]) for line in recent if line.get("duration_s") is not None]

    est_unit_cost_usd = median_or_default(costs, cfg.default_unit_cost_usd)
    median_duration_s = statistics.median(durations) if durations else None
    time_cap = time_cap_from_median_duration(median_duration_s, cfg.wave_max_minutes)

    ckpt_costs = [
        info.cumulative_cost_usd
        for tid, info in state.tasks.items()
        if _CK_TASK_ID_RE.match(tid)
        and info.status in SETTLED_TASK_STATUSES
        and tid != f"ck-{k:02d}"
    ]
    est_ckpt_cost_usd = median_or_default(ckpt_costs, cfg.default_ckpt_cost_usd)

    prev_stage = "explore" if override_info["newly_honored"] else read_prev_stage(inst, k)
    stabilize_passes_before = count_prior_stabilize_passes(inst, k)
    forced_closeout = any(
        line.get("type") == "event" and line.get("event") == "forced_closeout"
        for line in ledger_lines
    )

    result = derive_budget(
        spent=state.spent,
        run_budget_usd=run_budget_usd,
        wave_size=cfg.wave_size,
        time_cap=time_cap,
        est_unit_cost_usd=est_unit_cost_usd,
        est_ckpt_cost_usd=est_ckpt_cost_usd,
        final_push=cfg.final_push,
        prev_stage=prev_stage,
        converge_pct=cfg.converge_pct,
        stabilize_pct=cfg.stabilize_pct,
        closeout_pct=cfg.closeout_pct,
        stabilize_wave_size=cfg.stabilize_wave_size,
        k=k,
        max_waves=cfg.max_waves,
        stabilize_passes_before=stabilize_passes_before,
        max_stabilize_passes=cfg.max_stabilize_passes,
        forced_closeout=forced_closeout,
    )

    budget = {
        "run_budget_usd": run_budget_usd,
        "override_applied": {
            "honored": override_info["honored"],
            "run_budget_usd": override_info["run_budget_usd"],
            "reason": override_info["reason"],
        },
        "spent_usd": state.spent,
        "pct_used": result.pct_used,
        "est_unit_cost_usd": est_unit_cost_usd,
        "est_ckpt_cost_usd": est_ckpt_cost_usd,
        "reserve_tail_usd": result.reserve_tail_usd,
        "stage_raw": result.stage_raw,
        "stage_projected": result.stage_projected,
        "stage": result.stage,
        "thresholds": {
            "converge": cfg.converge_pct,
            "stabilize": cfg.stabilize_pct,
            "closeout": cfg.closeout_pct,
        },
    }
    cadence = {
        "wave_size": cfg.wave_size,
        "time_cap": time_cap,
        "budget_cap": result.budget_cap_to_100pct
        if result.stage == "stabilize"
        else result.budget_cap,
        "allowed_wave_size": result.allowed_wave_size,
        "median_unit_duration_s": median_duration_s,
    }
    stabilize_passes = stabilize_passes_before + (1 if result.stage == "stabilize" else 0)
    return {
        "budget": budget,
        "cadence": cadence,
        "must_close": result.must_close,
        "must_close_reasons": result.must_close_reasons,
        "allowed_decisions": result.allowed_decisions,
        "stabilize_passes": stabilize_passes,
    }


# =============================================================================================
# ===== Hold gate (D6) + charter-lock verify (INT-1) =========================================
# =============================================================================================


def hold_gate(inst: Path, k: int, now: datetime, *, dry_run: bool = False) -> None:
    """HLD S8.4 M1 `hold_gate`.

    - No hold decided (or already answered): a stray `control/hold-request.json` is INT-4
      (prevents a unit from forging a hold to stall the run).
    - Hold decided, request file missing: INT-2 (fail closed -- deletion can't bypass it).
    - Hold decided, request present, no newer answer: HOLD (exit 2, $0).
    - Hold decided, answered: archive both files under this checkpoint's own `hold/` dir,
      remove them from `control/`, and record `hold_answered` in the ledger.
    """
    lines = read_ledger_lines(inst)
    last_checkpoint_idx: int | None = None
    for idx, line in enumerate(lines):
        if line.get("type") == "event" and line.get("event") == "checkpoint":
            last_checkpoint_idx = idx
    last_checkpoint = lines[last_checkpoint_idx] if last_checkpoint_idx is not None else None

    answered_after = False
    if last_checkpoint_idx is not None:
        for line in lines[last_checkpoint_idx + 1 :]:
            if line.get("type") == "event" and line.get("event") == "hold_answered":
                answered_after = True
                break

    req_path = inst / "control" / "hold-request.json"
    ans_path = inst / "control" / "hold-answer.md"

    if last_checkpoint is None or last_checkpoint.get("decision") != "hold" or answered_after:
        if req_path.is_file():
            raise Violation("INT-4", detail="stray hold-request.json not backed by a hold decision")
        return

    if not req_path.is_file():
        raise Violation(
            "INT-2",
            detail=f"hold decided at {last_checkpoint.get('checkpoint')} but request file missing",
        )

    try:
        request_data = read_json_bounded(req_path, JSON_MAX_BYTES)
    except (OversizeInputError, json.JSONDecodeError) as exc:
        # Reviewer finding (T-ABDjSj review): a malformed hold-request.json must fail
        # closed through the rule-id path, not bubble up as an uncaught exception that
        # `main()`'s generic except turns into a bare exit-1 internal error with no rule
        # id and no prep-result.json -- exactly on the tamper/corruption path where a
        # diagnosable failure matters most (NFR-9).
        raise Violation("INT-2", detail=f"{req_path} is not valid JSON: {exc}") from exc
    if not ans_path.is_file() or ans_path.stat().st_mtime < req_path.stat().st_mtime:
        needs_input_path = (
            request_data.get("needs_input_path") if isinstance(request_data, dict) else None
        ) or str(req_path)
        raise Violation(
            "HOLD",
            message=(
                f"HOLD: overseer requested human input: see {needs_input_path}; "
                "write your answer to control/hold-answer.md then `ao resume`"
            ),
        )

    if dry_run:
        return

    hold_dir = checkpoint_dir(inst, k) / "hold"
    hold_dir.mkdir(parents=True, exist_ok=True)
    answer_bytes = ans_path.read_bytes()
    answer_sha256 = hashlib.sha256(answer_bytes).hexdigest()
    write_json_atomic(hold_dir / "request.json", request_data)
    (hold_dir / "answer.md").write_bytes(answer_bytes)
    req_path.unlink()
    ans_path.unlink()
    append_chained(
        inst,
        {
            "type": "event",
            "event": "hold_answered",
            "checkpoint": f"ck-{k:02d}",
            "answer_sha256": answer_sha256,
        },
        now,
    )


def verify_charter_lock(inst: Path) -> None:
    """HLD S8.4 M1 `verify_charter_lock` (INT-1). If `charter.lock.json` (written by the
    LATER `intake-check` task) doesn't exist yet, that's fine at this stage -- only a real
    mismatch fails closed.
    """
    lock_path = inst / "outputs" / "overseer" / "charter.lock.json"
    if not lock_path.is_file():
        return
    try:
        lock = read_json_bounded(lock_path, JSON_MAX_BYTES)
    except (OversizeInputError, json.JSONDecodeError) as exc:
        # Reviewer finding (T-ABDjSj review): same rationale as hold_gate above -- a
        # corrupted charter.lock.json is exactly the tamper scenario INT-1 exists to
        # catch, so it must fail through the rule-id path, not an uncaught exception.
        raise Violation("INT-1", detail=f"{lock_path} is not valid JSON: {exc}") from exc
    if not isinstance(lock, dict):
        raise Violation("INT-1", detail="charter.lock.json root must be a JSON object")

    charter_path = inst / "outputs" / "charter.json"
    if not charter_path.is_file():
        raise Violation(
            "INT-1", detail="charter.lock.json exists but outputs/charter.json is missing"
        )
    charter_sha256 = hashlib.sha256(charter_path.read_bytes()).hexdigest()
    if lock.get("sha256") != charter_sha256:
        raise Violation("INT-1", detail="charter.json sha256 does not match charter.lock.json")

    prompt_path = inst / "prompt.md"
    if prompt_path.is_file():
        prompt_sha256 = hashlib.sha256(prompt_path.read_bytes()).hexdigest()
        if lock.get("prompt_sha256") != prompt_sha256:
            raise Violation("INT-1", detail="prompt.md sha256 does not match charter.lock.json")


# =============================================================================================
# ===== M2: signal detectors (T-C6uQJW, HLD S8.3 rule table / S8.4 M2 pseudocode) =============
# =============================================================================================

# Named thresholds (HLD S8.3) -- never inline literals (reviewer #7).
MAX_PERIOD: Final[int] = 4
MIRROR_HALF_LENGTHS: Final[tuple[int, ...]] = (2, 3)
MIN_REPS_PERIODIC: Final[int] = 2
MIN_REPS_SINGLE: Final[int] = 3

_DONE_VERDICTS: Final[frozenset[str]] = frozenset({"pass", "na"})
_MET_OR_DEFERRED: Final[frozenset[str]] = frozenset({"met", "deferred"})


# =============================================================================================
# ===== PURE functions -- no I/O (ADR-0016 D4): period/mirror detection, ledger grouping =====
# =============================================================================================


def detect_period(seq: list[str], max_p: int = MAX_PERIOD) -> tuple[int, int] | None:
    """HLD S8.4 M2 `detect_period`, exactly: for period `p` in 1..max_p, the largest `r`
    such that the trailing `p*r` tokens of *seq* are `r` copies of one `p`-token block.
    Flags `(p >= 2 and r >= MIN_REPS_PERIODIC) or (p == 1 and r >= MIN_REPS_SINGLE)`; the
    smallest qualifying period wins (loop order is ascending `p`, first hit kept). Table
    tests are the spec (ticket AC1).
    """
    best: tuple[int, int] | None = None
    n = len(seq)
    for p in range(1, max_p + 1):
        r = 0
        while n >= p * (r + 1) and seq[n - p * (r + 1) : n - p * r] == seq[n - p :]:
            r += 1
        if (p >= 2 and r >= MIN_REPS_PERIODIC) or (p == 1 and r >= MIN_REPS_SINGLE):
            best = best or (p, r)
    return best


def detect_mirror(seq: list[str]) -> int | None:
    """HLD S8.4 M2 `detect_mirror`: for `m` in `MIRROR_HALF_LENGTHS`, the trailing `2*m`
    tokens form a palindrome whose first half has >= 2 distinct tokens; smallest qualifying
    `m` wins (ticket AC2).
    """
    for m in MIRROR_HALF_LENGTHS:
        tail = seq[-2 * m :]
        if len(tail) == 2 * m and tail == list(reversed(tail)) and len(set(tail[:m])) >= 2:
            return m
    return None


def returns_to_earlier(hist: list[str]) -> list[int]:
    """Ticket pseudocode `returns_to_earlier`: every index `k` (0-based) in *hist* where
    `hist[k] == hist[j]` for some `j <= k-2`, and `hist[k-1] != hist[k]` -- i.e. the tracked
    value changed away and then returned to a state seen at least 2 steps earlier. Returns
    ALL qualifying indices (not just whether any exist), so `content_oscillation`'s severity
    rule ("high if ... >= 2 returns total") can count them across paths.
    """
    hits: list[int] = []
    for idx in range(2, len(hist)):
        if hist[idx - 1] == hist[idx]:
            continue
        if any(hist[j] == hist[idx] for j in range(idx - 1)):
            hits.append(idx)
    return hits


def _work_item_sequences(unit_lines: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Group ledger `unit` lines by `work_item`, each group ordered by `(wave, unit_id)`
    (HLD S8.3's per-work-item sequence `S[w]`). Lines with a missing/blank `work_item` are
    excluded -- a malformed brief is BR-1's problem (M1), not a detector's.
    """
    groups: dict[str, list[dict[str, Any]]] = {}
    for line in unit_lines:
        work_item = line.get("work_item")
        if isinstance(work_item, str) and work_item:
            groups.setdefault(work_item, []).append(line)
    for lines in groups.values():
        lines.sort(key=lambda ln: (ln.get("wave") or 0, str(ln.get("unit_id") or "")))
    return groups


def _done_work_items(unit_lines: list[dict[str, Any]]) -> set[str]:
    """Distinct `work_item`s with at least one unit reporting `outcome: done` and
    `verdict` in `_DONE_VERDICTS` (HLD S8.3 `stall` / `progress.work_items_done`).
    """
    return {
        str(line["work_item"])
        for line in unit_lines
        if line.get("outcome") == "done"
        and line.get("verdict") in _DONE_VERDICTS
        and isinstance(line.get("work_item"), str)
        and line.get("work_item")
    }


def _signal_sort_key(sig: dict[str, Any]) -> tuple[str, str, str, str, str, str]:
    """Ticket AC7's ordering key `(type, work_item, path)`, widened with two more fully
    deterministic tiebreakers (`ask_id`, `evidence.unit_id`) so two candidates that tie on
    the primary 3-tuple (e.g. two `ask_starvation` signals, both with no `work_item`/`path`)
    still sort identically regardless of input ledger line order (AC7's own shuffle test).
    """
    evidence = sig.get("evidence") or {}
    return (
        str(sig.get("type") or ""),
        str(sig.get("work_item") or ""),
        str(sig.get("path") or ""),
        str(sig.get("ask_id") or ""),
        str(evidence.get("unit_id") or ""),
        str(sig.get("message") or ""),
    )


def _assign_signal_ids(candidates: list[dict[str, Any]], k: int) -> list[dict[str, Any]]:
    """Assigns stable `S-<KK>-<NN>` ids in the deterministic order of `_signal_sort_key`."""
    ordered = sorted(candidates, key=_signal_sort_key)
    signals: list[dict[str, Any]] = []
    for idx, sig in enumerate(ordered, start=1):
        finalized = dict(sig)
        finalized["id"] = f"S-{k:02d}-{idx:02d}"
        signals.append(finalized)
    return signals


def _period_mirror_signals(unit_lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    signals: list[dict[str, Any]] = []
    for work_item, lines in _work_item_sequences(unit_lines).items():
        seq = [f"{ln.get('kind')}:{ln.get('verdict')}" for ln in lines]
        period = detect_period(seq)
        if period is not None:
            p, r = period
            signals.append(
                {
                    "type": "period_repeat",
                    "severity": "high" if r >= 3 else "medium",
                    "work_item": work_item,
                    "ask_id": None,
                    "path": None,
                    "evidence": {"period": p, "repeats": r, "sequence": seq},
                    "message": f"work item {work_item} repeats a {p}-token pattern {r} times",
                }
            )
        mirror = detect_mirror(seq)
        if mirror is not None:
            signals.append(
                {
                    "type": "mirror_flipflop",
                    "severity": "medium",
                    "work_item": work_item,
                    "ask_id": None,
                    "path": None,
                    "evidence": {"half_length": mirror, "sequence": seq},
                    "message": (
                        f"work item {work_item} flip-flops in a mirrored "
                        f"pattern (half-length {mirror})"
                    ),
                }
            )
    return signals


def _repeated_failure_signals(unit_lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    signals: list[dict[str, Any]] = []
    for work_item, lines in _work_item_sequences(unit_lines).items():
        if len(lines) < 2:
            continue
        if lines[-1].get("verdict") == "fail" and lines[-2].get("verdict") == "fail":
            signals.append(
                {
                    "type": "repeated_failure",
                    "severity": "medium",
                    "work_item": work_item,
                    "ask_id": None,
                    "path": None,
                    "evidence": {
                        "last_two_unit_ids": [lines[-2].get("unit_id"), lines[-1].get("unit_id")]
                    },
                    "message": f"work item {work_item}'s last 2 units both have verdict=fail",
                }
            )
    return signals


def _attempt_cap_signals(cfg: Config, unit_lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    signals: list[dict[str, Any]] = []
    for work_item, lines in _work_item_sequences(unit_lines).items():
        if len(lines) >= cfg.max_attempts_per_item:
            signals.append(
                {
                    "type": "attempt_cap",
                    "severity": "high",
                    "work_item": work_item,
                    "ask_id": None,
                    "path": None,
                    "evidence": {
                        "attempts": len(lines),
                        "max_attempts_per_item": cfg.max_attempts_per_item,
                    },
                    "message": (
                        f"work item {work_item} has {len(lines)} units >= "
                        f"max_attempts_per_item={cfg.max_attempts_per_item}"
                    ),
                }
            )
    return signals


def _blocked_units_signals(unit_lines: list[dict[str, Any]], k: int) -> list[dict[str, Any]]:
    """HLD S8.3 `blocked_units`: any unit in the CURRENT wave K with `outcome: blocked` or
    `needs_input: true`.
    """
    signals: list[dict[str, Any]] = []
    for line in unit_lines:
        if line.get("wave") != k:
            continue
        if line.get("outcome") == "blocked" or bool(line.get("needs_input")):
            signals.append(
                {
                    "type": "blocked_units",
                    "severity": "medium",
                    "work_item": line.get("work_item"),
                    "ask_id": None,
                    "path": None,
                    "evidence": {
                        "unit_id": line.get("unit_id"),
                        "outcome": line.get("outcome"),
                        "needs_input": bool(line.get("needs_input")),
                    },
                    "message": f"unit {line.get('unit_id')} is blocked or needs input",
                }
            )
    return signals


# =============================================================================================
# ===== I/O helpers: bounded git subprocess calls, tolerant JSON reads =======================
# =============================================================================================


def _read_optional_json(path: Path) -> dict[str, Any] | None:
    """Tolerant JSON reader for M2: a MISSING or malformed file means "no data yet" (e.g. a
    verdict for a checkpoint that hasn't happened, or charter.json before intake ever ran),
    never a `Violation` -- M2 detectors are diagnostics over history, not integrity gates
    (that boundary is `verify_charter_lock`/`verify_ledger_chain`, M1).
    """
    try:
        data = read_json_bounded(path, JSON_MAX_BYTES)
    except (FileNotFoundError, OversizeInputError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _git_rev_parse_head(repo_abs_path: str) -> str | None:
    """Bounded `git -C <repo> rev-parse HEAD` (HLD S8.3 `repo_heads`). `None` for a
    directory that isn't a git work tree, or any subprocess error/timeout -- a repo this
    call can't read is simply invisible to `repo_heads`/git-derived tracked paths this
    checkpoint, never a fatal error (this is a diagnostic detector, not an integrity gate).
    """
    try:
        result = subprocess.run(
            ["git", "-C", repo_abs_path, "rev-parse", "HEAD"],
            timeout=GIT_TIMEOUT_S,
            capture_output=True,
            text=True,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    head = result.stdout.strip()
    return head or None


def _git_changed_paths(repo_abs_path: str, prev_head: str | None) -> list[str]:
    """Bounded `git diff --name-only <prev_head>` (skipped when *prev_head* is `None` -- no
    prior checkpoint recorded a head for this repo yet) plus `git status --porcelain`,
    repo-relative paths (HLD S8.3 Rev 2 reviewer #6: "tracked paths" also covers what a unit
    changed but omitted from its own breadcrumb). Any subprocess failure/timeout, or a
    directory that isn't a git work tree, yields an empty list -- never raises.
    """

    def _run(cmd: list[str]) -> list[str]:
        try:
            result = subprocess.run(
                cmd, timeout=GIT_TIMEOUT_S, capture_output=True, text=True, check=False
            )
        except (OSError, subprocess.TimeoutExpired):
            return []
        if result.returncode != 0:
            return []
        return [line for line in result.stdout.splitlines() if line.strip()]

    rel_paths: set[str] = set()
    if prev_head:
        for line in _run(["git", "-C", repo_abs_path, "diff", "--name-only", prev_head]):
            candidate = line.strip()
            if candidate:
                rel_paths.add(candidate)

    for line in _run(["git", "-C", repo_abs_path, "status", "--porcelain"]):
        if len(line) < 4:
            continue
        candidate = line[3:]  # "XY <path>" (porcelain v1); "XY <old> -> <new>" for renames
        if "->" in candidate:
            candidate = candidate.split("->", 1)[1]
        candidate = candidate.strip().strip('"')
        if candidate:
            rel_paths.add(candidate)

    return sorted(rel_paths)


def _confine_repo_relative(repo_root: Path, rel_path: str) -> str | None:
    """Confines a git-reported relative path to *repo_root* -- defense in depth mirroring
    `classify_path_entry`'s own confinement discipline (NFR-1). Git itself only ever reports
    paths inside the worktree it was invoked on, but this path didn't come through
    `classify_path_entry`'s own `repo_paths`-keyed validation, so it gets the same treatment
    before being hashed.
    """
    posix_rel = PurePosixPath(rel_path)
    if posix_rel.is_absolute() or ".." in posix_rel.parts:
        return None
    candidate = (repo_root / rel_path).resolve()
    if candidate != repo_root and not candidate.is_relative_to(repo_root):
        return None
    return str(candidate)


def _trim_tracked_paths(
    history: dict[str, Any], k: int, all_current: list[str], seen: set[str]
) -> tuple[list[str], int]:
    """Keeps the `MAX_TRACKED_PATHS` MOST RECENTLY CHANGED paths (HLD S8.4 M2 edge cases),
    replacing M1's stable-input-order-only trim.

    Judgment call (T-C6uQJW, see task report) -- "recency": this wave's own tracked paths
    (*all_current*, git-derived paths prioritized ahead of breadcrumb-declared ones -- see the
    caller's ordering comment) are always the most recent by definition. If they alone already
    reach the cap, they are kept in their existing (git-derived-first) order and nothing else
    is considered (there is no meaningful "more recent than this wave" ranking to apply among
    ties within the SAME wave). Otherwise, the remaining capacity is filled
    from paths recorded in prior checkpoints, ranked by the highest checkpoint number they
    were last seen at (ties broken alphabetically for determinism). Returns
    ``(trimmed_paths, dropped_count)``.
    """
    if len(all_current) >= MAX_TRACKED_PATHS:
        trimmed = all_current[:MAX_TRACKED_PATHS]
        return trimmed, len(all_current) - len(trimmed)

    last_seen_at: dict[str, int] = {}
    for key in sorted(history.keys()):
        if not key.isdigit() or int(key) >= k:
            continue
        entry = history[key]
        if not isinstance(entry, dict):
            continue
        paths = entry.get("paths")
        if not isinstance(paths, dict):
            continue
        ck_num = int(key)
        for older_path in paths:
            last_seen_at[older_path] = ck_num  # ascending key order -> most recent wins

    remaining_slots = MAX_TRACKED_PATHS - len(all_current)
    older_candidates = sorted(
        (p for p in last_seen_at if p not in seen),
        key=lambda p: (-last_seen_at[p], p),
    )
    carried = older_candidates[:remaining_slots]
    dropped = max(0, len(older_candidates) - len(carried))
    return all_current + carried, dropped


def verdict_path(inst: Path, k: int) -> Path:
    return checkpoint_dir(inst, k) / "verdict.json"


def _criteria_met_count(inst: Path, k: int) -> int | None:
    """Number of `criteria[].status == "met"` entries in ck-K's `verdict.json`, or `None`
    when that checkpoint has no verdict yet -- e.g. the CURRENT checkpoint, whose verdict the
    overseer writes only AFTER this digest is generated -- or the file is missing/malformed.
    """
    if k < 1:
        return None
    data = _read_optional_json(verdict_path(inst, k))
    if data is None:
        return None
    criteria = data.get("criteria")
    if not isinstance(criteria, list):
        return None
    return sum(1 for c in criteria if isinstance(c, dict) and c.get("status") == "met")


# =============================================================================================
# ===== I/O orchestrators: detect_signals / compute_progress (replace M1's stubs) ============
# =============================================================================================


def _content_oscillation_signals(inst: Path, k: int) -> list[dict[str, Any]]:
    """HLD S8.3 `content_oscillation`, across ALL recorded checkpoints (not just this one):
    a tracked path whose hash returned to a state seen >= 2 checkpoints earlier. Severity is
    uniform across every signal emitted this checkpoint: high if >= 2 qualifying paths or
    >= 2 total returns, else medium (ticket AC3).
    """
    history = _read_optional_json(path_history_path(inst)) or {}
    per_path: dict[str, list[str]] = {}
    for key in sorted(history.keys()):
        if not key.isdigit() or int(key) > k:
            continue
        entry = history[key]
        if not isinstance(entry, dict):
            continue
        paths = entry.get("paths")
        if not isinstance(paths, dict):
            continue
        for abs_path, digest in paths.items():
            per_path.setdefault(abs_path, []).append(str(digest))

    qualifying: list[tuple[str, int]] = []
    for abs_path, hist in per_path.items():
        hits = returns_to_earlier(hist)
        if hits:
            qualifying.append((abs_path, len(hits)))
    if not qualifying:
        return []

    total_returns = sum(count for _, count in qualifying)
    severity = "high" if len(qualifying) >= 2 or total_returns >= 2 else "medium"
    return [
        {
            "type": "content_oscillation",
            "severity": severity,
            "work_item": None,
            "ask_id": None,
            "path": abs_path,
            "evidence": {"returns": count},
            "message": f"{abs_path} returned to a content state seen at an earlier checkpoint",
        }
        for abs_path, count in qualifying
    ]


def _breadcrumb_integrity_signals(inst: Path, k: int) -> list[dict[str, Any]]:
    """HLD S8.3 `breadcrumb_integrity`: surfaces each entry M1's `update_path_history`
    already rejected for the CURRENT checkpoint (via `classify_path_entry`) as a `high`
    signal -- this does not re-implement the rejection logic (ticket AC6).
    """
    history = _read_optional_json(path_history_path(inst)) or {}
    entry = history.get(f"{k:02d}")
    if not isinstance(entry, dict):
        return []
    rejected = entry.get("rejected")
    if not isinstance(rejected, list):
        return []
    signals: list[dict[str, Any]] = []
    for item in rejected:
        if not isinstance(item, dict):
            continue
        entry_text = item.get("entry")
        signals.append(
            {
                "type": "breadcrumb_integrity",
                "severity": "high",
                "work_item": None,
                "ask_id": None,
                "path": entry_text,
                "evidence": {"unit_id": item.get("unit_id"), "reason": item.get("reason")},
                "message": f"breadcrumb changed_paths entry {entry_text!r} rejected: "
                f"{item.get('reason')}",
            }
        )
    return signals


def _consecutive_stall_waves(
    cfg: Config, inst: Path, k: int, unit_lines: list[dict[str, Any]]
) -> int:
    """Judgment call (T-C6uQJW, see task report) for HLD S8.3's `stall` rule ("no work item
    reached outcome:done with verdict in {pass,na}, AND the verdict-reported criteria_met
    count did not rise, for stall_waves consecutive waves"): the CURRENT checkpoint's own
    verdict doesn't exist yet when `ckpt-prep` runs, so criteria_met can only ever be known
    from a PAST checkpoint's `verdict.json`.

    Walks waves that have at least one ingested unit in ascending order through K (hold
    waves -- zero units -- are invisible per this ticket's own AC4: skipped, not counted as
    stalled or not), carrying the last-known criteria_met count forward across any wave whose
    own verdict isn't available yet. A wave is "productive" (resets the streak to 0) if it
    has a done+pass/na unit OR its carried-forward criteria_met is higher than the highest
    seen at any earlier non-hold wave. Returns the number of consecutive non-productive
    non-hold waves trailing wave K.
    """
    waves_with_units = sorted(
        {
            int(line["wave"])
            for line in unit_lines
            if isinstance(line.get("wave"), int) and line["wave"] <= k
        }
    )
    streak = 0
    last_criteria_met: int | None = None
    for wave in waves_with_units:
        wave_units = [line for line in unit_lines if line.get("wave") == wave]
        done = any(
            u.get("outcome") == "done" and u.get("verdict") in _DONE_VERDICTS for u in wave_units
        )
        cmet = _criteria_met_count(inst, wave)
        rose = False
        if cmet is not None:
            if last_criteria_met is not None and cmet > last_criteria_met:
                rose = True
            last_criteria_met = cmet if last_criteria_met is None else max(last_criteria_met, cmet)
        streak = 0 if (done or rose) else streak + 1
    return streak


def _ask_starvation_signals(
    cfg: Config, inst: Path, k: int, unit_lines: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """HLD S8.3 `ask_starvation`: a charter ask with zero units touching it in the last
    `cfg.stall_waves` waves, and not already `met`/`deferred` per the LATEST verdict's
    `alignment` (ck-(K-1) -- the most recent verdict written before this checkpoint's own
    digest is generated). No `charter.json` yet means no asks to starve, not an error.
    """
    charter = _read_optional_json(inst / "outputs" / "charter.json")
    if charter is None:
        return []
    asks = charter.get("asks")
    if not isinstance(asks, list):
        return []

    alignment_status: dict[str, str | None] = {}
    if k >= 2:
        verdict = _read_optional_json(verdict_path(inst, k - 1))
        if verdict is not None and isinstance(verdict.get("alignment"), list):
            for item in verdict["alignment"]:
                if isinstance(item, dict) and isinstance(item.get("ask_id"), str):
                    alignment_status[item["ask_id"]] = item.get("status")

    window_start = max(1, k - cfg.stall_waves + 1)
    touched_recent: set[str] = set()
    for line in unit_lines:
        wave = line.get("wave")
        if isinstance(wave, int) and window_start <= wave <= k:
            for ask_id in line.get("ask_ids") or []:
                touched_recent.add(str(ask_id))

    signals: list[dict[str, Any]] = []
    for ask in asks:
        if not isinstance(ask, dict):
            continue
        ask_id = ask.get("ask_id")
        if not isinstance(ask_id, str) or not ask_id:
            continue
        if alignment_status.get(ask_id) in _MET_OR_DEFERRED:
            continue
        if ask_id in touched_recent:
            continue
        signals.append(
            {
                "type": "ask_starvation",
                "severity": "medium",
                "work_item": None,
                "ask_id": ask_id,
                "path": None,
                "evidence": {"window_start_wave": window_start, "window_end_wave": k},
                "message": (f"ask {ask_id} has had no units in the last {cfg.stall_waves} wave(s)"),
            }
        )
    return signals


def _prompt_changed_signal(inst: Path) -> list[dict[str, Any]]:
    """HLD S8.3 `prompt_changed`: `sha256(prompt.md) != charter.json.prompt_sha256`. Both
    missing, or both present and matching, is not a signal.
    """
    prompt_path = inst / "prompt.md"
    prompt_sha256 = (
        hashlib.sha256(prompt_path.read_bytes()).hexdigest() if prompt_path.is_file() else None
    )
    charter = _read_optional_json(inst / "outputs" / "charter.json")
    charter_sha256 = charter.get("prompt_sha256") if charter is not None else None
    if prompt_sha256 == charter_sha256:
        return []
    return [
        {
            "type": "prompt_changed",
            "severity": "high",
            "work_item": None,
            "ask_id": None,
            "path": "prompt.md",
            "evidence": {"prompt_sha256": prompt_sha256, "charter_prompt_sha256": charter_sha256},
            "message": "prompt.md sha256 no longer matches charter.json's prompt_sha256",
        }
    ]


def _unit_lines_through(inst: Path, k: int) -> list[dict[str, Any]]:
    """Every ledger `type:"unit"` line ingested through wave *k* inclusive (reviewer finding,
    T-C6uQJW review: extracted so `detect_signals`/`compute_progress` share one definition of
    "the unit lines this checkpoint sees" instead of two copies of the same filter)."""
    return [
        line
        for line in read_ledger_lines(inst)
        if line.get("type") == "unit" and isinstance(line.get("wave"), int) and line["wave"] <= k
    ]


def detect_signals(cfg: Config, inst: Path, k: int) -> list[dict[str, Any]]:
    """HLD S8.4 M2 `detect_signals`: turns ledger + path history + previous verdicts +
    charter into the digest's `signals[]` (ticket ACs 1-7). Ids are assigned last, in a
    deterministic order independent of ledger line order on disk (AC7).
    """
    unit_lines = _unit_lines_through(inst, k)

    candidates: list[dict[str, Any]] = []
    candidates.extend(_period_mirror_signals(unit_lines))
    candidates.extend(_repeated_failure_signals(unit_lines))
    candidates.extend(_attempt_cap_signals(cfg, unit_lines))
    candidates.extend(_content_oscillation_signals(inst, k))
    candidates.extend(_breadcrumb_integrity_signals(inst, k))

    stall_count = _consecutive_stall_waves(cfg, inst, k, unit_lines)
    if stall_count >= cfg.stall_waves:
        candidates.append(
            {
                "type": "stall",
                "severity": "high",
                "work_item": None,
                "ask_id": None,
                "path": None,
                "evidence": {"stall_waves": stall_count, "threshold": cfg.stall_waves},
                "message": (
                    f"no work item finished and criteria_met has not risen for "
                    f"{stall_count} consecutive wave(s)"
                ),
            }
        )

    candidates.extend(_ask_starvation_signals(cfg, inst, k, unit_lines))
    candidates.extend(_blocked_units_signals(unit_lines, k))
    candidates.extend(_prompt_changed_signal(inst))

    return _assign_signal_ids(candidates, k)


def compute_progress(cfg: Config, inst: Path, k: int) -> dict[str, Any]:
    """HLD S8.4 M2 `progress` (digest schema S13.4): work-item totals/done/newly-done,
    the current consecutive stall count, the previous checkpoint's `criteria_met`, and a
    per-ask breakdown (ticket AC8).
    """
    unit_lines = _unit_lines_through(inst, k)

    done_through_k = _done_work_items(unit_lines)
    done_before_k = _done_work_items([line for line in unit_lines if line.get("wave") != k])
    work_items_all = {
        str(line["work_item"])
        for line in unit_lines
        if isinstance(line.get("work_item"), str) and line.get("work_item")
    }

    charter = _read_optional_json(inst / "outputs" / "charter.json")
    if charter is not None and isinstance(charter.get("asks"), list):
        ask_ids = [
            ask["ask_id"]
            for ask in charter["asks"]
            if isinstance(ask, dict) and isinstance(ask.get("ask_id"), str)
        ]
    else:
        # No charter yet (or a unit test calling this in isolation): fall back to whatever
        # ask_ids the ledger itself has already seen, so per_ask is never silently empty.
        ask_ids = sorted({str(a) for line in unit_lines for a in (line.get("ask_ids") or [])})

    per_ask: list[dict[str, Any]] = []
    for ask_id in ask_ids:
        touching = [line for line in unit_lines if ask_id in (line.get("ask_ids") or [])]
        done_items_for_ask = {
            str(line["work_item"])
            for line in touching
            if line.get("outcome") == "done"
            and line.get("verdict") in _DONE_VERDICTS
            and isinstance(line.get("work_item"), str)
            and line.get("work_item")
        }
        per_ask.append(
            {
                "ask_id": ask_id,
                "units": len(touching),
                "cost_usd": sum(float(line.get("cost_usd") or 0.0) for line in touching),
                "done_items": len(done_items_for_ask),
                "last_wave_touched": max((line["wave"] for line in touching), default=None),
            }
        )

    return {
        "work_items_total": len(work_items_all),
        "work_items_done": len(done_through_k),
        "newly_done": len(done_through_k - done_before_k),
        "stall_waves": _consecutive_stall_waves(cfg, inst, k, unit_lines),
        "criteria_met_prev": _criteria_met_count(inst, k - 1) or 0,
        "per_ask": per_ask,
    }


# =============================================================================================
# ===== M3 reservation: intake-check / ckpt-check / expander-check (T-HPJcc6, T-tAKBBB) ======
# ===== New subcommands + dispatch entries only -- nothing to implement here yet. ============
# =============================================================================================


# =============================================================================================
# ===== CLI subcommands (M1): intake-prep, ckpt-prep, unit-gate, request-closeout ============
# =============================================================================================


def _write_prep_result(dir_path: Path, violation: Violation, now: datetime) -> None:
    """Judgment call (see task report): only `intake-prep`/`ckpt-prep` write this file --
    both have a natural "own output dir" (`outputs/` for intake, `outputs/checkpoints/ck-KK/`
    for a checkpoint). `unit-gate` must write ZERO files even on its failure path (it needs to
    stay $0/fast, and the ticket's own AC5 tests the file list is unchanged), and
    `request-closeout` is an operator command whose own stderr message is already the
    complete answer.
    """
    write_json_atomic(
        dir_path / RESULT_FILENAME,
        {
            "schema": PREP_RESULT_SCHEMA,
            "ok": False,
            "rule_id": violation.rule_id,
            "message": str(violation),
            "generated_at": now_iso(now),
        },
    )


def intake_prep(args: argparse.Namespace) -> None:
    """HLD S8.4 M1 `intake_prep` (pre_hook of `intake`)."""
    ws, inst = confine_workspace(args.workspace_root, args.instance_dir)
    now = parse_now(args.now)
    try:
        cfg = load_config(inst)
        print("python", sys.version)  # A-1 preflight breadcrumb (README's documented remedy).
        ctx = read_hook_context(args)
        run_state_path = state_path(ws, cfg, ctx.run_id)
        if not run_state_path.is_file():
            raise Violation("ST-1", detail=str(run_state_path))
        for rel in _OUTPUT_SUBDIRS:
            (inst / rel).mkdir(parents=True, exist_ok=True)
    except Violation as violation:
        if not args.dry_run:
            _write_prep_result(inst / "outputs", violation, now)
        raise


def parse_ck_task_id(task_id: str) -> int:
    match = _CK_TASK_ID_RE.match(task_id)
    if not match:
        raise ToolError(f"task_id {task_id!r} is not a checkpoint id (expected ck-NN)")
    return int(match.group(1))


def ckpt_prep(args: argparse.Namespace) -> None:
    """HLD S8.4 M1 `ckpt_prep` (pre_hook of `ck-K`) -- ties config/state/ledger/hold/charter-
    lock/budget together and writes this checkpoint's `digest.json` (HLD S13.4 schema).
    """
    ws, inst = confine_workspace(args.workspace_root, args.instance_dir)
    now = parse_now(args.now)
    k = 0
    try:
        cfg = load_config(inst)
        ctx = read_hook_context(args)
        k = parse_ck_task_id(ctx.task_id)
        verify_ledger_chain(inst)
        hold_gate(inst, k, now, dry_run=args.dry_run)
        verify_charter_lock(inst)
        state = load_state(state_path(ws, cfg, ctx.run_id))
        units = wave_units(state, k)
        ingest_ledger(inst, units, state, k, now, dry_run=args.dry_run)
        update_path_history(inst, k, ctx.repo_paths, units, now, dry_run=args.dry_run)
        budget = compute_budget(cfg, state, inst, k, now, dry_run=args.dry_run)
        signals = detect_signals(cfg, inst, k)
        progress = compute_progress(cfg, inst, k)

        digest = {
            "schema": DIGEST_SCHEMA,
            "contract_version": cfg.contract_version,
            "checkpoint": f"ck-{k:02d}",
            "wave": k,
            "run_id": ctx.run_id,
            "generated_at": now_iso(now),
            "budget": budget["budget"],
            "cadence": budget["cadence"],
            "must_close": budget["must_close"],
            "must_close_reasons": budget["must_close_reasons"],
            "allowed_decisions": budget["allowed_decisions"],
            "stabilize_passes": budget["stabilize_passes"],
            "progress": progress,
            "capped_work_items": [],
            "signals": signals,
        }
        if args.dry_run:
            print(json.dumps(digest, indent=2, sort_keys=True))
        else:
            write_json_atomic(digest_path(inst, k), digest)
    except Violation as violation:
        if not args.dry_run:
            _write_prep_result(checkpoint_dir(inst, k) if k else inst / "outputs", violation, now)
        raise


def unit_gate(args: argparse.Namespace) -> None:
    """HLD S8.4 M1 `unit_gate` (pre_hook of every wave unit, FR-18) -- must be fast, $0, and
    writes NOTHING at all, success or failure (ticket AC5).
    """
    ws, inst = confine_workspace(args.workspace_root, args.instance_dir)
    now = parse_now(args.now)
    cfg = load_config(inst)
    ctx = read_hook_context(args)
    state = load_state(state_path(ws, cfg, ctx.run_id))
    run_budget_usd, _override_info = effective_budget(cfg, state, inst, now, record=False)

    if state.spent >= run_budget_usd:
        raise Violation(
            "BUDGET",
            message=(
                f"BUDGET: spent ${state.spent:.2f} >= budget ${run_budget_usd:.2f}; "
                "new work refused. To continue: write control/budget-override.json AND run "
                "`ao resume --extend-breaker run-budget-backstop`"
            ),
        )
    if len(state.injected_ids) > cfg.max_injected_tasks:
        raise Violation(
            "FANOUT",
            message=(
                f"FANOUT: injected task count {len(state.injected_ids)} exceeds "
                f"max_injected_tasks={cfg.max_injected_tasks}; refusing new work"
            ),
        )


def resolve_run_id_for_instance(workspace_root: Path, inst: Path, cfg: Config) -> str:
    """`request-closeout` is an operator CLI, never a hook (FR-19) -- there is no
    `$AO_HOOK_CONTEXT_PATH` to read `run_id` from, and neither the HLD nor the ticket specify
    how an operator names the run for this subcommand. Judgment call (see task report):
    resolve it by reading the instance's own rendered `workflow.json` `id` and finding the
    run(s) under `<workspace_root>/<runs_root>` whose `state.json.workflow_id` matches it,
    picking the most-recently-started one if more than one resume attempt's state exists.
    """
    workflow_json_path = inst / "workflow.json"
    if not workflow_json_path.is_file():
        raise Violation("ST-1", detail=f"{workflow_json_path} not found")
    try:
        workflow_data = read_json_bounded(workflow_json_path, JSON_MAX_BYTES)
    except (OversizeInputError, json.JSONDecodeError) as exc:
        # Reviewer finding (T-ABDjSj review): consistent with this same function's own
        # per-candidate state.json reads three lines below, which already catch this --
        # a malformed workflow.json must fail through ST-1, not an uncaught exception.
        raise Violation("ST-1", detail=f"{workflow_json_path} is not valid JSON: {exc}") from exc
    workflow_id = workflow_data.get("id") if isinstance(workflow_data, dict) else None

    runs_root = workspace_root / cfg.runs_root
    candidates: list[tuple[str, str]] = []
    if runs_root.is_dir():
        for child in sorted(runs_root.iterdir()):
            state_file = child / "state.json"
            if not state_file.is_file():
                continue
            try:
                data = read_json_bounded(state_file, STATE_MAX_BYTES)
            except (OversizeInputError, json.JSONDecodeError):
                continue
            if isinstance(data, dict) and data.get("workflow_id") == workflow_id:
                candidates.append((str(data.get("started_at", "")), child.name))
    if not candidates:
        raise Violation("ST-1", detail=f"no run state found for workflow_id={workflow_id!r}")
    candidates.sort()
    return candidates[-1][1]


def request_closeout(args: argparse.Namespace) -> None:
    """HLD S8.4 M1 `request_closeout` (FR-19) -- an operator CLI command, never a hook."""
    ws, inst = confine_workspace(args.workspace_root, args.instance_dir)
    now = parse_now(args.now)
    cfg = load_config(inst)
    run_id = resolve_run_id_for_instance(ws, inst, cfg)
    state = load_state(state_path(ws, cfg, run_id))
    if state.status not in ("failed", "cancelled"):
        raise Violation("RUNNING", detail=f"status={state.status}")

    pending = sorted(
        tid
        for tid, info in state.tasks.items()
        if _WAVE_UNIT_PREFIX_RE.match(tid) and info.status == "pending"
    )
    for unit_id in pending:
        match = _WAVE_UNIT_PREFIX_RE.match(unit_id)
        assert match is not None  # guaranteed by the filter above
        wave_no = int(match.group(1))
        report_path = inst / "outputs" / "waves" / f"w{wave_no:02d}" / f"{unit_id}.md"
        crumb_path = breadcrumb_path(inst, unit_id)
        if report_path.exists() or crumb_path.exists():
            continue  # never overwrite an existing report/breadcrumb
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            f"skipped: operator requested close-out ({args.reason})\n", encoding="utf-8"
        )
        write_json_atomic(
            crumb_path,
            {
                "schema": BREADCRUMB_SCHEMA,
                "unit_id": unit_id,
                "outcome": "no_op",
                "verdict": "na",
                "summary": "skipped: forced close-out",
                "changed_paths": [],
                "needs_input": False,
            },
        )

    append_chained(
        inst,
        {
            "type": "event",
            "event": "forced_closeout",
            "reason": args.reason,
            "pending_units": pending,
        },
        now,
    )
    print(
        "OK: resume with `ao resume`; pending units will be skipped and the next "
        "checkpoint will close out"
    )


# =============================================================================================
# ===== argparse / main =======================================================================
# =============================================================================================


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="overseer_tool.py")
    subparsers = parser.add_subparsers(dest="subcommand", required=True)
    for name in ("intake-prep", "ckpt-prep", "unit-gate", "request-closeout"):
        sub = subparsers.add_parser(name)
        sub.add_argument("--workspace-root", required=True)
        sub.add_argument("--instance-dir", required=True)
        sub.add_argument("--now", default=None)
        sub.add_argument("--dry-run", action="store_true")
        sub.add_argument("--task-id", default=None)
        if name == "request-closeout":
            sub.add_argument("--reason", required=True)
        else:
            sub.add_argument("--reason", default=None)
    return parser


_DISPATCH: Final[dict[str, Any]] = {
    "intake-prep": intake_prep,
    "ckpt-prep": ckpt_prep,
    "unit-gate": unit_gate,
    "request-closeout": request_closeout,
}


def main(argv: list[str] | None = None) -> int:
    """HLD S8.4 M1 `main()` skeleton: 0 pass, 2 contract violation/hold/budget refusal
    (rule id + message on stderr), 1 internal error (any uncaught exception, never silent).
    """
    args = _build_parser().parse_args(argv)
    try:
        _DISPATCH[args.subcommand](args)
        return 0
    except Violation as violation:
        print(str(violation), file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"overseer_tool internal error: {exc!r}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
