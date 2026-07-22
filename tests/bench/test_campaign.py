"""CliRunner E2E tests for `ao-bench campaign` (E-Bt4Xk9 T-Cm9Tb4, ADR-0009 D3 whole-run
USD cap) + `bench/campaign.py` unit coverage for the one guard that can't be reached
through the CLI (Click's own required-option validation gets there first).

Invokes the Typer app directly (outer boundary, per CLAUDE.md's e2e testing rule),
mirroring `tests/bench/test_cli_bench.py`'s own convention for `run`/`report`/`list`.
Network-free throughout: every suite/subject here uses the `fake` subject + `fake`
grader with a scripted `fake_cost` (no real LLM, no subprocess, no Docker). Every
`--out-dir` is a per-test `tmp_path` -- never the committed `benchmarks/results/` tree.

Uses the shared `suite_factory`/`subject_factory` fixtures from `tests/bench/conftest.py`
(materialize real instruction.md/fixture files `load_suite` requires) plus a per-test
random id suffix (`_uniq()`, mirrors `test_budget.py`/`test_parallel_runner.py`) so
repeated runs on the same day never collide on a `bench_run_id`/`campaign_id`.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from agent_orchestrator.bench import campaign as campaign_mod
from agent_orchestrator.bench import subjects as subjects_mod
from agent_orchestrator.bench.cli import app
from agent_orchestrator.bench.errors import BenchError
from agent_orchestrator.bench.registries import SUBJECT_REGISTRY
from agent_orchestrator.bench.spec import BenchTask
from agent_orchestrator.bench.subjects import RunContext, SubjectResult

runner = CliRunner()


def _uniq() -> str:
    return uuid.uuid4().hex[:8]


def _fake_task(task_id: str) -> dict[str, Any]:
    return {
        "id": task_id,
        "category": "bugfix",
        "instruction": f"tasks/{task_id}/instruction.md",
        "fixture": f"tasks/{task_id}/fixture",
        "grader": {"type": "fake"},
        "timeout_seconds": 60,
        "tags": [],
    }


def _campaign_json(out_dir: Path, suite_id: str) -> dict[str, Any]:
    dirs = list(out_dir.glob(f"*{suite_id}-campaign"))
    assert len(dirs) == 1, f"expected exactly one campaign dir, found {dirs}"
    return json.loads((dirs[0] / campaign_mod.CAMPAIGN_JSON_FILENAME).read_text())  # type: ignore[no-any-return]


# ---------------------------------------------------------------------------
# --help
# ---------------------------------------------------------------------------


def test_campaign_help_documents_flags() -> None:
    result = runner.invoke(app, ["campaign", "--help"])
    assert result.exit_code == 0, result.output
    for flag in (
        "--suite",
        "--subject",
        "--max-parallel",
        "--cost-budget-usd",
        "--run-budget-usd",
        "--out-dir",
        "--force",
        "--enable-xlarge",
    ):
        assert flag in result.output


def test_campaign_missing_subject_is_a_click_usage_error(tmp_path: Path) -> None:
    """At least one `--subject` is required; Click's OWN required-option validation
    rejects this before `run_campaign` is ever called (exit code 2, not this CLI's own
    `EXIT_USAGE_ERROR=1` -- consistent with how `run`'s own required `--suite`/
    `--subject` already behave when omitted).
    """
    result = runner.invoke(app, ["campaign", "--suite", str(tmp_path / "s.json")])
    assert result.exit_code == 2, result.output
    assert "--subject" in result.output


def test_run_campaign_direct_call_rejects_empty_subject_list() -> None:
    """`run_campaign`'s own defense-in-depth guard (unreachable through the CLI, which
    Click already blocks earlier -- see the test above)."""
    with pytest.raises(BenchError, match="at least one"):
        campaign_mod.run_campaign("suite.json", [])


# ---------------------------------------------------------------------------
# Disabled-tier gate (`--enable-xlarge`) -- uses the real committed
# benchmarks/tiers.json (xlarge really is enabled:false there).
# ---------------------------------------------------------------------------


def test_campaign_disabled_tier_refused_without_enable_xlarge(
    tmp_path: Path, suite_factory: Any, subject_factory: Any
) -> None:
    uniq = _uniq()
    suite_path = suite_factory(
        suite_id=f"camp-xlarge-{uniq}",
        tasks=[_fake_task("t1")],
        extra_top_level={"tier": "xlarge"},
    )
    subject_path = subject_factory(subject_id=f"sub-xlarge-{uniq}", dest_name="sub.json")
    out_dir = tmp_path / "results"

    result = runner.invoke(
        app,
        [
            "campaign",
            "--suite",
            str(suite_path),
            "--subject",
            str(subject_path),
            "--out-dir",
            str(out_dir),
        ],
    )
    assert result.exit_code == 1, result.output
    assert "xlarge" in result.output
    assert "--enable-xlarge" in result.output
    assert not list(out_dir.glob("*")) if out_dir.exists() else True


def test_campaign_disabled_tier_proceeds_with_enable_xlarge(
    tmp_path: Path, suite_factory: Any, subject_factory: Any
) -> None:
    uniq = _uniq()
    suite_id = f"camp-xlarge-ok-{uniq}"
    suite_path = suite_factory(
        suite_id=suite_id, tasks=[_fake_task("t1")], extra_top_level={"tier": "xlarge"}
    )
    subject_path = subject_factory(subject_id=f"sub-xlarge-ok-{uniq}", dest_name="sub.json")
    out_dir = tmp_path / "results"

    result = runner.invoke(
        app,
        [
            "campaign",
            "--suite",
            str(suite_path),
            "--subject",
            str(subject_path),
            "--out-dir",
            str(out_dir),
            "--enable-xlarge",
        ],
    )
    assert result.exit_code == 0, result.output
    campaign = _campaign_json(out_dir, suite_id)
    assert campaign["subjects"][0]["status"] == campaign_mod.STATUS_COMPLETED


# ---------------------------------------------------------------------------
# AC1 -- whole-run cap stops launching once cumulative ACTUAL spend reaches it.
# ---------------------------------------------------------------------------


def test_campaign_whole_run_cap_stops_third_subject(
    tmp_path: Path, suite_factory: Any, subject_factory: Any
) -> None:
    """3 subjects, each a single $5 task; --run-budget-usd 10 (whole cap) and a
    generous --cost-budget-usd 100 (per-subject base, deliberately NOT the binding
    constraint here -- see the sibling per-subject-cap test below for that). Subject 1
    (cum 0->5) and subject 2 (cum 5->10) both run in full; by subject 3, cumulative
    spend (10) already reaches the whole cap (10), so it is recorded skipped_budget
    WITHOUT `run_suite` ever being called for it (no run dir is ever created), and the
    comparison covers the two that ran plus shows subject 3 as not-run.
    """
    uniq = _uniq()
    suite_id = f"camp-cap-{uniq}"
    suite_path = suite_factory(suite_id=suite_id, tasks=[_fake_task("t1")])
    sub1 = subject_factory(
        subject_id=f"sub1-{uniq}", dest_name="sub1.json", extra={"fake_cost": 5.0}
    )
    sub2 = subject_factory(
        subject_id=f"sub2-{uniq}", dest_name="sub2.json", extra={"fake_cost": 5.0}
    )
    sub3 = subject_factory(
        subject_id=f"sub3-{uniq}", dest_name="sub3.json", extra={"fake_cost": 5.0}
    )
    out_dir = tmp_path / "results"

    result = runner.invoke(
        app,
        [
            "campaign",
            "--suite",
            str(suite_path),
            "--subject",
            str(sub1),
            "--subject",
            str(sub2),
            "--subject",
            str(sub3),
            "--out-dir",
            str(out_dir),
            "--cost-budget-usd",
            "100",
            "--run-budget-usd",
            "10",
        ],
    )
    assert result.exit_code == 0, result.output  # budget-skipping a subject is not a failure

    campaign = _campaign_json(out_dir, suite_id)
    assert campaign["whole_run_cap_usd"] == 10.0
    assert campaign["per_subject_cap_usd"] == 100.0
    assert campaign["total_spent_usd"] == 10.0
    subjects = {s["subject_id"]: s for s in campaign["subjects"]}
    assert subjects[f"sub1-{uniq}"]["status"] == campaign_mod.STATUS_COMPLETED
    assert subjects[f"sub1-{uniq}"]["launched"] is True
    assert subjects[f"sub1-{uniq}"]["total_cost_usd"] == 5.0
    assert subjects[f"sub1-{uniq}"]["run_dir"] is not None
    assert subjects[f"sub2-{uniq}"]["status"] == campaign_mod.STATUS_COMPLETED
    assert subjects[f"sub2-{uniq}"]["total_cost_usd"] == 5.0
    assert subjects[f"sub3-{uniq}"]["status"] == campaign_mod.STATUS_SKIPPED_BUDGET
    assert subjects[f"sub3-{uniq}"]["launched"] is False
    assert subjects[f"sub3-{uniq}"]["run_dir"] is None
    assert subjects[f"sub3-{uniq}"]["total_cost_usd"] is None

    # subject 3 was never launched -> no result dir was ever created for it.
    assert not list(out_dir.glob(f"*{suite_id}-sub3-{uniq}"))

    # comparison covers the ones that ran and flags the skipped one as not-run.
    assert campaign["comparison_json"] is not None
    comparison = json.loads(Path(campaign["comparison_json"]).read_text())
    assert comparison["subjects"] == [f"sub1-{uniq}", f"sub2-{uniq}", f"sub3-{uniq}"]
    assert comparison["not_run"] == [f"sub3-{uniq}"]


# ---------------------------------------------------------------------------
# AC2 -- per-subject cap = min(remaining_whole_budget, per_subject_cap).
# ---------------------------------------------------------------------------


def test_campaign_per_subject_cap_bounded_by_remaining_headroom(
    tmp_path: Path, suite_factory: Any, subject_factory: Any
) -> None:
    """--run-budget-usd 7, --cost-budget-usd 5 (per-subject base). Subject 1 (1 task,
    $5) consumes the base cap in full (cum -> 5). Subject 2's remaining headroom is
    then 7-5=2, TIGHTER than the $5 base -- resolved cap must be min(5, 2) = 2, not 5.
    Subject 2 has THREE $1 tasks: at cap=2 exactly 2 of them run (cum 0->1->2, third
    checked at cum=2>=2 -> skipped_budget) and its aggregate cost is $2, never $3 --
    which it WOULD be if the campaign had incorrectly used the $5 base instead of the
    tightened $2. This proves the min(...) is actually applied, not just resolved and
    ignored.
    """
    uniq = _uniq()
    suite_id = f"camp-persub-{uniq}"
    # `run_campaign` runs the SAME suite against every subject (ADR-0009 D3: "runs the
    # suite against a list of subjects"), so task COUNT can't vary per subject -- one
    # suite with THREE tasks, run against subject 1 (fake_cost $5/task, so its own
    # FIRST task alone already reaches the $5 base cap, leaving tasks 2/3
    # skipped_budget -- aggregate cost stays $5) and subject 2 (fake_cost $1/task, so
    # the $2 TIGHTENED cap lets exactly 2 of its 3 tasks run).
    suite_path = suite_factory(
        suite_id=suite_id,
        tasks=[_fake_task("t1"), _fake_task("t2"), _fake_task("t3")],
    )
    sub1 = subject_factory(
        subject_id=f"sub1-{uniq}", dest_name="sub1.json", extra={"fake_cost": 5.0}
    )
    sub2 = subject_factory(
        subject_id=f"sub2-{uniq}", dest_name="sub2.json", extra={"fake_cost": 1.0}
    )
    out_dir = tmp_path / "results"

    result = runner.invoke(
        app,
        [
            "campaign",
            "--suite",
            str(suite_path),
            "--subject",
            str(sub1),
            "--subject",
            str(sub2),
            "--out-dir",
            str(out_dir),
            "--cost-budget-usd",
            "5",
            "--run-budget-usd",
            "7",
        ],
    )
    assert result.exit_code == 0, result.output

    campaign = _campaign_json(out_dir, suite_id)
    subjects = {s["subject_id"]: s for s in campaign["subjects"]}
    # Subject 1: cap = min(5, remaining=7) = 5 -- first $5 task consumes it exactly.
    assert subjects[f"sub1-{uniq}"]["total_cost_usd"] == 5.0
    # Subject 2: cap = min(5, remaining=7-5=2) = 2 -- NOT 5. Exactly 2 of 3 $1 tasks run.
    assert subjects[f"sub2-{uniq}"]["total_cost_usd"] == 2.0
    assert campaign["total_spent_usd"] == 7.0  # hits the whole cap exactly, no overshoot

    sub2_run_dir = Path(subjects[f"sub2-{uniq}"]["run_dir"])
    on_disk = json.loads((sub2_run_dir / "run.json").read_text())
    statuses = sorted(t["subject_status"] for t in on_disk["tasks"])
    assert statuses == ["skipped_budget", "succeeded", "succeeded"]


def test_campaign_subject_can_overshoot_whole_cap_by_bounded_per_task_amount(
    tmp_path: Path, suite_factory: Any, subject_factory: Any
) -> None:
    """The whole-run cap can still be exceeded by at most ONE already-in-flight task's
    cost (ADR-0009 D3/D4 bounded-overshoot, one level up) -- never by more. Subject 1
    ($1) leaves $2 of headroom under a $3 whole cap; subject 2's single task costs $5
    (its own resolved cap is min(100, 2)=2, but the check-before-schedule only compares
    ALREADY-recorded cost, so the very first task of a fresh subject always passes and
    runs to completion). Total spend lands at $6, exceeding the $3 cap by exactly $4
    (subject 2's $5 task minus the $1 of headroom it had) -- bounded to that one task,
    not unbounded.
    """
    uniq = _uniq()
    suite_id = f"camp-overshoot-{uniq}"
    suite_path = suite_factory(suite_id=suite_id, tasks=[_fake_task("solo")])
    sub1 = subject_factory(
        subject_id=f"sub1-{uniq}", dest_name="sub1.json", extra={"fake_cost": 1.0}
    )
    sub2 = subject_factory(
        subject_id=f"sub2-{uniq}", dest_name="sub2.json", extra={"fake_cost": 5.0}
    )
    out_dir = tmp_path / "results"

    result = runner.invoke(
        app,
        [
            "campaign",
            "--suite",
            str(suite_path),
            "--subject",
            str(sub1),
            "--subject",
            str(sub2),
            "--out-dir",
            str(out_dir),
            "--cost-budget-usd",
            "100",
            "--run-budget-usd",
            "3",
        ],
    )
    assert result.exit_code == 0, result.output  # overshoot is documented, never a failure

    campaign = _campaign_json(out_dir, suite_id)
    assert campaign["total_spent_usd"] == 6.0  # 1 + 5, exceeds the $3 cap by $3 (< 1 x $5 task)
    subjects = {s["subject_id"]: s for s in campaign["subjects"]}
    assert (
        subjects[f"sub2-{uniq}"]["status"] == campaign_mod.STATUS_COMPLETED
    )  # launched, not skipped
    assert subjects[f"sub2-{uniq}"]["total_cost_usd"] == 5.0


# ---------------------------------------------------------------------------
# Resume: re-running the same campaign the same day skips already-completed subjects'
# tasks (via run_suite's own resume) and recomputes cumulative spend from disk.
# ---------------------------------------------------------------------------


def test_campaign_resume_does_not_rerun_completed_subject(
    tmp_path: Path, suite_factory: Any, subject_factory: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    uniq = _uniq()
    suite_id = f"camp-resume-{uniq}"
    suite_path = suite_factory(suite_id=suite_id, tasks=[_fake_task("t1")])
    sub1 = subject_factory(
        subject_id=f"sub1-{uniq}", dest_name="sub1.json", extra={"fake_cost": 2.0}
    )
    sub2 = subject_factory(
        subject_id=f"sub2-{uniq}", dest_name="sub2.json", extra={"fake_cost": 2.0}
    )
    out_dir = tmp_path / "results"

    call_count = {"n": 0}

    class _CountingSubject(subjects_mod.FakeSubject):
        def run(self, task: BenchTask, ctx: RunContext) -> SubjectResult:
            call_count["n"] += 1
            return super().run(task, ctx)

    monkeypatch.setitem(SUBJECT_REGISTRY, "fake", _CountingSubject)

    args = [
        "campaign",
        "--suite",
        str(suite_path),
        "--subject",
        str(sub1),
        "--subject",
        str(sub2),
        "--out-dir",
        str(out_dir),
        "--cost-budget-usd",
        "100",
        "--run-budget-usd",
        "100",
    ]

    first = runner.invoke(app, args)
    assert first.exit_code == 0, first.output
    assert call_count["n"] == 2  # one task each, both subjects launched

    campaign_before = _campaign_json(out_dir, suite_id)

    second = runner.invoke(app, args)  # same day -> same bench_run_ids -> resume
    assert second.exit_code == 0, second.output
    assert call_count["n"] == 2  # unchanged: neither subject's task was re-run

    campaign_after = _campaign_json(out_dir, suite_id)
    assert campaign_after["total_spent_usd"] == campaign_before["total_spent_usd"] == 4.0


# ---------------------------------------------------------------------------
# --max-parallel resolution (mirrors `ao-bench run`'s own precedence tests in
# test_cli_bench.py): campaign.py imports `run_suite` at MODULE level (not lazily, since
# it is core orchestration, not a CLI-only concern), so the stub is patched onto
# `agent_orchestrator.bench.campaign.run_suite`, not `...runner.run_suite`.
# ---------------------------------------------------------------------------


def test_campaign_max_parallel_flag_overrides_tier_default(
    tmp_path: Path, suite_factory: Any, subject_factory: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    def _fake_run_suite(*_args: Any, **kwargs: Any) -> Any:
        captured["max_parallel"] = kwargs.get("max_parallel")
        raise BenchError("captured-max-parallel")

    monkeypatch.setattr("agent_orchestrator.bench.campaign.run_suite", _fake_run_suite)

    uniq = _uniq()
    suite_path = suite_factory(
        suite_id=f"camp-mp-{uniq}", tasks=[_fake_task("t1")], extra_top_level={"tier": "medium"}
    )
    subject_path = subject_factory(subject_id=f"sub-mp-{uniq}", dest_name="sub.json")

    result = runner.invoke(
        app,
        [
            "campaign",
            "--suite",
            str(suite_path),
            "--subject",
            str(subject_path),
            "--out-dir",
            str(tmp_path / "results"),
            "--max-parallel",
            "2",
        ],
    )
    assert result.exit_code == 1, result.output
    assert captured["max_parallel"] == 2  # explicit flag wins over medium's tier default (4)


def test_campaign_max_parallel_defaults_to_suite_tier(
    tmp_path: Path, suite_factory: Any, subject_factory: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    def _fake_run_suite(*_args: Any, **kwargs: Any) -> Any:
        captured["max_parallel"] = kwargs.get("max_parallel")
        raise BenchError("captured-max-parallel")

    monkeypatch.setattr("agent_orchestrator.bench.campaign.run_suite", _fake_run_suite)

    uniq = _uniq()
    suite_path = suite_factory(
        suite_id=f"camp-mp-default-{uniq}",
        tasks=[_fake_task("t1")],
        extra_top_level={"tier": "medium"},
    )
    subject_path = subject_factory(subject_id=f"sub-mp-default-{uniq}", dest_name="sub.json")

    result = runner.invoke(
        app,
        [
            "campaign",
            "--suite",
            str(suite_path),
            "--subject",
            str(subject_path),
            "--out-dir",
            str(tmp_path / "results"),
        ],
    )
    assert result.exit_code == 1, result.output
    assert captured["max_parallel"] == 4  # benchmarks/tiers.json medium.default_max_parallel


# ---------------------------------------------------------------------------
# campaign.json shape (schema_version + every field TASK.md calls for).
# ---------------------------------------------------------------------------


def test_campaign_json_shape(tmp_path: Path, suite_factory: Any, subject_factory: Any) -> None:
    uniq = _uniq()
    suite_id = f"camp-shape-{uniq}"
    suite_path = suite_factory(suite_id=suite_id, tasks=[_fake_task("t1")])
    subject_path = subject_factory(
        subject_id=f"sub-shape-{uniq}", dest_name="sub.json", extra={"fake_cost": 0.5}
    )
    out_dir = tmp_path / "results"

    result = runner.invoke(
        app,
        [
            "campaign",
            "--suite",
            str(suite_path),
            "--subject",
            str(subject_path),
            "--out-dir",
            str(out_dir),
        ],
    )
    assert result.exit_code == 0, result.output

    campaign = _campaign_json(out_dir, suite_id)
    assert campaign["schema_version"] == campaign_mod.CAMPAIGN_SCHEMA_VERSION
    assert campaign["suite_id"] == suite_id
    assert campaign["tier"] == "small"
    for key in (
        "campaign_id",
        "generated_at",
        "whole_run_cap_usd",
        "per_subject_cap_usd",
        "max_parallel",
        "total_spent_usd",
        "subjects",
        "comparison_json",
        "comparison_md",
    ):
        assert key in campaign
    subject_row = campaign["subjects"][0]
    for key in (
        "subject_id",
        "subject_path",
        "status",
        "launched",
        "run_dir",
        "total_cost_usd",
        "solved",
        "total",
        "solve_rate",
    ):
        assert key in subject_row
    assert subject_row["solved"] == 1
    assert subject_row["total"] == 1
    assert subject_row["solve_rate"] == 1.0

    campaign_dirs = list(out_dir.glob(f"*{suite_id}-campaign"))
    assert (campaign_dirs[0] / "comparison.json").exists()
    assert (campaign_dirs[0] / "comparison.md").exists()
