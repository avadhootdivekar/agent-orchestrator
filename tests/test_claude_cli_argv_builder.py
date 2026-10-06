"""Tests for the pure ``build_claude_argv`` extraction (E-Rc4Hk8 / T-OeRYSO, ADR-0019 D7).

``build_claude_argv(agent, prompt)`` was lifted verbatim out of ``ClaudeCliExecutor.execute`` so
the cross-run result-cache key can hash the exact argv a dispatch would spawn (HLD §8.2.5). The
extraction must change nothing about what ``execute()`` runs, and the function must stay pure.

Coverage map (ids from the HLD test plan):

* U-A1   ``TestBehaviourIdentity`` -- equals the argv the REAL ``execute()`` hands to ``Popen``.
* U-A2   ``TestPurity``            -- performs no I/O; returns a fresh list on every call.
* U-A3   ``TestNonMutation``       -- never touches the agent's lists.
* U-K8a  ``TestIdInvariance``      -- argv does not depend on run id / task id (tripwire).
* extra  ``TestArgvShape``         -- pins what each matrix case exercises and the step order.
* extra  ``TestLayering``          -- ``claude_cli.py`` imports nothing from the cache package.
"""

from __future__ import annotations

import ast
import builtins
import io
import os
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from agent_orchestrator.executors import claude_cli
from agent_orchestrator.executors.claude_cli import ClaudeCliExecutor, build_claude_argv
from agent_orchestrator.executors.prompt import build_prompt
from agent_orchestrator.models import EFFORT_MAX_TURNS, AgentSpec, TaskContext

# ---------------------------------------------------------------------------
# The agent matrix (shared by U-A1, U-A3 and the pinned-shape tests)
# ---------------------------------------------------------------------------

# Stand-in prompt for tests that call ``build_claude_argv`` directly. U-A1 and U-K8a use the
# real ``build_prompt(ctx)`` output instead.
_PROMPT = "THE-PROMPT"
# What ``AgentSpec``'s default ``command_template`` (``claude -p {prompt}``) renders to.
_BASE = ["claude", "-p", _PROMPT]
# The flags ``_ensure_stream_capture_flags`` appends when the agent sets no output format.
_STREAM = ["--output-format", "stream-json", "--verbose"]
_EXCLUDE_DYNAMIC = "--exclude-dynamic-system-prompt-sections"


def _agent(**fields: Any) -> AgentSpec:
    """A claude_cli ``AgentSpec`` with *fields* overriding the model defaults."""
    return AgentSpec(executor="claude_cli", **fields)


@dataclass(frozen=True)
class Case:
    """One row of the agent matrix.

    ``agent`` holds ``AgentSpec`` keyword overrides. ``expected`` is the argv those overrides
    must produce for ``_PROMPT``, hand-derived from the documented rules; ``None`` marks an
    identity-only row (quirky-but-preserved behaviour that is deliberately NOT pinned).
    """

    id: str
    agent: dict[str, Any]
    expected: list[str] | None = None


_EFFORT_CASES = [
    Case(
        f"effort_{effort}",
        {"effort": effort},
        [*_BASE, "--max-turns", str(EFFORT_MAX_TURNS[effort]), *_STREAM],
    )
    for effort in ("low", "medium", "high", "xhigh")
]

_CASES: list[Case] = [
    Case("defaults_model_unset_effort_unset", {}, [*_BASE, *_STREAM]),
    Case("model_set_effort_unset", {"model": "sonnet"}, [*_BASE, "--model", "sonnet", *_STREAM]),
    Case(
        "model_already_in_command_template",
        {"command_template": ["claude", "-p", "{prompt}", "--model", "opus"], "model": "sonnet"},
        [*_BASE, "--model", "opus", *_STREAM],
    ),
    Case(
        "model_short_flag_in_extra_args",
        {"extra_args": ["-m", "opus"], "model": "sonnet"},
        [*_BASE, "-m", "opus", *_STREAM],
    ),
    *_EFFORT_CASES,
    Case(
        "explicit_max_turns_beats_effort",
        {"max_turns": 7, "effort": "high"},
        [*_BASE, "--max-turns", "7", *_STREAM],
    ),
    Case(
        "max_turns_flag_in_extra_args_blocks_injection",
        {"extra_args": ["--max-turns", "3"], "effort": "high", "max_turns": 9},
        [*_BASE, "--max-turns", "3", *_STREAM],
    ),
    Case(
        "disallowed_tools",
        {"disallowed_tools": ["BashOutput", "KillShell"]},
        [*_BASE, "--disallowedTools", "BashOutput", "KillShell", *_STREAM],
    ),
    Case(
        "forced_disallowed_tools",
        {"forced_disallowed_tools": ["Edit", "Write"]},
        [*_BASE, "--disallowedTools", "Edit", "Write", *_STREAM],
    ),
    Case(
        "disallowed_and_forced_merge_into_one_flag",
        {"disallowed_tools": ["Edit", "Bash"], "forced_disallowed_tools": ["Write", "Edit"]},
        [*_BASE, "--disallowedTools", "Edit", "Bash", "Write", *_STREAM],
    ),
    Case(
        "own_allowed_tools_policy_blocks_opt_in_list",
        {"extra_args": ["--allowedTools", "Read"], "disallowed_tools": ["Bash"]},
        [*_BASE, "--allowedTools", "Read", *_STREAM],
    ),
    Case(
        "own_allowed_tools_policy_still_gets_forced_list",
        {
            "extra_args": ["--allowedTools", "Read"],
            "disallowed_tools": ["Bash"],
            "forced_disallowed_tools": ["Edit"],
        },
        [*_BASE, "--allowedTools", "Read", "--disallowedTools", "Edit", *_STREAM],
    ),
    Case(
        "exclude_dynamic_sections",
        {"exclude_dynamic_system_prompt_sections": True},
        [*_BASE, _EXCLUDE_DYNAMIC, *_STREAM],
    ),
    Case(
        "exclude_dynamic_skipped_with_custom_system_prompt",
        {"exclude_dynamic_system_prompt_sections": True, "extra_args": ["--system-prompt", "Hi."]},
        [*_BASE, "--system-prompt", "Hi.", *_STREAM],
    ),
    Case(
        "exclude_dynamic_skipped_with_system_prompt_file_eq_form",
        {
            "exclude_dynamic_system_prompt_sections": True,
            "extra_args": ["--system-prompt-file=sp.md"],
        },
        [*_BASE, "--system-prompt-file=sp.md", *_STREAM],
    ),
    Case(
        "exclude_dynamic_not_duplicated",
        {"exclude_dynamic_system_prompt_sections": True, "extra_args": [_EXCLUDE_DYNAMIC]},
        [*_BASE, _EXCLUDE_DYNAMIC, *_STREAM],
    ),
    Case(
        "own_output_format_json",
        {"extra_args": ["--output-format", "json"]},
        [*_BASE, "--output-format", "json"],
    ),
    Case(
        "own_output_format_stream_json_eq_form_gets_verbose",
        {"extra_args": ["--output-format=stream-json"]},
        [*_BASE, "--output-format=stream-json", "--verbose"],
    ),
    Case(
        "own_stream_json_and_verbose_left_alone",
        {"extra_args": ["--output-format", "stream-json", "--verbose"]},
        [*_BASE, "--output-format", "stream-json", "--verbose"],
    ),
    Case(
        "extra_args_follow_template",
        {"extra_args": ["--foo", "bar"]},
        [*_BASE, "--foo", "bar", *_STREAM],
    ),
    Case(
        "prompt_placeholder_embedded_and_repeated",
        {
            "command_template": [
                "claude",
                "--append-system-prompt",
                "ctx: {prompt}",
                "-p",
                "{prompt}",
            ]
        },
        ["claude", "--append-system-prompt", f"ctx: {_PROMPT}", "-p", _PROMPT, *_STREAM],
    ),
    Case(
        "no_prompt_placeholder",
        {"command_template": ["claude", "--print", "hello"]},
        ["claude", "--print", "hello", *_STREAM],
    ),
    # Every injector at once: pins the ORDER of all steps (template, extra_args, --model,
    # --max-turns, tool policy, exclude-dynamic, stream flags) and that the variadic
    # --disallowedTools list is terminated by the next flag.
    Case(
        "kitchen_sink_pins_step_order",
        {
            "extra_args": ["--foo"],
            "model": "sonnet",
            "effort": "medium",
            "disallowed_tools": ["Bash"],
            "forced_disallowed_tools": ["Edit"],
            "exclude_dynamic_system_prompt_sections": True,
        },
        [
            *_BASE,
            "--foo",
            "--model",
            "sonnet",
            "--max-turns",
            str(EFFORT_MAX_TURNS["medium"]),
            "--disallowedTools",
            "Bash",
            "Edit",
            _EXCLUDE_DYNAMIC,
            *_STREAM,
        ],
    ),
    # Identity-only rows: existing behaviour that is preserved as-is but not worth pinning.
    Case("max_turns_zero_is_still_injected", {"max_turns": 0}),
    Case(
        "max_turns_eq_form_does_not_block_injection",
        {"extra_args": ["--max-turns=5"], "effort": "low"},
    ),
    Case("empty_command_template", {"command_template": [], "model": "sonnet"}),
]

_PINNED_CASES = [case for case in _CASES if case.expected is not None]


def _case_id(case: Case) -> str:
    return case.id


# ---------------------------------------------------------------------------
# Helpers: TaskContext factory and the REAL execute() Popen capture
# ---------------------------------------------------------------------------


def _ctx(
    agent: AgentSpec, output_dir: Path, *, run_id: str = "run1", task_id: str = "t1"
) -> TaskContext:
    return TaskContext(
        run_id=run_id,
        task_id=task_id,
        agent=agent,
        instruction_path="/ws/specs/instr.md",
        input_paths=["/ws/in.md"],
        output_paths=["/ws/out.md"],
        repo_paths={"core": "/ws"},
        timeout_seconds=60,
        output_dir=str(output_dir),
    )


@pytest.fixture()
def spawned_argv(monkeypatch: pytest.MonkeyPatch) -> Callable[[TaskContext], list[str]]:
    """Run the REAL ``ClaudeCliExecutor.execute`` against a recording ``Popen``.

    Returns a callable ``ctx -> argv`` giving the argv ``execute()`` passed to ``Popen``. The
    stand-in "process" exits immediately with code 0, so ``execute()`` runs its whole path
    (output dir, capture files, usage parsing, result) without spawning anything.
    """

    def run(ctx: TaskContext) -> list[str]:
        spawned: list[list[str]] = []

        class _RecordingPopen:
            returncode = 0

            def __init__(self, argv: list[str], **kwargs: object) -> None:
                spawned.append(list(argv))  # snapshot: later mutation must not blur the record

            def wait(self, timeout: float | None = None) -> int:
                return 0

            def kill(self) -> None:
                pass

        monkeypatch.setattr(
            "agent_orchestrator.executors.claude_cli.subprocess.Popen", _RecordingPopen
        )
        result = ClaudeCliExecutor().execute(ctx)
        assert result.status == "succeeded"
        assert len(spawned) == 1, "execute() must spawn exactly one process"
        return spawned[0]

    return run


# ---------------------------------------------------------------------------
# U-A1 -- behaviour identity against the REAL execute() path
# ---------------------------------------------------------------------------


class TestBehaviourIdentity:
    @pytest.mark.parametrize("case", _CASES, ids=_case_id)
    def test_equals_argv_spawned_by_execute(
        self,
        case: Case,
        tmp_path: Path,
        spawned_argv: Callable[[TaskContext], list[str]],
    ) -> None:
        agent = _agent(**case.agent)
        ctx = _ctx(agent, tmp_path)

        spawned = spawned_argv(ctx)

        assert build_claude_argv(agent, build_prompt(ctx)) == spawned

    def test_prompt_is_one_verbatim_token(
        self, tmp_path: Path, spawned_argv: Callable[[TaskContext], list[str]]
    ) -> None:
        """No shell tokenisation: a prompt with spaces, quotes, braces and a newline (even the
        literal ``{prompt}``) lands as exactly one argv element, identically on both paths."""
        agent = _agent()
        ctx = _ctx(agent, tmp_path).model_copy(
            update={"instruction_path": "/ws/we ird/'q' {prompt}\n{x}.md"}
        )
        prompt = build_prompt(ctx)
        assert "{prompt}" in prompt and "\n" in prompt

        spawned = spawned_argv(ctx)

        assert spawned == build_claude_argv(agent, prompt)
        assert spawned.count(prompt) == 1


# ---------------------------------------------------------------------------
# U-A2 -- purity: no I/O, a fresh list per call
# ---------------------------------------------------------------------------


class TestPurity:
    def test_performs_no_io(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # effort set + max_turns unset exercises the lazy EFFORT_MAX_TURNS import path too.
        agent = _agent(
            model="sonnet",
            effort="medium",
            disallowed_tools=["Bash"],
            forced_disallowed_tools=["Edit"],
            exclude_dynamic_system_prompt_sections=True,
        )
        expected = build_claude_argv(agent, _PROMPT)

        def forbidden(*args: object, **kwargs: object) -> None:
            raise AssertionError("build_claude_argv performed I/O")

        # `monkeypatch.context()` undoes the patches before any assertion/report machinery runs.
        with monkeypatch.context() as mp:
            mp.setattr(os, "makedirs", forbidden)
            mp.setattr(builtins, "open", forbidden)
            mp.setattr(io, "open", forbidden)  # what Path.open / read_text / write_text use
            mp.setattr(subprocess, "Popen", forbidden)
            argv = build_claude_argv(agent, _PROMPT)

        assert argv == expected

    def test_returns_a_fresh_equal_list_on_every_call(self) -> None:
        agent = _agent(model="sonnet", effort="low")

        first = build_claude_argv(agent, _PROMPT)
        second = build_claude_argv(agent, _PROMPT)
        assert first == second
        assert first is not second

        first.append("--mutated-by-caller")
        assert build_claude_argv(agent, _PROMPT) == second


# ---------------------------------------------------------------------------
# U-A3 -- non-mutation of the agent
# ---------------------------------------------------------------------------


class TestNonMutation:
    def test_command_template_and_extra_args_unchanged(self) -> None:
        agent = _agent(
            command_template=["claude", "-p", "{prompt}", "--verbose"],
            extra_args=["--foo", "bar"],
            model="sonnet",
            effort="high",
            disallowed_tools=["Bash"],
            forced_disallowed_tools=["Edit"],
            exclude_dynamic_system_prompt_sections=True,
        )
        template_before = list(agent.command_template)
        extra_before = list(agent.extra_args)

        argv = build_claude_argv(agent, _PROMPT)

        assert agent.command_template == template_before
        assert agent.extra_args == extra_before
        # The result must not alias either input list, or a caller extending it would corrupt
        # the agent.
        assert argv is not agent.command_template
        assert argv is not agent.extra_args
        argv.append("--mutated-by-caller")
        assert agent.command_template == template_before
        assert agent.extra_args == extra_before

    @pytest.mark.parametrize("case", _CASES, ids=_case_id)
    def test_whole_agent_unchanged_across_matrix(self, case: Case) -> None:
        agent = _agent(**case.agent)
        before = agent.model_dump()

        build_claude_argv(agent, _PROMPT)

        assert agent.model_dump() == before


# ---------------------------------------------------------------------------
# U-K8a -- tripwire: argv is independent of run id and task id
# ---------------------------------------------------------------------------


class TestIdInvariance:
    """The result-cache key excludes run id and task id (ADR-0019 D4) because neither can reach
    the prompt or the argv. If either test below fails, an id has become visible to argv
    construction: the key design must be revisited before this test is touched."""

    def test_argv_identical_for_contexts_differing_only_in_ids(self, tmp_path: Path) -> None:
        agent = _agent(model="sonnet", effort="medium")
        ctx_a = _ctx(agent, tmp_path, run_id="run-aaaa11", task_id="task-alpha")
        ctx_b = _ctx(agent, tmp_path, run_id="run-bbbb22", task_id="task-beta")
        assert (ctx_a.run_id, ctx_a.task_id) != (ctx_b.run_id, ctx_b.task_id)

        argv_a = build_claude_argv(agent, build_prompt(ctx_a))
        argv_b = build_claude_argv(agent, build_prompt(ctx_b))

        assert argv_a == argv_b
        for token in argv_a:
            assert "run-aaaa11" not in token and "task-alpha" not in token

    @pytest.mark.parametrize("placeholder", ["run_id", "task_id"])
    def test_ids_are_not_prompt_template_variables(self, placeholder: str, tmp_path: Path) -> None:
        """Probes the one channel ids could use to reach argv: ``prompt_template``. Today the
        placeholder is unknown to ``build_prompt``, so a template naming it fails to render."""
        agent = _agent(prompt_template="Work on {" + placeholder + "}.")
        try:
            rendered = build_prompt(_ctx(agent, tmp_path))
        except KeyError:
            return
        pytest.fail(
            f"{{{placeholder}}} now renders in prompt templates ({rendered!r}); the result-cache "
            "key excludes run/task ids (ADR-0019 D4), so revisit the key design"
        )


# ---------------------------------------------------------------------------
# Pinned argv shapes -- the independent oracle for U-A1
# ---------------------------------------------------------------------------


class TestArgvShape:
    """U-A1 compares the function with ``execute()``, and both now run the same code, so it
    cannot notice a regression inside the function itself. These shapes are hand-derived from
    the documented rules (not copied from the output) and pin what every matrix row exercises,
    including the order of the steps. A deliberate change to argv construction must update them
    together with the key-schema golden (GV-1)."""

    @pytest.mark.parametrize("case", _PINNED_CASES, ids=_case_id)
    def test_case_yields_pinned_argv(self, case: Case) -> None:
        assert build_claude_argv(_agent(**case.agent), _PROMPT) == case.expected


# ---------------------------------------------------------------------------
# Layering -- claude_cli.py must not import the cache package (no import cycle)
# ---------------------------------------------------------------------------

_CACHE_PACKAGE = "agent_orchestrator.cache"


def _imported_names(module_name: str, source: str) -> set[str]:
    """Absolute dotted names that the imports in *source* (module *module_name*) can bind."""
    package = module_name.split(".")[:-1]
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = package[: len(package) - (node.level - 1)] if node.level else []
            module = ".".join([*base, *([node.module] if node.module else [])])
            names.add(module)
            names.update(f"{module}.{alias.name}" for alias in node.names)
    return names


def _cache_imports(module_name: str, source: str) -> list[str]:
    return sorted(
        name
        for name in _imported_names(module_name, source)
        if name == _CACHE_PACKAGE or name.startswith(_CACHE_PACKAGE + ".")
    )


class TestLayering:
    def test_claude_cli_imports_nothing_from_the_cache_package(self) -> None:
        source = Path(claude_cli.__file__).read_text(encoding="utf-8")

        assert _cache_imports(claude_cli.__name__, source) == []

    @pytest.mark.parametrize(
        ("statement", "flagged"),
        [
            ("from ..cache import keys", True),
            ("from ..cache.keys import build_cache_key", True),
            ("from .. import cache", True),
            ("import agent_orchestrator.cache.keys", True),
            ("from agent_orchestrator.cache import keys", True),
            ("from functools import cache", False),
            ("from ..models import AgentSpec", False),
            ("from .prompt import build_prompt", False),
        ],
    )
    def test_scanner_is_not_vacuous(self, statement: str, flagged: bool) -> None:
        found = _cache_imports("agent_orchestrator.executors.claude_cli", statement)

        assert bool(found) is flagged
