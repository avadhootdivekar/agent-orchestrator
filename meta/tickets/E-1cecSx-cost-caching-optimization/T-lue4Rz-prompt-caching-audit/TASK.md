# TASK: T-lue4Rz-prompt-caching-audit

## Metadata
- Task ID: `T-lue4Rz-prompt-caching-audit`
- Epic ID: `E-1cecSx-cost-caching-optimization`
- Owner: `dev-epic` agent (implemented directly, not delegated — highest priority, already
  research-heavy)
- Created: 2026-09-21
- Last Updated: 2026-09-21
- Status: `Done`
- Estimate: `< 1 day`

## Requirements Mapping
- Requirement IDs: FR-B1-1, NFR-B1-1

## Description
Audit whether the per-task worktree path (or any other per-task-unique value: timestamps, task
ids, run ids) leaks into content Claude Code loads early (CLAUDE.md/system-prompt content) in a
way that breaks Anthropic's prompt cache across isolated tasks. See
`docs-md/cost-caching-optimization-hld.md` §1 for the full audit and finding.

**Finding (see design doc §1.2-§1.3):** ao's own code is clean (zero leaks — traced
`prompt.py`, `claude_cli.py`, `models.py` NFR-1 invariant, `isolation/*`). The real leak is
structural and internal to Claude Code's own default system-prompt preset, which embeds the
literal working directory (+ platform/shell/OS version/auto-memory-paths) ahead of any content
ao controls — confirmed via Anthropic's official docs, quoted verbatim in the design doc. Every
worktree-isolated task therefore gets a byte-different system prompt purely from Claude Code's
own behavior, independent of anything ao does.

**Fix:** new opt-in `AgentSpec.exclude_dynamic_system_prompt_sections: bool = False` field;
`ClaudeCliExecutor` injects the documented `--exclude-dynamic-system-prompt-sections` CLI flag
when set (skipped if the agent's own `command_template`/`extra_args` already sets
`--system-prompt`, `--system-prompt-file`, or the flag itself — "explicit caller flag wins",
matching the existing convention for `--output-format`/`--disallowedTools`).

## Acceptance Criteria
1. `AgentSpec.exclude_dynamic_system_prompt_sections: bool = False` added in `models.py`
   (additive; default `False` keeps every existing serialized config and dispatch argv
   byte-identical).
2. `claude_cli.py` gains a new, narrow argv-injection function (mirroring
   `_ensure_disallowed_tools`'s shape) that appends `--exclude-dynamic-system-prompt-sections`
   only when the flag is set AND argv doesn't already carry `--system-prompt`,
   `--system-prompt-file`, or the flag itself.
3. Unit tests: (a) flag unset → argv unchanged (regression-safe, existing argv-construction
   tests still pass verbatim); (b) flag set → flag appears in argv, correctly positioned
   relative to other flag-injection helpers; (c) flag set but agent already declares
   `--system-prompt`/`--system-prompt-file`/the flag itself in `command_template`/`extra_args`
   → no duplicate injection.
4. `docs-md/cost-caching-optimization-hld.md` §1 (already written) stands as the audit record;
   no further doc work required for this task beyond keeping it in sync if implementation
   details shift.
5. `ruff`/`mypy` clean on the touched files.

## Risks
- Version-compatibility risk of the CLI flag itself is why this ships opt-in, not a default
  (see design doc §1.4). No mitigation needed beyond documenting the opt-in recommendation for
  isolated workflows.

## Dependencies
- None (independent of B2/B3/B4).

## Pseudocode / Algorithm
```text
def _ensure_exclude_dynamic_sections(argv: list[str], enabled: bool) -> list[str]:
    if not enabled:
        return list(argv)
    if any(a in ("--system-prompt", "--system-prompt-file",
                  "--exclude-dynamic-system-prompt-sections") for a in argv):
        return list(argv)
    return list(argv) + ["--exclude-dynamic-system-prompt-sections"]
```
Wired into `ClaudeCliExecutor.execute` alongside the existing flag-injection calls, reading
`ctx.agent.exclude_dynamic_system_prompt_sections`.

## Schemas / Interface Notes
- Interface / API: `AgentSpec` (pydantic model, `models.py`) — one new optional bool field.
- Spec / data schema (JSON/YAML): agent config JSON/YAML may now set
  `"exclude_dynamic_system_prompt_sections": true`. No schema file change required unless
  `specs/*.schema.json` enumerates `AgentSpec` fields explicitly — check and update if so.
- Triggers / events: N/A.
- Artifacts (inputs/outputs by path): N/A — argv-only change.

## Handoff Boundary
- Upstream: none.
- Downstream: `T-UJElTR-e2e-verification` (demonstrates the flag firing in the example workflow).

## Artifacts
- Docs/comments: `meta/tickets/E-1cecSx-cost-caching-optimization/T-lue4Rz-prompt-caching-audit/`
- Design doc: `docs-md/cost-caching-optimization-hld.md` §1
