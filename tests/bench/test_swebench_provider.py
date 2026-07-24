"""Tests for `agent_orchestrator.bench.swebench_provider` (E-Bt4Xk9 T-Sw5Hd9).

Network-free by default: `_repo_clone_url` is monkeypatched to point at a real, LOCAL
fake git repo built by these tests (a genuine `git init` + commits under `tmp_path`,
cloned via a plain local path -- git treats this identically to a `file://` remote,
no network involved) instead of a real `https://github.com/...` clone. `test_workspace
.py`'s own precedent (using the real repo-local `BENCH_WORKSPACE_ROOT`/
`playground/.tmp/` tree directly rather than always faking it) is mirrored here for
the git CACHE too: `SWEBENCH_REPO_CACHE_ROOT` is monkeypatched to a `tmp_path` so these
tests never touch (or depend on) a developer's real clone cache.

The one real-network+GitHub test is `swebench`-marked and opt-in (AO_E2E_SWEBENCH=1,
tests/bench/conftest.py's gate) -- it clones ONE real pinned instance from the
committed `benchmarks/suites/swe-verified-mini` suite end-to-end.
"""

from __future__ import annotations

import json
import subprocess
import uuid
from pathlib import Path

import pytest

import agent_orchestrator.bench.swebench_provider as provider_mod
from agent_orchestrator.bench.errors import SubjectError
from agent_orchestrator.bench.registries import WORKSPACE_PROVIDER_REGISTRY
from agent_orchestrator.bench.spec import BenchTask, GraderConfig, Source
from agent_orchestrator.bench.swebench_provider import (
    SWEBENCH_PROVIDER_TYPE,
    SweBenchWorkspaceProvider,
)
from agent_orchestrator.bench.workspace import BENCH_WORKSPACE_ROOT, materialize_workspace


def _git(args: list[str], cwd: Path) -> None:
    result = subprocess.run(
        ["git", "-c", "user.email=test@example.com", "-c", "user.name=Test", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"git {args} failed: {result.stderr}"


def _make_fake_origin(tmp_path: Path) -> tuple[Path, str, str]:
    """A real local git repo with two commits; returns (origin_dir, base_sha, head_sha)."""
    origin = tmp_path / "fake-origin"
    origin.mkdir()
    _git(["init", "-b", "main"], cwd=origin)
    (origin / "app.py").write_text("def add(a, b):\n    return a - b  # bug\n")
    _git(["add", "."], cwd=origin)
    _git(["commit", "-m", "base"], cwd=origin)
    base_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=origin, capture_output=True, text=True, check=True
    ).stdout.strip()

    (origin / "README.md").write_text("later change, not part of base_commit\n")
    _git(["add", "."], cwd=origin)
    _git(["commit", "-m", "later"], cwd=origin)
    head_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=origin, capture_output=True, text=True, check=True
    ).stdout.strip()
    return origin, base_sha, head_sha


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Every test in this module gets its own cache root and a clone URL redirected at
    a local fake origin -- never the real repo-local cache, never GitHub.
    """
    cache_root = tmp_path / "cache-root"
    monkeypatch.setattr(provider_mod, "SWEBENCH_REPO_CACHE_ROOT", cache_root)
    return cache_root


def _write_instances_json(suite_dir: Path, instances: list[dict]) -> None:
    suite_dir.mkdir(parents=True, exist_ok=True)
    (suite_dir / "instances.json").write_text(
        json.dumps({"dataset": "d", "revision": "r", "instances": instances})
    )


def _swebench_task(task_id: str, instance_id: str) -> BenchTask:
    return BenchTask(
        id=task_id,
        category="bugfix",
        instruction="tasks/x/instruction.md",
        source=Source(type=SWEBENCH_PROVIDER_TYPE, instance_id=instance_id),
        grader=GraderConfig(type="swebench"),
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_swebench_provider_registered_at_import_time() -> None:
    assert WORKSPACE_PROVIDER_REGISTRY[SWEBENCH_PROVIDER_TYPE] is SweBenchWorkspaceProvider


# ---------------------------------------------------------------------------
# _load_instances_index / validation failures (no git needed)
# ---------------------------------------------------------------------------


def test_prepare_missing_instances_json_raises(tmp_path: Path) -> None:
    suite_dir = tmp_path / "suite"
    suite_dir.mkdir()
    task = _swebench_task("t1", "some__instance-1")
    with pytest.raises(SubjectError, match="instances.json"):
        SweBenchWorkspaceProvider().prepare(task, tmp_path / "repo", suite_base_dir=suite_dir)


def test_prepare_malformed_instances_json_raises(tmp_path: Path) -> None:
    suite_dir = tmp_path / "suite"
    suite_dir.mkdir()
    (suite_dir / "instances.json").write_text(json.dumps({"dataset": "d"}))  # no 'instances'
    task = _swebench_task("t1", "some__instance-1")
    with pytest.raises(SubjectError, match="instances"):
        SweBenchWorkspaceProvider().prepare(task, tmp_path / "repo", suite_base_dir=suite_dir)


def test_prepare_unknown_instance_id_raises(tmp_path: Path) -> None:
    suite_dir = tmp_path / "suite"
    _write_instances_json(suite_dir, [{"instance_id": "other", "repo": "a/b", "base_commit": "x"}])
    task = _swebench_task("t1", "some__instance-1")
    with pytest.raises(SubjectError, match="not found"):
        SweBenchWorkspaceProvider().prepare(task, tmp_path / "repo", suite_base_dir=suite_dir)


def test_prepare_instance_missing_repo_or_base_commit_raises(tmp_path: Path) -> None:
    suite_dir = tmp_path / "suite"
    _write_instances_json(suite_dir, [{"instance_id": "some__instance-1", "repo": "a/b"}])
    task = _swebench_task("t1", "some__instance-1")
    with pytest.raises(SubjectError, match="repo.*base_commit|base_commit.*repo"):
        SweBenchWorkspaceProvider().prepare(task, tmp_path / "repo", suite_base_dir=suite_dir)


def test_prepare_wrong_source_type_raises(tmp_path: Path) -> None:
    task = BenchTask(
        id="t1",
        category="bugfix",
        instruction="tasks/x/instruction.md",
        source=Source(type="fixture"),
        grader=GraderConfig(type="swebench"),
    )
    with pytest.raises(SubjectError, match="swebench"):
        SweBenchWorkspaceProvider().prepare(task, tmp_path / "repo", suite_base_dir=tmp_path)


def test_prepare_missing_source_raises(tmp_path: Path) -> None:
    task = BenchTask(
        id="t1",
        category="bugfix",
        instruction="tasks/x/instruction.md",
        fixture="tasks/x/fixture",
        grader=GraderConfig(type="swebench"),
    )
    with pytest.raises(SubjectError, match="swebench"):
        SweBenchWorkspaceProvider().prepare(task, tmp_path / "repo", suite_base_dir=tmp_path)


def test_prepare_missing_instance_id_raises(tmp_path: Path) -> None:
    task = BenchTask(
        id="t1",
        category="bugfix",
        instruction="tasks/x/instruction.md",
        source=Source(type=SWEBENCH_PROVIDER_TYPE),  # no instance_id
        grader=GraderConfig(type="swebench"),
    )
    with pytest.raises(SubjectError, match="instance_id"):
        SweBenchWorkspaceProvider().prepare(task, tmp_path / "repo", suite_base_dir=tmp_path)


# ---------------------------------------------------------------------------
# Real (local, network-free) git clone/checkout
# ---------------------------------------------------------------------------


def test_prepare_checks_out_exactly_base_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    origin, base_sha, head_sha = _make_fake_origin(tmp_path)
    monkeypatch.setattr(provider_mod, "_repo_clone_url", lambda repo: str(origin))

    suite_dir = tmp_path / "suite"
    _write_instances_json(
        suite_dir,
        [{"instance_id": "fake__repo-1", "repo": "fake/repo", "base_commit": base_sha}],
    )
    task = _swebench_task("t1", "fake__repo-1")
    repo_dir = tmp_path / "ws" / "repo"

    SweBenchWorkspaceProvider().prepare(task, repo_dir, suite_base_dir=suite_dir)

    head = subprocess.run(
        ["git", "-C", str(repo_dir), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert head == base_sha
    assert head != head_sha  # sanity: base_commit is genuinely NOT the origin's HEAD
    assert not (repo_dir / "README.md").exists()  # only added in the later commit
    assert (repo_dir / "app.py").read_text() == "def add(a, b):\n    return a - b  # bug\n"

    status = subprocess.run(
        ["git", "-C", str(repo_dir), "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert status == ""  # clean working tree (AC3 baseline for T-Sg6Jf2's git diff)

    exclude = (repo_dir / ".git" / "info" / "exclude").read_text()
    assert "INSTRUCTION.md" in exclude


def test_prepare_reuses_cache_across_two_instances_of_same_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    origin, base_sha, head_sha = _make_fake_origin(tmp_path)
    monkeypatch.setattr(provider_mod, "_repo_clone_url", lambda repo: str(origin))

    suite_dir = tmp_path / "suite"
    _write_instances_json(
        suite_dir,
        [
            {"instance_id": "fake__repo-1", "repo": "fake/repo", "base_commit": base_sha},
            {"instance_id": "fake__repo-2", "repo": "fake/repo", "base_commit": head_sha},
        ],
    )

    SweBenchWorkspaceProvider().prepare(
        _swebench_task("t1", "fake__repo-1"), tmp_path / "ws1" / "repo", suite_base_dir=suite_dir
    )
    cache_dir = provider_mod.SWEBENCH_REPO_CACHE_ROOT / "fake__repo"
    assert cache_dir.is_dir()
    cache_mtime = (cache_dir / ".git").stat().st_mtime

    SweBenchWorkspaceProvider().prepare(
        _swebench_task("t2", "fake__repo-2"), tmp_path / "ws2" / "repo", suite_base_dir=suite_dir
    )
    # Cache was reused, not re-cloned (no change to the cache's own .git dir).
    assert (cache_dir / ".git").stat().st_mtime == cache_mtime

    head2 = subprocess.run(
        ["git", "-C", str(tmp_path / "ws2" / "repo"), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert head2 == head_sha
    assert (tmp_path / "ws2" / "repo" / "README.md").exists()


def test_materialize_workspace_end_to_end_with_swebench_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Full integration through `bench/workspace.py`'s public seam (not just calling
    the provider directly): a task with `source.type="swebench"` produces `ws/repo`
    checked out at base_commit AND `ws/repo/INSTRUCTION.md` (materialize_workspace's
    own, provider-independent responsibility).
    """
    origin, base_sha, _head_sha = _make_fake_origin(tmp_path)
    monkeypatch.setattr(provider_mod, "_repo_clone_url", lambda repo: str(origin))

    suite_dir = tmp_path / "suite"
    _write_instances_json(
        suite_dir,
        [{"instance_id": "fake__repo-1", "repo": "fake/repo", "base_commit": base_sha}],
    )
    (suite_dir / "tasks" / "fake__repo-1").mkdir(parents=True)
    instruction_path = suite_dir / "tasks" / "fake__repo-1" / "instruction.md"
    instruction_path.write_text("# Fix the bug\n")

    task = BenchTask(
        id="fake__repo-1",
        category="bugfix",
        instruction="tasks/fake__repo-1/instruction.md",
        source=Source(type=SWEBENCH_PROVIDER_TYPE, instance_id="fake__repo-1"),
        grader=GraderConfig(type="swebench"),
    )

    # materialize_workspace's sandbox guard requires `ws` under the REAL
    # BENCH_WORKSPACE_ROOT (mirrors test_workspace.py's own `bench_ws_target`
    # fixture convention) -- tmp_path is outside it and would be rejected.
    (BENCH_WORKSPACE_ROOT / "tests").mkdir(parents=True, exist_ok=True)
    ws_target = BENCH_WORKSPACE_ROOT / "tests" / f"ws-{uuid.uuid4().hex[:12]}"
    ctx = materialize_workspace(
        task,
        ws_target,
        suite_base_dir=suite_dir,
        timeout_seconds=60,
    )
    repo_dir = Path(ctx.repo_dir)
    head = subprocess.run(
        ["git", "-C", str(repo_dir), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert head == base_sha
    assert (repo_dir / "INSTRUCTION.md").read_text() == "# Fix the bug\n"


# ---------------------------------------------------------------------------
# Opt-in real test (swebench marker) -- real GitHub clone of ONE pinned instance from
# the committed swe-verified-mini suite.
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]
_COMMITTED_SUITE_DIR = _REPO_ROOT / "benchmarks" / "suites" / "swe-verified-mini"


@pytest.mark.swebench
@pytest.mark.skipif(
    not _COMMITTED_SUITE_DIR.is_dir(), reason="swe-verified-mini not generated in this checkout"
)
def test_real_prepare_checks_out_one_pinned_instance() -> None:
    """Opt-in (AO_E2E_SWEBENCH=1): real GitHub clone + checkout of the smallest
    committed instance (psf__requests-2931), through the real, un-monkeypatched
    provider + the real repo-local cache -- proves the actual production path end to
    end (this task's TASK.md AC3 / this task's deliverable #5 real test).
    """
    from agent_orchestrator.bench.spec import load_suite
    from agent_orchestrator.bench.workspace import BENCH_WORKSPACE_ROOT

    suite = load_suite(_COMMITTED_SUITE_DIR / "suite.json")
    task = suite.task("psf__requests-2931")
    instances = json.loads((_COMMITTED_SUITE_DIR / "instances.json").read_text())
    expected_base_commit = next(
        i["base_commit"] for i in instances["instances"] if i["instance_id"] == "psf__requests-2931"
    )

    ws = BENCH_WORKSPACE_ROOT / "tests" / "swebench-real-opt-in" / task.id
    ctx = materialize_workspace(
        task,
        ws,
        suite_base_dir=_COMMITTED_SUITE_DIR.resolve(),
        timeout_seconds=120,
    )
    repo_dir = Path(ctx.repo_dir)
    head = subprocess.run(
        ["git", "-C", str(repo_dir), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert head == expected_base_commit
    assert (repo_dir / "INSTRUCTION.md").is_file()
    assert "resolve this issue" in (repo_dir / "INSTRUCTION.md").read_text()
