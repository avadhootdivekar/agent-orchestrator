"""`bug-hunt` family (T-Pf4Mix): debug planted faults in an existing, working-looking codebase.

The agent receives the `order-book` ``SPEC.md`` and a complete Python implementation of it
(``app/__main__.py``, ~450 lines) into which K seeded faults were planted by AST mutation
(comparison flips, arithmetic swaps, min/max swaps, best-level index swaps, boolean swaps, status /
side literal swaps, dropped statements). Reading and debugging unfamiliar code is a different
skill from writing new code, and the score degrades per unfixed fault.

Every planted fault is validated at generation time, in-process, against the instance's own
probes: alone it must change at least one probe response without raising (so it is observable
through the contract and never just crashes the engine), and all K together must still import.
Probes, SPEC and the hidden reference are exactly the `order-book` ones for the same
seed/difficulty, so the reference self-check and the do-nothing check (which must fail: the
planted faults are visible to probes) carry over unchanged.

Difficulty: ``traps`` = number of planted faults (2/3/5/8); ``entities`` = order-book feature
tier; ``concurrency`` = fuzz depth; ``scale`` = perf book depth. Python only (the code is Python).
"""

from __future__ import annotations

import ast
import random
import signal
import sys
import types
from collections.abc import Sequence
from typing import Any

from .base import Difficulty, Family, Instance, register_family
from .order_book import REFERENCE_PROGRAM, OrderBookFamily
from .refs import REFERENCE_DIR

FAMILY_NAME = "bug-hunt"
BUGS_BY_LEVEL = (2, 3, 5, 8)
APP_ENTRY = "app/__main__.py"
# Wall-clock budget for replaying every probe through one candidate mutant (the unmutated
# engine needs well under 0.1 s).
MUTANT_TIME_LIMIT_SECONDS = 3.0
_CMP_FLIPS: dict[type[ast.cmpop], type[ast.cmpop]] = {
    ast.Lt: ast.LtE,
    ast.LtE: ast.Lt,
    ast.Gt: ast.GtE,
    ast.GtE: ast.Gt,
    ast.Eq: ast.NotEq,
    ast.NotEq: ast.Eq,
}
_LITERAL_SWAPS = {"buy": "sell", "sell": "buy", "filled": "cancelled", "open": "cancelled",
                  "cancel_resting": "cancel_incoming"}  # fmt: skip
# Never mutate the I/O loop or validation helpers: a fault there only crashes or rejects input,
# which the probes see as "everything failed" rather than as one localisable bug.
_SKIP_FUNCTIONS = frozenset({"main", "instance_path", "handle", "_id", "_int", "op_ping"})
_MODULE_DOC = (
    '"""Single-instrument limit order book engine over JSON lines (see SPEC.md).\n\n'
    "Reads one JSON request per line on stdin and writes one JSON response per line on stdout.\n"
    '"""\n'
)

JsonDict = dict[str, Any]


def _sites(tree: ast.Module) -> list[tuple[int, str]]:
    """(node index in `ast.walk` order, mutation kind) for every mutable site."""
    sites: list[tuple[int, str]] = []
    skip_ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in _SKIP_FUNCTIONS:
            skip_ids.update(id(n) for n in ast.walk(node))
    for idx, node in enumerate(ast.walk(tree)):
        if id(node) in skip_ids:
            continue
        if isinstance(node, ast.Compare) and len(node.ops) == 1 and type(node.ops[0]) in _CMP_FLIPS:
            sites.append((idx, "cmp"))
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add | ast.Sub):
            sites.append((idx, "arith"))
        elif isinstance(node, ast.BoolOp):
            sites.append((idx, "bool"))
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id
            in (
                "min",
                "max",
            )
        ):
            sites.append((idx, "minmax"))
        elif (
            isinstance(node, ast.Subscript)
            and isinstance(node.slice, ast.UnaryOp)
            and isinstance(node.slice.op, ast.USub)
            and isinstance(node.slice.operand, ast.Constant)
            and node.slice.operand.value == 1
        ):
            sites.append((idx, "index"))
        elif isinstance(node, ast.Constant) and node.value in _LITERAL_SWAPS:
            sites.append((idx, "literal"))
        elif isinstance(node, ast.Expr | ast.AugAssign) and not isinstance(
            getattr(node, "value", None), ast.Constant
        ):
            sites.append((idx, "drop"))
    return sites


def _mutate(tree: ast.Module, site: tuple[int, str]) -> bool:
    """Apply one mutation in place; False if it is not applicable after all."""
    idx, kind = site
    node = list(ast.walk(tree))[idx]
    if kind == "cmp" and isinstance(node, ast.Compare):
        node.ops = [_CMP_FLIPS[type(node.ops[0])]()]
    elif kind == "arith" and isinstance(node, ast.BinOp):
        node.op = ast.Sub() if isinstance(node.op, ast.Add) else ast.Add()
    elif kind == "bool" and isinstance(node, ast.BoolOp):
        node.op = ast.Or() if isinstance(node.op, ast.And) else ast.And()
    elif kind == "minmax" and isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        node.func.id = "max" if node.func.id == "min" else "min"
    elif kind == "index" and isinstance(node, ast.Subscript):
        node.slice = ast.Constant(0)
    elif kind == "literal" and isinstance(node, ast.Constant) and isinstance(node.value, str):
        node.value = _LITERAL_SWAPS[node.value]
    elif kind == "drop" and isinstance(node, ast.Expr | ast.AugAssign):
        parent = _parent_of(tree, node)
        if parent is None:
            return False
        for field in ("body", "orelse"):
            body = getattr(parent, field, None)
            if isinstance(body, list) and node in body:
                if len(body) == 1:
                    return False
                body.remove(node)
                return True
        return False
    else:
        return False
    return True


def _parent_of(tree: ast.AST, target: ast.AST) -> ast.AST | None:
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            if child is target:
                return parent
    return None


def _load(source: str) -> types.ModuleType | None:
    name = "ao_bench_bug_hunt_candidate"
    module = types.ModuleType(name)
    sys.modules[name] = module  # dataclasses resolve annotations through sys.modules
    try:
        exec(compile(source, "<mutant>", "exec"), module.__dict__)  # noqa: S102 -- our own code
    except Exception:  # noqa: BLE001 -- any failure = mutation rejected
        return None
    finally:
        sys.modules.pop(name, None)
    return module


def _matches(expected: Any, actual: Any) -> bool:
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            k in actual and _matches(v, actual[k]) for k, v in expected.items()
        )
    if isinstance(expected, list):
        return (
            isinstance(actual, list)
            and len(expected) == len(actual)
            and all(_matches(e, a) for e, a in zip(expected, actual, strict=True))
        )
    return bool(expected == actual and type(expected) is type(actual))


class _MutantTimeout(Exception):
    pass


def _on_alarm(signum: int, frame: Any) -> None:
    raise _MutantTimeout


def _killed(module: types.ModuleType, limits: JsonDict, probes: list[JsonDict]) -> bool | None:
    """True if some probe step differs, False if none does, None if the engine raised or
    hung (a fault can turn a matching loop infinite; such a mutant is rejected)."""
    killed = False
    previous = signal.signal(signal.SIGALRM, _on_alarm)
    signal.setitimer(signal.ITIMER_REAL, MUTANT_TIME_LIMIT_SECONDS)
    try:
        for p in probes:
            engine = module.Engine(limits)
            for step in p["steps"]:
                try:
                    resp = engine.handle(step["op"]["json"])
                except _MutantTimeout:
                    return None
                except Exception:  # noqa: BLE001 -- a crashing mutant is rejected
                    return None
                if not _matches(step["expect"]["json"], resp):
                    killed = True
    except _MutantTimeout:
        return None
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
    return killed


def plant_bugs(source: str, rng: random.Random, count: int, limits: JsonDict,
               probes: list[JsonDict]) -> tuple[str, list[str]]:  # fmt: skip
    """Return (mutated source, list of planted-fault descriptions)."""
    base = ast.parse(source)
    sites = _sites(base)
    rng.shuffle(sites)
    chosen: list[tuple[int, str]] = []
    for site in sites:
        if len(chosen) >= count:
            break
        tree = ast.parse(source)
        if not _mutate(tree, site):
            continue
        module = _load(ast.unparse(tree))
        if module is None or _killed(module, limits, probes) is not True:
            continue
        combined = ast.parse(source)
        # Apply in descending node order so earlier indices stay valid after a drop.
        if not all(_mutate(combined, s) for s in sorted([*chosen, site], reverse=True)):
            continue
        if _load(ast.unparse(combined)) is None:
            continue
        chosen.append(site)
    if len(chosen) < count:
        raise ValueError(f"bug-hunt: only {len(chosen)} valid faults found, {count} requested")
    tree = ast.parse(source)
    for s in sorted(chosen, reverse=True):
        _mutate(tree, s)
    if (
        tree.body
        and isinstance(tree.body[0], ast.Expr)
        and isinstance(tree.body[0].value, ast.Constant)
    ):
        tree.body = tree.body[1:]  # drop the reference docstring
    return _MODULE_DOC + ast.unparse(tree) + "\n", [f"{k}@{i}" for i, k in sorted(chosen)]


@register_family
class BugHuntFamily(Family):
    name = FAMILY_NAME
    contract = "jsonl"
    instruction_kind = "debug"
    in_default_set = False

    def generate(
        self,
        seed: int,
        difficulty: Difficulty,
        language: str,
        allow: Sequence[str] | None = None,
    ) -> Instance:
        if language != "python":
            raise ValueError("bug-hunt instances are Python only")
        base = OrderBookFamily().generate(seed, difficulty, language, allow)
        source = (REFERENCE_DIR / REFERENCE_PROGRAM).read_text(encoding="utf-8")
        probes = base.probes["groups"]["core"] + base.probes["groups"]["trap"]
        mutated, planted = plant_bugs(
            source,
            random.Random(f"bug-hunt:{seed}"),
            BUGS_BY_LEVEL[difficulty.traps],
            base.limits,
            probes,
        )
        skeleton = dict(base.skeleton)
        skeleton[APP_ENTRY] = mutated
        limits = {**base.limits, "planted_faults": planted}
        return base.model_copy(update={"family": self.name, "skeleton": skeleton, "limits": limits})


__all__ = ["BUGS_BY_LEVEL", "FAMILY_NAME", "BugHuntFamily", "plant_bugs"]
