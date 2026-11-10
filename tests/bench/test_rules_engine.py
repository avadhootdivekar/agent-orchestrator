"""T-Pf4Mix `rules-engine` family: the reference pricing engine against HAND-COMPUTED quotes
(probes are derived from it), plus generator validity, determinism and trap search."""

from __future__ import annotations

from typing import Any

from agent_orchestrator.bench.gen import FAMILY_REGISTRY
from agent_orchestrator.bench.gen.base import Difficulty
from agent_orchestrator.bench.gen.rules_engine import new_engine
from agent_orchestrator.bench.gen.traps import TRAP_CATALOGUE
from agent_orchestrator.bench.gen.validate import validate_probes

LIMITS: dict[str, Any] = {
    "products": [
        {"sku": "A", "name": "a", "category": "food", "price": 1000, "weight": 500, "tags": []},
        {
            "sku": "B",
            "name": "b",
            "category": "food",
            "price": 300,
            "weight": 500,
            "tags": ["clearance"],
        },
        {
            "sku": "W",
            "name": "w",
            "category": "drink",
            "price": 2000,
            "weight": 1000,
            "tags": ["alcohol"],
        },
    ],  # fmt: skip
    "regions": {
        "R": {
            "tax_bps": {"food": 1000, "drink": 2000},
            "exempt": ["drink"],
            "alcohol_min_age": 21,
            "shipping_pct": 150,
        },
    },  # fmt: skip
    "rules": [
        {"id": "R1", "kind": "tag", "tag": "clearance", "bps": 5000},
        {"id": "R2", "kind": "volume", "sku": "B", "min_qty": 2, "bps": 1000},
        {"id": "R3", "kind": "bogo", "category": "food", "buy": 2},
        {"id": "R4", "kind": "bundle", "sku_a": "A", "sku_b": "W", "amount": 300},
    ],
    "coupons": {
        "P10": {
            "kind": "pct",
            "bps": 1000,
            "min_spend": 0,
            "valid_from": "2026-01-01",
            "valid_to": "2026-01-31",
        },
        "F5": {
            "kind": "fixed",
            "amount": 500,
            "min_spend": 0,
            "valid_from": "2026-01-01",
            "valid_to": "2026-12-31",
        },
        "S": {
            "kind": "ship",
            "min_spend": 0,
            "valid_from": "2026-01-01",
            "valid_to": "2026-12-31",
            "tiers": ["gold"],
        },
    },  # fmt: skip
    "shipping": {
        "brackets": [[1000, 400], [3000, 700]],
        "over_per_kg": 100,
        "free_threshold": 100_000,
        "free_tiers": [],
    },  # fmt: skip
    "cap_bps": 5000,
    "rounding": "half_up",
}


def quote(
    items: list[tuple[str, int]],
    coupons: list[str] | None = None,
    age: int = 30,
    date: str = "2026-01-31",
    tier: str = "standard",
    **lim: Any,
) -> dict[str, Any]:
    e = new_engine({**LIMITS, **lim})
    resp: dict[str, Any] = e.handle({
        "op": "quote", "customer": {"tier": tier, "region": "R", "age": age}, "date": date,
        "items": [{"sku": s, "qty": q} for s, q in items], "coupons": coupons or [],
    })  # fmt: skip
    return resp


def test_best_line_rule_bogo_on_undiscounted_only_and_merge() -> None:
    r = quote([("A", 2), ("B", 1), ("A", 1), ("B", 1)])  # merged: A x3, B x2
    a, b = r["lines"]
    assert (a["sku"], a["qty"], a["gross"], a["discount"], a["rule"]) == ("A", 3, 3000, 0, None)
    # B: R1 (50% of 600 = 300) beats R2 (10% = 60)
    assert (b["qty"], b["discount"], b["rule"], b["net"]) == (2, 300, "R1", 300)
    assert r["bogo"] == 1000  # 3 undiscounted A units -> floor(3/3) = 1 free (1000)
    # tax 10% on net only: A 300, B 30 ; shipping 2500 g -> 700 * 150% = 1050
    assert r["tax"] == 330 and r["shipping"] == 1050
    assert r["total"] == (3000 + 300 - 1000) + 1050 + 330


def test_coupons_window_best_and_rejections() -> None:
    r = quote([("A", 3)], coupons=["F5", "P10", "F5", "X", "S"])
    # merch: 3000 - bogo 1000 = 2000; P10 = 200 < F5 = 500 -> F5 applies
    assert r["coupon"] == {"code": "F5", "discount": 500}
    assert r["rejected_coupons"] == [
        {"code": "F5", "reason": "duplicate"},
        {"code": "X", "reason": "unknown"},
        {"code": "S", "reason": "not_eligible"},
        {"code": "P10", "reason": "not_best"},
    ]
    late = quote([("A", 3)], coupons=["P10"], date="2026-02-01")
    assert late["rejected_coupons"] == [{"code": "P10", "reason": "expired"}]


def test_cap_reduces_coupon_then_bundle_then_bogo() -> None:
    r = quote([("A", 3), ("W", 1)], coupons=["F5"], cap_bps=1000)
    # gross 5000, cap 500; discounts: bogo 1000 + bundle 300 + coupon 500 = 1800 -> excess 1300
    assert r["capped"] is True and r["coupon"]["discount"] == 0
    assert r["bundle"] == 0 and r["bogo"] == 500


def test_age_rounding_and_exempt_tax() -> None:
    assert quote([("W", 1)], age=20)["error"] == "restricted"
    assert quote([("W", 1)], age=21)["lines"][0]["tax"] == 0  # drink exempt in R
    up = quote([("B", 1)], rules=[{"id": "R1", "kind": "tag", "tag": "clearance", "bps": 2550}])
    floor = quote([("B", 1)], rounding="floor",
                  rules=[{"id": "R1", "kind": "tag", "tag": "clearance", "bps": 2550}])  # fmt: skip
    assert up["lines"][0]["discount"] == 77 and floor["lines"][0]["discount"] == 76  # 76.5


def test_generator_valid_deterministic_and_traps_found() -> None:
    fam = FAMILY_REGISTRY["rules-engine"]()
    d = Difficulty(entities=2, traps=3, concurrency=1, scale=1)
    a = fam.generate(21, d, "python")
    assert a == fam.generate(21, d, "python")
    assert validate_probes(a.probes) == [] and set(a.traps) <= set(TRAP_CATALOGUE)
    assert len(a.traps) >= 4 and not any(k in a.spec_md for k in a.traps)
