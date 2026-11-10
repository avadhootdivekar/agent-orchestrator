"""Generator foundations (E-Bn7Dsc / T-Gn3Ef4): difficulty, trap catalogue/sampling, registry,
skeletons, the probes.json validator.  Fake-tier: no network, no docker, no LLM."""

from __future__ import annotations

import copy
import json
import random
from typing import Any

import pytest

from agent_orchestrator.bench.gen import FAMILY_REGISTRY, Difficulty, Family
from agent_orchestrator.bench.gen import base as gen_base
from agent_orchestrator.bench.gen.materialize import generate_instance
from agent_orchestrator.bench.gen.skeleton import (
    DEFAULT_ALLOW,
    allow_list_text,
    resolve_allow,
    run_contract_text,
    skeleton_files,
    third_party,
)
from agent_orchestrator.bench.gen.traps import (
    TRAP_CATALOGUE,
    TRAP_COUNT_BY_LEVEL,
    sample_traps,
    traps_for_family,
)
from agent_orchestrator.bench.gen.validate import check_probes, validate_probes
from agent_orchestrator.bench.spec import GENERATED_LANGUAGES

REQUIRED_TRAPS = {
    "pagination_stability", "unique_constraint", "cascade_delete", "patch_null_vs_absent",
    "idempotency_key", "lost_update", "invalid_input_4xx", "sort_tiebreak", "unicode_casefold",
    "at_least_once", "duplicate_claim", "visibility_timeout", "starvation", "dedupe_key",
}  # fmt: skip


class TestDifficulty:
    def test_defaults_and_dict_order(self) -> None:
        d = Difficulty()
        assert d.as_dict() == {"entities": 2, "traps": 2, "concurrency": 1, "scale": 1}

    @pytest.mark.parametrize(
        "raw",
        [
            {"entities": 4},
            {"traps": -1},
            {"scale": True},
            {"concurrency": "1"},
            {"entities": 1.0},
            {"bogus": 1},
        ],
    )
    def test_from_mapping_rejects_bad_values(self, raw: dict[str, Any]) -> None:
        with pytest.raises(ValueError):
            Difficulty.from_mapping(raw)

    def test_from_mapping_accepts_partial_and_none(self) -> None:
        assert Difficulty.from_mapping(None) == Difficulty()
        assert Difficulty.from_mapping({"traps": 3}).traps == 3


class TestTrapCatalogue:
    def test_catalogue_covers_required_keys(self) -> None:
        assert REQUIRED_TRAPS <= set(TRAP_CATALOGUE)

    def test_requirements_refer_to_known_traps_of_the_same_family(self) -> None:
        for trap in TRAP_CATALOGUE.values():
            assert trap.families, trap.key
            for req in trap.requires:
                assert req in TRAP_CATALOGUE
                assert set(trap.families) <= set(TRAP_CATALOGUE[req].families)

    def test_every_registered_family_has_traps(self) -> None:
        for name in FAMILY_REGISTRY:
            assert len(traps_for_family(name)) >= TRAP_COUNT_BY_LEVEL[1]

    @pytest.mark.parametrize("family", ["crud-relational", "job-queue"])
    def test_sampling_is_deterministic_and_monotone_in_level(self, family: str) -> None:
        for seed in range(40):
            picks = [
                sample_traps(random.Random(seed), family, TRAP_COUNT_BY_LEVEL[lvl])
                for lvl in range(4)
            ]
            assert picks == [
                sample_traps(random.Random(seed), family, TRAP_COUNT_BY_LEVEL[lvl])
                for lvl in range(4)
            ]
            for lower, higher in zip(picks, picks[1:], strict=False):
                assert set(lower) <= set(higher)
            for lvl, chosen in enumerate(picks):
                assert len(chosen) >= min(TRAP_COUNT_BY_LEVEL[lvl], len(traps_for_family(family)))
                assert set(chosen) <= set(traps_for_family(family))

    def test_requirement_closure(self) -> None:
        for seed in range(60):
            crud = sample_traps(random.Random(seed), "crud-relational", 4)
            if "unicode_casefold" in crud:
                assert "unique_constraint" in crud
            queue = sample_traps(random.Random(seed), "job-queue", 3)
            if "at_least_once" in queue:
                assert "visibility_timeout" in queue

    def test_unknown_family_rejected(self) -> None:
        with pytest.raises(ValueError):
            sample_traps(random.Random(0), "no-such-family", 1)


class TestRegistry:
    def test_both_families_registered_with_contracts(self) -> None:
        assert FAMILY_REGISTRY["crud-relational"].contract == "http"
        assert FAMILY_REGISTRY["job-queue"].contract == "jsonl"

    def test_duplicate_registration_is_an_error(self) -> None:
        class Dup(Family):
            name = "crud-relational"
            contract = "http"

            def generate(self, seed, difficulty, language, allow=None):  # type: ignore[no-untyped-def]
                raise NotImplementedError

        with pytest.raises(ValueError, match="already registered"):
            gen_base.register_family(Dup)

    def test_unknown_family_and_language_rejected(self) -> None:
        with pytest.raises(ValueError, match="unknown generator family"):
            generate_instance("nope", 1, None, "python")
        with pytest.raises(ValueError, match="unknown language"):
            generate_instance("crud-relational", 1, None, "cobol")


class TestSkeleton:
    @pytest.mark.parametrize("language", sorted(GENERATED_LANGUAGES))
    @pytest.mark.parametrize("contract", ["http", "jsonl"])
    def test_files_and_run_sh(self, language: str, contract: str) -> None:
        run_sh_has_bind_note = (
            "bind $HOST:$PORT"
            in skeleton_files(contract, language, resolve_allow(language, None), title="t")[
                "run.sh"
            ]
        )
        assert run_sh_has_bind_note == (contract == "http")
        allow = resolve_allow(language, None)
        files = skeleton_files(contract, language, allow, title="t")
        run_sh = files["run.sh"]
        assert run_sh.startswith("#!/bin/sh\n")
        exec_line = {
            "python": "exec python3 -m app",
            "typescript": "exec node app.js",
            "go": "exec go run .",
        }[language]
        assert exec_line in run_sh
        manifest = {"python": "pyproject.toml", "typescript": "package.json", "go": "go.mod"}[
            language
        ]
        assert manifest in files
        assert "implement SPEC.md" in "".join(files.values())

    def test_python_manifest_lists_only_third_party_allow_entries(self) -> None:
        default = skeleton_files("http", "python", resolve_allow("python", None), title="t")
        assert 'dependencies = ["montydb"]' in default["pyproject.toml"]
        assert "sqlite3" not in default["pyproject.toml"].split("dependencies")[1]
        stdlib_only = skeleton_files("http", "python", ["sqlite3"], title="t")
        assert "dependencies = []" in stdlib_only["pyproject.toml"]
        pinned = skeleton_files("http", "python", ["sqlite3", "montydb==2.5.3"], title="t")
        assert '"montydb==2.5.3"' in pinned["pyproject.toml"]

    def test_typescript_is_plain_node_esm_with_no_deps(self) -> None:
        files = skeleton_files("http", "typescript", resolve_allow("typescript", None), title="t")
        pkg = json.loads(files["package.json"])
        assert pkg["type"] == "module"
        assert pkg["dependencies"] == {}
        assert "tsx" not in files["package.json"] + files["run.sh"]
        assert json.loads(files["tsconfig.json"])["compilerOptions"]["outDir"] == "."
        assert "node:sqlite" in allow_list_text("typescript", DEFAULT_ALLOW["typescript"])

    def test_go_manifest_mentions_allowed_module_only(self) -> None:
        files = skeleton_files("http", "go", resolve_allow("go", None), title="t")
        assert files["go.mod"].startswith("module app\n")
        assert "modernc.org/sqlite" in files["go.mod"]
        assert "montydb" not in files["go.mod"]

    def test_allow_list_text_states_the_allow_list(self) -> None:
        text = allow_list_text("python", ["sqlite3", "montydb"])
        assert "`montydb`" in text and "sqlite3" in text and "no network" in text
        assert third_party("go", ["modernc.org/sqlite"]) == ["modernc.org/sqlite"]
        assert third_party("python", ["sqlite3"]) == []

    @pytest.mark.parametrize("language", sorted(GENERATED_LANGUAGES))
    def test_run_contract_text_per_contract(self, language: str) -> None:
        http = run_contract_text("http", language)
        assert "/healthz" in http and "SIGTERM" in http
        assert "binds `$HOST:$PORT`" in http
        jsonl = run_contract_text("jsonl", language)
        assert '{"op": "ping"}' in jsonl and "standard input" in jsonl
        with pytest.raises(ValueError):
            run_contract_text("cli", language)

    def test_unknown_language_rejected(self) -> None:
        with pytest.raises(ValueError):
            resolve_allow("cobol", None)
        with pytest.raises(ValueError):
            skeleton_files("http", "cobol", [], title="t")


# ---------------------------------------------------------------------------------------------
# validator
# ---------------------------------------------------------------------------------------------


def _doc(family: str = "crud-relational") -> dict[str, Any]:
    return generate_instance(
        family, 3, Difficulty(entities=1, traps=2, concurrency=1), "python"
    ).probes


@pytest.fixture(params=["crud-relational", "job-queue"])
def good_doc(request: pytest.FixtureRequest) -> dict[str, Any]:
    return copy.deepcopy(_doc(request.param))


class TestValidator:
    def test_generated_docs_are_valid(self, good_doc: dict[str, Any]) -> None:
        assert validate_probes(good_doc) == []
        check_probes(good_doc)

    def test_top_level_shape(self, good_doc: dict[str, Any]) -> None:
        assert validate_probes([]) == ["probes.json must be an object"]
        good_doc["contract"] = "grpc"
        assert any("contract" in e for e in validate_probes(good_doc))
        doc = copy.deepcopy(_doc())
        doc["groups"].pop("perf")
        assert any("core/trap/perf" in e for e in validate_probes(doc))
        doc = copy.deepcopy(_doc())
        doc["weights"] = {"core": 0.5, "trap": 0.5, "perf": 0.5}
        assert any("weights sum" in e for e in validate_probes(doc))
        doc = copy.deepcopy(_doc())
        doc["pass_threshold"] = 1.5
        assert any("pass_threshold" in e for e in validate_probes(doc))

    def test_unknown_template_variable(self, good_doc: dict[str, Any]) -> None:
        step = good_doc["groups"]["core"][0]["steps"][0]
        step["op"] = (
            {"json": {"op": "x", "id": "{{never_saved}}"}}
            if (good_doc["contract"] == "jsonl")
            else {"method": "GET", "path": "/x/{{never_saved}}"}
        )
        assert any("never_saved" in e for e in validate_probes(good_doc))

    def test_index_variable_needs_repeat(self, good_doc: dict[str, Any]) -> None:
        step = good_doc["groups"]["core"][0]["steps"][0]
        step["op"] = {"json": {"op": "{{i}}"}} if good_doc["contract"] == "jsonl" else {
            "method": "GET", "path": "/{{i}}",
        }  # fmt: skip
        assert any("'i'" in e for e in validate_probes(good_doc))
        step["repeat"] = 3
        assert not any("'i'" in e for e in validate_probes(good_doc))

    def test_var_matcher_must_be_in_scope(self, good_doc: dict[str, Any]) -> None:
        step = good_doc["groups"]["core"][0]["steps"][0]
        step["expect"] = {"json": {"id": {"$var": "ghost"}}}
        assert any("ghost" in e for e in validate_probes(good_doc))

    @pytest.mark.parametrize(
        "matcher",
        [
            {"$bogus": 1},
            {"$len": 1, "$type": "array"},
            {"$type": "uuid"},
            {"$len": "3"},
            {"$regex": "("},
            {"$in": 3},
            {"$gte": "a"},
            {"$sorted_by": "created_at"},
            {"$sorted_by": "created_at", "order": "up"},
            {"$len": 1, "extra": 2},
            {"$each": {"$nope": 1}},
        ],
    )
    def test_bad_matchers_are_reported(self, good_doc: dict[str, Any], matcher: Any) -> None:
        good_doc["groups"]["core"][0]["steps"][0]["expect"] = {"json": matcher}
        assert validate_probes(good_doc), matcher

    def test_good_directive_forms_pass(self, good_doc: dict[str, Any]) -> None:
        good_doc["groups"]["core"][0]["steps"][0]["expect"] = {
            "json": {
                "a": {"$any": True}, "b": {"$type": "array"}, "c": {"$len_min": 1},
                "d": {"$regex": "^x"}, "e": {"$in": [1, 2]}, "f": {"$gte": 0},
                "g": {"$sorted_by": "x", "order": "desc", "tiebreak": "id"},
                "h": {"$set_equals": [1]}, "i": {"$each": {"$type": "string"}},
                "j": {"$contains": {"k": 1}}, "l": {"$not": {"$any": True}},
            }
        }  # fmt: skip
        assert validate_probes(good_doc) == []

    def test_http_op_and_expect_checks(self) -> None:
        doc = copy.deepcopy(_doc("crud-relational"))
        step = doc["groups"]["core"][0]["steps"][0]
        step["op"]["method"] = "FETCH"
        assert any("method" in e for e in validate_probes(doc))
        step["op"]["method"] = "GET"
        step["op"]["path"] = "healthz"
        assert any("path" in e for e in validate_probes(doc))
        step["op"]["path"] = "/healthz"
        step["op"]["extra"] = 1
        assert any("unknown op keys" in e for e in validate_probes(doc))
        del step["op"]["extra"]
        step["expect"] = {"status": "200"}
        assert any("expect.status" in e for e in validate_probes(doc))
        step["expect"] = {"exit_code": 0}
        assert any("cli-only" in e for e in validate_probes(doc))

    def test_jsonl_op_and_expect_checks(self) -> None:
        doc = copy.deepcopy(_doc("job-queue"))
        step = doc["groups"]["core"][0]["steps"][0]
        step["op"] = {"method": "GET"}
        assert validate_probes(doc)
        step["op"] = {"json": {"op": "ping"}}
        step["expect"] = {"status": 200}
        assert any("http-only" in e for e in validate_probes(doc))

    def test_probe_level_checks(self, good_doc: dict[str, Any]) -> None:
        core = good_doc["groups"]["core"]
        core[1]["id"] = core[0]["id"]
        assert any("duplicate probe id" in e for e in validate_probes(good_doc))
        core[0]["trap"] = "dedupe_key"
        assert any("only allowed in the trap group" in e for e in validate_probes(good_doc))
        trap = good_doc["groups"]["trap"][0]
        del trap["trap"]
        assert any("needs a 'trap' key" in e for e in validate_probes(good_doc))
        trap["trap"] = "made_up"
        assert any("not in the catalogue" in e for e in validate_probes(good_doc))
        trap["trap"] = "dedupe_key"
        trap["steps"] = []
        assert any("at least 1 step" in e for e in validate_probes(good_doc))
        trap["steps"] = good_doc["groups"]["core"][0]["steps"]
        trap["parallel"] = []
        assert any("parallel" in e for e in validate_probes(good_doc))

    def test_after_sees_only_steps_variables(self) -> None:
        doc = {
            "version": "1.0",
            "contract": "jsonl",
            "startup_timeout_seconds": 5,
            "groups": {
                "core": [
                    {
                        "id": "p",
                        "steps": [{"op": {"json": {"op": "ping"}}}],
                        "parallel": [
                            [{"op": {"json": {"op": "ping"}}, "save": {"only_in_parallel": "$.x"}}]
                        ],
                        "after": [{"op": {"json": {"op": "x", "v": "{{only_in_parallel}}"}}}],
                    }
                ],
                "trap": [],
                "perf": {},
            },
        }
        errs = validate_probes(doc)
        assert any("only_in_parallel" in e for e in errs)
        # parallel-saved variables are visible later in the SAME list
        doc["groups"]["core"][0]["parallel"][0].append(
            {"op": {"json": {"op": "x", "v": "{{only_in_parallel}}"}}}
        )
        del doc["groups"]["core"][0]["after"]
        assert not any("only_in_parallel" in e for e in validate_probes(doc))

    def test_save_paths_must_be_jsonpath_lite(self, good_doc: dict[str, Any]) -> None:
        step = good_doc["groups"]["core"][0]["steps"][0]
        step["save"] = {"x": "$..deep"}
        assert any("JSONPath-lite" in e for e in validate_probes(good_doc))
        step["save"] = {"x": "$.items[0].id"}
        assert validate_probes(good_doc) == []

    def test_perf_spec_invariants(self, good_doc: dict[str, Any]) -> None:
        def broken(mutate: Any) -> list[str]:
            doc = copy.deepcopy(good_doc)
            mutate(doc["groups"]["perf"])
            return validate_probes(doc)

        assert any("sizes" in e for e in broken(lambda p: p.update(sizes=[100])))
        assert any("sizes" in e for e in broken(lambda p: p.update(sizes=[1000, 100])))
        assert any("weights sum" in e for e in broken(lambda p: p["mix"][0].update(weight=0.9)))
        assert any("expected_alpha" in e for e in broken(lambda p: p["expected_alpha"].pop(
            next(iter(p["expected_alpha"])))))  # fmt: skip
        assert any("repeat" in e for e in broken(lambda p: p["load"].pop("repeat")))
        assert any("checks" in e for e in broken(lambda p: p.update(checks=["alpha", "magic"])))
        assert any("missing keys" in e for e in broken(lambda p: p.pop("ops_per_size")))
        assert any("unique string" in e for e in broken(
            lambda p: p["mix"][1].update(name=p["mix"][0]["name"])))  # fmt: skip

    def test_mix_steps_may_only_use_builtins_index_and_size(self, good_doc: dict[str, Any]) -> None:
        def op(var: str) -> dict[str, Any]:
            return (
                {"json": {"op": "x", "v": "{{" + var + "}}"}}
                if good_doc["contract"] == "jsonl"
                else {"method": "GET", "path": "/{{" + var + "}}"}
            )

        doc = copy.deepcopy(good_doc)
        perf = doc["groups"]["perf"]
        # a variable saved by the load step is NOT visible to a mix step (not guaranteed)
        perf["load"]["save"] = {"saved_by_load": "$.id"}
        perf["mix"][0]["step"]["op"] = op("saved_by_load")
        assert any("saved_by_load" in e for e in validate_probes(doc))
        # {{i}} (random index in [0, N)) and {{N}} (size) are fine in a mix step
        perf["mix"][0]["step"]["op"] = op("i")
        perf["mix"][1]["step"]["op"] = op("N")
        assert validate_probes(doc) == []
        perf["mix"][0]["step"]["repeat"] = 2
        assert any("repeat is not allowed" in e for e in validate_probes(doc))

    def test_check_probes_raises_with_summary(self) -> None:
        with pytest.raises(ValueError, match="invalid probes.json"):
            check_probes({"version": "9"})
