"""E2E (CliRunner): `ao rate` feedback + sidecar verdicts + a tiny real git repo feed
`ao report-usage` (text and --json), incl. --with-survival and graceful degradation."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agent_orchestrator.cli import app
from agent_orchestrator.models import RunState, TaskRunState

runner = CliRunner()
RUN = "run-1"
MODEL_A = "model-a"
MODEL_B = "model-b"
pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


def _git(ws: Path, *args: str) -> str:
    env = {
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t", "PATH": __import__("os").environ["PATH"],
        "HOME": str(ws),
    }  # fmt: skip
    out = subprocess.run(
        ["git", "-C", str(ws), *args], check=True, capture_output=True, text=True, env=env
    )
    return out.stdout.strip()


def _commit_file(ws: Path, name: str, lines: list[str], msg: str) -> str:
    (ws / name).write_text("\n".join(lines) + "\n")
    _git(ws, "add", name)
    _git(ws, "commit", "-q", "-m", msg)
    return _git(ws, "rev-parse", "HEAD")


def _ts(model: str, start: str, end: str, **kw) -> TaskRunState:
    return TaskRunState(
        status="succeeded", agent="developer", model=model, effort="medium", attempts=1,
        dispatch_cycle=1, cumulative_cost_usd=0.2, started_at=start, ended_at=end, **kw,
    )  # fmt: skip


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    """Workspace that is itself a git repo: task `impl_a` (model-a) adds code a human later
    rewrites away (survival ~0); task `impl_b` (model-b) adds code that stays."""
    _git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / ".gitignore").write_text(".orchestrator/\nreview/\n")
    _git(tmp_path, "add", ".gitignore")
    _git(tmp_path, "commit", "-q", "-m", "base")
    base = _git(tmp_path, "rev-parse", "HEAD")
    a_lines = [f"alpha_value_{i} = {i}" for i in range(12)]
    b_lines = [f"beta_value_{i} = {i}" for i in range(12)]
    head_a = _commit_file(tmp_path, "a.py", a_lines, "impl a")
    head_b = _commit_file(tmp_path, "b.py", b_lines, "impl b")
    _commit_file(tmp_path, "a.py", ["replaced = True"], "human rewrite")

    (tmp_path / "review").mkdir()
    (tmp_path / "review" / "review-verdict.json").write_text(json.dumps({"verdict": "PASS"}))
    state = RunState(
        run_id=RUN, workflow_id="w", repo_set="rs", started_at="t", updated_at="t",
        git_repos={"repo": str(tmp_path)}, git_start_heads={"repo": base},
        tasks={
            "impl_a": _ts(MODEL_A, "2026-01-01T00:00:00+00:00", "2026-01-01T00:01:00+00:00",
                          end_heads={"repo": head_a}),
            "impl_b": _ts(MODEL_B, "2026-01-01T00:02:00+00:00", "2026-01-01T00:03:00+00:00",
                          end_heads={"repo": head_b}),
            "review": TaskRunState(
                status="succeeded", agent="reviewer", model=MODEL_B, effort="medium",
                dispatch_cycle=1, upstream_producers=["impl_a"],
                review_verdict_path="review/review-verdict.json",
            ),
        },
    )  # fmt: skip
    d = tmp_path / ".orchestrator" / "runs" / RUN
    d.mkdir(parents=True)
    (d / "state.json").write_text(state.model_dump_json())
    return tmp_path


def _invoke(ws: Path, *args: str):
    return runner.invoke(app, [*args, "--workspace", str(ws)])


def _rate(ws: Path, *args: str) -> None:
    res = _invoke(ws, "rate", RUN, *args)
    assert res.exit_code == 0, res.output


def test_feedback_join_text_and_json(ws: Path) -> None:
    _rate(ws, "bad", "--task", "impl_a", "--reason", "unnecessary")  # reviewer said PASS
    _rate(ws, "good")  # run-level: effective for impl_b and review only

    res = _invoke(ws, "report-usage")
    assert res.exit_code == 0, res.output
    assert "feedback: 1 of 1 runs rated | survival: off" in res.output
    assert "Fb(g/o/b)" in res.output and "Unn" in res.output and "Surv%" in res.output
    assert "Runs scanned: 1" in res.output

    res = _invoke(ws, "report-usage", "--json")
    assert res.exit_code == 0, res.output
    data = json.loads(res.output)
    assert data["runs_rated"] == 1 and data["survival_available"] is False
    by = {(g["agent"], g["model"]): g for g in data["groups"]}
    a = by[("developer", MODEL_A)]
    assert (a["fb_bad"], a["fb_unnecessary"], a["fb_rated_tasks"]) == (1, 1, 1)
    # Reviewer PASSed impl_a, user explicitly rated it bad -> candidate; run-level 'good'
    # on the other tasks never creates candidates.
    assert (a["false_pass_candidates"], a["verdict_rated_pairs"]) == (1, 1)
    b = by[("developer", MODEL_B)]
    assert b["fb_good"] == 1 and b["false_pass_candidates"] == 0
    assert a["mean_cost_usd"] == pytest.approx(0.2) and a["survival_rate"] is None


def test_with_survival_text_and_json(ws: Path) -> None:
    _rate(ws, "ok", "--task", "impl_b")
    res = _invoke(ws, "report-usage", "--with-survival")
    assert res.exit_code == 0, res.output
    assert "survival: at HEAD" in res.output
    assert "Run signals" in res.output and RUN in res.output

    res = _invoke(ws, "report-usage", "--with-survival", "--ref", "HEAD", "--json")
    assert res.exit_code == 0, res.output
    data = json.loads(res.output)
    assert data["survival_available"] is True and data["survival_ref"] == "HEAD"
    by = {g["model"]: g for g in data["groups"] if g["agent"] == "developer"}
    assert by[MODEL_A]["lines_added"] == 12 and by[MODEL_A]["lines_survived"] == 0
    assert by[MODEL_A]["survival_rate"] == 0.0
    assert by[MODEL_B]["lines_added"] == 12 and by[MODEL_B]["survival_rate"] == 1.0
    assert by[MODEL_A]["survival_tasks"] == 1
    assert data["run_signals"][0]["run_id"] == RUN
    assert data["run_signals"][0]["signals"]["reverted_commits"] == 0


def test_survival_degrades_on_bad_ref_and_flag_validation(ws: Path) -> None:
    res = _invoke(ws, "report-usage", "--with-survival", "--ref", "no-such-ref", "--json")
    assert res.exit_code == 0, res.output  # report still produced
    data = json.loads(res.output)
    assert data["survival_available"] is False and data["survival_unavailable_reason"]
    assert data["groups"] and data["groups"][0]["lines_added"] == 0

    res = _invoke(ws, "report-usage", "--with-survival")
    assert res.exit_code == 0
    assert _invoke(ws, "report-usage", "--ref", "HEAD").exit_code == 2  # needs --with-survival
    assert _invoke(ws, "report-usage", "--with-survival", "--ref", "--x").exit_code == 2


def test_corrupt_feedback_and_run_do_not_break_report(ws: Path) -> None:
    runs = ws / ".orchestrator" / "runs"
    (runs / RUN / "feedback.json").write_text("{nope")
    (runs / "broken").mkdir()
    (runs / "broken" / "state.json").write_text("{nope")
    res = _invoke(ws, "report-usage", "--json")
    assert res.exit_code == 0, res.output
    assert "skipping run broken" in res.output  # warning goes to stderr
    data = json.loads(res.stdout)
    assert data["feedback_errors"] == 1 and data["runs_rated"] == 0
    assert data["runs_scanned"] == 1 and len(data["skipped"]) == 1
