"""Comparison over repeats + the HLD §2b/§6 statistics layer (`bench/results.py`).

Every run dir here comes from a real `run_suite` call over fake subjects (see
`stats_support`): fake-tier, no LLM/network, deterministic, `out_dir` always a `tmp_path`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from agent_orchestrator.bench import runner
from agent_orchestrator.bench.errors import ResultsError
from agent_orchestrator.bench.results import (
    COMPARISON_SCHEMA_VERSION,
    SUSPECT_MAX_COST_USD,
    UNRELIABLE_INFRA_SHARE,
    ComparisonRecord,
    ComparisonTaskCell,
    build_comparison,
    load_comparison,
    render_winners,
    write_comparison,
)

from .stats_support import (
    fixed_clock,
    install_scripted_subject,
    run_dir_of,
    run_repeat,
    solvable_task,
    uniq,
    write_parity_bundle,
    write_subject,
    write_suite,
)

PASS_COST = 0.5


def _tasks(n: int = 4, **kwargs: Any) -> list[dict[str, Any]]:
    return [solvable_task(f"t{i}", **kwargs) for i in range(1, n + 1)]


def _md(comparison: ComparisonRecord, tmp_path: Path) -> str:
    _, md_path = write_comparison(comparison, tmp_path / f"cmp-{uniq()}")
    return md_path.read_text()


# ---------------------------------------------------------------------------
# repeats: several run dirs for one subject aggregate per task
# ---------------------------------------------------------------------------


def test_repeats_aggregate_into_cells_and_pairwise(tmp_path: Path) -> None:
    suite_id = f"cs-rep-{uniq()}"
    out = tmp_path / "results"
    suite = write_suite(tmp_path, suite_id, _tasks(4))
    pass_sub = write_subject(
        tmp_path, f"fake-pass-{suite_id}", scripted_effect="copy-solution", fake_cost=PASS_COST
    )
    fail_sub = write_subject(
        tmp_path, f"fake-fail-{suite_id}", scripted_effect="noop", fake_cost=2.0
    )

    dirs = []
    for repeat in (1, 2):
        dirs.append(run_dir_of(out, run_repeat(suite, pass_sub, out, repeat)))
    for repeat in (1, 2):
        dirs.append(run_dir_of(out, run_repeat(suite, fail_sub, out, repeat)))
    assert dirs[0].name.endswith(f"fake-pass-{suite_id}")
    assert dirs[1].name.endswith("-r2")  # repeats land in their own dirs

    cmp = build_comparison(dirs, clock=fixed_clock)

    passer, failer = f"fake-pass-{suite_id}", f"fake-fail-{suite_id}"
    assert cmp.schema_version == COMPARISON_SCHEMA_VERSION == "2.0"
    assert cmp.subjects == [passer, failer]  # one column per subject, not per run dir
    assert cmp.repeats_per_subject == {passer: 2, failer: 2}
    assert cmp.task_ids == ["t1", "t2", "t3", "t4"]

    cell = cmp.matrix["t1"][passer]
    assert cell is not None
    assert (cell.n, cell.score_mean, cell.score_sd, cell.solve_rate) == (2, 1.0, 0.0, 1.0)
    assert cell.solved and cell.score == 1.0
    assert cell.cost_median == cell.cost_usd == PASS_COST
    assert cell.subject_status == "succeeded"
    fcell = cmp.matrix["t1"][failer]
    assert fcell is not None
    assert (fcell.n, fcell.score_mean, fcell.solve_rate) == (2, 0.0, 0.0)
    assert not fcell.solved

    # Totals cover every repeat's task runs.
    agg = cmp.per_subject[passer]
    assert agg is not None
    assert (agg.total, agg.solved) == (8, 8)
    assert agg.total_cost_usd == pytest.approx(8 * PASS_COST)

    # Pairwise: one entry for the one pair, paired over the 4 tasks both ran.
    assert len(cmp.pairwise) == 1
    pw = cmp.pairwise[0]
    assert (pw.a, pw.b, pw.metric, pw.n_tasks) == (passer, failer, "score", 4)
    assert pw.mean_diff == pytest.approx(1.0)
    assert pw.ci_lo is not None and pw.ci_lo > 0.0  # CI excludes 0
    assert pw.p_wilcoxon == pytest.approx(2 / 2**4)  # exact: all 4 diffs positive
    # Per-subject attempted-based rates (no infra here, so equal to the plain ones).
    st = cmp.subject_stats[passer]
    assert st is not None
    assert (st.repeats, st.task_runs, st.attempted, st.infra_errors) == (2, 8, 8, 0)
    assert st.solve_rate_attempted == 1.0 and st.score_mean_attempted == 1.0
    assert st.cost_per_score_point == pytest.approx(PASS_COST)

    md = _md(cmp, tmp_path)
    assert "| t1 | 2/2 / 0.5000 | 0/2 / 2.0000 |" in md  # repeats render "k/n / median cost"


def test_partial_repeat_gives_cells_with_different_n(tmp_path: Path) -> None:
    suite_id = f"cs-part-{uniq()}"
    out = tmp_path / "results"
    suite = write_suite(tmp_path, suite_id, _tasks(3))
    sub = write_subject(tmp_path, f"fake-pass-{suite_id}", scripted_effect="copy-solution")
    r1 = run_repeat(suite, sub, out, 1)
    r2 = run_repeat(suite, sub, out, 2, task_filter=["t2"])  # a staged repeat: one task only

    cmp = build_comparison([run_dir_of(out, r1), run_dir_of(out, r2)])

    ns = {tid: cmp.matrix[tid][r1.subject.id].n for tid in cmp.task_ids}  # type: ignore[union-attr]
    assert ns == {"t1": 1, "t2": 2, "t3": 1}
    assert cmp.repeats_per_subject == {r1.subject.id: 2}
    # Each task counts once in the rates however many repeats it had.
    st = cmp.subject_stats[r1.subject.id]
    assert st is not None and st.tasks_attempted == 3 and st.task_runs == 4
    # A single-repeat cell keeps the legacy "yes / cost" rendering.
    assert "| t1 | yes / " in _md(cmp, tmp_path)


def test_repeats_that_disagree_give_mean_sd_and_majority_rule(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    suite_id = f"cs-var-{uniq()}"
    out = tmp_path / "results"
    suite = write_suite(tmp_path, suite_id, _tasks(2))
    sub_id = f"flaky-{suite_id}"
    sub = write_subject(tmp_path, sub_id, fake_cost=0.25)
    # t1: solved, unsolved, solved (2/3); t2: solved, then unsolved twice (the last scripted
    # effect repeats) -> 1/3, not "solved" under the strict-majority rule.
    install_scripted_subject(
        monkeypatch,
        {
            (sub_id, "t1"): ["copy-solution", "noop", "copy-solution"],
            (sub_id, "t2"): ["copy-solution", "noop"],
        },
    )
    dirs = [run_dir_of(out, run_repeat(suite, sub, out, r)) for r in (1, 2, 3)]

    cmp = build_comparison(dirs)

    t1 = cmp.matrix["t1"][sub_id]
    t2 = cmp.matrix["t2"][sub_id]
    assert t1 is not None and t2 is not None
    assert t1.n == 3 and t1.solve_rate == pytest.approx(2 / 3) and t1.solved
    assert t1.score_mean == pytest.approx(2 / 3)
    assert t1.score_sd == pytest.approx(0.57735, abs=1e-4)  # sample sd of [1, 0, 1]
    assert t2.n == 3 and not t2.solved and t2.solve_rate == pytest.approx(1 / 3)
    assert t1.cost_median == 0.25


def test_cost_and_wall_are_medians_across_repeats(tmp_path: Path) -> None:
    suite_id = f"cs-med-{uniq()}"
    out = tmp_path / "results"
    suite = write_suite(tmp_path, suite_id, _tasks(1))
    sub = write_subject(
        tmp_path, f"fake-pass-{suite_id}", scripted_effect="copy-solution", fake_cost=0.5
    )
    dirs = [run_dir_of(out, run_repeat(suite, sub, out, r)) for r in (1, 2, 3)]
    for d, (cost, wall) in zip(dirs, [(0.5, 10.0), (1.0, 30.0), (3.0, 20.0)], strict=True):
        data = json.loads((d / runner.RUN_JSON_FILENAME).read_text())
        data["tasks"][0]["cost_usd"] = cost
        data["tasks"][0]["wall_clock_seconds"] = wall
        (d / runner.RUN_JSON_FILENAME).write_text(json.dumps(data))

    cell = build_comparison(dirs).matrix["t1"][f"fake-pass-{suite_id}"]

    assert cell is not None
    assert cell.cost_median == 1.0 and cell.wall_median == 20.0
    assert cell.cost_usd == 1.0 and cell.wall_clock_seconds == 20.0  # legacy fields follow


def test_repeat_dirs_with_different_config_fingerprint_are_refused(tmp_path: Path) -> None:
    suite_id = f"cs-cfg-{uniq()}"
    out = tmp_path / "results"
    suite = write_suite(tmp_path, suite_id, _tasks(1))
    sub = write_subject(tmp_path, f"fake-pass-{suite_id}", scripted_effect="copy-solution")
    r1 = run_dir_of(out, run_repeat(suite, sub, out, 1))
    r2 = run_dir_of(out, run_repeat(suite, sub, out, 2))
    # Identical fingerprints (the normal repeat case) are accepted ...
    assert build_comparison([r1, r2]).repeats_per_subject == {f"fake-pass-{suite_id}": 2}

    data = json.loads((r2 / runner.RUN_JSON_FILENAME).read_text())
    data["config_fingerprint"] = "someone-changed-the-config"
    (r2 / runner.RUN_JSON_FILENAME).write_text(json.dumps(data))
    with pytest.raises(ResultsError, match="config_fingerprint"):
        build_comparison([r1, r2])
    assert (
        build_comparison([r1, r2], allow_mixed=True).repeats_per_subject[f"fake-pass-{suite_id}"]
        == 2
    )


def test_missing_repeat_dir_is_not_a_separate_subject(tmp_path: Path) -> None:
    suite_id = f"cs-miss-{uniq()}"
    out = tmp_path / "results"
    suite = write_suite(tmp_path, suite_id, _tasks(1))
    sub = write_subject(tmp_path, f"fake-pass-{suite_id}", scripted_effect="copy-solution")
    r1 = run_dir_of(out, run_repeat(suite, sub, out, 1))

    cmp = build_comparison([r1, Path(f"{r1}-r2")])  # the -r2 dir was never created

    assert cmp.not_run == []
    assert cmp.subjects == [f"fake-pass-{suite_id}"]


# ---------------------------------------------------------------------------
# parity (HLD §2b)
# ---------------------------------------------------------------------------


def test_parity_mismatch_refused_then_allowed_and_stamped(tmp_path: Path) -> None:
    suite_id = f"cs-par-{uniq()}"
    out = tmp_path / "results"
    suite = write_suite(tmp_path, suite_id, _tasks(2))
    bundle = write_parity_bundle(tmp_path)
    a = write_subject(tmp_path, f"sub-a-{suite_id}", scripted_effect="copy-solution")
    b = write_subject(tmp_path, f"sub-b-{suite_id}", scripted_effect="noop")
    ra = run_repeat(suite, a, out, parity=bundle)
    rb = run_repeat(suite, b, out)  # NO bundle
    assert ra.parity_fingerprint is not None and rb.parity_fingerprint is None

    dirs = [run_dir_of(out, ra), run_dir_of(out, rb)]
    with pytest.raises(ResultsError, match="parity_fingerprint"):
        build_comparison(dirs)

    cmp = build_comparison(dirs, allow_mixed=True)
    assert cmp.parity_mixed is True
    assert cmp.parity_fingerprint is None  # no single fingerprint describes a mixed set
    assert cmp.parity_by_subject == {ra.subject.id: ra.parity_fingerprint, rb.subject.id: None}
    md = _md(cmp, tmp_path)
    assert "## Parity" in md and "NON-PARITY" in md


def test_shared_parity_bundle_is_recorded_on_the_comparison(tmp_path: Path) -> None:
    suite_id = f"cs-par2-{uniq()}"
    out = tmp_path / "results"
    suite = write_suite(tmp_path, suite_id, _tasks(1))
    bundle = write_parity_bundle(tmp_path)
    runs = [
        run_repeat(
            suite,
            write_subject(tmp_path, f"sub-{n}-{suite_id}", scripted_effect="copy-solution"),
            out,
            parity=bundle,
        )
        for n in "ab"
    ]

    cmp = build_comparison([run_dir_of(out, r) for r in runs])

    assert cmp.parity_mixed is False
    assert cmp.parity_fingerprint == runs[0].parity_fingerprint is not None
    assert f"`{cmp.parity_fingerprint}`" in _md(cmp, tmp_path)


def test_no_parity_bundle_is_stated_in_the_report(tmp_path: Path) -> None:
    suite_id = f"cs-par3-{uniq()}"
    out = tmp_path / "results"
    suite = write_suite(tmp_path, suite_id, _tasks(1))
    r = run_repeat(suite, write_subject(tmp_path, f"sub-{suite_id}"), out)

    cmp = build_comparison([run_dir_of(out, r)])

    assert cmp.parity_fingerprint is None and not cmp.parity_mixed
    assert "no parity bundle applied" in _md(cmp, tmp_path)


# ---------------------------------------------------------------------------
# infra errors, suspects, attempted-based rates
# ---------------------------------------------------------------------------


def test_infra_errors_counted_listed_and_excluded_from_attempted_rates(tmp_path: Path) -> None:
    suite_id = f"cs-inf-{uniq()}"
    out = tmp_path / "results"
    suite = write_suite(tmp_path, suite_id, _tasks(4))
    ok = write_subject(tmp_path, f"fake-pass-{suite_id}", scripted_effect="copy-solution")
    err = write_subject(tmp_path, f"fake-err-{suite_id}", scripted_effect="error", fake_cost=0.02)
    dirs = [run_dir_of(out, run_repeat(suite, s, out)) for s in (ok, err)]

    cmp = build_comparison(dirs)

    erring, passing = f"fake-err-{suite_id}", f"fake-pass-{suite_id}"
    assert cmp.infra[erring].count == 4
    assert cmp.infra[erring].task_ids == ["t1", "t2", "t3", "t4"]
    assert cmp.infra[erring].suspect_task_ids == []  # errors are infra already, not "suspects"
    assert cmp.infra[passing].count == 0
    st = cmp.subject_stats[erring]
    assert st is not None
    assert (st.task_runs, st.attempted, st.infra_errors, st.infra_share) == (4, 0, 4, 1.0)
    assert st.solve_rate_attempted == 0.0 and st.cost_per_score_point is None
    # The erroring subject has n == 0 cells, so it takes part in no pairwise test ...
    assert cmp.matrix["t1"][erring] is not None and cmp.matrix["t1"][erring].n == 0  # type: ignore[union-attr]
    assert cmp.pairwise[0].n_tasks == 0 and cmp.pairwise[0].mean_diff is None
    # ... never wins an attempted-based axis ...
    winners = render_winners(cmp)
    assert winners[0].startswith(f"- Highest solve rate: {passing} (100.0% of attempted")
    assert "tied" not in winners[0]
    # A subject with no attempted work (here: $0.08 total, "fastest") wins NO axis either.
    assert all(erring not in line for line in winners)
    # ... and 100% infra trips the unreliable banner.
    assert cmp.unreliable_subjects == [erring]
    md = _md(cmp, tmp_path)
    assert "UNRELIABLE" in md and erring in md.split("UNRELIABLE", 1)[1].splitlines()[0]
    assert "## Infra errors / suspects" in md


def test_attempted_rates_differ_from_all_task_rates_when_some_tasks_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    suite_id = f"cs-att-{uniq()}"
    out = tmp_path / "results"
    suite = write_suite(tmp_path, suite_id, _tasks(4))
    sub_id = f"half-infra-{suite_id}"
    sub = write_subject(tmp_path, sub_id, fake_cost=0.4)
    install_scripted_subject(
        monkeypatch,
        {(sub_id, "t1"): ["error"], **{(sub_id, f"t{i}"): ["copy-solution"] for i in (2, 3, 4)}},
    )

    cmp = build_comparison([run_dir_of(out, run_repeat(suite, sub, out))])

    agg = cmp.per_subject[sub_id]
    st = cmp.subject_stats[sub_id]
    assert agg is not None and st is not None
    assert agg.solve_rate == 0.75  # all-task view: the infra error counts as a miss ...
    assert st.solve_rate_attempted == 1.0  # ... the capability view does not
    assert (st.attempted, st.infra_errors, st.infra_share) == (3, 1, 0.25)
    assert cmp.unreliable_subjects == [sub_id]  # 25% > 10%


@pytest.mark.parametrize(("errors", "unreliable"), [(1, False), (2, True)])
def test_unreliable_banner_threshold_is_strictly_above_ten_percent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, errors: int, unreliable: bool
) -> None:
    suite_id = f"cs-thr-{uniq()}"
    out = tmp_path / "results"
    suite = write_suite(tmp_path, suite_id, _tasks(10))
    sub_id = f"sub-{suite_id}"
    sub = write_subject(tmp_path, sub_id)
    install_scripted_subject(
        monkeypatch,
        {(sub_id, f"t{i}"): ["error"] for i in range(1, errors + 1)},
    )
    assert UNRELIABLE_INFRA_SHARE == 0.10

    cmp = build_comparison([run_dir_of(out, run_repeat(suite, sub, out))])

    assert (cmp.unreliable_subjects == [sub_id]) is unreliable
    assert ("UNRELIABLE" in _md(cmp, tmp_path)) is unreliable


@pytest.mark.parametrize(
    ("cost", "suspect"),
    [(0.02, True), (0.149, True), (SUSPECT_MAX_COST_USD, False), (2.0, False)],
)
def test_suspect_heuristic_unsolved_cheap_and_quick(
    tmp_path: Path, cost: float, suspect: bool
) -> None:
    suite_id = f"cs-sus-{uniq()}"
    out = tmp_path / "results"
    suite = write_suite(tmp_path, suite_id, _tasks(1))
    sub_id = f"fake-fail-{suite_id}"
    sub = write_subject(tmp_path, sub_id, scripted_effect="fail", fake_cost=cost)

    cmp = build_comparison([run_dir_of(out, run_repeat(suite, sub, out))])

    assert (cmp.infra[sub_id].suspect_task_ids == ["t1"]) is suspect
    assert cmp.infra[sub_id].count == 0
    # Suspects are reported, never excluded: the miss stays in the attempted rate.
    st = cmp.subject_stats[sub_id]
    assert st is not None and st.attempted == 1 and st.solve_rate_attempted == 0.0


def test_a_solved_or_slow_or_costless_run_is_not_a_suspect(tmp_path: Path) -> None:
    suite_id = f"cs-sus2-{uniq()}"
    out = tmp_path / "results"
    suite = write_suite(tmp_path, suite_id, _tasks(3))
    sub_id = f"sub-{suite_id}"
    sub = write_subject(tmp_path, sub_id, scripted_effect="noop", fake_cost=0.05)
    d = run_dir_of(out, run_repeat(suite, sub, out))
    data = json.loads((d / runner.RUN_JSON_FILENAME).read_text())
    by_id = {t["task_id"]: t for t in data["tasks"]}
    by_id["t1"]["solved"] = True  # solved -> not a suspect
    by_id["t2"]["wall_clock_seconds"] = 180.0  # not quick (< 180) -> not a suspect
    by_id["t3"]["cost_usd"] = None  # unknown cost -> cannot be called cheap
    (d / runner.RUN_JSON_FILENAME).write_text(json.dumps(data))

    assert build_comparison([d]).infra[sub_id].suspect_task_ids == []


# ---------------------------------------------------------------------------
# per-family / per-language roll-ups
# ---------------------------------------------------------------------------


def _family_suite(tmp_path: Path, suite_id: str) -> Path:
    tasks = [
        solvable_task("t1", family="crud", language="python"),
        solvable_task("t2", family="crud", language="go"),
        solvable_task("t3", family="queue", language="python"),
        solvable_task("t4", family="queue", language="go"),
        solvable_task("t5"),  # no family/language: left out of the roll-ups
    ]
    return write_suite(tmp_path, suite_id, tasks)


def test_per_family_and_per_language_rollups(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    suite_id = f"cs-fam-{uniq()}"
    out = tmp_path / "results"
    suite = _family_suite(tmp_path, suite_id)
    full, crud_only = f"full-{suite_id}", f"crud-only-{suite_id}"
    install_scripted_subject(
        monkeypatch,
        {
            **{(full, f"t{i}"): ["copy-solution"] for i in range(1, 6)},
            (crud_only, "t1"): ["copy-solution"],
            (crud_only, "t2"): ["copy-solution"],
            (crud_only, "t3"): ["noop"],
            (crud_only, "t4"): ["noop"],
            (crud_only, "t5"): ["copy-solution"],
        },
    )
    dirs = [
        run_dir_of(out, run_repeat(suite, write_subject(tmp_path, sid), out))
        for sid in (full, crud_only)
    ]

    cmp = build_comparison(dirs)

    assert sorted(cmp.per_family) == ["crud", "queue"]  # t5 (no family) is not a group
    crud = cmp.per_family["crud"]
    assert (crud[full].score_mean, crud[full].solve_rate, crud[full].n) == (1.0, 1.0, 2)
    assert (crud[crud_only].score_mean, crud[crud_only].n) == (1.0, 2)
    queue = cmp.per_family["queue"]
    assert (queue[crud_only].score_mean, queue[crud_only].solve_rate, queue[crud_only].n) == (
        0.0,
        0.0,
        2,
    )
    assert sorted(cmp.per_language) == ["go", "python"]
    # python = t1 (solved) + t3 (not) for crud-only; go = t2 + t4.
    assert cmp.per_language["python"][crud_only].score_mean == pytest.approx(0.5)
    assert cmp.per_language["go"][crud_only].solve_rate == pytest.approx(0.5)
    assert cmp.per_language["python"][full].n == 2

    md = _md(cmp, tmp_path)
    assert "## Per family" in md and "## Per language" in md
    assert "| queue | 1.00 / 100.0% (n=2) | 0.00 / 0.0% (n=2) |" in md


def test_suite_without_family_metadata_has_empty_rollups_and_says_so(tmp_path: Path) -> None:
    suite_id = f"cs-nofam-{uniq()}"
    out = tmp_path / "results"
    suite = write_suite(tmp_path, suite_id, _tasks(2))
    r = run_repeat(suite, write_subject(tmp_path, f"sub-{suite_id}"), out)

    cmp = build_comparison([run_dir_of(out, r)])

    assert cmp.per_family == {} and cmp.per_language == {}
    md = _md(cmp, tmp_path)
    assert "No family metadata" in md and "No language metadata" in md


def test_rollup_skips_a_group_a_subject_never_attempted(tmp_path: Path) -> None:
    suite_id = f"cs-fam2-{uniq()}"
    out = tmp_path / "results"
    suite = _family_suite(tmp_path, suite_id)
    err_id = f"err-{suite_id}"
    r = run_repeat(suite, write_subject(tmp_path, err_id, scripted_effect="error"), out)

    cmp = build_comparison([run_dir_of(out, r)])

    assert cmp.per_family["crud"] == {}  # only attempted tasks roll up


# ---------------------------------------------------------------------------
# pairwise
# ---------------------------------------------------------------------------


def test_pairwise_covers_every_pair_over_common_tasks_and_is_deterministic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    suite_id = f"cs-pw-{uniq()}"
    out = tmp_path / "results"
    suite = write_suite(tmp_path, suite_id, _tasks(6))
    a, b, c = (f"{n}-{suite_id}" for n in "abc")
    install_scripted_subject(
        monkeypatch,
        {
            **{(a, f"t{i}"): ["copy-solution"] for i in range(1, 7)},
            **{(b, f"t{i}"): ["copy-solution" if i <= 3 else "noop"] for i in range(1, 7)},
            **{(c, f"t{i}"): ["noop"] for i in range(1, 7)},
        },
    )
    dirs = [run_dir_of(out, run_repeat(suite, write_subject(tmp_path, s), out)) for s in (a, b, c)]

    cmp = build_comparison(dirs)

    assert [(e.a, e.b) for e in cmp.pairwise] == [(a, b), (a, c), (b, c)]
    ab, ac, bc = cmp.pairwise
    assert (ab.mean_diff, ab.n_tasks) == (pytest.approx(0.5), 6)
    assert (ac.mean_diff, bc.mean_diff) == (pytest.approx(1.0), pytest.approx(0.5))
    assert ab.ci_lo is not None and ab.ci_lo > 0 and ab.p_wilcoxon is not None
    assert ab.p_wilcoxon == pytest.approx(2 / 2**3)  # three non-zero diffs, all positive
    assert ac.p_wilcoxon is not None and ac.p_wilcoxon < 0.05
    # Same inputs, same report (seeded bootstrap).
    again = build_comparison(dirs, clock=fixed_clock)
    assert [e.model_dump() for e in again.pairwise] == [e.model_dump() for e in cmp.pairwise]
    md = _md(cmp, tmp_path)
    assert "## Pairwise (paired bootstrap 95% CI, Wilcoxon p)" in md
    assert f"| {a} | {b} | score | +0.500 | [" in md


def test_pairwise_only_uses_tasks_both_subjects_ran(tmp_path: Path) -> None:
    suite_id = f"cs-pw2-{uniq()}"
    out = tmp_path / "results"
    suite = write_suite(tmp_path, suite_id, _tasks(4))
    a = write_subject(tmp_path, f"a-{suite_id}", scripted_effect="copy-solution")
    b = write_subject(tmp_path, f"b-{suite_id}", scripted_effect="noop")
    ra = run_repeat(suite, a, out)
    rb = run_repeat(suite, b, out, task_filter=["t1", "t2"])  # b never ran t3/t4

    cmp = build_comparison([run_dir_of(out, ra), run_dir_of(out, rb)])

    assert cmp.pairwise[0].n_tasks == 2
    assert cmp.matrix["t3"][rb.subject.id] is None


def test_single_subject_has_no_pairwise_entries(tmp_path: Path) -> None:
    suite_id = f"cs-pw3-{uniq()}"
    out = tmp_path / "results"
    suite = write_suite(tmp_path, suite_id, _tasks(2))
    r = run_repeat(suite, write_subject(tmp_path, f"solo-{suite_id}"), out)

    cmp = build_comparison([run_dir_of(out, r)])

    assert cmp.pairwise == []
    assert "| — | — | — | — | — | — | — | — |" in _md(cmp, tmp_path)


# ---------------------------------------------------------------------------
# markdown shape + winners
# ---------------------------------------------------------------------------


def test_comparison_md_keeps_original_sections_then_adds_stats_sections(tmp_path: Path) -> None:
    suite_id = f"cs-md-{uniq()}"
    out = tmp_path / "results"
    suite = write_suite(tmp_path, suite_id, _tasks(2))
    dirs = [
        run_dir_of(
            out,
            run_repeat(suite, write_subject(tmp_path, f"{n}-{suite_id}", scripted_effect=eff), out),
        )
        for n, eff in (("a", "copy-solution"), ("b", "noop"))
    ]

    md = _md(build_comparison(dirs), tmp_path)

    headings = [line for line in md.splitlines() if line.startswith("## ")]
    assert headings == [
        "## Per-subject",
        "## Per-task (solved / cost)",
        "## Winners",
        "## Parity",
        "## Attempted-based rates (infra excluded)",
        "## Pairwise (paired bootstrap 95% CI, Wilcoxon p)",
        "## Per family",
        "## Per language",
        "## Infra errors / suspects",
    ]
    assert (
        "| Subject | Solved/Total | Solve Rate | Total Cost($) | Cost/Solved($) | Total Wall(s) |"
        in md
    )
    assert "UNRELIABLE" not in md  # banner only when a subject is above the threshold


def test_winners_rank_on_attempted_rate_and_mean_score(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    suite_id = f"cs-win-{uniq()}"
    out = tmp_path / "results"
    suite = write_suite(tmp_path, suite_id, _tasks(4))
    careful, bold = f"careful-{suite_id}", f"bold-{suite_id}"
    # `careful` solved the 2 tasks it got to attempt (the rest were infra errors);
    # `bold` attempted all four and solved three. All-task rates favour `bold`
    # (0.75 vs 0.5) but on attempted work `careful` is perfect.
    install_scripted_subject(
        monkeypatch,
        {
            (careful, "t1"): ["copy-solution"],
            (careful, "t2"): ["copy-solution"],
            (careful, "t3"): ["error"],
            (careful, "t4"): ["error"],
            (bold, "t1"): ["copy-solution"],
            (bold, "t2"): ["copy-solution"],
            (bold, "t3"): ["copy-solution"],
            (bold, "t4"): ["noop"],
        },
    )
    dirs = [
        run_dir_of(out, run_repeat(suite, write_subject(tmp_path, s), out)) for s in (careful, bold)
    ]

    winners = render_winners(build_comparison(dirs))

    assert winners[0].startswith(f"- Highest solve rate: {careful} (100.0% of attempted")
    assert winners[1].startswith(f"- Highest mean score: {careful} (1.000 over attempted")


# ---------------------------------------------------------------------------
# schema: legacy 1.0 loader + round trip
# ---------------------------------------------------------------------------


def _legacy_comparison_dict() -> dict[str, Any]:
    cell = {
        "subject_status": "succeeded",
        "solved": True,
        "score": 1.0,
        "cost_usd": 0.5,
        "wall_clock_seconds": 3.0,
    }
    agg = {
        "solved": 1,
        "total": 1,
        "solve_rate": 1.0,
        "mean_score": 1.0,
        "total_cost_usd": 0.5,
        "cost_available": True,
        "cost_per_solved": 0.5,
        "total_wall_clock_seconds": 3.0,
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "total_cache_creation_input_tokens": 0,
        "total_cache_read_input_tokens": 0,
    }
    return {
        "schema_version": "1.0",
        "suite_id": "old-suite",
        "generated_at": "2026-07-22T12:00:00+00:00",
        "subjects": ["sub-a", "sub-b"],
        "not_run": ["sub-b"],
        "task_ids": ["t1"],
        "matrix": {
            "t1": {"sub-a": cell, "sub-b": None},
        },
        "per_subject": {"sub-a": agg, "sub-b": None},
    }


def test_load_comparison_reads_a_legacy_1_0_file(tmp_path: Path) -> None:
    path = tmp_path / "comparison.json"
    path.write_text(json.dumps(_legacy_comparison_dict()))

    cmp = load_comparison(tmp_path)  # a directory works, like load_run

    assert cmp.schema_version == "1.0"  # honest: this comparison had no stats layer
    cell = cmp.matrix["t1"]["sub-a"]
    assert cell is not None
    assert (cell.score_mean, cell.score_sd, cell.n, cell.solve_rate) == (1.0, 0.0, 1, 1.0)
    assert (cell.cost_median, cell.wall_median) == (0.5, 3.0)
    assert cmp.pairwise == [] and cmp.per_family == {} and cmp.infra == {}
    assert cmp.parity_fingerprint is None and cmp.parity_mixed is False
    # ... and the legacy record still renders (every new section has a placeholder).
    md = _md(cmp, tmp_path)
    assert "## Per-subject" in md and "## Winners" in md
    assert "Highest solve rate: sub-a (100.0% of attempted" in md  # aggregate fallback


def test_load_comparison_round_trips_a_current_comparison(tmp_path: Path) -> None:
    suite_id = f"cs-rt-{uniq()}"
    out = tmp_path / "results"
    suite = _family_suite(tmp_path, suite_id)
    dirs = [
        run_dir_of(
            out,
            run_repeat(suite, write_subject(tmp_path, f"{n}-{suite_id}", scripted_effect=e), out),
        )
        for n, e in (("a", "copy-solution"), ("b", "noop"))
    ]
    cmp = build_comparison(dirs, clock=fixed_clock)
    json_path, _ = write_comparison(cmp, tmp_path / "cmp")

    assert load_comparison(json_path) == cmp


def test_load_comparison_rejects_unknown_version_garbage_and_missing(tmp_path: Path) -> None:
    bad = _legacy_comparison_dict()
    bad["schema_version"] = "9.9"
    (tmp_path / "comparison.json").write_text(json.dumps(bad))
    with pytest.raises(ResultsError, match="unsupported comparison schema_version"):
        load_comparison(tmp_path)

    (tmp_path / "comparison.json").write_text("{not json")
    with pytest.raises(ResultsError, match="Failed to read"):
        load_comparison(tmp_path)

    (tmp_path / "comparison.json").write_text(json.dumps({"schema_version": "2.0"}))
    with pytest.raises(ResultsError, match="does not match"):
        load_comparison(tmp_path)

    with pytest.raises(ResultsError, match="No comparison.json"):
        load_comparison(tmp_path / "nowhere")


def test_legacy_cell_constructor_derives_the_new_fields() -> None:
    cell = ComparisonTaskCell(
        subject_status="failed", solved=False, score=0.25, cost_usd=None, wall_clock_seconds=1.5
    )
    assert (cell.score_mean, cell.n, cell.solve_rate, cell.cost_median) == (0.25, 1, 0.0, None)
    infra = ComparisonTaskCell(
        subject_status="error", solved=False, score=0.0, cost_usd=0.1, wall_clock_seconds=1.0
    )
    assert infra.n == 0  # an infra error is not an attempt
