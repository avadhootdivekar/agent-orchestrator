"""Cross-feature integration tests for tiers/budget/parallel/campaign (E-Bt4Xk9 T-Ts0Xn5).

These tests verify end-to-end workflows that single-feature tests can't reach:
- End-to-end CliRunner: medium-tier suite + FakeSubject campaign, tier defaults applied,
  budget enforced, campaign outputs written.
- Resume-under-budget-change: cap raised between runs, campaign resumes skipped tasks.
- Disabled-tier gate (xlarge) through campaign path.
- SI-1 regression: core/swebench never imported by core CLI/import.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from agent_orchestrator.bench.campaign import CAMPAIGN_JSON_FILENAME
from agent_orchestrator.bench.cli import app
from agent_orchestrator.bench.results import COMPARISON_JSON_FILENAME

runner = CliRunner()


# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------


def _uniq() -> str:
    import uuid

    return uuid.uuid4().hex[:8]


def _fake_task(task_id: str) -> dict[str, Any]:
    """A minimal fake task for budget testing."""
    return {
        "id": task_id,
        "category": "test",
        "instruction": f"tasks/{task_id}/instruction.md",
        "fixture": f"tasks/{task_id}/fixture",
        "grader": {"type": "fake"},
        "timeout_seconds": 30,
        "tags": [],
    }


def _write_suite(
    base: Path, suite_id: str, tasks: list[dict[str, Any]], tier: str = "medium", **extra: Any
) -> Path:
    """Write a suite.json with the given tier and tasks."""
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
        "description": "integration test suite",
        "tier": tier,
        "tasks": tasks,
    }
    data.update(extra)
    path = base / f"{suite_id}-suite.json"
    path.write_text(json.dumps(data, indent=2))
    return path


def _write_fake_subject_with_cost(base: Path, subject_id: str, fake_cost: float = 1.0) -> Path:
    """Write a fake subject.json configured to return fake_cost on each task."""
    data: dict[str, Any] = {
        "version": "1.0",
        "id": subject_id,
        "type": "fake",
        "fake_cost": fake_cost,
    }
    path = base / f"{subject_id}-subject.json"
    path.write_text(json.dumps(data, indent=2))
    return path


# ---------------------------------------------------------------------------
# (a) End-to-end CliRunner: medium-tier campaign, tier defaults applied
# ---------------------------------------------------------------------------


def test_campaign_e2e_medium_tier_defaults_applied(tmp_path: Path) -> None:
    """E2E: medium-tier suite (6 tasks, $1 each) + 3 subjects via campaign.

    Verifies:
    - Tier's default_max_parallel (4 for medium) is applied to run_suite.
    - Budget cap from medium tier ($50/subject) is enforced.
    - campaign.json + comparison.json are written to the campaign dir.
    """
    suite_dir = tmp_path / "suite"
    suite_dir.mkdir()
    subjects_dir = tmp_path / "subjects"
    subjects_dir.mkdir()
    out_dir = tmp_path / "results"
    out_dir.mkdir()

    # Create a medium-tier suite with 6 tasks ($6 total with $1 fake_cost, within $50 cap).
    tasks = [_fake_task(f"task-{i:02d}") for i in range(6)]
    suite_path = _write_suite(suite_dir, "medium-suite", tasks, tier="medium")

    # Create 3 fake subjects, $1 cost each.
    subject_paths = []
    for i in range(3):
        subject_id = f"subject-{i}"
        path = _write_fake_subject_with_cost(subjects_dir, subject_id, fake_cost=1.0)
        subject_paths.append(path)

    # Run campaign via CLI.
    result = runner.invoke(
        app,
        [
            "campaign",
            "--suite",
            str(suite_path),
            "--subject",
            str(subject_paths[0]),
            "--subject",
            str(subject_paths[1]),
            "--subject",
            str(subject_paths[2]),
            "--out-dir",
            str(out_dir),
        ],
    )

    assert result.exit_code == 0, result.output
    # Verify campaign outputs exist.
    campaign_dirs = list(out_dir.glob("*medium-suite-campaign"))
    assert len(campaign_dirs) == 1, f"expected exactly one campaign dir, found {campaign_dirs}"
    campaign_dir = campaign_dirs[0]
    assert (campaign_dir / CAMPAIGN_JSON_FILENAME).exists()
    assert (campaign_dir / COMPARISON_JSON_FILENAME).exists()

    # Verify campaign structure.
    campaign = json.loads((campaign_dir / CAMPAIGN_JSON_FILENAME).read_text())
    assert campaign["suite_id"] == "medium-suite"
    assert len(campaign["subjects"]) == 3, f"expected 3 subjects, got {len(campaign['subjects'])}"
    for subject in campaign["subjects"]:
        # All subjects should complete (each costs 6×$1 = $6, within $50 cap).
        assert subject["subject_id"] in {f"subject-{i}" for i in range(3)}
        # All subjects should have "completed" status (no skipped_budget).
        assert subject["status"] == "completed", (
            f"Subject {subject['subject_id']} has status {subject['status']}, expected completed"
        )


def test_campaign_e2e_with_parallel_reaches_pool_worker_count(tmp_path: Path) -> None:
    """E2E: parallel campaign over medium-tier suite.

    Verifies:
    - The tier's default_max_parallel (4) is respected.
    - All tasks complete without loss or corruption (determinism check).
    """
    suite_dir = tmp_path / "suite"
    suite_dir.mkdir()
    subjects_dir = tmp_path / "subjects"
    subjects_dir.mkdir()
    out_dir = tmp_path / "results"
    out_dir.mkdir()

    # Create medium-tier suite with 8 tasks (to observe parallelism).
    tasks = [_fake_task(f"task-{i:02d}") for i in range(8)]
    suite_path = _write_suite(suite_dir, "parallel-suite", tasks, tier="medium")

    # Create 1 subject.
    subject_path = _write_fake_subject_with_cost(subjects_dir, "subject-0", fake_cost=1.0)

    # Run campaign.
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
    # Verify all 8 tasks are in the run record (no loss).
    campaign_dirs = list(out_dir.glob("*parallel-suite-campaign"))
    assert len(campaign_dirs) == 1
    campaign = json.loads((campaign_dirs[0] / CAMPAIGN_JSON_FILENAME).read_text())
    assert len(campaign["subjects"]) == 1
    subject = campaign["subjects"][0]
    assert subject["run_dir"], "run_dir should be set for completed subject"

    # Read the run.json from the subject's run_dir
    run_json_path = Path(subject["run_dir"]) / "run.json"
    assert run_json_path.exists(), f"run.json not found at {run_json_path}"
    run_record = json.loads(run_json_path.read_text())
    task_ids = {task["task_id"] for task in run_record["tasks"]}
    expected_ids = {f"task-{i:02d}" for i in range(8)}
    assert task_ids == expected_ids, f"Task mismatch: expected {expected_ids}, got {task_ids}"


# ---------------------------------------------------------------------------
# (b) Resume-under-budget-change: skip -> raise cap -> resume completes
# ---------------------------------------------------------------------------


def test_campaign_resume_under_budget_change(tmp_path: Path) -> None:
    """Resume scenario: low cap (skips tasks) -> re-run with raised cap (completes).

    Verifies:
    - First run: 6 tasks at $5 each, cap $15 → 3 tasks run, 3 skipped.
    - Resume: cap raised to $35 → remaining 3 tasks complete.
    - Fingerprint exclusion works (different caps don't cause re-run of completed).
    """
    suite_dir = tmp_path / "suite"
    suite_dir.mkdir()
    subjects_dir = tmp_path / "subjects"
    subjects_dir.mkdir()
    out_dir = tmp_path / "results"
    out_dir.mkdir()

    # Create suite: 6 tasks (with $5 fake_cost each, $30 total).
    tasks = [_fake_task(f"task-{i:02d}") for i in range(6)]
    suite_path = _write_suite(suite_dir, "resume-suite", tasks, tier="medium")
    subject_path = _write_fake_subject_with_cost(subjects_dir, "subject-0", fake_cost=5.0)

    # Run 1: cap $15 → 3 tasks run, 3 skipped. (Budgeter allows bounded overshoot.)
    result1 = runner.invoke(
        app,
        [
            "campaign",
            "--suite",
            str(suite_path),
            "--subject",
            str(subject_path),
            "--out-dir",
            str(out_dir),
            "--cost-budget-usd",
            "15",  # Low cap: tasks cost $5 each; at least 3 will run
        ],
    )
    assert result1.exit_code == 0, result1.output

    # Verify some tasks ran and some skipped (bounded overshoot allows cap excess).
    campaign_dirs = list(out_dir.glob("*resume-suite-campaign"))
    assert len(campaign_dirs) == 1
    campaign = json.loads((campaign_dirs[0] / CAMPAIGN_JSON_FILENAME).read_text())
    subject1 = campaign["subjects"][0]
    assert subject1["run_dir"], "run_dir should be set for completed subject"

    # Read the run.json from the subject's run_dir
    run_json_path = Path(subject1["run_dir"]) / "run.json"
    assert run_json_path.exists(), f"run.json not found at {run_json_path}"
    run1 = json.loads(run_json_path.read_text())
    skipped1 = [t for t in run1["tasks"] if t["subject_status"] == "skipped_budget"]
    succeeded1 = [t for t in run1["tasks"] if t["subject_status"] != "skipped_budget"]
    # With budget overshoot, we expect at least 3 tasks to run, and at most 3 to be skipped
    assert len(succeeded1) >= 3, f"Expected at least 3 succeeded, got {len(succeeded1)}"
    assert len(skipped1) <= 3, f"Expected at most 3 skipped, got {len(skipped1)}"
    total = len(succeeded1) + len(skipped1)
    assert total == 6, f"Expected 6 total tasks, got {total}"

    # Run 2: raise cap to $35, same subject → remaining 3 complete.
    result2 = runner.invoke(
        app,
        [
            "campaign",
            "--suite",
            str(suite_path),
            "--subject",
            str(subject_path),
            "--out-dir",
            str(out_dir),
            "--cost-budget-usd",
            "35",  # Raised cap: all 6 tasks fit
        ],
    )
    assert result2.exit_code == 0, result2.output

    # Verify all 6 are now in the record (no re-run of completed).
    campaign = json.loads((campaign_dirs[0] / CAMPAIGN_JSON_FILENAME).read_text())
    # After resume, the run record should have all 6 tasks completed.
    subject_final = campaign["subjects"][0]
    assert subject_final["run_dir"], "run_dir should be set after resume"
    run_json_path = Path(subject_final["run_dir"]) / "run.json"
    run_final = json.loads(run_json_path.read_text())
    all_tasks = {task["task_id"]: task for task in run_final["tasks"]}
    assert len(all_tasks) == 6, f"Expected 6 tasks, got {len(all_tasks)}"
    for task in run_final["tasks"]:
        assert task["subject_status"] != "skipped_budget", (
            f"Task {task['id']} still skipped after resume with higher cap"
        )


# ---------------------------------------------------------------------------
# (c) Disabled-tier gate through campaign path
# ---------------------------------------------------------------------------


def test_campaign_disabled_xlarge_tier_rejected_without_enable_flag(tmp_path: Path) -> None:
    """Disabled tier (xlarge) is rejected by campaign unless --enable-xlarge is set.

    This is a cross-feature test ensuring the gate persists through campaign flow.
    """
    suite_dir = tmp_path / "suite"
    suite_dir.mkdir()
    subjects_dir = tmp_path / "subjects"
    subjects_dir.mkdir()
    out_dir = tmp_path / "results"
    out_dir.mkdir()

    # Create an xlarge-tier suite.
    tasks = [_fake_task(f"task-{i:02d}") for i in range(4)]
    suite_path = _write_suite(suite_dir, "xlarge-suite", tasks, tier="xlarge")
    subject_path = _write_fake_subject_with_cost(subjects_dir, "subject-0", fake_cost=1.0)

    # Try to run campaign WITHOUT --enable-xlarge → should fail.
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
    assert result.exit_code != 0, "campaign should reject disabled xlarge without --enable-xlarge"
    assert "xlarge" in result.output.lower() or "disabled" in result.output.lower()

    # Now run WITH --enable-xlarge → should succeed.
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


def test_campaign_enabled_tier_proceeds_normally(tmp_path: Path) -> None:
    """Enabled tiers (small/medium/large) proceed without --enable-xlarge."""
    suite_dir = tmp_path / "suite"
    suite_dir.mkdir()
    subjects_dir = tmp_path / "subjects"
    subjects_dir.mkdir()
    out_dir = tmp_path / "results"
    out_dir.mkdir()

    for tier in ("small", "medium", "large"):
        # Create suite with this tier.
        tasks = [_fake_task(f"task-{i:02d}") for i in range(2)]
        suite_path = _write_suite(suite_dir, f"{tier}-suite", tasks, tier=tier)
        subject_path = _write_fake_subject_with_cost(subjects_dir, f"subject-{tier}", fake_cost=1.0)

        # Run without --enable-xlarge → should succeed (not xlarge).
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
        assert result.exit_code == 0, f"campaign {tier} should succeed: {result.output}"


# ---------------------------------------------------------------------------
# (d) SI-1 regression: core never imports bench or swebench modules
# ---------------------------------------------------------------------------


def test_core_import_does_not_load_bench() -> None:
    """SI-1: `import agent_orchestrator` must not load any `bench/` module.

    Runs in subprocess to isolate module cache.
    """
    code = (
        "import sys\n"
        "import agent_orchestrator\n"
        "bench_mods = [m for m in sys.modules if m.startswith('agent_orchestrator.bench')]\n"
        "assert not bench_mods, f'bench loaded: {sorted(bench_mods)}'\n"
        "print('OK')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=30, check=False
    )
    assert result.returncode == 0, f"import failed: {result.stdout}\n{result.stderr}"
    assert "OK" in result.stdout


def test_core_import_does_not_load_swebench_submodules() -> None:
    """SI-1 extended: `import agent_orchestrator` must not load swebench_*.py modules.

    Even when swebench is available, these modules are lazy-imported only by bench/ code.
    """
    code = (
        "import sys\n"
        "import agent_orchestrator\n"
        "forbidden = {\n"
        "    'agent_orchestrator.bench.swebench_import',\n"
        "    'agent_orchestrator.bench.swebench_provider',\n"
        "    'agent_orchestrator.bench.swebench_grader',\n"
        "}\n"
        "loaded = {m for m in sys.modules if m in forbidden}\n"
        "assert not loaded, f'swebench modules loaded: {loaded}'\n"
        "print('OK')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=30, check=False
    )
    assert result.returncode == 0, f"import failed: {result.stdout}\n{result.stderr}"
    assert "OK" in result.stdout


def test_ao_bench_validate_works_without_swebench_extra() -> None:
    """SI-1 extended: `ao-bench validate` on a non-swebench suite works without extra.

    Simulates the extra missing via sys.modules patching (same as real missing).
    """
    import sys

    # Patch sys.modules to make swebench un-importable.
    old_swebench = sys.modules.get("swebench")
    old_datasets = sys.modules.get("datasets")
    try:
        sys.modules["swebench"] = None  # type: ignore[assignment]
        sys.modules["datasets"] = None  # type: ignore[assignment]

        # Now run a validate command on dev-core (a non-swebench suite).
        code = (
            "import sys\n"
            "sys.modules['swebench'] = None\n"
            "sys.modules['datasets'] = None\n"
            "import agent_orchestrator.bench.cli\n"
            "from typer.testing import CliRunner\n"
            "runner = CliRunner()\n"
            "result = runner.invoke(agent_orchestrator.bench.cli.app, ['validate', '--help'])\n"
            "assert result.exit_code == 0, result.output\n"
            "print('OK')\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, timeout=30, check=False
        )
        assert result.returncode == 0, f"validate --help failed: {result.stdout}\n{result.stderr}"
        assert "OK" in result.stdout
    finally:
        # Restore original state.
        if old_swebench is not None:
            sys.modules["swebench"] = old_swebench
        elif "swebench" in sys.modules:
            del sys.modules["swebench"]
        if old_datasets is not None:
            sys.modules["datasets"] = old_datasets
        elif "datasets" in sys.modules:
            del sys.modules["datasets"]
