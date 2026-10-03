"""Usage rollup joins (E-Us9Kd4 T-Jn4Gh5): feedback, reviewer-vs-user candidates, survival,
coverage, flags, and the shared `build_usage_report` / `usage_report_payload`."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from agent_orchestrator import usage as usage_mod
from agent_orchestrator.artifacts import LocalFsArtifactStore
from agent_orchestrator.feedback import FeedbackEntry, add_feedback
from agent_orchestrator.implicit_signals import RunSignals, apply_survival
from agent_orchestrator.models import RunState, TaskRunState
from agent_orchestrator.survival import RunSurvival, SurvivalReport, TaskSurvival
from agent_orchestrator.usage import (
    MAX_REPORT_RUN_IDS,
    MIN_SAMPLE_FOR_FLAG,
    aggregate_usage,
    build_usage_report,
    usage_report_payload,
)

MODEL = "claude-sonnet-5-5"


def _ts(**kw: Any) -> TaskRunState:
    base: dict[str, Any] = dict(
        status="succeeded", agent="developer", model=MODEL, effort="medium", attempts=1,
        dispatch_cycle=1, cumulative_cost_usd=0.1,
    )  # fmt: skip
    base.update(kw)
    return TaskRunState(**base)


def _state(run_id: str, tasks: dict[str, TaskRunState]) -> RunState:
    return RunState(
        run_id=run_id, workflow_id="w", repo_set="rs", started_at="t", updated_at="t", tasks=tasks
    )


def _entry(rating: Any, task: str | None = None, reasons=()) -> FeedbackEntry:
    return FeedbackEntry(
        ts="2026-10-02T00:00:00+00:00",
        scope="task" if task else "run",
        task_id=task,
        rating=rating,
        reasons=list(reasons),
        source="cli",
    )


def _write_state(ws: Path, state: RunState) -> None:
    d = ws / ".orchestrator" / "runs" / state.run_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "state.json").write_text(state.model_dump_json())


def _reviewed_state(ws: Path, run_id: str, verdicts: list[str]) -> RunState:
    """One producer `p<i>` (developer) + reviewer `r<i>` (reviewer) per verdict string."""
    tasks: dict[str, TaskRunState] = {}
    for i, v in enumerate(verdicts):
        tasks[f"p{i}"] = _ts()
        sidecar = f"{run_id}/r{i}/review-verdict.json"
        (ws / sidecar).parent.mkdir(parents=True, exist_ok=True)
        (ws / sidecar).write_text(json.dumps({"verdict": v}))
        tasks[f"r{i}"] = _ts(
            agent="reviewer", upstream_producers=[f"p{i}"], review_verdict_path=sidecar
        )
    return _state(run_id, tasks)


def _group(report, agent: str):
    return next(g for g in report.groups if g.agent == agent)


class TestFeedbackJoin:
    def test_old_signature_and_defaults_still_work(self, tmp_path: Path) -> None:
        rep = aggregate_usage([_state("r", {"a": _ts()})], LocalFsArtifactStore(str(tmp_path)))
        g = rep.groups[0]
        assert (g.fb_rated_tasks, g.lines_added, g.verdict_rated_pairs) == (0, 0, 0)
        assert g.survival_rate is None and g.fb_bad_rate is None
        assert rep.runs_rated == 0 and rep.survival_available is False

    def test_effective_is_task_entry_else_run_entry(self, tmp_path: Path) -> None:
        state = _state("r", {"a": _ts(), "b": _ts(), "c": _ts()})
        fb = {
            "r": [
                _entry("bad"),  # run-level fallback
                _entry("good", "a", reasons=["unnecessary"]),  # task entry wins for a
                _entry("ok", "b"),
                _entry("bad", "b"),  # later task entry supersedes (history kept)
            ]
        }
        rep = aggregate_usage([state], LocalFsArtifactStore(str(tmp_path)), feedback=fb)
        g = rep.groups[0]
        # a=good(unnecessary), b=bad, c=bad (run fallback)
        assert (g.fb_good, g.fb_ok, g.fb_bad) == (1, 0, 2)
        assert g.fb_unnecessary == 1 and g.fb_rated_tasks == 3
        assert rep.runs_rated == 1

    def test_unrated_runs_and_coverage(self, tmp_path: Path) -> None:
        states = [_state("r1", {"a": _ts()}), _state("r2", {"a": _ts()})]
        rep = aggregate_usage(
            states, LocalFsArtifactStore(str(tmp_path)), feedback={"r1": [_entry("good")], "r2": []}
        )
        assert (rep.runs_rated, rep.runs_scanned) == (1, 2)
        assert rep.groups[0].fb_rated_tasks == 1


class TestReviewerDisagreement:
    def test_explicit_task_ratings_only(self, tmp_path: Path) -> None:
        state = _reviewed_state(tmp_path, "r", ["PASS", "PASS", "FAIL", "FAIL", "PASS"])
        fb = {
            "r": [
                _entry("bad", "p0"),  # PASS but bad -> false pass
                _entry("good", "p1"),  # PASS and good -> agree
                _entry("good", "p2"),  # FAIL but good -> false fail
                _entry("bad", "p3"),  # FAIL and bad -> agree
                # p4: no explicit rating
            ]
        }
        rep = aggregate_usage([state], LocalFsArtifactStore(str(tmp_path)), feedback=fb)
        g = _group(rep, "developer")
        assert g.verdict_rated_pairs == 4
        assert (g.false_pass_candidates, g.false_fail_candidates) == (1, 1)
        assert g.reviewer_disagreement_rate == 0.5

    def test_run_level_bad_creates_no_candidates(self, tmp_path: Path) -> None:
        state = _reviewed_state(tmp_path, "r", ["PASS"] * 6)
        rep = aggregate_usage(
            [state], LocalFsArtifactStore(str(tmp_path)), feedback={"r": [_entry("bad")]}
        )
        g = _group(rep, "developer")
        assert g.fb_bad == 6  # effective ratings do count run-level...
        assert g.false_pass_candidates == 0 and g.verdict_rated_pairs == 0  # ...candidates don't
        assert not any("reviewer disagrees" in f for f in g.flags)

    def test_last_verdict_per_pair_counts_once(self, tmp_path: Path) -> None:
        # One reviewer task (one TaskRunState) has exactly one sidecar read, however many
        # times it was retried: the pair counts once.
        state = _reviewed_state(tmp_path, "r", ["PASS"])
        state.tasks["r0"].attempts = 3
        state.tasks["r0"].dispatch_cycle = 2
        rep = aggregate_usage(
            [state], LocalFsArtifactStore(str(tmp_path)), feedback={"r": [_entry("bad", "p0")]}
        )
        g = _group(rep, "developer")
        assert (g.verdict_rated_pairs, g.false_pass_candidates) == (1, 1)


class TestFlagsSampleFloor:
    def _flags(self, tmp_path: Path, n: int, bad: int) -> list[str]:
        state = _state("r", {f"t{i}": _ts() for i in range(n)})
        fb = {"r": [_entry("bad" if i < bad else "good", f"t{i}") for i in range(n)]}
        rep = aggregate_usage([state], LocalFsArtifactStore(str(tmp_path)), feedback=fb)
        return rep.groups[0].flags

    def test_user_bad_flag_needs_floor_and_share(self, tmp_path: Path) -> None:
        assert self._flags(tmp_path, MIN_SAMPLE_FOR_FLAG - 1, MIN_SAMPLE_FOR_FLAG - 1) == []
        assert self._flags(tmp_path, MIN_SAMPLE_FOR_FLAG, 1) == []  # enough samples, low share
        flags = self._flags(tmp_path, MIN_SAMPLE_FOR_FLAG, 3)
        assert len(flags) == 1 and flags[0].startswith("user-rated bad: 3/5")

    def test_disagreement_flag(self, tmp_path: Path) -> None:
        n = MIN_SAMPLE_FOR_FLAG
        state = _reviewed_state(tmp_path, "r", ["PASS"] * n)
        few = [_entry("bad", f"p{i}") for i in range(n - 1)]
        rep = aggregate_usage([state], LocalFsArtifactStore(str(tmp_path)), feedback={"r": few})
        assert not any("disagrees" in f for f in _group(rep, "developer").flags)
        rep = aggregate_usage(
            [state],
            LocalFsArtifactStore(str(tmp_path)),
            feedback={"r": few + [_entry("bad", f"p{n - 1}")]},
        )
        assert any("reviewer disagrees" in f for f in _group(rep, "developer").flags)


def _row(task: str, added: int, kept: int, *, attribution="isolation", confidence="high", **kw):
    return TaskSurvival(
        run_id="r", task_id=task, attribution=attribution, confidence=confidence,
        lines_added=added, lines_survived=kept, **kw,
    )  # fmt: skip


class TestSurvivalJoin:
    def test_joins_only_reliable_per_task_rows(self, tmp_path: Path) -> None:
        state = _state("r", {t: _ts() for t in "abcdefg"})
        surv = {
            ("r", "a"): _row("a", 10, 10),
            ("r", "b"): _row("b", 20, 10, attribution="serial", confidence="medium"),
            ("r", "c"): _row("c", 50, 0, attribution="time-window", confidence="low"),
            ("r", "d"): _row("d", 50, 0, confidence="low"),  # low confidence excluded
            ("r", "e"): _row("e", 50, 0, attribution="ambiguous"),
            ("r", "f"): _row("f", 0, 0),  # no countable lines
            ("r", "g"): _row("g", 9, 0, unavailable="git broke"),
        }
        rep = aggregate_usage([state], LocalFsArtifactStore(str(tmp_path)), survival=surv)
        g = rep.groups[0]
        assert (g.lines_added, g.lines_survived, g.survival_tasks) == (30, 20, 2)
        assert g.survival_rate == pytest.approx(2 / 3)
        assert rep.survival_available is True

    def test_low_survival_flag_floor(self, tmp_path: Path) -> None:
        n = MIN_SAMPLE_FOR_FLAG
        state = _state("r", {f"t{i}": _ts() for i in range(n)})
        worthless = {
            ("r", f"t{i}"): _row(f"t{i}", 20, 0, flags=["likely_worthless"]) for i in range(n)
        }
        rep = aggregate_usage([state], LocalFsArtifactStore(str(tmp_path)), survival=worthless)
        assert any(f.startswith("low survival: 5/5") for f in rep.groups[0].flags)
        fewer = {k: v for k, v in list(worthless.items())[: n - 1]}
        rep = aggregate_usage([state], LocalFsArtifactStore(str(tmp_path)), survival=fewer)
        assert not any("low survival" in f for f in rep.groups[0].flags)

    def test_reverted_adapter(self) -> None:
        sig = RunSignals(run_status="succeeded")
        total = TaskSurvival(run_id="r", flags=["reverted"])
        rs = RunSurvival(run_id="r", total=total, tasks=[_row("a", 5, 5, flags=["reverted"])])
        assert apply_survival(sig, rs).reverted_commits == 1
        clean = RunSurvival(run_id="r", total=TaskSurvival(run_id="r"))
        assert apply_survival(RunSignals(run_status="x"), clean).reverted_commits == 0
        gone = RunSurvival(run_id="r", total=TaskSurvival(run_id="r", unavailable="x"))
        assert apply_survival(RunSignals(run_status="x"), gone).reverted_commits is None


class TestBuilder:
    def test_skips_corrupt_run_and_corrupt_feedback(self, tmp_path: Path) -> None:
        _write_state(tmp_path, _state("good", {"a": _ts()}))
        _write_state(tmp_path, _state("badfb", {"a": _ts()}))
        bad_run = tmp_path / ".orchestrator" / "runs" / "corrupt"
        bad_run.mkdir(parents=True)
        (bad_run / "state.json").write_text("{not json")
        add_feedback(str(tmp_path), "good", scope="run", rating="good")
        (tmp_path / ".orchestrator" / "runs" / "badfb" / "feedback.json").write_text("{nope")

        rep = build_usage_report(str(tmp_path))
        assert rep.runs_scanned == 2  # corrupt run skipped
        assert len(rep.skipped) == 1 and rep.skipped[0].startswith("corrupt:")
        assert rep.feedback_errors == 1  # corrupt feedback skipped + counted, run still scanned
        assert rep.runs_rated == 1 and rep.groups[0].fb_good == 1
        assert rep.survival_available is False and rep.run_signals == []

    def test_explicit_ids_validated_and_missing_skipped(self, tmp_path: Path) -> None:
        _write_state(tmp_path, _state("r1", {"a": _ts()}))
        rep = build_usage_report(str(tmp_path), ["r1", "../etc", "ghost"])
        assert rep.runs_scanned == 1 and len(rep.skipped) == 2

    def test_run_id_cap(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="too many run ids"):
            build_usage_report(str(tmp_path), ["x"] * (MAX_REPORT_RUN_IDS + 1))

    def test_survival_unavailable_degrades(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_state(tmp_path, _state("r1", {"a": _ts()}))
        monkeypatch.setattr(
            usage_mod,
            "compute_survival",
            lambda *a, **k: SurvivalReport(unavailable="git is not available"),
        )
        rep = build_usage_report(str(tmp_path), with_survival=True)
        assert rep.survival_available is False
        assert rep.survival_unavailable_reason == "git is not available"
        assert rep.groups[0].tasks == 1  # rest of the report intact

        def boom(*a, **k):
            raise RuntimeError("kaput")

        monkeypatch.setattr(usage_mod, "compute_survival", boom)
        rep = build_usage_report(str(tmp_path), with_survival=True)
        assert rep.survival_available is False and "kaput" in (
            rep.survival_unavailable_reason or ""
        )

    def test_invalid_ref_degrades(self, tmp_path: Path) -> None:
        _write_state(tmp_path, _state("r1", {"a": _ts()}))
        rep = build_usage_report(str(tmp_path), with_survival=True, ref="--evil")
        assert rep.survival_available is False
        assert "invalid ref" in (rep.survival_unavailable_reason or "")

    def test_payload_has_computed_rates(self, tmp_path: Path) -> None:
        _write_state(tmp_path, _state("r1", {"a": _ts(), "b": _ts(attempts=2)}))
        add_feedback(str(tmp_path), "r1", scope="task", task_id="a", rating="bad")
        payload = usage_report_payload(build_usage_report(str(tmp_path)))
        row = payload["groups"][0]
        assert (
            row["retry_rate"] == 0.5 and row["fb_bad_rate"] == 1.0
        )  # 1 bad of 1 rated (b unrated)
        assert row["mean_cost_usd"] == pytest.approx(0.1)
        assert row["survival_rate"] is None and row["review_fail_rate"] is None
        assert payload["runs_rated"] == 1 and payload["survival_available"] is False
        json.dumps(payload)  # serialisable
