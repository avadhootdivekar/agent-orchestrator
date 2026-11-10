"""`rules-engine` family (T-Pf4Mix): a long, rule-dense spec -- cart pricing over JSON lines.

The difficulty is not any single rule but holding a long spec in mind and applying many interacting
rules in the stated order: best-of line discounts, buy-k-get-one on undiscounted lines only,
bundles, coupon eligibility/stacking with rejection reasons, a discount cap with a fixed reduction
order, weight-bracket shipping with free-shipping conditions, per-region tax with exemptions, an
alcohol age rule and a seeded rounding mode. The engine is stateless; every expected response is
computed by the reference ``Engine`` (``reference/rules_server.py``). Trap probes are found by
*search*: seeded random carts are replayed through the engine until one actually exercises the
trap (e.g. a quote that really hits the cap), so each trap probe tests its condition.

Difficulty: ``entities`` = catalog size / categories / regions; ``traps`` = number of rules and
coupons, and of emphasised trap probes; ``concurrency`` = number of random quote probes in core;
``scale`` is unused beyond the (non-discriminating) perf group -- the engine has no data to scale.
"""

from __future__ import annotations

import importlib.util
import random
import sys
from collections.abc import Callable, Sequence
from functools import cache
from types import ModuleType
from typing import Any

from . import dsl
from .base import Difficulty, Family, Instance, register_family
from .dsl import jsonl_step, mix_entry, perf_spec, probe
from .refs import REFERENCE_DIR, reference_files
from .skeleton import allow_list_text, resolve_allow, run_contract_text, skeleton_files
from .traps import trap_count

FAMILY_NAME = "rules-engine"
REFERENCE_PROGRAM = "rules_server.py"
PRODUCTS_BY_LEVEL = (8, 12, 16, 24)
CATEGORIES_BY_LEVEL = (3, 4, 5, 6)
REGIONS_BY_LEVEL = (2, 3, 3, 4)
RULES_BY_LEVEL = (5, 7, 11, 16)
COUPONS_BY_LEVEL = (4, 5, 6, 8)
RANDOM_PROBES_BY_LEVEL = (8, 14, 20, 30)
QUOTES_PER_RANDOM_PROBE = 3
SEARCH_ATTEMPTS = 4000
CATEGORY_NAMES = ("pantry", "dairy", "beverages", "household", "garden", "toys", "books",
                  "electronics", "apparel", "pets")  # fmt: skip
NOUNS = (
    "lamp", "kettle", "mug", "towel", "candle", "basket", "brush", "jar", "rope", "bowl",
    "tray", "clock", "kite", "puzzle", "scarf", "glove", "soap", "tea", "cocoa", "olive",
    "cider", "lager", "stout", "wine", "honey", "salt", "bread", "cheese", "seed", "pot",
)  # fmt: skip
REGION_CODES = ("NORTH", "SOUTH", "EAST", "WEST", "ISLES")
DATES = ("2026-01-05", "2026-02-14", "2026-03-31", "2026-04-01", "2026-06-15", "2026-07-04",
         "2026-09-30", "2026-10-01", "2026-12-24")  # fmt: skip
TRAP_KEYS = (
    "merge_duplicate_lines",
    "best_line_discount",
    "bogo_excludes_discounted",
    "coupon_not_best",
    "discount_cap",
    "tax_exempt",
    "free_shipping_after_coupon",
    "age_restriction",
    "coupon_window_inclusive",
    "rounding_mode",
)

JsonDict = dict[str, Any]


@cache
def _engine_module() -> ModuleType:
    path = REFERENCE_DIR / REFERENCE_PROGRAM
    spec = importlib.util.spec_from_file_location("ao_bench_rules_reference", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def new_engine(limits: JsonDict) -> Any:
    return _engine_module().Engine(limits)


def _expect(resp: JsonDict) -> JsonDict:
    return {"ok": False, "error": resp["error"]} if resp.get("ok") is False else resp


def scenario(limits: JsonDict, requests: Sequence[Any]) -> list[JsonDict]:
    engine = new_engine(limits)
    return [jsonl_step(r, match=_expect(engine.handle(r))) for r in requests]


# ---------------------------------------------------------------------------------------------
# Instance data
# ---------------------------------------------------------------------------------------------


def _build_limits(rng: random.Random, d: Difficulty) -> JsonDict:
    cats: list[str] = rng.sample(CATEGORY_NAMES, CATEGORIES_BY_LEVEL[d.entities])
    nouns = rng.sample(NOUNS, PRODUCTS_BY_LEVEL[d.entities])
    products: list[JsonDict] = []
    for i, noun in enumerate(nouns):
        tags: list[str] = []
        if noun in ("cider", "lager", "stout", "wine") or (i % 7 == 3):
            tags.append("alcohol")
        if rng.random() < 0.25:
            tags.append("clearance")
        if rng.random() < 0.2:
            tags.append("imported")
        products.append({
            "sku": f"{noun[:3].upper()}-{100 + i}",
            "name": noun,
            "category": cats[i % len(cats)],
            "price": rng.randint(99, 4999),
            "weight": rng.choice((150, 250, 400, 750, 1200, 2500)),
            "tags": sorted(tags),
        })  # fmt: skip
    regions: JsonDict = {}
    for code in REGION_CODES[: REGIONS_BY_LEVEL[d.entities]]:
        regions[code] = {
            "tax_bps": {c: rng.choice((0, 500, 800, 1250, 1900, 2100)) for c in cats},
            "exempt": sorted(rng.sample(cats, 1)),
            "alcohol_min_age": rng.choice((18, 21, None)),
            "shipping_pct": rng.choice((100, 115, 130, 150)),
        }
    rules = _build_rules(rng, products, cats, RULES_BY_LEVEL[d.traps])
    return {
        "products": products,
        "regions": regions,
        "rules": rules,
        "coupons": _build_coupons(rng, cats, COUPONS_BY_LEVEL[d.traps]),
        "shipping": {
            "brackets": [[1000, 499], [3000, 799], [8000, 1299]],
            "over_per_kg": rng.choice((99, 149, 199)),
            "free_threshold": rng.choice((5000, 7500, 10000)),
            "free_tiers": ["gold"],
        },
        "cap_bps": rng.choice((2000, 2500, 3000, 3500)),
        "rounding": rng.choice(("half_up", "floor")),
    }


def _build_rules(rng: random.Random, products: list[JsonDict], cats: list[str],
                 count: int) -> list[JsonDict]:  # fmt: skip
    kinds = ["volume", "tier_category", "tag", "bogo", "bundle"]
    kinds += [rng.choice(("volume", "tier_category", "tag", "bundle")) for _ in range(count - 5)]
    rng.shuffle(kinds)
    rules: list[JsonDict] = []
    bogo_cats: set[str] = set()
    bundled: set[str] = set()
    for i, kind in enumerate(kinds):
        rid = f"R{i + 1}"
        if kind == "volume":
            p = rng.choice(products)
            rules.append({"id": rid, "kind": kind, "sku": p["sku"], "min_qty": rng.randint(3, 8),
                          "bps": rng.choice((500, 1000, 1500, 2000))})  # fmt: skip
        elif kind == "tier_category":
            tier = rng.choice(("silver", "gold"))
            cat, bps = rng.choice(cats), rng.choice((300, 750, 1250))
            rules.append({"id": rid, "kind": kind, "tier": tier, "category": cat, "bps": bps})
        elif kind == "tag":
            rules.append({"id": rid, "kind": kind, "tag": rng.choice(("clearance", "imported")),
                          "bps": rng.choice((1000, 2500, 4000))})  # fmt: skip
        elif kind == "bogo":
            free = [c for c in cats if c not in bogo_cats]
            cat = rng.choice(free)
            bogo_cats.add(cat)
            rules.append({"id": rid, "kind": kind, "category": cat, "buy": rng.choice((2, 3))})
        else:
            free_p = [p for p in products if p["sku"] not in bundled]
            a, b = rng.sample(free_p, 2)
            bundled |= {a["sku"], b["sku"]}
            rules.append({"id": rid, "kind": kind, "sku_a": a["sku"], "sku_b": b["sku"],
                          "amount": rng.choice((100, 250, 400))})  # fmt: skip
    return rules


def _build_coupons(rng: random.Random, cats: list[str], count: int) -> JsonDict:
    kinds = ["pct", "fixed", "ship", "pct"] + [rng.choice(("pct", "fixed", "ship"))
                                               for _ in range(count - 4)]  # fmt: skip
    coupons: JsonDict = {}
    for i, kind in enumerate(kinds):
        start, end = sorted(rng.sample(range(len(DATES)), 2))
        c: JsonDict = {"kind": kind, "min_spend": rng.choice((0, 2000, 4000, 6000)),
                       "valid_from": DATES[start], "valid_to": DATES[end]}  # fmt: skip
        if kind == "pct":
            c["bps"] = rng.choice((500, 1000, 1500, 2000))
        elif kind == "fixed":
            c["amount"] = rng.choice((300, 500, 1000, 1500))
        if i == 3 or rng.random() < 0.2:
            c["tiers"] = sorted(rng.sample(["standard", "silver", "gold"], 2))
        coupons[f"{kind.upper()}{rng.randint(10, 99)}{i}"] = c
    return coupons


# ---------------------------------------------------------------------------------------------
# SPEC.md
# ---------------------------------------------------------------------------------------------


def _money(cents: int) -> str:
    return f"{cents} ({cents / 100:.2f})"


def _md_table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    lines.extend("| " + " | ".join(r) + " |" for r in rows)
    return "\n".join(lines)


def _rule_text(r: JsonDict) -> str:
    pct = f"{r.get('bps', 0) / 100:g}%"
    return {
        "volume": f"**{r['id']} (line)** — a line of `{r.get('sku')}` with quantity of at least "
        f"{r.get('min_qty')} gets {pct} off its gross amount.",
        "tier_category": f"**{r['id']} (line)** — for `{r.get('tier')}` customers, every line in "
        f"category `{r.get('category')}` gets {pct} off its gross amount.",
        "tag": f"**{r['id']} (line)** — every line whose product is tagged `{r.get('tag')}` gets "
        f"{pct} off its gross amount.",
        "bogo": f"**{r['id']} (buy {r.get('buy')}, get 1 free)** — in category "
        f"`{r.get('category')}`, for every {r.get('buy', 0) + 1} eligible units one unit is "
        "free (see step 3).",
        "bundle": f"**{r['id']} (bundle)** — for every pair of one `{r.get('sku_a')}` and one "
        f"`{r.get('sku_b')}` in the cart, {r.get('amount')} cents off.",
    }[r["kind"]]


def render_spec(limits: JsonDict, language: str, allow: Sequence[str]) -> str:
    rnd = ("rounded **down** to a whole cent" if limits["rounding"] == "floor"
           else "rounded to the nearest whole cent, halves rounded **up**")  # fmt: skip
    products = _md_table(
        ["SKU", "Name", "Category", "Unit price (cents)", "Weight (g)", "Tags"],
        [[f"`{p['sku']}`", p["name"], p["category"], str(p["price"]), str(p["weight"]),
          ", ".join(f"`{t}`" for t in p["tags"]) or "—"] for p in limits["products"]],
    )  # fmt: skip
    cats = sorted({p["category"] for p in limits["products"]})
    regions = _md_table(
        ["Region", *[f"Tax: {c}" for c in cats], "Tax-exempt", "Alcohol min. age",
         "Shipping factor"],
        [[f"`{code}`", *[f"{r['tax_bps'][c] / 100:g}%" for c in cats],
          ", ".join(r["exempt"]),
          "not sold" if r["alcohol_min_age"] is None else str(r["alcohol_min_age"]),
          f"{r['shipping_pct']}%"] for code, r in limits["regions"].items()],
    )  # fmt: skip
    coupons = _md_table(
        ["Code", "Kind", "Value", "Min. spend (cents)", "Valid from", "Valid to", "Tiers"],
        [[f"`{code}`", c["kind"],
          {"pct": f"{c.get('bps', 0) / 100:g}% off", "fixed": f"{c.get('amount')} cents off",
           "ship": "free shipping"}[c["kind"]],
          str(c["min_spend"]), c["valid_from"], c["valid_to"],
          ", ".join(c.get("tiers", [])) or "all"] for code, c in limits["coupons"].items()],
    )  # fmt: skip
    ship = limits["shipping"]
    brackets = "; ".join(f"up to {w} g: {p} cents" for w, p in ship["brackets"])
    rules = "\n".join(f"- {_rule_text(r)}" for r in limits["rules"])
    parts = [
        "# Specification: cart pricing engine over JSON lines",
        "",
        "Implement the pricing engine described here, in this directory. Everything in this "
        "document is a requirement. The engine is checked only through its standard input and "
        "output; it has no state between requests. All money is in integer cents.",
        "",
        "## 1. Run contract",
        "",
        run_contract_text("jsonl", language),
        "",
        "## 2. Allowed dependencies",
        "",
        allow_list_text(language, allow),
        "",
        "## 3. Protocol",
        "",
        "- Every request is one line holding a JSON object with a string field `op`; every "
        "response is one line holding a JSON object with a boolean field `ok`. Fields that are "
        "not listed are ignored; blank lines are skipped; a line that is not valid JSON gets an "
        "`invalid_request` error.",
        '- Errors: `{"ok": false, "error": "<code>", "message": "<text>"}` with code '
        "`invalid_request` (shape, type or range problems), `unknown_op`, `unknown_sku` (an "
        "item names a SKU that is not in the catalog) or `restricted` (see step 0). Validation "
        "problems (`invalid_request`, `unknown_sku`, in item order) are reported before "
        "`restricted`.",
        '- `{"op": "ping"}` → `{"ok": true}`.',
        "",
        "## 4. The `quote` request",
        "",
        '```\n{"op": "quote", "customer": {"tier": "standard" | "silver" | "gold", "region": '
        '<region code>, "age": <0-150>}, "date": "YYYY-MM-DD", "items": [{"sku": <sku>, "qty": '
        '<1-999>}, ...], "coupons": [<code>, ...]}\n```',
        "",
        "`items` is a non-empty array; `coupons` is optional (default empty). The **same SKU may "
        "appear in several items: they are merged into one line** whose quantity is the sum "
        "(which must not exceed 999), placed where the SKU first appears.",
        "",
        "Response:",
        "",
        '```\n{"ok": true, "lines": [{"sku", "qty", "gross", "discount", "rule", "net", "tax"}, '
        '...], "bogo": B, "bundle": U, "coupon": {"code": <code or null>, "discount": C}, '
        '"rejected_coupons": [{"code", "reason"}, ...], "capped": <bool>, "shipping": S, '
        '"tax": T, "total": X}\n```',
        "",
        "## 5. Catalog",
        "",
        products,
        "",
        "## 6. Regions",
        "",
        regions,
        "",
        "## 7. Rules",
        "",
        "Line rules (volume, tier, tag) and the other rules below; each applies wherever its "
        "condition holds:",
        "",
        rules,
        "",
        "## 8. Coupons",
        "",
        coupons,
        "",
        "## 9. Shipping",
        "",
        f"By total cart weight (unit weight × quantity, all lines): {brackets}; above "
        f"{ship['brackets'][-1][0]} g, {ship['brackets'][-1][1]} cents plus {ship['over_per_kg']} "
        "cents for every started kilogram above it. The result is multiplied by the region's "
        f"shipping factor ({rnd}). Shipping is 0 if the customer's tier is "
        f"{', '.join(ship['free_tiers'])}, or if a valid free-shipping coupon is accepted, or if "
        f"merchandise minus the coupon discount (after the cap, step 7) is at least "
        f"{_money(ship['free_threshold'])}.",
        "",
        "## 10. Computation (in this order)",
        "",
        "Every percentage amount (`amount × rate`) is " + rnd + ".",
        "",
        "0. **Age.** If any line's product is tagged `alcohol`, the region's minimum age must be "
        "set and the customer's age must be at least it; otherwise the whole quote fails with "
        "`restricted`.",
        "1. **Lines.** For each merged line, `gross` = unit price × quantity.",
        "2. **Line discount.** Among the line rules whose condition holds for the line, the one "
        "giving the **largest** amount applies (only one per line; on a tie the lower-numbered "
        "rule). `discount` is that amount (0 if none), `rule` is its id (or `null`), `net` = "
        "`gross` − `discount`.",
        "3. **Buy k, get 1.** For each such rule, take every unit of every line in its category "
        "**that received no line discount**; with n such units, `floor(n / (k + 1))` units are "
        "free, and the free ones are the **cheapest** units. `bogo` is the total price of all "
        "free units.",
        "4. **Bundles.** For each bundle rule, the number of pairs is the smaller of the two "
        "SKUs' quantities (whatever their line discounts); `bundle` is the sum of pairs × amount.",
        "5. **Merchandise** = sum of line `net` − `bogo` − `bundle`.",
        "6. **Coupons**, in request order. A code is rejected with reason `duplicate` if it "
        "already appeared in this request, `unknown` if it is not in the table, `expired` if the "
        "date is outside its validity (both dates inclusive), `not_eligible` if it lists tiers "
        "and the customer's is not one of them, `min_spend` if merchandise (step 5) is below its "
        "minimum. Every accepted free-shipping coupon applies. Among the accepted percentage and "
        "fixed coupons only **one** applies — the one with the largest discount (on a tie the "
        "earliest in the request); the others are rejected with `not_best`. A percentage coupon "
        "takes its rate of merchandise; a fixed coupon takes its amount, but at most the "
        "merchandise. `rejected_coupons` lists rejections in the order they were decided (all "
        "`not_best` rejections come last, in request order).",
        f"7. **Cap.** The total discount (all line discounts + `bogo` + `bundle` + coupon) may "
        f"not exceed {limits['cap_bps'] / 100:g}% of the sum of all `gross` amounts. If it does, "
        "`capped` is true and the excess is removed by reducing the **coupon** discount first, "
        "then `bundle`, then `bogo` (line discounts are never reduced). Merchandise is then "
        "recomputed with the reduced `bundle`/`bogo`.",
        "8. **Shipping** as in section 9 (using the values after step 7).",
        "9. **Tax.** For each line, `tax` = the region's rate for the line's category × the "
        "line's `net` (0 if the category is tax-exempt in that region). Coupon, bundle and "
        "buy-k-get-1 discounts do not reduce tax; shipping is not taxed. `tax` (top level) is "
        "the sum.",
        "10. **Total** = merchandise − coupon discount + shipping + tax.",
    ]
    return "\n".join(parts) + "\n"


# ---------------------------------------------------------------------------------------------
# Probes
# ---------------------------------------------------------------------------------------------


class Probes:
    def __init__(self, rng: random.Random, limits: JsonDict) -> None:
        self.rng = rng
        self.limits = limits
        self.engine = new_engine(limits)
        self.skus = [p["sku"] for p in limits["products"]]
        self.codes = list(limits["coupons"])

    def cart(self) -> JsonDict:
        r = self.rng
        items = [{"sku": s, "qty": r.choice((1, 1, 2, 3, 4, 6, 9, 12))}
                 for s in r.sample(self.skus, r.randint(1, 6))]  # fmt: skip
        if r.random() < 0.2:
            items.append({"sku": items[0]["sku"], "qty": r.randint(1, 4)})
        codes = r.sample(self.codes, r.randint(0, min(3, len(self.codes))))
        if r.random() < 0.1:
            codes.append("NOPE42")
        return {
            "op": "quote",
            "customer": {"tier": r.choice(("standard", "silver", "gold")),
                         "region": r.choice(list(self.limits["regions"])),
                         "age": r.choice((16, 18, 19, 21, 30, 45))},
            "date": r.choice(DATES),
            "items": items,
            "coupons": codes,
        }  # fmt: skip

    def search(self, pred: Callable[[JsonDict, JsonDict], bool]) -> JsonDict | None:
        for _ in range(SEARCH_ATTEMPTS):
            req = self.cart()
            resp = self.engine.handle(req)
            if pred(req, resp):
                return req
        return None

    def core(self, n_random: int) -> list[JsonDict]:
        out = [probe("core-ping", scenario(self.limits, [{"op": "ping"}]))]
        bad = [
            {"op": "quote"},
            {**self.cart(), "items": []},
            {**self.cart(), "items": [{"sku": self.skus[0], "qty": 0}]},
            {**self.cart(), "items": [{"sku": "NOT-THERE", "qty": 1}]},
            {**self.cart(), "date": "2026/01/01"},
            {"op": "price"},
        ]
        out.append(probe("core-validation", scenario(self.limits, bad)))
        for i in range(n_random):
            reqs = [self.cart() for _ in range(QUOTES_PER_RANDOM_PROBE)]
            out.append(probe(f"core-quotes-{i + 1}", scenario(self.limits, reqs)))
        return out

    def _ok(self, resp: JsonDict) -> bool:
        return bool(resp.get("ok"))

    def predicates(self) -> dict[str, Callable[[JsonDict, JsonDict], bool]]:
        limits = self.limits
        products = {p["sku"]: p for p in limits["products"]}
        eng = self.engine

        def multi_rule(req: JsonDict, resp: JsonDict) -> bool:
            if not self._ok(resp):
                return False
            for ln in resp["lines"]:
                p = products[ln["sku"]]
                amounts = {eng.pct(ln["gross"], b) for r in limits["rules"]
                           if (b := eng._line_rule_bps(r, p, ln["qty"], req["customer"]["tier"]))
                           is not None}  # fmt: skip
                if len(amounts) >= 2:
                    return True
            return False

        def bogo_mix(req: JsonDict, resp: JsonDict) -> bool:
            if not self._ok(resp) or resp["bogo"] <= 0:
                return False
            cats = {r["category"] for r in limits["rules"] if r["kind"] == "bogo"}
            return any(ln["rule"] and products[ln["sku"]]["category"] in cats
                       for ln in resp["lines"])  # fmt: skip

        def exempt(req: JsonDict, resp: JsonDict) -> bool:
            region = limits["regions"][req["customer"]["region"]]
            return self._ok(resp) and any(products[ln["sku"]]["category"] in region["exempt"]
                                          for ln in resp["lines"])  # fmt: skip

        def ship_after_coupon(req: JsonDict, resp: JsonDict) -> bool:
            if not self._ok(resp) or resp["shipping"] == 0:
                return False
            merch = sum(ln["net"] for ln in resp["lines"]) - resp["bogo"] - resp["bundle"]
            return merch >= limits["shipping"]["free_threshold"]

        def window_edge(req: JsonDict, resp: JsonDict) -> bool:
            if not self._ok(resp) or resp["coupon"]["code"] is None:
                return False
            c = limits["coupons"][resp["coupon"]["code"]]
            return req["date"] in (c["valid_from"], c["valid_to"])

        def fractional(req: JsonDict, resp: JsonDict) -> bool:
            return self._ok(resp) and any(ln["discount"] and (ln["gross"] * 100) % 10_000
                                          for ln in resp["lines"]) and resp["tax"] > 0  # fmt: skip

        def age(req: JsonDict, resp: JsonDict) -> bool:
            return resp.get("error") == "restricted"

        return {
            "merge_duplicate_lines": lambda q, r: (
                self._ok(r) and len({i["sku"] for i in q["items"]}) < len(q["items"])
            ),
            "best_line_discount": multi_rule,
            "bogo_excludes_discounted": bogo_mix,
            "coupon_not_best": lambda q, r: (
                self._ok(r) and any(x["reason"] == "not_best" for x in r["rejected_coupons"])
            ),
            "discount_cap": lambda q, r: self._ok(r) and r["capped"],
            "tax_exempt": exempt,
            "free_shipping_after_coupon": ship_after_coupon,
            "age_restriction": age,
            "coupon_window_inclusive": window_edge,
            "rounding_mode": fractional,
        }

    def trap_probes(self, keys: Sequence[str]) -> list[JsonDict]:
        preds = self.predicates()
        out: list[JsonDict] = []
        for key in keys:
            found = [q for q in (self.search(preds[key]) for _ in range(2)) if q is not None]
            if not found:
                continue
            out.append(probe(f"trap-{key.replace('_', '-')}", scenario(self.limits, found),
                             trap=key))  # fmt: skip
        return out

    def perf(self, scale: int) -> JsonDict:
        quote = self.cart()
        load = jsonl_step({"op": "ping"}, match={"ok": True}, repeat=1)
        mix = [
            mix_entry(1.0, "quote", "quote", jsonl_step(quote, match={"ok": {"$type": "boolean"}}))
        ]
        return perf_spec(scale, load, mix)


@register_family
class RulesEngineFamily(Family):
    name = FAMILY_NAME
    contract = "jsonl"
    in_default_set = False

    def generate(
        self,
        seed: int,
        difficulty: Difficulty,
        language: str,
        allow: Sequence[str] | None = None,
    ) -> Instance:
        allow_list = resolve_allow(language, allow)
        rng = random.Random(seed)
        limits = _build_limits(rng, difficulty)
        keys = list(TRAP_KEYS)
        rng.shuffle(keys)
        builder = Probes(rng, limits)
        trap = builder.trap_probes(sorted(keys[: trap_count(difficulty.traps)],
                                          key=TRAP_KEYS.index))  # fmt: skip
        if not trap:  # the trap group may never be empty: fall back to the cheapest-to-find
            trap = builder.trap_probes(["merge_duplicate_lines", "rounding_mode"])
        probes = dsl.probes_doc(
            "jsonl", language, builder.core(RANDOM_PROBES_BY_LEVEL[difficulty.concurrency]),
            trap, builder.perf(difficulty.scale),
        )  # fmt: skip
        return Instance(
            family=self.name,
            seed=seed,
            difficulty=difficulty,
            language=language,
            contract="jsonl",
            entities=[],
            traps=[p["trap"] for p in trap],
            limits=limits,
            allow=allow_list,
            spec_md=render_spec(limits, language, allow_list),
            skeleton=skeleton_files("jsonl", language, allow_list, title="cart pricing engine"),
            probes=probes,
            reference=reference_files(REFERENCE_PROGRAM),
        )


__all__ = ["FAMILY_NAME", "RulesEngineFamily", "new_engine", "render_spec"]
