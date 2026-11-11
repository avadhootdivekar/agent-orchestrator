"""Harbor agent that runs an `ao` workflow inside a Harbor task container.

Harbor (https://harborframework.com) runs each benchmark task in its own container and
grades it with the task's own verifier. Its built-in `claude-code` agent runs one
`claude -p` session. This agent runs an `ao` workflow (an `ao_workflow` bench subject,
default `benchmarks/subjects/ao-overseer-harbor.json`) on the same container instead, so a
Harbor job can compare plain Claude Code against `ao` driving the same Claude Code.

Parity with the `claude-code` arm comes from subclassing it: the same Claude Code install
(same pinned `version`), the same auth resolution (`ANTHROPIC_API_KEY` or
`CLAUDE_CODE_OAUTH_TOKEN`), the same model (`-m`), the same `--effort`, the same
`CLAUDE_CONFIG_DIR` (a fresh dir under the trial's agent logs), and `IS_SANDBOX=1`. On top
of that it installs `uv`, the `ao` wheel built from this checkout, and the subject files,
then runs `ao-bench exec-subject` in the task's working directory.

Use it with `harbor run --agent-import-path ao_harbor.agent:AoAgent` and `PYTHONPATH`
pointing at `benchmarks/harbor/` (`benchmarks/harbor/run.sh` does both).

Options (`--ak key=value`), besides every `claude-code` option:
  ao_wheel    path to a prebuilt `agent_orchestrator-*.whl` (default: build one from this
              checkout once per process with `uv build --wheel`)
  ao_subject  subject JSON (default: benchmarks/subjects/ao-overseer-harbor.json)
  repo_dir    repo dir inside the container (default: the container's working dir)
"""

from __future__ import annotations

import asyncio
import json
import shlex
import subprocess
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, override

from harbor.agents.installed.base import with_prompt_template
from harbor.agents.installed.claude_code import ClaudeCode, ClaudeCodeOptions
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext
from pydantic import Field

# benchmarks/harbor/ao_harbor/agent.py -> repo root is three levels up from this dir.
REPO_ROOT = Path(__file__).resolve().parents[3]
SUBJECTS_DIR = REPO_ROOT / "benchmarks" / "subjects"
PARITY_CLAUDE_DIR = REPO_ROOT / "benchmarks" / "harbor" / "parity" / "claude"
DEFAULT_SUBJECT = SUBJECTS_DIR / "ao-overseer-harbor.json"
# `ao-bench` validates subjects against benchmarks/schemas/, which the wheel does not package
# (bench/spec.py resolves it four `.parent` hops above its own file); install() uploads it there.
BENCH_SCHEMAS_DIR = REPO_ROOT / "benchmarks" / "schemas"
SCHEMAS_DIR_PROBE = "from agent_orchestrator.bench import spec; print(spec._SCHEMAS_DIR)"

# Container-side layout. /installed-agent is Harbor's own install dir for agents.
REMOTE_ROOT = PurePosixPath("/installed-agent/ao")
REMOTE_SUBJECTS = REMOTE_ROOT / "subjects"
REMOTE_INSTRUCTION = REMOTE_ROOT / "instruction.md"
REMOTE_WORKSPACE = PurePosixPath("/ao-ws")
# ao confines every repo to its workspace root (artifacts.LocalFsArtifactStore.resolve, after
# symlink resolution), and "/" cannot be a workspace root. So for the run the task's working
# dir (e.g. /app) is MOVED to REMOTE_REPO and a symlink is left at its original path, so the
# instruction's absolute paths keep working; run() moves it back before the verifier runs.
REMOTE_REPO = REMOTE_WORKSPACE / "repo"
# Interpreter the overseer-runner hooks call (`python_bin` in the harbor subject): the ao
# tool venv's own Python (>= 3.11), whatever the task image ships.
AO_PYTHON_LINK = "/usr/local/bin/ao-python"
AO_PYTHON_VERSION = "3.12"
UV_INSTALL_URL = "https://astral.sh/uv/install.sh"
PATH_PREFIX = 'export PATH="$HOME/.local/bin:$PATH"; '
USAGE_FILE = "ao-usage.json"
# overseer-runner params pinned to the arm's model (see run()).
MODEL_PIN_PARAMS = ("allowed_models", "default_unit_model")
# overseer-runner's whole-run USD cap, set from the shared `max_budget_usd` option.
RUN_BUDGET_PARAM = "run_budget_usd"
ORCHESTRATOR_TARBALL = "ao-orchestrator.tgz"
# Identity for commits made by ao's git stages and agents; task images rarely set one.
GIT_IDENTITY_ENV = {
    "GIT_AUTHOR_NAME": "ao-harbor",
    "GIT_AUTHOR_EMAIL": "ao-harbor@localhost",
    "GIT_COMMITTER_NAME": "ao-harbor",
    "GIT_COMMITTER_EMAIL": "ao-harbor@localhost",
}

# Grading capture, both arms. The shared wrapper prompt forbids git changes (local
# workspace-only mode), but some verifiers grade only committed work: DeepSWE collects
# `git diff <base> HEAD`, so uncommitted edits score as an empty patch (f2p 0). After the agent
# finishes, any uncommitted change in the task's working dir (a git work tree) is committed, so
# the verifier sees exactly the agent's working tree. No-op outside a work tree or when clean.
WORKTREE_COMMIT_MESSAGE = "harbor: agent working-tree changes"


def worktree_commit_command(repo_dir: str) -> str:
    """POSIX sh command committing every uncommitted change in *repo_dir*; prints
    `committed` when it made a commit and always exits 0."""
    git = "git -c 'safe.directory=*' -c user.name=ao-harbor -c user.email=ao-harbor@localhost"
    return (
        f"( cd {shlex.quote(repo_dir)} 2>/dev/null || exit 0; "
        f"{git} rev-parse --is-inside-work-tree >/dev/null 2>&1 || exit 0; "
        f'[ -n "$({git} status --porcelain)" ] || exit 0; '
        f"{git} add -A && {git} commit -q --no-verify "
        f"-m {shlex.quote(WORKTREE_COMMIT_MESSAGE)} && echo committed ) || true"
    )


_wheel_lock = asyncio.Lock()
_built_wheel: Path | None = None


class AoOptions(ClaudeCodeOptions):
    ao_wheel: str | None = Field(default=None, description="Prebuilt ao wheel path.")
    ao_subject: str | None = Field(default=None, description="ao_workflow subject JSON.")
    repo_dir: str | None = Field(default=None, description="Repo dir inside the container.")


def build_wheel(out_dir: Path) -> Path:
    """Build the ao wheel from this checkout into *out_dir*; return its path."""
    out_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(  # noqa: S603 -- fixed argv
        ["uv", "build", "--wheel", "--out-dir", str(out_dir), str(REPO_ROOT)],
        check=True,
        capture_output=True,
        text=True,
    )
    wheels = sorted(out_dir.glob("agent_orchestrator-*.whl"), key=lambda p: p.stat().st_mtime)
    if not wheels:
        raise RuntimeError(f"uv build produced no agent_orchestrator wheel in {out_dir}")
    return wheels[-1]


def subject_support_dirs(subject: Path) -> list[Path]:
    """The dirs a subject's relative `prompt_file`/`reposets`/`agents` live in (under
    SUBJECTS_DIR); a subject must keep its files beside it, as the bench ones do."""
    spec = json.loads(subject.read_text())
    dirs: set[Path] = set()
    for key in ("prompt_file", "reposets", "agents"):
        value = spec.get(key)
        if not value or Path(value).is_absolute():
            continue
        parent = (subject.parent / value).resolve().parent
        if not parent.is_relative_to(SUBJECTS_DIR) or parent == SUBJECTS_DIR:
            raise ValueError(f"{subject}: {key}={value!r} must sit in a dir under {SUBJECTS_DIR}")
        dirs.add(parent)
    return sorted(dirs)


def with_disallowed_tools(agents_doc: dict[str, Any], tools: list[str]) -> dict[str, Any]:
    """*agents_doc* (an `agents.json`) with *tools* added to every agent's
    `disallowed_tools` (ao passes them as `claude --disallowedTools`, as Harbor does for the
    plain arm). Returns a new dict; existing entries are kept, order preserved, no dups."""
    patched = json.loads(json.dumps(agents_doc))
    for spec in patched.get("agents", {}).values():
        current = list(spec.get("disallowed_tools") or [])
        spec["disallowed_tools"] = current + [t for t in tools if t not in current]
    return patched


def usage_to_context(usage: dict[str, Any], context: AgentContext) -> None:
    """Copy an `ao-bench exec-subject` result into Harbor's AgentContext.

    Harbor's `n_input_tokens` includes cache reads and writes; `n_cache_tokens` is the
    cache-read part (same convention as its claude-code agent)."""
    fresh = usage.get("input_tokens") or 0
    cache_read = usage.get("cache_read_input_tokens") or 0
    cache_write = usage.get("cache_creation_input_tokens") or 0
    context.cost_usd = usage.get("cost_usd")
    context.n_input_tokens = fresh + cache_read + cache_write
    context.n_cache_tokens = cache_read
    context.n_output_tokens = usage.get("output_tokens") or 0
    context.metadata = {
        **(context.metadata or {}),
        "ao": {
            key: usage.get(key)
            for key in (
                "subject_id",
                "model",
                "status",
                "attempts",
                "wall_clock_seconds",
                "raw_error",
            )
        },
    }


class CcAgent(ClaudeCode):
    """The plain Claude Code arm: Harbor's claude-code plus the shared parity bundle.

    The bundle (`benchmarks/harbor/parity/claude/`) is copied into the trial's fresh
    `CLAUDE_CONFIG_DIR` before claude starts, so whatever CLAUDE.md, skills, agents or
    commands one arm gets, the other gets too. `AoAgent` inherits this unchanged.
    """

    @staticmethod
    @override
    def name() -> str:
        return "claude-code"

    def _config_dir(self) -> PurePosixPath:
        # Same path Harbor's claude-code run() exports as CLAUDE_CONFIG_DIR.
        return self.environment_logs_dir / "sessions"

    async def _upload_parity_bundle(self, environment: BaseEnvironment) -> None:
        config_dir = self._config_dir().as_posix()
        await self.exec_as_agent(environment, command=f"mkdir -p {shlex.quote(config_dir)}")
        await environment.upload_dir(PARITY_CLAUDE_DIR, config_dir)
        if environment.default_user is not None:
            owner = shlex.quote(str(environment.default_user))
            await self.exec_as_root(environment, command=f"chown -R {owner} {config_dir}")

    async def _repo_dir(self, environment: BaseEnvironment) -> str:
        configured = getattr(self.options, "repo_dir", None)
        if configured:
            return configured
        result = await environment.exec(command="pwd")
        return (result.stdout or "/app").strip().splitlines()[-1]

    async def _commit_worktree(self, environment: BaseEnvironment, repo_dir: str) -> None:
        """Commit the agent's uncommitted changes in *repo_dir* (see worktree_commit_command).
        Best effort: a failure here must not turn a finished run into an agent error."""
        result = await environment.exec(command=worktree_commit_command(repo_dir), user="root")
        if "committed" in (result.stdout or ""):
            self.logger.info(f"Committed the agent's working-tree changes in {repo_dir}")

    @override
    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        await self._upload_parity_bundle(environment)
        repo_dir = await self._repo_dir(environment)
        try:
            await super().run(instruction, environment, context)
        finally:
            await self._commit_worktree(environment, repo_dir)


class AoAgent(CcAgent):
    """`ao` workflow on top of the same Claude Code install (see module docstring)."""

    options_model = AoOptions

    @staticmethod
    @override
    def name() -> str:
        return "ao"

    def _subject_path(self) -> Path:
        configured = getattr(self.options, "ao_subject", None)
        path = Path(configured) if configured else DEFAULT_SUBJECT
        return path if path.is_absolute() else (REPO_ROOT / path).resolve()

    async def _wheel(self) -> Path:
        global _built_wheel
        configured = getattr(self.options, "ao_wheel", None)
        if configured:
            return Path(configured)
        async with _wheel_lock:  # one build per Harbor process, shared by every trial
            if _built_wheel is None:
                out = Path(tempfile.mkdtemp(prefix="ao-harbor-wheel-"))
                _built_wheel = await asyncio.to_thread(build_wheel, out)
            return _built_wheel

    async def _upload_disallowed_tools(self, environment: BaseEnvironment, subject: Path) -> None:
        """Tool parity. Harbor gives the plain arm `--disallowedTools` from the shared
        options (`disallowed_tools`, plus WebSearch/WebFetch when the task's agent network is
        restricted, e.g. DeepSWE). ao's sessions never go through Harbor's claude command, so
        the same list goes into every agent of the uploaded `agents.json` (overwrites the copy
        just uploaded). No-op when the list is empty (TB 2.1: web tools on in both arms).
        Called from run(): Harbor applies the network-driven web-tool disable at the start of
        the agent phase (`set_web_tools_disabled`, after install), so options are final there."""
        tools = [t for t in (self.options.disallowed_tools or "").split(",") if t]
        agents_rel = json.loads(subject.read_text()).get("agents")
        if not tools or not agents_rel or Path(agents_rel).is_absolute():
            return
        local = (subject.parent / agents_rel).resolve()
        remote = REMOTE_SUBJECTS / local.relative_to(SUBJECTS_DIR).as_posix()
        patched = with_disallowed_tools(json.loads(local.read_text()), tools)
        await self._upload_config_text(
            environment,
            content=json.dumps(patched, indent=2),
            remote_path=remote.as_posix(),
            filename=local.name,
        )
        self.logger.info(f"ao agents get --disallowedTools {','.join(tools)} (plain-arm parity)")

    @override
    async def install(self, environment: BaseEnvironment) -> None:
        await super().install(environment)  # Claude Code, exactly as the claude-code arm
        await self.ensure_system_dependencies(environment, ("curl", "bash", "git"))
        await self.exec_as_root(environment, command=f"mkdir -p {REMOTE_SUBJECTS}")
        wheel = await self._wheel()
        remote_wheel = REMOTE_ROOT / wheel.name
        await environment.upload_file(wheel, remote_wheel.as_posix())
        # Only this subject's own files: nothing else from the checkout enters the container.
        subject = self._subject_path()
        await environment.upload_file(subject, (REMOTE_SUBJECTS / subject.name).as_posix())
        for support_dir in subject_support_dirs(subject):
            remote = REMOTE_SUBJECTS / support_dir.relative_to(SUBJECTS_DIR).as_posix()
            await environment.upload_dir(support_dir, remote.as_posix())
        if environment.default_user is not None:
            owner = shlex.quote(str(environment.default_user))
            await self.exec_as_root(environment, command=f"chown -R {owner} {REMOTE_ROOT}")
        await self.exec_as_agent(
            environment,
            command=(
                "set -euo pipefail; "
                + PATH_PREFIX
                + f"command -v uv >/dev/null 2>&1 || curl -LsSf {UV_INSTALL_URL} | sh; "
                + PATH_PREFIX
                + f"uv tool install --force --python {AO_PYTHON_VERSION} "
                + f"{shlex.quote(remote_wheel.as_posix())} && ao --version"
            ),
        )
        tool_python = await self.exec_as_agent(
            environment,
            command=PATH_PREFIX + 'echo "$(uv tool dir)/agent-orchestrator/bin/python"',
        )
        python_path = (tool_python.stdout or "").strip().splitlines()[-1]
        await self.exec_as_root(
            environment,
            command=f"ln -sf {shlex.quote(python_path)} {AO_PYTHON_LINK} && {AO_PYTHON_LINK} -V",
        )
        probe = await self.exec_as_agent(
            environment,
            command=f"{shlex.quote(python_path)} -c {shlex.quote(SCHEMAS_DIR_PROBE)}",
        )
        remote_schemas = (probe.stdout or "").strip().splitlines()[-1]
        await self.exec_as_root(environment, command=f"mkdir -p {shlex.quote(remote_schemas)}")
        await environment.upload_dir(BENCH_SCHEMAS_DIR, remote_schemas)

    def _run_env(self) -> dict[str, str]:
        env = self._resolve_auth_env()
        env.update(GIT_IDENTITY_ENV)
        env["IS_SANDBOX"] = "1"
        env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "1"
        # Same fresh config dir as the claude-code arm: no user settings, sessions kept.
        env["CLAUDE_CONFIG_DIR"] = self._config_dir().as_posix()
        model = self._resolved_model_name()
        if model:
            env["ANTHROPIC_MODEL"] = model
        if self.options.reasoning_effort:
            env["AO_THINKING_EFFORT"] = self.options.reasoning_effort
        if self.options.max_turns:
            env["AO_MAX_TURNS"] = str(self.options.max_turns)
        env.update(self._resolved_env_vars)
        return env

    @override
    @with_prompt_template
    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        env = self._run_env()
        logs = self.environment_logs_dir
        await self._upload_config_text(
            environment,
            content=instruction,
            remote_path=REMOTE_INSTRUCTION.as_posix(),
            filename="instruction.md",
        )
        repo_dir = await self._repo_dir(environment)
        await self._upload_disallowed_tools(environment, self._subject_path())
        subject = REMOTE_SUBJECTS / self._subject_path().name
        argv = [
            "ao-bench", "exec-subject",
            "--subject", subject.as_posix(),
            "--repo-dir", REMOTE_REPO.as_posix(),
            "--instruction-file", REMOTE_INSTRUCTION.as_posix(),
            "--workspace", REMOTE_WORKSPACE.as_posix(),
            "--out", (logs / USAGE_FILE).as_posix(),
            "--capture-dir", (logs / "ao-capture").as_posix(),
        ]  # fmt: skip
        if env.get("ANTHROPIC_MODEL"):
            model = env["ANTHROPIC_MODEL"]
            argv += ["--model", model]
            # Same-model parity: the overseer may otherwise pick another model per unit.
            for key in MODEL_PIN_PARAMS:
                argv += ["--param", f"{key}={model}"]
        if self.options.max_budget_usd:
            # Spend parity: the whole ao run (all its sessions) gets the same USD cap the plain
            # arm's single session gets from `claude --max-budget-usd` (same --ak value).
            argv += ["--param", f"{RUN_BUDGET_PARAM}={self.options.max_budget_usd}"]
        await self._upload_parity_bundle(environment)
        repo_q, ws_repo_q = shlex.quote(repo_dir), shlex.quote(REMOTE_REPO.as_posix())
        await self.exec_as_root(
            environment,
            command=(
                f"set -e; mkdir -p {shlex.quote(REMOTE_WORKSPACE.as_posix())}; "
                f"mv {repo_q} {ws_repo_q} && ln -s {ws_repo_q} {repo_q}"
            ),
        )
        try:
            await self.exec_as_agent(
                environment,
                command=(
                    PATH_PREFIX
                    + shlex.join(argv)
                    + f" 2>&1 | tee {(logs / 'ao-exec.txt').as_posix()}"
                ),
                env=env,
            )
        finally:
            # Keep the ao run state (state.json, task logs) with the trial, best effort.
            await environment.exec(
                command=(
                    f"tar -czf {(logs / ORCHESTRATOR_TARBALL).as_posix()} "
                    f"-C {REMOTE_WORKSPACE} .orchestrator 2>/dev/null || true"
                ),
            )
            # Put the task's working dir back (the symlink alone would also work for most
            # verifiers, so a failed restore is not fatal).
            await environment.exec(
                command=(f"if [ -L {repo_q} ]; then rm {repo_q} && mv {ws_repo_q} {repo_q}; fi"),
                user="root",
            )
            await self._commit_worktree(environment, repo_dir)

    @override
    def populate_context_post_run(self, context: AgentContext) -> None:
        usage_path = self.logs_dir / USAGE_FILE
        try:
            usage = json.loads(usage_path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            self.logger.debug(f"No ao usage file at {usage_path}: {exc}")
            return
        usage_to_context(usage, context)
