"""The two generator families end to end, minus the grader: determinism, difficulty scaling,
SPEC.md hygiene, probe well-formedness, and -- via a test-only DSL evaluator -- that each
instance's reference passes every probe it emits while mutated ("naive") references fail the
probe of the trap they get wrong.  Fake-tier: no network, no docker, no LLM."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from agent_orchestrator.bench.gen import FAMILY_REGISTRY, Difficulty, Instance
from agent_orchestrator.bench.gen.dsl import (
    PERF_SIZES_BY_SCALE,
    STARTUP_TIMEOUT_BY_LANGUAGE,
    WORKERS_BY_CONCURRENCY,
)
from agent_orchestrator.bench.gen.materialize import generate_instance, write_instance
from agent_orchestrator.bench.gen.traps import TRAP_COUNT_BY_LEVEL, traps_for_family
from agent_orchestrator.bench.gen.validate import validate_probes
from agent_orchestrator.bench.spec import KNOWN_LANGUAGES

from .dsl_runner import (
    InProcessHttp,
    InProcessJsonl,
    ProcessTarget,
    Runner,
    Target,
    _free_port,
    chain_patches,
    http_request,
    replace_once,
)

CRUD = "crud-relational"
QUEUE = "job-queue"
ENTITY_COUNTS = (2, 3, 4, 6)
PRIORITY_LEVELS = (2, 3, 4, 5)
# (seed, entities, traps, concurrency, scale): every axis corner appears, as do all languages.
SWEEP: list[tuple[int, int, int, int, int]] = [
    (0, 0, 0, 0, 0), (1, 1, 1, 1, 1), (2, 2, 2, 2, 2), (3, 3, 3, 3, 3), (4, 3, 3, 0, 0),
    (5, 0, 3, 3, 1), (6, 1, 2, 3, 3), (7, 2, 0, 1, 2), (8, 3, 1, 2, 0), (9, 2, 3, 1, 1),
    (10, 1, 3, 2, 3), (11, 3, 2, 0, 2),
]  # fmt: skip
LANGUAGES = sorted(KNOWN_LANGUAGES)
BANNED = re.compile(r"probe|reference|grading|trap", re.IGNORECASE)


def gen(
    family: str,
    seed: int = 1,
    language: str = "python",
    *,
    entities: int = 2,
    traps: int = 2,
    concurrency: int = 1,
    scale: int = 1,
    allow: list[str] | None = None,
) -> Instance:
    diff = Difficulty(entities=entities, traps=traps, concurrency=concurrency, scale=scale)
    return generate_instance(family, seed, diff, language, allow)


def sweep_instances(family: str) -> list[Instance]:
    return [
        gen(family, seed, LANGUAGES[seed % 3], entities=e, traps=t, concurrency=c, scale=s)
        for seed, e, t, c, s in SWEEP
    ]


def find_seed(family: str, wanted: set[str], **diff: int) -> int:
    for seed in range(500):
        if wanted <= set(gen(family, seed, **diff).traps):
            return seed
    raise AssertionError(f"no seed with traps {wanted}")


def materialize(inst: Instance, root: Path) -> tuple[Path, Path]:
    repo, grading = root / "repo", root / "grading"
    write_instance(inst, repo, grading)
    return repo, grading


def trap_probe(inst: Instance, trap: str) -> dict[str, Any]:
    (found,) = [p for p in inst.probes["groups"]["trap"] if p["trap"] == trap]
    return found


# ---------------------------------------------------------------------------------------------
# determinism
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("family", [CRUD, QUEUE])
class TestDeterminism:
    def test_same_inputs_are_byte_identical(self, family: str) -> None:
        import random

        for seed, e, t, c, s in SWEEP:
            lang = LANGUAGES[seed % 3]
            a = gen(family, seed, lang, entities=e, traps=t, concurrency=c, scale=s)
            random.seed(seed + 12345)  # global RNG state must be irrelevant
            random.random()
            b = gen(family, seed, lang, entities=e, traps=t, concurrency=c, scale=s)
            assert a.model_dump_json() == b.model_dump_json()
            assert a.instance_json_text() == b.instance_json_text()
            assert a.probes_json_text() == b.probes_json_text()

    def test_independent_of_python_hash_randomisation(self, family: str) -> None:
        code = (
            "import hashlib;from agent_orchestrator.bench.gen import Difficulty;"
            "from agent_orchestrator.bench.gen.materialize import generate_instance as g;"
            f"i=g({family!r},77,Difficulty(entities=3,traps=3,concurrency=2,scale=2),'go');"
            "print(hashlib.sha256(i.model_dump_json().encode()).hexdigest())"
        )
        digests = {
            subprocess.run(
                [sys.executable, "-c", code],
                env={**os.environ, "PYTHONHASHSEED": str(h)},
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
            for h in (1, 2, 3)
        }
        assert len(digests) == 1
        here = gen(family, 77, "go", entities=3, traps=3, concurrency=2, scale=2)
        assert digests == {hashlib.sha256(here.model_dump_json().encode()).hexdigest()}

    def test_different_seeds_differ(self, family: str) -> None:
        texts = {gen(family, seed).instance_json_text() for seed in range(12)}
        assert len(texts) >= 10  # a few collisions on small parameter spaces are fine
        specs = {gen(family, seed).spec_md for seed in range(12)}
        assert len(specs) >= 10

    def test_language_changes_only_the_skeleton_and_startup_budget(self, family: str) -> None:
        py, ts, go = (gen(family, 5, lang, traps=3) for lang in ("python", "typescript", "go"))
        assert py.entities == ts.entities == go.entities
        assert py.traps == ts.traps == go.traps and py.limits == ts.limits == go.limits
        for inst in (ts, go):
            assert inst.probes["groups"] == py.probes["groups"]
            assert inst.reference == py.reference
        assert go.probes["startup_timeout_seconds"] == STARTUP_TIMEOUT_BY_LANGUAGE["go"] > 30
        assert set(py.skeleton) != set(ts.skeleton) != set(go.skeleton)

    def test_allow_list_flows_to_manifest_spec_and_instance(self, family: str) -> None:
        default = gen(family, 2)
        assert default.allow == ["sqlite3", "montydb"] and "montydb" in default.spec_md
        stdlib = gen(family, 2, allow=["sqlite3"])
        assert stdlib.allow == ["sqlite3"] and "montydb" not in stdlib.spec_md
        assert "dependencies = []" in stdlib.skeleton["pyproject.toml"]
        assert stdlib.instance_json()["allow"] == ["sqlite3"]


def test_different_seeds_give_different_entity_names() -> None:
    names = {tuple(e["name"] for e in gen(CRUD, seed).entities) for seed in range(20)}
    assert len(names) >= 15
    fields = {tuple(f["name"] for f in gen(CRUD, seed).entities[0]["fields"]) for seed in range(20)}
    assert len(fields) >= 10


# ---------------------------------------------------------------------------------------------
# difficulty scaling
# ---------------------------------------------------------------------------------------------


class TestCrudScaling:
    def test_entity_count_and_relations(self) -> None:
        for level, expected in enumerate(ENTITY_COUNTS):
            inst = gen(CRUD, 9, entities=level)
            assert len(inst.entities) == expected
            belongs = [
                r for e in inst.entities for r in e["relations"] if r["kind"] == "belongs_to"
            ]
            assert len(belongs) == expected - 1  # a 1:N tree
            m2n = [r for e in inst.entities for r in e["relations"] if r["kind"] == "many_to_many"]
            assert len(m2n) == (2 if expected >= 3 else 0)  # recorded on both entities
            for e in inst.entities:
                assert e["plural"] == e["name"] + "s"
                types = {f["type"] for f in e["fields"]}
                assert {"string", "enum"} <= types

    def test_m2n_links_non_adjacent_entities(self) -> None:
        for seed in range(30):
            inst = gen(CRUD, seed, entities=3)
            parent_of = {
                (r["parent"], e["name"])
                for e in inst.entities
                for r in e["relations"]
                if r["kind"] == "belongs_to"
            }
            for e in inst.entities:
                for r in e["relations"]:
                    if r["kind"] == "many_to_many":
                        assert (e["name"], r["other"]) not in parent_of
                        assert (r["other"], e["name"]) not in parent_of

    def test_root_entity_has_every_field_type(self) -> None:
        for seed in range(15):
            root = gen(CRUD, seed).entities[0]
            assert {f["type"] for f in root["fields"]} == {
                "string", "enum", "int", "bool", "datetime",
            }  # fmt: skip

    def test_trap_count_by_level_and_superset(self) -> None:
        for seed in range(25):
            picks = [gen(CRUD, seed, traps=lvl).traps for lvl in range(4)]
            for lvl, chosen in enumerate(picks):
                assert len(chosen) >= TRAP_COUNT_BY_LEVEL[lvl]
                assert len(chosen) <= TRAP_COUNT_BY_LEVEL[lvl] + 1  # + one required trap at most
            for lo, hi in zip(picks, picks[1:], strict=False):
                assert set(lo) <= set(hi)
            assert len(picks[3]) > len(picks[0])

    def test_perf_addresses_records_through_the_index_variable(self) -> None:
        perf = gen(CRUD, 4).probes["groups"]["perf"]
        root_path = "/" + gen(CRUD, 4).entities[0]["plural"]
        assert perf["load"]["op"]["json"]["id"] == "perf-{{i}}"
        assert perf["load"]["op"]["path"] == root_path and "save" not in perf["load"]
        get = next(m for m in perf["mix"] if m["class"] == "indexed_get")["step"]
        assert get["op"]["path"] == f"{root_path}/perf-{{{{i}}}}"
        creates = [m for m in perf["mix"] if m["class"] == "write"]
        assert all("id" not in m["step"]["op"]["json"] for m in creates)
        assert all("save" not in m["step"] for m in perf["mix"])

    def test_client_supplied_id_is_core_and_spec_states_it(self) -> None:
        inst = gen(CRUD, 4)
        assert "core-client-supplied-id" in {p["id"] for p in inst.probes["groups"]["core"]}
        assert "1 to 64 characters from `A-Z a-z 0-9 _ -`" in inst.spec_md

    def test_perf_sizes_scale_with_difficulty(self) -> None:
        sizes = [gen(CRUD, 4, scale=s).probes["groups"]["perf"]["sizes"] for s in range(4)]
        assert sizes == [list(x) for x in PERF_SIZES_BY_SCALE]
        assert sizes[2] == [100, 10000] and sizes[3] == [100, 10000, 100000]
        assert [max(x) for x in sizes] == sorted(max(x) for x in sizes)
        assert gen(CRUD, 4, scale=3).limits["max_records"] == 100000

    def test_parallel_race_probes_follow_concurrency(self) -> None:
        wanted = {"unique_constraint", "idempotency_key", "lost_update"}
        seed = find_seed(CRUD, wanted, traps=3)
        for level in range(4):
            inst = gen(CRUD, seed, traps=3, concurrency=level)
            for trap in sorted(wanted):
                probe = trap_probe(inst, trap)
                if level == 0:
                    assert "parallel" not in probe, trap
                else:
                    assert len(probe["parallel"]) == WORKERS_BY_CONCURRENCY[level]
                    assert probe["after"], trap
        assert inst.limits["parallel_workers"] == 16

    def test_one_trap_probe_per_sampled_trap_and_core_per_entity(self) -> None:
        for inst in sweep_instances(CRUD):
            groups = inst.probes["groups"]
            assert [p["trap"] for p in groups["trap"]] == inst.traps
            ids = {p["id"] for p in groups["core"]}
            for e in inst.entities:
                for kind in ("create-get", "patch", "delete", "list-filter"):
                    assert f"core-{e['name']}-{kind}" in ids
            assert {"core-healthz", "core-error-shape", "core-pagination-basic"} <= ids

    def test_cascade_and_restrict_are_guaranteed_when_sampled(self) -> None:
        for seed in range(60):
            inst = gen(CRUD, seed, entities=2, traps=3)
            if "cascade_delete" in inst.traps:
                kinds = [r["on_delete"] for e in inst.entities for r in e["relations"]
                         if r["kind"] == "belongs_to"]  # fmt: skip
                assert "cascade" in kinds
            inst = gen(CRUD, seed, entities=3, traps=3)
            if "cascade_delete" in inst.traps:
                kinds = [r["on_delete"] for e in inst.entities for r in e["relations"]
                         if r["kind"] == "belongs_to"]  # fmt: skip
                assert {"cascade", "restrict"} <= set(kinds)

    def test_unique_fields_only_with_the_trap(self) -> None:
        for seed in range(40):
            inst = gen(CRUD, seed, traps=1)
            has_unique = any(f.get("unique") for e in inst.entities for f in e["fields"])
            assert has_unique == ("unique_constraint" in inst.traps)
            has_fold = any(f.get("unique_casefold") for e in inst.entities for f in e["fields"])
            assert has_fold == ("unicode_casefold" in inst.traps)


class TestQueueScaling:
    def test_priority_levels_follow_entities(self) -> None:
        for level, expected in enumerate(PRIORITY_LEVELS):
            assert gen(QUEUE, 3, entities=level).limits["priority_levels"] == expected

    def test_feature_flags_follow_the_traps(self) -> None:
        for seed in range(40):
            inst = gen(QUEUE, seed, traps=2)
            limits = inst.limits
            assert limits["leases"] == ("visibility_timeout" in inst.traps)
            assert limits["dedupe"] == ("dedupe_key" in inst.traps)
            assert (limits["fairness_every"] > 0) == ("starvation" in inst.traps)
            if limits["fairness_every"]:
                assert 4 <= limits["fairness_every"] <= 8
            assert 3 <= limits["default_max_attempts"] <= 5
            assert limits["default_priority"] in (0, limits["priority_levels"] - 1)

    def test_trap_counts_and_probes(self) -> None:
        for seed in range(15):
            picks = [gen(QUEUE, seed, traps=lvl).traps for lvl in range(4)]
            for lo, hi in zip(picks, picks[1:], strict=False):
                assert set(lo) <= set(hi)
            assert set(picks[3]) == set(traps_for_family(QUEUE))
            inst = gen(QUEUE, seed, traps=3)
            assert [p["trap"] for p in inst.probes["groups"]["trap"]] == inst.traps

    def test_parallel_claim_probe_follows_concurrency(self) -> None:
        for level in range(4):
            inst = gen(QUEUE, 1, traps=3, concurrency=level)
            probe = trap_probe(inst, "duplicate_claim")
            if level == 0:
                assert "parallel" not in probe
            else:
                assert len(probe["parallel"]) == WORKERS_BY_CONCURRENCY[level]

    def test_perf_sizes_scale(self) -> None:
        for scale in range(4):
            perf = gen(QUEUE, 2, scale=scale).probes["groups"]["perf"]
            assert perf["sizes"] == list(PERF_SIZES_BY_SCALE[scale])


# ---------------------------------------------------------------------------------------------
# SPEC.md / probes hygiene
# ---------------------------------------------------------------------------------------------

CRUD_HEADINGS = {
    "pagination_stability": "**Stable pagination.**",
    "unique_constraint": "**Uniqueness.**",
    "unicode_casefold": "**Caseless uniqueness.**",
    "cascade_delete": "**All-or-nothing delete.**",
    "patch_null_vs_absent": "**PATCH semantics.**",
    "idempotency_key": "**Idempotent creation.**",
    "lost_update": "**Conditional update.**",
    "invalid_input_4xx": "**Strict validation.**",
    "sort_tiebreak": "**Total order.**",
}
QUEUE_HEADINGS = {
    "visibility_timeout": "**Leases.**",
    "at_least_once": "**Redelivery and retry budget.**",
    "duplicate_claim": "**Exclusive claims.**",
    "starvation": "**Fair share.**",
    "dedupe_key": "**Deduplication.**",
    "invalid_input_4xx": "**Strict validation.**",
}


@pytest.mark.parametrize(("family", "headings"), [(CRUD, CRUD_HEADINGS), (QUEUE, QUEUE_HEADINGS)])
class TestSpecMd:
    def test_never_mentions_probes_reference_grading_or_traps(
        self, family: str, headings: dict[str, str]
    ) -> None:
        for inst in sweep_instances(family):
            assert not BANNED.search(inst.spec_md), BANNED.search(inst.spec_md)
            assert not BANNED.search(" ".join(inst.skeleton.values()))

    def test_states_run_contract_and_allow_list(
        self, family: str, headings: dict[str, str]
    ) -> None:
        for lang in LANGUAGES:
            inst = gen(family, 3, lang)
            md = inst.spec_md
            assert "## 1. Run contract" in md and "## 2. Allowed dependencies" in md
            assert {"python": "python3 -m app", "typescript": "node app.js", "go": "go run ."}[
                lang
            ] in md
            for entry in inst.allow:
                assert entry.split(":")[-1] in md
            if family == CRUD:
                assert "/healthz" in md and "PORT" in md
            else:
                assert "standard input" in md and "advance_clock" in md

    def test_sampled_behaviours_are_stated_and_only_those(
        self, family: str, headings: dict[str, str]
    ) -> None:
        for seed in range(12):
            inst = gen(family, seed, traps=seed % 4, entities=2)
            for trap, heading in headings.items():
                assert (heading in inst.spec_md) == (trap in inst.traps), (trap, seed)

    def test_every_op_or_endpoint_is_documented(
        self, family: str, headings: dict[str, str]
    ) -> None:
        inst = gen(family, 6, entities=3)
        if family == CRUD:
            for e in inst.entities:
                assert f"/{e['plural']}" in inst.spec_md
            assert "next_cursor" in inst.spec_md and "limit" in inst.spec_md
        else:
            for op in ("ping", "enqueue", "claim", "ack", "nack", "stats", "advance_clock"):
                assert f"`{op}`" in inst.spec_md


@pytest.mark.parametrize("family", [CRUD, QUEUE])
class TestProbeDocs:
    def test_every_generated_doc_is_valid_dsl(self, family: str) -> None:
        for inst in sweep_instances(family):
            assert validate_probes(inst.probes) == [], (inst.seed, inst.difficulty)
        for seed in range(30):
            inst = gen(family, seed, LANGUAGES[seed % 3], entities=seed % 4, traps=3,
                       concurrency=seed % 4, scale=seed % 4)  # fmt: skip
            assert validate_probes(inst.probes) == [], seed

    def test_weights_threshold_and_perf_defaults(self, family: str) -> None:
        probes = gen(family, 2, "go").probes
        assert probes["weights"] == {"core": 0.5, "trap": 0.35, "perf": 0.15}
        assert probes["pass_threshold"] == 0.8
        assert probes["contract"] == FAMILY_REGISTRY[family].contract
        perf = probes["groups"]["perf"]
        assert abs(sum(m["weight"] for m in perf["mix"]) - 1.0) < 1e-9
        assert set(perf["expected_alpha"].values()) == {0.3}
        assert perf["alpha_slack"] == 0.2 and perf["ref_ratio_max"] == 5.0
        assert perf["repeats"] == 3 and perf["checks"] == ["alpha", "ref_ratio", "timeout"]
        assert perf["load"]["repeat"] == perf["sizes"][0]

    def test_instance_json_follows_dsl_section_5(self, family: str) -> None:
        inst = gen(family, 8, traps=3)
        doc = json.loads(inst.instance_json_text())
        assert {"version", "family", "seed", "difficulty", "language", "contract", "entities",
                "traps", "limits", "reference"} <= set(doc)  # fmt: skip
        assert doc["reference"] == {"language": "python", "entry": "reference/run.sh"}
        assert doc["family"] == family and doc["seed"] == 8
        assert doc["difficulty"] == inst.difficulty.as_dict()
        for forbidden in ("spec_md", "skeleton", "probes"):
            assert forbidden not in doc
        assert set(inst.reference) == {"run.sh", "server.py"}
        assert inst.reference["run.sh"].startswith("#!/bin/sh")


# ---------------------------------------------------------------------------------------------
# reference passes its own probes (test-only evaluator; the real grader is the blackbox track)
# ---------------------------------------------------------------------------------------------


def _http_target(inst: Instance, root: Path, **kw: Any) -> Callable[[], Target]:
    _, grading = materialize(inst, root)
    return lambda: InProcessHttp(grading / "reference", grading / "instance.json", **kw)


def _jsonl_target(inst: Instance, root: Path, **kw: Any) -> Callable[[], Target]:
    _, grading = materialize(inst, root)
    return lambda: InProcessJsonl(grading / "reference", grading / "instance.json", **kw)


def _failures(results: dict[str, list[str]]) -> dict[str, str]:
    return {k: v[0][:400] for k, v in results.items() if v}


class TestReferencePassesItsProbes:
    @pytest.mark.parametrize("idx", range(len(SWEEP)))
    def test_crud_reference(self, tmp_path: Path, idx: int) -> None:
        inst = sweep_instances(CRUD)[idx]
        runner = Runner(inst.probes, _http_target(inst, tmp_path))
        assert _failures(runner.run_groups()) == {}
        assert runner.run_perf_smoke() == []

    @pytest.mark.parametrize("idx", range(len(SWEEP)))
    def test_queue_reference(self, tmp_path: Path, idx: int) -> None:
        inst = sweep_instances(QUEUE)[idx]
        runner = Runner(inst.probes, _jsonl_target(inst, tmp_path))
        assert _failures(runner.run_groups()) == {}
        assert runner.run_perf_smoke() == []

    def test_every_crud_trap_is_exercised_by_some_passing_instance(self, tmp_path: Path) -> None:
        seen: set[str] = set()
        for seed in range(0, 40):
            inst = gen(CRUD, seed, entities=seed % 4, traps=3, concurrency=seed % 3)
            if set(inst.traps) <= seen:
                continue
            seen |= set(inst.traps)
            runner = Runner(inst.probes, _http_target(inst, tmp_path / str(seed)))
            assert _failures(runner.run_groups()) == {}, seed
        assert seen == set(traps_for_family(CRUD))

    def test_instance_with_a_restrict_chain_under_cascade_passes(self, tmp_path: Path) -> None:
        # entities=3, cascade sampled: exercise the all-or-nothing nested-delete steps
        for seed in range(200):
            inst = gen(CRUD, seed, entities=3, traps=3)
            rels = [(e["name"], r) for e in inst.entities for r in e["relations"]
                    if r["kind"] == "belongs_to"]  # fmt: skip
            nested = [
                1
                for _, r1 in rels
                for c2, r2 in rels
                if "cascade_delete" in inst.traps
                and r1["on_delete"] == "cascade"
                and r2["on_delete"] == "restrict"
                and r2["parent"] == next(c for c, r in rels if r is r1)
            ]
            if nested:
                runner = Runner(inst.probes, _http_target(inst, tmp_path))
                assert _failures(runner.run_groups()) == {}
                return
        pytest.fail("no instance with a restrict relation below a cascade relation")


# ---------------------------------------------------------------------------------------------
# negative controls: naive implementations must fail the probe of the trap they get wrong
# ---------------------------------------------------------------------------------------------

CRUD_MUTANTS: dict[str, tuple[str, Callable[[str], str]]] = {
    "unique_constraint": (
        "trap-unique-constraint",
        replace_once("self._check_unique(entity, row, own_id=None)", "pass"),
    ),
    "unicode_casefold": (
        "trap-unicode-casefold",
        replace_once('return nfkc("NFKC", nfkc("NFKC", value).casefold())', "return value.lower()"),
    ),
    "cascade_delete": (
        "trap-cascade-delete",
        replace_once('if on_delete == "restrict":', "if False:"),
    ),
    "patch_null_vs_absent": (
        "trap-patch-null-vs-absent",
        replace_once(
            '                if value is None:\n                    if f.get("required"):',
            "                if value is None:\n                    continue\n"
            '                    if f.get("required"):',
        ),
    ),
    "idempotency_key": (
        "trap-idempotency-key",
        replace_once('self.headers.get("Idempotency-Key")', "None"),
    ),
    "lost_update": (
        "trap-lost-update",
        replace_once('if expected is not None and expected != row["version"]:', "if False:"),
    ),
    "invalid_input_4xx": (
        "trap-invalid-input",
        replace_once("ok = type(v) is int and", "ok = isinstance(v, int) and"),
    ),
    "pagination_stability": (
        "trap-pagination-stability",
        chain_patches(
            replace_once(
                "start = bisect.bisect_right(keys, cursor) if cursor else 0",
                "start = int(cursor[0]) if cursor else 0",
            ),
            replace_once(
                "(encode_cursor(page[-1]) if more and page else None)",
                '(encode_cursor((str((int(cursor[0]) if cursor else 0) + len(page)), ""))'
                " if more and page else None)",
            ),
        ),
    ),
}


def identity_patch(source: str) -> str:
    return source  # the mutation is a limits override, not a source change


QUEUE_MUTANTS: dict[str, tuple[str, Callable[[str], str], dict[str, Any]]] = {
    "visibility_timeout": (
        "trap-visibility-timeout",
        replace_once("self.lease_heap[0][0] <= self.now", "self.lease_heap[0][0] < self.now"),
        {},
    ),
    "at_least_once": (
        "trap-at-least-once",
        # an expired lease always requeues: never burns the last attempt, never goes dead
        replace_once(
            'job["receipt"] == receipt:\n                self._release(job)',
            'job["receipt"] == receipt:\n                self._requeue(job)',
        ),
        {},
    ),
    "duplicate_claim": (
        "trap-duplicate-claim",
        # a lost race: every 5th claim leaves the job claimable, so it is handed out again
        replace_once(
            '        self._set_state(job, "in_flight")\n        job["_gen"] += 1',
            "        if self.claims % 5 != 0:\n"
            '            self._set_state(job, "in_flight")\n'
            '            job["_gen"] += 1',
        ),
        {},
    ),
    "starvation": ("trap-starvation", identity_patch, {"fairness_every": 0}),
    "dedupe_key": ("trap-dedupe-key", identity_patch, {"dedupe": False}),
    "invalid_input_4xx": (
        "trap-invalid-input",
        replace_once("return type(v) is int", "return isinstance(v, int)"),
        {},
    ),
}


class TestNaiveImplementationsAreCaught:
    @pytest.mark.parametrize("trap", sorted(CRUD_MUTANTS))
    def test_crud_mutant_fails_its_trap_probe_and_not_the_core(
        self, tmp_path: Path, trap: str
    ) -> None:
        probe_id, patch = CRUD_MUTANTS[trap]
        seed = find_seed(CRUD, {trap}, entities=3, traps=3)
        inst = gen(CRUD, seed, entities=3, traps=3, concurrency=1)
        runner = Runner(inst.probes, _http_target(inst, tmp_path, patch=patch))
        failed = _failures(runner.run_groups())
        assert probe_id in failed, failed
        assert not [k for k in failed if k.startswith("core-")], failed

    @pytest.mark.parametrize("trap", sorted(QUEUE_MUTANTS))
    def test_queue_mutant_fails_its_trap_probe_and_not_the_core(
        self, tmp_path: Path, trap: str
    ) -> None:
        probe_id, patch, limits = QUEUE_MUTANTS[trap]
        seed = find_seed(QUEUE, {trap}, traps=3)
        inst = gen(QUEUE, seed, traps=3, concurrency=1)
        runner = Runner(inst.probes, _jsonl_target(inst, tmp_path, patch=patch, limits=limits))
        failed = _failures(runner.run_groups())
        assert probe_id in failed, failed
        assert not [k for k in failed if k.startswith("core-")], failed

    def test_unmutated_references_pass_for_the_same_instances(self, tmp_path: Path) -> None:
        # the controls above are only meaningful if the baseline for those instances is green
        inst = gen(CRUD, find_seed(CRUD, {"pagination_stability"}, entities=3, traps=3),
                   entities=3, traps=3)  # fmt: skip
        assert _failures(Runner(inst.probes, _http_target(inst, tmp_path)).run_groups()) == {}


# ---------------------------------------------------------------------------------------------
# the real run.sh, as a subprocess (what the grader will start)
# ---------------------------------------------------------------------------------------------


class TestReferenceSubprocess:
    def test_crud_reference_over_http(self, tmp_path: Path) -> None:
        inst = gen(CRUD, 21, entities=1, traps=1)
        _, grading = materialize(inst, tmp_path)
        target = ProcessTarget(grading, "http")
        target.start()  # readiness == GET /healthz -> 200 (checked inside start)
        try:
            root = inst.entities[0]
            path = "/" + root["plural"]
            body: dict[str, Any] = {root["fields"][0]["name"]: "round-trip"}
            body[root["fields"][1]["name"]] = root["fields"][1]["values"][0]
            status, created = http_request(
                "127.0.0.1", target.port, {"method": "POST", "path": path, "json": body}
            )
            assert status == 201 and created["version"] == 1
            status, fetched = http_request(
                "127.0.0.1", target.port, {"method": "GET", "path": f"{path}/{created['id']}"}
            )
            assert status == 200 and fetched == created
            for i in range(2):
                body[root["fields"][0]["name"]] = f"extra-{i}"
                http_request(
                    "127.0.0.1", target.port, {"method": "POST", "path": path, "json": body}
                )
            status, page = http_request(
                "127.0.0.1", target.port, {"method": "GET", "path": f"{path}?limit=2"}
            )
            assert status == 200 and len(page["items"]) == 2
            assert isinstance(page["next_cursor"], str)
            status, last = http_request(
                "127.0.0.1",
                target.port,
                {"method": "GET", "path": f"{path}?limit=2&cursor={page['next_cursor']}"},
            )
            assert status == 200 and len(last["items"]) == 1 and last["next_cursor"] is None
        finally:
            target.stop()

    def test_crud_reference_binds_host_and_port_from_the_environment(self, tmp_path: Path) -> None:
        inst = gen(CRUD, 22, entities=0, traps=0)
        _, grading = materialize(inst, tmp_path)
        port = _free_port()
        env = {**os.environ, "PORT": str(port), "PYTHONUNBUFFERED": "1"}
        env.pop("HOST", None)  # fallback: 127.0.0.1
        proc = subprocess.Popen([str(grading / "reference" / "run.sh")], env=env)
        try:
            for _ in range(200):
                try:
                    status, body = http_request(
                        "127.0.0.1", port, {"method": "GET", "path": "/healthz"}
                    )
                    break
                except OSError:
                    time.sleep(0.05)
            else:
                pytest.fail("reference never became ready")
            assert (status, body) == (200, {"status": "ok"})
        finally:
            proc.terminate()
            assert proc.wait(10) is not None  # SIGTERM -> prompt exit

    def test_queue_reference_over_stdin(self, tmp_path: Path) -> None:
        inst = gen(QUEUE, 21, traps=1)
        _, grading = materialize(inst, tmp_path)
        target = ProcessTarget(grading, "jsonl")
        target.start()  # readiness == {"op":"ping"} -> {"ok":true}
        try:

            def call(req: Any) -> Any:
                return target.request({"json": req})[1]

            assert call({"op": "ping"}) == {"ok": True}
            enq = call({"op": "enqueue", "payload": {"n": 1}})
            assert enq["ok"] and enq["job"]["state"] == "queued"
            claimed = call({"op": "claim", "worker": "w1"})
            assert claimed["job"]["id"] == enq["job"]["id"] and claimed["job"]["attempts"] == 1
            assert call(
                {"op": "ack", "id": claimed["job"]["id"], "receipt": claimed["job"]["receipt"]}
            ) == {"ok": True}
            assert call({"op": "stats"})["stats"]["done"] == 1
            assert call({"op": "claim", "worker": "w1"}) == {"ok": True, "job": None}
        finally:
            target.stop()

    @pytest.mark.parametrize(
        ("family", "kwargs"), [(CRUD, {"entities": 2, "traps": 3}), (QUEUE, {"traps": 3})]
    )
    def test_full_probe_suite_through_run_sh(
        self, tmp_path: Path, family: str, kwargs: dict[str, int]
    ) -> None:
        inst = gen(family, 31, **kwargs)
        _, grading = materialize(inst, tmp_path)
        contract = FAMILY_REGISTRY[family].contract
        runner = Runner(inst.probes, lambda: ProcessTarget(grading, contract))
        assert _failures(runner.run_groups()) == {}

    def test_reference_is_self_contained_when_copied_elsewhere(self, tmp_path: Path) -> None:
        # FakeSubject `copy-reference` overlays reference/** onto the repo: no INSTANCE_JSON there.
        inst = gen(QUEUE, 4)
        _, grading = materialize(inst, tmp_path)
        copy = tmp_path / "elsewhere"
        copy.mkdir()
        for f in (grading / "reference").iterdir():
            (copy / f.name).write_bytes(f.read_bytes())
            (copy / f.name).chmod(f.stat().st_mode)
        env = {k: v for k, v in os.environ.items() if k != "INSTANCE_JSON"}
        proc = subprocess.run(
            [str(copy / "run.sh")],
            input='{"op":"ping"}\n',
            capture_output=True,
            text=True,
            env=env,
            timeout=20,
            check=True,
        )
        assert json.loads(proc.stdout) == {"ok": True}
