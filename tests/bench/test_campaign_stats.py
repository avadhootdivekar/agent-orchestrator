"""Staged repeats for `run_campaign` / `ao-bench campaign` (HLD §6) and `ao-bench report`
over repeat dirs.

Fake-tier: real `run_suite` over fake subjects (see `stats_support`); the per-(subject,
task) scripted outcomes are what make subjects disagree and repeats differ. `out_dir` is
always a `tmp_path`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from agent_orchestrator.bench import campaign as campaign_mod
from agent_orchestrator.bench.campaign import (
    CampaignResult,
    run_campaign,
    select_repeat_tasks,
    undecided_pairs,
)
from agent_orchestrator.bench.cli import app
from agent_orchestrator.bench.errors import BenchError
from agent_orchestrator.bench.results import (
    ComparisonRecord,
    ComparisonTaskCell,
    PairwiseEntry,
)

from .stats_support import (
    fixed_clock,
    install_scripted_subject,
    solvable_task,
    uniq,
    write_parity_bundle,
    write_subject,
    write_suite,
)

cli = CliRunner()

SOLVE = "copy-solution"
FAIL = "noop"


def _setup(
    tmp_path: Path, n_tasks: int, *subject_ids: str, **subject_extra: Any
) -> tuple[Path, str, list[Path], Path]:
    """(suite_path, suite_id, subject_paths, out_dir) over tasks t1..t{n_tasks}."""
    suite_id = f"cc-{uniq()}"
    suite = write_suite(tmp_path, suite_id, [solvable_task(f"t{i}") for i in range(1, n_tasks + 1)])
    subjects = [write_subject(tmp_path, sid, **subject_extra) for sid in subject_ids]
    return suite, suite_id, subjects, tmp_path / "results"


def _plan(subject_id: str, effects: dict[str, list[str]]) -> dict[tuple[str, str], list[str]]:
    return {(subject_id, task_id): e for task_id, e in effects.items()}


def _campaign(suite: Path, subjects: list[Path], out: Path, **kwargs: Any) -> CampaignResult:
    return run_campaign(
        suite,
        subjects,
        out_dir=out,
        clock=fixed_clock,
        cost_budget_usd=100.0,
        run_budget_usd=kwargs.pop("run_budget_usd", 1000.0),
        **kwargs,
    )


def _run_json(dir_: str | Path) -> dict[str, Any]:
    return json.loads((Path(dir_) / "run.json").read_text())  # type: ignore[no-any-return]


# ---------------------------------------------------------------------------
# select_repeat_tasks / undecided_pairs: pure decision rule
# ---------------------------------------------------------------------------


def _cell(score_mean: float, *, n: int = 1, sd: float = 0.0) -> ComparisonTaskCell:
    return ComparisonTaskCell(
        subject_status="succeeded" if n else "error",
        solved=score_mean > 0.5,
        score=score_mean,
        cost_usd=None,
        wall_clock_seconds=0.0,
        score_mean=score_mean,
        score_sd=sd,
        n=n,
        solve_rate=score_mean,
        cost_median=None,
        wall_median=0.0,
    )


def _comparison(matrix: dict[str, dict[str, ComparisonTaskCell | None]]) -> ComparisonRecord:
    return ComparisonRecord(
        suite_id="s",
        generated_at="2026-10-23T00:00:00+00:00",
        subjects=["a", "b"],
        task_ids=sorted(matrix),
        matrix=matrix,
    )


def test_select_repeat_tasks_reasons_and_ordering() -> None:
    cmp = _comparison(
        {
            "agree-quiet": {"a": _cell(1.0), "b": _cell(1.0)},  # nothing to learn
            "agree-both-fail": {"a": _cell(0.0), "b": _cell(0.0)},
            "disagree-small": {"a": _cell(0.5), "b": _cell(0.25)},
            "disagree-big": {"a": _cell(1.0), "b": _cell(0.0)},
            "noisy": {"a": _cell(0.5, n=2, sd=0.7), "b": _cell(0.5, n=2, sd=0.0)},
            "infra-hole": {"a": _cell(1.0), "b": _cell(0.0, n=0)},
            "only-a-ran": {"a": _cell(1.0), "b": None},  # b never ran it: not a hole
        }
    )

    picked = select_repeat_tasks(cmp)

    assert picked == {
        "disagree-big": "disagree",
        "disagree-small": "disagree",
        "noisy": "variance",
        "infra-hole": "infra-retry",
    }
    # Most informative first: biggest spread, then within-subject sd, then id.
    assert list(picked) == ["disagree-big", "disagree-small", "noisy", "infra-hole"]


def test_select_repeat_tasks_empty_when_every_task_is_settled() -> None:
    cmp = _comparison(
        {"t1": {"a": _cell(1.0), "b": _cell(1.0)}, "t2": {"a": _cell(0.0), "b": None}}
    )
    assert select_repeat_tasks(cmp) == {}


def test_undecided_pairs_flags_straddling_and_dataless_pairs() -> None:
    cmp = ComparisonRecord(
        suite_id="s",
        generated_at="x",
        subjects=["a", "b", "c", "d"],
        pairwise=[
            PairwiseEntry(a="a", b="b", mean_diff=0.5, ci_lo=0.1, ci_hi=0.9, n_tasks=5),  # decided
            PairwiseEntry(
                a="a", b="c", mean_diff=-0.5, ci_lo=-0.9, ci_hi=-0.1, n_tasks=5
            ),  # decided
            PairwiseEntry(
                a="a", b="d", mean_diff=0.1, ci_lo=-0.3, ci_hi=0.5, n_tasks=5
            ),  # straddles
            PairwiseEntry(
                a="b", b="c", mean_diff=0.0, ci_lo=0.0, ci_hi=0.4, n_tasks=5
            ),  # touches 0
            PairwiseEntry(a="b", b="d"),  # no common task
        ],
    )
    assert undecided_pairs(cmp) == [("a", "d"), ("b", "c"), ("b", "d")]


# ---------------------------------------------------------------------------
# run_campaign: staged repeats
# ---------------------------------------------------------------------------


def test_staged_repeat_reruns_only_tasks_where_subjects_disagree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    suite, suite_id, subjects, out = _setup(tmp_path, 8, "sub-a", "sub-b")
    # t1-t3: a wins; t4-t6: b wins (mean paired diff 0 -> CI straddles 0);
    # t7: both solve; t8: both fail (the two tasks that cannot move the difference).
    plan: dict[tuple[str, str], list[str]] = {}
    for i in range(1, 9):
        a_eff = SOLVE if i in (1, 2, 3, 7) else FAIL
        b_eff = SOLVE if i in (4, 5, 6, 7) else FAIL
        plan[("sub-a", f"t{i}")] = [a_eff]
        plan[("sub-b", f"t{i}")] = [b_eff]
    counters = install_scripted_subject(monkeypatch, plan)

    result = _campaign(suite, subjects, out, repeats=2)

    rec = result.record
    assert (rec.repeats_requested, rec.repeats_done, rec.staged) == (2, 2, True)
    assert [e.repeat for e in rec.stage_log] == [1, 2]
    assert rec.stage_log[0].task_ids == [f"t{i}" for i in range(1, 9)]
    assert rec.stage_log[0].reason.startswith("stage 1")
    second = rec.stage_log[1]
    assert sorted(second.task_ids) == [f"t{i}" for i in range(1, 7)]  # NOT t7 / t8
    assert "1 pair(s) with a CI that includes 0 (sub-a vs sub-b)" in second.reason
    assert "6 disagree" in second.reason
    assert second.subjects == ["sub-a", "sub-b"]  # repeat k+1 runs for ALL subjects
    assert rec.stage_stop_reason == campaign_mod.STOP_MAX_REPEATS

    # The repeat dirs exist, for both subjects, and only hold the selected tasks.
    for sr in rec.subjects:
        assert len(sr.repeat_run_dirs) == 1 and sr.repeat_run_dirs[0].endswith("-r2")
        r2 = _run_json(sr.repeat_run_dirs[0])
        assert r2["repeat"] == 2
        assert sorted(t["task_id"] for t in r2["tasks"]) == [f"t{i}" for i in range(1, 7)]
        assert {t["repeat"] for t in r2["tasks"]} == {2}
    # t7/t8 ran once per subject, t1-t6 twice.
    assert counters[("sub-a", "t7")] == 1 and counters[("sub-b", "t8")] == 1
    assert counters[("sub-a", "t1")] == 2 and counters[("sub-b", "t6")] == 2

    # The comparison covers every repeat dir: n=2 where re-run, n=1 elsewhere.
    cmp = result.comparison
    assert cmp is not None
    assert cmp.repeats_per_subject == {"sub-a": 2, "sub-b": 2}
    assert cmp.matrix["t1"]["sub-a"].n == 2  # type: ignore[union-attr]
    assert cmp.matrix["t7"]["sub-a"].n == 1  # type: ignore[union-attr]
    assert cmp.pairwise[0].n_tasks == 8
    on_disk = json.loads(Path(rec.comparison_json or "").read_text())
    assert on_disk["repeats_per_subject"] == {"sub-a": 2, "sub-b": 2}
    # campaign.json carries the same story.
    campaign_json = json.loads((Path(result.campaign_dir) / "campaign.json").read_text())
    assert campaign_json["repeats_requested"] == 2 and campaign_json["stage_log"][1]["repeat"] == 2
    assert campaign_json["schema_version"] == campaign_mod.CAMPAIGN_SCHEMA_VERSION


def test_staged_stops_once_every_ci_excludes_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    suite, _, subjects, out = _setup(tmp_path, 4, "sub-a", "sub-b")
    plan = {("sub-a", f"t{i}"): [SOLVE] for i in range(1, 5)}
    plan.update({("sub-b", f"t{i}"): [FAIL] for i in range(1, 5)})
    counters = install_scripted_subject(monkeypatch, plan)

    result = _campaign(suite, subjects, out, repeats=3)

    rec = result.record
    assert (rec.repeats_requested, rec.repeats_done) == (3, 1)
    assert rec.stage_stop_reason == campaign_mod.STOP_ALL_DECIDED
    assert [e.repeat for e in rec.stage_log] == [1]
    assert all(sr.repeat_run_dirs == [] for sr in rec.subjects)
    assert sum(counters.values()) == 8  # 2 subjects x 4 tasks, once


def test_staged_stops_when_subjects_never_disagree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    suite, _, subjects, out = _setup(tmp_path, 3, "sub-a", "sub-b")
    counters = install_scripted_subject(monkeypatch, {})  # both plain noop-ish fakes

    result = _campaign(suite, subjects, out, repeats=3)

    rec = result.record
    assert rec.repeats_done == 1
    assert rec.stage_stop_reason == campaign_mod.STOP_NO_CANDIDATES
    assert sum(counters.values()) == 6  # more repeats could not change a difference of 0


def test_staged_runs_up_to_repeat_three_then_stops_at_max(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    suite, _, subjects, out = _setup(tmp_path, 2, "sub-a", "sub-b")
    # a wins t1, b wins t2 on every repeat: the CI [-1, 1] never excludes 0.
    plan = {
        ("sub-a", "t1"): [SOLVE],
        ("sub-a", "t2"): [FAIL],
        ("sub-b", "t1"): [FAIL],
        ("sub-b", "t2"): [SOLVE],
    }
    install_scripted_subject(monkeypatch, plan)

    rec = _campaign(suite, subjects, out, repeats=3).record

    assert [e.repeat for e in rec.stage_log] == [1, 2, 3]
    assert rec.repeats_done == 3
    assert rec.stage_stop_reason == campaign_mod.STOP_MAX_REPEATS
    assert [d[-3:] for d in rec.subjects[0].repeat_run_dirs] == ["-r2", "-r3"]


def test_staged_stops_early_once_a_repeat_decides_the_pair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    suite, _, subjects, out = _setup(tmp_path, 6, "sub-a", "sub-b")
    plan: dict[tuple[str, str], list[str]] = {}
    for i in range(1, 6):  # a clearly wins t1-t5
        plan[("sub-a", f"t{i}")] = [SOLVE]
        plan[("sub-b", f"t{i}")] = [FAIL]
    # t6 flips on the first repeat: b wins it at r1, but at r2 each solves it once-each.
    plan[("sub-a", "t6")] = [FAIL, SOLVE]
    plan[("sub-b", "t6")] = [SOLVE, FAIL]
    install_scripted_subject(monkeypatch, plan)

    rec = _campaign(suite, subjects, out, repeats=4).record

    # r1 diffs [1,1,1,1,1,-1] straddle 0; after r2 t6 averages out (diff 0) and the CI of
    # [1,1,1,1,1,0] excludes 0, so there is no third repeat.
    assert [e.repeat for e in rec.stage_log] == [1, 2]
    assert rec.stage_stop_reason == campaign_mod.STOP_ALL_DECIDED


def test_infra_hole_is_retried_in_the_next_repeat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    suite, _, subjects, out = _setup(tmp_path, 3, "sub-a", "sub-b")
    # b hits an infra error on t1 the first time only; otherwise the subjects agree
    # everywhere (a pair of identical subjects would otherwise stop with no candidates).
    plan = {
        ("sub-a", "t1"): [SOLVE],
        ("sub-b", "t1"): ["error", SOLVE],
        ("sub-a", "t2"): [SOLVE],
        ("sub-b", "t2"): [SOLVE],
        ("sub-a", "t3"): [FAIL],
        ("sub-b", "t3"): [FAIL],
    }
    install_scripted_subject(monkeypatch, plan)

    result = _campaign(suite, subjects, out, repeats=2)

    rec = result.record
    assert [e.task_ids for e in rec.stage_log[1:]] == [["t1"]]
    assert "1 infra-retry" in rec.stage_log[1].reason
    cell = result.comparison.matrix["t1"]["sub-b"]  # type: ignore[union-attr]
    assert cell is not None and cell.n == 1  # attempted once (the error did not count)
    assert result.comparison.infra["sub-b"].count == 1  # type: ignore[union-attr]


def test_single_subject_has_nothing_to_compare_so_no_repeats(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    suite, _, subjects, out = _setup(tmp_path, 2, "sub-a")
    install_scripted_subject(monkeypatch, {})

    rec = _campaign(suite, subjects, out, repeats=3).record

    assert rec.repeats_done == 1 and rec.stage_stop_reason == campaign_mod.STOP_NO_PAIRS


def test_single_repeat_campaign_is_unchanged(tmp_path: Path) -> None:
    suite, _, subjects, out = _setup(tmp_path, 2, "sub-a", "sub-b")

    rec = _campaign(suite, subjects, out).record

    assert (rec.repeats_requested, rec.repeats_done) == (1, 1)
    assert rec.stage_stop_reason == campaign_mod.STOP_NOT_REQUESTED
    assert [e.repeat for e in rec.stage_log] == [1]
    assert all(sr.repeat_run_dirs == [] and sr.repeat_cost_usd == 0.0 for sr in rec.subjects)
    assert not [p for p in out.iterdir() if p.name.endswith("-r2")]


def test_non_staged_repeats_run_every_task_for_every_subject(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    suite, _, subjects, out = _setup(tmp_path, 3, "sub-a", "sub-b")
    counters = install_scripted_subject(monkeypatch, {})  # identical subjects: staged would stop

    rec = _campaign(suite, subjects, out, repeats=3, staged=False).record

    assert rec.staged is False and rec.repeats_done == 3
    assert rec.stage_stop_reason == campaign_mod.STOP_NON_STAGED
    assert [e.task_ids for e in rec.stage_log] == [["t1", "t2", "t3"]] * 3
    assert all(e.reason.startswith(("stage 1", "non-staged")) for e in rec.stage_log)
    assert all(c == 3 for c in counters.values())  # every (subject, task) ran 3 times


def test_whole_run_cap_covers_every_repeat(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    suite, _, subjects, out = _setup(tmp_path, 2, "sub-a", "sub-b", fake_cost=1.0)
    install_scripted_subject(monkeypatch, {})

    # Stage 1 spends $2 per subject = $4; $1 of headroom is left for repeat 2: sub-a gets
    # it (one task, then its own cap trips), sub-b is not launched at all.
    result = _campaign(suite, subjects, out, repeats=2, staged=False, run_budget_usd=5.0)

    rec = result.record
    assert rec.total_spent_usd == pytest.approx(5.0)
    assert rec.stage_log[1].subjects == ["sub-a"]
    assert len(rec.subjects[0].repeat_run_dirs) == 1 and rec.subjects[1].repeat_run_dirs == []
    assert rec.subjects[0].repeat_cost_usd == pytest.approx(1.0)
    r2 = _run_json(rec.subjects[0].repeat_run_dirs[0])
    assert sorted(t["subject_status"] for t in r2["tasks"]) == ["skipped_budget", "succeeded"]


def test_exhausted_cap_stops_the_repeat_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    suite, _, subjects, out = _setup(tmp_path, 2, "sub-a", "sub-b", fake_cost=1.0)
    install_scripted_subject(monkeypatch, {})

    rec = _campaign(suite, subjects, out, repeats=3, staged=False, run_budget_usd=4.0).record

    assert rec.stage_stop_reason == campaign_mod.STOP_BUDGET
    assert rec.repeats_done == 1
    assert all(sr.repeat_run_dirs == [] for sr in rec.subjects)


def test_budget_skipped_subject_never_gets_repeats(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    suite, _, subjects, out = _setup(tmp_path, 1, "sub-a", "sub-b", "sub-c", fake_cost=2.0)
    install_scripted_subject(monkeypatch, {})

    result = _campaign(suite, subjects, out, repeats=2, staged=False, run_budget_usd=4.0)

    rec = result.record
    assert [sr.launched for sr in rec.subjects] == [True, True, False]
    assert rec.subjects[2].status == campaign_mod.STATUS_SKIPPED_BUDGET
    assert rec.subjects[2].repeat_run_dirs == []
    assert result.comparison is not None and result.comparison.not_run == ["sub-c"]


def test_resumed_campaign_replays_without_rerunning_repeats(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    suite, _, subjects, out = _setup(tmp_path, 2, "sub-a", "sub-b")
    plan = {
        ("sub-a", "t1"): [SOLVE],
        ("sub-a", "t2"): [FAIL],
        ("sub-b", "t1"): [FAIL],
        ("sub-b", "t2"): [SOLVE],
    }
    counters = install_scripted_subject(monkeypatch, plan)

    first = _campaign(suite, subjects, out, repeats=2).record
    calls_after_first = dict(counters)
    second = _campaign(suite, subjects, out, repeats=2).record  # same day -> resume

    assert counters == calls_after_first  # not one task was re-run
    assert second.stage_log == first.stage_log
    assert second.total_spent_usd == first.total_spent_usd


@pytest.mark.parametrize("bad", [0, -1])
def test_repeats_below_one_is_a_usage_error(tmp_path: Path, bad: int) -> None:
    suite, _, subjects, out = _setup(tmp_path, 1, "sub-a")
    with pytest.raises(BenchError, match="--repeats must be >= 1"):
        _campaign(suite, subjects, out, repeats=bad)


# ---------------------------------------------------------------------------
# CLI: ao-bench campaign --repeats / --no-staged, ao-bench report over repeat dirs
# ---------------------------------------------------------------------------


def _campaign_args(suite: Path, subjects: list[Path], out: Path, *extra: str) -> list[str]:
    args = ["campaign", "--suite", str(suite), "--out-dir", str(out)]
    for s in subjects:
        args += ["--subject", str(s)]
    return args + list(extra)


def test_campaign_help_documents_repeat_flags() -> None:
    result = cli.invoke(app, ["campaign", "--help"])
    assert result.exit_code == 0, result.output
    assert "--repeats" in result.output and "--no-staged" in result.output


def test_cli_campaign_repeats_prints_the_stage_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    suite, suite_id, subjects, out = _setup(tmp_path, 2, "sub-a", "sub-b")
    plan = {
        ("sub-a", "t1"): [SOLVE],
        ("sub-a", "t2"): [FAIL],
        ("sub-b", "t1"): [FAIL],
        ("sub-b", "t2"): [SOLVE],
    }
    install_scripted_subject(monkeypatch, plan)

    result = cli.invoke(app, _campaign_args(suite, subjects, out, "--repeats", "2"))

    assert result.exit_code == 0, result.output
    assert "Repeats (staged): 2/2 done; stopped: max_repeats" in result.output
    assert "repeat 1: 2 task(s) [t1, t2] -- stage 1" in result.output
    assert "repeat 2: 2 task(s)" in result.output and "(2 disagree)" in result.output
    assert "+1 repeat(s)" in result.output
    campaign_dir = next(out.glob(f"*{suite_id}-campaign"))
    data = json.loads((campaign_dir / "campaign.json").read_text())
    assert data["repeats_requested"] == 2 and data["repeats_done"] == 2 and data["staged"] is True
    assert [e["repeat"] for e in data["stage_log"]] == [1, 2]


def test_cli_campaign_no_staged_runs_everything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    suite, suite_id, subjects, out = _setup(tmp_path, 2, "sub-a", "sub-b")
    counters = install_scripted_subject(monkeypatch, {})

    result = cli.invoke(app, _campaign_args(suite, subjects, out, "--repeats", "2", "--no-staged"))

    assert result.exit_code == 0, result.output
    assert "Repeats (non-staged): 2/2 done; stopped: non_staged_complete" in result.output
    assert all(c == 2 for c in counters.values())
    data = json.loads((next(out.glob(f"*{suite_id}-campaign")) / "campaign.json").read_text())
    assert data["staged"] is False


def test_cli_campaign_repeats_zero_is_rejected_by_the_parser(tmp_path: Path) -> None:
    suite, _, subjects, out = _setup(tmp_path, 1, "sub-a")
    result = cli.invoke(app, _campaign_args(suite, subjects, out, "--repeats", "0"))
    assert result.exit_code == 2
    assert "--repeats" in result.output


def test_cli_campaign_default_is_single_pass(tmp_path: Path) -> None:
    suite, suite_id, subjects, out = _setup(tmp_path, 1, "sub-a")

    result = cli.invoke(app, _campaign_args(suite, subjects, out))

    assert result.exit_code == 0, result.output
    assert "Repeats (" not in result.output  # nothing to say about a single pass


def _cli_run(suite: Path, subject: Path, out: Path, *extra: str) -> None:
    result = cli.invoke(
        app,
        ["run", "--suite", str(suite), "--subject", str(subject), "--out-dir", str(out), *extra],
    )
    assert result.exit_code in (0, 2), result.output


def test_cli_report_accepts_repeat_dirs_and_prints_pairwise(tmp_path: Path) -> None:
    suite_id = f"cr-{uniq()}"
    out = tmp_path / "results"
    suite = write_suite(tmp_path, suite_id, [solvable_task(f"t{i}") for i in range(1, 5)])
    a = write_subject(tmp_path, f"a-{suite_id}", scripted_effect=SOLVE)
    b = write_subject(tmp_path, f"b-{suite_id}", scripted_effect=FAIL)
    for subj in (a, b):
        _cli_run(suite, subj, out)
        _cli_run(suite, subj, out, "--repeat", "2")
    dirs = sorted(out.glob(f"*{suite_id}*"))
    assert len(dirs) == 4

    args = ["report", "--out-dir", str(tmp_path / "cmp")]
    for d in dirs:
        args += ["--run-dir", str(d)]
    result = cli.invoke(app, args)

    assert result.exit_code == 0, result.output
    assert f"- a-{suite_id} vs b-{suite_id}: +1.000 [1.000, 1.000] p=0.125 (n=4)" in result.output
    data = json.loads(next((tmp_path / "cmp").glob("*/comparison.json")).read_text())
    assert data["repeats_per_subject"] == {f"a-{suite_id}": 2, f"b-{suite_id}": 2}
    assert data["schema_version"] == "2.0"


def test_cli_report_results_root_discovery_groups_repeats_and_skips_campaign_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    suite, suite_id, subjects, out = _setup(tmp_path, 2, "sub-a", "sub-b")
    plan = {
        ("sub-a", "t1"): [SOLVE],
        ("sub-a", "t2"): [FAIL],
        ("sub-b", "t1"): [FAIL],
        ("sub-b", "t2"): [SOLVE],
    }
    install_scripted_subject(monkeypatch, plan)
    assert cli.invoke(app, _campaign_args(suite, subjects, out, "--repeats", "2")).exit_code == 0

    result = cli.invoke(
        app,
        [
            "report",
            "--results-root",
            str(out),
            "--suite",
            suite_id,
            "--out-dir",
            str(tmp_path / "c"),
        ],
    )

    assert result.exit_code == 0, result.output
    data = json.loads(next((tmp_path / "c").glob("*/comparison.json")).read_text())
    assert data["subjects"] == ["sub-a", "sub-b"]  # no phantom "campaign" subject
    assert data["repeats_per_subject"] == {"sub-a": 2, "sub-b": 2}
    assert data["not_run"] == []


def test_cli_report_discovery_takes_the_latest_date_of_each_subject(tmp_path: Path) -> None:
    from agent_orchestrator.bench.cli import _discover_latest_run_dirs

    root = tmp_path / "r"
    for name in (
        "2026-10-01-s-sub",
        "2026-10-01-s-sub-r2",
        "2026-10-02-s-sub",
        "2026-10-02-s-sub-r2",
        "2026-10-02-s-sub-r3",
        "2026-10-02-s-other",
        "2026-10-02-s-compare",
        "2026-10-02-s-campaign",
    ):
        (root / name).mkdir(parents=True)

    found = [p.name for p in _discover_latest_run_dirs(root, "s")]

    assert found == [
        "2026-10-02-s-other",
        "2026-10-02-s-sub",
        "2026-10-02-s-sub-r2",
        "2026-10-02-s-sub-r3",
    ]


def test_cli_report_parity_mismatch_refused_unless_allow_mixed(tmp_path: Path) -> None:
    suite_id = f"cp-{uniq()}"
    out = tmp_path / "results"
    suite = write_suite(tmp_path, suite_id, [solvable_task("t1"), solvable_task("t2")])
    bundle = write_parity_bundle(tmp_path)
    a = write_subject(tmp_path, f"a-{suite_id}", scripted_effect=SOLVE)
    b = write_subject(tmp_path, f"b-{suite_id}", scripted_effect=FAIL)
    _cli_run(suite, a, out, "--parity", str(bundle))
    _cli_run(suite, b, out)  # no bundle: not apples-to-apples
    dirs = sorted(out.glob(f"*{suite_id}*"))
    base = ["report", "--out-dir", str(tmp_path / "cmp"), "--run-dir", str(dirs[0]),
            "--run-dir", str(dirs[1])]  # fmt: skip

    refused = cli.invoke(app, base)
    allowed = cli.invoke(app, [*base, "--allow-mixed"])

    assert refused.exit_code == 1 and "parity_fingerprint" in refused.output
    assert allowed.exit_code == 0, allowed.output
    assert "NON-PARITY" in allowed.output
    data = json.loads(next((tmp_path / "cmp").glob("*/comparison.json")).read_text())
    assert data["parity_mixed"] is True


def test_cli_report_warns_when_a_subject_is_unreliable(tmp_path: Path) -> None:
    suite_id = f"cu-{uniq()}"
    out = tmp_path / "results"
    suite = write_suite(tmp_path, suite_id, [solvable_task("t1"), solvable_task("t2")])
    a = write_subject(tmp_path, f"a-{suite_id}", scripted_effect=SOLVE)
    b = write_subject(tmp_path, f"err-{suite_id}", scripted_effect="error")
    _cli_run(suite, a, out)
    _cli_run(suite, b, out)  # exit 2 (harness failures) is fine here
    dirs = sorted(out.glob(f"*{suite_id}*"))

    result = cli.invoke(
        app,
        [
            "report",
            "--out-dir",
            str(tmp_path / "cmp"),
            "--run-dir",
            str(dirs[0]),
            "--run-dir",
            str(dirs[1]),
        ],  # fmt: skip
    )

    assert result.exit_code == 0, result.output
    assert f"WARNING: UNRELIABLE (>10% infra errors): err-{suite_id}" in result.output
    assert f"vs err-{suite_id}: no common attempted task" in result.output
