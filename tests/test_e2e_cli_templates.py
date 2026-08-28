"""E2E tests for `ao templates` / `ao new` (E-Tpl3x9, HLD §2.5) via CliRunner.

Driven through the CLI — the outermost boundary a user touches — with a small, self-
contained fixture template (NOT the shipped `routed-runner` built-in, which is delivered
and validated by a different, concurrently-developed task, T-Tb3rtr) and a `FakeExecutor`
agents.json, so these exercise arg parsing, workspace/config resolution, scaffolding, and
the in-process `--run` dispatch together end to end.

`_BUILTIN_ROOT` is monkeypatched to an empty directory in every test here so this suite's
assertions never depend on (or are broken by) the real, separately-owned built-in
templates -- discovery-precedence / builtin-shadowing behavior itself is covered by the
unit tests in `test_templates.py`, which exercise it directly against controlled fixtures.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

import agent_orchestrator.templates as templates_mod
from agent_orchestrator.cli import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def _empty_builtin_root(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        templates_mod, "_BUILTIN_ROOT", tmp_path_factory.mktemp("empty-builtin-root")
    )


# ---------------------------------------------------------------------------
# Fixture template + spec triplet
# ---------------------------------------------------------------------------


def _write_fixture_template(root: Path, *, dirname: str = "greeter-template") -> Path:
    """A minimal, self-contained template: one task, one param, a keep_existing prompt."""
    tdir = root / dirname
    tdir.mkdir(parents=True, exist_ok=True)

    manifest = {
        "version": "1.0",
        "name": "greeter",
        "description": "A minimal e2e fixture template.",
        "id_pattern": "e-{rand6}-{slug}",
        "instance_dir": "runs/{id}",
        "params": {
            "greeting": {"description": "Greeting text", "required": False, "default": "hello"},
        },
        "dirs": ["outputs"],
        "files": [
            {"source": "workflow.json.tmpl", "target": "workflow.json"},
            {"source": "prompt.md.tmpl", "target": "prompt.md", "keep_existing": True},
        ],
        "required_agents": ["worker"],
    }
    (tdir / "template.yaml").write_text(json.dumps(manifest))  # valid YAML is valid JSON

    workflow = {
        "version": "1.0",
        "id": "{{ id }}",
        "repo_set": "main",
        "prompt_path": "{{ instance_dir }}/prompt.md",
        "tasks": [
            {
                "id": "do-work",
                "agent": "worker",
                "instruction": "instructions/do-work.md",
                "inputs": ["{{ instance_dir }}/prompt.md"],
                "outputs": ["{{ instance_dir }}/outputs/result.md"],
                "depends_on": [],
            }
        ],
    }
    (tdir / "workflow.json.tmpl").write_text(json.dumps(workflow))
    (tdir / "prompt.md.tmpl").write_text("# {{ id }}\n\n{{ params.greeting }}\n")
    return tdir


def _write_reposets_and_agents(tmp_path: Path) -> tuple[Path, Path]:
    rs = tmp_path / "reposets.json"
    rs.write_text(
        json.dumps(
            {
                "version": "1.0",
                "repo_sets": {
                    "main": {
                        "repos": [{"id": "repo", "path": str(tmp_path), "role": "primary"}],
                        "workspace_root": str(tmp_path),
                    }
                },
            }
        )
    )
    ag = tmp_path / "agents.json"
    ag.write_text(json.dumps({"version": "1.0", "agents": {"worker": {"executor": "fake"}}}))
    return rs, ag


# ---------------------------------------------------------------------------
# `_invoke_run_in_process` mechanism (HLD §2.5: "verify it works", not just assume)
# ---------------------------------------------------------------------------


class TestInvokeRunInProcess:
    """`ao new --run` must invoke the existing `run` command in-process (no subprocess to a
    possibly-stale `ao` binary, no duplicating `run`'s body) via
    `typer.main.get_command(app).main(args=[...], standalone_mode=False)`. These pin down
    that exact mechanism directly: it must suppress `run`'s internal `raise typer.Exit(...)`
    from propagating as a real `SystemExit` out of the test process, AND return the intended
    integer exit code both on success and on failure.
    """

    def test_returns_zero_on_a_succeeding_run(self, tmp_path: Path) -> None:
        from agent_orchestrator.cli import _invoke_run_in_process

        tdir = _write_fixture_template(tmp_path / "tmpl-src")
        rs, ag = _write_reposets_and_agents(tmp_path)
        scaffold = runner.invoke(app, ["new", str(tdir), "my-thing", "--workspace", str(tmp_path)])
        assert scaffold.exit_code == 0, scaffold.output
        instance_dir = next((tmp_path / "runs").iterdir())

        exit_code = _invoke_run_in_process(str(instance_dir / "workflow.json"), str(rs), str(ag))
        assert exit_code == 0

    def test_returns_nonzero_and_does_not_raise_systemexit_on_failure(self) -> None:
        from agent_orchestrator.cli import _invoke_run_in_process

        # A nonexistent workflow path is a deterministic, fast way to make `run` fail its
        # own internal `_load_all` and `raise typer.Exit(1)`.
        exit_code = _invoke_run_in_process("/definitely/does/not/exist/workflow.json", None, None)
        assert exit_code == 1


# ---------------------------------------------------------------------------
# `ao templates`
# ---------------------------------------------------------------------------


class TestAoTemplatesCommand:
    def test_no_templates_registered(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["templates", "--workspace", str(tmp_path)])
        assert result.exit_code == 0, result.output
        assert "No templates discovered" in result.output

    def test_lists_workspace_registered_template(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        tdir = _write_fixture_template(tmp_path / "cfg-templates")
        (tmp_path / ".git").mkdir()  # halts find_project_config's walk-up
        (tmp_path / ".ao").mkdir()
        (tmp_path / ".ao" / "config.yaml").write_text(f"templates:\n  - {tdir}\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)  # isolate from this repo's own .ao/config.yaml

        result = runner.invoke(app, ["templates"])
        assert result.exit_code == 0, result.output
        assert "greeter" in result.output
        assert "[workspace]" in result.output
        assert "--param greeting=<value>" in result.output


# ---------------------------------------------------------------------------
# `ao new` — scaffold only
# ---------------------------------------------------------------------------


class TestAoNewScaffold:
    def test_scaffold_by_path_creates_files_and_prints_next_steps(self, tmp_path: Path) -> None:
        tdir = _write_fixture_template(tmp_path / "tmpl-src")
        result = runner.invoke(app, ["new", str(tdir), "my-thing", "--workspace", str(tmp_path)])
        assert result.exit_code == 0, result.output
        assert "Instance ready:" in result.output
        assert "Next steps:" in result.output

        # Actual files landed on disk under the workspace.
        run_dirs = list((tmp_path / "runs").iterdir())
        assert len(run_dirs) == 1
        instance_dir = run_dirs[0]
        assert instance_dir.name.startswith("e-") and instance_dir.name.endswith("-my-thing")
        assert (instance_dir / "workflow.json").is_file()
        assert (instance_dir / "prompt.md").is_file()
        wf = json.loads((instance_dir / "workflow.json").read_text())
        assert wf["prompt_path"] == f"runs/{instance_dir.name}/prompt.md"

    def test_unknown_template_errors(self, tmp_path: Path) -> None:
        result = runner.invoke(
            app, ["new", "does-not-exist", "a-slug", "--workspace", str(tmp_path)]
        )
        assert result.exit_code != 0
        assert "unknown template" in result.output.lower()

    def test_malformed_param_errors(self, tmp_path: Path) -> None:
        tdir = _write_fixture_template(tmp_path / "tmpl-src")
        result = runner.invoke(
            app,
            [
                "new",
                str(tdir),
                "my-thing",
                "--param",
                "no-equals-sign",
                "--workspace",
                str(tmp_path),
            ],
        )
        assert result.exit_code != 0
        assert "key=value" in result.output

    def test_idempotent_rerun_preserves_prompt(self, tmp_path: Path) -> None:
        tdir = _write_fixture_template(tmp_path / "tmpl-src")
        r1 = runner.invoke(app, ["new", str(tdir), "my-thing", "--workspace", str(tmp_path)])
        assert r1.exit_code == 0, r1.output
        instance_dir = next((tmp_path / "runs").iterdir())
        prompt_path = instance_dir / "prompt.md"
        prompt_path.write_text("HAND EDITED\n")

        r2 = runner.invoke(app, ["new", str(tdir), instance_dir.name, "--workspace", str(tmp_path)])
        assert r2.exit_code == 0, r2.output
        assert prompt_path.read_text() == "HAND EDITED\n"
        assert "prompt.md" in r2.output  # reported as already present

    def test_prompt_file_conflict_errors(self, tmp_path: Path) -> None:
        tdir = _write_fixture_template(tmp_path / "tmpl-src")
        f1 = tmp_path / "prompt1.md"
        f1.write_text("first version\n")
        f2 = tmp_path / "prompt2.md"
        f2.write_text("a DIFFERENT version\n")

        r1 = runner.invoke(
            app,
            [
                "new",
                str(tdir),
                "my-thing",
                "--workspace",
                str(tmp_path),
                "--prompt-file",
                str(f1),
            ],
        )
        assert r1.exit_code == 0, r1.output
        instance_dir = next((tmp_path / "runs").iterdir())

        r2 = runner.invoke(
            app,
            [
                "new",
                str(tdir),
                instance_dir.name,
                "--workspace",
                str(tmp_path),
                "--prompt-file",
                str(f2),
            ],
        )
        assert r2.exit_code != 0
        assert "conflict" in r2.output.lower()

    def test_validate_only_and_run_are_mutually_exclusive(self, tmp_path: Path) -> None:
        tdir = _write_fixture_template(tmp_path / "tmpl-src")
        result = runner.invoke(
            app,
            [
                "new",
                str(tdir),
                "my-thing",
                "--workspace",
                str(tmp_path),
                "--validate-only",
                "--run",
            ],
        )
        assert result.exit_code != 0
        assert "mutually exclusive" in result.output.lower()


# ---------------------------------------------------------------------------
# `ao new --validate-only` / `--run`
# ---------------------------------------------------------------------------


class TestAoNewValidateAndRun:
    def test_validate_only_succeeds_against_fake_agents(self, tmp_path: Path) -> None:
        tdir = _write_fixture_template(tmp_path / "tmpl-src")
        rs, ag = _write_reposets_and_agents(tmp_path)

        result = runner.invoke(
            app,
            [
                "new",
                str(tdir),
                "my-thing",
                "--param",
                "greeting=hi there",
                "--workspace",
                str(tmp_path),
                "--reposets",
                str(rs),
                "--agents",
                str(ag),
                "--validate-only",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "OK: rendered workflow is valid" in result.output

    def test_validate_only_fails_when_agent_missing(self, tmp_path: Path) -> None:
        tdir = _write_fixture_template(tmp_path / "tmpl-src")
        rs, _ = _write_reposets_and_agents(tmp_path)
        # agents.json missing the "worker" agent the template requires.
        ag = tmp_path / "agents-empty.json"
        ag.write_text(json.dumps({"version": "1.0", "agents": {}}))

        result = runner.invoke(
            app,
            [
                "new",
                str(tdir),
                "my-thing",
                "--workspace",
                str(tmp_path),
                "--reposets",
                str(rs),
                "--agents",
                str(ag),
                "--validate-only",
            ],
        )
        assert result.exit_code != 0
        assert "ERROR" in result.output

    def test_run_drives_scaffolded_instance_to_completion(self, tmp_path: Path) -> None:
        """The full loop: ao new --run scaffolds AND executes in-process via the fake
        executor, exactly like `ao run --workflow <rendered>` would -- the outcome this
        whole feature exists to produce ("From template" -> a completed run) driven purely
        through the CLI boundary."""
        tdir = _write_fixture_template(tmp_path / "tmpl-src")
        rs, ag = _write_reposets_and_agents(tmp_path)

        result = runner.invoke(
            app,
            [
                "new",
                str(tdir),
                "my-thing",
                "--param",
                "greeting=hi there",
                "--workspace",
                str(tmp_path),
                "--reposets",
                str(rs),
                "--agents",
                str(ag),
                "--run",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "OK: rendered workflow is valid" in result.output
        assert "succeeded" in result.output.lower()

        instance_dir = next((tmp_path / "runs").iterdir())
        assert (instance_dir / "outputs" / "result.md").is_file()

    def test_run_flag_rejects_unrelated_run_only_options(self, tmp_path: Path) -> None:
        tdir = _write_fixture_template(tmp_path / "tmpl-src")
        rs, ag = _write_reposets_and_agents(tmp_path)

        result = runner.invoke(
            app,
            [
                "new",
                str(tdir),
                "my-thing",
                "--workspace",
                str(tmp_path),
                "--reposets",
                str(rs),
                "--agents",
                str(ag),
                "--max-attempts",
                "1",
                "--run",
            ],
        )
        # --max-attempts is a `run`-only option, not one `new` declares -- this must fail
        # cleanly as an argument-parsing error rather than silently being swallowed/ignored.
        assert result.exit_code != 0

    def test_run_fails_cleanly_on_a_cyclic_rendered_workflow(self, tmp_path: Path) -> None:
        """A template CAN render a broken (cyclic) workflow -- `ao new --run` must fail
        loudly (non-zero exit, cycle named in the output) rather than hang or exit 0."""
        tdir = _write_fixture_template(tmp_path / "tmpl-src", dirname="cyclic-template")
        workflow = {
            "version": "1.0",
            "id": "{{ id }}",
            "repo_set": "main",
            "prompt_path": "{{ instance_dir }}/prompt.md",
            "tasks": [
                {
                    "id": "a",
                    "agent": "worker",
                    "instruction": "instructions/a.md",
                    "depends_on": ["b"],
                },
                {
                    "id": "b",
                    "agent": "worker",
                    "instruction": "instructions/b.md",
                    "depends_on": ["a"],
                },
            ],
        }
        (tdir / "workflow.json.tmpl").write_text(json.dumps(workflow))
        rs, ag = _write_reposets_and_agents(tmp_path)

        result = runner.invoke(
            app,
            [
                "new",
                str(tdir),
                "my-thing",
                "--workspace",
                str(tmp_path),
                "--reposets",
                str(rs),
                "--agents",
                str(ag),
                "--run",
            ],
        )
        assert result.exit_code != 0
        assert "cycle" in result.output.lower() or "circular" in result.output.lower()
