"""T-Ov3Sbj: `ao_workflow` subject in template mode (`ao new <template> ... --run`) and the
`init_git` repo preparation. Fake tier: the `ao` subprocess is mocked; `init_git` runs real
local `git` in a temp dir (no network)."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from agent_orchestrator.bench import subjects
from agent_orchestrator.bench.spec import load_subject
from agent_orchestrator.bench.subjects import (
    GIT_ORIGIN_DIRNAME,
    TEMPLATE_INSTANCE_SLUG,
    AoWorkflowSubject,
    init_git_with_local_origin,
)
from tests.bench.test_subjects import (
    _make_ctx,
    _subject_spec,
    _task,
    _write_fake_run_state,
    _write_reposet_template,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
COMMITTED_OVERSEER_SUBJECT = REPO_ROOT / "benchmarks" / "subjects" / "ao-overseer-sonnet.json"


@pytest.fixture()
def binaries_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subjects.shutil, "which", lambda name: f"/usr/bin/{name}")


def _template_spec(base: Path, **extra: object) -> object:
    _write_reposet_template(base / "reposet.json", repo_set_id="bench-set")
    (base / "agents.json").write_text("{}")
    (base / "prompt.md").write_text("# Bench task\n")
    data: dict[str, object] = {
        "template": "overseer-runner",
        "template_params": {"run_budget_usd": "3", "final_push": "false"},
        "prompt_file": "prompt.md",
        "reposets": "reposet.json",
        "agents": "agents.json",
        "model": "claude-sonnet-5-5",
    }
    data.update(extra)
    return _subject_spec(subject_id="ao-overseer", subject_type="ao_workflow", **data)


def _capture_run(monkeypatch: pytest.MonkeyPatch, ws: Path) -> dict[str, object]:
    captured: dict[str, object] = {}

    def fake_run(argv, *, cwd, stdout_path, stderr_path, timeout_seconds, env=None):  # noqa: ANN001
        captured.update(argv=argv, env=env)
        stdout_path.write_text("")
        stderr_path.write_text("")
        _write_fake_run_state(ws, "o-abc123-bench", cost=1.25)
        return 0, False

    monkeypatch.setattr(subjects, "_run_with_timeout", fake_run)
    return captured


def test_template_mode_runs_ao_new_with_params_and_prompt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, binaries_present: None
) -> None:
    ctx = _make_ctx(tmp_path, subject_base_dir=tmp_path, budget_total=999)
    captured = _capture_run(monkeypatch, tmp_path)
    result = AoWorkflowSubject(_template_spec(tmp_path)).run(_task(), ctx)  # type: ignore[arg-type]

    assert result.status == "succeeded" and result.cost_usd == pytest.approx(1.25)
    argv = captured["argv"]
    assert isinstance(argv, list)
    assert argv[:6] == ["uv", "run", "ao", "new", "overseer-runner", TEMPLATE_INSTANCE_SLUG]
    assert argv[argv.index("--workspace") + 1] == ctx.workspace
    params = [argv[i + 1] for i, a in enumerate(argv) if a == "--param"]
    # repo_set defaults to the rendered reposet's first repo_set; the rest are sorted
    assert params == ["repo_set=bench-set", "final_push=false", "run_budget_usd=3"]
    assert argv[argv.index("--prompt-file") + 1] == str((tmp_path / "prompt.md").resolve())
    assert "--run" in argv and "--budget-total" not in argv  # ao new has no budget flag
    env = captured["env"]
    assert isinstance(env, dict) and env["AO_MODEL"] == "claude-sonnet-5-5"


def test_template_mode_requires_prompt_file(tmp_path: Path, binaries_present: None) -> None:
    ctx = _make_ctx(tmp_path, subject_base_dir=tmp_path)
    spec = _template_spec(tmp_path, prompt_file=None)
    result = AoWorkflowSubject(spec).run(_task(), ctx)  # type: ignore[arg-type]
    assert result.status == "error" and "prompt_file" in (result.raw_error or "")


def test_template_dir_relative_to_subject_is_passed_as_a_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, binaries_present: None
) -> None:
    (tmp_path / "my-template").mkdir()
    ctx = _make_ctx(tmp_path, subject_base_dir=tmp_path)
    captured = _capture_run(monkeypatch, tmp_path)
    AoWorkflowSubject(_template_spec(tmp_path, template="my-template")).run(_task(), ctx)  # type: ignore[arg-type]
    argv = captured["argv"]
    assert isinstance(argv, list) and argv[4] == str((tmp_path / "my-template").resolve())


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_init_git_makes_one_commit_with_a_local_origin(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    repo = ws / "repo"
    (repo / ".claude").mkdir(parents=True)
    (repo / ".claude" / "settings.json").write_text("{}")
    (repo / "INSTRUCTION.md").write_text("do it\n")
    origin = init_git_with_local_origin(repo, ws)

    assert origin == ws / GIT_ORIGIN_DIRNAME and origin.is_dir()
    assert repo not in origin.parents  # never inside the graded repo

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=repo, capture_output=True, text=True, check=True
        ).stdout.strip()

    assert git("branch", "--show-current") == "main"
    assert git("rev-list", "--count", "HEAD") == "1"
    assert git("rev-parse", "--abbrev-ref", "@{u}") == "origin/main"
    assert git("status", "--porcelain") == ""  # .claude/ is excluded, nothing untracked
    assert ".claude/settings.json" not in git("ls-files")
    assert (
        subprocess.run(
            ["git", "merge-base", "--is-ancestor", "HEAD", "origin/main"], cwd=repo
        ).returncode
        == 0
    )


def test_init_git_failure_is_a_subject_error_result(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, binaries_present: None
) -> None:
    def boom(*_a: object, **_k: object) -> Path:
        raise subprocess.CalledProcessError(128, ["git", "init"])

    monkeypatch.setattr(subjects, "init_git_with_local_origin", boom)
    ctx = _make_ctx(tmp_path, subject_base_dir=tmp_path)
    result = AoWorkflowSubject(_template_spec(tmp_path, init_git=True)).run(_task(), ctx)  # type: ignore[arg-type]
    assert result.status == "error" and "init_git failed" in (result.raw_error or "")


def test_committed_overseer_subject_is_sonnet_only_and_thin() -> None:
    spec = load_subject(COMMITTED_OVERSEER_SUBJECT)
    assert spec.type == "ao_workflow" and spec.template == "overseer-runner"
    assert spec.model == "claude-sonnet-5-5" and spec.init_git is True
    # sonnet only: no per-checkpoint / per-unit model override and no leverage-haiku mode
    for key in ("overseer_model", "default_unit_model", "leverage_haiku", "allowed_models"):
        assert key not in spec.template_params
    base = COMMITTED_OVERSEER_SUBJECT.parent
    agents = json.loads((base / "ao-overseer" / "agents.json").read_text())["agents"]
    for agent in agents.values():
        assert "--model" not in agent["command_template"]  # AO_MODEL applies to every agent
    prompt = (base / "ao-overseer" / "prompt.md").read_text()
    assert len(prompt) <= 600  # parity wrapper budget (benchmarks/parity/default)


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_init_git_on_an_existing_master_repo_with_an_origin(tmp_path: Path) -> None:
    """Tier-2 providers hand over a one-commit snapshot on `master` (T-Cb8Rk2 campaign bug)."""
    ws = tmp_path / "ws"
    repo = ws / "repo"
    repo.mkdir(parents=True)
    env_git = ["git", "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run(["git", "init", "-q", "-b", "master"], cwd=repo, check=True)
    (repo / "a.py").write_text("x = 1\n")
    subprocess.run([*env_git, "add", "-A"], cwd=repo, check=True)
    subprocess.run([*env_git, "commit", "-q", "-m", "snapshot"], cwd=repo, check=True)
    subprocess.run(["git", "remote", "add", "origin", "/nonexistent"], cwd=repo, check=True)
    (repo / "INSTRUCTION.md").write_text("do it\n")

    init_git_with_local_origin(repo, ws)

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=repo, capture_output=True, text=True, check=True
        ).stdout.strip()

    assert git("branch", "--show-current") == "main"
    assert git("rev-parse", "--abbrev-ref", "@{u}") == "origin/main"
    assert git("remote", "get-url", "origin") == str(ws / GIT_ORIGIN_DIRNAME)
    assert git("rev-list", "--count", "HEAD") == "2"  # snapshot + the hand-over commit
