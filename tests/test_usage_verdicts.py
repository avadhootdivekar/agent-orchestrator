"""Generic verdict capture (`usage.verdict_path_for`/`parse_verdict`) + outcome aggregation +
`ao report-usage` outcome rendering, incl. an overseer-style fixture."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agent_orchestrator.artifacts import LocalFsArtifactStore
from agent_orchestrator.cli import app
from agent_orchestrator.errors import SpecValidationError
from agent_orchestrator.models import (
    MAX_CONTROL_FILE_BYTES,
    RunState,
    TaskRunState,
    TaskSpec,
    WorkflowSpec,
)
from agent_orchestrator.spec import validate_isolation
from agent_orchestrator.usage import (
    KIND_CHECKPOINT,
    KIND_FINAL_VERIFY,
    KIND_REVIEW,
    aggregate_usage,
    dispatch_provenance,
    parse_verdict,
    read_verdict,
    verdict_path_for,
)

runner = CliRunner()

CK = {
    "schema": "ao.overseer.verdict/v1",
    "decision": "Continue",
    "criteria": [{"id": "c1", "status": "met"}, {"id": "c2", "status": "unmet"}, "junk"],
    "alignment": [{"ask_id": "A1", "status": "on_track"}, {"ask_id": "A2", "status": "at_risk"}],
}
FV = {
    "schema": "ao.overseer.final-verify/v1",
    "asks": [{"id": "A1", "verdict": "met"}, {"id": "A2", "verdict": "partial"}, {"id": "A3"}],
}


def _t(outputs=(), **kw) -> TaskSpec:
    return TaskSpec(id="t", agent="a", instruction="i.md", outputs=list(outputs), **kw)


class TestVerdictPathFor:
    def test_explicit_beats_everything(self) -> None:
        t = _t(["o/review.md", "o/verdict.json"], verdict_path="x/v.json")
        assert verdict_path_for(t) == "x/v.json"

    def test_declared_verdict_output_beats_sibling(self) -> None:
        t = _t(["o/review.md", "o/ck-01/verdict.json"])
        assert verdict_path_for(t) == "o/ck-01/verdict.json"
        assert verdict_path_for(_t(["o/foo-verdict.json"])) == "o/foo-verdict.json"

    def test_sibling_table(self) -> None:
        assert verdict_path_for(_t(["o/review.md"])) == "o/review-verdict.json"
        assert verdict_path_for(_t(["o/final/verify.md"])) == "o/final/verify-verdict.json"
        assert verdict_path_for(_t(["o/report.md"])) is None

    def test_provenance_records_both(self) -> None:
        prov = dispatch_provenance(_t(["o/review.md"]), None, None, [])
        assert prov["verdict_path"] == prov["review_verdict_path"] == "o/review-verdict.json"
        prov = dispatch_provenance(_t(["o/verify.md"]), None, None, [])
        assert prov["verdict_path"] == "o/verify-verdict.json"
        assert prov["review_verdict_path"] is None


class TestParse:
    def test_review(self) -> None:
        kind, v = parse_verdict({"verdict": "pass", "must_fix": 2})  # type: ignore[misc]
        assert kind == KIND_REVIEW and v.verdict == "PASS" and v.must_fix == 2  # type: ignore[union-attr]

    def test_checkpoint(self) -> None:
        kind, v = parse_verdict(CK)  # type: ignore[misc]
        assert kind == KIND_CHECKPOINT and v.decision == "continue"  # type: ignore[union-attr]
        assert (v.met, v.unmet, v.deferred) == (1, 1, 0)  # type: ignore[union-attr]
        assert v.alignment == {"on_track": 1, "at_risk": 1}  # type: ignore[union-attr]

    def test_final_verify_ignores_bad_asks(self) -> None:
        kind, v = parse_verdict(FV)  # type: ignore[misc]
        assert kind == KIND_FINAL_VERIFY
        assert (v.met, v.partial, v.not_met) == (1, 1, 0)  # type: ignore[union-attr]

    def test_unknown_and_malformed_shapes(self) -> None:
        assert parse_verdict({"foo": 1}) is None
        assert parse_verdict({"verdict": "MAYBE"}) is None
        kind, v = parse_verdict({"decision": 5, "criteria": "x", "alignment": None})  # type: ignore[misc]
        assert kind == KIND_CHECKPOINT and v.decision is None and v.met == 0  # type: ignore[union-attr]

    def test_read_missing_malformed_oversized(self, tmp_path: Path) -> None:
        store = LocalFsArtifactStore(str(tmp_path))
        assert read_verdict(store, None) is None
        assert read_verdict(store, "nope.json") is None
        (tmp_path / "bad.json").write_text("{x")
        assert read_verdict(store, "bad.json") is None
        (tmp_path / "list.json").write_text("[1]")
        assert read_verdict(store, "list.json") is None
        (tmp_path / "big.json").write_text(json.dumps({**CK, "pad": "x" * MAX_CONTROL_FILE_BYTES}))
        assert read_verdict(store, "big.json") is None
        (tmp_path / "ok.json").write_text(json.dumps(FV))
        assert read_verdict(store, "ok.json") is not None


def _ts(vpath=None, status="succeeded", producers=(), model="m", **kw) -> TaskRunState:
    return TaskRunState(
        status=status, agent="ag", model=model, dispatch_cycle=1, verdict_path=vpath,
        upstream_producers=list(producers), **kw,
    )  # fmt: skip


def _state(tasks, run_id="r1") -> RunState:
    return RunState(
        run_id=run_id, workflow_id="w", repo_set="rs", started_at="t", updated_at="t", tasks=tasks
    )


def _write(tmp: Path, rel: str, data) -> None:
    (tmp / rel).parent.mkdir(parents=True, exist_ok=True)
    (tmp / rel).write_text(json.dumps(data))


class TestAggregateOutcomes:
    def test_outcomes_last_checkpoint_wins_and_coverage(self, tmp_path: Path) -> None:
        _write(tmp_path, "ck-01/verdict.json", CK)
        _write(
            tmp_path,
            "ck-02/verdict.json",
            {"decision": "closeout", "criteria": [{"status": "met"}, {"status": "deferred"}]},
        )
        _write(tmp_path, "final/verify-verdict.json", FV)
        st = _state(
            {
                "ck-01": _ts("ck-01/verdict.json"),
                "ck-02": _ts("ck-02/verdict.json"),
                "ck-03": _ts("ck-03/verdict.json"),  # declared but never written
                "final-verify": _ts("final/verify-verdict.json"),
            }
        )
        rep = aggregate_usage([st], LocalFsArtifactStore(str(tmp_path)))
        assert (rep.verdicts_found, rep.reviews_seen) == (3, 4)
        (o,) = rep.outcomes
        assert (o.checkpoints_found, o.checkpoints_seen) == (2, 3)
        assert o.last_decision == "closeout"
        assert (o.criteria_met, o.criteria_unmet, o.criteria_deferred) == (1, 0, 1)
        assert o.alignment == {}
        assert o.final_verify is not None and o.final_verify.partial == 1
        assert all(g.reviewed == 0 for g in rep.groups)

    def test_unknown_shaped_verdict_json_is_not_counted(self, tmp_path: Path) -> None:
        _write(tmp_path, "x/verdict.json", {"hello": "world"})
        rep = aggregate_usage(
            [_state({"a": _ts("x/verdict.json")})], LocalFsArtifactStore(str(tmp_path))
        )
        assert (rep.verdicts_found, rep.reviews_seen) == (0, 0) and rep.outcomes == []

    def test_run_without_verdicts_has_no_outcome(self, tmp_path: Path) -> None:
        rep = aggregate_usage([_state({"a": _ts()})], LocalFsArtifactStore(str(tmp_path)))
        assert rep.outcomes == []

    def test_custom_workflow_verdict_path_attributes_to_producer(self, tmp_path: Path) -> None:
        _write(tmp_path, "gate/result.json", {"verdict": "FAIL", "findings": {"major": 2}})
        st = _state(
            {
                "impl": _ts(model="haiku"),
                "gate": _ts("gate/result.json", producers=["impl"], model="opus"),
            }
        )
        rep = aggregate_usage([st], LocalFsArtifactStore(str(tmp_path)))
        by = {g.model: g for g in rep.groups}
        assert (by["haiku"].reviewed, by["haiku"].review_fail, by["haiku"].major) == (1, 1, 2)
        assert by["opus"].reviewed == 0 and rep.outcomes == []

    def test_legacy_review_verdict_path_still_read(self, tmp_path: Path) -> None:
        _write(tmp_path, "r.json", {"verdict": "PASS"})
        st = _state(
            {
                "impl": _ts(),
                "rev": TaskRunState(
                    status="succeeded", dispatch_cycle=1, review_verdict_path="r.json",
                    upstream_producers=["impl"],
                ),
            }
        )  # fmt: skip
        rep = aggregate_usage([st], LocalFsArtifactStore(str(tmp_path)))
        assert rep.verdicts_found == 1 and sum(g.reviewed for g in rep.groups) == 1


class TestBackCompat:
    def test_old_state_json_loads_without_new_fields(self) -> None:
        raw = {
            "run_id": "r", "workflow_id": "w", "repo_set": "rs", "started_at": "t",
            "updated_at": "t", "tasks": {"a": {"status": "succeeded", "review_verdict_path": "p"}},
        }  # fmt: skip
        st = RunState.model_validate_json(json.dumps(raw))
        assert st.tasks["a"].verdict_path is None
        assert st.tasks["a"].review_verdict_path == "p"

    def test_taskspec_default_none(self) -> None:
        assert _t().verdict_path is None


class TestSpecValidation:
    @pytest.mark.parametrize("bad", ["/abs/v.json", "../v.json", "a/../../v.json"])
    def test_unsafe_verdict_path_rejected(self, bad: str) -> None:
        wf = WorkflowSpec.model_validate(
            {"version": "1.0", "id": "w", "repo_set": "rs",
             "tasks": [{"id": "a", "agent": "g", "instruction": "i.md", "verdict_path": bad}]}
        )  # fmt: skip
        with pytest.raises(SpecValidationError, match="verdict_path"):
            validate_isolation(wf, wf.tasks)

    def test_safe_verdict_path_accepted(self) -> None:
        wf = WorkflowSpec.model_validate(
            {"version": "1.0", "id": "w", "repo_set": "rs",
             "tasks": [{"id": "a", "agent": "g", "instruction": "i.md", "verdict_path": "v.json"}]}
        )  # fmt: skip
        validate_isolation(wf, wf.tasks)


class TestReportUsageOverseerE2E:
    def _ws(self, tmp_path: Path) -> dict[str, str]:
        _write(tmp_path, "o/checkpoints/ck-01/verdict.json", CK)
        _write(tmp_path, "o/final/verify-verdict.json", FV)
        st = _state(
            {
                "ck-01": _ts("o/checkpoints/ck-01/verdict.json"),
                "final-verify": _ts("o/final/verify-verdict.json"),
                "ck-02": _ts("o/checkpoints/ck-02/verdict.json"),  # missing sidecar
            },
            run_id="ovr-1",
        )
        run_dir = tmp_path / ".orchestrator" / "runs" / "ovr-1"
        run_dir.mkdir(parents=True)
        (run_dir / "state.json").write_text(st.model_dump_json())
        return {"HOME": str(tmp_path / "home"), "AO_WORKSPACE_ROOT": str(tmp_path)}

    def test_text_output(self, tmp_path: Path) -> None:
        res = runner.invoke(app, ["report-usage"], env=self._ws(tmp_path))
        assert res.exit_code == 0, res.output
        assert "verdicts found: 2/3" in res.output
        assert "Outcome vs charter" in res.output
        assert "ovr-1: checkpoints 1/2  last decision: continue" in res.output
        assert "criteria met/unmet/deferred: 1/1/0" in res.output
        assert "final verify met/partial/not_met: 1/1/0" in res.output

    def test_json_output(self, tmp_path: Path) -> None:
        res = runner.invoke(app, ["report-usage", "--json"], env=self._ws(tmp_path))
        assert res.exit_code == 0, res.output
        payload = json.loads(res.output)
        (o,) = payload["outcomes"]
        assert o["run_id"] == "ovr-1" and o["last_decision"] == "continue"
        assert o["final_verify"] == {"met": 1, "partial": 1, "not_met": 0}
        assert (payload["verdicts_found"], payload["reviews_seen"]) == (2, 3)
