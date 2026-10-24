"""Reference implementation of the `rules-engine` family (grader-owned, never agent-visible).

A stateless cart-pricing engine over JSON lines, parameterised by ``instance.json`` (catalog,
regions, rules, coupons, shipping, cap, rounding). The generator imports ``Engine`` to compute every
expected response, so probes and this program cannot disagree. All money is integer cents; rates
are basis points (1/100 of a percent).

This file is copied verbatim into ``<grading_dir>/reference/`` by the generated provider.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TIERS = ("standard", "silver", "gold")
QTY_MAX = 999


class OpError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code, self.message = code, message


def invalid(message: str) -> OpError:
    return OpError("invalid_request", message)


def as_int(v: Any, lo: int, hi: int) -> int | None:
    """`v` if it is an integer (not a bool) in [lo, hi], else None."""
    if isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi:
        return v
    return None


class Engine:
    def __init__(self, limits: dict[str, Any]) -> None:
        self.products = {p["sku"]: p for p in limits["products"]}
        self.regions: dict[str, Any] = limits["regions"]
        self.rules: list[dict[str, Any]] = limits["rules"]
        self.coupons: dict[str, Any] = limits["coupons"]
        self.shipping: dict[str, Any] = limits["shipping"]
        self.cap_bps: int = limits["cap_bps"]
        self.rounding: str = limits["rounding"]

    def rnd(self, numerator: int, denominator: int) -> int:
        """Round numerator/denominator (both >= 0) to an integer with the instance's mode."""
        if self.rounding == "floor":
            return numerator // denominator
        return (2 * numerator + denominator) // (2 * denominator)  # half up

    def pct(self, amount: int, bps: int) -> int:
        return self.rnd(amount * bps, 10_000)

    def handle(self, req: Any) -> dict[str, Any]:
        try:
            if not isinstance(req, dict) or not isinstance(req.get("op"), str):
                raise invalid("request must be an object with a string op")
            if req["op"] == "ping":
                return {"ok": True}
            if req["op"] == "quote":
                return self.quote(req)
            raise OpError("unknown_op", f"unknown op {req['op']!r}")
        except OpError as err:
            return {"ok": False, "error": err.code, "message": err.message}

    # -- validation ----------------------------------------------------------------------
    def _parse(
        self, req: dict[str, Any]
    ) -> tuple[dict[str, Any], str, list[tuple[str, int]], list[str]]:
        cust = req.get("customer")
        if not isinstance(cust, dict):
            raise invalid("customer must be an object")
        if cust.get("tier") not in TIERS:
            raise invalid("customer.tier must be standard, silver or gold")
        if cust.get("region") not in self.regions:
            raise invalid("customer.region is not a known region")
        if as_int(cust.get("age"), 0, 150) is None:
            raise invalid("customer.age must be an integer from 0 to 150")
        date = req.get("date")
        if not isinstance(date, str) or not DATE_RE.match(date):
            raise invalid("date must be YYYY-MM-DD")
        items = req.get("items")
        if not isinstance(items, list) or not items:
            raise invalid("items must be a non-empty array")
        merged: dict[str, int] = {}
        for it in items:
            if not isinstance(it, dict) or not isinstance(it.get("sku"), str):
                raise invalid("each item needs a string sku")
            qty = as_int(it.get("qty"), 1, QTY_MAX)
            if qty is None:
                raise invalid(f"qty must be an integer from 1 to {QTY_MAX}")
            if it["sku"] not in self.products:
                raise OpError("unknown_sku", f"unknown sku {it['sku']!r}")
            merged[it["sku"]] = merged.get(it["sku"], 0) + qty
        for qty in merged.values():
            if qty > QTY_MAX:
                raise invalid(f"total qty per sku must not exceed {QTY_MAX}")
        coupons = req.get("coupons", [])
        if not isinstance(coupons, list) or not all(isinstance(c, str) for c in coupons):
            raise invalid("coupons must be an array of strings")
        return cust, date, list(merged.items()), coupons

    # -- pricing --------------------------------------------------------------------------
    def quote(self, req: dict[str, Any]) -> dict[str, Any]:
        cust, date, items, codes = self._parse(req)
        region = self.regions[cust["region"]]
        if any("alcohol" in self.products[s]["tags"] for s, _ in items):
            min_age = region["alcohol_min_age"]
            if min_age is None or cust["age"] < min_age:
                raise OpError("restricted", "alcohol cannot be sold to this customer")

        lines: list[dict[str, Any]] = []
        for sku, qty in items:
            p = self.products[sku]
            gross = p["price"] * qty
            best, best_rule = 0, None
            for rule in self.rules:
                bps = self._line_rule_bps(rule, p, qty, cust["tier"])
                if bps is None:
                    continue
                amount = self.pct(gross, bps)
                if amount > best:
                    best, best_rule = amount, rule["id"]
            lines.append({"sku": sku, "qty": qty, "gross": gross, "discount": best,
                          "rule": best_rule, "net": gross - best})  # fmt: skip

        bogo = 0
        for rule in self.rules:
            if rule["kind"] != "bogo":
                continue
            units: list[tuple[int, str]] = []
            for ln in lines:
                p = self.products[ln["sku"]]
                if p["category"] == rule["category"] and ln["rule"] is None:
                    units += [(p["price"], ln["sku"])] * ln["qty"]
            free = len(units) // (rule["buy"] + 1)
            bogo += sum(price for price, _ in sorted(units)[:free])

        qty_of = dict(items)
        bundle = 0
        for rule in self.rules:
            if rule["kind"] == "bundle":
                pairs = min(qty_of.get(rule["sku_a"], 0), qty_of.get(rule["sku_b"], 0))
                bundle += pairs * rule["amount"]

        merch = sum(ln["net"] for ln in lines) - bogo - bundle
        coupon_code, coupon, free_ship, rejected = self._coupons(codes, date, cust["tier"], merch)

        gross_total = sum(ln["gross"] for ln in lines)
        cap = self.pct(gross_total, self.cap_bps)
        excess = sum(ln["discount"] for ln in lines) + bogo + bundle + coupon - cap
        capped = excess > 0
        if capped:  # remove the excess from the coupon, then bundle, then bogo
            cut = min(excess, coupon)
            coupon, excess = coupon - cut, excess - cut
            cut = min(excess, bundle)
            bundle, excess = bundle - cut, excess - cut
            bogo -= min(excess, bogo)
        merch = sum(ln["net"] for ln in lines) - bogo - bundle

        shipping = self._shipping(items, region, cust["tier"], merch - coupon, free_ship)

        tax_total = 0
        for ln in lines:
            cat = self.products[ln["sku"]]["category"]
            rate = 0 if cat in region["exempt"] else region["tax_bps"][cat]
            ln["tax"] = self.pct(ln["net"], rate)
            tax_total += ln["tax"]

        return {
            "ok": True,
            "lines": lines,
            "bogo": bogo,
            "bundle": bundle,
            "coupon": {"code": coupon_code, "discount": coupon},
            "rejected_coupons": rejected,
            "capped": capped,
            "shipping": shipping,
            "tax": tax_total,
            "total": merch - coupon + shipping + tax_total,
        }

    def _line_rule_bps(self, rule: dict[str, Any], product: dict[str, Any], qty: int,
                       tier: str) -> int | None:  # fmt: skip
        kind = rule["kind"]
        if kind == "volume" and rule["sku"] == product["sku"] and qty >= rule["min_qty"]:
            return int(rule["bps"])
        if (
            kind == "tier_category"
            and rule["tier"] == tier
            and rule["category"] == product["category"]
        ):
            return int(rule["bps"])
        if kind == "tag" and rule["tag"] in product["tags"]:
            return int(rule["bps"])
        return None

    def _coupons(self, codes: list[str], date: str, tier: str,
                 merch: int) -> tuple[str | None, int, bool, list[dict[str, str]]]:  # fmt: skip
        rejected: list[dict[str, str]] = []
        seen: set[str] = set()
        candidates: list[tuple[int, str]] = []
        free_ship = False
        for code in codes:
            c: dict[str, Any] = self.coupons.get(code) or {}
            if code in seen:
                rejected.append({"code": code, "reason": "duplicate"})
                continue
            seen.add(code)
            if not c:
                reason = "unknown"
            elif not c["valid_from"] <= date <= c["valid_to"]:
                reason = "expired"
            elif c.get("tiers") and tier not in c["tiers"]:
                reason = "not_eligible"
            elif merch < c["min_spend"]:
                reason = "min_spend"
            else:
                reason = ""
            if reason:
                rejected.append({"code": code, "reason": reason})
                continue
            if c["kind"] == "ship":
                free_ship = True
                continue
            amount = self.pct(merch, c["bps"]) if c["kind"] == "pct" else min(c["amount"], merch)
            candidates.append((amount, code))
        best_code, best = None, 0
        for amount, code in candidates:
            if best_code is None or amount > best:
                best_code, best = code, amount
        for _, code in candidates:
            if code != best_code:
                rejected.append({"code": code, "reason": "not_best"})
        return best_code, best, free_ship, rejected

    def _shipping(self, items: list[tuple[str, int]], region: dict[str, Any], tier: str,
                  merch_after_coupon: int, free_ship: bool) -> int:  # fmt: skip
        s = self.shipping
        if free_ship or tier in s["free_tiers"] or merch_after_coupon >= s["free_threshold"]:
            return 0
        weight = sum(self.products[sku]["weight"] * qty for sku, qty in items)
        base = None
        for max_weight, price in s["brackets"]:
            if weight <= max_weight:
                base = price
                break
        if base is None:
            last_max, last_price = s["brackets"][-1]
            extra_kg = -(-(weight - last_max) // 1000)
            base = last_price + extra_kg * s["over_per_kg"]
        return self.rnd(base * region["shipping_pct"], 100)


def instance_path() -> str:
    return os.environ.get("INSTANCE_JSON") or str(Path(__file__).with_name("instance.json"))


def main() -> None:
    with open(instance_path(), encoding="utf-8") as fh:
        inst = json.load(fh)
    engine = Engine(inst["limits"])
    out = sys.stdout
    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except ValueError:
            resp: dict[str, Any] = {"ok": False, "error": "invalid_request",
                                    "message": "not valid JSON"}  # fmt: skip
        else:
            resp = engine.handle(req)
        out.write(json.dumps(resp) + "\n")
        out.flush()


if __name__ == "__main__":
    main()
