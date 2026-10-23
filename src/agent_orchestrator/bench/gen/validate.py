"""Structural validator for a generated ``probes.json`` (bench-probe-dsl.md §2-§4).

Independent of the grader (`bench/blackbox.py` is a separate module): this only checks that what a
family emits is *well-formed DSL* -- shapes, matcher directives, JSONPath-lite, and that every
``{{var}}`` / ``{"$var": ..}`` is saved earlier in scope.  It cannot say a probe is *correct*
(that is the reference-passes-all-probes check run against the real grader).

``validate_probes`` returns problems (empty = ok); ``check_probes`` raises ``ValueError``.
"""

from __future__ import annotations

import re
from typing import Any

from .traps import TRAP_CATALOGUE

CONTRACTS = ("http", "jsonl", "cli")
HTTP_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"})
EXPECT_KEYS = frozenset({"status", "exit_code", "json", "stdout_contains", "max_ms"})
STEP_KEYS = frozenset({"op", "expect", "save", "repeat", "sleep_ms"})
PROBE_KEYS = frozenset({"id", "trap", "fresh_state", "steps", "parallel", "after"})
PERF_KEYS = frozenset(
    {
        "sizes", "load", "mix", "ops_per_size", "repeats", "expected_alpha", "alpha_slack",
        "ref_ratio_max", "checks",
    }
)  # fmt: skip
PERF_CHECKS = frozenset({"alpha", "ref_ratio", "timeout"})
BUILTIN_VARS = frozenset({"rand", "now_iso"})
JSON_TYPES = frozenset({"string", "number", "integer", "boolean", "array", "object", "null"})
DIRECTIVES = frozenset(
    {
        "$any", "$type", "$len", "$len_min", "$len_max", "$regex", "$in", "$gte", "$lte",
        "$sorted_by", "$set_equals", "$each", "$contains", "$var", "$not",
    }
)  # fmt: skip
_TEMPLATE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
_JSONPATH_LITE = re.compile(r"^\$(?:\.[A-Za-z_][A-Za-z0-9_]*|\[\d+\])*$")
_WEIGHT_EPS = 1e-9


def _template_names(value: Any) -> set[str]:
    """Every ``{{name}}`` in any string nested in *value* (dict keys included)."""
    found: set[str] = set()
    if isinstance(value, str):
        found.update(_TEMPLATE.findall(value))
    elif isinstance(value, dict):
        for k, v in value.items():
            found.update(_template_names(k))
            found.update(_template_names(v))
    elif isinstance(value, list):
        for v in value:
            found.update(_template_names(v))
    return found


def _var_refs(matcher: Any) -> set[str]:
    """Names referenced by ``{"$var": name}`` directives inside a matcher."""
    refs: set[str] = set()
    if isinstance(matcher, dict):
        if "$var" in matcher and isinstance(matcher["$var"], str):
            refs.add(matcher["$var"])
        for v in matcher.values():
            refs.update(_var_refs(v))
    elif isinstance(matcher, list):
        for v in matcher:
            refs.update(_var_refs(v))
    return refs


def _check_matcher(m: Any, where: str, errs: list[str]) -> None:
    if isinstance(m, list):
        for i, item in enumerate(m):
            _check_matcher(item, f"{where}[{i}]", errs)
        return
    if not isinstance(m, dict):
        return  # scalar: equality
    dkeys = [k for k in m if isinstance(k, str) and k.startswith("$")]
    if not dkeys:
        for k, v in m.items():
            _check_matcher(v, f"{where}.{k}", errs)
        return
    if len(dkeys) != 1:
        errs.append(f"{where}: directive object must have exactly one $key, got {dkeys}")
        return
    d = dkeys[0]
    if d not in DIRECTIVES:
        errs.append(f"{where}: unknown directive {d}")
        return
    extra = set(m) - {d}
    if d == "$sorted_by":
        extra -= {"order", "tiebreak"}
        if m.get("order") not in ("asc", "desc"):
            errs.append(f"{where}: $sorted_by needs order 'asc'|'desc'")
        if not isinstance(m[d], str):
            errs.append(f"{where}: $sorted_by must name a field")
        if "tiebreak" in m and not isinstance(m["tiebreak"], str):
            errs.append(f"{where}: $sorted_by tiebreak must be a field name")
    if extra:
        errs.append(f"{where}: unexpected keys {sorted(extra)} next to {d}")
    arg = m[d]
    if d == "$type" and arg not in JSON_TYPES:
        errs.append(f"{where}: $type {arg!r} not in {sorted(JSON_TYPES)}")
    elif d in ("$len", "$len_min", "$len_max") and (
        isinstance(arg, bool) or not isinstance(arg, int)
    ):
        errs.append(f"{where}: {d} needs an integer")
    elif d == "$regex":
        try:
            re.compile(arg)
        except (re.error, TypeError):
            errs.append(f"{where}: invalid $regex {arg!r}")
    elif d in ("$in", "$set_equals") and not isinstance(arg, list):
        errs.append(f"{where}: {d} needs an array")
    elif d in ("$gte", "$lte") and (isinstance(arg, bool) or not isinstance(arg, (int, float))):
        errs.append(f"{where}: {d} needs a number")
    elif d == "$var" and not isinstance(arg, str):
        errs.append(f"{where}: $var needs a variable name")
    elif d in ("$each", "$contains", "$not"):
        _check_matcher(arg, f"{where}.{d}", errs)


def _check_op(op: Any, contract: str, where: str, errs: list[str]) -> None:
    if not isinstance(op, dict):
        errs.append(f"{where}: op must be an object")
        return
    if contract == "http":
        allowed = {"method", "path", "json", "headers"}
        if op.get("method") not in HTTP_METHODS:
            errs.append(f"{where}: http op.method must be one of {sorted(HTTP_METHODS)}")
        path = op.get("path")
        if not isinstance(path, str) or not path.startswith("/"):
            errs.append(f"{where}: http op.path must be a string starting with '/'")
        headers = op.get("headers")
        if headers is not None and not (
            isinstance(headers, dict) and all(isinstance(v, str) for v in headers.values())
        ):
            errs.append(f"{where}: http op.headers must map str -> str")
    elif contract == "jsonl":
        allowed = {"json"}
        if "json" not in op:
            errs.append(f"{where}: jsonl op needs 'json'")
    else:
        allowed = {"args", "stdin"}
        args = op.get("args")
        if not (isinstance(args, list) and all(isinstance(a, str) for a in args)):
            errs.append(f"{where}: cli op.args must be a list of strings")
    unknown = set(op) - allowed
    if unknown:
        errs.append(f"{where}: unknown op keys {sorted(unknown)} for contract {contract}")


def _check_expect(expect: Any, contract: str, where: str, errs: list[str]) -> None:
    if not isinstance(expect, dict):
        errs.append(f"{where}: expect must be an object")
        return
    unknown = set(expect) - EXPECT_KEYS
    if unknown:
        errs.append(f"{where}: unknown expect keys {sorted(unknown)}")
    status = expect.get("status")
    if status is not None:
        if contract != "http":
            errs.append(f"{where}: expect.status is http-only")
        ok_int = isinstance(status, int) and not isinstance(status, bool)
        ok_list = (
            isinstance(status, list)
            and status
            and all(isinstance(s, int) and not isinstance(s, bool) for s in status)
        )
        if not (ok_int or ok_list):
            errs.append(f"{where}: expect.status must be an int or a non-empty list of ints")
    if "exit_code" in expect and contract != "cli":
        errs.append(f"{where}: expect.exit_code is cli-only")
    if "stdout_contains" in expect and contract != "cli":
        errs.append(f"{where}: expect.stdout_contains is cli-only")
    if "json" in expect:
        _check_matcher(expect["json"], f"{where}.expect.json", errs)


def _check_step(
    s: Any,
    contract: str,
    where: str,
    scope: set[str],
    errs: list[str],
    *,
    allow_index: bool | None = None,
) -> set[str]:
    """Validate one step against the variables in *scope*; return the names it saves."""
    if not isinstance(s, dict):
        errs.append(f"{where}: step must be an object")
        return set()
    unknown = set(s) - STEP_KEYS
    if unknown:
        errs.append(f"{where}: unknown step keys {sorted(unknown)}")
    _check_op(s.get("op"), contract, f"{where}.op", errs)
    if "expect" in s:
        _check_expect(s["expect"], contract, f"{where}.expect", errs)
    repeat = s.get("repeat")
    if repeat is not None and (
        isinstance(repeat, bool) or not isinstance(repeat, int) or repeat < 1
    ):
        errs.append(f"{where}: repeat must be a positive integer")
    sleep = s.get("sleep_ms")
    if sleep is not None and (isinstance(sleep, bool) or not isinstance(sleep, int) or sleep < 0):
        errs.append(f"{where}: sleep_ms must be a non-negative integer")
    has_index = (repeat is not None) if allow_index is None else allow_index
    visible = set(scope) | BUILTIN_VARS | ({"i"} if has_index else set())
    used = _template_names(s.get("op")) | _template_names(s.get("expect"))
    used |= _var_refs(s.get("expect"))
    for name in sorted(used - visible):
        errs.append(f"{where}: variable {name!r} is not saved earlier in scope")
    saved: set[str] = set()
    save = s.get("save")
    if save is not None:
        if not isinstance(save, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in save.items()
        ):
            errs.append(f"{where}: save must map name -> JSONPath string")
        else:
            for name, path in save.items():
                if not _JSONPATH_LITE.match(path):
                    errs.append(f"{where}: save {name!r}: bad JSONPath-lite {path!r}")
            saved = set(save)
    return saved


def _check_steps(
    steps: Any,
    contract: str,
    where: str,
    scope: set[str],
    errs: list[str],
    *,
    min_len: int,
) -> set[str]:
    """Validate a step list; returns the scope after it (scope + everything it saved)."""
    if not isinstance(steps, list) or len(steps) < min_len:
        errs.append(f"{where}: must be a list of at least {min_len} step(s)")
        return set(scope)
    cur = set(scope)
    for i, s in enumerate(steps):
        cur |= _check_step(s, contract, f"{where}[{i}]", cur, errs)
    return cur


def _check_probe(p: Any, group: str, contract: str, where: str, errs: list[str]) -> str | None:
    if not isinstance(p, dict):
        errs.append(f"{where}: probe must be an object")
        return None
    unknown = set(p) - PROBE_KEYS
    if unknown:
        errs.append(f"{where}: unknown probe keys {sorted(unknown)}")
    pid = p.get("id")
    if not isinstance(pid, str) or not re.match(r"^[a-z0-9][a-z0-9-]*$", pid or ""):
        errs.append(f"{where}: id must be lowercase kebab, got {pid!r}")
        pid = None
    trap = p.get("trap")
    if trap is not None:
        if group != "trap":
            errs.append(f"{where}: 'trap' only allowed in the trap group")
        elif trap not in TRAP_CATALOGUE:
            errs.append(f"{where}: trap {trap!r} not in the catalogue")
    elif group == "trap":
        errs.append(f"{where}: trap-group probe needs a 'trap' key")
    if "fresh_state" in p and not isinstance(p["fresh_state"], bool):
        errs.append(f"{where}: fresh_state must be boolean")
    after_steps = _check_steps(p.get("steps"), contract, f"{where}.steps", set(), errs, min_len=1)
    if "parallel" in p:
        par = p["parallel"]
        if not isinstance(par, list) or not par:
            errs.append(f"{where}.parallel: must be a non-empty list of step lists")
        else:
            for li, lst in enumerate(par):
                _check_steps(lst, contract, f"{where}.parallel[{li}]", after_steps, errs, min_len=1)
    if "after" in p:
        _check_steps(p["after"], contract, f"{where}.after", after_steps, errs, min_len=1)
    return pid


def _check_perf(perf: Any, contract: str, errs: list[str]) -> None:
    where = "groups.perf"
    if not isinstance(perf, dict):
        errs.append(f"{where}: must be an object")
        return
    missing = PERF_KEYS - set(perf)
    unknown = set(perf) - PERF_KEYS
    if missing:
        errs.append(f"{where}: missing keys {sorted(missing)}")
    if unknown:
        errs.append(f"{where}: unknown keys {sorted(unknown)}")
    sizes = perf.get("sizes")
    if not (
        isinstance(sizes, list)
        and len(sizes) >= 2
        and all(isinstance(n, int) and not isinstance(n, bool) and n > 0 for n in sizes)
        and sizes == sorted(set(sizes))
    ):
        errs.append(
            f"{where}.sizes: need >=2 distinct ascending positive ints (alpha needs a slope)"
        )
    for key in ("ops_per_size", "repeats"):
        v = perf.get(key)
        if isinstance(v, bool) or not isinstance(v, int) or v < 1:
            errs.append(f"{where}.{key}: must be a positive int")
    load = perf.get("load")
    load_saved: set[str] = set()
    if isinstance(load, dict):
        if not isinstance(load.get("repeat"), int) or isinstance(load.get("repeat"), bool):
            errs.append(f"{where}.load: needs an integer 'repeat' (placeholder for N)")
        load_saved = _check_step(load, contract, f"{where}.load", set(), errs)
    else:
        errs.append(f"{where}.load: must be a step object")
    mix = perf.get("mix")
    classes: set[str] = set()
    if not isinstance(mix, list) or not mix:
        errs.append(f"{where}.mix: must be a non-empty list")
    else:
        total = 0.0
        names: set[str] = set()
        for i, m in enumerate(mix):
            mw = f"{where}.mix[{i}]"
            if not isinstance(m, dict) or set(m) != {"weight", "name", "class", "step"}:
                errs.append(f"{mw}: needs exactly weight/name/class/step")
                continue
            w = m["weight"]
            if isinstance(w, bool) or not isinstance(w, (int, float)) or w <= 0:
                errs.append(f"{mw}.weight: must be a positive number")
            else:
                total += float(w)
            if not isinstance(m["name"], str) or m["name"] in names:
                errs.append(f"{mw}.name: must be a unique string")
            else:
                names.add(m["name"])
            if not isinstance(m["class"], str):
                errs.append(f"{mw}.class: must be a string")
            else:
                classes.add(m["class"])
            if isinstance(m["step"], dict) and "repeat" in m["step"]:
                errs.append(f"{mw}.step: repeat is not allowed in a mix step")
            # A drawn op may come first, so it may only use variables the load step saved.
            _check_step(m["step"], contract, f"{mw}.step", load_saved, errs, allow_index=False)
        if abs(total - 1.0) > 1e-6 + _WEIGHT_EPS:
            errs.append(f"{where}.mix: weights sum to {total}, expected 1.0")
    alpha = perf.get("expected_alpha")
    if not isinstance(alpha, dict) or set(alpha) != classes:
        errs.append(
            f"{where}.expected_alpha: must have exactly the mix op classes {sorted(classes)}"
        )
    checks = perf.get("checks")
    if not (isinstance(checks, list) and checks and set(checks) <= PERF_CHECKS):
        errs.append(f"{where}.checks: must be a non-empty subset of {sorted(PERF_CHECKS)}")
    for key in ("alpha_slack", "ref_ratio_max"):
        v = perf.get(key)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v <= 0:
            errs.append(f"{where}.{key}: must be a positive number")


def validate_probes(doc: Any) -> list[str]:
    """Return a list of DSL problems in *doc* (a decoded probes.json); empty means well-formed."""
    errs: list[str] = []
    if not isinstance(doc, dict):
        return ["probes.json must be an object"]
    if doc.get("version") != "1.0":
        errs.append("version must be '1.0'")
    contract = doc.get("contract")
    if contract not in CONTRACTS:
        errs.append(f"contract must be one of {CONTRACTS}")
        return errs
    timeout = doc.get("startup_timeout_seconds")
    if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout < 1:
        errs.append("startup_timeout_seconds must be a positive int")
    groups = doc.get("groups")
    if not isinstance(groups, dict) or set(groups) != {"core", "trap", "perf"}:
        errs.append("groups must have exactly core/trap/perf")
        return errs
    seen: set[str] = set()
    for group in ("core", "trap"):
        probes = groups[group]
        if not isinstance(probes, list) or not probes:
            errs.append(f"groups.{group}: must be a non-empty list")
            continue
        for i, p in enumerate(probes):
            pid = _check_probe(p, group, contract, f"groups.{group}[{i}]", errs)
            if pid is not None:
                if pid in seen:
                    errs.append(f"groups.{group}[{i}]: duplicate probe id {pid!r}")
                seen.add(pid)
    _check_perf(groups["perf"], contract, errs)
    weights = doc.get("weights")
    if weights is not None:
        if not (isinstance(weights, dict) and set(weights) == {"core", "trap", "perf"}):
            errs.append("weights must have exactly core/trap/perf")
        elif abs(sum(weights.values()) - 1.0) > 1e-6:
            errs.append(f"weights sum to {sum(weights.values())}, expected 1.0")
    thr = doc.get("pass_threshold")
    if thr is not None and (
        isinstance(thr, bool) or not isinstance(thr, (int, float)) or not 0 < thr <= 1
    ):
        errs.append("pass_threshold must be in (0, 1]")
    return errs


def check_probes(doc: Any) -> None:
    errs = validate_probes(doc)
    if errs:
        shown = "\n  ".join(errs[:20])
        more = f"\n  ... and {len(errs) - 20} more" if len(errs) > 20 else ""
        raise ValueError(f"invalid probes.json ({len(errs)} problem(s)):\n  {shown}{more}")
