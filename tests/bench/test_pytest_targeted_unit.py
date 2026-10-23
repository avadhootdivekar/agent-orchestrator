"""Pure-function unit tests for `bench/pytest_targeted.py` (no subprocess, no git)."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_orchestrator.bench.pytest_targeted import (
    F2P_WEIGHT,
    P2P_WEIGHT,
    _build_argv,
    _group_stats,
    _junit_node_id,
    _score,
    parse_junit,
    parse_report_lines,
    sample_pass_to_pass,
)


def test_sample_is_deterministic_bounded_and_order_independent() -> None:
    ids = [f"tests/t.py::test_{i:03d}" for i in range(100)]
    first = sample_pass_to_pass(ids, 30)
    assert len(first) == 30 and first == sorted(first)
    assert sample_pass_to_pass(list(reversed(ids)), 30) == first  # same for every subject
    assert sample_pass_to_pass(ids + ids[:5], 30) == first  # duplicates ignored
    assert sample_pass_to_pass(ids[:10], 30) == sorted(ids[:10])  # under the cap: all of them
    assert sample_pass_to_pass(ids, 0) == []


@pytest.mark.parametrize(
    ("f2p", "p2p", "expected"),
    [
        (1.0, 1.0, 1.0),
        (0.0, 1.0, P2P_WEIGHT),
        (1.0, 0.0, F2P_WEIGHT),
        (0.5, 0.5, 0.5),
        (0.5, None, 0.5),  # empty p2p group: weight renormalises
        (None, 0.25, 0.25),
        (None, None, 0.0),
    ],
)
def test_score_weights(f2p: float | None, p2p: float | None, expected: float) -> None:
    assert _score(f2p, p2p, timed_out=False) == pytest.approx(expected)


def test_timed_out_run_scores_zero() -> None:
    assert _score(1.0, 1.0, timed_out=True) == 0.0


def test_group_stats_skip_semantics() -> None:
    statuses = {"a": "passed", "b": "skipped", "c": "failed", "d": "missing"}
    lenient = _group_stats(statuses, strict=frozenset())
    assert (lenient["total"], lenient["passed"]) == (3, 1)  # skip excluded from denominator
    strict = _group_stats(statuses, strict=frozenset({"b"}))
    assert (strict["total"], strict["passed"]) == (4, 1)  # explicit f2p skip == failure
    assert _group_stats({}, strict=frozenset())["rate"] is None


def test_report_line_fallback_parser() -> None:
    out = (
        "PASSED tests/a.py::test_ok\n"
        "PASSED tests/a.py::test_param[1-x]\n"
        "FAILED tests/a.py::TestC::test_bad - assert 1 == 2\n"
        "ERROR tests/broken.py\n"
        "SKIPPED [1] tests/a.py:7: no\n"
    )
    assert parse_report_lines(out).statuses == {
        "tests/a.py::test_ok": "passed",
        "tests/a.py::test_param[1-x]": "passed",
        "tests/a.py::TestC::test_bad": "failed",
        "tests/broken.py": "failed",
    }


def test_junit_classname_resolves_modules_classes_and_collection_errors(tmp_path: Path) -> None:
    (tmp_path / "tests" / "sub").mkdir(parents=True)
    (tmp_path / "tests" / "test_a.py").write_text("")
    (tmp_path / "tests" / "sub" / "test_b.py").write_text("")
    assert _junit_node_id("tests.test_a", "test_x", tmp_path) == "tests/test_a.py::test_x"
    assert (
        _junit_node_id("tests.test_a.TestC", "test_x", tmp_path) == "tests/test_a.py::TestC::test_x"
    )
    assert _junit_node_id("tests.sub.test_b", "test_y[1]", tmp_path) == (
        "tests/sub/test_b.py::test_y[1]"
    )
    assert _junit_node_id("", "tests.sub.test_b", tmp_path) == "tests/sub/test_b.py"
    assert _junit_node_id("pkg.unknown", "test_z", tmp_path) == "pkg.unknown::test_z"  # best effort


def test_junit_parse_flags_timeouts_and_tolerates_garbage(tmp_path: Path) -> None:
    (tmp_path / "t.py").write_text("")
    xml = tmp_path / "j.xml"
    xml.write_text(
        "<testsuites><testsuite>"
        '<testcase classname="t" name="test_slow">'
        '<failure message="Failed: Timeout >60.0s"/></testcase>'
        '<testcase classname="t" name="test_ok"/>'
        '<testcase classname="t" name="test_skip"><skipped/></testcase>'
        "</testsuite></testsuites>"
    )
    parsed = parse_junit(xml, tmp_path)
    assert parsed is not None
    assert parsed.statuses == {
        "t.py::test_slow": "failed",
        "t.py::test_ok": "passed",
        "t.py::test_skip": "skipped",
    }
    assert parsed.timeouts == ["t.py::test_slow"]
    xml.write_text("<not-xml")
    assert parse_junit(xml, tmp_path) is None
    assert parse_junit(tmp_path / "missing.xml", tmp_path) is None


def test_build_argv_renders_placeholders_and_appends_fixed_flags(tmp_path: Path) -> None:
    argv = _build_argv(
        "{python} -m pytest -q {targets}",
        "/opt/py thon/bin/python",
        ["tests/a b.py::t[x y]", "tests/c.py"],
        tmp_path,
        tmp_path / "g",
        tmp_path / "j.xml",
        60,
    )
    assert argv[0] == "/opt/py thon/bin/python"  # quoted then re-split: spaces survive
    assert "tests/a b.py::t[x y]" in argv and "tests/c.py" in argv
    assert f"--junitxml={tmp_path / 'j.xml'}" in argv and "--timeout=60" in argv
    assert {"-rA", "--continue-on-collection-errors", f"--rootdir={tmp_path}"} <= set(argv)
    no_timeout = _build_argv(
        "{python} -m pytest {targets}", "py", ["t"], tmp_path, tmp_path, tmp_path / "j", None
    )
    assert not any(a.startswith("--timeout") for a in no_timeout)
