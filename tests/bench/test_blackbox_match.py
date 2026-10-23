"""Unit tests for the pure parts of bench/blackbox.py (T-Bx2Cd3): the matcher language
(bench-probe-dsl.md §3.4, every directive plus mismatch paths and definition errors),
JSONPath-lite, templating, and the perf/score maths. No sandbox, no processes.
"""

from __future__ import annotations

import random
import re
from datetime import UTC, datetime
from typing import Any

import pytest

from agent_orchestrator.bench.blackbox import (
    MISSING,
    PerfRun,
    Templater,
    evaluate_perf,
    fit_alpha,
    json_path_get,
    match,
    percentile,
    validate_json_path,
    validate_matcher,
    weighted_score,
)
from agent_orchestrator.bench.errors import GraderError

# ---------------------------------------------------------------------------
# structural matching: scalars / objects / arrays
# ---------------------------------------------------------------------------


class TestStructural:
    def test_scalar_equality(self) -> None:
        assert match(5, 5) == []
        assert match("a", "a") == []
        assert match(None, None) == []
        assert match(5, 5.0) == []  # JSON numbers: 5 == 5.0

    def test_scalar_mismatch_names_path_and_values(self) -> None:
        assert match(5, 6) == ["$: expected 5, got 6"]
        assert match("a", "b", "$.name") == ['$.name: expected "a", got "b"']

    def test_bool_is_not_a_number(self) -> None:
        assert match(1, True)
        assert match(True, 1)
        assert match(False, 0)
        assert match(True, True) == []

    def test_null_vs_missing_vs_value(self) -> None:
        assert match(None, 0)
        assert match({"a": None}, {"a": None}) == []
        assert match({"a": None}, {}) == ["$.a: missing"]

    def test_object_allows_extra_keys_in_actual(self) -> None:
        assert match({"a": 1}, {"a": 1, "b": 2}) == []

    def test_object_missing_key_and_nested_path(self) -> None:
        assert match({"a": {"b": 1}}, {"a": {}}) == ["$.a.b: missing"]
        assert match({"a": {"b": 1}}, {"a": {"b": 2}}) == ["$.a.b: expected 1, got 2"]

    def test_object_vs_non_object(self) -> None:
        assert match({"a": 1}, [1]) == ["$: expected object, got array"]
        assert match({"a": 1}, None) == ["$: expected object, got null"]

    def test_array_same_length_elementwise(self) -> None:
        assert match([1, {"a": 2}], [1, {"a": 2, "x": 0}]) == []
        assert match([1, 2], [1, 3]) == ["$[1]: expected 2, got 3"]

    def test_array_length_mismatch(self) -> None:
        assert match([1, 2], [1]) == ["$: expected array of length 2, got length 1"]
        assert match([1], "x") == ["$: expected array, got string"]

    def test_several_mismatches_are_all_reported(self) -> None:
        out = match({"a": 1, "b": 2}, {"a": 9, "b": 8})
        assert out == ["$.a: expected 1, got 9", "$.b: expected 2, got 8"]

    def test_missing_actual_at_top_level(self) -> None:
        assert match({"a": 1}, MISSING) == ["$: missing"]
        assert match(1, MISSING) == ["$: missing"]


# ---------------------------------------------------------------------------
# directives
# ---------------------------------------------------------------------------


class TestDirectives:
    def test_any(self) -> None:
        assert match({"a": {"$any": True}}, {"a": None}) == []
        assert match({"a": {"$any": True}}, {"a": [1]}) == []
        assert match({"a": {"$any": True}}, {}) == ["$.a: expected a present value, got <missing>"]

    @pytest.mark.parametrize(
        ("type_name", "good", "bad"),
        [
            ("string", "x", 1),
            ("number", 1.5, "1"),
            ("number", 3, True),
            ("integer", 3, 3.5),
            ("integer", 3.0, "3"),
            ("boolean", False, 0),
            ("array", [], {}),
            ("object", {}, []),
            ("null", None, 0),
        ],
    )
    def test_type(self, type_name: str, good: Any, bad: Any) -> None:
        assert match({"$type": type_name}, good) == []
        out = match({"$type": type_name}, bad)
        assert len(out) == 1 and f"expected type {type_name}" in out[0]

    def test_type_of_missing_value_fails(self) -> None:
        assert match({"a": {"$type": "null"}}, {}) == ["$.a: expected type null, got missing"]

    def test_len_family(self) -> None:
        assert match({"$len": 2}, [1, 2]) == []
        assert match({"$len": 2}, "ab") == []
        assert match({"$len": 1}, {"a": 1}) == []
        assert match({"$len": 3}, [1]) == ["$: expected length 3, got length 1"]
        assert match({"$len_min": 1}, []) == ["$: expected length >= 1, got length 0"]
        assert match({"$len_min": 1}, [0]) == []
        assert match({"$len_max": 1}, [1, 2]) == ["$: expected length <= 1, got length 2"]
        assert match({"$len_max": 2}, [1, 2]) == []
        assert "needs a string/array/object" in match({"$len": 1}, 5)[0]

    def test_regex_uses_search(self) -> None:
        assert match({"$regex": "^[0-9a-f-]{36}$"}, "123e4567-e89b-12d3-a456-426614174000") == []
        assert match({"$regex": "bc"}, "abcd") == []
        assert "does not match" in match({"$regex": "^a$"}, "b")[0]
        assert "needs a string" in match({"$regex": "a"}, 1)[0]

    def test_in(self) -> None:
        assert match({"$in": [1, "a", None]}, "a") == []
        assert match({"$in": [1, 2]}, 3) == ["$: expected one of [1, 2], got 3"]
        assert match({"$in": [1, 2]}, True)  # bool is not 1

    def test_gte_lte(self) -> None:
        assert match({"$gte": 0}, 0) == []
        assert match({"$gte": 0}, -1) == ["$: expected >= 0, got -1"]
        assert match({"$lte": 100}, 100) == []
        assert match({"$lte": 100}, 101) == ["$: expected <= 100, got 101"]
        assert match({"$gte": "2026-01-01"}, "2026-06-01") == []  # ISO strings compare too
        assert "cannot compare" in match({"$gte": 0}, "x")[0]
        assert "cannot compare" in match({"$lte": 0}, True)[0]

    def test_sorted_by_asc_desc_and_tiebreak(self) -> None:
        rows = [{"t": 1, "id": 1}, {"t": 1, "id": 2}, {"t": 2, "id": 3}]
        assert match({"$sorted_by": "t"}, rows) == []
        assert match({"$sorted_by": "t", "order": "asc", "tiebreak": "id"}, rows) == []
        assert match({"$sorted_by": "t", "order": "desc"}, rows)
        assert match({"$sorted_by": "t", "order": "desc"}, list(reversed(rows))) == []
        # equal keys but tiebreak out of order
        bad_tie = [{"t": 1, "id": 2}, {"t": 1, "id": 1}]
        assert match({"$sorted_by": "t"}, bad_tie) == []
        out = match({"$sorted_by": "t", "tiebreak": "id"}, bad_tie)
        assert "not ordered by id" in out[0]
        assert "not sorted by t asc" in match({"$sorted_by": "t"}, [{"t": 2}, {"t": 1}])[0]

    def test_sorted_by_degenerate_inputs(self) -> None:
        assert match({"$sorted_by": "t"}, []) == []
        assert match({"$sorted_by": "t"}, [{"t": 1}]) == []
        assert "needs an array" in match({"$sorted_by": "t"}, {"t": 1})[0]
        assert "lacks sort field" in match({"$sorted_by": "t"}, [{"t": 1}, {"x": 2}])[0]
        assert "not comparable" in match({"$sorted_by": "t"}, [{"t": 1}, {"t": "a"}])[0]
        assert match({"$sorted_by": "a.b"}, [{"a": {"b": 1}}, {"a": {"b": 2}}]) == []  # dotted

    def test_set_equals_is_a_multiset(self) -> None:
        assert match({"$set_equals": [1, 2, 2]}, [2, 1, 2]) == []
        out = match({"$set_equals": [1, 2, 2]}, [1, 2])
        assert "multiset differs" in out[0] and "missing" in out[0]
        assert match({"$set_equals": [{"a": 1}, {"b": 2}]}, [{"b": 2}, {"a": 1}]) == []
        assert match({"$set_equals": [1]}, [True])  # bool != 1
        assert "needs an array" in match({"$set_equals": [1]}, 1)[0]

    def test_each(self) -> None:
        assert match({"$each": {"$type": "integer"}}, [1, 2, 3]) == []
        assert match({"$each": {"$type": "integer"}}, []) == []  # vacuous
        out = match({"$each": {"id": {"$gte": 1}}}, [{"id": 1}, {"id": 0}], "$.items")
        assert out == ["$.items[1].id: expected >= 1, got 0"]
        assert "needs an array" in match({"$each": {"$any": True}}, {})[0]

    def test_contains(self) -> None:
        assert match({"$contains": {"id": 2}}, [{"id": 1}, {"id": 2}]) == []
        out = match({"$contains": {"id": 3}}, [{"id": 1}, {"id": 2}], "$.items")
        assert "no element matches" in out[0] and "2 elements" in out[0]
        assert match({"$contains": 1}, []) != []
        assert "needs an array" in match({"$contains": 1}, "x")[0]

    def test_var(self) -> None:
        assert match({"$var": "id"}, 7, variables={"id": 7}) == []
        assert match({"id": {"$var": "id"}}, {"id": 8}, variables={"id": 7}) == [
            "$.id: expected variable id=7, got 8"
        ]
        assert match({"$var": "s"}, "x", variables={"s": "x"}) == []

    def test_var_unknown_is_a_definition_error(self) -> None:
        with pytest.raises(GraderError, match="unknown variable 'nope'"):
            match({"$var": "nope"}, 1, variables={"id": 1})

    def test_not_and_absence(self) -> None:
        assert match({"$not": {"$type": "string"}}, 5) == []
        assert match({"$not": {"$type": "string"}}, "x") == [
            '$: unexpectedly matched {"$type": "string"}'
        ]
        # absence assertion: the key must not be there
        assert match({"a": {"$not": {"$any": True}}}, {}) == []
        assert match({"a": {"$not": {"$any": True}}}, {"a": 1})
        assert match({"a": {"$not": {"$any": True}}}, {"a": None})  # null is still present

    def test_nested_directives_compose(self) -> None:
        exp = {"items": {"$each": {"tags": {"$contains": {"$regex": "^x"}}}}, "n": {"$gte": 1}}
        act = {"items": [{"tags": ["a", "xb"]}], "n": 1}
        assert match(exp, act) == []
        act["items"][0]["tags"] = ["a"]  # type: ignore[index]
        assert "no element matches" in match(exp, act)[0]


# ---------------------------------------------------------------------------
# definition errors (never mismatches)
# ---------------------------------------------------------------------------


class TestDefinitionErrors:
    @pytest.mark.parametrize(
        ("bad", "needle"),
        [
            ({"$bogus": 1}, "unknown matcher directive"),
            ({"$any": True, "$type": "string"}, "several $ directives"),
            ({"$len": 1, "extra": 1}, "takes no sibling keys"),
            ({"$any": False}, "must be"),
            ({"$type": "date"}, "$type expects one of"),
            ({"$len": -1}, "non-negative integer"),
            ({"$len": "x"}, "non-negative integer"),
            ({"$regex": "("}, "does not compile"),
            ({"$regex": 1}, "expects a string"),
            ({"$in": 1}, "expects an array"),
            ({"$set_equals": {}}, "expects an array"),
            ({"$gte": [1]}, "number or string"),
            ({"$sorted_by": ""}, "field name"),
            ({"$sorted_by": "a", "order": "up"}, "order must be one of"),
            ({"$sorted_by": "a", "tiebreak": 3}, "tiebreak"),
            ({"$var": ""}, "variable name"),
            ({"$each": {"$nope": 1}}, "unknown matcher directive"),
            ({"a": [{"$contains": {"$len": "x"}}]}, "non-negative integer"),
        ],
    )
    def test_bad_matcher_raises_grader_error(self, bad: dict[str, Any], needle: str) -> None:
        with pytest.raises(GraderError, match=re.escape(needle)):
            match(bad, 1)
        with pytest.raises(GraderError, match=re.escape(needle)):
            validate_matcher(bad)

    def test_validate_matcher_collects_var_names(self) -> None:
        names = validate_matcher({"a": {"$var": "x"}, "b": [{"$not": {"$var": "y"}}]})
        assert names == {"x", "y"}

    def test_sorted_by_modifiers_are_not_stray_keys(self) -> None:
        validate_matcher({"$sorted_by": "a", "order": "desc", "tiebreak": "id"})


# ---------------------------------------------------------------------------
# JSONPath-lite
# ---------------------------------------------------------------------------


class TestJsonPath:
    DOC = {"a": {"b": [10, {"c": "x"}]}, "n": None, "list": [1, 2, 3]}

    @pytest.mark.parametrize(
        ("path", "want"),
        [
            ("$", DOC),
            ("$.n", None),
            ("$.a.b[0]", 10),
            ("$.a.b[1].c", "x"),
            ("$.list[-1]", 3),
        ],
    )
    def test_lookups(self, path: str, want: Any) -> None:
        assert json_path_get(self.DOC, path) == want

    @pytest.mark.parametrize(
        "path", ["$.zz", "$.a.zz", "$.list[9]", "$.a[0]", "$.list.x", "$.list[-9]"]
    )
    def test_missing_returns_sentinel(self, path: str) -> None:
        assert json_path_get(self.DOC, path) is MISSING

    @pytest.mark.parametrize("path", ["a.b", "$.a[", "$..a", "$.a[x]", ""])
    def test_bad_path_is_a_definition_error(self, path: str) -> None:
        with pytest.raises(GraderError):
            validate_json_path(path)
        with pytest.raises(GraderError):
            json_path_get(self.DOC, path)


# ---------------------------------------------------------------------------
# templating
# ---------------------------------------------------------------------------


def _templater(seed: int = 1) -> Templater:
    return Templater(random.Random(seed), lambda: datetime(2026, 10, 23, 12, 30, 45, 123456, UTC))


class TestTemplater:
    def test_embedded_variables_are_stringified(self) -> None:
        t = _templater()
        assert t.render("/items/{{id}}?c={{cursor}}", {"id": 5, "cursor": "ab"}) == "/items/5?c=ab"
        assert t.render("{{ id }}-x", {"id": 5}) == "5-x"  # whitespace tolerated
        assert t.render("x{{o}}", {"o": {"k": [1]}}) == 'x{"k":[1]}'

    def test_whole_string_variable_keeps_its_type(self) -> None:
        t = _templater()
        assert t.render("{{id}}", {"id": 5}) == 5
        assert t.render({"parent": "{{id}}", "flag": "{{f}}"}, {"id": 5, "f": True}) == {
            "parent": 5,
            "flag": True,
        }
        assert t.render("{{s}}", {"s": "txt"}) == "txt"

    def test_index_rand_now_are_always_strings(self) -> None:
        t = _templater()
        assert t.render("{{i}}", {}, 3) == "3"
        assert t.render("n{{i}}", {}, 3) == "n3"
        assert t.render("{{now_iso}}", {}) == "2026-10-23T12:30:45.123Z"
        rand = t.render("{{rand}}", {})
        assert isinstance(rand, str) and len(rand) == 8 and int(rand, 16) >= 0

    def test_rand_is_fresh_per_evaluation_but_seeded(self) -> None:
        a, b = _templater(7), _templater(7)
        seq_a = [a.render("{{rand}}", {}) for _ in range(5)]
        assert len(set(seq_a)) == 5
        assert seq_a == [b.render("{{rand}}", {}) for _ in range(5)]
        assert seq_a != [_templater(8).render("{{rand}}", {}) for _ in range(5)]

    def test_renders_nested_structures_without_mutating_input(self) -> None:
        t = _templater()
        src = {"op": {"json": {"names": ["a{{i}}", "b{{i}}"], "n": 1}}}
        out = t.render(src, {}, 2)
        assert out == {"op": {"json": {"names": ["a2", "b2"], "n": 1}}}
        assert src["op"]["json"]["names"] == ["a{{i}}", "b{{i}}"]

    def test_unknown_variable_raises(self) -> None:
        with pytest.raises(GraderError, match="unknown template variable"):
            _templater().render("/x/{{nope}}", {"id": 1})
        with pytest.raises(GraderError, match="unknown template variable"):
            _templater().render("{{nope}}", {})

    def test_non_identifier_braces_are_left_alone(self) -> None:
        assert _templater().render("^a{{2}}$ {x}", {}) == "^a{{2}}$ {x}"


# ---------------------------------------------------------------------------
# perf maths and score
# ---------------------------------------------------------------------------


class TestPerfMath:
    def test_percentile_nearest_rank(self) -> None:
        xs = [float(i) for i in range(1, 101)]
        assert percentile(xs, 0.5) == 50.0
        assert percentile(xs, 0.95) == 95.0
        assert percentile([3.0], 0.95) == 3.0
        assert percentile([5.0, 1.0, 3.0], 0.5) == 3.0  # order-independent

    def test_fit_alpha_recovers_exponent(self) -> None:
        sizes = [100, 1000, 10000]
        assert fit_alpha([(n, 2.0) for n in sizes]) == pytest.approx(0.0, abs=1e-9)
        assert fit_alpha([(n, n * 0.01) for n in sizes]) == pytest.approx(1.0)
        assert fit_alpha([(n, (n**2) * 1e-4) for n in sizes]) == pytest.approx(2.0)

    def test_fit_alpha_needs_two_sizes_and_survives_zero_latency(self) -> None:
        assert fit_alpha([(100, 1.0)]) is None
        assert fit_alpha([(100, 1.0), (100, 2.0)]) is None
        assert fit_alpha([(10, 0.0), (100, 0.0)]) == pytest.approx(0.0)

    def _spec(self, **over: Any) -> dict[str, Any]:
        spec: dict[str, Any] = {
            "sizes": [100, 10000],
            "mix": [
                {"weight": 0.7, "name": "get", "class": "indexed_get", "step": {}},
                {"weight": 0.3, "name": "list", "class": "paginated_list", "step": {}},
            ],
            "expected_alpha": {"indexed_get": 0.3, "paginated_list": 0.3},
            "alpha_slack": 0.2,
            "ref_ratio_max": 5.0,
            "checks": ["alpha", "ref_ratio", "timeout"],
        }
        spec.update(over)
        return spec

    def _run(self, get_alpha: float, list_alpha: float, p95_at_max: float) -> PerfRun:
        run = PerfRun()
        for cls, alpha in (("indexed_get", get_alpha), ("paginated_list", list_alpha)):
            run.class_p95[cls] = {100: 1.0, 10000: 100.0**alpha}
        run.overall_p95 = {100: 1.0, 10000: p95_at_max}
        run.ops = {10000: {"get": {"p50_ms": 1.0, "p95_ms": 2.0}}}
        return run

    def test_all_checks_pass(self) -> None:
        detail, passed, total = evaluate_perf(
            self._spec(), self._run(0.1, 0.4, 2.0), self._run(0.1, 0.1, 1.0), enforce_ref=True
        )
        assert (passed, total) == (4, 4)
        assert detail["classes"]["indexed_get"]["alpha_pass"] is True
        assert detail["classes"]["paginated_list"]["alpha"] == pytest.approx(0.4, abs=1e-3)
        assert detail["ref_ratio_p95"] == pytest.approx(2.0)
        assert detail["ref_ratio_pass"] is True
        assert detail["timed_out"] is False
        assert detail["sizes"]["10000"]["get"] == {"p50_ms": 1.0, "p95_ms": 2.0}

    def test_alpha_over_slack_fails_that_class_only(self) -> None:
        detail, passed, total = evaluate_perf(
            self._spec(), self._run(0.1, 1.0, 2.0), self._run(0.1, 0.1, 1.0), enforce_ref=True
        )
        assert (passed, total) == (3, 4)
        assert detail["classes"]["paginated_list"]["alpha_pass"] is False

    def test_ref_ratio_enforced_only_for_same_language(self) -> None:
        art, ref = self._run(0.1, 0.1, 50.0), self._run(0.1, 0.1, 1.0)
        enforced, p_e, t_e = evaluate_perf(self._spec(), art, ref, enforce_ref=True)
        assert enforced["ref_ratio_pass"] is False and (p_e, t_e) == (3, 4)
        info, p_i, t_i = evaluate_perf(self._spec(), art, ref, enforce_ref=False)
        # ratio still reported, but neither pass/fail nor counted
        assert info["ref_ratio_p95"] == pytest.approx(50.0)
        assert info["ref_ratio_pass"] is None and info["ref_ratio_enforced"] is False
        assert (p_i, t_i) == (3, 3)

    def test_timeout_and_error_zero_the_timeout_check(self) -> None:
        art = self._run(0.1, 0.1, 2.0)
        art.timed_out = True
        _, passed, total = evaluate_perf(self._spec(), art, self._run(0, 0, 1), enforce_ref=True)
        assert (passed, total) == (3, 4)
        errored = PerfRun(error="load failed")
        detail, passed, total = evaluate_perf(self._spec(), errored, None, enforce_ref=True)
        assert passed == 0 and total == 4 and detail["error"] == "load failed"

    def test_checks_subset_controls_what_counts(self) -> None:
        _, passed, total = evaluate_perf(
            self._spec(checks=["timeout"]), self._run(5, 5, 99), None, enforce_ref=True
        )
        assert (passed, total) == (1, 1)


class TestWeightedScore:
    W = {"core": 0.5, "trap": 0.35, "perf": 0.15}

    def test_full_and_partial(self) -> None:
        score, bd = weighted_score({"core": (4, 4), "trap": (1, 2), "perf": (3, 3)}, self.W)
        assert score == pytest.approx(0.5 + 0.175 + 0.15)
        assert bd["trap"]["fraction"] == 0.5 and bd["core"]["effective_weight"] == 0.5

    def test_all_passing_is_exactly_one(self) -> None:
        assert weighted_score({"core": (3, 3), "trap": (2, 2), "perf": (5, 5)}, self.W)[0] == 1.0

    def test_empty_group_renormalises_weights(self) -> None:
        # no perf group: core+trap carry the whole score
        score, bd = weighted_score({"core": (2, 2), "trap": (2, 2), "perf": (0, 0)}, self.W)
        assert score == 1.0 and bd["perf"]["effective_weight"] == 0.0
        score, _ = weighted_score({"core": (2, 2), "trap": (0, 2), "perf": (0, 0)}, self.W)
        assert score == pytest.approx(0.5 / 0.85, abs=1e-5)

    def test_zero_everything(self) -> None:
        assert weighted_score({"core": (0, 3), "trap": (0, 1), "perf": (0, 2)}, self.W)[0] == 0.0
        assert weighted_score({"core": (0, 0), "trap": (0, 0), "perf": (0, 0)}, self.W)[0] == 0.0

    def test_custom_weights(self) -> None:
        score, _ = weighted_score(
            {"core": (1, 1), "trap": (0, 1), "perf": (1, 1)}, {"core": 1, "trap": 1, "perf": 2}
        )
        assert score == pytest.approx(0.75)
