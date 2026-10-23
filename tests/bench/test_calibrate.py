"""`ao-bench calibrate` (HLD §7): pooled pass rate per task vs the discriminating band.

CliRunner e2e over fake anchors (real `run_suite` runs, scripted per-(subject, task)
outcomes -- see `stats_support`) plus unit tests of the band/suggestion rules. Fake-tier:
no LLM/network; every result dir is under a `tmp_path`.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from agent_orchestrator.bench import calibrate as cal
from agent_orchestrator.bench.calibrate import calibrate_suite
from agent_orchestrator.bench.cli import app
from agent_orchestrator.bench.errors import BenchError

from .stats_support import (
    fixed_clock,
    install_scripted_subject,
    solvable_task,
    uniq,
    write_subject,
    write_suite,
)

cli = CliRunner()

SOLVE = "copy-solution"
FAIL = "noop"
ANCHOR_A = "anchor-a"
ANCHOR_B = "anchor-b"


def _tasks() -> list[dict[str, Any]]:
    """Five tasks spanning every verdict. `difficulty` marks the Tier-1 (generated) ones."""
    easy = solvable_task("t-easy", family="crud")
    easy["difficulty"] = {"entities": 1, "traps": 1}
    hard = solvable_task("t-hard", family="queue")
    hard["difficulty"] = {"entities": 3, "traps": 3}
    return [
        easy,
        hard,
        solvable_task("t-hard-fixed"),  # no difficulty vector: a Tier-2-style fixed task
        solvable_task("t-mid", family="crud"),
        solvable_task("t-infra", family="queue"),
    ]


def _plan() -> dict[tuple[str, str], list[str]]:
    plan: dict[tuple[str, str], list[str]] = {}
    for anchor in (ANCHOR_A, ANCHOR_B):
        plan[(anchor, "t-easy")] = [SOLVE]  # 4/4 -> above
        plan[(anchor, "t-hard")] = [FAIL]  # 0/4 -> below
        plan[(anchor, "t-hard-fixed")] = [FAIL]  # 0/4 -> below, no vector -> retire
        plan[(anchor, "t-infra")] = ["error"]  # every run an infra error -> no data
    plan[(ANCHOR_A, "t-mid")] = [SOLVE]  # a always, b never -> 2/4 -> in band
    plan[(ANCHOR_B, "t-mid")] = [FAIL]
    return plan


def _setup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tasks: list[dict[str, Any]] | None = None
) -> tuple[Path, list[Path], Path, dict[tuple[str, str], int]]:
    suite = write_suite(tmp_path, f"cal-{uniq()}", tasks if tasks is not None else _tasks())
    subjects = [write_subject(tmp_path, a) for a in (ANCHOR_A, ANCHOR_B)]
    counters = install_scripted_subject(monkeypatch, _plan())
    return suite, subjects, tmp_path / "results", counters


def _invoke(suite: Path, subjects: list[Path], out: Path, *extra: str) -> Any:
    args = ["calibrate", "--suite", str(suite), "--out-dir", str(out)]
    for s in subjects:
        args += ["--subject", str(s)]
    return cli.invoke(app, args + list(extra))


def _calibration(suite: Path) -> dict[str, dict[str, Any] | None]:
    data = json.loads(suite.read_text())
    return {t["id"]: t.get("calibration") for t in data["tasks"]}


# ---------------------------------------------------------------------------
# rules: band verdicts, suggestions, band parsing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("rate", "verdict"),
    [
        (None, cal.VERDICT_NO_DATA),
        (0.0, cal.VERDICT_BELOW),
        (0.1999, cal.VERDICT_BELOW),
        (0.2, cal.VERDICT_IN),  # the band is inclusive at both ends
        (0.5, cal.VERDICT_IN),
        (0.8, cal.VERDICT_IN),
        (0.8001, cal.VERDICT_ABOVE),
        (1.0, cal.VERDICT_ABOVE),
    ],
)
def test_verdict_for_band_edges(rate: float | None, verdict: str) -> None:
    assert cal.verdict_for(rate, (0.2, 0.8)) == verdict


@pytest.mark.parametrize(
    ("verdict", "vector", "suggestion"),
    [
        (cal.VERDICT_IN, True, cal.SUGGEST_KEEP),
        (cal.VERDICT_IN, False, cal.SUGGEST_KEEP),
        (cal.VERDICT_ABOVE, True, cal.SUGGEST_RAISE),
        (cal.VERDICT_BELOW, True, cal.SUGGEST_LOWER),
        (cal.VERDICT_ABOVE, False, cal.SUGGEST_RETIRE),
        (cal.VERDICT_BELOW, False, cal.SUGGEST_RETIRE),
        (cal.VERDICT_NO_DATA, True, cal.SUGGEST_RERUN),
    ],
)
def test_suggestion_rules(verdict: str, vector: bool, suggestion: str) -> None:
    assert cal.suggestion_for(verdict, has_difficulty_vector=vector) == suggestion


def test_parse_band() -> None:
    assert cal.parse_band("0.2,0.8") == (0.2, 0.8)
    assert cal.parse_band(" 0 , 1 ") == (0.0, 1.0)
    for bad in ("0.8,0.2", "0.5,0.5", "-0.1,0.5", "0.1,1.5", "0.2", "a,b", "0.1,0.5,0.9", ""):
        with pytest.raises(ValueError, match="--band"):
            cal.parse_band(bad)


# ---------------------------------------------------------------------------
# calibrate_suite (library entry point)
# ---------------------------------------------------------------------------


def test_calibrate_suite_pools_subjects_times_repeats(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    suite, subjects, out, counters = _setup(tmp_path, monkeypatch)

    report = calibrate_suite(suite, subjects, repeats=2, out_dir=out, clock=fixed_clock)

    by_id = {r.task_id: r for r in report.tasks}
    assert report.date == "2026-10-23"
    assert report.subjects == [ANCHOR_A, ANCHOR_B] and report.repeats == 2
    assert report.band == (0.2, 0.8)
    assert [r.task_id for r in report.tasks] == sorted(by_id)

    easy, hard, fixed, mid, infra = (
        by_id[t] for t in ("t-easy", "t-hard", "t-hard-fixed", "t-mid", "t-infra")
    )
    assert (easy.attempts, easy.passes, easy.pooled_pass_rate) == (4, 4, 1.0)
    assert (easy.verdict, easy.suggestion) == ("above", "raise difficulty")
    assert (hard.pooled_pass_rate, hard.verdict, hard.suggestion) == (
        0.0,
        "below",
        "lower difficulty",
    )
    assert (fixed.verdict, fixed.suggestion) == ("below", "retire")
    assert (mid.passes, mid.attempts, mid.pooled_pass_rate) == (2, 4, 0.5)
    assert (mid.verdict, mid.suggestion) == ("in", "keep")
    # Every run of t-infra errored: nothing attempted -> no pooled rate, nothing to judge.
    assert (infra.attempts, infra.excluded, infra.pooled_pass_rate) == (0, 4, None)
    assert (infra.verdict, infra.suggestion) == ("no_data", cal.SUGGEST_RERUN)
    assert easy.family == "crud"

    # 2 subjects x 2 repeats = 4 runs of every task; repeats land in their own dirs.
    assert all(c == 2 for c in counters.values())
    assert len(report.run_dirs) == 4
    assert [Path(d).name.endswith("-r2") for d in report.run_dirs] == [False, True, False, True]
    assert all((Path(d) / "run.json").exists() for d in report.run_dirs)


def test_infra_errors_are_excluded_from_the_pooled_rate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    suite = write_suite(tmp_path, f"cal-{uniq()}", [solvable_task("t1")])
    subject = write_subject(tmp_path, ANCHOR_A)
    # 4 repeats of one anchor: solved, error, error, unsolved -> pooled over the 2 attempted.
    install_scripted_subject(monkeypatch, {(ANCHOR_A, "t1"): [SOLVE, "error", "error", FAIL]})

    report = calibrate_suite(
        suite, [subject], repeats=4, out_dir=tmp_path / "out", clock=fixed_clock
    )

    (only,) = report.tasks
    assert (only.attempts, only.passes, only.excluded) == (2, 1, 2)
    assert only.pooled_pass_rate == 0.5


def test_calibrate_suite_skips_disabled_tasks_and_honours_task_filter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tasks = _tasks()
    tasks[0]["enabled"] = False  # t-easy retired
    suite, subjects, out, counters = _setup(tmp_path, monkeypatch, tasks)

    report = calibrate_suite(
        suite, subjects, repeats=1, out_dir=out, tasks=["t-mid", "t-hard"], clock=fixed_clock
    )

    assert [r.task_id for r in report.tasks] == ["t-hard", "t-mid"]
    assert not any(tid == "t-easy" for _, tid in counters)
    assert not any(tid == "t-infra" for _, tid in counters)


def test_calibrate_suite_usage_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    suite, subjects, out, _ = _setup(tmp_path, monkeypatch)
    with pytest.raises(BenchError, match="at least one --subject"):
        calibrate_suite(suite, [], out_dir=out)
    with pytest.raises(BenchError, match="--repeats must be >= 1"):
        calibrate_suite(suite, subjects, repeats=0, out_dir=out)
    with pytest.raises(BenchError, match="--task not in suite.*nope"):
        calibrate_suite(suite, subjects, tasks=["t-mid", "nope"], out_dir=out)
    assert not out.exists()  # nothing was run for any of them


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_calibrate_prints_table_and_exits_zero_with_out_of_band_tasks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    suite, subjects, out, _ = _setup(tmp_path, monkeypatch)
    before = suite.read_text()

    result = _invoke(suite, subjects, out, "--repeats", "2", "--band", "0.2,0.8")

    assert result.exit_code == 0, result.output  # out-of-band tasks are a report, not a failure
    text = result.output
    assert "Calibration: suite=cal-" in text and "band=[0.20, 0.80]" in text
    rows = {line.split()[0]: line for line in text.splitlines() if line.startswith("t-")}
    assert "above" in rows["t-easy"] and "raise difficulty" in rows["t-easy"]
    assert "below" in rows["t-hard"] and "lower difficulty" in rows["t-hard"]
    assert "retire" in rows["t-hard-fixed"]
    assert "4/4" in rows["t-easy"] and "1.00" in rows["t-easy"]
    assert " in " in rows["t-mid"] and "keep" in rows["t-mid"] and "2/4" in rows["t-mid"]
    assert "no_data" in rows["t-infra"] and "4 infra/skipped run(s) excluded" in rows["t-infra"]
    assert "Summary: 1 in band, 1 above, 2 below, 1 no data." in text
    # Dry run: the suite file is untouched.
    assert "Dry run: pass --write" in text
    assert suite.read_text() == before


def test_cli_calibrate_write_stores_records_in_suite_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    suite, subjects, out, _ = _setup(tmp_path, monkeypatch)
    original = json.loads(suite.read_text())
    # A previous record on the no-data task must survive (never clobbered by "no data").
    previous = {
        "date": "2026-01-01",
        "subjects": ["old"],
        "repeats": 1,
        "pooled_pass_rate": 0.4,
        "verdict": "in",
    }
    for t in original["tasks"]:
        if t["id"] == "t-infra":
            t["calibration"] = previous
    suite.write_text(json.dumps(original, indent=2))

    result = _invoke(suite, subjects, out, "--repeats", "2", "--write")

    assert result.exit_code == 0, result.output
    assert "Wrote calibration records for 4 task(s)" in result.output
    records = _calibration(suite)
    for task_id, rate, verdict in (
        ("t-easy", 1.0, "above"),
        ("t-hard", 0.0, "below"),
        ("t-hard-fixed", 0.0, "below"),
        ("t-mid", 0.5, "in"),
    ):
        rec = records[task_id]
        assert rec is not None
        assert set(rec) == {"date", "subjects", "repeats", "pooled_pass_rate", "verdict"}
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", rec["date"])
        assert rec["subjects"] == [ANCHOR_A, ANCHOR_B] and rec["repeats"] == 2
        assert (rec["pooled_pass_rate"], rec["verdict"]) == (rate, verdict)
    assert records["t-infra"] == previous

    # Formatting: canonical json.dumps(indent=2), everything else byte-for-byte the data.
    text = suite.read_text()
    new = json.loads(text)
    assert text == json.dumps(new, indent=2, ensure_ascii=False) + "\n"
    for t in new["tasks"]:
        t.pop("calibration", None)
    for t in original["tasks"]:
        t.pop("calibration", None)
    assert new == original
    # And it is still a valid suite for the real loader.
    from agent_orchestrator.bench.spec import load_suite

    assert {t.id: t.calibration is not None for t in load_suite(suite).tasks}["t-easy"] is True


def test_cli_calibrate_write_is_idempotent_and_replaces_old_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    suite, subjects, out, _ = _setup(tmp_path, monkeypatch)
    assert _invoke(suite, subjects, out, "--write").exit_code == 0
    first = suite.read_text()

    assert _invoke(suite, subjects, out, "--write").exit_code == 0  # same day -> resumed runs

    assert suite.read_text() == first


def test_cli_calibrate_custom_band_changes_verdicts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    suite, subjects, out, _ = _setup(tmp_path, monkeypatch)

    result = _invoke(suite, subjects, out, "--band", "0.6,0.9", "--write")

    assert result.exit_code == 0, result.output
    assert "band=[0.60, 0.90]" in result.output
    records = _calibration(suite)
    assert records["t-mid"]["verdict"] == "below"  # 0.5 < 0.6  # type: ignore[index]
    assert records["t-easy"]["verdict"] == "above"  # type: ignore[index]


def test_cli_calibrate_task_filter_writes_only_those_tasks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    suite, subjects, out, counters = _setup(tmp_path, monkeypatch)

    result = _invoke(suite, subjects, out, "--task", "t-mid", "--repeats", "1", "--write")

    assert result.exit_code == 0, result.output
    records = _calibration(suite)
    assert records["t-mid"] is not None
    assert all(v is None for k, v in records.items() if k != "t-mid")
    assert {tid for _, tid in counters} == {"t-mid"}


def test_cli_calibrate_single_anchor_is_allowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    suite, subjects, out, _ = _setup(tmp_path, monkeypatch)

    result = _invoke(suite, subjects[:1], out, "--repeats", "2", "--write")

    assert result.exit_code == 0, result.output
    rec = _calibration(suite)["t-mid"]
    assert rec is not None and rec["subjects"] == [ANCHOR_A] and rec["pooled_pass_rate"] == 1.0


def test_cli_calibrate_help_documents_flags() -> None:
    result = cli.invoke(app, ["calibrate", "--help"])
    assert result.exit_code == 0, result.output
    for flag in ("--suite", "--subject", "--repeats", "--band", "--out-dir", "--write"):
        assert flag in result.output


@pytest.mark.parametrize("band", ["0.9,0.1", "abc", "0.5"])
def test_cli_calibrate_bad_band_is_a_usage_error_before_any_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, band: str
) -> None:
    suite, subjects, out, counters = _setup(tmp_path, monkeypatch)

    result = _invoke(suite, subjects, out, "--band", band)

    assert result.exit_code == 1
    assert "--band" in result.output
    assert counters == {} and not out.exists()


def test_cli_calibrate_repeats_zero_is_rejected_by_the_parser(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    suite, subjects, out, _ = _setup(tmp_path, monkeypatch)
    result = _invoke(suite, subjects, out, "--repeats", "0")
    assert result.exit_code == 2


def test_cli_calibrate_unknown_task_and_missing_suite_exit_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    suite, subjects, out, _ = _setup(tmp_path, monkeypatch)
    unknown = _invoke(suite, subjects, out, "--task", "nope")
    assert unknown.exit_code == 1 and "nope" in unknown.output

    missing = _invoke(tmp_path / "no-such-suite.json", subjects, out)
    assert missing.exit_code == 1 and "ERROR" in missing.output


def test_cli_calibrate_write_to_yaml_suite_fails_fast(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    suite, subjects, out, counters = _setup(tmp_path, monkeypatch)
    yaml_suite = suite.with_suffix(".yaml")
    yaml_suite.write_text(suite.read_text())  # JSON is valid YAML

    result = _invoke(yaml_suite, subjects, out, "--write")

    assert result.exit_code == 1
    assert "JSON suites only" in result.output
    assert counters == {} and not out.exists()  # refused BEFORE spending anything


def test_write_calibration_rolls_back_if_the_suite_stops_loading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    suite, subjects, out, _ = _setup(tmp_path, monkeypatch)
    report = calibrate_suite(suite, subjects, repeats=1, out_dir=out, clock=fixed_clock)
    before = suite.read_text()

    def _boom(_path: object) -> None:
        raise RuntimeError("suite no longer loads")

    monkeypatch.setattr("agent_orchestrator.bench.spec.load_suite", _boom)
    with pytest.raises(RuntimeError, match="no longer loads"):
        cal.write_calibration(suite, report)

    assert suite.read_text() == before  # the original text was restored
