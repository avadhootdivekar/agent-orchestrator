"""Trap catalogue (HLD §4.1 "Intrinsic traps").

A trap is a behaviour that a plausible-but-naive implementation gets wrong.  The *key* is shared
across families (``invalid_input_4xx`` exists for both an HTTP and a JSONL contract); each family
owns the concrete requirement text and probe.  Descriptions here are for maintainers -- the
agent-visible ``SPEC.md`` states the behaviour as a plain requirement and never uses the word.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

# Trap-count by ``difficulty.traps`` level (0..3); never 0 so the `trap` group is never empty.
TRAP_COUNT_BY_LEVEL: tuple[int, ...] = (1, 2, 4, 6)


@dataclass(frozen=True)
class Trap:
    key: str
    description: str
    families: tuple[str, ...]
    requires: tuple[str, ...] = ()


_CRUD = "crud-relational"
_QUEUE = "job-queue"
_BOOK = "order-book"
_RULES = "rules-engine"
_ALGO = "algo-puzzle"
_BUG = "bug-hunt"  # reuses the order-book instance, traps and probes

TRAP_CATALOGUE: dict[str, Trap] = {
    t.key: t
    for t in (
        Trap(
            "pagination_stability",
            "Offset/page-number cursors skip or duplicate items when rows are deleted/inserted "
            "between page requests; a keyset cursor survives even deletion of the cursor row.",
            (_CRUD,),
        ),
        Trap(
            "unique_constraint",
            "Uniqueness enforced by read-then-write races under concurrency, or not enforced "
            "on PATCH, or blocks a self-PATCH.",
            (_CRUD,),
        ),
        Trap(
            "cascade_delete",
            "Cascade vs restrict on parent delete, all-or-nothing across the cascade closure.",
            (_CRUD,),
        ),
        Trap(
            "patch_null_vs_absent",
            "PATCH must distinguish an absent key (leave) from an explicit null (clear).",
            (_CRUD,),
        ),
        Trap(
            "idempotency_key",
            "POST with Idempotency-Key must be processed once, including concurrent duplicates.",
            (_CRUD,),
        ),
        Trap(
            "lost_update",
            "Concurrent conditional updates (If-Match/version) must not both win.",
            (_CRUD,),
        ),
        Trap(
            "invalid_input_4xx",
            "Strict validation: wrong types (bool-as-int), ranges, enums, unknown fields, bad "
            "limits/cursors must be rejected with the specified error, never coerced or 500.",
            (_CRUD, _QUEUE, _BOOK, _BUG),
        ),
        Trap(
            "sort_tiebreak",
            "Equal sort keys (same-millisecond created_at) need a deterministic id tiebreak.",
            (_CRUD,),
        ),
        Trap(
            "unicode_casefold",
            "Uniqueness is on the NFKC+casefold key (ß/SS, composed/decomposed accents), which "
            "lower() / COLLATE NOCASE do not give.",
            (_CRUD,),
            requires=("unique_constraint",),
        ),
        Trap(
            "visibility_timeout",
            "Leases on claim: boundary-inclusive expiry on an injected clock, stale receipts "
            "rejected after redelivery, per-claim timeout override.",
            (_QUEUE,),
        ),
        Trap(
            "at_least_once",
            "Expired leases redeliver and burn an attempt; the last attempt expiring moves the "
            "job to dead (never lost, never redelivered forever); acked jobs never return.",
            (_QUEUE,),
            requires=("visibility_timeout",),
        ),
        Trap(
            "duplicate_claim",
            "Concurrent claims must never hand the same job to two workers.",
            (_QUEUE,),
        ),
        Trap(
            "starvation",
            "Strict priority starves low-priority jobs; the spec'd fair-share rule must hold.",
            (_QUEUE,),
        ),
        Trap(
            "dedupe_key",
            "Enqueue with a live dedupe_key returns the existing job; terminal jobs free the key.",
            (_QUEUE,),
        ),
        # order-book (T-Pf4Mix): each is a plain requirement in SPEC.md; see gen/order_book.py.
        Trap(
            "fifo_within_level", "Same-price resting orders fill first-in-first-out.", (_BOOK, _BUG)
        ),
        Trap(
            "maker_price",
            "Trades execute at the resting order's price, not the taker's.",
            (_BOOK, _BUG),
        ),
        Trap(
            "partial_fill_rests",
            "A partially filled GTC limit rests its remainder at its "
            "limit, behind earlier orders at that price.",
            (_BOOK, _BUG),
        ),
        Trap("ioc_remainder", "IOC remainders are cancelled, never rested.", (_BOOK, _BUG)),
        Trap(
            "fok_atomic",
            "FOK is all-or-nothing with no side effects; own liquidity does not count.",
            (_BOOK, _BUG),
        ),
        Trap(
            "market_empty_book",
            "A market order with no liquidity is cancelled, not rested.",
            (_BOOK, _BUG),
        ),
        Trap(
            "post_only_cross",
            "Post-only orders that would trade are rejected, including on replace.",
            (_BOOK, _BUG),
        ),
        Trap(
            "replace_priority",
            "A same-price size reduction keeps time priority; any other "
            "replace loses it and may trade.",
            (_BOOK, _BUG),
        ),
        Trap(
            "self_trade",
            "Self-trade prevention policy (cancel resting / cancel incoming).",
            (_BOOK, _BUG),
        ),
        Trap("stop_cascade", "Stops trigger in placement order and can cascade.", (_BOOK, _BUG)),
        Trap(
            "price_improvement_refund",
            "A limit buy filled below its limit gets the difference back.",
            (_BOOK, _BUG),
        ),
        Trap(
            "cancel_releases_funds",
            "Cancelling releases exactly the unfilled reservation.",
            (_BOOK, _BUG),
        ),  # fmt: skip
        # rules-engine (T-Pf4Mix): long-spec interaction traps; see gen/rules_engine.py.
        Trap("merge_duplicate_lines", "Repeated SKUs merge into one line.", (_RULES,)),
        Trap("best_line_discount", "Only the largest applicable line rule applies.", (_RULES,)),
        Trap("bogo_excludes_discounted", "Buy-k-get-1 counts only undiscounted lines.", (_RULES,)),
        Trap("coupon_not_best", "Only the best percentage/fixed coupon applies.", (_RULES,)),
        Trap("discount_cap", "Cap reduces coupon, then bundle, then bogo.", (_RULES,)),
        Trap("tax_exempt", "Exempt categories are untaxed per region.", (_RULES,)),
        Trap(
            "free_shipping_after_coupon",
            "The free-shipping threshold uses merchandise after the coupon.",
            (_RULES,),
        ),
        Trap(
            "age_restriction", "Alcohol needs the region's minimum age (or is not sold).", (_RULES,)
        ),
        Trap("coupon_window_inclusive", "Coupon validity dates are inclusive.", (_RULES,)),
        Trap(
            "rounding_mode", "Every percentage uses the instance's rounding mode.", (_RULES,)
        ),  # fmt: skip
        # algo-puzzle (T-Pf4Mix): see gen/algo_puzzle.py.
        Trap("variant_semantics", "The seeded problem variant, not the textbook one.", (_ALGO,)),
        Trap("negative_values", "Negative values and bounds in range counting.", (_ALGO,)),
    )
}


def traps_for_family(family: str) -> list[str]:
    """Trap keys usable by *family*, in catalogue order."""
    return [t.key for t in TRAP_CATALOGUE.values() if family in t.families]


def trap_count(level: int) -> int:
    return TRAP_COUNT_BY_LEVEL[level]


def sample_traps(rng: random.Random, family: str, count: int) -> list[str]:
    """Pick about *count* traps for *family*, plus any trap they `require`.

    The shuffle is the first RNG consumption of a family's ``generate`` so, for one seed, a higher
    ``count`` yields a superset of a lower one.  Result is in catalogue order (stable spec/probe
    ordering).
    """
    candidates = traps_for_family(family)
    if not candidates:
        raise ValueError(f"no traps registered for family {family!r}")
    order = list(candidates)
    rng.shuffle(order)
    chosen: set[str] = set()
    for key in order:
        if len(chosen) >= count:
            break
        chosen.add(key)
        chosen.update(TRAP_CATALOGUE[key].requires)
    return [k for k in candidates if k in chosen]
