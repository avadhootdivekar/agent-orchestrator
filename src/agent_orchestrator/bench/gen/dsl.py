"""Small builders + constants for emitting probes.json (bench-probe-dsl.md).

Only shape helpers -- the semantics live in the DSL document and the (separate) blackbox grader.
"""

from __future__ import annotations

from typing import Any

from .base import PROBES_VERSION

UUID_RE = "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
ISO_MS_RE = "^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\\.[0-9]{3}Z$"
RAND_RE = "[0-9a-f]{8}"

GROUP_WEIGHTS = {"core": 0.5, "trap": 0.35, "perf": 0.15}
PASS_THRESHOLD = 0.8
STARTUP_TIMEOUT_BY_LANGUAGE = {"python": 30, "typescript": 30, "go": 120}  # `go run` compiles
PERF_EXPECTED_ALPHA = 0.3
PERF_ALPHA_SLACK = 0.2
PERF_REF_RATIO_MAX = 5.0
PERF_OPS_PER_SIZE = 500
PERF_REPEATS = 3
PERF_CHECKS = ["alpha", "ref_ratio", "timeout"]
# Perf dataset sizes by difficulty.scale (HLD §5.2: [1e2,1e4,1e5], scaled by difficulty).
PERF_SIZES_BY_SCALE: tuple[tuple[int, ...], ...] = (
    (100, 500),
    (100, 1000),
    (100, 10000),
    (100, 10000, 100000),
)
# Parallel workers by difficulty.concurrency (0 = no parallel phase).
WORKERS_BY_CONCURRENCY: tuple[int, ...] = (0, 4, 8, 16)


def tmpl(name: str) -> str:
    """``{{name}}`` template reference."""
    return "{{" + name + "}}"


def http_op(
    method: str,
    path: str,
    body: Any = None,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    op: dict[str, Any] = {"method": method, "path": path, "json": body}
    if headers:
        op["headers"] = headers
    return op


def step(
    op: dict[str, Any],
    *,
    status: int | list[int] | None = None,
    match: Any = None,
    save: dict[str, str] | None = None,
    repeat: int | None = None,
    has_match: bool = False,
) -> dict[str, Any]:
    """One DSL step.  ``match`` (the ``expect.json`` matcher) may legitimately be ``None`` (JSON
    null), so ``has_match`` distinguishes 'no matcher' from 'matcher is null'."""
    s: dict[str, Any] = {"op": op}
    expect: dict[str, Any] = {}
    if status is not None:
        expect["status"] = status
    if match is not None or has_match:
        expect["json"] = match
    if expect:
        s["expect"] = expect
    if save:
        s["save"] = save
    if repeat is not None:
        s["repeat"] = repeat
    return s


def jsonl_step(
    request: Any,
    *,
    match: Any = None,
    save: dict[str, str] | None = None,
    repeat: int | None = None,
) -> dict[str, Any]:
    return step({"json": request}, match=match, save=save, repeat=repeat)


def probe(
    probe_id: str,
    steps: list[dict[str, Any]],
    *,
    trap: str | None = None,
    fresh_state: bool = True,
    parallel: list[list[dict[str, Any]]] | None = None,
    after: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    p: dict[str, Any] = {"id": probe_id}
    if trap is not None:
        p["trap"] = trap
    if not fresh_state:
        p["fresh_state"] = False
    p["steps"] = steps
    if parallel:
        p["parallel"] = parallel
    if after:
        p["after"] = after
    return p


def probes_doc(
    contract: str,
    language: str,
    core: list[dict[str, Any]],
    trap: list[dict[str, Any]],
    perf: dict[str, Any],
) -> dict[str, Any]:
    return {
        "version": PROBES_VERSION,
        "contract": contract,
        "startup_timeout_seconds": STARTUP_TIMEOUT_BY_LANGUAGE[language],
        "groups": {"core": core, "trap": trap, "perf": perf},
        "weights": dict(GROUP_WEIGHTS),
        "pass_threshold": PASS_THRESHOLD,
    }


def perf_spec(
    scale: int,
    load: dict[str, Any],
    mix: list[dict[str, Any]],
) -> dict[str, Any]:
    """Perf group (DSL §4).  ``load.repeat`` is a placeholder (the grader sets it to each N);
    mix weights must sum to 1.0 and every mix step may only reference variables the load step
    saves (the first drawn op can be any of them)."""
    sizes = list(PERF_SIZES_BY_SCALE[scale])
    classes = sorted({m["class"] for m in mix})
    load = dict(load)
    load["repeat"] = sizes[0]
    return {
        "sizes": sizes,
        "load": load,
        "mix": mix,
        "ops_per_size": PERF_OPS_PER_SIZE,
        "repeats": PERF_REPEATS,
        "expected_alpha": {c: PERF_EXPECTED_ALPHA for c in classes},
        "alpha_slack": PERF_ALPHA_SLACK,
        "ref_ratio_max": PERF_REF_RATIO_MAX,
        "checks": list(PERF_CHECKS),
    }


def mix_entry(weight: float, name: str, op_class: str, mix_step: dict[str, Any]) -> dict[str, Any]:
    return {"weight": weight, "name": name, "class": op_class, "step": mix_step}
