"""Security-hardening tests for the usefulness-signals stack (E-Us9Kd4 review fixes)."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from agent_orchestrator import feedback as fb
from agent_orchestrator import survival
from agent_orchestrator.feedback import FeedbackError, add_feedback, validate_run_id
from agent_orchestrator.implicit_signals import implicit_signals
from agent_orchestrator.models import RunIntegrationState, RunState, TaskRunState
from agent_orchestrator.survival import compute_survival, is_valid_sha

from .test_survival import KEY, commit, git, iso_state, lines, make_repo, one_task

RUN = "wf-20260101T000000Z"


@pytest.fixture(autouse=True)
def _isolated_git_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("AO_STATE_DIR", str(tmp_path / "ao-state"))


# --- (1) bounded git output + wall-clock budget ------------------------------------------


class TestOutputCap:
    def test_oversized_diff_becomes_truncated(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = make_repo(tmp_path)
        sha = commit(repo, {"big.py": lines("alpha", 3000)})  # diff well over the tiny cap
        monkeypatch.setattr(survival, "MAX_DIFF_BYTES", 10_000)
        row = one_task(compute_survival([iso_state(repo, sha)], str(repo)))
        assert row.truncated == [f"{survival.TRUNC_BYTES}>10000"]
        assert row.lines_added == 0

    def test_under_cap_is_unaffected(self, tmp_path: Path) -> None:
        repo = make_repo(tmp_path)
        sha = commit(repo, {"a.py": lines("alpha", 12)})
        row = one_task(compute_survival([iso_state(repo, sha)], str(repo)))
        assert row.truncated == [] and row.lines_survived == 12

    def test_injected_runner_output_is_capped_too(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = make_repo(tmp_path)
        sha = commit(repo, {"a.py": lines("alpha", 500)})
        monkeypatch.setattr(survival, "MAX_DIFF_BYTES", 2_000)
        import subprocess

        def runner(argv, *, cwd, env, timeout):
            return subprocess.run(argv, cwd=cwd, env=env, timeout=timeout, capture_output=True)

        row = one_task(compute_survival([iso_state(repo, sha)], str(repo), runner=runner))
        assert row.truncated and row.truncated[0].startswith("bytes>")

    def test_oversized_blob_skips_file_not_run(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = make_repo(tmp_path)
        sha = commit(repo, {"a.py": lines("alpha", 12)})
        orig = survival._Git.run

        def run(self, cwd, args):
            if args[0] == "cat-file":
                raise survival._OutputTooLarge
            return orig(self, cwd, args)

        monkeypatch.setattr(survival._Git, "run", run)
        row = one_task(compute_survival([iso_state(repo, sha)], str(repo)))
        assert row.truncated and row.lines_added == 12 and row.lines_survived == 0


class TestBudget:
    def test_deadline_exceeded_is_unavailable_not_raise(self, tmp_path: Path) -> None:
        repo = make_repo(tmp_path)
        sha = commit(repo, {"a.py": lines("alpha", 12)})
        ticks = iter(range(0, 10_000, 100))  # each clock read jumps 100s; budget is 20s

        report = compute_survival(
            [iso_state(repo, sha)], str(repo), clock=lambda: float(next(ticks)), budget_s=20.0
        )
        assert report.unavailable and "budget" in report.unavailable
        assert one_task(report).lines_added == 0

    def test_within_budget_is_normal(self, tmp_path: Path) -> None:
        repo = make_repo(tmp_path)
        sha = commit(repo, {"a.py": lines("alpha", 12)})
        report = compute_survival([iso_state(repo, sha)], str(repo), clock=lambda: 0.0)
        assert report.unavailable is None
        assert one_task(report).lines_survived == 12


# --- (3) shas from state.json -------------------------------------------------------------


class TestShaValidation:
    @pytest.mark.parametrize(
        "value", ["abc123\n", "--output=/tmp/x", "ABCDEF1", "abc12", "g" * 10, "a" * 65, "", "a b"]
    )
    def test_invalid(self, value: str) -> None:
        assert not is_valid_sha(value)

    def test_valid(self) -> None:
        assert is_valid_sha("abcdef1") and is_valid_sha("0" * 40) and is_valid_sha("f" * 64)

    @pytest.mark.parametrize("bad", ["--output=/tmp/pwned", "HEAD", "abc123\n", "x" * 40])
    def test_bad_squash_sha_is_unavailable_low_confidence(self, tmp_path: Path, bad: str) -> None:
        repo = make_repo(tmp_path)
        report = compute_survival([iso_state(repo, bad)], str(repo))
        row = one_task(report)
        assert row.unavailable and "invalid commit id" in row.unavailable
        assert row.confidence == "low" and row.lines_added == 0
        assert not (tmp_path / "pwned").exists() and not Path("/tmp/pwned").exists()

    def test_bad_landed_range_base(self, tmp_path: Path) -> None:
        repo = make_repo(tmp_path)
        head = commit(repo, {"a.py": lines("alpha", 12)})
        st = iso_state(repo, head)
        st.task_integration["t1"].landed_ranges = {KEY: [["--output=x", head]]}
        row = one_task(compute_survival([st], str(repo)))
        assert row.unavailable and row.confidence == "low"

    def test_bad_serial_heads(self, tmp_path: Path) -> None:
        repo = make_repo(tmp_path)
        st = RunState(
            run_id="r1",
            workflow_id="wf",
            repo_set="rs",
            started_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T01:00:00+00:00",
            git_repos={KEY: str(repo)},
            integration=RunIntegrationState(active=False),
            git_start_heads={KEY: git(repo, "rev-parse", "HEAD")},
            tasks={
                "t": TaskRunState(
                    status="succeeded",
                    started_at="2026-01-01T00:10:00+00:00",
                    ended_at="2026-01-01T00:20:00+00:00",
                    end_heads={KEY: "--exec=evil"},
                )
            },
        )
        row = one_task(compute_survival([st], str(repo)), "t")
        assert row.unavailable and "invalid commit id" in row.unavailable

    def test_implicit_signals_bad_heads_do_not_raise(self, tmp_path: Path) -> None:
        repo = make_repo(tmp_path)
        good = git(repo, "rev-parse", "HEAD")
        st = iso_state(repo, good)
        st.integration = RunIntegrationState(
            active=True,
            heads={KEY: "--output=/tmp/pwned"},
            repos={KEY: f"{repo}/.git"},
            base_heads={KEY: good},
        )
        sig = implicit_signals(st, str(tmp_path))
        assert sig.landed is None and "invalid integration head" in (sig.landed_reason or "")
        st.integration.heads = {KEY: good}
        st.integration.base_heads = {KEY: "-p"}
        sig = implicit_signals(st, str(tmp_path))
        assert sig.followup_commits is None and "invalid base head" in (sig.followup_reason or "")


# --- (2) run id fullmatch -----------------------------------------------------------------


class TestRunId:
    @pytest.mark.parametrize("rid", ["r1\n", "r1\n\n", "\nr1", "r 1", "a" * 129])
    def test_rejected(self, rid: str) -> None:
        with pytest.raises(FeedbackError):
            validate_run_id(rid)

    def test_accepted(self) -> None:
        assert validate_run_id("r1") == "r1"


# --- (4) feedback file hardening ----------------------------------------------------------


@pytest.fixture
def ws(tmp_path: Path) -> str:
    run_dir = tmp_path / ".orchestrator" / "runs" / RUN
    run_dir.mkdir(parents=True)
    st = RunState(
        run_id=RUN,
        workflow_id="wf",
        repo_set="r",
        started_at="t",
        updated_at="t",
        tasks={"a": TaskRunState()},
    )
    (run_dir / "state.json").write_text(st.model_dump_json())
    return str(tmp_path)


def _run_dir(ws: str) -> Path:
    return Path(ws) / ".orchestrator" / "runs" / RUN


class TestFeedbackFileSafety:
    def test_symlinked_feedback_json_refused_without_abs_path(
        self, ws: str, tmp_path: Path
    ) -> None:
        target = tmp_path / "elsewhere.json"
        target.write_text('{"schema_version": 1, "entries": []}')
        (_run_dir(ws) / "feedback.json").symlink_to(target)
        with pytest.raises(FeedbackError) as ei:
            add_feedback(ws, RUN, scope="run", rating="good")
        assert "symlink" in str(ei.value)
        assert str(tmp_path) not in str(ei.value)
        assert target.read_text() == '{"schema_version": 1, "entries": []}'
        with pytest.raises(FeedbackError):
            fb.load_feedback(ws, RUN)

    def test_dangling_symlink_refused(self, ws: str, tmp_path: Path) -> None:
        (_run_dir(ws) / "feedback.json").symlink_to(tmp_path / "nonexistent")
        with pytest.raises(FeedbackError):
            add_feedback(ws, RUN, scope="run", rating="good")
        assert not (tmp_path / "nonexistent").exists()

    def test_planted_legacy_tmp_symlink_is_not_followed(self, ws: str, tmp_path: Path) -> None:
        victim = tmp_path / "victim.txt"
        victim.write_text("keep")
        (_run_dir(ws) / "feedback.tmp").symlink_to(victim)  # the old predictable tmp name
        add_feedback(ws, RUN, scope="run", rating="good")
        assert victim.read_text() == "keep"

    def test_tmp_creation_is_exclusive_nofollow(
        self, ws: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        victim = tmp_path / "victim.txt"
        victim.write_text("keep")
        # Force the "unique" name to be predictable and pre-plant a symlink there.
        monkeypatch.setattr(fb.secrets, "token_hex", lambda n: "fixed")
        (_run_dir(ws) / "feedback.json.fixed.tmp").symlink_to(victim)
        with pytest.raises(FeedbackError) as ei:
            add_feedback(ws, RUN, scope="run", rating="good")
        assert str(tmp_path) not in str(ei.value)
        assert victim.read_text() == "keep"
        assert not (_run_dir(ws) / "feedback.json").exists()

    def test_normal_write_leaves_no_tmp_and_is_owner_rw(self, ws: str) -> None:
        add_feedback(ws, RUN, scope="run", rating="good")
        add_feedback(ws, RUN, scope="run", rating="bad")
        names = sorted(p.name for p in _run_dir(ws).iterdir())
        assert not [n for n in names if n.endswith(".tmp")]
        mode = (_run_dir(ws) / "feedback.json").stat().st_mode & 0o777
        assert mode & 0o600 == 0o600 and not mode & 0o002
        assert len(fb.load_feedback(ws, RUN).entries) == 2

    def test_failed_write_cleans_tmp(self, ws: str, monkeypatch: pytest.MonkeyPatch) -> None:
        def boom(src, dst):
            raise OSError("nope")

        monkeypatch.setattr(fb.os, "replace", boom)
        with pytest.raises(OSError):
            add_feedback(ws, RUN, scope="run", rating="good")
        assert not [p for p in _run_dir(ws).iterdir() if p.name.endswith(".tmp")]


# --- (5) dashboard 500 body ---------------------------------------------------------------


class TestStoreErrorBody:
    def test_generic_body_full_detail_logged(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        pytest.importorskip("fastapi")
        from fastapi.testclient import TestClient

        from agent_orchestrator.project_config import ProjectConfig
        from agent_orchestrator.ui.app import API_PREFIX, STORE_ERROR_CLIENT_DETAIL, create_app
        from agent_orchestrator.ui.service import DashboardService, DashboardStoreError

        secret = f"corrupt feedback file {tmp_path}/secret/feedback.json: boom"
        svc = DashboardService(
            str(tmp_path), supervisor=MagicMock(), project_config=ProjectConfig()
        )

        def raise_store(run_id: str) -> dict:
            raise DashboardStoreError(secret)

        svc.get_feedback = raise_store  # type: ignore[method-assign]
        with caplog.at_level(logging.ERROR, logger="agent_orchestrator.ui.app"):
            with TestClient(create_app(svc), base_url="http://127.0.0.1") as c:
                resp = c.get(f"{API_PREFIX}/runs/{RUN}/feedback")
        assert resp.status_code == 500
        assert resp.json()["detail"] == STORE_ERROR_CLIENT_DETAIL
        assert str(tmp_path) not in resp.text
        assert secret in caplog.text
