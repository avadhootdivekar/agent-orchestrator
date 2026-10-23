"""Name pools for instance generation (HLD §4.1 "Instance generation").

Names are drawn from these pools by the seeded RNG so memorised solutions ("the todo app") do not
transfer.  Constraints the generators rely on: every entity noun pluralises with a plain ``s``
(``/<noun>s``), and the field-name pools are disjoint per type so one entity never gets two fields
with the same name.
"""

from __future__ import annotations

ENTITY_NOUNS: tuple[str, ...] = (
    "project", "ticket", "invoice", "customer", "supplier", "shipment", "parcel", "warehouse",
    "course", "student", "booking", "venue", "artist", "album", "playlist", "recipe",
    "ingredient", "device", "sensor", "campaign", "survey", "vehicle", "driver", "route",
    "workshop", "tenant", "listing", "account", "order", "product", "review", "coupon",
    "member", "author", "book", "branch", "loan", "patient", "clinic", "appointment",
    "doctor", "pipeline", "release", "incident", "service", "team", "sprint", "contract",
    "vendor", "asset", "policy", "claim", "ledger", "meeting", "speaker", "session",
)  # fmt: skip

# Required, human-readable "label" field of every entity.
LABEL_FIELDS: tuple[str, ...] = ("name", "title", "label", "handle")

# (field name, minimum, maximum)
INT_FIELDS: tuple[tuple[str, int, int], ...] = (
    ("quantity", 0, 9999),
    ("priority", 1, 5),
    ("rank", 1, 100),
    ("weight", 0, 500),
    ("score", 0, 100),
    ("level", 1, 10),
    ("capacity", 1, 1000),
    ("retries", 0, 10),
)

BOOL_FIELDS: tuple[str, ...] = ("active", "archived", "flagged", "public", "verified", "pinned")

DATETIME_FIELDS: tuple[str, ...] = (
    "due_at", "starts_at", "expires_at", "published_at", "closed_at", "scheduled_at",
)  # fmt: skip

# Free-text optional string fields.
TEXT_FIELDS: tuple[str, ...] = ("note", "summary", "description", "comment", "city", "owner_name")

# (enum field name, candidate values)
ENUM_FIELDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("status", ("draft", "active", "paused", "closed", "archived", "pending", "blocked")),
    ("kind", ("standard", "premium", "trial", "internal", "partner", "legacy")),
    ("tier", ("bronze", "silver", "gold", "platinum", "basic", "plus")),
    ("category", ("hardware", "software", "services", "travel", "training", "facilities")),
    ("stage", ("intake", "review", "approved", "rejected", "shipped", "done", "waiting")),
)
