"""`crud-relational` family (HLD §4.1): relational entities behind an HTTP/JSON API.

``generate(seed, difficulty, language)`` draws an entity graph (1:N tree + optional M:N), typed
fields and the sampled traps, then derives three artefacts from that one description:

* ``SPEC.md`` -- precise prose + tables; the sampled traps appear as plain requirements;
* ``probes.json`` -- core (happy path per entity), trap (one probe per sampled trap), perf;
* the Python stdlib reference (``reference/crud_server.py``), parameterised by ``instance.json``.

Difficulty: ``entities`` -> 2/3/4/6 entities (+1 M:N from 3 up); ``traps`` -> see
``traps.TRAP_COUNT_BY_LEVEL``; ``concurrency`` -> parallel workers in the race probes (0 = none);
``scale`` -> perf dataset sizes (``dsl.PERF_SIZES_BY_SCALE``).
"""

from __future__ import annotations

import itertools
import random
from collections.abc import Sequence
from typing import Any

from . import dsl
from .base import Difficulty, Family, Instance, register_family
from .dsl import ISO_MS_RE, RAND_RE, UUID_RE, http_op, mix_entry, perf_spec, probe, step, tmpl
from .refs import reference_files
from .skeleton import allow_list_text, resolve_allow, run_contract_text, skeleton_files
from .traps import sample_traps, trap_count
from .wordlists import (
    BOOL_FIELDS,
    DATETIME_FIELDS,
    ENTITY_NOUNS,
    ENUM_FIELDS,
    INT_FIELDS,
    LABEL_FIELDS,
    TEXT_FIELDS,
)

FAMILY_NAME = "crud-relational"
ENTITY_COUNT_BY_LEVEL = (2, 3, 4, 6)
PAGE_SIZE_MAX_CHOICES = (25, 50, 100)
PAGE_SIZE_DEFAULT_CHOICES = (10, 20)
LABEL_MAX_LENGTH_CHOICES = (40, 60, 80)
TEXT_MAX_LENGTH_CHOICES = (100, 200, 300)
PERF_LIST_PAGE = 20
NIL_UUID = "00000000-0000-4000-8000-000000000000"
SAMPLE_DATETIMES = ("2026-03-15T09:30:00Z", "2027-01-02T03:04:05Z")
OPTIONAL_KINDS = ("int", "bool", "datetime", "text")
FIELD_TYPE_NAMES = {
    "string": "string",
    "int": "integer",
    "bool": "boolean",
    "datetime": "string (datetime)",
    "enum": "string (enum)",
}

JsonDict = dict[str, Any]


# ---------------------------------------------------------------------------------------------
# schema
# ---------------------------------------------------------------------------------------------


def _label_field(rng: random.Random) -> JsonDict:
    return {
        "name": rng.choice(LABEL_FIELDS),
        "type": "string",
        "required": True,
        "max_length": rng.choice(LABEL_MAX_LENGTH_CHOICES),
    }


def _enum_field(rng: random.Random) -> JsonDict:
    name, pool = rng.choice(ENUM_FIELDS)
    return {
        "name": name,
        "type": "enum",
        "required": True,
        "values": rng.sample(pool, rng.randint(3, 4)),
    }


def _optional_field(rng: random.Random, kind: str) -> JsonDict:
    if kind == "int":
        name, lo, hi = rng.choice(INT_FIELDS)
        return {"name": name, "type": "int", "required": False, "minimum": lo, "maximum": hi}
    if kind == "bool":
        return {"name": rng.choice(BOOL_FIELDS), "type": "bool", "required": False}
    if kind == "datetime":
        return {"name": rng.choice(DATETIME_FIELDS), "type": "datetime", "required": False}
    return {
        "name": rng.choice(TEXT_FIELDS),
        "type": "string",
        "required": False,
        "max_length": rng.choice(TEXT_MAX_LENGTH_CHOICES),
    }


def _build_entities(rng: random.Random, count: int, traps: Sequence[str]) -> list[JsonDict]:
    names = rng.sample(ENTITY_NOUNS, count)
    entities: list[JsonDict] = []
    for index, name in enumerate(names):
        fields = [_label_field(rng), _enum_field(rng)]
        kinds = (
            list(OPTIONAL_KINDS)
            if index == 0
            else [k for k in OPTIONAL_KINDS if k in rng.sample(OPTIONAL_KINDS, rng.randint(1, 3))]
        )
        fields.extend(_optional_field(rng, kind) for kind in kinds)
        entities.append({"name": name, "plural": name + "s", "fields": fields, "relations": []})
    # 1:N tree: every entity after the first belongs to one earlier entity.
    belongs: list[JsonDict] = []
    for index in range(1, count):
        parent = names[rng.randrange(index)]
        rel = {
            "kind": "belongs_to",
            "parent": parent,
            "fk": f"{parent}_id",
            "on_delete": rng.choice(("cascade", "restrict")),
        }
        entities[index]["relations"].append(rel)
        belongs.append(rel)
    if "cascade_delete" in traps:
        cascade_at = rng.randrange(len(belongs))
        belongs[cascade_at]["on_delete"] = "cascade"
        if len(belongs) > 1:
            restrict_at = rng.randrange(len(belongs) - 1)
            belongs[restrict_at + (restrict_at >= cascade_at)]["on_delete"] = "restrict"
    if count >= 3:
        # optionally a many-to-many link between two entities that are not already parent/child
        # (a tree with >= 3 nodes always has such a pair)
        linked = {(rel["parent"], child["name"]) for child in entities
                  for rel in child["relations"] if rel["kind"] == "belongs_to"}  # fmt: skip
        candidates = [
            (a, b)
            for a, b in itertools.combinations(range(count), 2)
            if (names[a], names[b]) not in linked and (names[b], names[a]) not in linked
        ]
        a, b = rng.choice(candidates)
        entities[a]["relations"].append({"kind": "many_to_many", "other": names[b]})
        entities[b]["relations"].append({"kind": "many_to_many", "other": names[a]})
    if "unique_constraint" in traps:
        unique_at = rng.randrange(count)
        for index, entity in enumerate(entities):
            label = entity["fields"][0]
            if index == unique_at or rng.random() < 0.5:
                label["unique"] = True
            if index == unique_at and "unicode_casefold" in traps:
                label["unique_casefold"] = True
    return entities


def _build_limits(rng: random.Random, difficulty: Difficulty) -> JsonDict:
    page_max = rng.choice(PAGE_SIZE_MAX_CHOICES)
    return {
        "page_size_max": page_max,
        "page_size_default": min(rng.choice(PAGE_SIZE_DEFAULT_CHOICES), page_max),
        "max_records": dsl.PERF_SIZES_BY_SCALE[difficulty.scale][-1],
        "parallel_workers": dsl.WORKERS_BY_CONCURRENCY[difficulty.concurrency],
    }


# ---------------------------------------------------------------------------------------------
# schema accessors shared by the spec and the probes
# ---------------------------------------------------------------------------------------------


class Schema:
    def __init__(self, entities: list[JsonDict], limits: JsonDict, traps: Sequence[str]) -> None:
        self.entities = entities
        self.by_name = {e["name"]: e for e in entities}
        self.limits = limits
        self.traps = list(traps)

    @property
    def root(self) -> JsonDict:
        return self.entities[0]

    def path(self, name: str) -> str:
        return "/" + self.by_name[name]["plural"]

    def belongs_to(self, name: str) -> list[JsonDict]:
        return [r for r in self.by_name[name]["relations"] if r["kind"] == "belongs_to"]

    def many_to_many(self) -> list[tuple[str, str]]:
        pairs = []
        for e in self.entities:
            for r in e["relations"]:
                if r["kind"] == "many_to_many" and e["name"] < r["other"]:
                    pairs.append((e["name"], r["other"]))
        return pairs

    def cascade_relations(self) -> list[tuple[str, JsonDict]]:
        """(child, relation) for every belongs_to, in entity order."""
        return [(e["name"], r) for e in self.entities for r in self.belongs_to(e["name"])]

    def label(self, name: str) -> JsonDict:
        return self.by_name[name]["fields"][0]

    def enum(self, name: str) -> JsonDict:
        return self.by_name[name]["fields"][1]

    def optional(self, name: str, type_: str) -> JsonDict | None:
        """First optional (non-label, non-enum) field of *type_*, if the entity has one."""
        for f in self.by_name[name]["fields"][2:]:
            if f["type"] == type_:
                return f
        return None

    def unique_entity(self) -> JsonDict | None:
        for e in self.entities:
            if self.label(e["name"]).get("unique_casefold"):
                return e
        for e in self.entities:
            if self.label(e["name"]).get("unique"):
                return e
        return None

    def filterable(self, name: str) -> list[str]:
        out = [f["name"] for f in self.by_name[name]["fields"] if f["type"] != "datetime"]
        out.extend(r["fk"] for r in self.belongs_to(name))
        return out


# ---------------------------------------------------------------------------------------------
# SPEC.md
# ---------------------------------------------------------------------------------------------


def _md_table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    lines.extend("| " + " | ".join(r) + " |" for r in rows)
    return "\n".join(lines)


def _constraint(f: JsonDict, schema: Schema, entity: str) -> str:
    t = f["type"]
    parts: list[str] = []
    if t == "string":
        parts.append(f"1 to {f['max_length']} characters")
    elif t == "int":
        parts.append(f"integer from {f['minimum']} to {f['maximum']}")
    elif t == "enum":
        parts.append("one of " + ", ".join(f"`{v}`" for v in f["values"]))
    elif t == "datetime":
        parts.append("`YYYY-MM-DDTHH:MM:SSZ` (UTC)")
    else:
        parts.append("`true` or `false`")
    if f.get("unique"):
        parts.append("unique among all " + schema.by_name[entity]["plural"])
    return "; ".join(parts)


def _entity_section(schema: Schema, e: JsonDict) -> str:
    name = e["name"]
    rows = [
        ["`id`", "string", "server", "UUID v4, lowercase, canonical form; assigned on creation"],
        ["`created_at`", "string", "server", "UTC time of creation, millisecond precision"],
        ["`version`", "integer", "server", "1 on creation; +1 on every successful PATCH"],
    ]
    for rel in schema.belongs_to(name):
        rows.append(
            [
                f"`{rel['fk']}`",
                "string",
                "yes",
                f"`id` of an existing `{rel['parent']}`; cannot be changed after creation",
            ]
        )
    for f in e["fields"]:
        type_name = FIELD_TYPE_NAMES[f["type"]]
        rows.append(
            [
                f"`{f['name']}`",
                type_name,
                "yes" if f["required"] else "no (default `null`)",
                _constraint(f, schema, name),
            ]
        )
    text = f"### `{name}` (collection `{schema.path(name)}`)\n\n"
    text += _md_table(["Field", "Type", "Required", "Rules"], rows)
    text += "\n"
    return text


def _relations_section(schema: Schema) -> str:
    lines = []
    rel_rows = []
    for child, rel in schema.cascade_relations():
        effect = (
            f"deleting a `{rel['parent']}` also deletes all its `{child}` records"
            if rel["on_delete"] == "cascade"
            else f"a `{rel['parent']}` that still has `{child}` records cannot be deleted "
            "(409 `conflict`)"
        )
        rel_rows.append(
            [
                f"`{rel['parent']}` 1 : N `{child}`",
                f"`{child}.{rel['fk']}`",
                rel["on_delete"],
                effect,
            ]
        )
    if rel_rows:
        lines.append(_md_table(["Relation", "Stored as", "On delete", "Effect"], rel_rows))
    for a, b in schema.many_to_many():
        lines.append(
            f"`{a}` N : M `{b}`: records are linked and unlinked through the endpoints below. "
            f"Deleting either record removes its links (it is never blocked by them)."
        )
    return "\n\n".join(lines)


def _endpoint_rows(schema: Schema) -> list[list[str]]:
    rows = [["GET", "/healthz", '200 `{"status": "ok"}`', "Readiness"]]
    for e in schema.entities:
        p, n = schema.path(e["name"]), e["name"]
        rows += [
            ["POST", p, f"201 the new `{n}`", "Create. Body: the writable fields above"],
            ["GET", p, '200 `{"items": [...], "next_cursor": ...}`', f"List `{n}` records"],
            ["GET", f"{p}/{{id}}", f"200 the `{n}`; 404", "Fetch one"],
            ["PATCH", f"{p}/{{id}}", f"200 the updated `{n}`; 404", "Partial update"],
            ["DELETE", f"{p}/{{id}}", "204 no body; 404", "Delete"],
        ]
    for a, b in schema.many_to_many():
        pa, pb = schema.path(a), schema.path(b)
        for x, y, px, py in ((a, b, pa, pb), (b, a, pb, pa)):
            rows += [
                ["PUT", f"{px}/{{id}}{py}/{{other_id}}", "204 no body; 404",
                 f"Link a `{x}` to a `{y}` (idempotent; 404 if either does not exist)"],
                ["DELETE", f"{px}/{{id}}{py}/{{other_id}}", "204 no body; 404",
                 "Remove the link (404 if it does not exist)"],
                ["GET", f"{px}/{{id}}{py}", "200 `{\"items\": [...], \"next_cursor\": ...}`",
                 f"The `{y}` records linked to a `{x}`, same paging and order as a collection "
                 "(no filters)"],
            ]  # fmt: skip
    return rows


def _error_rows(schema: Schema) -> list[list[str]]:
    rows = [
        ["400", "`invalid_request`", "The request is invalid in any way (see the field rules)"],
        ["404", "`not_found`", "Unknown id, unknown route, or removing a link that does not exist"],
    ]
    has_restrict = any(r["on_delete"] == "restrict" for _, r in schema.cascade_relations())
    if "unique_constraint" in schema.traps or has_restrict:
        rows.append(["409", "`conflict`", "A uniqueness rule or a delete restriction is violated"])
    if "lost_update" in schema.traps:
        rows.append(["412", "`precondition_failed`", "`If-Match` does not match the version"])
    if "idempotency_key" in schema.traps:
        rows.append(
            ["422", "`idempotency_key_reuse`", "`Idempotency-Key` reused with a different body"]
        )
    return rows


def _trap_requirements(schema: Schema) -> list[str]:
    traps = schema.traps
    limits = schema.limits
    out: list[str] = []
    if "pagination_stability" in traps:
        out.append(
            "**Stable pagination.** Following `next_cursor` from the first page to the last "
            "returns every record that exists for the whole traversal exactly once and in list "
            "order, whatever is created or deleted between the page requests -- including "
            "deleting the last record of the page the cursor came from. A cursor therefore "
            "cannot be an offset or position into the collection and cannot depend on the record "
            "it was taken from still existing."
        )
    if "unique_constraint" in traps:
        uniques = [
            f"`{e['name']}.{schema.label(e['name'])['name']}`"
            for e in schema.entities
            if schema.label(e["name"]).get("unique")
        ]
        out.append(
            f"**Uniqueness.** {', '.join(uniques)} must be unique among live records. A POST or "
            "PATCH that would give two records the same value is rejected with 409 `conflict` "
            "and changes nothing. PATCHing a record to the value it already has is not a "
            "conflict. Deleting a record frees its value. The rule holds under concurrency: if "
            "many requests try to use the same value at the same moment, exactly one succeeds "
            "and every other one receives 409."
        )
    if "unicode_casefold" in traps:
        e = schema.unique_entity()
        assert e is not None
        out.append(
            f"**Caseless uniqueness.** Uniqueness of "
            f"`{e['name']}.{schema.label(e['name'])['name']}` is decided on a comparison key, "
            "not on the raw string: key(s) = NFKC(casefold(NFKC(s))) "
            "where NFKC is Unicode compatibility composition and casefold is full Unicode case "
            "folding. So `Straße` and `STRASSE` collide, and so do `Café` (precomposed `é`) and "
            "`CAFÉ` written as `E` followed by U+0301 (combining acute accent). The stored and "
            "returned value is exactly what the client sent; only the comparison uses the key."
        )
    if "cascade_delete" in traps:
        out.append(
            "**All-or-nothing delete.** A DELETE removes the record and everything that cascades "
            "from it, or nothing at all: if any record that would be removed by cascading has "
            "children under a `restrict` relation, the whole DELETE fails with 409 `conflict` and "
            "no record is deleted."
        )
    if "patch_null_vs_absent" in traps:
        out.append(
            "**PATCH semantics.** PATCH changes only the fields present in the body: an absent "
            "field keeps its value. A field present with the value `null` sets an optional field "
            "back to `null`; `null` for a required field is a 400. An empty body `{}` is a valid "
            "PATCH that changes nothing (the version still increases)."
        )
    if "idempotency_key" in traps:
        out.append(
            "**Idempotent creation.** POST accepts an `Idempotency-Key` header (1 to 255 "
            "characters). The first request with a given key (per collection) is processed "
            "normally. Every later request with the same key and an identical JSON body creates "
            "nothing and returns the original response (same status, same body) -- including a "
            "request that arrives while the first one is still being processed. The same key with "
            "a different body is rejected with 422 `idempotency_key_reuse`. Requests without the "
            "header are never deduplicated."
        )
    if "lost_update" in traps:
        out.append(
            "**Conditional update.** PATCH accepts an `If-Match` header holding a version number "
            "(decimal digits, optionally in double quotes). If it differs from the record's "
            "current `version`, the response is 412 `precondition_failed` and nothing changes. "
            "When several PATCH requests carrying the same `If-Match` race each other, exactly "
            "one of them succeeds and the rest get 412. Without the header, PATCH is "
            "unconditional."
        )
    if "invalid_input_4xx" in traps:
        out.append(
            "**Strict validation.** Invalid input is never coerced, truncated or ignored and "
            "never causes a server error: the response is 400 `invalid_request` and nothing "
            "changes. This covers: a body that is not a JSON object; a missing required field; "
            "`null` for a required field; an unknown or read-only field (`id`, `created_at`, "
            "`version`, relation ids on PATCH); a value of the wrong JSON type (a boolean, a "
            "float or a numeric string for an integer field; a number for a string field; a "
            "string for a boolean field); an integer outside its range; a value that is not one "
            "of the enum values; an empty or too long string; a datetime that is malformed, "
            "impossible (month 13) or not in the exact `YYYY-MM-DDTHH:MM:SSZ` form; a relation id "
            "that is not the id of an existing record; `limit` that is not an integer from 1 to "
            f"{limits['page_size_max']}; an undecodable `cursor`; `order` other than `asc`/`desc`; "
            "an unknown query parameter; a filter value of the wrong type."
        )
    if "sort_tiebreak" in traps:
        out.append(
            "**Total order.** Records with the same `created_at` (several can be created within "
            "one millisecond) are ordered by `id`, compared as plain strings, ascending -- and "
            "descending when `order=desc`. The order of a list is therefore total and identical "
            "on every request."
        )
    return out


def render_spec(schema: Schema, language: str, allow: Sequence[str]) -> str:
    names = ", ".join(f"`{e['name']}`" for e in schema.entities)
    limits = schema.limits
    parts = [
        f"# Specification: JSON API for {names}",
        "",
        "Implement the HTTP service described here, in this directory. Everything in this document "
        "is a requirement. The service is checked only through its HTTP interface.",
        "",
        "## 1. Run contract",
        "",
        run_contract_text("http", language),
        "",
        "## 2. Allowed dependencies",
        "",
        allow_list_text(language, allow),
        "",
        "## 3. Conventions",
        "",
        "- All request and response bodies are JSON objects. Unknown routes answer 404.",
        "- Timestamps (`created_at`) are UTC ISO-8601 with millisecond precision, e.g. "
        "`2026-01-15T09:30:00.123Z`.",
        "- Datetime fields use the form `YYYY-MM-DDTHH:MM:SSZ`, must be a real calendar date and "
        "time, and are stored and returned unchanged.",
        "- String values are stored and returned exactly as sent (no trimming, no case change). "
        "Integer values are JSON integers; booleans are JSON `true`/`false`.",
        "- Server-assigned fields (`id`, `created_at`, `version`) are always present in every "
        "record; optional fields that were never set are present with the value `null`.",
        '- Error responses carry a JSON body `{"error": {"code": "<code>", "message": '
        '"<text>"}}` and one of these statuses:',
        "",
        _md_table(["Status", "`error.code`", "When"], _error_rows(schema)),
        "",
        "## 4. Entities",
        "",
    ]
    for e in schema.entities:
        parts += [_entity_section(schema, e), ""]
    rel = _relations_section(schema)
    if rel:
        parts += ["### Relations", "", rel, ""]
    parts += [
        "## 5. Endpoints",
        "",
        "`PATCH` bodies may contain any subset of the writable fields (not the relation ids). "
        "`POST` bodies may contain every writable field including the relation id(s), and must "
        "contain the required ones.",
        "",
        _md_table(["Method", "Path", "Success", "Notes"], _endpoint_rows(schema)),
        "",
        "## 6. Listing",
        "",
        f'- `GET /<collection>?limit=&cursor=&order=` returns `{{"items": [...], '
        f'"next_cursor": <string or null>}}`. `limit` is an integer from 1 to '
        f"{limits['page_size_max']} (default {limits['page_size_default']}). `next_cursor` is "
        "`null` on the last page; otherwise passing it as `cursor` (with the same other query "
        "parameters) returns the next page. Clients treat cursors as opaque.",
        "- Order: `created_at` ascending (oldest first); `order=desc` reverses it. The order is "
        "deterministic: the same request returns the same order every time.",
        "- Filters: any of the following query parameters restricts the list to records whose "
        "field equals the value (several filters combine with AND; values are compared exactly; "
        "integers are decimal digits and booleans are `true`/`false`; a value that matches nothing "
        "gives an empty list). Filters apply before paging.",
        "",
    ]
    for e in schema.entities:
        fields = ", ".join(f"`{n}`" for n in schema.filterable(e["name"]))
        parts.append(f"  - `{schema.path(e['name'])}`: {fields}")
    extras = _trap_requirements(schema)
    if extras:
        parts += ["", "## 7. Additional requirements", ""]
        parts += [f"{i}. {text}" for i, text in enumerate(extras, 1)]
    parts += [
        "",
        f"## {8 if extras else 7}. Scale",
        "",
        f"Collections may grow to {limits['max_records']:,} records. Fetching a record by id, "
        "creating a record and fetching one page of a list must not take longer as the "
        "collection grows: do not scan or sort a whole collection to serve those requests.",
        "",
    ]
    return "\n".join(parts)


# ---------------------------------------------------------------------------------------------
# probes
# ---------------------------------------------------------------------------------------------


class Probes:
    """Builds the probe set for one schema (see bench-probe-dsl.md)."""

    def __init__(self, schema: Schema, difficulty: Difficulty) -> None:
        self.s = schema
        self.workers = dsl.WORKERS_BY_CONCURRENCY[difficulty.concurrency]
        self.page_max = schema.limits["page_size_max"]
        self.difficulty = difficulty

    # ---- bodies -----------------------------------------------------------------------------
    def value(self, f: JsonDict, variant: int = 0) -> Any:
        t = f["type"]
        if t == "string":
            return f"sample text {variant}"
        if t == "int":
            lo, hi = f["minimum"], f["maximum"]
            return lo + min(2 * variant + 1, hi - lo)
        if t == "bool":
            return variant % 2 == 0
        if t == "datetime":
            return SAMPLE_DATETIMES[variant % len(SAMPLE_DATETIMES)]
        return f["values"][variant % len(f["values"])]

    def body(
        self,
        entity: str,
        fk_vars: dict[str, str],
        *,
        variant: int = 0,
        label: str | None = None,
        omit: Sequence[str] = (),
    ) -> JsonDict:
        e = self.s.by_name[entity]
        body: JsonDict = {}
        for rel in self.s.belongs_to(entity):
            body[rel["fk"]] = tmpl(fk_vars[rel["parent"]])
        for f in e["fields"]:
            if f["name"] in omit:
                continue
            if f is e["fields"][0]:
                body[f["name"]] = label if label is not None else f"{entity}-{tmpl('rand')}"
            else:
                body[f["name"]] = self.value(f, variant)
        return body

    def expected(
        self,
        entity: str,
        fk_vars: dict[str, str],
        *,
        variant: int = 0,
        label: str | None = None,
        omit: Sequence[str] = (),
        version: int = 1,
        overrides: JsonDict | None = None,
    ) -> JsonDict:
        e = self.s.by_name[entity]
        m: JsonDict = {
            "id": {"$regex": UUID_RE},
            "created_at": {"$regex": ISO_MS_RE},
            "version": version,
        }
        for rel in self.s.belongs_to(entity):
            m[rel["fk"]] = {"$var": fk_vars[rel["parent"]]}
        for f in e["fields"]:
            if f["name"] in omit:
                m[f["name"]] = None
            elif f is e["fields"][0]:
                m[f["name"]] = {"$regex": f"^{entity}-{RAND_RE}$"} if label is None else label
            else:
                m[f["name"]] = self.value(f, variant)
        m.update(overrides or {})
        return m

    # ---- creation helpers -------------------------------------------------------------------
    def create(
        self,
        entity: str,
        tag: str,
        fk_vars: dict[str, str],
        *,
        variant: int = 0,
        label: str | None = None,
        omit: Sequence[str] = (),
        status: int | list[int] = 201,
        save_id: bool = True,
        repeat: int | None = None,
        full_match: bool = True,
    ) -> JsonDict:
        match = None
        if full_match and status == 201:
            match = self.expected(entity, fk_vars, variant=variant, label=label, omit=omit)
        return step(
            http_op(
                "POST",
                self.s.path(entity),
                self.body(entity, fk_vars, variant=variant, label=label, omit=omit),
            ),
            status=status,
            match=match,
            save={f"{tag}_{entity}": "$.id"} if save_id else None,
            repeat=repeat,
        )

    def chain(
        self,
        entity: str,
        tag: str,
        *,
        variant: int = 0,
        label: str | None = None,
        omit: Sequence[str] = (),
    ) -> tuple[list[JsonDict], dict[str, str]]:
        """Steps creating the ancestors of *entity* (root first) and then *entity* itself.
        Returns the steps and the ``{entity: var}`` map of every record created (ids)."""
        steps: list[JsonDict] = []
        vars_: dict[str, str] = {}
        for rel in self.s.belongs_to(entity):
            parent_steps, parent_vars = self.chain(rel["parent"], tag)
            steps += parent_steps
            vars_.update(parent_vars)
        steps.append(self.create(entity, tag, dict(vars_), variant=variant, label=label, omit=omit))
        vars_[entity] = f"{tag}_{entity}"
        return steps, vars_

    def get(self, entity: str, var: str, *, status: int = 200, match: Any = None) -> JsonDict:
        return step(
            http_op("GET", f"{self.s.path(entity)}/{tmpl(var)}"), status=status, match=match
        )

    def list_(
        self, entity: str, query: str = "", *, match: Any = None, save: JsonDict | None = None
    ) -> JsonDict:
        path = self.s.path(entity) + (f"?{query}" if query else "")
        return step(http_op("GET", path), status=200, match=match, save=save)

    @staticmethod
    def error(code: str) -> JsonDict:
        return {"error": {"code": code, "message": {"$type": "string"}}}

    # ---- core -------------------------------------------------------------------------------
    def core(self) -> list[JsonDict]:
        s = self.s
        out: list[JsonDict] = [
            probe(
                "core-healthz",
                [step(http_op("GET", "/healthz"), status=200, match={"status": "ok"})],
                fresh_state=False,
            ),
            probe(
                "core-error-shape",
                [
                    step(
                        http_op("GET", f"{s.path(s.root['name'])}/{NIL_UUID}"),
                        status=404,
                        match=self.error("not_found"),
                    )
                ],
                fresh_state=False,
            ),
        ]
        for e in s.entities:
            out += self._core_entity(e["name"])
        out.append(self._core_pagination())
        for a, b in s.many_to_many():
            out.append(self._core_m2n(a, b))
        return out

    def _core_entity(self, name: str) -> list[JsonDict]:
        s = self.s
        e = s.by_name[name]
        omit = [e["fields"][-1]["name"]] if not e["fields"][-1]["required"] else []
        path = s.path(name)
        # create + get
        steps, v = self.chain(name, "a", omit=omit)
        fk_vars = {p: x for p, x in v.items() if p != name}
        want = self.expected(name, fk_vars, omit=omit)
        want["id"] = {"$var": v[name]}
        steps += [
            self.get(name, v[name], match=want),
            step(http_op("GET", f"{path}/{NIL_UUID}"), status=404),
        ]
        create_get = probe(f"core-{name}-create-get", steps, fresh_state=False)
        # patch
        steps, v = self.chain(name, "a")
        fk_vars = {p: x for p, x in v.items() if p != name}
        patch_body: JsonDict = {e["fields"][1]["name"]: self.value(e["fields"][1], 1)}
        changed: JsonDict = dict(patch_body)
        for f in e["fields"][2:3]:
            patch_body[f["name"]] = self.value(f, 1)
            changed[f["name"]] = self.value(f, 1)
        want = self.expected(name, fk_vars, version=2, overrides=changed)
        want["id"] = {"$var": v[name]}
        steps += [
            step(http_op("PATCH", f"{path}/{tmpl(v[name])}", patch_body), status=200, match=want),
            self.get(name, v[name], match=want),
            step(http_op("PATCH", f"{path}/{NIL_UUID}", patch_body), status=404),
        ]
        patch = probe(f"core-{name}-patch", steps, fresh_state=False)
        # delete
        steps, v = self.chain(name, "a")
        steps += [
            step(http_op("DELETE", f"{path}/{tmpl(v[name])}"), status=204),
            self.get(name, v[name], status=404),
            step(http_op("DELETE", f"{path}/{tmpl(v[name])}"), status=404),
        ]
        delete = probe(f"core-{name}-delete", steps, fresh_state=False)
        return [create_get, patch, delete, self._core_list_filter(name)]

    def _core_list_filter(self, name: str) -> JsonDict:
        s = self.s
        enum = s.enum(name)
        parents = s.belongs_to(name)
        steps, v = self.chain(name, "a", variant=0)
        fk_vars = {p: x for p, x in v.items() if p != name}
        steps.append(self.create(name, "a2", fk_vars, variant=1, full_match=False))
        steps.append(self.create(name, "a3", fk_vars, variant=0, full_match=False))
        total = 3
        if parents:
            other_steps, _ = self.chain(name, "b", variant=1)
            steps += other_steps
            total = 4
        e0, e1 = enum["values"][0], enum["values"][1 % len(enum["values"])]
        n_e1 = 1 + (1 if parents else 0)
        steps += [
            self.list_(name, f"limit={self.page_max}", match={"items": {"$len": total}}),
            self.list_(
                name, f"{enum['name']}={e0}&limit={self.page_max}", match={"items": {"$len": 2}}
            ),
            self.list_(
                name,
                f"{enum['name']}={e0}&limit={self.page_max}",
                match={"items": {"$each": {enum["name"]: e0}}},
            ),
            self.list_(
                name, f"{enum['name']}={e1}&limit={self.page_max}", match={"items": {"$len": n_e1}}
            ),
        ]
        if parents:
            fk = parents[0]["fk"]
            pvar = v[parents[0]["parent"]]
            steps += [
                self.list_(
                    name, f"{fk}={tmpl(pvar)}&limit={self.page_max}", match={"items": {"$len": 3}}
                ),
                self.list_(
                    name,
                    f"{fk}={tmpl(pvar)}&{enum['name']}={e0}&limit={self.page_max}",
                    match={"items": {"$len": 2}},
                ),
            ]
        return probe(f"core-{name}-list-filter", steps)

    def _core_pagination(self) -> JsonDict:
        root = self.s.root["name"]
        n = 7
        ids = {f"p{i}": f"$.items[{i}].id" for i in range(n)}

        def only(*names: str) -> JsonDict:
            return {"items": [{"id": {"$var": x}} for x in names]}

        steps = [
            self.create(root, "x", {}, save_id=False, repeat=n, full_match=False),
            self.list_(root, f"limit={self.page_max}", match={"items": {"$len": n}}, save=ids),
            self.list_(
                root,
                f"limit={self.page_max}",
                match={"items": {"$sorted_by": "created_at", "order": "asc"}},
            ),
            self.list_(
                root,
                f"limit={self.page_max}&order=desc",
                match={"items": {"$sorted_by": "created_at", "order": "desc"}},
            ),
            self.list_(
                root,
                "limit=3",
                match={**only("p0", "p1", "p2"), "next_cursor": {"$type": "string"}},
                save={"c1": "$.next_cursor"},
            ),
            self.list_(
                root,
                "limit=3&cursor={{c1}}",
                match={**only("p3", "p4", "p5"), "next_cursor": {"$type": "string"}},
                save={"c2": "$.next_cursor"},
            ),
            self.list_(root, "limit=3&cursor={{c2}}", match={**only("p6"), "next_cursor": None}),
        ]
        return probe("core-pagination-basic", steps)

    def _core_m2n(self, a: str, b: str) -> JsonDict:
        s = self.s
        steps_a, va = self.chain(a, "a")
        steps_b, vb = self.chain(b, "b")
        pa, pb = s.path(a), s.path(b)
        link = f"{pa}/{tmpl(va[a])}{pb}/{tmpl(vb[b])}"
        steps = (
            steps_a
            + steps_b
            + [
                step(http_op("PUT", link), status=204),
                step(http_op("PUT", link), status=204),
                step(
                    http_op("GET", f"{pa}/{tmpl(va[a])}{pb}"),
                    status=200,
                    match={"items": [{"id": {"$var": vb[b]}}], "next_cursor": None},
                ),
                step(
                    http_op("GET", f"{pb}/{tmpl(vb[b])}{pa}"),
                    status=200,
                    match={"items": [{"id": {"$var": va[a]}}], "next_cursor": None},
                ),
                step(http_op("DELETE", link), status=204),
                step(
                    http_op("GET", f"{pa}/{tmpl(va[a])}{pb}"),
                    status=200,
                    match={"items": {"$len": 0}, "next_cursor": None},
                ),
                step(http_op("DELETE", link), status=404),
                step(http_op("PUT", f"{pa}/{tmpl(va[a])}{pb}/{NIL_UUID}"), status=404),
            ]
        )
        return probe(f"core-{a}-{b}-links", steps, fresh_state=False)

    # ---- traps ------------------------------------------------------------------------------
    def traps(self) -> list[JsonDict]:
        builders = {
            "pagination_stability": self._t_pagination,
            "unique_constraint": self._t_unique,
            "unicode_casefold": self._t_casefold,
            "cascade_delete": self._t_cascade,
            "patch_null_vs_absent": self._t_patch_null,
            "idempotency_key": self._t_idempotency,
            "lost_update": self._t_lost_update,
            "invalid_input_4xx": self._t_invalid,
            "sort_tiebreak": self._t_sort,
        }
        return [builders[t]() for t in self.s.traps]

    def _t_pagination(self) -> JsonDict:
        root = self.s.root["name"]
        ids = {f"p{i}": f"$.items[{i}].id" for i in range(6)}

        def only(*names: str) -> JsonDict:
            return {"items": [{"id": {"$var": x}} for x in names]}

        path = self.s.path(root)
        steps = [
            self.create(root, "x", {}, save_id=False, repeat=6, full_match=False),
            self.list_(root, f"limit={self.page_max}", match={"items": {"$len": 6}}, save=ids),
            self.list_(root, "limit=3", match=only("p0", "p1", "p2"), save={"c1": "$.next_cursor"}),
            step(http_op("DELETE", f"{path}/{{{{p2}}}}"), status=204),
            self.list_(
                root, "limit=3&cursor={{c1}}", match={**only("p3", "p4", "p5"), "next_cursor": None}
            ),
            self.list_(
                root,
                "limit=3&order=desc",
                match=only("p5", "p4", "p3"),
                save={"c2": "$.next_cursor"},
            ),
            self.create(root, "n", {}, full_match=False),
            self.list_(
                root,
                "limit=3&order=desc&cursor={{c2}}",
                match={**only("p1", "p0"), "next_cursor": None},
            ),
        ]
        return probe("trap-pagination-stability", steps, trap="pagination_stability")

    def _t_unique(self) -> JsonDict:
        u = self.s.unique_entity()
        assert u is not None
        name = u["name"]
        label = self.s.label(name)["name"]
        path = self.s.path(name)
        steps, v = self.chain(name, "a", label="dup-one")
        fk_vars = {p: x for p, x in v.items() if p != name}
        steps += [
            self.create(name, "dupe", fk_vars, label="dup-one", status=409, save_id=False),
            self.create(name, "b", fk_vars, label="dup-two"),
            step(http_op("PATCH", f"{path}/{tmpl('b_' + name)}", {label: "dup-one"}), status=409),
            step(http_op("PATCH", f"{path}/{tmpl(v[name])}", {label: "dup-one"}), status=200),
            step(http_op("PATCH", f"{path}/{tmpl('b_' + name)}", {label: "dup-three"}), status=200),
            step(http_op("DELETE", f"{path}/{tmpl(v[name])}"), status=204),
            self.create(name, "c", fk_vars, label="dup-one"),
        ]
        parallel = after = None
        if self.workers:
            race = step(
                http_op("POST", path, self.body(name, fk_vars, label="race-one")), status=[201, 409]
            )
            parallel = [[dict(race)] for _ in range(self.workers)]
            after = [self.list_(name, f"{label}=race-one", match={"items": {"$len": 1}})]
        return probe(
            "trap-unique-constraint",
            steps,
            trap="unique_constraint",
            parallel=parallel,
            after=after,
        )

    def _t_casefold(self) -> JsonDict:
        u = self.s.unique_entity()
        assert u is not None
        name = u["name"]
        steps, v = self.chain(name, "a", label="Straße")
        fk_vars = {p: x for p, x in v.items() if p != name}
        clash = [("STRASSE", 409), ("strasse", 409), ("Café", 201), ("CAFÉ", 409),
                 ("Strasse-two", 201)]  # fmt: skip
        steps += [
            self.create(
                name, f"k{i}", fk_vars, label=lbl, status=st, save_id=False, full_match=False
            )
            for i, (lbl, st) in enumerate(clash)
        ]
        return probe("trap-unicode-casefold", steps, trap="unicode_casefold")

    def _t_cascade(self) -> JsonDict:
        s = self.s
        relations = s.cascade_relations()
        cascades = [(c, r) for c, r in relations if r["on_delete"] == "cascade"]
        restricts = [(c, r) for c, r in relations if r["on_delete"] == "restrict"]
        child, rel = cascades[0]
        parent = rel["parent"]
        steps, v = self.chain(child, "c")
        fk_vars = {p: x for p, x in v.items() if p != child}
        steps.append(self.create(child, "c2", fk_vars, full_match=False))
        steps += [
            step(http_op("DELETE", f"{s.path(parent)}/{tmpl(v[parent])}"), status=204),
            self.get(child, v[child], status=404),
            self.get(child, f"c2_{child}", status=404),
            self.get(parent, v[parent], status=404),
        ]
        if restricts:
            rchild, rrel = restricts[0]
            rparent = rrel["parent"]
            rsteps, rv = self.chain(rchild, "r")
            steps += rsteps
            steps += [
                step(http_op("DELETE", f"{s.path(rparent)}/{tmpl(rv[rparent])}"), status=409),
                self.get(rparent, rv[rparent], status=200),
                step(http_op("DELETE", f"{s.path(rchild)}/{tmpl(rv[rchild])}"), status=204),
                step(http_op("DELETE", f"{s.path(rparent)}/{tmpl(rv[rparent])}"), status=204),
            ]
        # A cascade whose child itself holds a restrict relation: the delete must be atomic.
        nested = [
            (r1["parent"], c1, c2)
            for c1, r1 in cascades
            for c2, r2 in restricts
            if r2["parent"] == c1
        ]
        if nested:
            top, mid, leaf = nested[0]
            asteps, av = self.chain(leaf, "z")
            steps += asteps
            steps += [
                step(http_op("DELETE", f"{s.path(top)}/{tmpl(av[top])}"), status=409),
                self.get(top, av[top], status=200),
                self.get(mid, av[mid], status=200),
                step(http_op("DELETE", f"{s.path(leaf)}/{tmpl(av[leaf])}"), status=204),
                step(http_op("DELETE", f"{s.path(top)}/{tmpl(av[top])}"), status=204),
                self.get(mid, av[mid], status=404),
            ]
        return probe("trap-cascade-delete", steps, trap="cascade_delete")

    def _t_patch_null(self) -> JsonDict:
        s = self.s
        root = s.root["name"]
        path = s.path(root)
        e = s.by_name[root]
        text = s.optional(root, "string")
        int_f = s.optional(root, "int")
        dt = s.optional(root, "datetime")
        assert text and int_f and dt
        label, enum = e["fields"][0]["name"], e["fields"][1]["name"]
        steps, v = self.chain(root, "a")
        rid = tmpl(v[root])
        base = self.expected(root, {}, overrides={})
        base["id"] = {"$var": v[root]}

        def state(version: int, **over: Any) -> JsonDict:
            m = dict(base)
            m["version"] = version
            m.update(over)
            return m

        steps += [
            step(http_op("PATCH", f"{path}/{rid}", {}), status=200, match=state(2)),
            step(
                http_op("PATCH", f"{path}/{rid}", {text["name"]: None}),
                status=200,
                match=state(3, **{text["name"]: None}),
            ),
            step(
                http_op("PATCH", f"{path}/{rid}", {int_f["name"]: self.value(int_f, 1)}),
                status=200,
                match=state(4, **{text["name"]: None, int_f["name"]: self.value(int_f, 1)}),
            ),
            step(
                http_op("PATCH", f"{path}/{rid}", {dt["name"]: None}),
                status=200,
                match=state(
                    5, **{text["name"]: None, int_f["name"]: self.value(int_f, 1), dt["name"]: None}
                ),
            ),
            step(http_op("PATCH", f"{path}/{rid}", {label: None}), status=400),
            step(http_op("PATCH", f"{path}/{rid}", {enum: None}), status=400),
            self.get(
                root,
                v[root],
                match=state(
                    5, **{text["name"]: None, int_f["name"]: self.value(int_f, 1), dt["name"]: None}
                ),
            ),
        ]
        return probe("trap-patch-null-vs-absent", steps, trap="patch_null_vs_absent")

    def _t_idempotency(self) -> JsonDict:
        s = self.s
        root = s.root["name"]
        path = s.path(root)
        one = self.body(root, {}, label="idem-one")

        def keyed(key: str) -> dict[str, str]:
            return {"Idempotency-Key": key}

        steps = [
            step(
                http_op("POST", path, one, keyed("key-one")),
                status=201,
                match=self.expected(root, {}, label="idem-one"),
                save={"id1": "$.id"},
            ),
            step(
                http_op("POST", path, one, keyed("key-one")),
                status=201,
                match={"id": {"$var": "id1"}},
            ),
            step(
                http_op("POST", path, self.body(root, {}, label="idem-two"), keyed("key-one")),
                status=422,
                match=self.error("idempotency_key_reuse"),
            ),
            step(
                http_op("POST", path, self.body(root, {}, label="idem-three"), keyed("key-two")),
                status=201,
                match={"id": {"$not": {"$var": "id1"}}},
            ),
            self.list_(root, f"limit={self.page_max}", match={"items": {"$len": 2}}),
        ]
        parallel = after = None
        if self.workers:
            race = step(
                http_op("POST", path, self.body(root, {}, label="idem-race"), keyed("key-race")),
                status=201,
            )
            parallel = [[dict(race)] for _ in range(self.workers)]
            after = [self.list_(root, f"limit={self.page_max}", match={"items": {"$len": 3}})]
        return probe(
            "trap-idempotency-key", steps, trap="idempotency_key", parallel=parallel, after=after
        )

    def _t_lost_update(self) -> JsonDict:
        s = self.s
        root = s.root["name"]
        path = s.path(root)
        e = s.by_name[root]
        enum = e["fields"][1]
        label = e["fields"][0]["name"]
        steps, v = self.chain(root, "a")
        rid = tmpl(v[root])

        def ifm(version: int) -> dict[str, str]:
            return {"If-Match": str(version)}

        steps += [
            step(
                http_op("PATCH", f"{path}/{rid}", {enum["name"]: self.value(enum, 1)}, ifm(1)),
                status=200,
                match={"version": 2},
            ),
            step(
                http_op("PATCH", f"{path}/{rid}", {enum["name"]: self.value(enum, 2)}, ifm(1)),
                status=412,
                match=self.error("precondition_failed"),
            ),
            step(
                http_op("PATCH", f"{path}/{rid}", {enum["name"]: self.value(enum, 2)}, ifm(2)),
                status=200,
                match={"version": 3},
            ),
            self.get(root, v[root], match={"version": 3, enum["name"]: self.value(enum, 2)}),
        ]
        parallel = after = None
        if self.workers:
            steps.append(self.create(root, "b", {}, full_match=False))
            racer = step(
                http_op(
                    "PATCH", f"{path}/{tmpl('b_' + root)}", {label: f"lu-{tmpl('rand')}"}, ifm(1)
                ),
                status=[200, 412],
            )
            parallel = [[dict(racer)] for _ in range(self.workers)]
            after = [self.get(root, f"b_{root}", match={"version": 2})]
        return probe("trap-lost-update", steps, trap="lost_update", parallel=parallel, after=after)

    def _t_invalid(self) -> JsonDict:
        s = self.s
        root = s.root["name"]
        path = s.path(root)
        e = s.by_name[root]
        label, enum = e["fields"][0], e["fields"][1]
        by_type = {f["type"]: f for f in e["fields"][2:]}
        int_f, bool_f, dt_f, text_f = (
            by_type["int"], by_type["bool"], by_type["datetime"], by_type["string"],
        )  # fmt: skip
        good = self.body(root, {}, label="valid-one")

        def bad_post(**changes: Any) -> JsonDict:
            body = dict(good)
            for k, val in changes.items():
                if val is _DROP:
                    body.pop(k)
                else:
                    body[k] = val
            return step(http_op("POST", path, body), status=400)

        steps = [
            step(http_op("POST", path, good), status=201, save={"ok_id": "$.id"}),
            step(
                http_op("POST", path, ["not", "an", "object"]),
                status=400,
                match=self.error("invalid_request"),
            ),
            bad_post(**{label["name"]: _DROP}),
            bad_post(**{label["name"]: None}),
            bad_post(**{label["name"]: ""}),
            bad_post(**{label["name"]: "x" * (label["max_length"] + 1)}),
            bad_post(**{label["name"]: 12345}),
            bad_post(bogus_field=1),
            bad_post(id="client-chosen"),
            bad_post(version=7),
            bad_post(**{enum["name"]: "not-a-listed-value"}),
            bad_post(**{enum["name"]: _DROP}),
        ]
        steps += [
            bad_post(**{int_f["name"]: wrong})
            for wrong in ("5", True, 2.5, int_f["maximum"] + 1, int_f["minimum"] - 1)
        ]
        steps += [bad_post(**{bool_f["name"]: wrong}) for wrong in ("true", 1)]
        steps += [
            bad_post(**{dt_f["name"]: wrong})
            for wrong in ("2026-13-45T00:00:00Z", "yesterday", "2026-01-15T10:30:00+00:00",
                          "2026-02-30T10:30:00Z", "2026-01-15 10:30:00Z")
        ]  # fmt: skip
        steps += [
            bad_post(**{text_f["name"]: 42}),
            bad_post(**{text_f["name"]: "y" * (text_f["max_length"] + 1)}),
        ]
        rid = tmpl("ok_id")
        for body in (
            {int_f["name"]: "x"}, {"bogus_field": 1}, {"id": "other"}, {"version": 9},
            {label["name"]: None}, {enum["name"]: "not-a-listed-value"}, {dt_f["name"]: "soon"},
        ):  # fmt: skip
            steps.append(step(http_op("PATCH", f"{path}/{rid}", body), status=400))
        steps.append(step(http_op("PATCH", f"{path}/{rid}", [1]), status=400))
        for query in (
            "limit=0", "limit=abc", f"limit={self.page_max + 1}", "limit=-1", "limit=",
            "cursor=not-a-cursor", "order=sideways", "bogus=1", f"{int_f['name']}=abc",
            f"{bool_f['name']}=maybe",
        ):  # fmt: skip
            steps.append(step(http_op("GET", f"{path}?{query}"), status=400))
        # Invalid relation ids on a child entity (every non-root entity has one parent).
        child, rel = s.cascade_relations()[0]
        cpath, fk = s.path(child), rel["fk"]
        pre, pv = self.chain(rel["parent"], "p")
        cbody = self.body(child, {rel["parent"]: pv[rel["parent"]]}, label="child-valid")
        steps += pre
        steps += [
            step(http_op("POST", cpath, {**cbody, fk: NIL_UUID}), status=400),
            step(http_op("POST", cpath, {**cbody, fk: 123}), status=400),
            step(http_op("POST", cpath, {k: x for k, x in cbody.items() if k != fk}), status=400),
            step(http_op("POST", cpath, cbody), status=201, save={"kid": "$.id"}),
            step(http_op("PATCH", f"{cpath}/{tmpl('kid')}", {fk: NIL_UUID}), status=400),
        ]
        # The parent chain created one more root record; nothing invalid may have been stored.
        steps.append(self.list_(root, f"limit={self.page_max}", match={"items": {"$len": 2}}))
        return probe("trap-invalid-input", steps, trap="invalid_input_4xx")

    def _t_sort(self) -> JsonDict:
        s = self.s
        root = s.root["name"]
        asc = {"items": {"$sorted_by": "created_at", "order": "asc", "tiebreak": "id"}}
        desc = {"items": {"$sorted_by": "created_at", "order": "desc", "tiebreak": "id"}}
        lim = self.page_max
        checks = [
            self.list_(root, f"limit={lim}", match=asc),
            self.list_(root, f"limit={lim}&order=desc", match=desc),
        ]
        if self.workers:
            per = (lim - 1) // self.workers
            steps = [self.create(root, "x", {}, save_id=False, full_match=False)]
            maker = self.create(root, "y", {}, save_id=False, repeat=per, full_match=False)
            return probe(
                "trap-sort-tiebreak",
                steps,
                trap="sort_tiebreak",
                parallel=[[dict(maker)] for _ in range(self.workers)],
                after=checks,
            )
        steps = [self.create(root, "x", {}, save_id=False, repeat=lim, full_match=False)]
        return probe("trap-sort-tiebreak", steps + checks, trap="sort_tiebreak")

    # ---- perf -------------------------------------------------------------------------------
    def perf(self) -> JsonDict:
        s = self.s
        root = s.root["name"]
        path = s.path(root)
        body = self.body(root, {})
        load = step(http_op("POST", path, body), status=201, save={"item_id": "$.id"}, repeat=1)
        mix = [
            mix_entry(
                0.5,
                "get",
                "indexed_get",
                step(http_op("GET", f"{path}/{tmpl('item_id')}"), status=200),
            ),
            mix_entry(0.2, "create", "write", step(http_op("POST", path, body), status=201)),
            mix_entry(
                0.3,
                "list",
                "paginated_list",
                step(
                    http_op("GET", f"{path}?limit={min(PERF_LIST_PAGE, self.page_max)}"), status=200
                ),
            ),
        ]
        return perf_spec(self.difficulty.scale, load, mix)


class _Drop:
    """Sentinel: remove this key from a probe body."""


_DROP = _Drop()


# ---------------------------------------------------------------------------------------------
# family
# ---------------------------------------------------------------------------------------------


@register_family
class CrudRelationalFamily(Family):
    name = FAMILY_NAME
    contract = "http"

    def generate(
        self,
        seed: int,
        difficulty: Difficulty,
        language: str,
        allow: Sequence[str] | None = None,
    ) -> Instance:
        allow_list = resolve_allow(language, allow)
        rng = random.Random(seed)
        traps = sample_traps(rng, self.name, trap_count(difficulty.traps))
        entities = _build_entities(rng, ENTITY_COUNT_BY_LEVEL[difficulty.entities], traps)
        limits = _build_limits(rng, difficulty)
        schema = Schema(entities, limits, traps)
        builder = Probes(schema, difficulty)
        probes = dsl.probes_doc("http", language, builder.core(), builder.traps(), builder.perf())
        title = "-".join(e["name"] for e in entities[:2])
        return Instance(
            family=self.name,
            seed=seed,
            difficulty=difficulty,
            language=language,
            contract="http",
            entities=entities,
            traps=traps,
            limits=limits,
            allow=allow_list,
            spec_md=render_spec(schema, language, allow_list),
            skeleton=skeleton_files("http", language, allow_list, title=f"{title} service"),
            probes=probes,
            reference=reference_files("crud_server.py"),
        )
