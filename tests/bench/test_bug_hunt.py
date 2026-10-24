"""T-Pf4Mix `bug-hunt` family: planted faults are real (each changes a probe response), the set
still imports, and everything else is the order-book instance for the same seed."""

from __future__ import annotations

import random

import pytest

from agent_orchestrator.bench.gen import FAMILY_REGISTRY
from agent_orchestrator.bench.gen.base import Difficulty
from agent_orchestrator.bench.gen.bug_hunt import (
    APP_ENTRY,
    BUGS_BY_LEVEL,
    _killed,
    _load,
    plant_bugs,
)
from agent_orchestrator.bench.gen.order_book import REFERENCE_PROGRAM
from agent_orchestrator.bench.gen.refs import REFERENCE_DIR
from agent_orchestrator.bench.gen.validate import validate_probes

D = Difficulty(entities=2, traps=1, concurrency=1, scale=1)


def test_instance_matches_order_book_except_the_buggy_app() -> None:
    bug = FAMILY_REGISTRY["bug-hunt"]().generate(11, D, "python")
    book = FAMILY_REGISTRY["order-book"]().generate(11, D, "python")
    assert bug.probes == book.probes and bug.spec_md == book.spec_md
    assert bug.reference == book.reference
    assert bug.family == "bug-hunt" and len(bug.limits["planted_faults"]) == BUGS_BY_LEVEL[1]
    assert bug.skeleton[APP_ENTRY] != book.skeleton[APP_ENTRY]
    assert "reference" not in bug.skeleton[APP_ENTRY].lower().split('"""')[1]
    assert validate_probes(bug.probes) == []


def test_buggy_app_imports_and_fails_probes_but_reference_passes() -> None:
    bug = FAMILY_REGISTRY["bug-hunt"]().generate(12, D, "python")
    probes = bug.probes["groups"]["core"] + bug.probes["groups"]["trap"]
    assert _load(bug.skeleton[APP_ENTRY]) is not None
    assert _killed(bug.skeleton[APP_ENTRY], bug.limits, probes) is True
    reference = (REFERENCE_DIR / REFERENCE_PROGRAM).read_text()
    assert _killed(reference, bug.limits, probes) is False
    assert _killed("while True:\n    pass\n", bug.limits, probes) is None  # hang -> rejected


def test_deterministic_and_python_only() -> None:
    fam = FAMILY_REGISTRY["bug-hunt"]()
    assert fam.generate(13, D, "python") == fam.generate(13, D, "python")
    with pytest.raises(ValueError, match="Python only"):
        fam.generate(13, D, "go")


def test_too_many_faults_requested_is_an_error() -> None:
    source = "class Engine:\n    def __init__(self, limits):\n        pass\n"
    with pytest.raises(ValueError, match="valid faults"):
        plant_bugs(source, random.Random(0), 1, {}, [])


def test_generation_works_off_the_main_thread() -> None:
    import threading

    out: list[object] = []
    th = threading.Thread(
        target=lambda: out.append(FAMILY_REGISTRY["bug-hunt"]().generate(14, D, "python"))
    )
    th.start()
    th.join(120)
    assert out and len(out[0].limits["planted_faults"]) == BUGS_BY_LEVEL[1]  # type: ignore[attr-defined]
