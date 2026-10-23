"""Tests for the `blackbox` grader (T-Bx2Cd3, bench-probe-dsl.md, HLD §5.2).

Layers:
- `validate_probe_spec` (static definition checks);
- `ProbeRunner` against a scripted fake transport/session (templating, save, repeat,
  parallel, fresh_state, error classes) -- no processes;
- the grader end to end against the hand-written stdlib reference under
  tests/bench/data/blackbox (http) plus tiny jsonl/cli suites, all in the `process`
  sandbox: reference copied into the repo by FakeSubject "copy-reference";
- one run through `runner.run_suite`;
- one docker-gated grade (AO_E2E_DOCKER=1).
"""

from __future__ import annotations

import copy
import json
import random
import shutil
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from agent_orchestrator.bench import sandbox as sb
from agent_orchestrator.bench.blackbox import (
    BLACKBOX_GRADER_TYPE,
    MISSING,
    BlackboxGrader,
    Limits,
    ProbeRunner,
    StepOutcome,
    Templater,
    Transport,
    validate_probe_spec,
)
from agent_orchestrator.bench.errors import GraderError
from agent_orchestrator.bench.registries import GRADER_REGISTRY
from agent_orchestrator.bench.runner import run_suite
from agent_orchestrator.bench.sandbox import ArtefactFailure, ArtefactTimeout
from agent_orchestrator.bench.spec import GraderConfig, load_subject
from agent_orchestrator.bench.subjects import FakeSubject
from agent_orchestrator.bench.workspace import RunContext

FIXTURE = Path(__file__).parent / "data" / "blackbox"
PROCESS_SANDBOX = {"type": "process"}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


@dataclass
class Ctx:
    repo_dir: str
    grading_dir: str | None


def _cfg(**extra: Any) -> GraderConfig:
    extra.setdefault("sandbox", dict(PROCESS_SANDBOX))
    return GraderConfig(type=BLACKBOX_GRADER_TYPE, **extra)


def _copy_reference(tmp_path: Path, subject_factory: Any, grading: Path, repo: Path) -> None:
    """FakeSubject `copy-reference`: overlay <grading>/reference/** onto the repo."""
    spec = load_subject(subject_factory(type_="fake", extra={"scripted_effect": "copy-reference"}))
    ctx = RunContext(
        workspace=str(tmp_path),
        repo_dir=str(repo),
        instruction_path="i",
        capture_dir=str(tmp_path / "capture"),
        timeout_seconds=30,
        grading_dir=str(grading),
    )
    result = FakeSubject(spec).run(None, ctx)  # type: ignore[arg-type]
    assert result.status == "succeeded"


def _stage(
    tmp_path: Path,
    subject_factory: Any,
    edit_grading: Callable[[Path], None] | None = None,
) -> tuple[Path, Path]:
    """-> (grading_dir, repo_dir): hidden assets copied from the fixture, repo = the
    reference copied in by FakeSubject (what a perfect agent would produce)."""
    grading = tmp_path / "grading"
    shutil.copytree(FIXTURE, grading)
    if edit_grading is not None:
        edit_grading(grading)
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "SPEC.md").write_text("skeleton the agent sees\n")
    _copy_reference(tmp_path, subject_factory, grading, repo)
    return grading, repo


def _patch(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    assert text.count(old) == 1, f"patch anchor not unique in {path}: {old!r}"
    path.write_text(text.replace(old, new))


def _edit_probes(grading: Path, fn: Callable[[dict[str, Any]], None]) -> None:
    p = grading / "probes.json"
    data = json.loads(p.read_text())
    fn(data)
    p.write_text(json.dumps(data))


def _by_id(detail: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {r["id"]: r for r in detail["probe_results"]}


# ---------------------------------------------------------------------------
# registry + static validation
# ---------------------------------------------------------------------------


def test_registered_under_blackbox() -> None:
    assert GRADER_REGISTRY[BLACKBOX_GRADER_TYPE] is BlackboxGrader


def _probe(**over: Any) -> dict[str, Any]:
    probe: dict[str, Any] = {
        "id": "p1",
        "steps": [{"op": {"method": "GET", "path": "/healthz"}, "expect": {"status": 200}}],
    }
    probe.update(over)
    return probe


def _spec(core: list[dict[str, Any]] | None = None, **over: Any) -> dict[str, Any]:
    spec: dict[str, Any] = {
        "version": "1.0",
        "contract": "http",
        "groups": {"core": core if core is not None else [_probe()]},
    }
    spec.update(over)
    return spec


def _step(op: dict[str, Any] | None = None, **over: Any) -> dict[str, Any]:
    step: dict[str, Any] = {"op": op or {"method": "GET", "path": "/x"}}
    step.update(over)
    return step


class TestValidateProbeSpec:
    def test_minimal_spec_is_normalised(self) -> None:
        out = validate_probe_spec(_spec())
        assert out["contract"] == "http" and out["startup_timeout_seconds"] == 30.0
        assert out["weights"] == {"core": 0.5, "trap": 0.35, "perf": 0.15}
        assert out["pass_threshold"] is None and out["groups"]["trap"] == []
        assert out["groups"]["perf"] is None

    def test_the_committed_fixture_is_valid(self) -> None:
        spec = validate_probe_spec(json.loads((FIXTURE / "probes.json").read_text()))
        assert len(spec["groups"]["core"]) == 8 and len(spec["groups"]["trap"]) == 2
        assert spec["groups"]["perf"]["sizes"] == [50, 500]

    @pytest.mark.parametrize(
        ("spec", "needle"),
        [
            ({"contract": "grpc"}, "contract"),
            ({"groups": {"core": []}}, "at least one probe"),
            ({"groups": {}}, "at least one probe"),
            ({"groups": {"core": [_probe()], "bonus": []}}, "groups"),
            ({"startup_timeout_seconds": 0}, "startup_timeout_seconds"),
            ({"weights": {"core": -1}}, "weights"),
            ({"weights": {"core": 1, "extra": 1}}, "weights"),
            ({"pass_threshold": 1.5}, "pass_threshold"),
            ([], "top level"),
        ],
    )
    def test_top_level_errors(self, spec: Any, needle: str) -> None:
        raw = spec if isinstance(spec, list) else {**_spec(), **spec}
        with pytest.raises(GraderError, match=needle):
            validate_probe_spec(raw)

    @pytest.mark.parametrize(
        ("probe", "needle"),
        [
            (_probe(id=""), "non-empty string `id`"),
            (_probe(bogus=1), "unknown probe keys"),
            (_probe(trap="pagination_stability"), "only valid in the trap group"),
            (_probe(fresh_state="yes"), "fresh_state"),
            (_probe(steps=[]), "non-empty array of steps"),
            (_probe(parallel=[]), "non-empty array of step lists"),
            (_probe(parallel=[[]]), "non-empty array of steps"),
            (_probe(steps=[_step(sleep_ms=-1)]), "sleep_ms"),
            (_probe(steps=[_step(repeat=0)]), "repeat"),
            (_probe(steps=[_step(repeat="{{N}}")]), "repeat"),
            (_probe(steps=[_step(bogus=1)]), "unknown step keys"),
            (_probe(steps=[_step({"method": "FETCH", "path": "/x"})]), "method"),
            (_probe(steps=[_step({"path": "x"})]), "starting with '/'"),
            (_probe(steps=[_step({"method": "GET", "path": "/x", "args": []})]), "not valid"),
            (_probe(steps=[_step(expect={"exit_code": 0})]), "not valid for contract"),
            (_probe(steps=[_step(expect={"status": "200"})]), "expect.status"),
            (_probe(steps=[_step(expect={"max_ms": 0})]), "max_ms"),
            (_probe(steps=[_step(expect={"json": {"$bogus": 1}})]), "unknown matcher directive"),
            (_probe(steps=[_step(expect={"json": {"$regex": "("}})]), "does not compile"),
            (_probe(steps=[_step(save={"i": "$.id"})]), "must be an identifier"),
            (_probe(steps=[_step(save={"x": "id"})]), "JSONPath"),
            (_probe(steps=[_step({"method": "GET", "path": "/x/{{nope}}"})]), "unknown variable"),
            (
                _probe(steps=[_step(expect={"json": {"id": {"$var": "nope"}}})]),
                "unknown variable",
            ),
        ],
    )
    def test_probe_errors(self, probe: dict[str, Any], needle: str) -> None:
        with pytest.raises(GraderError, match=needle):
            validate_probe_spec(_spec([probe]))

    def test_variable_scoping_between_steps_parallel_and_after(self) -> None:
        saver = _step(save={"a": "$.id"})
        use_a = _step({"method": "GET", "path": "/x/{{a}}"})
        validate_probe_spec(_spec([_probe(steps=[saver, use_a])]))  # later step sees it
        with pytest.raises(GraderError, match="unknown variable"):  # ... but not an earlier one
            validate_probe_spec(_spec([_probe(steps=[use_a, saver])]))
        # a step may not use a variable it saves itself
        with pytest.raises(GraderError, match="unknown variable"):
            validate_probe_spec(
                _spec(
                    [_probe(steps=[_step({"method": "GET", "path": "/{{a}}"}, save={"a": "$.id"})])]
                )
            )
        # parallel lists see `steps`; their own captures are local and invisible to `after`
        par = _probe(
            steps=[saver],
            parallel=[[_step(save={"p": "$.id"}), use_a], [use_a]],
            after=[use_a],
        )
        validate_probe_spec(_spec([par]))
        par["after"] = [_step({"method": "GET", "path": "/x/{{p}}"})]
        with pytest.raises(GraderError, match="unknown variable.*p"):
            validate_probe_spec(_spec([par]))

    def test_duplicate_probe_ids_across_groups(self) -> None:
        spec = _spec([_probe(id="dup")])
        spec["groups"]["trap"] = [_probe(id="dup", trap="t")]
        with pytest.raises(GraderError, match="duplicate probe id"):
            validate_probe_spec(spec)

    @pytest.mark.parametrize(
        ("contract", "step", "needle"),
        [
            ("jsonl", {"op": {"path": "/x"}}, "not valid for contract"),
            ("jsonl", {"op": {"json": "ping"}}, "`json` object"),
            ("jsonl", {"op": {"json": {"op": "x"}}, "expect": {"status": 200}}, "not valid"),
            ("cli", {"op": {"args": "add"}}, "array of strings"),
            ("cli", {"op": {"args": ["a"], "stdin": 5}}, "stdin"),
            ("cli", {"op": {"args": ["a"]}, "expect": {"exit_code": "0"}}, "exit_code"),
            ("cli", {"op": {"args": ["a"]}, "expect": {"stdout_contains": 3}}, "stdout_contains"),
        ],
    )
    def test_contract_specific_shapes(
        self, contract: str, step: dict[str, Any], needle: str
    ) -> None:
        spec = _spec([_probe(steps=[step])], contract=contract)
        with pytest.raises(GraderError, match=needle):
            validate_probe_spec(spec)

    def _perf(self, **over: Any) -> dict[str, Any]:
        perf = copy.deepcopy(json.loads((FIXTURE / "probes.json").read_text())["groups"]["perf"])
        perf.update(over)
        return perf

    def test_perf_spec_errors(self) -> None:
        def check(needle: str, **over: Any) -> None:
            spec = _spec()
            spec["groups"]["perf"] = self._perf(**over)
            with pytest.raises(GraderError, match=needle):
                validate_probe_spec(spec)

        check("unknown perf keys", bogus=1)
        check("sizes", sizes=[])
        check("sizes", sizes=[0, 10])
        check("at least two distinct sizes", sizes=[10])
        check("checks", checks=["alpha", "speed"])
        check("ops_per_size", ops_per_size=0)
        check("repeats", repeats=0)
        check("alpha_slack", alpha_slack=-1)
        check("ref_ratio_max", ref_ratio_max=0)
        check("expected_alpha", expected_alpha={"indexed_get": "low"})
        check("not in mix", expected_alpha={"nonexistent": 0.3})
        check("sum to 1.0", mix=[{**self._perf()["mix"][0], "weight": 0.5}])
        check("exactly the keys", mix=[{"weight": 1.0, "name": "x", "step": {}}])
        check("unique string", mix=self._perf()["mix"] + [self._perf()["mix"][0]])
        check("mix", mix=[])
        check("unknown variable", load={"op": {"method": "GET", "path": "/{{nope}}"}})

    def test_perf_mix_may_use_load_saves_and_N(self) -> None:
        spec = _spec()
        perf = self._perf()
        perf["load"] = {**perf["load"], "save": {"last": "$.id"}}
        perf["mix"][0]["step"] = {"op": {"method": "GET", "path": "/widgets/{{last}}?n={{N}}"}}
        spec["groups"]["perf"] = perf
        validate_probe_spec(spec)


# ---------------------------------------------------------------------------
# ProbeRunner with a scripted transport (no processes)
# ---------------------------------------------------------------------------


class FakeTransport(Transport):
    def __init__(self, handler: Callable[[dict[str, Any]], StepOutcome | None]) -> None:
        self.handler = handler
        self.calls: list[dict[str, Any]] = []
        self.threads: set[int] = set()
        self._lock = threading.Lock()

    def send(self, op: Any, timeout: float) -> StepOutcome:
        with self._lock:
            self.calls.append(copy.deepcopy(dict(op)))
            self.threads.add(threading.get_ident())
        out = self.handler(dict(op))
        assert out is not None
        return out


class FakeSession:
    def __init__(
        self, transport: FakeTransport, *, start_failures: int = 0, ever: bool = True
    ) -> None:
        self.transport = transport
        self.restarts = 0
        self.start_failures = start_failures
        self.ever_started = ever

    def ensure(self, fresh: bool) -> FakeTransport:
        if fresh:
            self.restarts += 1
        return self.transport


def _ok(status: int = 200, body: Any = MISSING, ms: float = 1.0) -> StepOutcome:
    return StepOutcome(
        ms=ms, status=status, body=body, text=json.dumps(body) if body is not MISSING else ""
    )


def _runner(
    handler: Callable[[dict[str, Any]], StepOutcome | None],
    *,
    contract: str = "http",
    limits: Limits | None = None,
    **session_kw: Any,
) -> tuple[ProbeRunner, FakeSession, FakeTransport]:
    transport = FakeTransport(handler)
    session = FakeSession(transport, **session_kw)
    templater = Templater(random.Random(3), lambda: datetime(2026, 1, 1, tzinfo=UTC))
    runner = ProbeRunner(contract, session, templater, limits or Limits())  # type: ignore[arg-type]
    return runner, session, transport


def _get(path: str, **kw: Any) -> dict[str, Any]:
    return {"op": {"method": "GET", "path": path}, **kw}


class TestProbeRunner:
    def test_save_repeat_index_and_typed_substitution(self) -> None:
        created: list[str] = []

        def handler(op: dict[str, Any]) -> StepOutcome:
            if op["method"] == "POST" and op["path"] == "/items":
                created.append(op["json"]["name"])
                return _ok(201, {"id": 39 + len(created)})
            return _ok(200, {"ok": True})

        runner, _, transport = _runner(handler)
        probe = {
            "id": "p",
            "steps": [
                {
                    "op": {"method": "POST", "path": "/items", "json": {"name": "n{{i}}"}},
                    "expect": {"status": 201},
                    "save": {"id": "$.id"},
                    "repeat": 3,
                },
                {
                    "op": {
                        "method": "POST",
                        "path": "/children/{{id}}",
                        "json": {"parent": "{{id}}"},
                    },
                    "expect": {"status": [200], "json": {"ok": True}},
                },
            ],
        }
        out = runner.run_probe(probe, "core")
        assert out.passed, out.mismatches
        assert created == ["n0", "n1", "n2"]  # {{i}} = repeat index
        last = transport.calls[-1]  # `save` keeps the last repeat's id (42)
        assert last["path"] == "/children/42"
        assert last["json"] == {"parent": 42}  # whole-string placeholder keeps its int type

    def test_failing_step_stops_its_list_and_skips_parallel_and_after(self) -> None:
        runner, _, transport = _runner(lambda op: _ok(500, {"error": "x"}))
        probe = {
            "id": "p",
            "steps": [_get("/a", expect={"status": 200}), _get("/never")],
            "parallel": [[_get("/par")]],
            "after": [_get("/after")],
        }
        out = runner.run_probe(probe, "core")
        assert not out.passed and len(out.mismatches) == 1
        assert "steps[0] GET /a: status: expected 200, got 500" in out.mismatches[0]
        assert [c["path"] for c in transport.calls] == ["/a"]

    def test_parallel_lists_really_run_concurrently(self) -> None:
        barrier = threading.Barrier(3, timeout=5)

        def handler(op: dict[str, Any]) -> StepOutcome:
            if op["path"].startswith("/par"):
                barrier.wait()  # only passes if all three are in flight at once
            return _ok(200, {"seen": op["path"]})

        runner, _, transport = _runner(handler)
        probe = {
            "id": "p",
            "steps": [_get("/setup")],
            "parallel": [[_get(f"/par{k}", expect={"status": 200})] for k in range(3)],
            "after": [_get("/after", expect={"json": {"seen": "/after"}})],
        }
        out = runner.run_probe(probe, "core")
        assert out.passed, out.mismatches
        assert len(transport.threads) >= 3
        assert [c["path"] for c in transport.calls][-1] == "/after"  # after runs last

    def test_parallel_failure_is_reported_per_list_and_blocks_after(self) -> None:
        def handler(op: dict[str, Any]) -> StepOutcome:
            return _ok(500 if op["path"] == "/par1" else 200, {})

        runner, _, transport = _runner(handler)
        probe = {
            "id": "p",
            "steps": [_get("/setup")],
            "parallel": [
                [_get("/par0", expect={"status": 200})],
                [_get("/par1", expect={"status": 200})],
            ],
            "after": [_get("/after")],
        }
        out = runner.run_probe(probe, "core")
        assert not out.passed and any("parallel[1]" in m for m in out.mismatches)
        assert "/after" not in [c["path"] for c in transport.calls]

    def test_parallel_captures_stay_thread_local(self) -> None:
        runner, _, transport = _runner(lambda op: _ok(200, {"id": op["path"]}))
        probe = {
            "id": "p",
            "steps": [_get("/s", save={"base": "$.id"})],
            "parallel": [
                [_get("/a", save={"mine": "$.id"}), _get("/{{base}}{{mine}}")],
                [_get("/b", save={"mine": "$.id"}), _get("/{{base}}{{mine}}")],
            ],
        }
        out = runner.run_probe(probe, "core")
        assert out.passed, out.mismatches
        paths = sorted(c["path"] for c in transport.calls[1:])
        # each list built its second path from ITS OWN `mine` plus the shared `base`
        assert paths == ["//s/a", "//s/b", "/a", "/b"]

    def test_fresh_state_restarts_only_when_asked(self) -> None:
        runner, session, _ = _runner(lambda op: _ok(200, {}))
        runner.run_probe(_probe_get("a"), "core")
        runner.run_probe({**_probe_get("b"), "fresh_state": False}, "core")
        runner.run_probe({**_probe_get("c"), "fresh_state": True}, "core")
        assert session.restarts == 2

    def test_transport_errors_are_probe_failures_not_exceptions(self) -> None:
        def handler(op: dict[str, Any]) -> StepOutcome:
            if op["path"] == "/slow":
                raise ArtefactTimeout("GET /slow timed out after 10s")
            raise ArtefactFailure("connection refused")

        runner, _, _ = _runner(handler)
        down = runner.run_probe(_probe_get("a"), "core")
        assert not down.passed and "connection refused" in down.mismatches[0]
        slow = runner.run_probe({"id": "s", "steps": [_get("/slow")]}, "core")
        assert not slow.passed and "timed out" in slow.mismatches[0]

    def test_unknown_variable_at_runtime_is_a_definition_error(self) -> None:
        runner, _, _ = _runner(lambda op: _ok(200, {}))
        with pytest.raises(GraderError, match="unknown template variable"):
            runner.run_probe({"id": "p", "steps": [_get("/x/{{nope}}")]}, "core")

    def test_unknown_matcher_var_at_runtime_is_a_definition_error(self) -> None:
        runner, _, _ = _runner(lambda op: _ok(200, {"id": 1}))
        step = _get("/x", expect={"json": {"id": {"$var": "nope"}}})
        with pytest.raises(GraderError, match="unknown variable 'nope'"):
            runner.run_probe({"id": "p", "steps": [step]}, "core")

    def test_missing_save_path_fails_the_step(self) -> None:
        runner, _, _ = _runner(lambda op: _ok(200, {"other": 1}))
        out = runner.run_probe({"id": "p", "steps": [_get("/x", save={"v": "$.id"})]}, "core")
        assert not out.passed and "save v: $.id not found" in out.mismatches[0]

    def test_probe_time_budget(self) -> None:
        def slow(op: dict[str, Any]) -> StepOutcome:
            time.sleep(0.06)
            return _ok(200, {})

        runner, _, transport = _runner(slow, limits=Limits(probe_timeout_seconds=0.1))
        out = runner.run_probe({"id": "p", "steps": [{**_get("/x"), "repeat": 50}]}, "core")
        assert not out.passed and "time budget" in out.mismatches[0]
        assert len(transport.calls) < 10

    def test_expect_checks_per_contract(self) -> None:
        runner, _, _ = _runner(lambda op: _ok(200, {"a": 1}, ms=250.0))
        probe = {
            "id": "p",
            "steps": [_get("/x", expect={"status": [201, 202], "json": {"a": 2}, "max_ms": 100})],
        }
        out = runner.run_probe(probe, "core")
        joined = "\n".join(out.mismatches)
        assert "status: expected [201, 202], got 200" in joined
        assert "$.a: expected 2, got 1" in joined and "exceeds max_ms 100" in joined

        cli_runner, _, _ = _runner(
            lambda op: StepOutcome(exit_code=2, text="usage: nope", body=MISSING), contract="cli"
        )
        cli = cli_runner.run_probe(
            {
                "id": "c",
                "steps": [
                    {
                        "op": {"args": ["x"]},
                        "expect": {"exit_code": 0, "stdout_contains": "ok", "json": {"a": 1}},
                    }
                ],
            },
            "core",
        )
        text = "\n".join(cli.mismatches)
        assert "exit_code: expected 0, got 2" in text and "does not contain 'ok'" in text
        assert "response has no JSON body" in text

    def test_run_groups_short_circuits_when_artefact_never_started(self) -> None:
        runner, _, transport = _runner(lambda op: _ok(200, {}), start_failures=1, ever=False)
        spec = {"groups": {"core": [_probe_get("a"), _probe_get("b")], "trap": [_probe_get("c")]}}
        outcomes = runner.run_groups(spec)
        assert [o.passed for o in outcomes] == [False, False, False]
        assert all("never became ready" in o.mismatches[0] for o in outcomes)
        assert transport.calls == []

    def test_outcome_detail_shape(self) -> None:
        runner, _, _ = _runner(lambda op: _ok(200, {}))
        out = runner.run_probe({**_probe_get("a"), "trap": "t1"}, "trap")
        detail = out.as_detail()
        assert set(detail) == {"id", "group", "trap", "passed", "mismatches", "ms"}
        assert detail["trap"] == "t1" and detail["group"] == "trap" and detail["passed"] is True


def _probe_get(pid: str) -> dict[str, Any]:
    return {"id": pid, "steps": [_get("/x", expect={"status": 200})]}


# ---------------------------------------------------------------------------
# the grader against the stdlib reference fixture (process sandbox)
# ---------------------------------------------------------------------------


class TestGraderHttp:
    def test_copy_reference_is_solved_with_full_score(
        self, tmp_path: Path, subject_factory: Any
    ) -> None:
        grading, repo = _stage(tmp_path, subject_factory)
        result = BlackboxGrader().grade(_cfg(), Ctx(str(repo), str(grading)))
        d = result.detail
        assert result.solved is True, result.raw_tail
        assert result.score == pytest.approx(1.0, abs=0.15)  # perf thresholds are generous
        assert d["score_breakdown"]["core"]["fraction"] == 1.0
        assert d["score_breakdown"]["trap"]["fraction"] == 1.0
        assert d["score_breakdown"]["core"] == {
            "weight": 0.5,
            "effective_weight": 0.5,
            "passed": 8,
            "total": 8,
            "fraction": 1.0,
        }
        assert d["sandbox"] == "process" and d["image"] is None
        assert d["reference_ok"] is True and d["core_all_passed"] is True
        assert d["reference_trap_failed"] == []
        assert d["contract"] == "http" and d["language"] == "python"
        assert result.raw_tail == ""  # nothing failed

    def test_probe_results_shape(self, tmp_path: Path, subject_factory: Any) -> None:
        grading, repo = _stage(tmp_path, subject_factory)
        d = BlackboxGrader().grade(_cfg(), Ctx(str(repo), str(grading))).detail
        results = d["probe_results"]
        assert len(results) == 10 and {r["group"] for r in results} == {"core", "trap"}
        for r in results:
            assert set(r) == {"id", "group", "trap", "passed", "mismatches", "ms"}
            assert r["passed"] is True and r["mismatches"] == [] and r["ms"] > 0
        by_id = _by_id(d)
        assert by_id["pagination-stable-under-delete"]["trap"] == "pagination_stability"
        assert by_id["healthz"]["trap"] is None
        json.dumps(d)  # run.json-serialisable

    def test_perf_detail_per_dsl(self, tmp_path: Path, subject_factory: Any) -> None:
        grading, repo = _stage(tmp_path, subject_factory)
        perf = BlackboxGrader().grade(_cfg(), Ctx(str(repo), str(grading))).detail["perf"]
        assert set(perf["sizes"]) == {"50", "500"}
        for ops in perf["sizes"].values():
            assert set(ops) == {"get", "create", "list"}
            assert all(
                set(v) == {"p50_ms", "p95_ms"} and v["p95_ms"] >= v["p50_ms"] > 0
                for v in ops.values()
            )
        assert set(perf["classes"]) == {"indexed_get", "write", "paginated_list"}
        for cls in perf["classes"].values():
            assert cls["alpha"] is not None and cls["alpha_pass"] is True
        # same language as the reference -> ratio enforced
        assert perf["ref_ratio_enforced"] is True and perf["ref_ratio_pass"] is True
        assert perf["ref_ratio_p95"] is not None and perf["ref_ratio_p95"] > 0
        assert perf["timed_out"] is False and perf["error"] is None and perf["failed_ops"] == {}
        assert perf["sandbox"] == "process" and perf["image"] is None

    def test_pagination_bug_fails_the_trap_but_not_core(
        self, tmp_path: Path, subject_factory: Any
    ) -> None:
        grading, repo = _stage(tmp_path, subject_factory)
        server = repo / "server.py"
        # the classic bug: cursor is an offset into the *current* list, not the last seen id
        _patch(
            server,
            "start = bisect.bisect_right(IDS, cursor)  # cursor is the last seen id",
            "start = cursor",
        )
        _patch(
            server,
            "next_cursor = chunk[-1] if (chunk and more) else None",
            "next_cursor = start + limit if more else None",
        )
        result = BlackboxGrader().grade(_cfg(), Ctx(str(repo), str(grading)))
        by_id = _by_id(result.detail)
        assert all(r["passed"] for r in result.detail["probe_results"] if r["group"] == "core")
        assert result.detail["core_all_passed"] is True
        trap = by_id["pagination-stable-under-delete"]
        assert trap["passed"] is False
        assert "steps[6] GET /widgets?limit=2&cursor=2: $.items" in trap["mismatches"][0]
        assert by_id["unique-name-under-concurrent-create"]["passed"] is True
        assert result.detail["score_breakdown"]["trap"]["fraction"] == 0.5
        assert result.score < 1.0
        # solved follows the probes.json threshold (0.8) ...
        assert result.solved == (result.score >= 0.8)
        # ... and a stricter suite-level threshold overrides it
        strict = BlackboxGrader().grade(_cfg(pass_threshold=0.99), Ctx(str(repo), str(grading)))
        assert strict.solved is False and strict.detail["pass_threshold"] == 0.99
        assert "FAIL trap:pagination-stable-under-delete" in strict.raw_tail

    def test_core_failure_means_never_solved(self, tmp_path: Path, subject_factory: Any) -> None:
        grading, repo = _stage(tmp_path, subject_factory)
        _patch(repo / "server.py", "return self._send(201, item)", "return self._send(200, item)")
        result = BlackboxGrader().grade(_cfg(pass_threshold=0.0), Ctx(str(repo), str(grading)))
        assert result.detail["core_all_passed"] is False
        assert result.solved is False  # even with a zero threshold: core must all pass

    def test_missing_run_sh_is_an_artefact_failure_not_infra(
        self, tmp_path: Path, subject_factory: Any
    ) -> None:
        grading, repo = _stage(tmp_path, subject_factory)
        (repo / "run.sh").unlink()
        result = BlackboxGrader().grade(_cfg(), Ctx(str(repo), str(grading)))  # no GraderError
        assert result.solved is False and result.score == 0.0
        assert result.detail["artefact_error"] == "run.sh not found in the artefact directory"
        assert all(not r["passed"] for r in result.detail["probe_results"])
        assert result.detail["reference_ok"] is True
        assert result.detail["perf"]["error"].startswith("perf skipped")

    def test_artefact_that_never_listens_fails_fast(
        self, tmp_path: Path, subject_factory: Any
    ) -> None:
        grading, repo = _stage(tmp_path, subject_factory)
        (repo / "run.sh").write_text("exec sleep 60\n")
        cfg = _cfg(sandbox={"type": "process", "startup_timeout_seconds": 1.5})
        t0 = time.monotonic()
        result = BlackboxGrader().grade(cfg, Ctx(str(repo), str(grading)))
        assert time.monotonic() - t0 < 30  # one startup wait, not one per probe
        assert result.solved is False and result.score == 0.0
        assert result.detail["artefact_error"] == "artefact never became ready"
        first = result.detail["probe_results"][0]["mismatches"][0]
        assert "failed to start" in first and "/healthz" in first

    def test_artefact_crash_log_is_in_the_mismatch(
        self, tmp_path: Path, subject_factory: Any
    ) -> None:
        grading, repo = _stage(tmp_path, subject_factory)
        (repo / "run.sh").write_text(
            "echo 'ModuleNotFoundError: no module named widgets' >&2\nexit 1\n"
        )
        result = BlackboxGrader().grade(_cfg(), Ctx(str(repo), str(grading)))
        assert "ModuleNotFoundError" in result.detail["probe_results"][0]["mismatches"][0]

    def test_slow_artefact_times_out_perf_without_infra_error(
        self, tmp_path: Path, subject_factory: Any
    ) -> None:
        grading, repo = _stage(tmp_path, subject_factory)
        server = repo / "server.py"
        _patch(server, "import threading\n", "import threading\nimport time\n")
        anchor = (
            "            with LOCK:\n                item = ITEMS.get(self._item_id(path))\n"
            "            return self._send(200, item) if item"
        )
        _patch(
            server,
            anchor,
            "            time.sleep(3)\n" + anchor,
        )
        cfg = _cfg(limits={"request_timeout_seconds": 0.3, "probe_timeout_seconds": 10})
        result = BlackboxGrader().grade(cfg, Ctx(str(repo), str(grading)))
        perf = result.detail["perf"]
        assert perf["timed_out"] is True and "timed out" in perf["error"]
        assert result.detail["score_breakdown"]["perf"]["fraction"] == 0.0
        assert result.solved is False  # GET-by-id core probes time out too
        assert any(r["group"] == "core" and not r["passed"] for r in result.detail["probe_results"])

    def test_ref_ratio_not_enforced_across_languages(
        self, tmp_path: Path, subject_factory: Any
    ) -> None:
        def typescript(grading: Path) -> None:
            inst = json.loads((grading / "instance.json").read_text())
            inst["language"] = "typescript"  # artefact language != reference language (python)
            (grading / "instance.json").write_text(json.dumps(inst))

        grading, repo = _stage(tmp_path, subject_factory, typescript)
        result = BlackboxGrader().grade(_cfg(), Ctx(str(repo), str(grading)))
        perf = result.detail["perf"]
        assert result.detail["language"] == "typescript"
        assert perf["ref_ratio_enforced"] is False and perf["ref_ratio_pass"] is None
        assert perf["ref_ratio_p95"] is not None  # informational
        # 3 alpha classes + timeout counted; ref_ratio is not
        assert result.detail["score_breakdown"]["perf"]["total"] == 4

    def test_reference_reads_the_grader_supplied_instance(
        self, tmp_path: Path, subject_factory: Any
    ) -> None:
        def rename_entity(grading: Path) -> None:
            inst = json.loads((grading / "instance.json").read_text())
            inst["entities"][0]["plural"] = "gizmos"
            (grading / "instance.json").write_text(json.dumps(inst))
            probes = (grading / "probes.json").read_text().replace("/widgets", "/gizmos")
            (grading / "probes.json").write_text(probes)

        grading, repo = _stage(tmp_path, subject_factory, rename_entity)
        # reference/instance.json (sibling copy) still says "widgets": only $INSTANCE_JSON says
        # "gizmos". The reference passing proves the env path was used; the copied artefact
        # (no env, sibling copy) serves /widgets, so it fails -- as a wrong artefact should.
        result = BlackboxGrader().grade(_cfg(), Ctx(str(repo), str(grading)))
        assert result.detail["reference_ok"] is True
        assert result.detail["core_all_passed"] is False and result.solved is False

    def test_repo_dir_is_not_mutated_by_grading(self, tmp_path: Path, subject_factory: Any) -> None:
        grading, repo = _stage(tmp_path, subject_factory)
        before = sorted(p.relative_to(repo).as_posix() for p in repo.rglob("*"))
        BlackboxGrader().grade(_cfg(), Ctx(str(repo), str(grading)))
        assert sorted(p.relative_to(repo).as_posix() for p in repo.rglob("*")) == before
        assert not list(grading.glob("blackbox-*")), "work dirs are cleaned up"

    def test_grading_is_deterministic_for_a_seed(
        self, tmp_path: Path, subject_factory: Any
    ) -> None:
        grading, repo = _stage(tmp_path, subject_factory)
        a = BlackboxGrader(seed=7).grade(_cfg(), Ctx(str(repo), str(grading)))
        b = BlackboxGrader(seed=7).grade(_cfg(), Ctx(str(repo), str(grading)))
        assert [(r["id"], r["passed"]) for r in a.detail["probe_results"]] == [
            (r["id"], r["passed"]) for r in b.detail["probe_results"]
        ]

    def test_custom_weights_and_probes_file_name(
        self, tmp_path: Path, subject_factory: Any
    ) -> None:
        grading, repo = _stage(tmp_path, subject_factory)
        shutil.copy(grading / "probes.json", grading / "other.json")
        (grading / "probes.json").write_text("not json")  # must not be read
        _patch(repo / "server.py", "return self._send(201, item)", "return self._send(200, item)")
        cfg = _cfg(probes="other.json", weights={"core": 1, "trap": 0, "perf": 0})
        result = BlackboxGrader().grade(cfg, Ctx(str(repo), str(grading)))
        assert result.detail["score_breakdown"]["core"]["weight"] == 1.0
        assert result.score < 1.0


class TestGraderInfraErrors:
    def test_broken_reference_is_infra(self, tmp_path: Path, subject_factory: Any) -> None:
        def break_reference(grading: Path) -> None:
            _patch(
                grading / "reference" / "server.py",
                "return self._send(201, item)",
                "return self._send(200, item)",
            )

        grading, repo = _stage(tmp_path, subject_factory, break_reference)
        with pytest.raises(GraderError, match=r"reference implementation fails \d+ core probe"):
            BlackboxGrader().grade(_cfg(), Ctx(str(repo), str(grading)))

    def test_reference_that_cannot_start_is_infra(
        self, tmp_path: Path, subject_factory: Any
    ) -> None:
        def no_start(grading: Path) -> None:
            (grading / "reference" / "run.sh").write_text("exit 1\n")

        grading, repo = _stage(tmp_path, subject_factory, no_start)
        with pytest.raises(GraderError, match="reference implementation fails"):
            BlackboxGrader().grade(_cfg(), Ctx(str(repo), str(grading)))

    def test_reference_trap_failures_are_recorded_not_fatal(
        self, tmp_path: Path, subject_factory: Any
    ) -> None:
        def sloppy_reference(grading: Path) -> None:
            ref = grading / "reference" / "server.py"
            _patch(
                ref,
                "start = bisect.bisect_right(IDS, cursor)  # cursor is the last seen id",
                "start = cursor",
            )
            _patch(
                ref,
                "next_cursor = chunk[-1] if (chunk and more) else None",
                "next_cursor = start + limit if more else None",
            )

        grading, repo = _stage(tmp_path, subject_factory, sloppy_reference)
        result = BlackboxGrader().grade(_cfg(), Ctx(str(repo), str(grading)))
        assert result.detail["reference_trap_failed"] == ["pagination-stable-under-delete"]

    def test_missing_reference_dir(self, tmp_path: Path, subject_factory: Any) -> None:
        grading, repo = _stage(tmp_path, subject_factory)
        shutil.rmtree(grading / "reference")
        with pytest.raises(GraderError, match="reference implementation missing"):
            BlackboxGrader().grade(_cfg(), Ctx(str(repo), str(grading)))

    def test_bad_matcher_is_infra(self, tmp_path: Path, subject_factory: Any) -> None:
        def bad(data: dict[str, Any]) -> None:
            data["groups"]["core"][0]["steps"][0]["expect"]["json"] = {"status": {"$bogus": 1}}

        grading, repo = _stage(tmp_path, subject_factory, lambda g: _edit_probes(g, bad))
        with pytest.raises(GraderError, match="unknown matcher directive"):
            BlackboxGrader().grade(_cfg(), Ctx(str(repo), str(grading)))

    def test_unknown_variable_in_probes_is_infra(
        self, tmp_path: Path, subject_factory: Any
    ) -> None:
        def bad(data: dict[str, Any]) -> None:
            data["groups"]["trap"][0]["steps"][4]["op"]["path"] = "/widgets/{{ghost}}"

        grading, repo = _stage(tmp_path, subject_factory, lambda g: _edit_probes(g, bad))
        with pytest.raises(GraderError, match="unknown variable"):
            BlackboxGrader().grade(_cfg(), Ctx(str(repo), str(grading)))

    def test_missing_or_invalid_probes_file(self, tmp_path: Path, subject_factory: Any) -> None:
        grading, repo = _stage(tmp_path, subject_factory)
        (grading / "probes.json").write_text("{nope")
        with pytest.raises(GraderError, match="not valid JSON"):
            BlackboxGrader().grade(_cfg(), Ctx(str(repo), str(grading)))
        (grading / "probes.json").unlink()
        with pytest.raises(GraderError, match="probes file not found"):
            BlackboxGrader().grade(_cfg(), Ctx(str(repo), str(grading)))

    def test_grading_dir_required(self, tmp_path: Path) -> None:
        with pytest.raises(GraderError, match="needs a grading_dir"):
            BlackboxGrader().grade(_cfg(), Ctx(str(tmp_path), None))
        with pytest.raises(GraderError, match="does not exist"):
            BlackboxGrader().grade(_cfg(), Ctx(str(tmp_path), str(tmp_path / "nope")))

    def test_probes_path_may_not_escape_grading_dir(
        self, tmp_path: Path, subject_factory: Any
    ) -> None:
        grading, repo = _stage(tmp_path, subject_factory)
        with pytest.raises(GraderError, match="escapes the grading dir"):
            BlackboxGrader().grade(_cfg(probes="../repo/SPEC.md"), Ctx(str(repo), str(grading)))

    @pytest.mark.parametrize(
        ("cfg_extra", "needle"),
        [
            ({"sandbox": {"type": "process", "typo": 1}}, "invalid sandbox config"),
            ({"sandbox": {"type": "process", "memory": "lots"}}, "invalid sandbox config"),
            ({"limits": {"bogus": 1}}, "unknown grader.limits keys"),
            ({"limits": {"probe_timeout_seconds": 0}}, "positive numbers"),
            ({"contract": "cli"}, "disagrees with probes.json"),
        ],
    )
    def test_bad_grader_config_is_infra(
        self, tmp_path: Path, subject_factory: Any, cfg_extra: dict[str, Any], needle: str
    ) -> None:
        grading, repo = _stage(tmp_path, subject_factory)
        extra = {"sandbox": dict(PROCESS_SANDBOX), **cfg_extra}
        with pytest.raises(GraderError, match=needle):
            BlackboxGrader().grade(
                GraderConfig(type=BLACKBOX_GRADER_TYPE, **extra), Ctx(str(repo), str(grading))
            )

    def test_docker_requested_but_unavailable_is_infra(
        self, tmp_path: Path, subject_factory: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sb, "docker_available", lambda: False)
        grading, repo = _stage(tmp_path, subject_factory)
        with pytest.raises(GraderError, match="unreachable"):
            BlackboxGrader().grade(_cfg(sandbox={"type": "docker"}), Ctx(str(repo), str(grading)))

    def test_auto_falls_back_to_process_and_says_so(
        self, tmp_path: Path, subject_factory: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sb, "docker_available", lambda: False)
        grading, repo = _stage(tmp_path, subject_factory)
        result = BlackboxGrader().grade(
            _cfg(sandbox={"type": "auto"}), Ctx(str(repo), str(grading))
        )
        assert result.detail["sandbox"] == "process" and result.solved is True


# ---------------------------------------------------------------------------
# jsonl + cli contracts, no perf group (weights renormalise)
# ---------------------------------------------------------------------------

JSONL_REFERENCE = """\
import json, sys
store = {}
for line in sys.stdin:
    req = json.loads(line)
    op = req.get("op")
    if op == "ping":
        out = {"ok": True}
    elif op == "create":
        store[len(store) + 1] = req["body"]
        out = {"ok": True, "id": len(store)}
    elif op == "get":
        if req["id"] in store:
            out = {"ok": True, "body": store[req["id"]]}
        else:
            out = {"ok": False, "error": "not_found"}
    elif op == "count":
        out = {"ok": True, "n": len(store)}
    else:
        out = {"ok": False, "error": "unknown_op"}
    print(json.dumps(out), flush=True)
"""

CLI_REFERENCE = """\
import json, os, sys
STATE = "state.json"
def load():
    return json.load(open(STATE)) if os.path.exists(STATE) else []
args = sys.argv[1:]
if args[:1] == ["add"] and len(args) == 2:
    items = load(); items.append(args[1]); json.dump(items, open(STATE, "w"))
    print(json.dumps({"added": args[1]}))
elif args[:1] == ["list"]:
    print(json.dumps({"items": load()}))
elif args[:1] == ["echo"]:
    sys.stdout.write(sys.stdin.read())
else:
    sys.stderr.write("usage: add NAME | list | echo\\n"); sys.exit(2)
"""


def _jsonl_probes() -> dict[str, Any]:
    def req(**body: Any) -> dict[str, Any]:
        return {"json": body}

    return {
        "version": "1.0",
        "contract": "jsonl",
        "startup_timeout_seconds": 15,
        "groups": {
            "core": [
                {
                    "id": "create-then-get",
                    "steps": [
                        {
                            "op": req(op="create", body={"name": "a-{{rand}}"}),
                            "expect": {"json": {"ok": True, "id": {"$type": "integer"}}},
                            "save": {"id": "$.id"},
                        },
                        {
                            "op": req(op="get", id="{{id}}"),  # typed: must arrive as an int
                            "expect": {"json": {"ok": True, "body": {"name": {"$regex": "^a-"}}}},
                        },
                    ],
                },
                {
                    "id": "fresh-state-per-probe",
                    "steps": [
                        {"op": req(op="count"), "expect": {"json": {"ok": True, "n": 0}}},
                    ],
                },
                {
                    "id": "unknown-op",
                    "steps": [
                        {
                            "op": req(op="zzz"),
                            "expect": {
                                "json": {"ok": False, "error": "unknown_op"},
                                "max_ms": 5000,
                            },
                        }
                    ],
                },
            ],
            "trap": [
                {
                    "id": "parallel-creates-all-land",
                    "trap": "lost_update",
                    "steps": [{"op": req(op="count"), "expect": {"json": {"n": 0}}}],
                    "parallel": [
                        [
                            {
                                "op": req(op="create", body={"w": k}),
                                "repeat": 3,
                                "expect": {"json": {"ok": True}},
                            }
                        ]
                        for k in range(2)
                    ],
                    "after": [{"op": req(op="count"), "expect": {"json": {"n": 6}}}],
                }
            ],
        },
    }


def _cli_probes() -> dict[str, Any]:
    def run(*args: str, stdin: str | None = None) -> dict[str, Any]:
        op: dict[str, Any] = {"args": list(args)}
        if stdin is not None:
            op["stdin"] = stdin
        return op

    return {
        "version": "1.0",
        "contract": "cli",
        "groups": {
            "core": [
                {
                    "id": "add-then-list",
                    "steps": [
                        {
                            "op": run("add", "x-{{rand}}"),
                            "expect": {
                                "exit_code": 0,
                                "stdout_contains": "added",
                                "json": {"added": {"$regex": "^x-"}},
                            },
                            "save": {"item": "$.added"},
                        },
                        {
                            "op": run("list"),
                            "expect": {
                                "exit_code": 0,
                                "json": {"items": {"$contains": {"$var": "item"}}},
                            },
                        },
                    ],
                },
                {
                    "id": "keep-state",
                    "steps": [{"op": run("add", "keep"), "expect": {"exit_code": 0}}],
                },
                {
                    "id": "state-persists-without-fresh",
                    "fresh_state": False,
                    "steps": [{"op": run("list"), "expect": {"json": {"items": ["keep"]}}}],
                },
                {
                    "id": "fresh-state-resets",
                    "steps": [{"op": run("list"), "expect": {"json": {"items": {"$len": 0}}}}],
                },
                {
                    "id": "stdin-is-passed",
                    "steps": [
                        {"op": run("echo", stdin="hello"), "expect": {"stdout_contains": "hello"}}
                    ],
                },
            ],
            "trap": [
                {
                    "id": "usage-error-exit-code",
                    "trap": "exit_codes",
                    "steps": [{"op": run("bogus"), "expect": {"exit_code": 2}}],
                }
            ],
        },
    }


def _mini(
    tmp_path: Path, subject_factory: Any, probes: dict[str, Any], app: str
) -> tuple[Path, Path]:
    grading = tmp_path / "grading"
    (grading / "reference").mkdir(parents=True)
    (grading / "probes.json").write_text(json.dumps(probes))
    (grading / "instance.json").write_text(
        json.dumps({"version": "1.0", "language": "python", "contract": probes["contract"]})
    )
    (grading / "reference" / "app.py").write_text(app)
    (grading / "reference" / "run.sh").write_text('#!/bin/sh\nexec python3 app.py "$@"\n')
    repo = tmp_path / "repo"
    repo.mkdir()
    _copy_reference(tmp_path, subject_factory, grading, repo)
    return grading, repo


class TestOtherContracts:
    def test_jsonl_contract(self, tmp_path: Path, subject_factory: Any) -> None:
        grading, repo = _mini(tmp_path, subject_factory, _jsonl_probes(), JSONL_REFERENCE)
        result = BlackboxGrader().grade(_cfg(), Ctx(str(repo), str(grading)))
        assert result.solved is True and result.score == 1.0, result.raw_tail
        assert result.detail["perf"] is None
        assert result.detail["score_breakdown"]["perf"]["effective_weight"] == 0.0

    def test_jsonl_wrong_artefact_fails_probes(self, tmp_path: Path, subject_factory: Any) -> None:
        grading, repo = _mini(tmp_path, subject_factory, _jsonl_probes(), JSONL_REFERENCE)
        # an artefact whose `count` op lies (state leaks / writes lost)
        _patch(
            repo / "app.py", 'out = {"ok": True, "n": len(store)}', 'out = {"ok": True, "n": 999}'
        )
        result = BlackboxGrader().grade(_cfg(), Ctx(str(repo), str(grading)))
        by_id = _by_id(result.detail)
        assert by_id["fresh-state-per-probe"]["passed"] is False
        assert by_id["parallel-creates-all-land"]["passed"] is False
        assert result.solved is False

    def test_jsonl_artefact_that_never_pings(self, tmp_path: Path, subject_factory: Any) -> None:
        grading, repo = _mini(tmp_path, subject_factory, _jsonl_probes(), JSONL_REFERENCE)
        (repo / "app.py").write_text("import time\ntime.sleep(60)\n")
        cfg = _cfg(sandbox={"type": "process", "startup_timeout_seconds": 1.0})
        result = BlackboxGrader().grade(cfg, Ctx(str(repo), str(grading)))
        assert (
            result.solved is False
            and result.detail["artefact_error"] == "artefact never became ready"
        )

    def test_cli_contract_with_fresh_state_semantics(
        self, tmp_path: Path, subject_factory: Any
    ) -> None:
        grading, repo = _mini(tmp_path, subject_factory, _cli_probes(), CLI_REFERENCE)
        result = BlackboxGrader().grade(_cfg(), Ctx(str(repo), str(grading)))
        assert result.solved is True and result.score == 1.0, result.raw_tail
        assert len(result.detail["probe_results"]) == 6

    def test_cli_wrong_exit_code_fails_trap(self, tmp_path: Path, subject_factory: Any) -> None:
        grading, repo = _mini(tmp_path, subject_factory, _cli_probes(), CLI_REFERENCE)
        _patch(repo / "app.py", "sys.exit(2)", "sys.exit(1)")
        result = BlackboxGrader().grade(_cfg(), Ctx(str(repo), str(grading)))
        assert _by_id(result.detail)["usage-error-exit-code"]["passed"] is False
        assert result.detail["score_breakdown"]["core"]["fraction"] == 1.0
        assert result.score == pytest.approx(0.5 / 0.85, abs=1e-5)  # perf absent -> renormalised

    def test_cli_missing_run_sh(self, tmp_path: Path, subject_factory: Any) -> None:
        grading, repo = _mini(tmp_path, subject_factory, _cli_probes(), CLI_REFERENCE)
        (repo / "run.sh").unlink()
        result = BlackboxGrader().grade(_cfg(), Ctx(str(repo), str(grading)))
        assert result.solved is False and "run.sh not found" in result.detail["artefact_error"]


# ---------------------------------------------------------------------------
# end to end through runner.run_suite
# ---------------------------------------------------------------------------


def _blackbox_suite(tmp_path: Path, suite_factory: Any, assets_src: Path) -> Path:
    assets = tmp_path / "assets" / "blackbox"
    shutil.copytree(assets_src, assets)
    task = {
        "id": "bb-widgets",
        "category": "feature",
        "instruction": "tasks/bb-widgets/instruction.md",
        "fixture": "tasks/bb-widgets/fixture",
        "grader": {
            "type": "blackbox",
            "assets": "assets/blackbox",
            "sandbox": {"type": "process"},
        },
        "timeout_seconds": 120,
        "tags": ["python"],
    }
    return suite_factory(suite_id="bb-mini", tasks=[task])


class TestRunSuiteE2E:
    def test_copy_reference_subject_solves_the_blackbox_task(
        self, tmp_path: Path, suite_factory: Any, subject_factory: Any
    ) -> None:
        suite = _blackbox_suite(tmp_path, suite_factory, FIXTURE)
        subject = subject_factory(
            subject_id="fake-ref", type_="fake", extra={"scripted_effect": "copy-reference"}
        )
        record = run_suite(suite, subject, out_dir=tmp_path / "out")
        task = record.tasks[0]
        assert task.solved is True, task.grader_raw_tail
        assert task.grader_type == "blackbox" and task.score >= 0.85
        assert task.grader_detail["sandbox"] == "process"
        assert task.grader_detail["reference_ok"] is True
        assert record.aggregate.solved == 1 and record.aggregate.infra_errors == 0

    def test_noop_subject_is_a_failed_task_not_an_infra_error(
        self, tmp_path: Path, suite_factory: Any, subject_factory: Any
    ) -> None:
        suite = _blackbox_suite(tmp_path, suite_factory, FIXTURE)
        subject = subject_factory(
            subject_id="fake-noop", type_="fake", extra={"scripted_effect": "noop"}
        )
        record = run_suite(suite, subject, out_dir=tmp_path / "out")
        task = record.tasks[0]
        assert task.solved is False and task.score == 0.0
        assert task.grader_detail["artefact_error"] == "run.sh not found in the artefact directory"
        assert record.aggregate.infra_errors == 0 and record.aggregate.attempted == 1

    def test_broken_reference_surfaces_as_an_infra_error(
        self, tmp_path: Path, suite_factory: Any, subject_factory: Any
    ) -> None:
        broken = tmp_path / "broken-assets"
        shutil.copytree(FIXTURE, broken)
        _patch(
            broken / "reference" / "server.py",
            "return self._send(201, item)",
            "return self._send(200, item)",
        )
        suite = _blackbox_suite(tmp_path, suite_factory, broken)
        subject = subject_factory(
            subject_id="fake-ref", type_="fake", extra={"scripted_effect": "copy-reference"}
        )
        record = run_suite(suite, subject, out_dir=tmp_path / "out")
        task = record.tasks[0]
        assert task.solved is False
        assert task.subject_status == "error" and "reference implementation fails" in (
            task.raw_error or ""
        )
        assert record.aggregate.infra_errors == 1 and record.aggregate.attempted == 0


# ---------------------------------------------------------------------------
# docker (opt-in)
# ---------------------------------------------------------------------------


@pytest.mark.docker
def test_copy_reference_is_solved_in_docker(tmp_path: Path, subject_factory: Any) -> None:
    sb.docker_available.cache_clear()
    if not sb.docker_available():
        pytest.skip("docker daemon not reachable")
    if not sb.image_present("python:3.12-slim"):
        pytest.skip("python:3.12-slim not pulled (docker pull python:3.12-slim)")
    grading, repo = _stage(tmp_path, subject_factory)
    cfg = _cfg(sandbox={"type": "docker"})
    result = BlackboxGrader().grade(cfg, Ctx(str(repo), str(grading)))
    d = result.detail
    assert result.solved is True, result.raw_tail
    assert d["sandbox"] == "docker" and d["image"] == "python:3.12-slim"
    assert d["perf"]["sandbox"] == "docker" and d["perf"]["image"] == "python:3.12-slim"
    assert d["score_breakdown"]["core"]["fraction"] == 1.0
    assert d["score_breakdown"]["trap"]["fraction"] == 1.0
    leftovers = sb._docker(
        ["ps", "-aq", "--filter", f"name={sb.CONTAINER_NAME_PREFIX}"]
    ).stdout.split()
    assert leftovers == [], "grading leaked containers"


class TestHiddenAssetsStayHidden:
    REPORTER = (
        "import json, os\n"
        'print(json.dumps({"env_has_instance": "INSTANCE_JSON" in os.environ,'
        ' "files": sorted(os.listdir("."))}))\n'
    )

    def test_artefact_gets_neither_the_instance_env_nor_the_instance_file(
        self, tmp_path: Path, subject_factory: Any
    ) -> None:
        probes = {
            "version": "1.0",
            "contract": "cli",
            "groups": {
                "core": [
                    {
                        "id": "no-hidden-assets",
                        "steps": [
                            {
                                "op": {"args": ["report"]},
                                "expect": {
                                    "json": {
                                        "env_has_instance": False,
                                        "files": {"$not": {"$contains": ".ao-instance.json"}},
                                    }
                                },
                            }
                        ],
                    }
                ]
            },
        }
        # The *reference* is a stub that answers the right thing unconditionally; the artefact
        # is a real reporter -- so it passes only if the grader really hides the instance.
        stub = 'import json\nprint(json.dumps({"env_has_instance": False, "files": []}))\n'
        grading, repo = _mini(tmp_path, subject_factory, probes, stub)
        (repo / "app.py").write_text(self.REPORTER)
        result = BlackboxGrader().grade(_cfg(), Ctx(str(repo), str(grading)))
        assert result.solved is True, result.raw_tail
        # sanity: the reporter really would have noticed the grader's copy
        art = tmp_path / "snapshot"
        art.mkdir()
        (art / "app.py").write_text(self.REPORTER)
        (art / ".ao-instance.json").write_text("{}")
        (art / "run.sh").write_text('exec python3 app.py "$@"\n')
        from agent_orchestrator.bench.sandbox import ProcessSandbox, SandboxConfig

        rc, out, _ = ProcessSandbox(SandboxConfig(type="process"), "python").run_cli(
            art, ["report"], None, {"INSTANCE_JSON": "x"}
        )
        report = json.loads(out)
        assert rc == 0 and report["env_has_instance"] is True
        assert ".ao-instance.json" in report["files"]
