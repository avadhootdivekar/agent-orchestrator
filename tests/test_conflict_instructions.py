"""Tests for T-Tp7Zs2 (E-Wk9Tz3 M10): the packaged `conflict-friendly-coding.md`
general instruction, and the `routed-runner` template's isolation wiring.

Two things live here that the static, no-imports style of
`test_builtin_routed_runner_assets.py` can't prove on its own:

1. AC-2 — `conflict-friendly-coding.md` is usable through the EXISTING
   `general_instructions` mechanism with NO new plumbing, and the assembled prompt
   names its path only, never its contents (NFR-1).
2. AC-6/AC-12 (S-2) — the DEFAULT render of `routed-runner` stays isolation-inert (no
   new `ao validate` warnings), and a user who opts a task into `isolation: worktree`
   and wires up `integration.resolver_agent` per this template's own README recipe gets
   a clean `ao validate` (no V10 warning) when the resolver agent declares
   `disallowed_tools` correctly, and a real V10 warning when it doesn't (proving the
   check is non-vacuous).

`workflow.json.tmpl` deliberately does NOT embed a live `integration` block (see
README.md "Parallel isolation"): `spec.py`'s V5 rule warns on ANY non-default
`integration` block when no task is isolated, which every render of this template is
by default (opt-in stays opt-in) — embedding one would put a new warning on the
default render. The example lives in README.md instead; this file proves the
recipe it documents actually works once a user applies it.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agent_orchestrator import templates as templates_pkg
from agent_orchestrator.artifacts import LocalFsArtifactStore
from agent_orchestrator.cli import app
from agent_orchestrator.engine import Orchestrator
from agent_orchestrator.executors.fake import FakeExecutor
from agent_orchestrator.models import AgentSpec, RepoRef, RepoSet, TaskSpec, WorkflowSpec
from agent_orchestrator.runstate import RunStateStore

runner = CliRunner()

_TEMPLATES_ROOT = Path(templates_pkg.__file__).resolve().parent
CONFLICT_INSTRUCTIONS_PATH = _TEMPLATES_ROOT / "instructions" / "conflict-friendly-coding.md"

# Required-agent roster for the DEFAULT (non-isolated) render -- mirrors
# test_e2e_builtin_routed_runner.py's own fixture, kept separate since that file is
# owned by a different task and must not be edited here.
_BASE_AGENT_NAMES = [
    "architect",
    "git-operator",
    "developer",
    "tester",
    "reviewer",
    "market-surveyor",
    "architect-opus",
    "reviewer-opus",
    "full-tester",
    "manager",
]

# The example `integration` block from README.md's "Parallel isolation" recipe,
# reproduced here so the test proves the DOCUMENTED recipe works, not a different one.
_README_INTEGRATION_EXAMPLE = {
    "verify_command": ["true"],
    "resolver_agent": "merge-resolver",
    "resolvers": {"union": ["**/*.md"]},
}


@pytest.fixture(autouse=True)
def _no_workspace_env_leak(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate from AO_WORKFLOW/AO_REPOSETS/AO_AGENTS possibly set by the parent repo's
    own workspace, mirroring test_e2e_builtin_routed_runner.py's own fixture."""
    monkeypatch.delenv("AO_WORKFLOW", raising=False)
    monkeypatch.delenv("AO_REPOSETS", raising=False)
    monkeypatch.delenv("AO_AGENTS", raising=False)


def _make_workspace(
    tmp_path: Path, *, merge_resolver_disallowed_tools: list[str] | None
) -> tuple[Path, Path, Path]:
    """A minimal routed-runner-capable workspace (reposets.json/agents.json/.ao/config.yaml).

    `merge_resolver_disallowed_tools=None` omits the `merge-resolver` agent entirely
    (fine for the default, non-isolated render, where it is never referenced). A list
    adds it with that `disallowed_tools` value, for the isolation-enabled scenarios.
    """
    ws = tmp_path / "workspace"
    ws.mkdir()

    rs = ws / "reposets.json"
    rs.write_text(
        json.dumps(
            {
                "version": "1.0",
                "repo_sets": {
                    "main": {
                        "repos": [{"id": "target", "path": str(ws), "role": "primary"}],
                        "workspace_root": str(ws),
                    }
                },
            }
        )
    )

    agents = {name: {"executor": "fake"} for name in _BASE_AGENT_NAMES}
    if merge_resolver_disallowed_tools is not None:
        agents["merge-resolver"] = {
            "executor": "fake",
            "disallowed_tools": merge_resolver_disallowed_tools,
        }
    ag = ws / "agents.json"
    ag.write_text(json.dumps({"version": "1.0", "agents": agents}))

    ao_dir = ws / ".ao"
    ao_dir.mkdir()
    (ao_dir / "config.yaml").write_text("")

    return ws, rs, ag


def _rendered_instance_dir(ws: Path) -> Path:
    runs = list((ws / "workflows" / "routed-runner" / "runs").glob("*"))
    assert len(runs) == 1, runs
    return runs[0]


# ---------------------------------------------------------------------------
# AC-1: conflict-friendly-coding.md ships with the 7 numbered rules + S-3 note.
# ---------------------------------------------------------------------------


class TestConflictFriendlyCodingContent:
    text = CONFLICT_INSTRUCTIONS_PATH.read_text(encoding="utf-8")

    def test_ships_in_the_package(self) -> None:
        assert CONFLICT_INSTRUCTIONS_PATH.is_file()

    def test_seven_numbered_checkable_rules(self) -> None:
        numbered = re.findall(r"^(\d+)\. \*\*", self.text, re.MULTILINE)
        assert numbered == [str(n) for n in range(1, 8)], numbered

    @pytest.mark.parametrize(
        "phrase",
        [
            "New file over hub edit",
            "append-only",
            "One entry per line",
            "trailing newline",
            "drive-by reformatting",
            "Keep the diff",
            "focused",
            "Additive-first",
            "Worktree hygiene",
            "never `git checkout`",
            "never `git stash`",
            "never `git push`",
            "`rebase`/`reset` history",
            "touches",
            "mismatch",
        ],
    )
    def test_rule_phrases_present(self, phrase: str) -> None:
        assert phrase in self.text, phrase

    def test_s3_auto_commit_and_secret_guidance(self) -> None:
        lowered = self.text.lower()
        assert "auto-commit" in lowered
        assert "staged" in lowered
        assert "secret" in lowered
        assert "commit_denylist" in self.text

    def test_stays_within_the_risk_note_line_budget(self) -> None:
        # TASK.md risk note: "keep conflict-friendly-coding.md under ~80 lines".
        assert len(self.text.splitlines()) <= 80


# ---------------------------------------------------------------------------
# AC-2: usable through the EXISTING general_instructions mechanism, paths only.
# ---------------------------------------------------------------------------


class TestConflictFriendlyCodingViaGeneralInstructions:
    def test_prompt_names_its_path_never_its_contents(self, tmp_path: Path) -> None:
        """AC-2, via the *documented* recipe (README.md "Parallel isolation" step 3).

        `general_instructions` paths go through the same workspace-root path guard as
        `task.instruction` (engine.py `_resolve_general_instructions`), so — like every
        OTHER general instruction, packaged or not — this file must be copied into the
        workspace before it is referenceable; the packaged install path itself is never
        inside a real workspace_root. This test copies it in exactly as an operator
        would, then proves the mechanism names that workspace-local PATH only, never
        its contents (NFR-1).
        """
        (tmp_path / "instr.md").write_text("do the thing", encoding="utf-8")
        workspace_copy = tmp_path / "conflict-friendly-coding.md"
        workspace_copy.write_text(
            CONFLICT_INSTRUCTIONS_PATH.read_text(encoding="utf-8"), encoding="utf-8"
        )
        workflow = WorkflowSpec(
            version="1.0",
            id="cf-demo",
            repo_set="main",
            tasks=[
                TaskSpec(id="only", agent="dev", instruction="instr.md", outputs=["out.md"]),
            ],
        )
        reposets = {
            "main": RepoSet(
                repos=[RepoRef(id="repo", path=str(tmp_path))], workspace_root=str(tmp_path)
            )
        }
        agents = {"dev": AgentSpec(executor="fake")}
        executor = FakeExecutor()
        store = LocalFsArtifactStore(str(tmp_path))
        orch = Orchestrator(
            executor,
            store,
            RunStateStore(str(tmp_path), store),
            # Exactly what the CLI's `--general-instruction` flag ultimately feeds into
            # (cli.resolve_general_instructions -> Orchestrator(general_instructions=...)).
            general_instructions=[str(workspace_copy)],
        )
        state = orch.run(workflow, reposets, agents)

        assert state.status == "succeeded"
        prompt = executor.prompts["only"]
        assert str(workspace_copy) in prompt
        # NFR-1: paths only, never contents.
        assert "Registrations are append-only" not in prompt
        assert "One entry per line" not in prompt


# ---------------------------------------------------------------------------
# AC-6/AC-9: the default render stays isolation-inert.
# ---------------------------------------------------------------------------


class TestDefaultRenderStaysInert:
    def test_default_render_has_no_isolation_related_warnings(self, tmp_path: Path) -> None:
        ws, rs, ag = _make_workspace(tmp_path, merge_resolver_disallowed_tools=None)

        result = runner.invoke(
            app,
            [
                "new",
                "routed-runner",
                "default-check",
                "--param",
                "repo_set=main",
                "--workspace",
                str(ws),
                "--reposets",
                str(rs),
                "--agents",
                str(ag),
                "--validate-only",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "OK: rendered workflow is valid" in result.output
        assert "WARNING" not in result.output

        wf = json.loads((_rendered_instance_dir(ws) / "workflow.json").read_text())
        assert wf["defaults"]["isolation"] == "none"
        by_id = {t["id"]: t for t in wf["tasks"]}
        assert by_id["git-branch-off"]["isolation"] == "none"
        # No live `integration`/`scheduling` block in the shipped template (see module
        # docstring) -- the example lives in README.md instead.
        assert "integration" not in wf
        assert "scheduling" not in wf


# ---------------------------------------------------------------------------
# AC-12 (S-2): the README's isolation recipe, applied by a user, validates cleanly
# with a correctly-configured merge-resolver agent, and warns (non-vacuously) when
# the agent's own disallowed_tools is missing the force-injected pair.
# ---------------------------------------------------------------------------


class TestIsolationEnabledRecipeFromReadme:
    def _render_and_apply_recipe(
        self, tmp_path: Path, merge_resolver_disallowed_tools: list[str]
    ) -> tuple[Path, Path, Path]:
        ws, rs, ag = _make_workspace(
            tmp_path, merge_resolver_disallowed_tools=merge_resolver_disallowed_tools
        )
        result = runner.invoke(
            app,
            [
                "new",
                "routed-runner",
                "iso-check",
                "--param",
                "repo_set=main",
                "--workspace",
                str(ws),
                "--reposets",
                str(rs),
                "--agents",
                str(ag),
                "--validate-only",
            ],
        )
        assert result.exit_code == 0, result.output

        wf_path = _rendered_instance_dir(ws) / "workflow.json"
        wf = json.loads(wf_path.read_text())
        wf["integration"] = _README_INTEGRATION_EXAMPLE
        for task in wf["tasks"]:
            if task["id"] == "task-impl":
                task["isolation"] = "worktree"
        wf_path.write_text(json.dumps(wf))
        return wf_path, rs, ag

    def test_correctly_configured_merge_resolver_avoids_v10_warning(self, tmp_path: Path) -> None:
        wf_path, rs, ag = self._render_and_apply_recipe(tmp_path, ["WebFetch", "WebSearch"])

        result = runner.invoke(
            app,
            ["validate", "--workflow", str(wf_path), "--reposets", str(rs), "--agents", str(ag)],
        )
        assert result.exit_code == 0, result.output
        assert "OK: all specs valid" in result.output
        assert "WARNING" not in result.output

    def test_missing_disallowed_tools_triggers_a_real_v10_warning(self, tmp_path: Path) -> None:
        # Proves the previous test's absence-of-warning is non-vacuous: the SAME
        # scenario with the agent's own disallowed_tools left empty DOES warn.
        wf_path, rs, ag = self._render_and_apply_recipe(tmp_path, [])

        result = runner.invoke(
            app,
            ["validate", "--workflow", str(wf_path), "--reposets", str(rs), "--agents", str(ag)],
        )
        assert result.exit_code == 0, result.output  # V10 is a warning, not fatal
        assert "WARNING" in result.output
        assert "merge-resolver" in result.output
        assert "disallowed_tools" in result.output
