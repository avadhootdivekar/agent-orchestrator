"""Core seams for the discriminating-suite work (E-Bn7Dsc): grading_dir separation,
grader assets, parity bundle, infra-aware aggregate, FakeSubject copy-reference, grader
command placeholders, repeat-aware run ids. Fake-tier only (no network, no LLM).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agent_orchestrator.bench import parity as parity_mod
from agent_orchestrator.bench.errors import GraderError
from agent_orchestrator.bench.graders import expand_command
from agent_orchestrator.bench.metrics import INFRA_ERROR_STATUS, TaskMetric, aggregate
from agent_orchestrator.bench.registries import register_workspace_provider
from agent_orchestrator.bench.runner import compute_bench_run_id, run_suite
from agent_orchestrator.bench.spec import load_subject, load_suite
from agent_orchestrator.bench.subjects import FakeSubject
from agent_orchestrator.bench.workspace import (
    BENCH_WORKSPACE_ROOT,
    RunContext,
    WorkspaceProvider,
    materialize_workspace,
)
from agent_orchestrator.errors import SpecValidationError


def _ws(tmp_path: Path, name: str) -> Path:
    # materialize_workspace guards `ws` under BENCH_WORKSPACE_ROOT (repo-local, gitignored).
    return BENCH_WORKSPACE_ROOT / "_test_core_seams" / tmp_path.name / name


def _write_parity(root: Path, *, md: str = "# shared\n", skill: str = "s1") -> Path:
    bundle = root / "parity"
    (bundle / "claude" / "skills" / skill).mkdir(parents=True)
    (bundle / "claude" / "skills" / skill / "SKILL.md").write_text("skill body\n")
    (bundle / "CLAUDE.md").write_text(md)
    (bundle / "parity.json").write_text(
        json.dumps(
            {
                "claude_dir": "claude",
                "claude_md": "CLAUDE.md",
                "env": {"BENCH_PARITY_KNOB": "1"},
                "max_turns": 7,
                "permission_mode": "bypassPermissions",
            }
        )
    )
    return bundle


# ---------------------------------------------------------------------------
# grading_dir + assets
# ---------------------------------------------------------------------------


class TestGradingDir:
    def test_assets_copied_into_grading_dir_not_repo(self, tmp_path: Path, suite_factory) -> None:
        suite_path = suite_factory()
        suite_dir = suite_path.parent
        assets = suite_dir / "tasks" / "bugfix-off-by-one" / "grading"
        assets.mkdir(parents=True)
        (assets / "hidden_test.py").write_text("def test_hidden(): pass\n")
        data = json.loads(suite_path.read_text())
        data["tasks"][0]["grader"]["assets"] = "tasks/bugfix-off-by-one/grading"
        suite_path.write_text(json.dumps(data))
        suite = load_suite(suite_path)
        grading = tmp_path / "grading"
        ctx = materialize_workspace(
            suite.tasks[0],
            _ws(tmp_path, "ws"),
            suite_base_dir=suite_dir,
            timeout_seconds=5,
            grading_dir=grading,
        )
        assert ctx.grading_dir == str(grading)
        assert (grading / "hidden_test.py").is_file()
        assert not list(Path(ctx.repo_dir).rglob("hidden_test.py"))
        # grading_dir is never under the agent-visible workspace
        assert not str(grading).startswith(ctx.workspace)

    def test_missing_assets_dir_rejected_at_load(self, suite_factory) -> None:
        suite_path = suite_factory()
        data = json.loads(suite_path.read_text())
        data["tasks"][0]["grader"]["assets"] = "tasks/nope"
        suite_path.write_text(json.dumps(data))
        with pytest.raises(SpecValidationError, match="assets"):
            load_suite(suite_path)

    def test_legacy_provider_without_grading_dir_kwarg_still_works(
        self, tmp_path: Path, suite_factory
    ) -> None:
        calls: list[str] = []

        class Legacy(WorkspaceProvider):
            def prepare(self, task, repo_dir, *, suite_base_dir, subject_base_dir=None) -> None:
                repo_dir.mkdir(parents=True)
                calls.append(task.id)

        register_workspace_provider("legacy-test", Legacy)
        suite_path = suite_factory()
        suite = load_suite(suite_path)
        from agent_orchestrator.bench.spec import Source

        task = suite.tasks[0].model_copy(update={"source": Source(type="legacy-test")})
        ctx = materialize_workspace(
            task,
            _ws(tmp_path, "legacy"),
            suite_base_dir=suite_path.parent,
            timeout_seconds=5,
            grading_dir=tmp_path / "g",
        )
        assert calls == [task.id]
        assert Path(ctx.grading_dir or "").is_dir()

    def test_new_fields_default_and_language_validated(self, suite_factory) -> None:
        suite_path = suite_factory()
        suite = load_suite(suite_path)
        t = suite.tasks[0]
        assert t.enabled is True and t.family is None and t.language is None
        data = json.loads(suite_path.read_text())
        data["tasks"][0]["language"] = "cobol"
        suite_path.write_text(json.dumps(data))
        with pytest.raises(SpecValidationError):
            load_suite(suite_path)


# ---------------------------------------------------------------------------
# parity
# ---------------------------------------------------------------------------


class TestParity:
    def test_fingerprint_changes_with_file_content_not_path(self, tmp_path: Path) -> None:
        b1 = parity_mod.load_parity(_write_parity(tmp_path / "a"))
        b2 = parity_mod.load_parity(_write_parity(tmp_path / "b"))
        assert parity_mod.parity_fingerprint(b1) == parity_mod.parity_fingerprint(b2)
        b3 = parity_mod.load_parity(_write_parity(tmp_path / "c", md="# different\n"))
        assert parity_mod.parity_fingerprint(b3) != parity_mod.parity_fingerprint(b1)

    def test_apply_writes_claude_md_to_workspace_and_claude_dir_to_repo(
        self, tmp_path: Path
    ) -> None:
        bundle = parity_mod.load_parity(_write_parity(tmp_path))
        ws = tmp_path / "ws"
        repo = ws / "repo"
        (repo / ".git" / "info").mkdir(parents=True)
        parity_mod.apply_parity(bundle, repo_dir=repo, workspace=ws)
        assert (ws / "CLAUDE.md").read_text() == "# shared\n"
        assert (repo / ".claude" / "skills" / "s1" / "SKILL.md").is_file()
        assert not (repo / "CLAUDE.md").exists()  # the repo's own tree is untouched
        assert "/.claude/" in (repo / ".git" / "info" / "exclude").read_text()

    def test_missing_bundle_path_is_spec_error(self, tmp_path: Path) -> None:
        with pytest.raises(SpecValidationError):
            parity_mod.load_parity(tmp_path / "nope")

    def test_materialize_applies_parity_and_records_fingerprint(
        self, tmp_path: Path, suite_factory
    ) -> None:
        suite_path = suite_factory()
        suite = load_suite(suite_path)
        bundle = parity_mod.load_parity(_write_parity(tmp_path))
        ctx = materialize_workspace(
            suite.tasks[0],
            _ws(tmp_path, "p"),
            suite_base_dir=suite_path.parent,
            timeout_seconds=5,
            grading_dir=tmp_path / "g",
            parity=bundle,
        )
        assert ctx.parity_fingerprint == parity_mod.parity_fingerprint(bundle)
        assert ctx.env == {"BENCH_PARITY_KNOB": "1"}
        assert ctx.permission_mode == "bypassPermissions"
        assert (Path(ctx.workspace) / "CLAUDE.md").is_file()
        assert (Path(ctx.repo_dir) / ".claude" / "skills" / "s1" / "SKILL.md").is_file()

    def test_run_suite_records_parity_and_max_turns_from_bundle(
        self, tmp_path: Path, suite_factory, subject_factory
    ) -> None:
        suite_path = suite_factory()
        subject_path = subject_factory(type_="fake", extra={"scripted_effect": "noop"})
        bundle_dir = _write_parity(tmp_path)
        clock = lambda: datetime(2026, 1, 2, tzinfo=UTC)  # noqa: E731
        record = run_suite(
            suite_path, subject_path, out_dir=tmp_path / "out", clock=clock, parity=bundle_dir
        )
        assert record.parity_fingerprint == parity_mod.parity_fingerprint(
            parity_mod.load_parity(bundle_dir)
        )
        assert record.repeat == 1
        # suite-level `parity` field is honoured too, and feeds the run fingerprint
        data = json.loads(suite_path.read_text())
        data["parity"] = str(bundle_dir)
        suite_path.write_text(json.dumps(data))
        record2 = run_suite(suite_path, subject_path, out_dir=tmp_path / "out2", clock=clock)
        assert record2.parity_fingerprint == record.parity_fingerprint
        record3 = run_suite(suite_path, subject_path, out_dir=tmp_path / "out3", clock=clock)
        assert record3.config_fingerprint == record2.config_fingerprint


# ---------------------------------------------------------------------------
# repeats, disabled tasks, aggregate, fake copy-reference, command placeholders
# ---------------------------------------------------------------------------


class TestMisc:
    def test_run_id_repeat_suffix(self, suite_factory, subject_factory) -> None:
        suite = load_suite(suite_factory())
        subject = load_subject(subject_factory(type_="fake"))
        clock = lambda: datetime(2026, 1, 2, tzinfo=UTC)  # noqa: E731
        base = compute_bench_run_id(suite, subject, clock)
        assert compute_bench_run_id(suite, subject, clock, 1) == base
        assert compute_bench_run_id(suite, subject, clock, 3) == base + "-r3"

    def test_disabled_task_is_never_run(
        self, tmp_path: Path, suite_factory, subject_factory
    ) -> None:
        suite_path = suite_factory()
        data = json.loads(suite_path.read_text())
        data["tasks"][0]["enabled"] = False
        suite_path.write_text(json.dumps(data))
        record = run_suite(
            suite_path,
            subject_factory(type_="fake", extra={"scripted_effect": "noop"}),
            out_dir=tmp_path,
        )
        assert record.tasks == [] and record.ended_at is None

    def test_aggregate_excludes_infra_errors_from_attempted(self) -> None:
        def m(status: str, solved: bool) -> TaskMetric:
            return TaskMetric(
                subject_id="s",
                task_id=f"t-{status}-{solved}",
                domain="d",
                category="bugfix",
                solved=solved,
                score=1.0 if solved else 0.0,
                wall_clock_seconds=1.0,
                cost_usd=0.1,
                subject_status=status,
                grader_type="fake",
                workspace="",
                config_fingerprint="f",
                started_at="x",
                ended_at="y",
            )

        agg = aggregate(
            [
                m("succeeded", True),
                m("failed", False),
                m(INFRA_ERROR_STATUS, False),
                m("skipped_budget", False),
            ]
        )
        assert agg.total == 4 and agg.solved == 1
        assert agg.infra_errors == 1 and agg.attempted == 2
        assert agg.solve_rate_attempted == 0.5 and agg.mean_score_attempted == 0.5

    def test_fake_copy_reference_overlays_from_grading_dir(
        self, tmp_path: Path, subject_factory
    ) -> None:
        spec = load_subject(
            subject_factory(type_="fake", extra={"scripted_effect": "copy-reference"})
        )
        repo = tmp_path / "repo"
        repo.mkdir()
        grading = tmp_path / "grading"
        (grading / "reference" / "pkg").mkdir(parents=True)
        (grading / "reference" / "run.sh").write_text("#!/bin/sh\n")
        (grading / "reference" / "pkg" / "a.py").write_text("x=1\n")
        ctx = RunContext(
            workspace=str(tmp_path),
            repo_dir=str(repo),
            instruction_path="i",
            capture_dir=str(tmp_path / "c"),
            timeout_seconds=5,
            grading_dir=str(grading),
        )
        sr = FakeSubject(spec).run(None, ctx)  # type: ignore[arg-type]
        assert sr.status == "succeeded"
        assert (repo / "run.sh").is_file() and (repo / "pkg" / "a.py").read_text() == "x=1\n"

    def test_expand_command_placeholders(self, tmp_path: Path) -> None:
        ctx = RunContext(
            workspace="w",
            repo_dir="/r",
            instruction_path="i",
            capture_dir="c",
            timeout_seconds=1,
            grading_dir="/g",
        )
        assert (
            expand_command("python {grading_dir}/run.py --repo {repo_dir} {x}", ctx)
            == "python /g/run.py --repo /r {x}"
        )
        ctx_no = RunContext(
            workspace="w", repo_dir="/r", instruction_path="i", capture_dir="c", timeout_seconds=1
        )
        with pytest.raises(GraderError):
            expand_command("python {grading_dir}/run.py", ctx_no)
