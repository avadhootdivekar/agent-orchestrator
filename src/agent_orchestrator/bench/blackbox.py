"""`blackbox` grader (HLD §5.2, bench-probe-dsl.md).

Drives an agent-built artefact over its run contract (`http` / `jsonl` / `cli`) with the
probe DSL, scores `core` / `trap` / `perf` groups, and compares perf against the hidden
reference implementation run in the same session.

Layout of this module (top to bottom):

1. Matcher language (`match`, DSL §3.4) and JSONPath-lite (`json_path_get`).
2. Static validation of `probes.json` (`validate_probe_spec`): every structural defect --
   bad matcher, unknown variable, op/expect keys that do not fit the contract -- is a
   `GraderError` (infra), found *before* anything runs, so it never depends on how the
   artefact behaves.
3. Execution: `Templater`, transports (one per contract), `Session` (artefact lifecycle:
   a fresh copy of the artefact dir per `fresh_state` probe, so state files the artefact
   writes never leak between probes and the agent's repo is never mutated),
   `ProbeRunner` (steps / `parallel` / `after`).
4. Perf (`measure_perf`, `fit_alpha`, `evaluate_perf`, DSL §4).
5. `BlackboxGrader`.

Error classes (DSL §6): artefact misbehaviour is a failed probe, never a `GraderError`;
definition, sandbox and reference failures are `GraderError`.

Interpretations of points the DSL leaves open (all documented in the ticket STATUS):
- `{{name}}` that is the *whole* string and names a saved variable keeps the variable's
  JSON type (so a saved integer id can be sent as an integer); embedded in a longer string
  it is stringified. `{{i}}`, `{{rand}}`, `{{now_iso}}` always render to strings.
- In the perf mix, `{{i}}` is a seeded pseudo-random index in `[0, N)` (so
  `GET /things/{{i}}` hits existing keys); `{{N}}` is the current size.
- Latencies of mix ops whose `expect` failed are excluded from percentiles (a fast error
  response must not improve p95); the `load` step must satisfy its `expect`.
"""

from __future__ import annotations

import http.client
import json
import math
import random
import re
import shutil
import socket
import statistics
import struct
import tempfile
import threading
import time
from abc import ABC, abstractmethod
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .errors import GraderError
from .graders import Grader, GraderContext, GradeResult
from .registries import register_grader
from .sandbox import (
    ArtefactFailure,
    ArtefactStartError,
    ArtefactTimeout,
    Handle,
    JsonlHandle,
    Sandbox,
    SandboxConfig,
    create_sandbox,
)
from .spec import GraderConfig

BLACKBOX_GRADER_TYPE = "blackbox"

# ---------------------------------------------------------------------------
# Named constants (CLAUDE.md: no magic literals)
# ---------------------------------------------------------------------------

DEFAULT_PROBES_FILE = "probes.json"
INSTANCE_FILE = "instance.json"
REFERENCE_DIRNAME = "reference"
INSTANCE_ENV = "INSTANCE_JSON"
# Where the grader drops its copy of instance.json inside the *reference* run dir (never the
# artefact's). A distinct name so a reference that also ships its own sibling instance.json
# (needed to run as a copy-reference artefact) is not shadowed or confused.
RUN_DIR_INSTANCE_FILE = ".ao-instance.json"
RUNS_DIRNAME = "_runs"
DEFAULT_REFERENCE_LANGUAGE = "python"
DEFAULT_WEIGHTS: dict[str, float] = {"core": 0.5, "trap": 0.35, "perf": 0.15}
DEFAULT_PASS_THRESHOLD = 0.8
DEFAULT_STARTUP_TIMEOUT = 30.0
DEFAULT_PROBE_TIMEOUT_SECONDS = 60.0
DEFAULT_REQUEST_TIMEOUT_SECONDS = 10.0
DEFAULT_PERF_TIMEOUT_SECONDS = 300.0
DEFAULT_ALPHA_SLACK = 0.2
DEFAULT_REF_RATIO_MAX = 5.0
PERF_CHECKS = ("alpha", "ref_ratio", "timeout")
PERF_LATENCY_FLOOR_MS = 1e-3  # log() guard
MIX_WEIGHT_TOLERANCE = 1e-3
PARALLEL_BARRIER_TIMEOUT_SECONDS = 5.0
MAX_MISMATCHES_PER_PROBE = 20
MISMATCH_MAX_CHARS = 700
VALUE_PREVIEW_CHARS = 80
BODY_PREVIEW_CHARS = 120
RAW_TAIL_MAX_CHARS = 2000
CONTRACTS = ("http", "jsonl", "cli")
HTTP_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")
GROUPS = ("core", "trap", "perf")
# Env every run gets so a python artefact that forgets to flush stdout does not hang a
# jsonl probe (applies equally to the reference).
BASE_ARTEFACT_ENV = {"PYTHONUNBUFFERED": "1"}
# Names `{{...}}` resolves itself; a probe may not `save` into them.
BUILTIN_VARS = frozenset({"i", "rand", "now_iso"})
# Never copied into a per-probe run dir (VCS / tool caches only; deps dirs ARE copied
# because the host `process` fallback has no pre-installed deps).
COPY_IGNORE = (".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache")


class _Missing:
    """Sentinel: 'no such value' (absent key / empty body). Distinct from JSON null."""

    def __repr__(self) -> str:
        return "<missing>"


MISSING: Any = _Missing()


def _fail(where: str, msg: str) -> GraderError:
    return GraderError(f"probes definition error at {where}: {msg}")


# ===========================================================================
# 1. Matcher language (DSL §3.4)
# ===========================================================================

_JSON_TYPES = ("string", "number", "integer", "boolean", "array", "object", "null")
_SORT_ORDERS = ("asc", "desc")
# Sibling keys a directive object may carry besides its single `$key`.
_DIRECTIVE_MODIFIERS: dict[str, frozenset[str]] = {"$sorted_by": frozenset({"order", "tiebreak"})}


def _json_type(v: Any) -> str:
    if v is MISSING:
        return "missing"
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, int):
        return "integer"
    if isinstance(v, float):
        return "number"
    if isinstance(v, str):
        return "string"
    if isinstance(v, list):
        return "array"
    return "object"


def _is_type(v: Any, name: str) -> bool:
    if name == "number":
        return isinstance(v, (int, float)) and not isinstance(v, bool)
    if name == "integer":  # JSON-schema semantics: 1.0 is an integer
        return (isinstance(v, int) and not isinstance(v, bool)) or (
            isinstance(v, float) and v.is_integer()
        )
    return _json_type(v) == name


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _json_eq(a: Any, b: Any) -> bool:
    """JSON equality: `True != 1`, `1 == 1.0`, containers compared structurally."""
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if _is_number(a) and _is_number(b):
        return bool(a == b)
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_json_eq(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_json_eq(x, y) for x, y in zip(a, b, strict=True))
    return type(a) is type(b) and bool(a == b)


def _fmt(v: Any) -> str:
    if v is MISSING:
        return "<missing>"
    try:
        text = json.dumps(v, sort_keys=True, ensure_ascii=False)
    except (TypeError, ValueError):
        text = repr(v)
    return text if len(text) <= VALUE_PREVIEW_CHARS else text[: VALUE_PREVIEW_CHARS - 3] + "..."


def _canon(v: Any) -> str:
    """Canonical string for multiset comparison (ints/floats unified, bools distinct)."""
    if isinstance(v, bool) or v is None or isinstance(v, str):
        return json.dumps(v)
    if _is_number(v):
        return json.dumps(float(v))
    if isinstance(v, list):
        return "[" + ",".join(_canon(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{" + ",".join(f"{json.dumps(k)}:{_canon(v[k])}" for k in sorted(v)) + "}"
    return repr(v)


def _directive_key(m: Mapping[str, Any]) -> str | None:
    """The single `$directive` key of a matcher object, or None for a plain object matcher."""
    dkeys = [k for k in m if isinstance(k, str) and k.startswith("$")]
    if not dkeys:
        return None
    if len(dkeys) > 1:
        raise GraderError(f"matcher object has several $ directives: {sorted(dkeys)}")
    key = dkeys[0]
    if key not in _DIRECTIVES:
        raise GraderError(f"unknown matcher directive {key!r}; known: {sorted(_DIRECTIVES)}")
    stray = set(m) - {key} - _DIRECTIVE_MODIFIERS.get(key, frozenset())
    if stray:
        raise GraderError(f"matcher directive {key} takes no sibling keys, got {sorted(stray)}")
    return key


def _count_arg(key: str, arg: Any) -> int:
    if not isinstance(arg, int) or isinstance(arg, bool) or arg < 0:
        raise GraderError(f"{key} expects a non-negative integer, got {_fmt(arg)}")
    return arg


def _list_arg(key: str, arg: Any) -> list[Any]:
    if not isinstance(arg, list):
        raise GraderError(f"{key} expects an array, got {_fmt(arg)}")
    return arg


def _check_directive(key: str, m: Mapping[str, Any], found_vars: set[str]) -> None:
    """Static validation of one directive object (recurses into nested matchers)."""
    arg = m[key]
    if key == "$any":
        if arg is not True:
            raise GraderError('$any must be {"$any": true}')
    elif key == "$type":
        if arg not in _JSON_TYPES:
            raise GraderError(f"$type expects one of {list(_JSON_TYPES)}, got {_fmt(arg)}")
    elif key in ("$len", "$len_min", "$len_max"):
        _count_arg(key, arg)
    elif key == "$regex":
        if not isinstance(arg, str):
            raise GraderError(f"$regex expects a string, got {_fmt(arg)}")
        try:
            re.compile(arg)
        except re.error as exc:
            raise GraderError(f"$regex {arg!r} does not compile: {exc}") from exc
    elif key in ("$in", "$set_equals"):
        _list_arg(key, arg)
    elif key in ("$gte", "$lte"):
        if not (_is_number(arg) or isinstance(arg, str)):
            raise GraderError(f"{key} expects a number or string, got {_fmt(arg)}")
    elif key == "$sorted_by":
        if not isinstance(arg, str) or not arg:
            raise GraderError(f"$sorted_by expects a field name, got {_fmt(arg)}")
        if m.get("order", "asc") not in _SORT_ORDERS:
            raise GraderError(f"$sorted_by order must be one of {list(_SORT_ORDERS)}")
        tiebreak = m.get("tiebreak")
        if tiebreak is not None and (not isinstance(tiebreak, str) or not tiebreak):
            raise GraderError("$sorted_by tiebreak must be a field name")
    elif key == "$var":
        if not isinstance(arg, str) or not arg:
            raise GraderError(f"$var expects a variable name, got {_fmt(arg)}")
        found_vars.add(arg)
    elif key in ("$each", "$contains", "$not"):
        validate_matcher(arg, found_vars)


def validate_matcher(m: Any, found_vars: set[str] | None = None) -> set[str]:
    """Statically validate a matcher tree; raise `GraderError` on any defect. Returns the
    set of `$var` names it references (accumulated into `found_vars` when given)."""
    found = found_vars if found_vars is not None else set()
    if isinstance(m, dict):
        key = _directive_key(m)
        if key is not None:
            _check_directive(key, m, found)
        else:
            for sub in m.values():
                validate_matcher(sub, found)
    elif isinstance(m, list):
        for sub in m:
            validate_matcher(sub, found)
    return found


def _order_key(a: Any, b: Any) -> int | None:
    """-1/0/1 comparing two comparable scalars (numbers or strings), None if incomparable."""
    if _is_number(a) and _is_number(b):
        return (a > b) - (a < b)
    if isinstance(a, str) and isinstance(b, str):
        return (a > b) - (a < b)
    return None


def _get_field(obj: Any, name: str) -> Any:
    cur = obj
    for part in name.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return MISSING
        cur = cur[part]
    return cur


def _d_any(m: Mapping[str, Any], act: Any, path: str, _v: Mapping[str, Any]) -> list[str]:
    return [f"{path}: expected a present value, got <missing>"] if act is MISSING else []


def _d_type(m: Mapping[str, Any], act: Any, path: str, _v: Mapping[str, Any]) -> list[str]:
    want = m["$type"]
    if act is not MISSING and _is_type(act, want):
        return []
    return [f"{path}: expected type {want}, got {_json_type(act)}"]


def _d_len(m: Mapping[str, Any], act: Any, path: str, _v: Mapping[str, Any]) -> list[str]:
    key = next(k for k in ("$len", "$len_min", "$len_max") if k in m)
    if not isinstance(act, (str, list, dict)):
        return [f"{path}: {key} needs a string/array/object, got {_json_type(act)}"]
    want, n = m[key], len(act)
    ok = {"$len": n == want, "$len_min": n >= want, "$len_max": n <= want}[key]
    word = {"$len": "length", "$len_min": "length >=", "$len_max": "length <="}[key]
    return [] if ok else [f"{path}: expected {word} {want}, got length {n}"]


def _d_regex(m: Mapping[str, Any], act: Any, path: str, _v: Mapping[str, Any]) -> list[str]:
    if not isinstance(act, str):
        return [f"{path}: $regex needs a string, got {_json_type(act)}"]
    if re.search(m["$regex"], act) is None:
        return [f"{path}: {_fmt(act)} does not match /{m['$regex']}/"]
    return []


def _d_in(m: Mapping[str, Any], act: Any, path: str, _v: Mapping[str, Any]) -> list[str]:
    if act is not MISSING and any(_json_eq(act, c) for c in m["$in"]):
        return []
    return [f"{path}: expected one of {_fmt(m['$in'])}, got {_fmt(act)}"]


def _d_bound(m: Mapping[str, Any], act: Any, path: str, _v: Mapping[str, Any]) -> list[str]:
    key = "$gte" if "$gte" in m else "$lte"
    cmp = _order_key(act, m[key])
    if cmp is None:
        return [f"{path}: {key} cannot compare {_json_type(act)} with {_fmt(m[key])}"]
    if (key == "$gte" and cmp >= 0) or (key == "$lte" and cmp <= 0):
        return []
    return [f"{path}: expected {'>=' if key == '$gte' else '<='} {_fmt(m[key])}, got {_fmt(act)}"]


def _d_sorted_by(m: Mapping[str, Any], act: Any, path: str, _v: Mapping[str, Any]) -> list[str]:
    if not isinstance(act, list):
        return [f"{path}: $sorted_by needs an array, got {_json_type(act)}"]
    field_name, tiebreak = m["$sorted_by"], m.get("tiebreak")
    sign = -1 if m.get("order", "asc") == "desc" else 1
    for idx in range(1, len(act)):
        prev, cur = act[idx - 1], act[idx]
        pk, ck = _get_field(prev, field_name), _get_field(cur, field_name)
        if pk is MISSING or ck is MISSING:
            return [f"{path}[{idx}]: element lacks sort field {field_name!r}"]
        cmp = _order_key(pk, ck)
        if cmp is None:
            return [f"{path}[{idx}]: sort field {field_name!r} values are not comparable"]
        if cmp * sign > 0:
            return [
                f"{path}[{idx}]: not sorted by {field_name} {m.get('order', 'asc')}: "
                f"{_fmt(pk)} then {_fmt(ck)}"
            ]
        if cmp == 0 and tiebreak is not None:
            pt, ct = _get_field(prev, tiebreak), _get_field(cur, tiebreak)
            tcmp = _order_key(pt, ct)
            if tcmp is None:
                return [f"{path}[{idx}]: tiebreak field {tiebreak!r} missing or not comparable"]
            if tcmp * sign > 0:
                return [
                    f"{path}[{idx}]: tie on {field_name}={_fmt(pk)} not ordered by "
                    f"{tiebreak}: {_fmt(pt)} then {_fmt(ct)}"
                ]
    return []


def _d_set_equals(m: Mapping[str, Any], act: Any, path: str, _v: Mapping[str, Any]) -> list[str]:
    if not isinstance(act, list):
        return [f"{path}: $set_equals needs an array, got {_json_type(act)}"]
    want, got = Counter(_canon(x) for x in m["$set_equals"]), Counter(_canon(x) for x in act)
    if want == got:
        return []
    missing = sorted((want - got).elements())[:3]
    extra = sorted((got - want).elements())[:3]
    return [f"{path}: multiset differs; missing {missing}, unexpected {extra}"]


def _d_each(m: Mapping[str, Any], act: Any, path: str, v: Mapping[str, Any]) -> list[str]:
    if not isinstance(act, list):
        return [f"{path}: $each needs an array, got {_json_type(act)}"]
    out: list[str] = []
    for idx, el in enumerate(act):
        out += _match(m["$each"], el, f"{path}[{idx}]", v)
    return out


def _d_contains(m: Mapping[str, Any], act: Any, path: str, v: Mapping[str, Any]) -> list[str]:
    if not isinstance(act, list):
        return [f"{path}: $contains needs an array, got {_json_type(act)}"]
    if any(not _match(m["$contains"], el, path, v) for el in act):
        return []
    return [f"{path}: no element matches {_fmt(m['$contains'])} ({len(act)} elements)"]


def _d_var(m: Mapping[str, Any], act: Any, path: str, v: Mapping[str, Any]) -> list[str]:
    name = m["$var"]
    if name not in v:
        raise GraderError(f"matcher references unknown variable {name!r}")
    if act is not MISSING and _json_eq(v[name], act):
        return []
    return [f"{path}: expected variable {name}={_fmt(v[name])}, got {_fmt(act)}"]


def _d_not(m: Mapping[str, Any], act: Any, path: str, v: Mapping[str, Any]) -> list[str]:
    if _match(m["$not"], act, path, v):
        return []
    return [f"{path}: unexpectedly matched {_fmt(m['$not'])}"]


_DIRECTIVES: dict[str, Callable[[Mapping[str, Any], Any, str, Mapping[str, Any]], list[str]]] = {
    "$any": _d_any,
    "$type": _d_type,
    "$len": _d_len,
    "$len_min": _d_len,
    "$len_max": _d_len,
    "$regex": _d_regex,
    "$in": _d_in,
    "$gte": _d_bound,
    "$lte": _d_bound,
    "$sorted_by": _d_sorted_by,
    "$set_equals": _d_set_equals,
    "$each": _d_each,
    "$contains": _d_contains,
    "$var": _d_var,
    "$not": _d_not,
}


def _match(exp: Any, act: Any, path: str, variables: Mapping[str, Any]) -> list[str]:
    if isinstance(exp, dict):
        key = _directive_key(exp)
        if key is not None:
            return _DIRECTIVES[key](exp, act, path, variables)
        if act is MISSING:
            return [f"{path}: missing"]
        if not isinstance(act, dict):
            return [f"{path}: expected object, got {_json_type(act)}"]
        out: list[str] = []
        for k, sub in exp.items():
            out += _match(sub, act.get(k, MISSING), f"{path}.{k}", variables)
        return out
    if act is MISSING:
        return [f"{path}: missing"]
    if isinstance(exp, list):
        if not isinstance(act, list):
            return [f"{path}: expected array, got {_json_type(act)}"]
        if len(exp) != len(act):
            return [f"{path}: expected array of length {len(exp)}, got length {len(act)}"]
        out = []
        for idx, (e, a) in enumerate(zip(exp, act, strict=True)):
            out += _match(e, a, f"{path}[{idx}]", variables)
        return out
    if _json_eq(exp, act):
        return []
    return [f"{path}: expected {_fmt(exp)}, got {_fmt(act)}"]


def match(
    expected: Any,
    actual: Any,
    path: str = "$",
    variables: Mapping[str, Any] | None = None,
) -> list[str]:
    """Matcher language (bench-probe-dsl.md §3.4). Returns `[]` on match, else human-readable
    mismatch lines (`$.items[0].id: expected 5, got 6`).

    `actual` may be the module sentinel `MISSING` ("no value": an absent key / empty body);
    `{"$not": {"$any": true}}` is how a matcher asserts absence. A malformed matcher (unknown
    directive, bad argument, unknown `$var`) raises `GraderError` -- it is a definition
    error, never a mismatch.
    """
    validate_matcher(expected)
    return _match(expected, actual, path, variables or {})


# --- JSONPath-lite -------------------------------------------------------------------

_PATH_TOKEN = re.compile(r"\.([^.\[\]]+)|\[(-?\d+)\]")


def validate_json_path(path: str) -> None:
    if not isinstance(path, str) or not path.startswith("$"):
        raise GraderError(f"JSONPath-lite must start with '$', got {path!r}")
    pos, rest = 0, path[1:]
    while pos < len(rest):
        m = _PATH_TOKEN.match(rest, pos)
        if m is None:
            raise GraderError(f"bad JSONPath-lite {path!r} at offset {pos + 1}")
        pos = m.end()


def json_path_get(doc: Any, path: str) -> Any:
    """`$.a.b[0]` lookup; returns `MISSING` when the path does not exist in `doc`."""
    validate_json_path(path)
    cur = doc
    for m in _PATH_TOKEN.finditer(path[1:]):
        if m.group(1) is not None:
            if not isinstance(cur, dict) or m.group(1) not in cur:
                return MISSING
            cur = cur[m.group(1)]
        else:
            idx = int(m.group(2))
            if not isinstance(cur, list) or not -len(cur) <= idx < len(cur):
                return MISSING
            cur = cur[idx]
    return cur


# ===========================================================================
# 2. Static validation of probes.json (DSL §2-§4)
# ===========================================================================

_TEMPLATE_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_STEP_KEYS = frozenset({"op", "expect", "save", "repeat", "sleep_ms", "description"})
_PROBE_KEYS = frozenset({"id", "trap", "fresh_state", "steps", "parallel", "after", "description"})
_OP_KEYS = {
    "http": frozenset({"method", "path", "json", "headers"}),
    "jsonl": frozenset({"json"}),
    "cli": frozenset({"args", "stdin"}),
}
_EXPECT_KEYS = {
    "http": frozenset({"status", "json", "max_ms"}),
    "jsonl": frozenset({"json", "max_ms"}),
    "cli": frozenset({"exit_code", "json", "stdout_contains", "max_ms"}),
}
_PERF_KEYS = frozenset(
    {
        "sizes",
        "load",
        "mix",
        "ops_per_size",
        "repeats",
        "expected_alpha",
        "alpha_slack",
        "ref_ratio_max",
        "checks",
        "timeout_seconds",
    }
)
_MIX_KEYS = frozenset({"weight", "name", "class", "step"})


def template_refs(obj: Any) -> set[str]:
    """Every `{{name}}` referenced by a string value anywhere inside `obj`."""
    if isinstance(obj, str):
        return set(_TEMPLATE_RE.findall(obj))
    if isinstance(obj, dict):
        return set().union(*(template_refs(v) for v in obj.values())) if obj else set()
    if isinstance(obj, list):
        return set().union(*(template_refs(v) for v in obj)) if obj else set()
    return set()


def _num(v: Any) -> bool:
    return _is_number(v)


def _validate_op_expect(step: Mapping[str, Any], contract: str, where: str) -> None:
    op = step.get("op")
    if not isinstance(op, dict):
        raise _fail(where, "`op` must be an object")
    stray = set(op) - _OP_KEYS[contract]
    if stray:
        raise _fail(where, f"op keys {sorted(stray)} not valid for contract {contract!r}")
    if contract == "http":
        if not isinstance(op.get("path"), str) or not op["path"].startswith("/"):
            raise _fail(where, "http op needs a `path` string starting with '/'")
        method = op.get("method", "GET")
        if not isinstance(method, str) or method.upper() not in HTTP_METHODS:
            raise _fail(where, f"http op method must be one of {list(HTTP_METHODS)}")
        headers = op.get("headers")
        if headers is not None and not (
            isinstance(headers, dict) and all(isinstance(v, str) for v in headers.values())
        ):
            raise _fail(where, "http op `headers` must be an object of strings")
    elif contract == "jsonl":
        # Any JSON value: probes for "a line that is not a JSON object" (T-Cb8Rk2) send an
        # array or a bare string; the transport writes `json.dumps(value)` either way.
        if "json" not in op:
            raise _fail(where, "jsonl op needs a `json` value (the request line)")
    else:
        args = op.get("args", [])
        if not (isinstance(args, list) and all(isinstance(a, str) for a in args)):
            raise _fail(where, "cli op `args` must be an array of strings")
        if op.get("stdin") is not None and not isinstance(op["stdin"], str):
            raise _fail(where, "cli op `stdin` must be a string")
    expect = step.get("expect")
    if expect is None:
        return
    if not isinstance(expect, dict):
        raise _fail(where, "`expect` must be an object")
    stray = set(expect) - _EXPECT_KEYS[contract]
    if stray:
        raise _fail(where, f"expect keys {sorted(stray)} not valid for contract {contract!r}")
    status = expect.get("status")
    if status is not None and not (
        (isinstance(status, int) and not isinstance(status, bool))
        or (isinstance(status, list) and status and all(isinstance(s, int) for s in status))
    ):
        raise _fail(where, "expect.status must be an int or a non-empty list of ints")
    if "exit_code" in expect and not isinstance(expect["exit_code"], int):
        raise _fail(where, "expect.exit_code must be an int")
    if "stdout_contains" in expect and not isinstance(expect["stdout_contains"], str):
        raise _fail(where, "expect.stdout_contains must be a string")
    if "max_ms" in expect and not (_num(expect["max_ms"]) and expect["max_ms"] > 0):
        raise _fail(where, "expect.max_ms must be a positive number")


def _validate_step(
    step: Any, contract: str, where: str, scope: set[str], *, templated_repeat: bool = False
) -> set[str]:
    """Validate one step against the variables visible so far; returns the vars it saves."""
    if not isinstance(step, dict):
        raise _fail(where, "step must be an object")
    stray = set(step) - _STEP_KEYS
    if stray:
        raise _fail(where, f"unknown step keys {sorted(stray)}")
    _validate_op_expect(step, contract, where)
    try:
        refs = template_refs(step["op"]) | template_refs(step.get("expect"))
        refs |= validate_matcher((step.get("expect") or {}).get("json"))
    except GraderError as exc:
        raise _fail(where, str(exc)) from exc
    unknown = refs - scope - BUILTIN_VARS
    if unknown:
        raise _fail(where, f"unknown variable(s) {sorted(unknown)} (not saved by an earlier step)")
    repeat = step.get("repeat")
    if repeat is not None and not (
        (isinstance(repeat, int) and not isinstance(repeat, bool) and repeat >= 1)
        or (templated_repeat and isinstance(repeat, str))
    ):
        raise _fail(where, "`repeat` must be an integer >= 1")
    sleep = step.get("sleep_ms", 0)
    if not (_num(sleep) and sleep >= 0):
        raise _fail(where, "`sleep_ms` must be a number >= 0")
    save = step.get("save", {})
    if not isinstance(save, dict):
        raise _fail(where, "`save` must be an object of {var: JSONPath}")
    for var, jp in save.items():
        if not _IDENT_RE.match(var) or var in BUILTIN_VARS:
            raise _fail(
                where, f"save variable {var!r} must be an identifier and not {BUILTIN_VARS}"
            )
        try:
            validate_json_path(jp)
        except GraderError as exc:
            raise _fail(where, f"save {var}: {exc}") from exc
    return set(save)


def _validate_steps(
    steps: Any, contract: str, where: str, scope: set[str], *, require_nonempty: bool = True
) -> set[str]:
    """Validate a step list in order; returns the scope after it (scope + all saves)."""
    if not isinstance(steps, list) or (require_nonempty and not steps):
        raise _fail(where, "must be a non-empty array of steps")
    cur = set(scope)
    for idx, step in enumerate(steps):
        cur |= _validate_step(step, contract, f"{where}[{idx}]", cur)
    return cur


def _validate_probe(probe: Any, contract: str, group: str, where: str) -> str:
    if not isinstance(probe, dict):
        raise _fail(where, "probe must be an object")
    stray = set(probe) - _PROBE_KEYS
    if stray:
        raise _fail(where, f"unknown probe keys {sorted(stray)}")
    pid = probe.get("id")
    if not isinstance(pid, str) or not pid:
        raise _fail(where, "probe needs a non-empty string `id`")
    where = f"{where} (id {pid!r})"
    if probe.get("trap") is not None and group != "trap":
        raise _fail(where, "`trap` is only valid in the trap group")
    if "fresh_state" in probe and not isinstance(probe["fresh_state"], bool):
        raise _fail(where, "`fresh_state` must be a boolean")
    scope = _validate_steps(probe.get("steps"), contract, f"{where}.steps", set())
    parallel = probe.get("parallel")
    if parallel is not None:
        if not isinstance(parallel, list) or not parallel:
            raise _fail(where, "`parallel` must be a non-empty array of step lists")
        for k, lst in enumerate(parallel):
            _validate_steps(lst, contract, f"{where}.parallel[{k}]", scope)
    if probe.get("after") is not None:
        # `after` sees variables from `steps` only (parallel captures are not merged).
        _validate_steps(probe["after"], contract, f"{where}.after", scope)
    return pid


def _validate_perf(perf: Any, contract: str) -> dict[str, Any]:
    where = "groups.perf"
    if not isinstance(perf, dict):
        raise _fail(where, "must be an object")
    stray = set(perf) - _PERF_KEYS
    if stray:
        raise _fail(where, f"unknown perf keys {sorted(stray)}")
    sizes = perf.get("sizes")
    if not (
        isinstance(sizes, list)
        and sizes
        and all(isinstance(n, int) and not isinstance(n, bool) and n >= 1 for n in sizes)
    ):
        raise _fail(where, "`sizes` must be a non-empty array of positive integers")
    checks = perf.get("checks", list(PERF_CHECKS))
    if not (isinstance(checks, list) and set(checks) <= set(PERF_CHECKS)):
        raise _fail(where, f"`checks` must be a subset of {list(PERF_CHECKS)}")
    if "alpha" in checks and len(set(sizes)) < 2:
        raise _fail(where, "the `alpha` check needs at least two distinct sizes")
    for key, lo in (("ops_per_size", 1), ("repeats", 1)):
        v = perf.get(key, 1)
        if not (isinstance(v, int) and not isinstance(v, bool) and v >= lo):
            raise _fail(where, f"`{key}` must be an integer >= {lo}")
    for key in ("alpha_slack",):
        if not (_num(perf.get(key, 0)) and perf.get(key, 0) >= 0):
            raise _fail(where, f"`{key}` must be a number >= 0")
    if not (_num(perf.get("ref_ratio_max", 1)) and perf.get("ref_ratio_max", 1) > 0):
        raise _fail(where, "`ref_ratio_max` must be a positive number")
    expected = perf.get("expected_alpha", {})
    if not (isinstance(expected, dict) and all(_num(v) for v in expected.values())):
        raise _fail(where, "`expected_alpha` must map op class -> number")
    scope = {"N"}
    load_scope = scope | _validate_step(
        perf.get("load"), contract, f"{where}.load", scope, templated_repeat=True
    )
    mix = perf.get("mix")
    if not isinstance(mix, list) or not mix:
        raise _fail(where, "`mix` must be a non-empty array")
    names: set[str] = set()
    total = 0.0
    for idx, entry in enumerate(mix):
        w = f"{where}.mix[{idx}]"
        if not isinstance(entry, dict) or set(entry) != _MIX_KEYS:
            raise _fail(w, f"mix entries need exactly the keys {sorted(_MIX_KEYS)}")
        if not (_num(entry["weight"]) and entry["weight"] > 0):
            raise _fail(w, "`weight` must be a positive number")
        if not isinstance(entry["name"], str) or entry["name"] in names:
            raise _fail(w, "`name` must be a unique string")
        if not isinstance(entry["class"], str) or not entry["class"]:
            raise _fail(w, "`class` must be a non-empty string")
        names.add(entry["name"])
        total += float(entry["weight"])
        _validate_step(entry["step"], contract, f"{w}.step", load_scope)
    if abs(total - 1.0) > MIX_WEIGHT_TOLERANCE:
        raise _fail(where, f"mix weights must sum to 1.0, got {total:g}")
    classes = {e["class"] for e in mix}
    if "alpha" in checks and not set(expected) <= classes:
        raise _fail(where, f"expected_alpha classes {sorted(set(expected) - classes)} not in mix")
    return perf


def validate_probe_spec(spec: Any) -> dict[str, Any]:
    """Validate a parsed probes.json; return it normalised (defaults filled in)."""
    if not isinstance(spec, dict):
        raise _fail("probes.json", "top level must be an object")
    contract = spec.get("contract")
    if contract not in CONTRACTS:
        raise _fail("contract", f"must be one of {list(CONTRACTS)}, got {contract!r}")
    startup = spec.get("startup_timeout_seconds", DEFAULT_STARTUP_TIMEOUT)
    if not (_num(startup) and startup > 0):
        raise _fail("startup_timeout_seconds", "must be a positive number")
    groups = spec.get("groups")
    if not isinstance(groups, dict) or set(groups) - set(GROUPS):
        raise _fail("groups", f"must be an object with keys from {list(GROUPS)}")
    ids: set[str] = set()
    norm_groups: dict[str, Any] = {}
    for group in ("core", "trap"):
        probes = groups.get(group, [])
        if not isinstance(probes, list):
            raise _fail(f"groups.{group}", "must be an array")
        for idx, probe in enumerate(probes):
            pid = _validate_probe(probe, contract, group, f"groups.{group}[{idx}]")
            if pid in ids:
                raise _fail(f"groups.{group}[{idx}]", f"duplicate probe id {pid!r}")
            ids.add(pid)
        norm_groups[group] = probes
    if not norm_groups["core"]:
        raise _fail("groups.core", "must contain at least one probe")
    norm_groups["perf"] = (
        _validate_perf(groups["perf"], contract) if groups.get("perf") is not None else None
    )
    weights = {**DEFAULT_WEIGHTS, **(spec.get("weights") or {})}
    if not all(_num(weights.get(g)) and weights[g] >= 0 for g in GROUPS) or set(weights) - set(
        GROUPS
    ):
        raise _fail("weights", f"must map {list(GROUPS)} to numbers >= 0")
    threshold = spec.get("pass_threshold")
    if threshold is not None and not (_num(threshold) and 0 <= threshold <= 1):
        raise _fail("pass_threshold", "must be a number in [0, 1]")
    return {
        "contract": contract,
        "startup_timeout_seconds": float(startup),
        "groups": norm_groups,
        "weights": {g: float(weights[g]) for g in GROUPS},
        "pass_threshold": None if threshold is None else float(threshold),
    }


def load_probe_spec(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise GraderError(f"probes file not found: {path}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GraderError(f"probes file {path} is unreadable / not valid JSON: {exc}") from exc
    return validate_probe_spec(raw)


# ===========================================================================
# 3. Execution
# ===========================================================================


@dataclass(frozen=True)
class Limits:
    probe_timeout_seconds: float = DEFAULT_PROBE_TIMEOUT_SECONDS
    request_timeout_seconds: float = DEFAULT_REQUEST_TIMEOUT_SECONDS
    perf_timeout_seconds: float = DEFAULT_PERF_TIMEOUT_SECONDS

    @classmethod
    def parse(cls, raw: Mapping[str, Any] | None, perf_default: float | None = None) -> Limits:
        raw = dict(raw or {})
        names = {"probe_timeout_seconds", "request_timeout_seconds", "perf_timeout_seconds"}
        if set(raw) - names:
            raise GraderError(f"unknown grader.limits keys {sorted(set(raw) - names)}")
        merged: dict[str, Any] = {
            "perf_timeout_seconds": perf_default or DEFAULT_PERF_TIMEOUT_SECONDS,
            **raw,
        }
        if not all(_num(v) and v > 0 for v in merged.values()):
            raise GraderError("grader.limits values must be positive numbers")
        return cls(**{k: float(v) for k, v in merged.items()})


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _stringify(v: Any) -> str:
    if isinstance(v, str):
        return v
    return json.dumps(v, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class Templater:
    """`{{var}}` / `{{i}}` / `{{rand}}` / `{{now_iso}}` rendering (DSL §3.1). RNG and clock
    are injected so tests (and a seeded grader) are deterministic."""

    def __init__(self, rng: random.Random, clock: Callable[[], datetime] = _utc_now) -> None:
        self._rng = rng
        self._clock = clock

    def render(self, obj: Any, variables: Mapping[str, Any], i: int = 0) -> Any:
        if isinstance(obj, str):
            return self._render_str(obj, variables, i)
        if isinstance(obj, dict):
            return {k: self.render(v, variables, i) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self.render(v, variables, i) for v in obj]
        return obj

    def _builtin(self, name: str, i: int) -> str | None:
        if name == "i":
            return str(i)
        if name == "rand":
            return f"{self._rng.getrandbits(32):08x}"
        if name == "now_iso":
            return self._clock().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        return None

    def _render_str(self, s: str, variables: Mapping[str, Any], i: int) -> Any:
        whole = _TEMPLATE_RE.fullmatch(s)
        if whole and whole.group(1) in variables and whole.group(1) not in BUILTIN_VARS:
            return variables[whole.group(1)]  # typed substitution

        def sub(m: re.Match[str]) -> str:
            name = m.group(1)
            builtin = self._builtin(name, i)
            if builtin is not None:
                return builtin
            if name in variables:
                return _stringify(variables[name])
            raise GraderError(f"unknown template variable {{{{{name}}}}} (not saved by a step)")

        return _TEMPLATE_RE.sub(sub, s)


@dataclass
class StepOutcome:
    """What one executed step observed."""

    ms: float = 0.0
    status: int | None = None
    exit_code: int | None = None
    body: Any = MISSING  # parsed JSON (http body / jsonl line / cli stdout) or MISSING
    text: str = ""  # raw body / line / stdout


def _parse_json(text: str) -> Any:
    if not text.strip():
        return MISSING
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return MISSING


class Transport(ABC):
    @abstractmethod
    def send(self, op: Mapping[str, Any], timeout: float) -> StepOutcome:
        """Execute one rendered op. Raises `ArtefactFailure` (incl. `ArtefactTimeout`)."""


class HttpTransport(Transport):
    """One short-lived connection per request: no stale keep-alive state, thread-safe, and
    the same cost model for the artefact and the reference.

    T-Cb8Rk2: at perf `scale >= 2` a grading makes ~30k requests. Through `docker-proxy`
    (the published port) every connection left a TIME_WAIT socket on the proxy -> container
    leg, whose container IP is reused across restarts, and the ~28k-th connection was reset
    once the ephemeral range ran out -- even the reference "failed". So when the sandbox
    knows the container's own address (`Handle.direct_host`) we connect there, bypassing
    the proxy, and every socket is closed abortively (`SO_LINGER` 0) so no TIME_WAIT is left
    on either leg. Keep-alive was rejected: Python servers' Nagle + delayed-ACK put a
    ~40 ms floor under every reused-connection request, which would distort perf timings.
    """

    def __init__(self, handle: Handle) -> None:
        assert handle.port is not None
        if handle.direct_host is not None and handle.direct_port is not None:
            self._host, self._port = handle.direct_host, handle.direct_port
        else:
            self._host, self._port = "127.0.0.1", handle.port

    def send(self, op: Mapping[str, Any], timeout: float) -> StepOutcome:
        method = str(op.get("method", "GET")).upper()
        headers = dict(op.get("headers") or {})
        body = None
        if op.get("json") is not None:
            body = json.dumps(op["json"]).encode("utf-8")
            headers.setdefault("Content-Type", "application/json")
        conn = http.client.HTTPConnection(self._host, self._port, timeout=timeout)
        t0 = time.perf_counter()
        try:
            conn.request(method, op["path"], body=body, headers=headers)
            resp = conn.getresponse()
            raw = resp.read()
        except TimeoutError as exc:
            raise ArtefactTimeout(f"{method} {op['path']} timed out after {timeout:g}s") from exc
        except (OSError, http.client.HTTPException) as exc:
            raise ArtefactFailure(f"{method} {op['path']} failed: {exc!r}") from exc
        finally:
            _abortive_close(conn)
        ms = (time.perf_counter() - t0) * 1000.0
        text = raw.decode("utf-8", errors="replace")
        return StepOutcome(ms=ms, status=resp.status, body=_parse_json(text), text=text)


_LINGER_ABORT = struct.pack("ii", 1, 0)


def _abortive_close(conn: http.client.HTTPConnection) -> None:
    """Close with RST instead of FIN so the client side keeps no TIME_WAIT (see
    `HttpTransport`). The response has been read in full by then."""
    sock = conn.sock
    if sock is not None:
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, _LINGER_ABORT)
        except OSError:
            pass
    conn.close()


class JsonlTransport(Transport):
    def __init__(self, handle: JsonlHandle) -> None:
        self._handle = handle

    def send(self, op: Mapping[str, Any], timeout: float) -> StepOutcome:
        t0 = time.perf_counter()
        line = self._handle.request(json.dumps(op["json"]), timeout)
        ms = (time.perf_counter() - t0) * 1000.0
        return StepOutcome(ms=ms, body=_parse_json(line), text=line)


class CliTransport(Transport):
    def __init__(self, sandbox: Sandbox, run_dir: Path, env: Mapping[str, str]) -> None:
        self._sandbox = sandbox
        self._run_dir = run_dir
        self._env = dict(env)

    def send(self, op: Mapping[str, Any], timeout: float) -> StepOutcome:
        t0 = time.perf_counter()
        rc, out, err = self._sandbox.run_cli(
            self._run_dir, list(op.get("args", [])), op.get("stdin"), self._env, timeout
        )
        ms = (time.perf_counter() - t0) * 1000.0
        text = out if not err.strip() else out + "\n[stderr] " + err[-BODY_PREVIEW_CHARS:]
        return StepOutcome(ms=ms, exit_code=rc, body=_parse_json(out), text=text)


class Session:
    """One artefact's lifecycle. `restart()` = fresh copy of `source_dir` in a new run dir
    + (http/jsonl) a freshly started process; `cli` has no process, only the run dir."""

    def __init__(
        self,
        sandbox: Sandbox,
        contract: str,
        source_dir: Path,
        work_root: Path,
        *,
        env_for: Callable[[Path], Mapping[str, str]] | None = None,
        prepare: Callable[[Path], None] | None = None,
    ) -> None:
        self._sandbox = sandbox
        self._contract = contract
        self._source = source_dir
        self._work_root = work_root
        self._env_for = env_for
        self._prepare = prepare
        self.transport: Transport | None = None
        self._handle: Handle | None = None
        self._run_dir: Path | None = None
        self.ever_started = False
        self.start_failures = 0

    def _snapshot(self) -> Path:
        run_dir = Path(tempfile.mkdtemp(prefix="run-", dir=self._work_root))
        try:
            shutil.copytree(
                self._source,
                run_dir,
                symlinks=True,
                dirs_exist_ok=True,
                ignore=shutil.ignore_patterns(*COPY_IGNORE),
            )
            if self._prepare is not None:
                self._prepare(run_dir)
        except (OSError, shutil.Error) as exc:
            shutil.rmtree(run_dir, ignore_errors=True)
            raise GraderError(f"cannot snapshot {self._source} for grading: {exc}") from exc
        return run_dir

    def restart(self) -> Transport:
        self.close()
        run_dir = self._snapshot()
        self._run_dir = run_dir
        env = {**BASE_ARTEFACT_ENV, **(self._env_for(run_dir) if self._env_for else {})}
        try:
            if self._contract == "http":
                self._handle = self._sandbox.start_http(run_dir, env)
                self.transport = HttpTransport(self._handle)
            elif self._contract == "jsonl":
                jhandle = self._sandbox.start_jsonl(run_dir, env)
                self._handle = jhandle
                self.transport = JsonlTransport(jhandle)
            else:
                if not (run_dir / "run.sh").is_file():
                    raise ArtefactStartError("run.sh not found in the artefact directory")
                self.transport = CliTransport(self._sandbox, run_dir, env)
        except ArtefactFailure:
            self.start_failures += 1
            self.close()
            raise
        self.ever_started = True
        return self.transport

    def ensure(self, fresh: bool) -> Transport:
        if fresh or self.transport is None:
            return self.restart()
        return self.transport

    def close(self) -> None:
        if self._handle is not None:
            self._sandbox.stop(self._handle)
            self._handle = None
        self.transport = None
        if self._run_dir is not None:
            shutil.rmtree(self._run_dir, ignore_errors=True)
            self._run_dir = None


@dataclass
class ProbeOutcome:
    id: str
    group: str
    trap: str | None
    passed: bool
    mismatches: list[str]
    ms: float

    def as_detail(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "group": self.group,
            "trap": self.trap,
            "passed": self.passed,
            "mismatches": self.mismatches,
            "ms": round(self.ms, 1),
        }


@dataclass
class StepResult:
    mismatches: list[str]
    outcome: StepOutcome | None = None
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return not self.mismatches


def _describe(op: Mapping[str, Any], contract: str) -> str:
    if contract == "http":
        return f"{str(op.get('method', 'GET')).upper()} {op.get('path')}"
    if contract == "jsonl":
        line = op.get("json")
        if isinstance(line, dict):
            return f"op={line.get('op')}"
        return f"line={json.dumps(line)[:40]}"  # a deliberately non-object request line
    return "run.sh " + " ".join(str(a) for a in op.get("args", []))


def _clip(s: str) -> str:
    return s if len(s) <= MISMATCH_MAX_CHARS else s[: MISMATCH_MAX_CHARS - 3] + "..."


class ProbeRunner:
    """Executes probe steps (DSL §3) against a `Session`."""

    def __init__(
        self, contract: str, session: Session, templater: Templater, limits: Limits
    ) -> None:
        self.contract = contract
        self.session = session
        self.templater = templater
        self.limits = limits

    # -- one step ---------------------------------------------------------------------

    def execute_step(
        self,
        step: Mapping[str, Any],
        transport: Transport,
        variables: dict[str, Any],
        i: int,
        deadline: float,
        where: str,
    ) -> StepResult:
        op = self.templater.render(step["op"], variables, i)
        expect = self.templater.render(step.get("expect"), variables, i)
        sleep_ms = step.get("sleep_ms", 0)
        if sleep_ms:
            time.sleep(sleep_ms / 1000.0)
        timeout = max(0.001, min(self.limits.request_timeout_seconds, deadline - time.monotonic()))
        label = f"{where} {_describe(op, self.contract)}"
        try:
            outcome = transport.send(op, timeout)
        except ArtefactTimeout as exc:
            return StepResult([_clip(f"{label}: {exc}")], timed_out=True)
        except ArtefactFailure as exc:
            return StepResult([_clip(f"{label}: {exc}")])
        problems = self._check_expect(expect, outcome, variables)
        if not problems:
            for var, jp in (step.get("save") or {}).items():
                value = json_path_get(outcome.body, jp)
                if value is MISSING:
                    problems.append(f"save {var}: {jp} not found in response body")
                else:
                    variables[var] = value
        return StepResult([_clip(f"{label}: {p}") for p in problems], outcome)

    def _check_expect(
        self, expect: Mapping[str, Any] | None, out: StepOutcome, variables: Mapping[str, Any]
    ) -> list[str]:
        if not expect:
            return []
        problems: list[str] = []
        if "status" in expect:
            want = expect["status"] if isinstance(expect["status"], list) else [expect["status"]]
            if out.status not in want:
                preview = out.text[:BODY_PREVIEW_CHARS]
                problems.append(
                    f"status: expected {expect['status']}, got {out.status} ({preview})"
                )
        if "exit_code" in expect and out.exit_code != expect["exit_code"]:
            problems.append(
                f"exit_code: expected {expect['exit_code']}, got {out.exit_code} "
                f"({out.text[-BODY_PREVIEW_CHARS:]})"
            )
        if "stdout_contains" in expect and expect["stdout_contains"] not in out.text:
            problems.append(f"stdout does not contain {expect['stdout_contains']!r}")
        if "json" in expect:
            found = _match(expect["json"], out.body, "$", variables)
            if out.body is MISSING and found == ["$: missing"]:
                found = [f"$: response has no JSON body (text: {out.text[:BODY_PREVIEW_CHARS]!r})"]
            problems += found
        if "max_ms" in expect and out.ms > expect["max_ms"]:
            problems.append(f"latency {out.ms:.1f}ms exceeds max_ms {expect['max_ms']}")
        return problems

    # -- step lists / probes ----------------------------------------------------------

    def _run_steps(
        self,
        steps: Sequence[Mapping[str, Any]],
        transport: Transport,
        variables: dict[str, Any],
        deadline: float,
        label: str,
        mismatches: list[str],
    ) -> bool:
        for idx, step in enumerate(steps):
            for i in range(step.get("repeat", 1)):
                if time.monotonic() > deadline:
                    mismatches.append(f"{label}[{idx}]: probe exceeded its time budget")
                    return False
                res = self.execute_step(step, transport, variables, i, deadline, f"{label}[{idx}]")
                if not res.ok:
                    mismatches.extend(res.mismatches)
                    return False
        return True

    def _run_parallel(
        self,
        lists: Sequence[Sequence[Mapping[str, Any]]],
        transport: Transport,
        variables: Mapping[str, Any],
        deadline: float,
        mismatches: list[str],
    ) -> bool:
        barrier = threading.Barrier(len(lists))

        def work(k: int) -> list[str]:
            local: dict[str, Any] = dict(variables)  # captures stay thread-local by design
            found: list[str] = []
            try:
                barrier.wait(timeout=PARALLEL_BARRIER_TIMEOUT_SECONDS)
            except threading.BrokenBarrierError:
                pass  # a sibling died early; run anyway
            self._run_steps(lists[k], transport, local, deadline, f"parallel[{k}]", found)
            return found

        with ThreadPoolExecutor(max_workers=len(lists)) as pool:
            results = [f.result() for f in [pool.submit(work, k) for k in range(len(lists))]]
        for found in results:
            mismatches.extend(found)
        return not any(results)

    def run_probe(self, probe: Mapping[str, Any], group: str) -> ProbeOutcome:
        t0 = time.perf_counter()
        deadline = time.monotonic() + self.limits.probe_timeout_seconds
        mismatches: list[str] = []
        try:
            transport = self.session.ensure(probe.get("fresh_state", True))
        except ArtefactFailure as exc:
            mismatches.append(_clip(f"artefact failed to start: {exc}"))
            return self._outcome(probe, group, t0, mismatches)
        variables: dict[str, Any] = {}
        ok = self._run_steps(probe["steps"], transport, variables, deadline, "steps", mismatches)
        if ok and probe.get("parallel"):
            ok = self._run_parallel(probe["parallel"], transport, variables, deadline, mismatches)
        if ok and probe.get("after"):
            self._run_steps(probe["after"], transport, variables, deadline, "after", mismatches)
        return self._outcome(probe, group, t0, mismatches)

    @staticmethod
    def _outcome(
        probe: Mapping[str, Any], group: str, t0: float, mismatches: list[str]
    ) -> ProbeOutcome:
        return ProbeOutcome(
            id=probe["id"],
            group=group,
            trap=probe.get("trap"),
            passed=not mismatches,
            mismatches=mismatches[:MAX_MISMATCHES_PER_PROBE],
            ms=(time.perf_counter() - t0) * 1000.0,
        )

    def run_groups(self, spec: Mapping[str, Any]) -> list[ProbeOutcome]:
        """All core then trap probes. If the artefact has never once become ready, the first
        start failure short-circuits the rest (no N x startup-timeout waits)."""
        outcomes: list[ProbeOutcome] = []
        for group in ("core", "trap"):
            for probe in spec["groups"][group]:
                if self.session.start_failures and not self.session.ever_started:
                    outcomes.append(
                        ProbeOutcome(
                            probe["id"],
                            group,
                            probe.get("trap"),
                            False,
                            ["artefact never became ready (see the first failing probe)"],
                            0.0,
                        )
                    )
                    continue
                outcomes.append(self.run_probe(probe, group))
        return outcomes


# ===========================================================================
# 4. Perf (DSL §4, HLD §5.2)
# ===========================================================================


def percentile(samples: Sequence[float], q: float) -> float:
    """Nearest-rank percentile (`q` in [0, 1]) -- deterministic, no interpolation."""
    ordered = sorted(samples)
    rank = max(1, math.ceil(q * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


def fit_alpha(points: Sequence[tuple[int, float]]) -> float | None:
    """Least-squares slope of `log(p95)` against `log(N)`; None with < 2 distinct sizes."""
    xs = [math.log(n) for n, _ in points]
    if len(set(xs)) < 2:
        return None
    ys = [math.log(max(p, PERF_LATENCY_FLOOR_MS)) for _, p in points]
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True)) / sxx


@dataclass
class PerfRun:
    """Medians over repeats for one implementation."""

    ops: dict[int, dict[str, dict[str, float]]] = field(default_factory=dict)  # N -> op -> stats
    class_p95: dict[str, dict[int, float]] = field(default_factory=dict)  # class -> N -> p95
    overall_p95: dict[int, float] = field(default_factory=dict)  # N -> pooled p95
    failed_ops: dict[str, int] = field(default_factory=dict)
    timed_out: bool = False
    error: str | None = None


def measure_perf(
    spec: Mapping[str, Any],
    runner: ProbeRunner,
    *,
    seed: str,
    budget_seconds: float,
) -> PerfRun:
    """Load N records, run the weighted mix, repeat; medians across repeats (DSL §4).
    Never raises for artefact misbehaviour: that lands in `PerfRun.error` / `timed_out`."""
    run = PerfRun()
    started = time.monotonic()
    deadline = started + budget_seconds
    mix = spec["mix"]
    cumulative: list[float] = []
    acc = 0.0
    for entry in mix:
        acc += float(entry["weight"])
        cumulative.append(acc)
    per_op: dict[int, dict[str, list[tuple[float, float]]]] = {}
    per_class: dict[int, dict[str, list[float]]] = {}
    per_all: dict[int, list[float]] = {}
    for n in spec["sizes"]:
        for rep in range(spec.get("repeats", 1)):
            rng = random.Random(f"perf:{seed}:{n}:{rep}")  # str seed: stable across processes
            try:
                transport = runner.session.restart()
            except ArtefactFailure as exc:
                run.error = _clip(f"artefact failed to start for size {n}: {exc}")
                return run
            variables: dict[str, Any] = {"N": n}
            load = {**spec["load"], "repeat": n}
            load_msgs: list[str] = []
            ok = runner._run_steps([load], transport, variables, deadline, "perf.load", load_msgs)
            if not ok:
                run.timed_out = any("timed out" in m or "time budget" in m for m in load_msgs)
                run.error = _clip(f"load failed at size {n}: {load_msgs[0] if load_msgs else '?'}")
                return run
            by_op: dict[str, list[float]] = {}
            by_class: dict[str, list[float]] = {}
            pooled: list[float] = []
            for _ in range(spec.get("ops_per_size", 1)):
                if time.monotonic() > deadline:
                    run.timed_out = True
                    run.error = f"perf phase exceeded its {budget_seconds:g}s budget"
                    return run
                pick = rng.random() * acc
                entry = mix[next((k for k, c in enumerate(cumulative) if pick < c), len(mix) - 1)]
                res = runner.execute_step(
                    entry["step"], transport, variables, rng.randrange(n), deadline, "perf.mix"
                )
                if res.timed_out:
                    run.timed_out = True
                    run.error = res.mismatches[0]
                    return run
                if not res.ok or res.outcome is None:
                    run.failed_ops[entry["name"]] = run.failed_ops.get(entry["name"], 0) + 1
                    continue
                by_op.setdefault(entry["name"], []).append(res.outcome.ms)
                by_class.setdefault(entry["class"], []).append(res.outcome.ms)
                pooled.append(res.outcome.ms)
            for name, samples in by_op.items():
                per_op.setdefault(n, {}).setdefault(name, []).append(
                    (percentile(samples, 0.5), percentile(samples, 0.95))
                )
            for cls, samples in by_class.items():
                per_class.setdefault(n, {}).setdefault(cls, []).append(percentile(samples, 0.95))
            if pooled:
                per_all.setdefault(n, []).append(percentile(pooled, 0.95))
    for n, ops in per_op.items():
        run.ops[n] = {
            name: {
                "p50_ms": statistics.median(p[0] for p in pairs),
                "p95_ms": statistics.median(p[1] for p in pairs),
            }
            for name, pairs in ops.items()
        }
    for n, classes in per_class.items():
        for cls, p95s in classes.items():
            run.class_p95.setdefault(cls, {})[n] = statistics.median(p95s)
    run.overall_p95 = {n: statistics.median(v) for n, v in per_all.items()}
    return run


def evaluate_perf(
    spec: Mapping[str, Any],
    art: PerfRun,
    ref: PerfRun | None,
    *,
    enforce_ref: bool,
) -> tuple[dict[str, Any], int, int]:
    """-> (`detail.perf`, checks passed, checks counted). Each expected-alpha class is one
    check; `ref_ratio` one (only when enforced); `timeout` one."""
    checks = spec.get("checks", list(PERF_CHECKS))
    slack = float(spec.get("alpha_slack", DEFAULT_ALPHA_SLACK))
    ref_max = float(spec.get("ref_ratio_max", DEFAULT_REF_RATIO_MAX))
    expected = spec.get("expected_alpha", {})
    largest = max(spec["sizes"])
    classes: dict[str, Any] = {}
    passed = total = 0
    for cls in sorted({e["class"] for e in spec["mix"]}):
        points = sorted(art.class_p95.get(cls, {}).items())
        alpha = fit_alpha(points)
        want = expected.get(cls)
        alpha_pass = None if want is None or alpha is None else alpha <= want + slack
        classes[cls] = {
            "alpha": None if alpha is None else round(alpha, 4),
            "expected_alpha": want,
            "alpha_pass": alpha_pass,
        }
        if "alpha" in checks and want is not None:
            total += 1
            passed += 1 if alpha_pass else 0
    ref_ratio: float | None = None
    ref_pass: bool | None = None
    if ref is not None and largest in art.overall_p95 and largest in ref.overall_p95:
        ref_ratio = art.overall_p95[largest] / max(ref.overall_p95[largest], PERF_LATENCY_FLOOR_MS)
    if "ref_ratio" in checks and enforce_ref:
        total += 1
        ref_pass = ref_ratio is not None and ref_ratio <= ref_max
        passed += 1 if ref_pass else 0
    if "timeout" in checks:
        total += 1
        passed += 0 if (art.timed_out or art.error) else 1
    detail: dict[str, Any] = {
        "sizes": {
            str(n): {
                op: {k: round(v, 3) for k, v in stats.items()} for op, stats in sorted(ops.items())
            }
            for n, ops in sorted(art.ops.items())
        },
        "classes": classes,
        "ref_ratio_p95": None if ref_ratio is None else round(ref_ratio, 3),
        "ref_ratio_max": ref_max,
        "ref_ratio_enforced": enforce_ref,
        "ref_ratio_pass": ref_pass,
        "p95_ms_largest": None
        if largest not in art.overall_p95
        else round(art.overall_p95[largest], 3),
        "ref_p95_ms_largest": None
        if ref is None or largest not in ref.overall_p95
        else round(ref.overall_p95[largest], 3),
        "timed_out": art.timed_out,
        "failed_ops": dict(art.failed_ops),
        "error": art.error,
    }
    return detail, passed, total


# ===========================================================================
# 5. The grader
# ===========================================================================


def _group_fraction(passed: int, total: int) -> float:
    return passed / total if total else 0.0


def weighted_score(
    counts: Mapping[str, tuple[int, int]], weights: Mapping[str, float]
) -> tuple[float, dict[str, Any]]:
    """Σ weight x fraction over groups that have something to count; weights are
    renormalised over those present, so a suite without `perf` can still reach 1.0."""
    present = {g: c for g, c in counts.items() if c[1] > 0 and weights.get(g, 0) > 0}
    norm = sum(weights[g] for g in present)
    breakdown: dict[str, Any] = {}
    score = 0.0
    for g in GROUPS:
        passed, total = counts.get(g, (0, 0))
        frac = _group_fraction(passed, total)
        eff = weights[g] / norm if g in present and norm else 0.0
        score += eff * frac
        breakdown[g] = {
            "weight": weights[g],
            "effective_weight": round(eff, 6),
            "passed": passed,
            "total": total,
            "fraction": round(frac, 6),
        }
    return min(1.0, max(0.0, round(score, 6))), breakdown


def _read_json_file(path: Path, what: str) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GraderError(f"{what} {path} unreadable / not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise GraderError(f"{what} {path} must be a JSON object")
    return data


class BlackboxGrader(Grader):
    """cfg (`GraderConfig` + `model_extra`): `probes` (file in grading_dir, default
    probes.json), `sandbox` (SandboxConfig keys), `limits` (probe/request/perf timeouts),
    `weights`, `pass_threshold` (cfg beats probes.json), `language`/`contract` fallbacks."""

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
        seed: int | str | None = None,
    ) -> None:
        self._clock = clock or _utc_now
        self._seed = seed

    def _grade(self, cfg: GraderConfig, ctx: GraderContext) -> GradeResult:
        extra = dict(cfg.model_extra or {})
        grading_dir = self._grading_dir(ctx)
        probes_path = self._resolve_in(grading_dir, extra.get("probes", DEFAULT_PROBES_FILE))
        spec = load_probe_spec(probes_path)
        contract = spec["contract"]
        if extra.get("contract") not in (None, contract):
            raise GraderError(
                f"grader.contract {extra['contract']!r} disagrees with probes.json {contract!r}"
            )
        instance_path = grading_dir / INSTANCE_FILE
        instance = _read_json_file(instance_path, "instance") if instance_path.is_file() else {}
        sandbox_raw = dict(extra.get("sandbox") or {})
        language = instance.get("language") or extra.get("language") or sandbox_raw.get("language")
        if not language:
            raise GraderError("blackbox grader: no language (instance.json / cfg.language)")
        ref_info = instance.get("reference") or {}
        ref_language = ref_info.get("language", DEFAULT_REFERENCE_LANGUAGE)
        reference_dir = grading_dir / REFERENCE_DIRNAME
        if not reference_dir.is_dir():
            raise GraderError(f"reference implementation missing: {reference_dir}")

        sandbox_raw.setdefault("startup_timeout_seconds", spec["startup_timeout_seconds"])
        sandbox_cfg = SandboxConfig.parse({**sandbox_raw, "language": language})
        limits = Limits.parse(extra.get("limits"), perf_default=cfg.timeout_seconds)
        weights = {**spec["weights"], **(extra.get("weights") or {})}
        threshold = (
            cfg.pass_threshold
            if cfg.pass_threshold is not None
            else (
                spec["pass_threshold"]
                if spec["pass_threshold"] is not None
                else DEFAULT_PASS_THRESHOLD
            )
        )
        seed = str(self._seed if self._seed is not None else instance.get("seed", 0))

        sandbox = create_sandbox(sandbox_cfg, language)
        ref_sandbox = create_sandbox(
            sandbox_cfg.model_copy(
                update={
                    "type": sandbox.kind,
                    "language": ref_language,
                    "image": None,
                    "stack": None,
                }
            ),
            ref_language,
        )
        work_root = Path(tempfile.mkdtemp(prefix="blackbox-", dir=grading_dir))
        templater = Templater(random.Random(f"templates:{seed}"), self._clock)
        sessions: list[Session] = []
        try:
            # 1. The reference must pass every core probe, else the task itself is broken.
            def copy_instance(run_dir: Path) -> None:
                # The reference reads instance.json at startup; the copy lives inside its run
                # dir so it is visible in a container too (never given to the artefact).
                if instance_path.is_file():
                    shutil.copy2(instance_path, run_dir / RUN_DIR_INSTANCE_FILE)

            ref_session = Session(
                ref_sandbox,
                contract,
                reference_dir,
                work_root,
                env_for=lambda run_dir: {
                    INSTANCE_ENV: ref_sandbox.mount_path(run_dir, run_dir / RUN_DIR_INSTANCE_FILE)
                },
                prepare=copy_instance,
            )
            sessions.append(ref_session)
            ref_runner = ProbeRunner(contract, ref_session, templater, limits)
            ref_outcomes = ref_runner.run_groups(spec)
            ref_core_failed = [o for o in ref_outcomes if o.group == "core" and not o.passed]
            if ref_core_failed:
                shown = "; ".join(f"{o.id}: {o.mismatches[:2]}" for o in ref_core_failed[:3])
                raise GraderError(
                    f"reference implementation fails {len(ref_core_failed)} core probe(s) "
                    f"(generator/reference bug): {shown}"
                )
            ref_trap_failed = [o.id for o in ref_outcomes if o.group == "trap" and not o.passed]

            # 2. The artefact.
            artefact_dir = Path(ctx.repo_dir)
            art_session = Session(sandbox, contract, artefact_dir, work_root)
            sessions.append(art_session)
            art_runner = ProbeRunner(contract, art_session, templater, limits)
            artefact_error: str | None = None
            if not (artefact_dir / "run.sh").is_file():
                artefact_error = "run.sh not found in the artefact directory"
                outcomes = [
                    ProbeOutcome(p["id"], g, p.get("trap"), False, [artefact_error], 0.0)
                    for g in ("core", "trap")
                    for p in spec["groups"][g]
                ]
            else:
                outcomes = art_runner.run_groups(spec)
                if art_session.start_failures and not art_session.ever_started:
                    artefact_error = "artefact never became ready"

            # 3. Perf.
            perf_spec = spec["groups"]["perf"]
            perf_detail: dict[str, Any] | None = None
            perf_counts = (0, 0)
            if perf_spec is not None:
                perf_detail, perf_counts = self._perf(
                    perf_spec,
                    spec,
                    artefact_error,
                    art_runner,
                    ref_runner,
                    seed=seed,
                    limits=limits,
                    cfg_timeout=cfg.timeout_seconds,
                    enforce_ref=language == ref_language,
                )
                perf_detail.update({"sandbox": sandbox.kind, "image": sandbox.image})
        finally:
            for s in sessions:
                s.close()
            shutil.rmtree(work_root, ignore_errors=True)

        counts = {
            g: (
                sum(1 for o in outcomes if o.group == g and o.passed),
                sum(1 for o in outcomes if o.group == g),
            )
            for g in ("core", "trap")
        }
        counts["perf"] = perf_counts
        score, breakdown = weighted_score(counts, weights)
        core_ok = all(o.passed for o in outcomes if o.group == "core")
        solved = core_ok and score >= threshold
        detail: dict[str, Any] = {
            "contract": contract,
            "language": language,
            "sandbox": sandbox.kind,
            "image": sandbox.image,
            "reference_ok": True,
            "reference_language": ref_language,
            "reference_trap_failed": ref_trap_failed,
            "core_all_passed": core_ok,
            "pass_threshold": threshold,
            "score_breakdown": breakdown,
            "probe_results": [o.as_detail() for o in outcomes],
            "perf": perf_detail,
        }
        if artefact_error:
            detail["artefact_error"] = artefact_error
        failing = [f"FAIL {o.group}:{o.id}: {o.mismatches[0]}" for o in outcomes if not o.passed]
        return GradeResult(
            solved=solved,
            score=score,
            detail=detail,
            raw_tail="\n".join(failing)[-RAW_TAIL_MAX_CHARS:],
        )

    # -- helpers ----------------------------------------------------------------------

    @staticmethod
    def _grading_dir(ctx: GraderContext) -> Path:
        gd = getattr(ctx, "grading_dir", None)
        if not gd:
            raise GraderError("blackbox grader needs a grading_dir (probes/reference are hidden)")
        path = Path(gd)
        if not path.is_dir():
            raise GraderError(f"grading_dir does not exist: {path}")
        return path

    @staticmethod
    def _resolve_in(base: Path, name: Any) -> Path:
        if not isinstance(name, str) or not name:
            raise GraderError("grader.probes must be a file name inside the grading dir")
        resolved = (base / name).resolve()
        if base.resolve() not in resolved.parents:
            raise GraderError(f"grader.probes {name!r} escapes the grading dir")
        return resolved

    def _perf(
        self,
        perf_spec: Mapping[str, Any],
        spec: Mapping[str, Any],
        artefact_error: str | None,
        art_runner: ProbeRunner,
        ref_runner: ProbeRunner,
        *,
        seed: str,
        limits: Limits,
        cfg_timeout: int | None,
        enforce_ref: bool,
    ) -> tuple[dict[str, Any], tuple[int, int]]:
        budget = float(perf_spec.get("timeout_seconds") or limits.perf_timeout_seconds)
        if artefact_error:
            art = PerfRun(error=f"perf skipped: {artefact_error}")
            ref: PerfRun | None = None
        else:
            ref = None
            if "ref_ratio" in perf_spec.get("checks", PERF_CHECKS):
                ref = measure_perf(perf_spec, ref_runner, seed=seed, budget_seconds=budget)
                if ref.error:
                    raise GraderError(f"reference implementation failed the perf run: {ref.error}")
                silent = {e["class"] for e in perf_spec["mix"]} - set(ref.class_p95)
                if silent:
                    raise GraderError(
                        f"reference implementation produced no perf samples for op class(es) "
                        f"{sorted(silent)} (mix steps failing their expect: {ref.failed_ops})"
                    )
            art = measure_perf(perf_spec, art_runner, seed=seed, budget_seconds=budget)
        detail, passed, total = evaluate_perf(perf_spec, art, ref, enforce_ref=enforce_ref)
        return detail, (passed, total)


register_grader(BLACKBOX_GRADER_TYPE, BlackboxGrader)

__all__ = [
    "BLACKBOX_GRADER_TYPE",
    "BlackboxGrader",
    "MISSING",
    "fit_alpha",
    "json_path_get",
    "load_probe_spec",
    "match",
    "percentile",
    "validate_matcher",
    "validate_probe_spec",
    "weighted_score",
]
