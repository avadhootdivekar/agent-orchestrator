"""Unit + integration tests for bench/results.py (T-Rpt3Wq acceptance criteria).

Network-free throughout: every run driving these writers goes through `run_suite` with
`FakeSubject`/`FakeGrader` (grader `type: fake`), mirroring `tests/bench/test_runner.py`'s
own local fixture-builder pattern rather than reusing `suite_factory`/`subject_factory`
(those default to a `pytest`-typed grader that never "solves" without a real fixture).

`out_dir`/comparison output dirs are always a per-test `tmp_path` -- never the committed
`benchmarks/results/` tree (CLAUDE.md / assigning-message rule).

Assertions on `summary.md`/`comparison.md` are "golden-ish": headers/rows/sections must be
present, not byte-exact, so formatting tweaks don't make every test fragile.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from agent_orchestrator.bench import runner
from agent_orchestrator.bench.errors import ResultsError
from agent_orchestrator.bench.metrics import Aggregate, aggregate
from agent_orchestrator.bench.results import (
    ComparisonRecord,
    build_comparison,
    compute_compare_id,
    load_run,
    render_winners,
    write_comparison,
    write_summary_md,
)
from agent_orchestrator.bench.runner import BenchRunRecord

_FIXED_NOW = datetime(2026, 7, 22, 12, 0, 0, tzinfo=UTC)


def _fixed_clock() -> datetime:
    return _FIXED_NOW


def _uniq() -> str:
    return uuid.uuid4().hex[:8]


# ---------------------------------------------------------------------------
# Local fixture builders (mirrors tests/bench/test_runner.py -- `type: fake` grader
# needs no real pytest fixture, just the placeholder instruction/fixture files).
# ---------------------------------------------------------------------------


def _fake_task(
    task_id: str, *, grader: dict[str, Any] | None = None, category: str = "bugfix"
) -> dict[str, Any]:
    return {
        "id": task_id,
        "category": category,
        "instruction": f"tasks/{task_id}/instruction.md",
        "fixture": f"tasks/{task_id}/fixture",
        "grader": grader or {"type": "fake"},
        "timeout_seconds": 60,
        "tags": [],
    }


def _write_suite(base: Path, suite_id: str, tasks: list[dict[str, Any]], **extra: Any) -> Path:
    for task in tasks:
        instruction = base / task["instruction"]
        instruction.parent.mkdir(parents=True, exist_ok=True)
        instruction.write_text("# instructions\n")
        fixture = base / task["fixture"]
        fixture.mkdir(parents=True, exist_ok=True)
        (fixture / "placeholder.txt").write_text("fixture content\n")

    data: dict[str, Any] = {
        "version": "1.0",
        "id": suite_id,
        "domain": "software",
        "description": "results test suite",
        "tasks": tasks,
    }
    data.update(extra)
    path = base / f"{suite_id}-suite.json"
    path.write_text(json.dumps(data, indent=2))
    return path


def _write_fake_subject(base: Path, subject_id: str, **extra: Any) -> Path:
    data: dict[str, Any] = {"version": "1.0", "id": subject_id, "type": "fake"}
    data.update(extra)
    path = base / f"{subject_id}-subject.json"
    path.write_text(json.dumps(data, indent=2))
    return path


def _run(
    tmp_path: Path,
    *,
    suite_id: str,
    subject_id: str,
    tasks: list[dict[str, Any]],
    out_dir: Path,
    subject_extra: dict[str, Any] | None = None,
    suite_extra: dict[str, Any] | None = None,
) -> BenchRunRecord:
    suite_path = _write_suite(tmp_path, suite_id, tasks, **(suite_extra or {}))
    subject_path = _write_fake_subject(tmp_path, subject_id, **(subject_extra or {}))
    return runner.run_suite(suite_path, subject_path, out_dir=out_dir, clock=_fixed_clock)


# ---------------------------------------------------------------------------
# load_run
# ---------------------------------------------------------------------------


def test_load_run_from_dir_and_from_file(tmp_path: Path) -> None:
    uniq = _uniq()
    out_dir = tmp_path / "results"
    record = _run(
        tmp_path,
        suite_id=f"rs-load-{uniq}",
        subject_id=f"fk-load-{uniq}",
        tasks=[_fake_task("t1")],
        out_dir=out_dir,
    )
    run_dir = out_dir / record.bench_run_id

    from_dir = load_run(run_dir)
    assert from_dir.bench_run_id == record.bench_run_id
    from_file = load_run(run_dir / runner.RUN_JSON_FILENAME)
    assert from_file.bench_run_id == record.bench_run_id


def test_load_run_missing_raises_results_error(tmp_path: Path) -> None:
    with pytest.raises(ResultsError, match="No run.json found"):
        load_run(tmp_path / "does-not-exist")


def test_load_run_corrupt_json_raises_results_error(tmp_path: Path) -> None:
    run_dir = tmp_path / "corrupt-run"
    run_dir.mkdir()
    (run_dir / runner.RUN_JSON_FILENAME).write_text("{not valid json")
    with pytest.raises(ResultsError, match="Failed to read"):
        load_run(run_dir)


def test_load_run_schema_mismatch_raises_results_error(tmp_path: Path) -> None:
    run_dir = tmp_path / "bad-schema-run"
    run_dir.mkdir()
    (run_dir / runner.RUN_JSON_FILENAME).write_text(json.dumps({"totally": "not a run record"}))
    with pytest.raises(ResultsError, match="does not match the expected run.json schema"):
        load_run(run_dir)


# ---------------------------------------------------------------------------
# write_summary_md (AC1)
# ---------------------------------------------------------------------------


def test_write_summary_md_from_record_has_header_rows_and_footer(tmp_path: Path) -> None:
    uniq = _uniq()
    out_dir = tmp_path / "results"
    record = _run(
        tmp_path,
        suite_id=f"rs-sum-{uniq}",
        subject_id=f"fk-sum-{uniq}",
        tasks=[_fake_task("b-task"), _fake_task("a-task")],
        out_dir=out_dir,
    )
    run_dir = out_dir / record.bench_run_id

    summary_path = write_summary_md(record, run_dir)
    assert summary_path == run_dir / "summary.md"
    text = summary_path.read_text()

    assert "| Task | Category | Solved | Score | Wall(s) | Cost($) | Tokens(in/out)" in text
    assert "| a-task |" in text
    assert "| b-task |" in text
    # id-sorted rows regardless of suite-file order.
    assert text.index("| a-task |") < text.index("| b-task |")
    assert "## Aggregate" in text
    assert "solved: 2/2" in text


def test_write_summary_md_from_run_dir_loads_and_writes(tmp_path: Path) -> None:
    uniq = _uniq()
    out_dir = tmp_path / "results"
    record = _run(
        tmp_path,
        suite_id=f"rs-sumdir-{uniq}",
        subject_id=f"fk-sumdir-{uniq}",
        tasks=[_fake_task("t1")],
        out_dir=out_dir,
    )
    run_dir = out_dir / record.bench_run_id

    summary_path = write_summary_md(run_dir)
    assert summary_path.exists()
    assert "| t1 |" in summary_path.read_text()


def test_write_summary_md_record_without_run_dir_raises(tmp_path: Path) -> None:
    uniq = _uniq()
    record = _run(
        tmp_path,
        suite_id=f"rs-norundir-{uniq}",
        subject_id=f"fk-norundir-{uniq}",
        tasks=[_fake_task("t1")],
        out_dir=tmp_path / "results",
    )
    with pytest.raises(ResultsError, match="run_dir is required"):
        write_summary_md(record)


def test_write_summary_md_none_cost_renders_em_dash(tmp_path: Path) -> None:
    uniq = _uniq()
    out_dir = tmp_path / "results"
    # No fake_cost/fake_tokens set -> SubjectResult.cost_usd/tokens default to None.
    record = _run(
        tmp_path,
        suite_id=f"rs-none-{uniq}",
        subject_id=f"fk-none-{uniq}",
        tasks=[_fake_task("t1")],
        out_dir=out_dir,
    )
    run_dir = out_dir / record.bench_run_id
    text = write_summary_md(record, run_dir).read_text()

    assert record.tasks[0].cost_usd is None
    assert "| t1 | bugfix | yes | 1.00 |" in text
    assert "—" in text  # cost/tokens/turns column(s) render the em dash, not "None".
    assert "None" not in text


def test_write_summary_md_empty_run_no_rows_but_footer_present(tmp_path: Path) -> None:
    empty_record = BenchRunRecord(
        bench_run_id="2026-07-22-rs-empty-x-fk-empty-x",
        suite_id="rs-empty-x",
        domain="software",
        subject=runner.BenchRunSubjectInfo(id="fk-empty-x", type="fake", resolved_config={}),
        config_fingerprint="deadbeef",
        started_at=_FIXED_NOW.isoformat(),
        ended_at=_FIXED_NOW.isoformat(),
        env=runner.BenchRunEnv(ao_version="0.0.0-test", os="test-os"),
        tasks=[],
        aggregate=aggregate([]),
    )
    run_dir = tmp_path / "empty-run"
    text = write_summary_md(empty_record, run_dir).read_text()

    assert "| Task | Category |" in text  # header present
    assert "| --- |" in text  # separator present
    assert "## Aggregate" in text
    assert "solved: 0/0" in text
    assert "cost_per_solved: —" in text


# ---------------------------------------------------------------------------
# build_comparison / write_comparison (AC2-AC5)
# ---------------------------------------------------------------------------


def test_build_and_write_comparison_two_subjects_same_suite(tmp_path: Path) -> None:
    uniq = _uniq()
    suite_id = f"rs-cmp-{uniq}"
    out_dir = tmp_path / "results"

    winner = _run(
        tmp_path,
        suite_id=suite_id,
        subject_id=f"fk-winner-{uniq}",
        tasks=[_fake_task("t1"), _fake_task("t2")],
        out_dir=out_dir,
        subject_extra={"fake_cost": 0.01},
    )
    # `FakeGrader` grades independently of subject status -- `command: "false"` is the
    # scripted "never solved" verdict (mirrors `CommandGrader`'s exit-code convention;
    # see bench/graders.py's `FakeGrader` docstring), used here to give this subject a
    # deterministic solved=0 outcome regardless of what FakeSubject itself did.
    loser_tasks = [
        _fake_task("t1", grader={"type": "fake", "command": "false"}),
        _fake_task("t2", grader={"type": "fake", "command": "false"}),
    ]
    loser = _run(
        tmp_path,
        suite_id=suite_id,
        subject_id=f"fk-loser-{uniq}",
        tasks=loser_tasks,
        out_dir=out_dir,
        subject_extra={"fake_cost": 0.5},
    )

    run_dirs = [out_dir / winner.bench_run_id, out_dir / loser.bench_run_id]
    comparison = build_comparison(run_dirs, clock=_fixed_clock)

    assert comparison.suite_id == suite_id
    assert set(comparison.subjects) == {winner.subject.id, loser.subject.id}
    assert comparison.not_run == []
    assert comparison.task_ids == ["t1", "t2"]

    winner_agg = comparison.per_subject[winner.subject.id]
    loser_agg = comparison.per_subject[loser.subject.id]
    assert winner_agg is not None and winner_agg.solved == 2
    assert loser_agg is not None and loser_agg.solved == 0
    assert loser_agg.cost_per_solved is None  # AC2: None when solved==0

    json_path, md_path = write_comparison(comparison, tmp_path / "compare-out")
    assert json_path.exists() and md_path.exists()

    on_disk = json.loads(json_path.read_text())
    assert on_disk["suite_id"] == suite_id
    assert set(on_disk["matrix"]["t1"].keys()) == {winner.subject.id, loser.subject.id}
    assert on_disk["per_subject"][loser.subject.id]["cost_per_solved"] is None

    md_text = md_path.read_text()
    assert "## Per-subject" in md_text
    assert "## Per-task (solved / cost)" in md_text
    assert "## Winners" in md_text
    assert f"Highest solve rate: {winner.subject.id}" in md_text
    assert "Lowest cost per solved" in md_text
    # loser never solved anything -> excluded from the cost-per-solved race, winner wins it.
    assert f"Lowest cost per solved: {winner.subject.id}" in md_text


def test_build_comparison_refuses_different_suites(tmp_path: Path) -> None:
    uniq = _uniq()
    out_dir = tmp_path / "results"
    a = _run(
        tmp_path,
        suite_id=f"rs-suite-a-{uniq}",
        subject_id=f"fk-a-{uniq}",
        tasks=[_fake_task("t1")],
        out_dir=out_dir,
    )
    b = _run(
        tmp_path,
        suite_id=f"rs-suite-b-{uniq}",
        subject_id=f"fk-b-{uniq}",
        tasks=[_fake_task("t1")],
        out_dir=out_dir,
    )
    with pytest.raises(ResultsError, match="different suites"):
        build_comparison([out_dir / a.bench_run_id, out_dir / b.bench_run_id])


def test_build_comparison_refuses_different_schema_version(tmp_path: Path) -> None:
    uniq = _uniq()
    suite_id = f"rs-schema-{uniq}"
    out_dir = tmp_path / "results"
    a = _run(
        tmp_path,
        suite_id=suite_id,
        subject_id=f"fk-schema-a-{uniq}",
        tasks=[_fake_task("t1")],
        out_dir=out_dir,
    )
    b_dir = out_dir / f"2026-07-22-{suite_id}-fk-schema-b-{uniq}"
    b_dir.mkdir(parents=True)
    on_disk = json.loads((out_dir / a.bench_run_id / runner.RUN_JSON_FILENAME).read_text())
    on_disk["schema_version"] = "2.0"
    on_disk["subject"]["id"] = f"fk-schema-b-{uniq}"
    (b_dir / runner.RUN_JSON_FILENAME).write_text(json.dumps(on_disk))

    with pytest.raises(ResultsError, match="different schema_version"):
        build_comparison([out_dir / a.bench_run_id, b_dir])


def test_build_comparison_mixed_fingerprint_same_subject_refused_then_allowed(
    tmp_path: Path,
) -> None:
    uniq = _uniq()
    suite_id = f"rs-mix-{uniq}"
    subject_id = f"fk-mix-{uniq}"
    out_dir = tmp_path / "results"

    first = _run(
        tmp_path,
        suite_id=suite_id,
        subject_id=subject_id,
        tasks=[_fake_task("t1")],
        out_dir=out_dir,
    )
    first_dir = out_dir / first.bench_run_id
    # A distinct dir holding a run for the SAME subject id but a different
    # config_fingerprint (simulates a different-day/different-override re-run).
    second_dir = out_dir / f"{first.bench_run_id}-rerun"
    second_dir.mkdir(parents=True)
    on_disk = json.loads((first_dir / runner.RUN_JSON_FILENAME).read_text())
    on_disk["config_fingerprint"] = "different-fingerprint"
    (second_dir / runner.RUN_JSON_FILENAME).write_text(json.dumps(on_disk))

    with pytest.raises(ResultsError, match="different config_fingerprint"):
        build_comparison([first_dir, second_dir])

    # allow_mixed=True: no longer refused; last-wins (second_dir's data survives).
    comparison = build_comparison([first_dir, second_dir], allow_mixed=True)
    assert comparison.subjects.count(subject_id) == 1
    assert comparison.per_subject[subject_id] is not None


def test_build_comparison_missing_run_json_shown_as_not_run(tmp_path: Path) -> None:
    uniq = _uniq()
    suite_id = f"rs-missing-{uniq}"
    out_dir = tmp_path / "results"
    present = _run(
        tmp_path,
        suite_id=suite_id,
        subject_id=f"fk-present-{uniq}",
        tasks=[_fake_task("t1")],
        out_dir=out_dir,
    )
    missing_subject_id = f"fk-missing-{uniq}"
    missing_dir = out_dir / f"2026-07-22-{suite_id}-{missing_subject_id}"
    # No run.json written at all under this dir (not even the dir needs to exist).

    comparison = build_comparison([out_dir / present.bench_run_id, missing_dir], clock=_fixed_clock)

    assert missing_subject_id in comparison.not_run
    assert comparison.per_subject[missing_subject_id] is None
    assert comparison.matrix["t1"][missing_subject_id] is None
    assert comparison.per_subject[present.subject.id] is not None

    _, md_path = write_comparison(comparison, tmp_path / "compare-missing")
    md_text = md_path.read_text()
    assert f"not run: {missing_subject_id}" in md_text
    assert f"{missing_subject_id} (not run)" in md_text


def test_build_comparison_single_subject_degrades_gracefully(tmp_path: Path) -> None:
    uniq = _uniq()
    out_dir = tmp_path / "results"
    only = _run(
        tmp_path,
        suite_id=f"rs-solo-{uniq}",
        subject_id=f"fk-solo-{uniq}",
        tasks=[_fake_task("t1"), _fake_task("t2")],
        out_dir=out_dir,
    )
    comparison = build_comparison([out_dir / only.bench_run_id], clock=_fixed_clock)

    assert comparison.subjects == [only.subject.id]
    assert comparison.not_run == []
    assert comparison.per_subject[only.subject.id] is not None

    _, md_path = write_comparison(comparison, tmp_path / "compare-solo")
    md_text = md_path.read_text()
    assert f"Highest solve rate: {only.subject.id}" in md_text
    assert f"Fastest total wall-clock: {only.subject.id}" in md_text


def test_build_comparison_all_missing_raises_results_error(tmp_path: Path) -> None:
    with pytest.raises(ResultsError, match="No valid run.json found"):
        build_comparison([tmp_path / "nope-1", tmp_path / "nope-2"])


def test_build_comparison_divergent_task_sets_union_keyed(tmp_path: Path) -> None:
    uniq = _uniq()
    suite_id = f"rs-union-{uniq}"
    out_dir = tmp_path / "results"

    a = _run(
        tmp_path,
        suite_id=suite_id,
        subject_id=f"fk-union-a-{uniq}",
        tasks=[_fake_task("only-a"), _fake_task("shared")],
        out_dir=out_dir,
    )
    b = _run(
        tmp_path,
        suite_id=suite_id,
        subject_id=f"fk-union-b-{uniq}",
        tasks=[_fake_task("only-b"), _fake_task("shared")],
        out_dir=out_dir,
    )
    comparison = build_comparison([out_dir / a.bench_run_id, out_dir / b.bench_run_id])

    assert comparison.task_ids == ["only-a", "only-b", "shared"]
    assert comparison.matrix["only-a"][b.subject.id] is None  # b never ran "only-a"
    assert comparison.matrix["only-b"][a.subject.id] is None  # a never ran "only-b"
    assert comparison.matrix["shared"][a.subject.id] is not None
    assert comparison.matrix["shared"][b.subject.id] is not None


def _tied_aggregate() -> Aggregate:
    """An `Aggregate` shared byte-for-byte by every subject in
    `test_render_winners_discloses_ties` -- built directly (rather than via a real
    `run_suite` call) so every axis (solve_rate/cost/cost_per_solved/wall-clock) ties
    EXACTLY, including wall-clock (a real `FakeSubject` run's wall-clock is genuine
    monotonic time and would almost never tie by chance).
    """
    return Aggregate(
        solved=2,
        total=2,
        solve_rate=1.0,
        mean_score=1.0,
        total_cost_usd=0.05,
        cost_available=True,
        cost_per_solved=0.025,
        total_wall_clock_seconds=1.5,
        total_input_tokens=0,
        total_output_tokens=0,
        total_cache_creation_input_tokens=0,
        total_cache_read_input_tokens=0,
    )


def test_render_winners_discloses_ties() -> None:
    """W5: when >1 subject shares the winning value on an axis, the winner line must
    disclose the tie (e.g. "... (100.0%, tied with 2 others)") rather than silently
    presenting the deterministic subject-id tiebreak winner as if it were sole."""
    subject_ids = ["fk-tie-a", "fk-tie-b", "fk-tie-c"]
    comparison = ComparisonRecord(
        suite_id="rs-tie",
        generated_at=_FIXED_NOW.isoformat(),
        subjects=subject_ids,
        task_ids=["t1"],
        matrix={},
        per_subject={sid: _tied_aggregate() for sid in subject_ids},
    )

    winners = render_winners(comparison)

    # Deterministic tiebreak (ascending subject id) names "fk-tie-a" as the line's
    # winner; the tie with the other 2 subjects must still be disclosed on every axis.
    assert len(winners) == 4
    for line in winners:
        assert line.startswith("- ")
        assert "fk-tie-a" in line
        assert "tied with 2 others" in line


def test_render_winners_no_tie_omits_tie_note(tmp_path: Path) -> None:
    """Companion negative case: a genuinely sole winner gets no tie note at all (the
    two-subject `test_build_and_write_comparison_two_subjects_same_suite` case above
    already covers this via the rendered `comparison.md`; this asserts the same thing
    directly against `render_winners`'s return value)."""
    winner_agg = _tied_aggregate()
    loser_agg = winner_agg.model_copy(
        update={
            "solve_rate": 0.5,
            "solved": 1,
            "total_cost_usd": 0.5,
            "cost_per_solved": 0.5,
            "total_wall_clock_seconds": 3.0,
        }
    )
    comparison = ComparisonRecord(
        suite_id="rs-no-tie",
        generated_at=_FIXED_NOW.isoformat(),
        subjects=["fk-winner", "fk-loser"],
        task_ids=["t1"],
        matrix={},
        per_subject={"fk-winner": winner_agg, "fk-loser": loser_agg},
    )

    winners = render_winners(comparison)
    for line in winners:
        assert "tied with" not in line


def test_build_comparison_no_subjects_ran_winners_render_no_crash(tmp_path: Path) -> None:
    comparison = ComparisonRecord(
        suite_id="rs-empty-cmp",
        generated_at=_FIXED_NOW.isoformat(),
        subjects=["ghost"],
        not_run=["ghost"],
        task_ids=[],
        matrix={},
        per_subject={"ghost": None},
    )
    _, md_path = write_comparison(comparison, tmp_path / "compare-nobody-ran")
    md_text = md_path.read_text()
    assert "no subjects were run" in md_text


# ---------------------------------------------------------------------------
# compute_compare_id
# ---------------------------------------------------------------------------


def test_compute_compare_id_stable_and_dated() -> None:
    assert compute_compare_id("dev-core", _fixed_clock) == "2026-07-22-dev-core-compare"
