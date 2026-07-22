"""CliRunner E2E tests for `ao-bench run/report/list` (T-Cli8Nf acceptance criteria).

Invokes the Typer app directly (outer boundary, per CLAUDE.md's e2e testing rule),
exactly like `tests/bench/test_cli_validate.py` does for `validate` and
`tests/test_cli.py` does for the core `ao` app. `validate --help` itself is already
covered by `test_cli_validate.py`; this file focuses on `run`/`report`/`list` plus the
`--help`/exit-code/SI-1 acceptance criteria that span the whole `ao-bench` app.

Network-free: every suite/subject here uses the `fake` subject + `fake` grader (no real
LLM, no subprocess). Every `--out-dir`/`--results-root` is a per-test `tmp_path` --
never the committed `benchmarks/results/` tree.
"""

from __future__ import annotations

import json
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from agent_orchestrator.bench.cli import app

runner = CliRunner()


def _uniq() -> str:
    return uuid.uuid4().hex[:8]


def _fake_task(task_id: str, *, grader: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "id": task_id,
        "category": "bugfix",
        "instruction": f"tasks/{task_id}/instruction.md",
        "fixture": f"tasks/{task_id}/fixture",
        "grader": grader or {"type": "fake"},
        "timeout_seconds": 30,
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
        "description": "cli test suite",
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


# ---------------------------------------------------------------------------
# AC1 -- `ao-bench --help` lists every command; each subcommand documents its flags.
# ---------------------------------------------------------------------------


def test_help_lists_all_commands() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0, result.output
    for name in ("validate", "run", "report", "list"):
        assert name in result.output


def test_run_help_documents_flags() -> None:
    result = runner.invoke(app, ["run", "--help"])
    assert result.exit_code == 0, result.output
    for flag in (
        "--suite",
        "--subject",
        "--out-dir",
        "--force",
        "--task",
        "--budget-total",
        "--max-turns",
        "--timeout",
        "--cost-budget-usd",
    ):
        assert flag in result.output


def test_report_help_documents_flags() -> None:
    result = runner.invoke(app, ["report", "--help"])
    assert result.exit_code == 0, result.output
    for flag in ("--run-dir", "--results-root", "--suite", "--out-dir", "--allow-mixed"):
        assert flag in result.output


def test_list_help_documents_flags() -> None:
    result = runner.invoke(app, ["list", "--help"])
    assert result.exit_code == 0, result.output
    assert "--suite" in result.output


# ---------------------------------------------------------------------------
# AC2 -- `ao-bench run`: exit 0 + run.json/summary.md written; a failing subject -> non-0.
# ---------------------------------------------------------------------------


def test_run_writes_run_json_and_summary_md_exit_0(tmp_path: Path) -> None:
    uniq = _uniq()
    suite_id = f"cli-run-{uniq}"
    suite_path = _write_suite(tmp_path, suite_id, [_fake_task("t1"), _fake_task("t2")])
    subject_path = _write_fake_subject(tmp_path, f"fk-ok-{uniq}", fake_cost=0.02)
    out_dir = tmp_path / "results"

    result = runner.invoke(
        app,
        [
            "run",
            "--suite",
            str(suite_path),
            "--subject",
            str(subject_path),
            "--out-dir",
            str(out_dir),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Result dir:" in result.output
    assert "solved 2/2" in result.output

    run_dirs = list(out_dir.glob(f"*{suite_id}*"))
    assert len(run_dirs) == 1
    run_dir = run_dirs[0]
    assert (run_dir / "run.json").exists()
    assert (run_dir / "summary.md").exists()
    on_disk = json.loads((run_dir / "run.json").read_text())
    assert on_disk["suite_id"] == suite_id
    assert len(on_disk["tasks"]) == 2


def test_run_subject_task_failure_exits_nonzero(tmp_path: Path) -> None:
    uniq = _uniq()
    suite_id = f"cli-fail-{uniq}"
    suite_path = _write_suite(tmp_path, suite_id, [_fake_task("t1")])
    subject_path = _write_fake_subject(tmp_path, f"fk-fail-{uniq}", scripted_effect="fail")
    out_dir = tmp_path / "results"

    result = runner.invoke(
        app,
        [
            "run",
            "--suite",
            str(suite_path),
            "--subject",
            str(subject_path),
            "--out-dir",
            str(out_dir),
        ],
    )
    assert result.exit_code == 2, result.output
    run_dirs = list(out_dir.glob(f"*{suite_id}*"))
    assert len(run_dirs) == 1
    assert (run_dirs[0] / "run.json").exists()  # still written -- a graded outcome, not a crash


def test_run_bad_spec_path_exits_1(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "run",
            "--suite",
            str(tmp_path / "no-such-suite.json"),
            "--subject",
            str(tmp_path / "no-such-subject.json"),
            "--out-dir",
            str(tmp_path / "results"),
        ],
    )
    assert result.exit_code == 1, result.output
    assert "ERROR" in result.output


def test_run_task_filter_matching_nothing_exits_0_writes_nothing(tmp_path: Path) -> None:
    uniq = _uniq()
    suite_id = f"cli-nomatch-{uniq}"
    suite_path = _write_suite(tmp_path, suite_id, [_fake_task("t1")])
    subject_path = _write_fake_subject(tmp_path, f"fk-nomatch-{uniq}")
    out_dir = tmp_path / "results"

    result = runner.invoke(
        app,
        [
            "run",
            "--suite",
            str(suite_path),
            "--subject",
            str(subject_path),
            "--out-dir",
            str(out_dir),
            "--task",
            "does-not-exist",
        ],
    )
    assert result.exit_code == 0, result.output
    assert not list(out_dir.glob(f"*{suite_id}*"))


def test_run_force_reruns_completed_task(tmp_path: Path) -> None:
    uniq = _uniq()
    suite_id = f"cli-force-{uniq}"
    suite_path = _write_suite(tmp_path, suite_id, [_fake_task("t1")])
    subject_path = _write_fake_subject(tmp_path, f"fk-force-{uniq}")
    out_dir = tmp_path / "results"
    args = [
        "run",
        "--suite",
        str(suite_path),
        "--subject",
        str(subject_path),
        "--out-dir",
        str(out_dir),
    ]

    first = runner.invoke(app, args)
    assert first.exit_code == 0, first.output
    run_dir = next(out_dir.glob(f"*{suite_id}*"))
    before = (run_dir / "run.json").read_text()

    second = runner.invoke(app, args)  # resume: skip (idempotent, unchanged on disk)
    assert second.exit_code == 0, second.output
    assert (run_dir / "run.json").read_text() == before

    third = runner.invoke(app, [*args, "--force"])
    assert third.exit_code == 0, third.output


# ---------------------------------------------------------------------------
# USD cost-budget enforcement (T-Bg2Wq4, ADR-0009 D3): --cost-budget-usd, tier default
# resolution (AC4), and skipped_budget never flipping the exit code (AC2).
# ---------------------------------------------------------------------------


def test_run_explicit_cost_budget_usd_caps_the_run(tmp_path: Path) -> None:
    uniq = _uniq()
    suite_id = f"cli-budget-explicit-{uniq}"
    suite_path = _write_suite(tmp_path, suite_id, [_fake_task("t1"), _fake_task("t2")])
    subject_path = _write_fake_subject(tmp_path, f"fk-budget-{uniq}", fake_cost=10.0)
    out_dir = tmp_path / "results"

    result = runner.invoke(
        app,
        [
            "run",
            "--suite",
            str(suite_path),
            "--subject",
            str(subject_path),
            "--out-dir",
            str(out_dir),
            "--cost-budget-usd",
            "5",
        ],
    )
    assert result.exit_code == 0, result.output  # skipped_budget is not a failure (AC2)

    run_dir = next(out_dir.glob(f"*{suite_id}*"))
    on_disk = json.loads((run_dir / "run.json").read_text())
    by_id = {t["task_id"]: t for t in on_disk["tasks"]}
    # t1 (cum 0<5) runs for real; t2 (cum 10>=5) is skipped_budget.
    assert by_id["t1"]["subject_status"] == "succeeded"
    assert by_id["t2"]["subject_status"] == "skipped_budget"
    assert by_id["t2"]["cost_usd"] == 0.0


def test_run_no_cost_budget_flag_defaults_to_suite_tier_small_cap(tmp_path: Path) -> None:
    """AC4: no `--cost-budget-usd` and no `tier` on the suite -> defaults to "small"
    -> benchmarks/tiers.json's committed `small.cost_budget_usd_per_subject` ($5)."""
    uniq = _uniq()
    suite_id = f"cli-budget-tiersmall-{uniq}"
    suite_path = _write_suite(tmp_path, suite_id, [_fake_task("t1"), _fake_task("t2")])
    subject_path = _write_fake_subject(tmp_path, f"fk-tiersmall-{uniq}", fake_cost=10.0)
    out_dir = tmp_path / "results"

    result = runner.invoke(
        app,
        [
            "run",
            "--suite",
            str(suite_path),
            "--subject",
            str(subject_path),
            "--out-dir",
            str(out_dir),
        ],
    )
    assert result.exit_code == 0, result.output

    run_dir = next(out_dir.glob(f"*{suite_id}*"))
    on_disk = json.loads((run_dir / "run.json").read_text())
    by_id = {t["task_id"]: t for t in on_disk["tasks"]}
    # small.cost_budget_usd_per_subject == $5 (benchmarks/tiers.json): t1 (cum 0<5) runs,
    # t2 (cum 10>=5) is skipped_budget.
    assert by_id["t1"]["subject_status"] == "succeeded"
    assert by_id["t2"]["subject_status"] == "skipped_budget"


def test_run_no_cost_budget_flag_defaults_to_suite_tier_medium_cap(tmp_path: Path) -> None:
    """AC4: `tier: medium` on the suite -> defaults to $50 (medium.cost_budget_usd_per_
    subject) -- the same two $10 tasks that budget-capped under the small default (see
    above) both fit comfortably under medium's cap."""
    uniq = _uniq()
    suite_id = f"cli-budget-tiermedium-{uniq}"
    suite_path = _write_suite(
        tmp_path,
        suite_id,
        [_fake_task("t1"), _fake_task("t2")],
        tier="medium",
    )
    subject_path = _write_fake_subject(tmp_path, f"fk-tiermedium-{uniq}", fake_cost=10.0)
    out_dir = tmp_path / "results"

    result = runner.invoke(
        app,
        [
            "run",
            "--suite",
            str(suite_path),
            "--subject",
            str(subject_path),
            "--out-dir",
            str(out_dir),
        ],
    )
    assert result.exit_code == 0, result.output

    run_dir = next(out_dir.glob(f"*{suite_id}*"))
    on_disk = json.loads((run_dir / "run.json").read_text())
    assert all(t["subject_status"] == "succeeded" for t in on_disk["tasks"])


def test_run_explicit_cost_budget_usd_overrides_tier_default(tmp_path: Path) -> None:
    """An explicit --cost-budget-usd wins over even a more generous tier default."""
    uniq = _uniq()
    suite_id = f"cli-budget-override-{uniq}"
    suite_path = _write_suite(
        tmp_path,
        suite_id,
        [_fake_task("t1"), _fake_task("t2")],
        tier="medium",  # tier default would be $50 -- both tasks would otherwise fit.
    )
    subject_path = _write_fake_subject(tmp_path, f"fk-override-{uniq}", fake_cost=10.0)
    out_dir = tmp_path / "results"

    result = runner.invoke(
        app,
        [
            "run",
            "--suite",
            str(suite_path),
            "--subject",
            str(subject_path),
            "--out-dir",
            str(out_dir),
            "--cost-budget-usd",
            "5",
        ],
    )
    assert result.exit_code == 0, result.output

    run_dir = next(out_dir.glob(f"*{suite_id}*"))
    on_disk = json.loads((run_dir / "run.json").read_text())
    by_id = {t["task_id"]: t for t in on_disk["tasks"]}
    assert by_id["t1"]["subject_status"] == "succeeded"
    assert by_id["t2"]["subject_status"] == "skipped_budget"


def test_run_only_skipped_budget_tasks_still_exits_zero(tmp_path: Path) -> None:
    """AC2: a run whose only non-solved tasks are skipped_budget exits 0 -- never a
    harness failure. Cap=$0 skips EVERY task (running_cost 0 >= cap 0 from the first
    check), the most extreme case: zero successes, zero failures, all skipped_budget.
    """
    uniq = _uniq()
    suite_id = f"cli-budget-allzero-{uniq}"
    suite_path = _write_suite(tmp_path, suite_id, [_fake_task("t1"), _fake_task("t2")])
    subject_path = _write_fake_subject(tmp_path, f"fk-allzero-{uniq}", fake_cost=1.0)
    out_dir = tmp_path / "results"

    result = runner.invoke(
        app,
        [
            "run",
            "--suite",
            str(suite_path),
            "--subject",
            str(subject_path),
            "--out-dir",
            str(out_dir),
            "--cost-budget-usd",
            "0",
        ],
    )
    assert result.exit_code == 0, result.output

    run_dir = next(out_dir.glob(f"*{suite_id}*"))
    on_disk = json.loads((run_dir / "run.json").read_text())
    assert all(t["subject_status"] == "skipped_budget" for t in on_disk["tasks"])


# ---------------------------------------------------------------------------
# AC3 -- `ao-bench report` over two fake result dirs -> comparison.{json,md}, exit 0.
# ---------------------------------------------------------------------------


def test_report_two_run_dirs_writes_comparison_exit_0(tmp_path: Path) -> None:
    uniq = _uniq()
    suite_id = f"cli-cmp-{uniq}"
    out_dir = tmp_path / "results"

    suite_a = _write_suite(tmp_path / "a", suite_id, [_fake_task("t1")])
    subject_a = _write_fake_subject(tmp_path / "a", f"fk-a-{uniq}", fake_cost=0.01)
    run_a = runner.invoke(
        app,
        ["run", "--suite", str(suite_a), "--subject", str(subject_a), "--out-dir", str(out_dir)],
    )
    assert run_a.exit_code == 0, run_a.output

    suite_b = _write_suite(tmp_path / "b", suite_id, [_fake_task("t1")])
    subject_b = _write_fake_subject(tmp_path / "b", f"fk-b-{uniq}", fake_cost=0.05)
    run_b = runner.invoke(
        app,
        ["run", "--suite", str(suite_b), "--subject", str(subject_b), "--out-dir", str(out_dir)],
    )
    assert run_b.exit_code == 0, run_b.output

    run_dirs = sorted(out_dir.glob(f"*{suite_id}*"))
    assert len(run_dirs) == 2
    compare_out = tmp_path / "compare"

    result = runner.invoke(
        app,
        [
            "report",
            "--run-dir",
            str(run_dirs[0]),
            "--run-dir",
            str(run_dirs[1]),
            "--out-dir",
            str(compare_out),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Comparison:" in result.output
    assert "Highest solve rate" in result.output

    compare_dirs = list(compare_out.glob(f"*{suite_id}-compare"))
    assert len(compare_dirs) == 1
    assert (compare_dirs[0] / "comparison.json").exists()
    assert (compare_dirs[0] / "comparison.md").exists()


def test_report_results_root_auto_discovers_latest_per_subject(tmp_path: Path) -> None:
    uniq = _uniq()
    suite_id = f"cli-auto-{uniq}"
    out_dir = tmp_path / "results"

    for tag in ("a", "b"):
        suite_p = _write_suite(tmp_path / tag, suite_id, [_fake_task("t1")])
        subject_p = _write_fake_subject(tmp_path / tag, f"fk-{tag}-{uniq}")
        res = runner.invoke(
            app,
            [
                "run",
                "--suite",
                str(suite_p),
                "--subject",
                str(subject_p),
                "--out-dir",
                str(out_dir),
            ],
        )
        assert res.exit_code == 0, res.output

    result = runner.invoke(
        app,
        [
            "report",
            "--results-root",
            str(out_dir),
            "--suite",
            suite_id,
            "--out-dir",
            str(tmp_path / "compare"),
        ],
    )
    assert result.exit_code == 0, result.output


def test_report_no_run_dir_and_no_results_root_exits_1() -> None:
    result = runner.invoke(app, ["report"])
    assert result.exit_code == 1, result.output
    assert "ERROR" in result.output


def test_report_different_suites_exits_1(tmp_path: Path) -> None:
    uniq = _uniq()
    out_dir = tmp_path / "results"

    suite_a = _write_suite(tmp_path / "a", f"cli-sa-{uniq}", [_fake_task("t1")])
    subject_a = _write_fake_subject(tmp_path / "a", f"fk-sa-{uniq}")
    runner.invoke(
        app,
        ["run", "--suite", str(suite_a), "--subject", str(subject_a), "--out-dir", str(out_dir)],
    )
    suite_b = _write_suite(tmp_path / "b", f"cli-sb-{uniq}", [_fake_task("t1")])
    subject_b = _write_fake_subject(tmp_path / "b", f"fk-sb-{uniq}")
    runner.invoke(
        app,
        ["run", "--suite", str(suite_b), "--subject", str(subject_b), "--out-dir", str(out_dir)],
    )
    run_dirs = sorted(out_dir.iterdir())

    result = runner.invoke(
        app,
        [
            "report",
            "--run-dir",
            str(run_dirs[0]),
            "--run-dir",
            str(run_dirs[1]),
            "--out-dir",
            str(tmp_path / "compare"),
        ],
    )
    assert result.exit_code == 1, result.output
    assert "ERROR" in result.output


def test_report_results_root_no_results_for_suite_exits_1(
    tmp_path: Path,
) -> None:
    """When --results-root and --suite given but no matching dirs, exit 1.

    Tests line 273-276.
    """
    results_root = tmp_path / "results"
    results_root.mkdir()
    # Create no result directories -- just an empty directory

    result = runner.invoke(
        app,
        [
            "report",
            "--results-root",
            str(results_root),
            "--suite",
            "nonexistent-suite",
        ],
    )
    assert result.exit_code == 1, result.output
    assert "ERROR" in result.output
    assert "no result dirs found" in result.output


# ---------------------------------------------------------------------------
# `ao-bench list`
# ---------------------------------------------------------------------------


def test_list_prints_task_table(tmp_path: Path) -> None:
    suite_path = _write_suite(
        tmp_path, f"cli-list-{_uniq()}", [_fake_task("b-task"), _fake_task("a-task")]
    )
    result = runner.invoke(app, ["list", "--suite", str(suite_path)])
    assert result.exit_code == 0, result.output
    assert "a-task" in result.output
    assert "b-task" in result.output
    assert result.output.index("a-task") < result.output.index("b-task")


def test_list_bad_suite_exits_1(tmp_path: Path) -> None:
    result = runner.invoke(app, ["list", "--suite", str(tmp_path / "missing.json")])
    assert result.exit_code == 1, result.output


# ---------------------------------------------------------------------------
# AC5 -- SI-1 regression gate: `ao` never imports `bench`.
# ---------------------------------------------------------------------------


def test_core_cli_import_does_not_pull_in_bench() -> None:
    """`import agent_orchestrator.cli` must never load `agent_orchestrator.bench` --
    a fresh subprocess so no other test's imports in this same process can mask a
    regression (module caching would otherwise hide it).
    """
    code = (
        "import sys\n"
        "import agent_orchestrator.cli\n"
        "assert not any(m.startswith('agent_orchestrator.bench') for m in sys.modules), "
        "sorted(m for m in sys.modules if m.startswith('agent_orchestrator.bench'))\n"
        "print('OK')\n"
    )
    result = subprocess.run(  # noqa: S603 -- fixed argv, no shell, trusted fixed script
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=30, check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK" in result.stdout
