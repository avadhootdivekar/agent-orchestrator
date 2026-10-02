"""Tests for dispatch provenance + `ao report-usage` (cross-run model/effort usage analytics).

Unit tests cover `usage.py`; the e2e class drives the real CLI (`ao run` then
`ao report-usage`) with the fake executor, as CLAUDE.md asks for the outermost boundary.
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from agent_orchestrator.artifacts import LocalFsArtifactStore
from agent_orchestrator.cli import app
from agent_orchestrator.models import RunState, TaskRunState, TaskSpec
from agent_orchestrator.usage import (
    MIN_SAMPLE_FOR_FLAG,
    ReviewVerdict,
    aggregate_usage,
    dispatch_provenance,
    list_run_ids,
    read_review_verdict,
    review_verdict_path_for,
)

runner = CliRunner()
HAIKU = "claude-haiku-4-5-20251001"


def _task(tid: str, inputs=(), outputs=()) -> TaskSpec:
    return TaskSpec(
        id=tid, agent="ag", instruction="i.md", inputs=list(inputs), outputs=list(outputs)
    )


class TestProvenance:
    def test_producers_are_tasks_whose_outputs_are_consumed(self) -> None:
        impl = _task("impl", outputs=["o/dev.md"])
        test = _task("test", inputs=["o/dev.md"], outputs=["o/test.md"])
        review = _task("review", inputs=["o/dev.md", "o/test.md"], outputs=["o/t1/review.md"])
        unrelated = _task("other", outputs=["o/x.md"])
        prov = dispatch_provenance(review, HAIKU, "medium", [impl, test, review, unrelated])
        assert prov["upstream_producers"] == ["impl", "test"]
        assert prov["model"] == HAIKU and prov["effort"] == "medium" and prov["agent"] == "ag"
        assert prov["review_verdict_path"] == "o/t1/review-verdict.json"

    def test_non_review_task_has_no_verdict_path(self) -> None:
        assert review_verdict_path_for(_task("a", outputs=["o/report.md"])) is None

    def test_no_inputs_means_no_producers(self) -> None:
        assert (
            dispatch_provenance(_task("a"), None, None, [_task("b", outputs=["x"])])[
                "upstream_producers"
            ]
            == []
        )


class TestReadVerdict:
    def _store(self, tmp_path: Path) -> LocalFsArtifactStore:
        return LocalFsArtifactStore(str(tmp_path))

    def test_valid_sidecar(self, tmp_path: Path) -> None:
        (tmp_path / "v.json").write_text(
            json.dumps({"verdict": "fail", "findings": {"critical": 1, "major": 2}, "must_fix": 3})
        )
        v = read_review_verdict(self._store(tmp_path), "v.json")
        assert v == ReviewVerdict(verdict="FAIL", critical=1, major=2, minor=0, must_fix=3)

    def test_missing_malformed_or_unknown_verdict_is_none(self, tmp_path: Path) -> None:
        store = self._store(tmp_path)
        assert read_review_verdict(store, None) is None
        assert read_review_verdict(store, "absent.json") is None
        (tmp_path / "bad.json").write_text("{not json")
        assert read_review_verdict(store, "bad.json") is None
        (tmp_path / "odd.json").write_text('{"verdict": "MAYBE"}')
        assert read_review_verdict(store, "odd.json") is None

    def test_garbage_counts_default_to_zero(self, tmp_path: Path) -> None:
        (tmp_path / "v.json").write_text(
            json.dumps(
                {"verdict": "PASS", "findings": {"major": -4, "minor": "x"}, "must_fix": True}
            )
        )
        v = read_review_verdict(self._store(tmp_path), "v.json")
        assert v is not None and (v.major, v.minor, v.must_fix) == (0, 0, 0)


def _ts(agent, model, *, status="succeeded", cost=0.1, attempts=1, cycle=1, **kw) -> TaskRunState:
    return TaskRunState(
        status=status, agent=agent, model=model, effort="medium", attempts=attempts,
        dispatch_cycle=cycle, cumulative_cost_usd=cost, cumulative_input_tokens=10,
        cumulative_output_tokens=5, **kw,
    )  # fmt: skip


def _state(tasks: dict[str, TaskRunState]) -> RunState:
    return RunState(
        run_id="r", workflow_id="w", repo_set="rs", started_at="t", updated_at="t", tasks=tasks
    )


class TestAggregate:
    def test_groups_costs_and_retries(self, tmp_path: Path) -> None:
        state = _state(
            {
                "a": _ts("developer", HAIKU, cost=0.1),
                "b": _ts("developer", HAIKU, cost=0.3, attempts=2),
                "c": _ts("developer", "claude-sonnet-5-5", cost=0.6),
                "d": _ts("developer", HAIKU, status="failed", cost=0.0),
                "skipped": TaskRunState(status="skipped"),  # never dispatched -> ignored
            }
        )
        rep = aggregate_usage([state], LocalFsArtifactStore(str(tmp_path)))
        by_model = {g.model: g for g in rep.groups}
        h = by_model[HAIKU]
        assert (h.tasks, h.succeeded, h.failed, h.retried) == (3, 2, 1, 1)
        assert abs(h.cost_usd - 0.4) < 1e-9 and h.mean_cost_usd is not None
        assert by_model["claude-sonnet-5-5"].tasks == 1
        assert rep.runs_scanned == 1

    def test_unrecorded_model_buckets_as_default(self, tmp_path: Path) -> None:
        state = _state({"a": TaskRunState(status="succeeded", dispatch_cycle=1)})
        rep = aggregate_usage([state], LocalFsArtifactStore(str(tmp_path)))
        assert (rep.groups[0].agent, rep.groups[0].model) == ("(default)", "(default)")

    def test_review_verdict_attributed_to_producer_not_reviewer(self, tmp_path: Path) -> None:
        (tmp_path / "t1").mkdir()
        (tmp_path / "t1" / "review-verdict.json").write_text(
            json.dumps({"verdict": "FAIL", "findings": {"major": 2}, "must_fix": 1})
        )
        state = _state(
            {
                "impl": _ts("developer", HAIKU),
                "review": _ts(
                    "reviewer", "claude-sonnet-5-5",
                    upstream_producers=["impl", "never-ran"],
                    review_verdict_path="t1/review-verdict.json",
                ),
            }
        )  # fmt: skip
        rep = aggregate_usage([state], LocalFsArtifactStore(str(tmp_path)))
        by_agent = {g.agent: g for g in rep.groups}
        assert (by_agent["developer"].reviewed, by_agent["developer"].review_fail) == (1, 1)
        assert by_agent["developer"].major == 2 and by_agent["developer"].must_fix == 1
        assert by_agent["reviewer"].reviewed == 0
        assert (rep.reviews_seen, rep.verdicts_found) == (1, 1)

    def test_missing_sidecar_counts_review_but_not_verdict(self, tmp_path: Path) -> None:
        state = _state(
            {
                "impl": _ts("developer", HAIKU),
                "review": _ts("reviewer", "m", upstream_producers=["impl"],
                              review_verdict_path="nope/review-verdict.json"),
            }
        )  # fmt: skip
        rep = aggregate_usage([state], LocalFsArtifactStore(str(tmp_path)))
        assert (rep.reviews_seen, rep.verdicts_found) == (1, 0)

    def _flag_state(self, tmp_path: Path, n: int, fails: int) -> RunState:
        tasks: dict[str, TaskRunState] = {}
        for i in range(n):
            (tmp_path / f"r{i}").mkdir()
            verdict = "FAIL" if i < fails else "PASS"
            (tmp_path / f"r{i}" / "review-verdict.json").write_text(
                json.dumps({"verdict": verdict})
            )
            tasks[f"impl{i}"] = _ts("developer", HAIKU)
            tasks[f"rev{i}"] = _ts("reviewer", "m", upstream_producers=[f"impl{i}"],
                                   review_verdict_path=f"r{i}/review-verdict.json")  # fmt: skip
        return _state(tasks)

    def test_high_rework_flag_needs_sample_and_rate(self, tmp_path: Path) -> None:
        store = LocalFsArtifactStore(str(tmp_path))
        dev = lambda rep: next(g for g in rep.groups if g.agent == "developer")  # noqa: E731
        hot = aggregate_usage([self._flag_state(tmp_path, MIN_SAMPLE_FOR_FLAG, 4)], store)
        assert dev(hot).flags and "high rework" in dev(hot).flags[0]

    def test_no_flag_below_sample_floor_or_rate(self, tmp_path: Path) -> None:
        store = LocalFsArtifactStore(str(tmp_path))
        small = aggregate_usage([self._flag_state(tmp_path, MIN_SAMPLE_FOR_FLAG - 1, 4)], store)
        assert not next(g for g in small.groups if g.agent == "developer").flags

    def test_list_run_ids(self, tmp_path: Path) -> None:
        assert list_run_ids(str(tmp_path)) == []
        (tmp_path / ".orchestrator" / "runs" / "r1").mkdir(parents=True)
        (tmp_path / ".orchestrator" / "runs" / "r1" / "state.json").write_text("{}")
        (tmp_path / ".orchestrator" / "runs" / "empty").mkdir()
        assert list_run_ids(str(tmp_path)) == ["r1"]


def _write_specs(tmp_path: Path) -> None:
    (tmp_path / "specs" / "instructions").mkdir(parents=True)
    for name in ("impl", "review"):
        (tmp_path / "specs" / "instructions" / f"{name}.md").write_text(f"# {name}\n")
    # Reviewer's sidecar, pre-seeded (the fake executor only writes declared outputs).
    (tmp_path / "output" / "t1").mkdir(parents=True)
    (tmp_path / "output" / "t1" / "review-verdict.json").write_text(
        json.dumps({"verdict": "FAIL", "findings": {"major": 1}, "must_fix": 1})
    )
    wf = {
        "version": "1.0", "id": "usage-e2e", "repo_set": "rs",
        "tasks": [
            {"id": "impl", "agent": "dev", "instruction": "specs/instructions/impl.md",
             "outputs": ["output/t1/dev.md"], "model": HAIKU},
            {"id": "review", "agent": "rev", "instruction": "specs/instructions/review.md",
             "depends_on": ["impl"], "inputs": ["output/t1/dev.md"],
             "outputs": ["output/t1/review.md"]},
        ],
    }  # fmt: skip
    (tmp_path / "workflow.json").write_text(json.dumps(wf))
    (tmp_path / "repo").mkdir()
    (tmp_path / "reposets.json").write_text(
        json.dumps(
            {
                "version": "1.0",
                "repo_sets": {
                    "rs": {
                        "workspace_root": str(tmp_path),
                        "repos": [{"id": "core", "path": "repo", "role": "primary"}],
                    }
                },
            }
        )
    )
    (tmp_path / "agents.json").write_text(
        json.dumps(
            {
                "version": "1.0",
                "agents": {
                    "dev": {"executor": "fake", "model": "claude-sonnet-5-5", "effort": "high"},
                    "rev": {"executor": "fake", "model": "claude-sonnet-5-5"},
                },
            }
        )
    )


class TestReportUsageE2E:
    def test_run_then_report_usage(self, tmp_path: Path) -> None:
        _write_specs(tmp_path)
        env = {
            "AO_WORKSPACE_ROOT": str(tmp_path),
            "HOME": str(tmp_path / "home"),
            "AO_STATE_DIR": str(tmp_path / "ao-state"),
        }
        spec_args = [
            "--workflow", str(tmp_path / "workflow.json"),
            "--reposets", str(tmp_path / "reposets.json"),
            "--agents", str(tmp_path / "agents.json"),
        ]  # fmt: skip
        res = runner.invoke(app, ["run", *spec_args], env=env)
        assert res.exit_code == 0, res.output

        # Provenance is persisted: task override beat the agent's model; effort fell through.
        run_dir = next((tmp_path / ".orchestrator" / "runs").iterdir())
        state = json.loads((run_dir / "state.json").read_text())
        impl = state["tasks"]["impl"]
        assert (impl["agent"], impl["model"], impl["effort"]) == ("dev", HAIKU, "high")
        review = state["tasks"]["review"]
        assert review["upstream_producers"] == ["impl"]
        assert review["review_verdict_path"] == "output/t1/review-verdict.json"

        res = runner.invoke(app, ["report-usage"], env=env)
        assert res.exit_code == 0, res.output
        assert "verdicts found: 1/1" in res.output
        assert HAIKU in res.output and "claude-sonnet-5-5" in res.output

        res = runner.invoke(app, ["report-usage", "--json"], env=env)
        assert res.exit_code == 0, res.output
        groups = {g["agent"]: g for g in json.loads(res.output)["groups"]}
        assert groups["dev"]["reviewed"] == 1 and groups["dev"]["review_fail"] == 1
        assert groups["dev"]["review_fail_rate"] == 1.0
        assert groups["rev"]["reviewed"] == 0

    def test_empty_workspace(self, tmp_path: Path) -> None:
        res = runner.invoke(
            app, ["report-usage", "--workspace", str(tmp_path)], env={"HOME": str(tmp_path)}
        )
        assert res.exit_code == 0, res.output
        assert "no settled" in res.output


def test_review_instructions_document_the_verdict_sidecar() -> None:
    import agent_orchestrator
    from agent_orchestrator.usage import REVIEW_VERDICT_BASENAME

    instr = (
        Path(agent_orchestrator.__file__).parent / "templates/builtin/routed-runner/instructions"
    )
    for name in ("10-review-task.md", "33-task-review.md", "23-bug-review.md", "42-doc-review.md"):
        text = (instr / name).read_text()
        assert REVIEW_VERDICT_BASENAME in text, name
        assert '"verdict": "PASS"' in text and "must_fix" in text, name
