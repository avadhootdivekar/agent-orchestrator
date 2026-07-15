# ADR-0005 — Headless `claude -p` tool policy: allow-all default, opt-in disable

- Status: **Accepted** (implemented 2026-07-15)
- Date: 2026-07-15
- Deciders: Avadhoot Divekar (user, via `meta/prompts/prompt.md` "Current Ask" + follow-up directive "ALLOW ALL … provide flags to disable optionally"), Claude (developer role)
- Related: `src/agent_orchestrator/executors/claude_cli.py` (implementation) · `specs/agents.schema.json` (`disallowed_tools`) · ADR-0003 (settings precedence) · ADR-0006 (per-agent config vs run-level flags — the general guideline behind decision 2)

## Context

Every task in this orchestrator is a **headless `claude -p` subprocess** (`ClaudeCliExecutor`:
`Popen`, `stdin=DEVNULL`, hard `timeout_seconds`, injected `--max-turns`). The origin ask
(`meta/prompts/prompt.md`) raised two things:

1. **Background-command monitoring under `claude -p`.** The user's understanding — *"this is supposed to
   wait for a background command to complete, but in `-p` it just exits"* — is **correct**. `claude -p`
   runs the agent loop and exits the moment the model ends a turn with no pending tool calls. There is
   no persistent interactive session left alive to observe a `run_in_background` shell finishing, nor to
   be re-invoked when it does. So a backgrounded shell is torn down with the process (same process
   group) or orphaned and never observed; the tools that *monitor/manage* those shells (`BashOutput`,
   `KillShell`/`KillBash`) read nothing useful. The model *can* poll within the same run, but that burns
   turns/tokens and is bounded by `--max-turns` / `timeout_seconds` — so "start in background, check
   later" is unreliable **by construction**. Foreground `Bash` (the default) blocks within the turn and
   works fine — that is the correct pattern for anything that must complete.

2. **What else to disable, and where the knob lives** (the ask flagged this as likely Claude-specific,
   not a general technique, and left placement to us).

## Decision

### 1. Default posture is **allow-all** (per user directive)
The executor injects **no** tool restriction by default. Web search (`WebFetch`/`WebSearch`),
`TodoWrite`, and subagent spawning (`Task`) stay **enabled**. Disabling is **opt-in**. Rejected the
initially-proposed "safe/deterministic by default deny-list" — the user explicitly chose allow-all so
default runs retain full agent capability; a surprising default-deny is worse than an explicit opt-in.

### 2. Opt-in mechanism is a per-agent declarative field
`AgentSpec.disallowed_tools: list[str] = []` (added to `specs/agents.schema.json`). When non-empty the
executor appends `--disallowedTools <names>`. Per-agent (not run-level) is the right granularity — a
research agent may want web while a codegen agent silences background tools — and it avoids the
global-override clobbering footgun seen with `--model` (a global run-level tool override would stomp
per-agent intent).

### 3. Explicit CLI policy wins over the field
If an agent's `command_template`/`extra_args` already carries a tool-selection flag
(`--disallowedTools` / `--allowedTools` / `--tools`, either spelling or `=` form), the field is **not**
applied — the explicit, more-specific flag keeps full control. `extra_args` therefore remains a valid
ad-hoc escape hatch.

### 4. Provider-specific, kept out of the core
The semantics live entirely in the `claude_cli` executor plus one claude-shaped `AgentSpec` field
(alongside the existing claude-ish `model`/`effort`/`max_turns`). The engine, DAG, and scheduler are
untouched — consistent with "third-party integrations must never destabilize the core".

### 5. Named convenience set for the `-p` background trap
`RECOMMENDED_HEADLESS_DISALLOWED_TOOLS = ("BashOutput", "KillShell", "KillBash")` documents the one set
almost always worth disabling under headless `-p`. **Both** kill-tool names are listed: `KillBash` was
renamed `KillShell` in Claude Code v2 and v2.1.209 still ships both literals; `--disallowedTools`
matches names exactly and ignores unknowns, so listing both is correct across versions and a safe
no-op. Opting in is copy-paste:

```json
{ "agents": { "codegen": { "executor": "claude_cli",
  "disallowed_tools": ["BashOutput", "KillShell", "KillBash"] } } }
```

## Candidate tools to disable (enumeration — "capture what else")

None are applied by default; this is the menu an agent author picks from.

| Group | Tools | Why you might disable under headless `-p` |
|-------|-------|-------------------------------------------|
| Background shell | `BashOutput`, `KillShell`, `KillBash` | Unobservable/unwaitable once the `-p` turn ends (the trap above). Foreground `Bash` stays. |
| Subagent spawn | `Task` | In-agent nested spawns escape the orchestrator's DAG, cost accounting, breakers, and transcript capture. Disable when you want the engine to own all fan-out. |
| Network | `WebFetch`, `WebSearch` | Non-deterministic; an exfiltration/SSRF surface; at odds with "reproducible from spec + artifacts". Disable for hermetic runs. |
| In-session scratch | `TodoWrite` | Harmless but pointless in a single headless task; disable only to trim turn/token noise. |

## Consequences

- Zero behavioral change for existing specs (empty `disallowed_tools` ⇒ no flag injected).
- `--disallowedTools` is variadic (`<tools...>`), so it is injected **before** the stream-capture flags;
  `--output-format` terminates the list rather than being consumed as a tool name (unit-tested).
- The `-p` background limitation is now documented and one opt-in line away from being silenced.

## Alternatives considered

- **Default-deny "safe by default"** — rejected per the user's explicit allow-all directive; also
  surprising and capability-reducing out of the box.
- **Run-level `--disallow-tools` CLI flag for all agents** — rejected: wrong granularity and repeats the
  known global-`--model` clobbering hazard; per-agent config is declarative and precise.
- **Generic tool-policy abstraction in the core** — rejected: this is provider-specific to the Claude
  CLI; belongs in the executor adapter, not the engine.
- **Rely solely on `extra_args`** — rejected as the *primary* mechanism (not discoverable or
  schema-validated), but retained as the explicit override path (decision 3).
