"""`ao-bench gen`, the `generated` workspace provider and the runner path, end to end (CliRunner at
the outer boundary).  Fake-tier: FakeSubject / `fake` grader only -- no docker, no network, no LLM,
and nothing here depends on the blackbox grader implementation."""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from agent_orchestrator.bench.cli import app
from agent_orchestrator.bench.errors import SubjectError
from agent_orchestrator.bench.gen import FAMILY_REGISTRY
from agent_orchestrator.bench.gen.cli import instruction_text, parse_difficulty
from agent_orchestrator.bench.gen.traps import TRAP_CATALOGUE
from agent_orchestrator.bench.gen.validate import validate_probes
from agent_orchestrator.bench.generated_provider import GeneratedProvider
from agent_orchestrator.bench.registries import WORKSPACE_PROVIDER_REGISTRY
from agent_orchestrator.bench.runner import run_suite
from agent_orchestrator.bench.spec import (
    BenchTask,
    GraderConfig,
    Source,
    load_suite,
)
from agent_orchestrator.bench.workspace import (
    REPO_ROOT,
    bench_grading_root,
    bench_workspace_root,
    materialize_workspace,
)

cli = CliRunner()
PARITY = str(REPO_ROOT / "benchmarks" / "parity" / "default")
MINI_SUITE_DIR = REPO_ROOT / "benchmarks" / "suites" / "gen-services-mini"
# Trap *keys* are not markers here: some are protocol words the spec uses (`dedupe_key`,
# `visibility_timeout`, the `idempotency_key_reuse` code).
HIDDEN_MARKERS = ("probes.json", "INSTANCE_JSON", "server.py", "reference/")


def run_gen(out: Path, *args: str) -> Any:
    return cli.invoke(app, ["gen", "--out", str(out), "--parity", PARITY, *args])


def file_tree(root: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()
    }


@pytest.fixture()
def bench_ws(tmp_path: Path) -> Iterator[Path]:
    """A workspace root under bench_workspace_root() (the provider's sandbox guard), cleaned up."""
    root = bench_workspace_root() / "_test_gen_provider" / tmp_path.name
    yield root
    shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------------------------------------
# ao-bench gen
# ---------------------------------------------------------------------------------------------


class TestGenCli:
    def test_generates_a_loadable_suite_with_deterministic_ids(self, tmp_path: Path) -> None:
        out = tmp_path / "gen-suite"
        result = run_gen(out, "--count", "3", "--languages", "python,typescript,go", "--seed", "40")
        assert result.exit_code == 0, result.output
        suite = load_suite(out / "suite.json")
        assert suite.id == "gen-suite" and suite.tier == "medium"
        assert suite.parity == PARITY
        assert [t.id for t in suite.tasks] == [
            "crud-relational-py-s40", "crud-relational-ts-s41", "crud-relational-go-s42",
            "job-queue-py-s40", "job-queue-ts-s41", "job-queue-go-s42",
        ]  # fmt: skip
        for t in suite.tasks:
            assert t.category == "feature" and t.grader.type == "blackbox"
            assert t.source is not None and t.source.type == "generated"
            assert t.family in FAMILY_REGISTRY and t.language in ("python", "typescript", "go")
            assert t.difficulty == {"entities": 2, "traps": 2, "concurrency": 1, "scale": 1}
            assert {"generated", t.family, t.language, "medium"} <= set(t.tags)
            assert t.source.model_dump()["seed"] == int(t.id.rsplit("-s", 1)[1])
        assert len({t.id for t in suite.tasks}) == 6

    def test_grader_carries_a_copy_of_the_stack_for_the_sandbox(self, tmp_path: Path) -> None:
        out = tmp_path / "s"
        assert run_gen(out, "--count", "2", "--languages", "python,go").exit_code == 0
        data = json.loads((out / "suite.json").read_text())
        assert data["stack"] == {
            "python": {"deps": ["montydb"]},
            "go": {"deps": ["modernc.org/sqlite"]},
        }
        for task in data["tasks"]:
            assert task["grader"] == {"type": "blackbox", "sandbox": {"stack": data["stack"]}}
            lang = task["language"]
            assert task["source"]["deps"] == data["stack"][lang]["deps"]
        # a copy, not an alias: editing one task's stack must not be reflected elsewhere
        assert data["tasks"][0]["grader"]["sandbox"]["stack"] is not data["stack"]

    def test_typescript_stack_has_no_deps(self, tmp_path: Path) -> None:
        out = tmp_path / "s"
        assert run_gen(out, "--count", "1", "--languages", "typescript").exit_code == 0
        data = json.loads((out / "suite.json").read_text())
        assert data["stack"] == {"typescript": {"deps": []}}

    def test_is_deterministic_byte_for_byte(self, tmp_path: Path) -> None:
        diff = "entities=3,traps=3,concurrency=2,scale=0"
        args = ["--count", "2", "--languages", "python,typescript", "--seed", "9",
                "--difficulty", diff, "--id", "same"]  # fmt: skip
        assert run_gen(tmp_path / "a", *args).exit_code == 0
        assert run_gen(tmp_path / "b", *args).exit_code == 0
        assert file_tree(tmp_path / "a") == file_tree(tmp_path / "b")
        (tmp_path / "b" / "suite.json").unlink()
        assert run_gen(tmp_path / "b", *args).exit_code == 0  # regenerating over an old dir
        assert file_tree(tmp_path / "a") == file_tree(tmp_path / "b")

    def test_nothing_hidden_is_written_into_the_suite(self, tmp_path: Path) -> None:
        out = tmp_path / "s"
        assert run_gen(out, "--count", "2", "--difficulty", "traps=3").exit_code == 0
        tree = file_tree(out)
        assert set(tree) == {
            "suite.json",
            "README.md",
            "tasks/_generated/instruction-http.md",
            "tasks/_generated/instruction-jsonl.md",
        }
        for rel, content in tree.items():
            text = content.decode()
            for key in TRAP_CATALOGUE:
                assert key not in text, (rel, key)
            # docs may *describe* the mechanism, but no hidden content is stored in the suite
            assert "INSTANCE_JSON" not in text, rel
            assert '"groups"' not in text and '"steps"' not in text and "def main" not in text

    def test_options(self, tmp_path: Path) -> None:
        out = tmp_path / "opts"
        result = run_gen(
            out, "--family", "job-queue", "--count", "2", "--languages", "go", "--seed", "5",
            "--difficulty", "traps=3,scale=0", "--tier", "large", "--id", "my-suite",
        )  # fmt: skip
        assert result.exit_code == 0, result.output
        suite = load_suite(out / "suite.json")
        assert suite.id == "my-suite" and suite.tier == "large"
        assert [t.id for t in suite.tasks] == ["job-queue-go-s5", "job-queue-go-s6"]
        assert suite.tasks[0].difficulty == {
            "entities": 2, "traps": 3, "concurrency": 1, "scale": 0,
        }  # fmt: skip
        assert not (out / "tasks/_generated/instruction-http.md").exists()

    def test_default_families_are_all_registered_ones(self, tmp_path: Path) -> None:
        out = tmp_path / "all"
        assert run_gen(out, "--count", "1").exit_code == 0
        assert {t.family for t in load_suite(out / "suite.json").tasks} == set(FAMILY_REGISTRY)

    @pytest.mark.parametrize(
        "args",
        [
            ["--family", "no-such-family"],
            ["--languages", "python,cobol"],
            ["--languages", ""],
            ["--difficulty", "entities=9"],
            ["--difficulty", "bogus=1"],
            ["--difficulty", "traps=two"],
            ["--difficulty", "traps"],
            ["--tier", "huge"],
            ["--count", "0"],
        ],
    )
    def test_bad_arguments_fail_without_writing(self, tmp_path: Path, args: list[str]) -> None:
        out = tmp_path / "bad"
        result = run_gen(out, *args)
        assert result.exit_code != 0
        assert not (out / "suite.json").exists()

    def test_instruction_is_generic_and_points_at_spec_and_run_sh(self, tmp_path: Path) -> None:
        out = tmp_path / "s"
        assert run_gen(out, "--count", "1").exit_code == 0
        for contract in ("http", "jsonl"):
            text = (out / "tasks/_generated" / f"instruction-{contract}.md").read_text()
            assert text == instruction_text(contract)
            assert "SPEC.md" in text and "./run.sh" in text
            assert not any(k in text for k in TRAP_CATALOGUE)

    def test_generated_suite_passes_ao_bench_validate(self, tmp_path: Path) -> None:
        out = tmp_path / "s"
        assert run_gen(out, "--count", "2", "--languages", "python,typescript,go").exit_code == 0
        result = cli.invoke(app, ["validate", "--suite", str(out / "suite.json")])
        assert result.exit_code == 0, result.output

    def test_parse_difficulty(self) -> None:
        assert parse_difficulty("").as_dict() == {
            "entities": 2, "traps": 2, "concurrency": 1, "scale": 1,
        }  # fmt: skip
        assert parse_difficulty(" traps = 3 , scale=0 ".replace(" ", "")).traps == 3


class TestCommittedMiniSuite:
    def test_validates_and_has_the_expected_shape(self) -> None:
        result = cli.invoke(app, ["validate", "--suite", str(MINI_SUITE_DIR / "suite.json")])
        assert result.exit_code == 0, result.output
        suite = load_suite(MINI_SUITE_DIR / "suite.json")
        assert suite.id == "gen-services-mini" and suite.tier == "medium"
        assert suite.parity == "../../parity/default"
        assert (MINI_SUITE_DIR / suite.parity).resolve().is_dir()
        assert [(t.family, t.language) for t in suite.tasks] == [
            ("crud-relational", "python"),
            ("job-queue", "python"),
        ]
        assert [t.id for t in suite.tasks] == ["crud-relational-py-s4242", "job-queue-py-s4242"]

    def test_is_exactly_what_ao_bench_gen_produces(self, tmp_path: Path) -> None:
        out = tmp_path / "gen-services-mini"
        result = cli.invoke(
            app,
            ["gen", "--out", str(out), "--count", "1", "--languages", "python", "--seed", "4242",
             "--tier", "medium", "--parity", "../../parity/default"],
        )  # fmt: skip
        assert result.exit_code == 0, result.output
        assert file_tree(out) == file_tree(MINI_SUITE_DIR)


# ---------------------------------------------------------------------------------------------
# the provider
# ---------------------------------------------------------------------------------------------

EXPECTED_REPO_FILES = {
    "python": {"pyproject.toml", "app/__init__.py", "app/__main__.py"},
    "typescript": {"package.json", "tsconfig.json", "app.js"},
    "go": {"go.mod", "main.go"},
}


def gen_suite(tmp_path: Path, *extra: str, count: int = 1) -> Path:
    out = tmp_path / "suite"
    languages = "python,typescript,go"
    assert run_gen(out, "--count", str(count), "--languages", languages, *extra).exit_code == 0
    return out


class TestGeneratedProvider:
    def test_is_registered(self) -> None:
        assert WORKSPACE_PROVIDER_REGISTRY["generated"] is GeneratedProvider

    @pytest.mark.parametrize("count", [3])
    def test_writes_agent_files_to_repo_and_hidden_files_to_grading_dir(
        self, tmp_path: Path, bench_ws: Path, count: int
    ) -> None:
        out = gen_suite(tmp_path, count=count)
        suite = load_suite(out / "suite.json")
        for task in suite.tasks:
            grading = tmp_path / "grading" / task.id
            ctx = materialize_workspace(
                task, bench_ws / task.id, suite_base_dir=out, timeout_seconds=30,
                grading_dir=grading,
            )  # fmt: skip
            repo = Path(ctx.repo_dir)
            lang = task.language or ""
            files = {str(p.relative_to(repo)) for p in repo.rglob("*") if p.is_file()}
            assert files == {"SPEC.md", "run.sh", "INSTRUCTION.md", *EXPECTED_REPO_FILES[lang]}
            assert stat.S_IMODE((repo / "run.sh").stat().st_mode) & 0o111
            assert (repo / "run.sh").read_text().startswith("#!/bin/sh\n")
            contract = FAMILY_REGISTRY[task.family or ""].contract
            assert (repo / "INSTRUCTION.md").read_text() == instruction_text(contract)
            # grader-owned side
            instance = json.loads((grading / "instance.json").read_text())
            assert instance["family"] == task.family and instance["language"] == lang
            assert instance["seed"] == task.source.model_dump()["seed"]  # type: ignore[union-attr]
            assert instance["reference"] == {"language": "python", "entry": "reference/run.sh"}
            probes = json.loads((grading / "probes.json").read_text())
            assert validate_probes(probes) == [] and probes["contract"] == contract
            ref = grading / "reference"
            assert {p.name for p in ref.iterdir()} == {"run.sh", "server.py", "instance.json"}
            assert stat.S_IMODE((ref / "run.sh").stat().st_mode) & 0o111
            assert (ref / "instance.json").read_text() == (grading / "instance.json").read_text()
            # hidden assets never reach the agent-visible tree, and the dirs are disjoint
            assert ctx.grading_dir == str(grading)
            assert not str(grading).startswith(ctx.workspace)
            for path in (p for p in Path(ctx.workspace).rglob("*") if p.is_file()):
                text = path.read_text(errors="ignore")
                assert not any(m in text for m in HIDDEN_MARKERS), (path, task.id)
            assert not list(Path(ctx.workspace).rglob("probes.json"))
            assert not list(Path(ctx.workspace).rglob("reference"))

    def test_materialising_twice_is_byte_identical(self, tmp_path: Path, bench_ws: Path) -> None:
        out = gen_suite(tmp_path, "--difficulty", "entities=3,traps=3")
        task = load_suite(out / "suite.json").tasks[0]
        trees = []
        for n in range(2):
            ctx = materialize_workspace(
                task, bench_ws / f"w{n}", suite_base_dir=out, timeout_seconds=30,
                grading_dir=tmp_path / f"g{n}",
            )  # fmt: skip
            trees.append((file_tree(Path(ctx.repo_dir)), file_tree(tmp_path / f"g{n}")))
        assert trees[0] == trees[1]

    def test_source_deps_drive_manifest_and_spec(self, tmp_path: Path, bench_ws: Path) -> None:
        out = gen_suite(tmp_path)
        data = json.loads((out / "suite.json").read_text())
        data["tasks"][0]["source"]["deps"] = []  # a stdlib-only stack
        (out / "suite.json").write_text(json.dumps(data))
        task = load_suite(out / "suite.json").tasks[0]
        ctx = materialize_workspace(
            task, bench_ws / "w", suite_base_dir=out, timeout_seconds=30,
            grading_dir=tmp_path / "g",
        )  # fmt: skip
        repo = Path(ctx.repo_dir)
        assert "dependencies = []" in (repo / "pyproject.toml").read_text()
        assert "montydb" not in (repo / "SPEC.md").read_text()
        assert json.loads((tmp_path / "g" / "instance.json").read_text())["allow"] == ["sqlite3"]

    def test_without_a_grading_dir_it_refuses(self, tmp_path: Path, bench_ws: Path) -> None:
        out = gen_suite(tmp_path)
        task = load_suite(out / "suite.json").tasks[0]
        with pytest.raises(SubjectError, match="grading_dir"):
            materialize_workspace(task, bench_ws / "w", suite_base_dir=out, timeout_seconds=30)

    @staticmethod
    def _task(source: dict[str, Any] | None, **fields: Any) -> BenchTask:
        return BenchTask(
            id="t-1",
            category="feature",
            instruction="i.md",
            source=Source(**source) if source is not None else None,
            grader=GraderConfig(type="fake"),
            **fields,
        )

    @staticmethod
    def _good() -> dict[str, Any]:
        return {
            "type": "generated", "family": "crud-relational", "seed": 3,
            "difficulty": {"entities": 1, "traps": 1}, "language": "python",
        }  # fmt: skip

    def _prepare(self, tmp_path: Path, task: BenchTask, *, grading: bool = True) -> None:
        GeneratedProvider().prepare(
            task, tmp_path / "repo", suite_base_dir=tmp_path,
            grading_dir=tmp_path / "grading" if grading else None,
        )  # fmt: skip

    def test_good_source_prepares(self, tmp_path: Path) -> None:
        self._prepare(tmp_path, self._task(self._good()))
        assert (tmp_path / "repo" / "SPEC.md").is_file()
        assert (tmp_path / "grading" / "probes.json").is_file()

    @pytest.mark.parametrize(
        ("change", "message"),
        [
            ({"family": "no-such"}, "unknown generator family"),
            ({"family": ""}, "family"),
            ({"seed": True}, "seed"),
            ({"seed": "7"}, "seed"),
            ({"seed": 1.5}, "seed"),
            ({"language": "cobol"}, "language"),
            ({"language": None}, "language"),
            ({"difficulty": {"traps": 4}}, "out of range"),
            ({"difficulty": {"traps": True}}, "integer"),
            ({"difficulty": {"nope": 1}}, "unknown difficulty axes"),
            ({"difficulty": [1, 2]}, "difficulty"),
            ({"deps": "montydb"}, "deps"),
            ({"deps": [""]}, "deps"),
            ({"deps": [3]}, "deps"),
            ({"bogus": 1}, "unknown generated-source keys"),
        ],
    )
    def test_bad_source_raises_subject_error(
        self, tmp_path: Path, change: dict[str, Any], message: str
    ) -> None:
        source = {**self._good(), **change}
        with pytest.raises(SubjectError, match=message):
            self._prepare(tmp_path, self._task(source))
        assert not (tmp_path / "repo").exists()

    def test_missing_family_or_seed_raises(self, tmp_path: Path) -> None:
        for key in ("family", "seed", "language"):
            source = self._good()
            del source[key]
            with pytest.raises(SubjectError):
                self._prepare(tmp_path / key, self._task(source))

    def test_task_metadata_must_agree_with_source(self, tmp_path: Path) -> None:
        for fields in (
            {"family": "job-queue"},
            {"language": "go"},
            {"difficulty": {"entities": 3}},
        ):
            with pytest.raises(SubjectError, match="contradicts"):
                self._prepare(tmp_path / str(sorted(fields)), self._task(self._good(), **fields))
        # consistent metadata (even partial difficulty) is fine
        self._prepare(
            tmp_path / "ok",
            self._task(self._good(), family="crud-relational", language="python",
                       difficulty={"entities": 1, "traps": 1}),
        )  # fmt: skip

    def test_missing_source_or_grading_dir_raises(self, tmp_path: Path) -> None:
        with pytest.raises(SubjectError, match="requires task.source"):
            self._prepare(tmp_path, self._task(None))
        with pytest.raises(SubjectError, match="grading_dir"):
            self._prepare(tmp_path / "x", self._task(self._good()), grading=False)


# ---------------------------------------------------------------------------------------------
# through the runner (FakeSubject + `fake` grader; the blackbox grader is not involved)
# ---------------------------------------------------------------------------------------------


class TestThroughTheRunner:
    @staticmethod
    def _fake_graded_suite(tmp_path: Path) -> Path:
        out = gen_suite(tmp_path / "gen")
        data = json.loads((out / "suite.json").read_text())
        for task in data["tasks"]:
            task["grader"] = {"type": "fake"}
        data["parity"] = PARITY
        (out / "suite.json").write_text(json.dumps(data))
        return out / "suite.json"

    @staticmethod
    def _subject(tmp_path: Path, effect: str) -> Path:
        path = tmp_path / f"subject-{effect}.json"
        path.write_text(json.dumps({"version": "1.0", "id": f"fake-{effect}", "type": "fake",
                                    "scripted_effect": effect}))  # fmt: skip
        return path

    def test_noop_subject_run_materialises_the_grading_dir(self, tmp_path: Path) -> None:
        suite_path = self._fake_graded_suite(tmp_path)
        record = run_suite(suite_path, self._subject(tmp_path, "noop"), out_dir=tmp_path / "res")
        run_root = bench_workspace_root() / record.bench_run_id
        try:
            assert len(record.tasks) == 2
            for t in record.tasks:
                grading = bench_grading_root() / record.bench_run_id / "fake-noop" / t.task_id
                assert (grading / "instance.json").is_file()
                assert (grading / "probes.json").is_file()
                assert (grading / "reference" / "run.sh").is_file()
                ws_repo = run_root / "fake-noop" / t.task_id / "repo"
                assert (ws_repo / "SPEC.md").is_file() and (ws_repo / "INSTRUCTION.md").is_file()
                # the hidden tree is under a sibling root, not under the workspace root at all
                assert not list(run_root.rglob("probes.json"))
        finally:
            shutil.rmtree(run_root, ignore_errors=True)

    def test_copy_reference_makes_the_reference_the_artefact(self, tmp_path: Path) -> None:
        suite_path = self._fake_graded_suite(tmp_path)
        record = run_suite(
            suite_path, self._subject(tmp_path, "copy-reference"), out_dir=tmp_path / "res"
        )
        run_root = bench_workspace_root() / record.bench_run_id
        try:
            by_family = {t.task_id: t for t in record.tasks}
            queue_id = next(i for i in by_family if i.startswith("job-queue"))
            repo = run_root / "fake-copy-reference" / queue_id / "repo"
            # no INSTANCE_JSON here: the reference found the instance.json copied next to it
            env = {k: v for k, v in os.environ.items() if k != "INSTANCE_JSON"}
            proc = subprocess.run(
                [str(repo / "run.sh")], input='{"op":"ping"}\n{"op":"stats"}\n', env=env,
                capture_output=True, text=True, timeout=30, check=True,
            )  # fmt: skip
            lines = [json.loads(x) for x in proc.stdout.splitlines()]
            assert lines[0] == {"ok": True} and lines[1]["stats"]["total"] == 0
        finally:
            shutil.rmtree(run_root, ignore_errors=True)
