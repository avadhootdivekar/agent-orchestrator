"""T-Pf4Mix: Exercism (Aider polyglot) provider, grader parsers and importer.

Fake tier: a local git repo shaped like `Aider-AI/polyglot-benchmark` stands in for the network
source; the Docker grader itself is exercised by the opt-in `docker` test at the bottom.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from agent_orchestrator.bench import exercism as ex
from agent_orchestrator.bench.spec import BenchTask, GraderConfig, Source, load_suite

pytestmark = pytest.mark.usefixtures("isolated_repo_cache")

PY_EXERCISE = {
    ".meta/config.json": json.dumps(
        {
            "files": {
                "solution": ["two_fer.py"],
                "test": ["two_fer_test.py"],
                "example": [".meta/example.py"],
            }
        }
    ),
    ".meta/example.py": "def two_fer(name='you'):\n    return f'One for {name}, one for me.'\n",
    ".docs/instructions.md": "# Two-fer\n\nSay `One for X, one for me.`\n",
    ".docs/instructions.append.md": "Default name is `you`.\n",
    "two_fer.py": "def two_fer(name='you'):\n    pass\n",
    "two_fer_test.py": (
        "from two_fer import two_fer\n\n"
        "def test_default():\n    assert two_fer() == 'One for you, one for me.'\n\n"
        "def test_name():\n    assert two_fer('Bo') == 'One for Bo, one for me.'\n"
    ),
}
GO_EXERCISE = {
    ".meta/config.json": json.dumps(
        {
            "files": {
                "solution": ["leap.go"],
                "test": ["leap_test.go"],
                "editor": ["cases_test.go"],
                "example": [".meta/example.go"],
            }
        }
    ),
    ".meta/example.go": "package leap\n",
    ".docs/instructions.md": "# Leap\n",
    "leap.go": "package leap\n",
    "leap_test.go": "package leap\n",
    "cases_test.go": "package leap\n// the test cases\n",
    "go.mod": "module leap\n",
}
RUST_WITH_DEP = {
    ".meta/config.json": json.dumps(
        {
            "files": {
                "solution": ["src/lib.rs"],
                "test": ["tests/a.rs"],
                "example": [".meta/example.rs"],
            }
        }
    ),  # fmt: skip
    ".meta/example.rs": "use rand::Rng;\npub fn f() {}\n",
    "Cargo.toml": '[package]\nname = "a"\nversion = "0.1.0"\n',
    "src/lib.rs": "pub fn f() {}\n",
    "tests/a.rs": "#[test]\nfn t() {}\n",
}
RUST_CLEAN = {
    **RUST_WITH_DEP,
    ".meta/example.rs": "use std::collections::HashMap;\npub fn f() {}\n",
}


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


@pytest.fixture()
def fake_polyglot(tmp_path: Path) -> tuple[str, str]:
    repo = tmp_path / "polyglot"
    layout = {
        "python/exercises/practice/two-fer": PY_EXERCISE,
        "go/exercises/practice/leap": GO_EXERCISE,
        "rust/exercises/practice/needs-rand": RUST_WITH_DEP,
        "rust/exercises/practice/clean": RUST_CLEAN,
    }
    for prefix, files in layout.items():
        for rel, content in files.items():
            path = repo / prefix / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "fixture")
    return str(repo), _git(repo, "rev-parse", "HEAD")


def _task(repo: str, commit: str, language: str, exercise: str) -> BenchTask:
    return BenchTask(
        id=f"ex-{language}-{exercise}",
        category="feature",
        instruction="i.md",
        source=Source(
            type="exercism", repo=repo, commit=commit, language=language, exercise=exercise
        ),  # fmt: skip
        grader=GraderConfig(type="exercism"),
    )


def test_provider_splits_visible_hidden_and_reference(
    tmp_path: Path, fake_polyglot: tuple[str, str]
) -> None:
    repo, commit = fake_polyglot
    repo_dir, grading = tmp_path / "ws" / "repo", tmp_path / "grading"
    grading.mkdir()
    ex.ExercismProvider().prepare(
        _task(repo, commit, "python", "two-fer"), repo_dir, suite_base_dir=tmp_path,
        grading_dir=grading,
    )  # fmt: skip
    assert sorted(p.name for p in repo_dir.iterdir()) == ["README.md", "two_fer.py"]
    readme = (repo_dir / "README.md").read_text()
    assert "# Two-fer" in readme and "Default name is `you`." in readme and "`two_fer.py`" in readme
    assert (grading / "hidden" / "two_fer_test.py").is_file()
    assert "f'One for" in (grading / "reference" / "two_fer.py").read_text()
    meta = json.loads((grading / ex.META_FILENAME).read_text())
    assert meta["hidden"] == ["two_fer_test.py"] and meta["solution"] == ["two_fer.py"]


def test_go_test_data_files_are_hidden_too(tmp_path: Path, fake_polyglot: tuple[str, str]) -> None:
    repo, commit = fake_polyglot
    repo_dir, grading = tmp_path / "repo", tmp_path / "g"
    grading.mkdir()
    ex.ExercismProvider().prepare(
        _task(repo, commit, "go", "leap"), repo_dir, suite_base_dir=tmp_path, grading_dir=grading
    )
    assert sorted(p.name for p in repo_dir.iterdir()) == ["README.md", "go.mod", "leap.go"]
    assert sorted(p.name for p in (grading / "hidden").iterdir()) == [
        "cases_test.go",
        "leap_test.go",
    ]


def test_pool_rules_and_seeded_sample_are_deterministic(
    tmp_path: Path, fake_polyglot: tuple[str, str]
) -> None:
    repo, commit = fake_polyglot
    assert ex.eligible(repo, commit, "rust", "clean")
    assert not ex.eligible(repo, commit, "rust", "needs-rand")  # example uses a crate
    kwargs = {"suite_id": "pg", "languages": ["python", "rust"], "per_language": 1, "seed": 7,
              "repo": repo, "commit": commit}  # fmt: skip
    a = ex.build_suite(out=tmp_path / "a", **kwargs)  # type: ignore[arg-type]
    b = ex.build_suite(out=tmp_path / "b", **kwargs)  # type: ignore[arg-type]
    assert a["sample"] == b["sample"] == {"python": ["two-fer"], "rust": ["clean"]}
    suite = json.loads((tmp_path / "a" / "suite.json").read_text())
    assert [t["id"] for t in suite["tasks"]] == ["ex-python-two-fer", "ex-rust-clean"]
    assert "Rust" in (tmp_path / "a" / "instructions" / "rust.md").read_text()


def test_parsers(tmp_path: Path) -> None:
    (tmp_path / "results.xml").write_text(
        '<testsuites><testsuite><testcase name="a"/><testcase name="b"><failure/></testcase>'
        '<testcase name="c"><skipped/></testcase></testsuite></testsuites>'
    )
    assert ex._parse_junit(tmp_path) == (1, 3)
    events = [
        {"Package": "p", "Test": "TestA", "Action": "pass"},
        {"Package": "p", "Test": "TestB", "Action": "fail"},
        {"Package": "p", "Test": "TestB/x", "Action": "pass"},
        {"Package": "p", "Test": "TestB/y", "Action": "fail"},
        {"Package": "p", "Action": "fail"},
    ]
    (tmp_path / "results.json").write_text("\n".join(json.dumps(e) for e in events))
    assert ex._parse_go(tmp_path) == (2, 3)  # leaves: TestA, TestB/x, TestB/y
    (tmp_path / "out.txt").write_text(
        "test result: ok. 3 passed; 0 failed; 0 ignored\ntest result: FAILED. 1 passed; 2 failed; "
        "0 ignored\n"
    )
    assert ex._parse_rust(tmp_path) == (4, 6)
    (tmp_path / "results.json").write_text(
        json.dumps({"numPassedTests": 5, "numTotalTests": 7, "numRuntimeErrorTestSuites": 0})
    )
    assert ex._parse_jest(tmp_path) == (5, 7)
    empty = tmp_path / "empty"
    empty.mkdir()
    assert ex._parse_junit(empty) == ex._parse_go(empty) == ex._parse_rust(empty) == (0, 0)


def test_committed_suite_loads_and_ledger_is_disabled() -> None:
    suite = load_suite(
        Path(__file__).resolve().parents[2] / "benchmarks/suites/polyglot-mini/suite.json"
    )
    langs = {t.language for t in suite.tasks}
    assert langs == {"python", "go", "rust", "cpp", "javascript"}
    disabled = [t.id for t in suite.tasks if not t.enabled]
    assert disabled == ["ex-javascript-ledger"]


@pytest.mark.docker
def test_python_exercise_graded_in_docker(tmp_path: Path, fake_polyglot: tuple[str, str]) -> None:
    repo, commit = fake_polyglot
    repo_dir, grading = tmp_path / "repo", tmp_path / "g"
    grading.mkdir()
    task = _task(repo, commit, "python", "two-fer")
    ex.ExercismProvider().prepare(task, repo_dir, suite_base_dir=tmp_path, grading_dir=grading)

    class Ctx:
        def __init__(self) -> None:
            self.repo_dir, self.grading_dir = str(repo_dir), str(grading)

    stub = ex.ExercismGrader().grade(task.grader, Ctx())
    assert not stub.solved and stub.detail["tests_total"] == 2
    shutil.copy2(grading / "reference" / "two_fer.py", repo_dir / "two_fer.py")
    ref = ex.ExercismGrader().grade(task.grader, Ctx())
    assert ref.solved and ref.score == 1.0
    assert os.listdir(grading)  # grading dir untouched apart from the provider's files
