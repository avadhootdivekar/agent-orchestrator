"""`ao-bench exec-subject`: run one `ao_workflow` subject against an existing repo dir.

`ao-bench run` owns the whole loop (materialize a workspace, run the subject, grade). An
external harness such as Harbor (https://harborframework.com, `benchmarks/harbor/`) owns
the workspace and the grading itself: it hands over a task container whose working dir is
the repo, plus an instruction string, and runs its own verifier afterwards. This command
is the seam between the two. It runs the subject through the same `AoWorkflowSubject` the
bench uses (reposet render, `init_git`, `ao new ... --run`, usage totals), with `ao`
launched as an installed tool instead of `uv run ao` from a checkout, and writes the
subject's result (status, cost, tokens) as JSON for the harness adapter to read.

Only template-mode subjects (`template` + `prompt_file`) are supported: the subject's prompt
file is the wrapper, and the task instruction is appended to it verbatim, so nothing is
written into the repo under test.
"""

from __future__ import annotations

import json
import shlex
from pathlib import Path

import typer

from ..errors import SpecValidationError

# Heading the instruction is appended under (the subject prompt is the wrapper around it).
INSTRUCTION_HEADING = "## Task instruction (verbatim)"
EFFECTIVE_PROMPT_NAME = "prompt.effective.md"
DEFAULT_AO_COMMAND = "ao"
# A generous default: the external harness enforces its own agent timeout and kills us.
DEFAULT_TIMEOUT_SECONDS = 6 * 60 * 60
# Exit codes: 0 = the subject ran (whatever it solved; the harness grades), 1 = usage/spec
# error, 2 = harness-level subject error (`status == "error"`: spawn failure, no run dir).
EXIT_OK = 0
EXIT_USAGE_ERROR = 1
EXIT_SUBJECT_ERROR = 2


def compose_prompt(subject_prompt: str, instruction: str) -> str:
    """Subject wrapper prompt + the task instruction, verbatim, under a fixed heading.

    No trailing newline: Jinja drops a template's final newline, and the plain arm's
    `harbor-prompt` template must render byte-identical to this."""
    return f"{subject_prompt.rstrip()}\n\n{INSTRUCTION_HEADING}\n\n{instruction.strip()}"


# Jinja placeholder Harbor's prompt_template_path renders the task instruction into.
JINJA_INSTRUCTION = "{{ instruction | trim }}"


def _subject_prompt(subject: Path) -> tuple[str, str]:
    """(subject id, the subject's wrapper prompt text); raises typer.Exit on a bad subject."""
    from .spec import load_subject

    try:
        spec = load_subject(subject)
    except SpecValidationError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(EXIT_USAGE_ERROR) from exc
    if spec.type != "ao_workflow" or spec.template is None or spec.prompt_file is None:
        typer.echo(
            f"ERROR: {subject}: needs an ao_workflow subject in template mode"
            " (`template` and `prompt_file` set)",
            err=True,
        )
        raise typer.Exit(EXIT_USAGE_ERROR)
    prompt_path = Path(spec.prompt_file)
    if not prompt_path.is_absolute():
        prompt_path = subject.resolve().parent / prompt_path
    return spec.id, prompt_path.read_text()


def prompt_template_command(
    subject: Path = typer.Option(
        ..., "--subject", help="ao_workflow subject JSON (template mode)."
    ),
    out: Path = typer.Option(..., "--out", help="Where to write the Jinja prompt template."),
) -> None:
    """Write the plain-Claude-Code arm's prompt template for SUBJECT (input parity).

    The template is `compose_prompt(<subject prompt>, "{{ instruction | trim }}")`: rendered
    by Harbor's `prompt_template_path`, the plain arm receives byte-for-byte the prompt
    `exec-subject` hands the ao run (wrapper + heading + instruction)."""
    _, wrapper = _subject_prompt(subject)
    if "{{" in wrapper or "{%" in wrapper:
        typer.echo(f"ERROR: {subject}: prompt contains Jinja syntax", err=True)
        raise typer.Exit(EXIT_USAGE_ERROR)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(compose_prompt(wrapper, JINJA_INSTRUCTION))
    typer.echo(str(out))


def exec_subject_command(
    subject: Path = typer.Option(
        ..., "--subject", help="ao_workflow subject JSON (template mode)."
    ),
    repo_dir: Path = typer.Option(..., "--repo-dir", help="The repo under test (edited in place)."),
    instruction_file: Path = typer.Option(
        ..., "--instruction-file", help="Task instruction, appended verbatim to the prompt."
    ),
    workspace: Path = typer.Option(
        ...,
        "--workspace",
        help="Scratch dir for the ao run (.orchestrator/, rendered reposet); outside repo-dir.",
    ),
    out: Path = typer.Option(..., "--out", help="Where to write the subject result JSON."),
    capture_dir: Path | None = typer.Option(
        None, "--capture-dir", help="ao stdout/stderr capture dir (default: <workspace>/capture)."
    ),
    model: str | None = typer.Option(None, "--model", help="Overrides the subject's `model`."),
    param: list[str] | None = typer.Option(
        None,
        "--param",
        help="KEY=VALUE template param, overriding the subject's (repeatable). The Harbor"
        " adapter pins allowed_models/default_unit_model to --model with it.",
    ),
    timeout_seconds: int = typer.Option(DEFAULT_TIMEOUT_SECONDS, "--timeout-seconds"),
    ao_command: str = typer.Option(
        DEFAULT_AO_COMMAND,
        "--ao-command",
        help="How to launch ao (shell-split), e.g. 'ao' or '/root/.local/bin/ao'.",
    ),
) -> None:
    """Run one ao_workflow subject on REPO_DIR and write its result JSON (for Harbor)."""
    from .spec import BenchTask, GraderConfig, load_subject
    from .subjects import AoWorkflowSubject
    from .workspace import RunContext

    _, wrapper = _subject_prompt(subject)
    spec = load_subject(subject)
    repo_dir, workspace = repo_dir.resolve(), workspace.resolve()
    if not repo_dir.is_dir():
        typer.echo(f"ERROR: --repo-dir {repo_dir} is not a directory", err=True)
        raise typer.Exit(EXIT_USAGE_ERROR)
    if workspace == repo_dir or workspace.is_relative_to(repo_dir):
        typer.echo("ERROR: --workspace must be outside --repo-dir", err=True)
        raise typer.Exit(EXIT_USAGE_ERROR)

    base_dir = subject.resolve().parent
    workspace.mkdir(parents=True, exist_ok=True)
    effective_prompt = workspace / EFFECTIVE_PROMPT_NAME
    effective_prompt.write_text(compose_prompt(wrapper, instruction_file.read_text()))
    params = dict(spec.template_params)
    for item in param or []:
        key, sep, value = item.partition("=")
        if not sep or not key:
            typer.echo(f"ERROR: --param {item!r} is not KEY=VALUE", err=True)
            raise typer.Exit(EXIT_USAGE_ERROR)
        params[key] = value
    updates: dict[str, object] = {"prompt_file": str(effective_prompt), "template_params": params}
    if model:
        updates["model"] = model
    spec = spec.model_copy(update=updates)

    capture = (capture_dir or workspace / "capture").resolve()
    ctx = RunContext(
        workspace=str(workspace),
        repo_dir=str(repo_dir),
        instruction_path=str(instruction_file.resolve()),
        capture_dir=str(capture),
        timeout_seconds=timeout_seconds,
        subject_base_dir=str(base_dir),
        ao_launcher=shlex.split(ao_command),
    )
    # AoWorkflowSubject.run ignores `task` (the workspace is already materialized); this
    # placeholder only satisfies the Subject.run signature. Grading is the harness's job.
    task = BenchTask(
        id="external",
        category="other",
        instruction=str(instruction_file),
        fixture=str(repo_dir),
        grader=GraderConfig(type="external"),
    )
    try:
        result = AoWorkflowSubject(spec).run(task, ctx)
        payload = result.model_dump(mode="json")
    except Exception as exc:  # noqa: BLE001 -- SubjectError etc.: still write the JSON
        payload = {"status": "error", "raw_error": f"{type(exc).__name__}: {exc}"}
    payload["subject_id"] = spec.id
    payload["model"] = spec.model
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2))
    typer.echo(json.dumps({k: payload.get(k) for k in ("status", "cost_usd", "raw_error")}))
    raise typer.Exit(EXIT_SUBJECT_ERROR if payload.get("status") == "error" else EXIT_OK)


# --- `ao-bench harbor-parity`: did both arms' sessions load the same environment? -------
# Where a trial's stream-json lives: Harbor's claude-code agent tees `claude-code.txt`; the
# ao agent tars the run's `.orchestrator/` (one transcript.jsonl per agent attempt).
CC_STREAM_FILE = "claude-code.txt"
AO_ORCHESTRATOR_TARBALL = "ao-orchestrator.tgz"
# Scalar init-event fields compared on top of envcheck's FINGERPRINT_FIELDS name sets.
PARITY_SCALAR_FIELDS: tuple[str, ...] = (
    "model",
    "permissionMode",
    "output_style",
    "claude_code_version",
)
EXIT_PARITY_MISMATCH = 3


def trial_init_events(trial_dir: Path) -> list[dict]:
    """Every Claude Code `system/init` event a Harbor trial recorded (one per session)."""
    import tarfile
    import tempfile

    from .envcheck import init_events

    agent_dir = trial_dir / "agent"
    events = init_events(agent_dir / CC_STREAM_FILE)
    tarball = agent_dir / AO_ORCHESTRATOR_TARBALL
    if tarball.is_file():
        with tempfile.TemporaryDirectory() as tmp, tarfile.open(tarball) as tar:
            tar.extractall(tmp, filter="data")
            for transcript in sorted(Path(tmp).rglob("transcript.jsonl")):
                events += init_events(transcript)
    return events


def job_parity_profile(job_dir: Path) -> dict[str, object]:
    """What every session of every trial in *job_dir* loaded, as comparable sets."""
    from .envcheck import fingerprint_events

    events = [e for trial in sorted(job_dir.glob("*/agent")) for e in
              trial_init_events(trial.parent)]  # fmt: skip
    fingerprint = fingerprint_events(events)
    profile: dict[str, object] = {"sessions": len(events)}
    if fingerprint is None:
        return profile
    for name, values in fingerprint.sets().items():
        profile[name] = sorted(values)
    profile["tools"] = sorted({t for e in events for t in (e.get("tools") or [])
                               if isinstance(t, str)})  # fmt: skip
    for name in PARITY_SCALAR_FIELDS:
        profile[name] = sorted({str(e.get(name)) for e in events})
    profile["auto_memory"] = fingerprint.auto_memory
    profile["divergent_within_job"] = fingerprint.divergent
    return profile


def parity_command(
    jobs: list[Path] = typer.Argument(..., help="Harbor job dirs, one per arm."),
) -> None:
    """Compare what each arm's Claude Code sessions loaded (tools, skills, agents, slash
    commands, MCP servers, plugins, model, permission mode, output style, version, auto
    memory). Prints JSON; exit 0 when every arm matches, 3 on a mismatch."""
    profiles = {job.name: job_parity_profile(job) for job in jobs}
    names = list(profiles)
    keys = sorted({k for p in profiles.values() for k in p} - {"sessions"})
    mismatches: dict[str, dict[str, object]] = {}
    for key in keys:
        values = {name: profiles[name].get(key) for name in names}
        if key == "model":
            continue  # arms may compare models on purpose; reported, not a mismatch
        if len({json.dumps(v, sort_keys=True) for v in values.values()}) > 1:
            mismatches[key] = values
    missing = [name for name in names if not profiles[name].get("sessions")]
    report = {"profiles": profiles, "mismatches": mismatches, "no_sessions": missing}
    typer.echo(json.dumps(report, indent=2))
    ok = not mismatches and not missing and not any(p.get("auto_memory") for p in profiles.values())
    raise typer.Exit(EXIT_OK if ok else EXIT_PARITY_MISMATCH)
