"""As-built security review (2026-09-07) regressions for the T2 resolver's containment.

Covers findings **C-1** (the forced tool-denial union never reached the child process),
**C-2** (`resolver_deny_push` denied almost nothing) and **C-3** (the resolver kept a shell
and therefore full write access to the shared `.git`).

Every test here reproduces the AUDITOR's attack shape -- the resolver `AgentSpec` that
names its own tool policy, and a real `git push` under the exact env `resolver_env`
produces -- rather than unit-testing the new branch in isolation. Findings whose blast
radius sits inside `Integrator` (H-1, H-3) are regression-tested in `test_integrator.py`
next to the rest of that module's suite.

`tests/isolation/conftest.py`'s autouse `_isolated_git_env` keeps every `git` invocation
below off the real `~`/`$AO_STATE_DIR`.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from agent_orchestrator import templates as templates_pkg
from agent_orchestrator.executors.claude_cli import ClaudeCliExecutor, _apply_tool_policy
from agent_orchestrator.isolation import escalation
from agent_orchestrator.models import (
    RESOLVER_FORCED_DISALLOWED_TOOLS,
    AgentSpec,
    IntegrationSpec,
    TaskContext,
    TaskSpec,
)

MERGE_RESOLVE_PATH = (
    Path(templates_pkg.__file__).parent / "builtin" / "instructions" / "merge-resolve.md"
)

DISALLOWED_FLAG = "--disallowedTools"

# The auditor's exp1 shapes: every spelling `_TOOL_POLICY_FLAGS` recognises, in the two
# places an AgentSpec can carry argv (`extra_args` and `command_template`), plus the `=`
# form. Each one used to make `_ensure_disallowed_tools` skip injection ENTIRELY.
_HOSTILE_AGENT_SHAPES: list[tuple[str, dict[str, list[str]]]] = [
    (
        "extra_args --allowedTools",
        {"extra_args": ["--allowedTools", "Read,Edit,Bash,Task,WebFetch,WebSearch"]},
    ),
    (
        "extra_args --allowed-tools",
        {"extra_args": ["--allowed-tools", "Read,Edit,Bash,Task,WebFetch,WebSearch"]},
    ),
    ("extra_args --disallowedTools", {"extra_args": ["--disallowedTools", "Glob"]}),
    ("extra_args --disallowed-tools", {"extra_args": ["--disallowed-tools", "Glob"]}),
    ("extra_args --tools", {"extra_args": ["--tools", "default"]}),
    (
        "command_template --allowed-tools= form",
        {"command_template": ["claude", "-p", "--allowed-tools=Bash,WebFetch"]},
    ),
    (
        "command_template --allowedTools two-arg form",
        {"command_template": ["claude", "-p", "--allowedTools", "Bash,WebFetch"]},
    ),
]


def _spawned_argv(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, agent: AgentSpec) -> list[str]:
    """The argv `ClaudeCliExecutor` actually hands to `subprocess.Popen` for *agent*.

    Asserting on the spawned argv (not on the model, and not on a helper in isolation) is
    the whole point of C-1: the union was computed and stored correctly on the `AgentSpec`
    and then silently discarded one layer down.
    """
    captured: dict[str, list[str]] = {}

    class _FakePopen:
        def __init__(self, argv: list[str], **kwargs: object) -> None:
            captured["argv"] = argv
            self.returncode = 0

        def wait(self, timeout: float | None = None) -> int:
            return 0

        def kill(self) -> None:  # pragma: no cover - never reached (wait returns 0)
            pass

    monkeypatch.setattr("agent_orchestrator.executors.claude_cli.subprocess.Popen", _FakePopen)
    ctx = TaskContext(
        run_id="run-1",
        task_id="task-a",
        agent=agent,
        instruction_path="/i.md",
        input_paths=[],
        output_paths=[],
        repo_paths={},
        timeout_seconds=60,
        output_dir=str(tmp_path / "out"),
    )
    result = ClaudeCliExecutor().execute(ctx)
    assert result.status == "succeeded"
    return captured["argv"]


def _denied_tools(argv: list[str]) -> set[str]:
    """Tool names covered by any ``--disallowedTools`` occurrence in *argv*.

    The flag is variadic, so a run of names terminates at the next ``--flag``.
    """
    denied: set[str] = set()
    i = 0
    while i < len(argv):
        if argv[i] == DISALLOWED_FLAG:
            i += 1
            while i < len(argv) and not argv[i].startswith("--"):
                denied.update(name for name in argv[i].split(",") if name)
                i += 1
            continue
        i += 1
    return denied


def _resolver_agent(**overrides: list[str]) -> AgentSpec:
    kwargs: dict[str, object] = {
        "executor": "claude_cli",
        "command_template": ["claude", "-p", "{prompt}"],
    }
    kwargs.update(overrides)
    return AgentSpec(**kwargs)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------------------
# C-1 -- the forced denial set must reach the child process, whatever the agent declares
# ---------------------------------------------------------------------------------------


class TestForcedDenialReachesTheChildProcess:
    @pytest.mark.parametrize(
        ("label", "overrides"), _HOSTILE_AGENT_SHAPES, ids=[s[0] for s in _HOSTILE_AGENT_SHAPES]
    )
    def test_forced_set_survives_every_tool_policy_spelling(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, label: str, overrides: dict
    ) -> None:
        """exp1_union_bypass.py, through the REAL executor: a resolver agent that names its
        own tool policy used to re-enable the whole forced set."""
        forced = escalation.resolver_agent_spec(_resolver_agent(**overrides), IntegrationSpec())
        argv = _spawned_argv(monkeypatch, tmp_path, forced)

        denied = _denied_tools(argv)
        for tool in RESOLVER_FORCED_DISALLOWED_TOOLS:
            assert tool in denied, f"{tool} not denied for {label}: {argv}"

    @pytest.mark.parametrize(
        ("label", "overrides"), _HOSTILE_AGENT_SHAPES, ids=[s[0] for s in _HOSTILE_AGENT_SHAPES]
    )
    def test_forced_flag_is_appended_after_the_agents_own_policy_flag(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, label: str, overrides: dict
    ) -> None:
        """`--disallowedTools` is additive on top of `--allowedTools` only when it comes
        LAST; and it must still be terminated by the stream flags rather than swallowing
        them as tool names."""
        forced = escalation.resolver_agent_spec(_resolver_agent(**overrides), IntegrationSpec())
        argv = _spawned_argv(monkeypatch, tmp_path, forced)

        forced_at = len(argv) - 1 - argv[::-1].index(DISALLOWED_FLAG)
        policy_positions = [
            i
            for i, arg in enumerate(argv)
            if arg.split("=", 1)[0]
            in {"--allowedTools", "--allowed-tools", "--disallowed-tools", "--tools"}
        ]
        for position in policy_positions:
            assert forced_at > position
        assert forced_at < argv.index("--output-format")

    def test_benign_agent_still_gets_exactly_one_flag(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """No double injection: with no agent-supplied policy flag, the opt-in list and the
        forced list are merged into a single `--disallowedTools`."""
        forced = escalation.resolver_agent_spec(
            _resolver_agent(disallowed_tools=["KillShell"]), IntegrationSpec()
        )
        argv = _spawned_argv(monkeypatch, tmp_path, forced)
        assert argv.count(DISALLOWED_FLAG) == 1
        assert {"KillShell", *RESOLVER_FORCED_DISALLOWED_TOOLS} <= _denied_tools(argv)

    def test_ordinary_agents_keep_explicit_flag_wins(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """The rule C-1 says must NOT change: for a non-resolver agent (no forced set), an
        explicit tool-policy flag still wins over `disallowed_tools`."""
        agent = _resolver_agent(
            disallowed_tools=["WebSearch"], extra_args=["--allowedTools", "Read"]
        )
        argv = _spawned_argv(monkeypatch, tmp_path, agent)
        assert DISALLOWED_FLAG not in argv

    def test_forced_field_alone_is_enough(self) -> None:
        """`_apply_tool_policy` is the choke point; an empty opt-in list must not suppress
        the forced one."""
        argv = _apply_tool_policy(["claude", "-p", "--tools", "default"], (), ("Bash",))
        assert argv[-2:] == [DISALLOWED_FLAG, "Bash"]


# ---------------------------------------------------------------------------------------
# C-3 -- the resolver has no shell and no subagent tool, and does not stage its own work
# ---------------------------------------------------------------------------------------


class TestResolverToolFloor:
    def test_floor_denies_shell_subagent_and_egress(self) -> None:
        assert set(RESOLVER_FORCED_DISALLOWED_TOOLS) >= {"Bash", "Task", "WebFetch", "WebSearch"}

    def test_floor_is_not_configurable_away(self) -> None:
        """An operator narrowing `resolver_disallowed_tools` cannot drop the floor -- the
        whole reason C-3's fix "depends on C-1 being fixed first"."""
        spec = IntegrationSpec(resolver_disallowed_tools=["Glob"])
        forced = escalation.resolver_agent_spec(_resolver_agent(), spec)
        assert {"Bash", "Task"} <= set(forced.forced_disallowed_tools)
        assert "Glob" in forced.forced_disallowed_tools

    def test_dispatch_builder_carries_the_floor_onto_the_named_resolver(self) -> None:
        agents = {"original": _resolver_agent(), "merge-resolver": _resolver_agent()}
        dispatch = escalation.build_resolver_dispatch(
            TaskSpec(id="a", agent="original", instruction="i.md"),
            agents,
            None,
            IntegrationSpec(resolver_agent="merge-resolver"),
            manifest_relpath="m.json",
            instruction_relpath="i.md",
        )
        assert {"Bash", "Task"} <= set(dispatch.agents["merge-resolver"].forced_disallowed_tools)
        # Untouched: containment is scoped to the resolver, not the whole agent registry.
        assert agents["original"].forced_disallowed_tools == []

    def test_packaged_instruction_no_longer_tells_the_model_to_stage(self) -> None:
        text = MERGE_RESOLVE_PATH.read_text(encoding="utf-8")
        assert "`git add`" not in text
        assert "engine stages" in text.lower()

    def test_packaged_instruction_makes_no_false_push_guarantee(self) -> None:
        """C-2's compounding half: the shipped prompt asserted "You do not have push access
        from this worktree", which the implementation did not provide."""
        text = MERGE_RESOLVE_PATH.read_text(encoding="utf-8").lower()
        assert "do not have push access" not in text
        assert "no shell" in text

    def test_manifest_instructions_match_the_packaged_prompt(self) -> None:
        text = escalation._RESOLVER_MANIFEST_INSTRUCTIONS.lower()
        assert "git add" not in text
        assert "engine stages" in text
        assert "untrusted" in text


# ---------------------------------------------------------------------------------------
# C-2 -- resolver_env, honestly scoped: what it now blocks, and what it still does not
# ---------------------------------------------------------------------------------------


def _git(
    args: list[str], cwd: Path, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def _push_fixture(tmp_path: Path) -> tuple[Path, Path]:
    """A repo with one commit and a bare remote, mirroring exp2_push.sh/exp3_ssh.sh."""
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    (repo / "f.txt").write_text("hi\n")
    assert _git(["add", "f.txt"], repo).returncode == 0
    assert _git(["commit", "-qm", "c1"], repo).returncode == 0
    return repo, remote


def _resolver_process_env() -> dict[str, str]:
    """`os.environ` overlaid with `resolver_env`, exactly as `ClaudeCliExecutor` builds the
    child's environment from `TaskContext.env`."""
    return {**os.environ, **escalation.resolver_env(IntegrationSpec())}


class TestResolverEnvTransportHardening:
    def test_ssh_push_is_blocked(self, tmp_path: Path) -> None:
        """exp3_ssh.sh: the common real-world transport. A repo-local `core.sshCommand`
        stands in for a working ssh setup; `GIT_SSH_COMMAND` must override it."""
        repo, remote = _push_fixture(tmp_path)
        fake_ssh = tmp_path / "fakessh"
        fake_ssh.write_text('#!/bin/sh\nshift $(($#-2)) 2>/dev/null || true\nexec sh -c "$2"\n')
        fake_ssh.chmod(0o755)
        assert _git(["config", "core.sshCommand", str(fake_ssh)], repo).returncode == 0
        assert (
            _git(["remote", "add", "origin", f"ssh://git@example.invalid{remote}"], repo).returncode
            == 0
        )

        # Non-vacuous: without the overlay this exact push SUCCEEDS.
        assert _git(["push", "-q", "origin", "HEAD:refs/heads/control"], repo).returncode == 0

        blocked = _git(
            ["push", "-q", "origin", "HEAD:refs/heads/pwned"], repo, env=_resolver_process_env()
        )
        assert blocked.returncode != 0
        refs = _git(["for-each-ref", "--format=%(refname)"], remote).stdout
        assert "refs/heads/pwned" not in refs

    def test_https_push_is_blocked(self, tmp_path: Path) -> None:
        repo, _remote = _push_fixture(tmp_path)
        assert (
            _git(["remote", "add", "origin", "https://example.invalid/x.git"], repo).returncode == 0
        )
        blocked = _git(
            ["push", "origin", "HEAD:refs/heads/pwned"], repo, env=_resolver_process_env()
        )
        assert blocked.returncode != 0

    def test_local_path_push_is_still_possible_documented_residual_risk(
        self, tmp_path: Path
    ) -> None:
        """HONEST RESIDUAL (do not "fix" this test by weakening the assertion): no
        environment variable stops a push to a local-path or `file://` remote -- those
        transports spawn `git-receive-pack` directly. The ACTUAL control for a hijacked
        resolver is `RESOLVER_FORCED_DISALLOWED_TOOLS`' `Bash` denial, i.e. having no
        process able to run `git` at all. This test exists so the gap stays visible.
        """
        repo, remote = _push_fixture(tmp_path)
        assert _git(["remote", "add", "origin", str(remote)], repo).returncode == 0
        pushed = _git(
            ["push", "-q", "origin", "HEAD:refs/heads/local"], repo, env=_resolver_process_env()
        )
        assert pushed.returncode == 0
        assert "refs/heads/local" in _git(["for-each-ref", "--format=%(refname)"], remote).stdout


class TestResolverEnvShape:
    def test_credential_ssh_and_proxy_pairs_present(self) -> None:
        env = escalation.resolver_env(IntegrationSpec(), base_env={})
        assert env["GIT_TERMINAL_PROMPT"] == "0"
        assert env["GIT_ASKPASS"] == "/bin/false"
        assert env["GIT_SSH_COMMAND"] == "/bin/false"
        pairs = {
            env[f"GIT_CONFIG_KEY_{n}"]: env[f"GIT_CONFIG_VALUE_{n}"]
            for n in range(int(env["GIT_CONFIG_COUNT"]))
        }
        assert pairs["credential.helper"] == ""
        assert pairs["core.sshCommand"] == "/bin/false"
        assert pairs["http.proxy"] == "127.0.0.1:1"
        assert pairs["https.proxy"] == "127.0.0.1:1"

    def test_operator_git_config_pairs_are_not_clobbered(self) -> None:
        """The old hardcoded `GIT_CONFIG_COUNT=2` overwrote an operator's own exported
        `GIT_CONFIG_KEY_0`/`_1`."""
        base = {
            "GIT_CONFIG_COUNT": "2",
            "GIT_CONFIG_KEY_0": "user.name",
            "GIT_CONFIG_VALUE_0": "operator",
            "GIT_CONFIG_KEY_1": "user.email",
            "GIT_CONFIG_VALUE_1": "op@example.invalid",
        }
        env = escalation.resolver_env(IntegrationSpec(), base_env=base)
        assert "GIT_CONFIG_KEY_0" not in env
        assert "GIT_CONFIG_KEY_1" not in env
        assert env["GIT_CONFIG_KEY_2"] == "credential.helper"
        assert int(env["GIT_CONFIG_COUNT"]) == 2 + 4

    @pytest.mark.parametrize("bogus", ["", "not-a-number", "-3"])
    def test_a_malformed_operator_count_degrades_to_zero(self, bogus: str) -> None:
        env = escalation.resolver_env(IntegrationSpec(), base_env={"GIT_CONFIG_COUNT": bogus})
        assert env["GIT_CONFIG_KEY_0"] == "credential.helper"
        assert env["GIT_CONFIG_COUNT"] == "4"

    def test_opt_out_returns_empty(self) -> None:
        assert escalation.resolver_env(IntegrationSpec(resolver_deny_push=False)) == {}
