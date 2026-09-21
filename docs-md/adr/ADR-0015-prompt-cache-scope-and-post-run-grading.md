# ADR-0015 — Prompt-cache-scope opt-in flag, and post-run (not in-engine) settlement grading

- Status: **Accepted** (2026-09-21)
- Date: 2026-09-21
- Deciders: `dev-epic` agent, informed by early-gate `reviewer` + `architect` passes (Epic B,
  `E-1cecSx-cost-caching-optimization`)
- Related: ADR-0006 (per-agent config over run-level flags — governs decision 1 below),
  ADR-0007 (parallel execution — governs the trade-off decision 1 accepts), Epic A's
  `docs-md/task-lifecycle-hooks-hld.md` (`hooks.py::run_hook`, reused unchanged by both
  decisions), `docs-md/cost-caching-optimization-hld.md` §1, §3.3

## Context

Epic B's early-gate review (both `reviewer` and `architect`, 2026-09-21) surfaced two decisions
significant enough, and durable enough, to warrant a standing record rather than living only in
a design doc that later work won't necessarily re-read.

## Decision 1 — Opt into Claude Code's own `--exclude-dynamic-system-prompt-sections` flag,
per-agent, rather than normalizing paths or taking over the system prompt

**Context**: Claude Code's own default system prompt embeds the literal working directory (+
platform/shell/OS version/auto memory paths) ahead of any content ao controls (confirmed via
`code.claude.com/docs/en/prompt-caching`). Every worktree-isolated task therefore gets a
byte-different system prompt from every other, breaking Anthropic's prompt cache across tasks.

**Options considered**:
1. **Normalize the worktree path** (bind-mount/symlink every task worktree to one stable path).
   Rejected: OS-specific, fights the entire point of worktree isolation (distinct, addressable
   per-task checkouts), and risks breaking `git worktree`'s own `.git` linkage assumptions.
2. **Take over the system prompt** (`--system-prompt-file` with a static, ao-authored prefix).
   Rejected: this WOULD stabilize the prefix, but it disables `--exclude-dynamic-system-prompt-
   sections` entirely (documented CLI behavior: that flag is ignored whenever a custom system
   prompt is set), discards Claude Code's own preset tool guidance/safety instructions (the
   `AgentSpec` field docs already warn "Custom prompt: Built-in safety... Must be added"), and
   creates a far larger, ongoing vendor-coupling surface (ao would own re-deriving Claude Code's
   own prompt content forever).
3. **Opt into Claude Code's own `--exclude-dynamic-system-prompt-sections` flag** (headless-mode
   only, moves the per-session sections into the first user message instead). **Chosen.**

**Precedent** (architect review, landscape survey): this is the standard resolution pattern for
"the tool's own cache/build key includes a path irrelevant to the semantic work" — ccache's
`hash_dir = false`, Bazel's `--experimental_output_paths=strip`, and Gradle's `@PathSensitive
(RELATIVE)` all let the TOOL exclude the irrelevant input from its own key, opt-in, with a
documented fidelity trade-off, rather than the caller faking/normalizing its own inputs.

**Layering**: per-agent (`AgentSpec.exclude_dynamic_system_prompt_sections: bool = False`),
not a run-level CLI flag, not a `TaskSpec` override. This follows **ADR-0006** directly: cache-
scope behavior does not vary per-task the way `model`/`effort` legitimately do, so it belongs
where every other argv-shaping per-agent knob already lives (`disallowed_tools`, `extra_args`,
`model`). A bare `extra_args: ["--exclude-dynamic-system-prompt-sections"]` escape hatch would
have worked with zero new code, but a dedicated field is schema-validated (author gets a
discoverable, documented option in `agents.schema.json` rather than needing to know the raw
flag string), and it is the one guarded injection point that skips itself when the agent
already sets a custom `--system-prompt`/`--system-prompt-file` (the raw `extra_args` escape
hatch would not).

**Default is `False` (opt-in), not auto-enabled for `isolation: worktree` workflows**: the
flag's exact CLI-version floor is undocumented; an older `claude` binary that rejects an
unrecognized flag would break every dispatch of an agent that had it forced on, which CLAUDE.md's
"safe by default" principle rules out as a silent default. A future auto-detection Stretch item
(§5 of the epic's design doc) — **if ever built** — must be a **fill-in default** (applies only
where the agent declares no explicit value) per ADR-0003/ADR-0006, never a clobber of an explicit
`false` — the exact shape of the live `--model` defect ADR-0006 itself was written to prevent.

**Known limitation, disclosed not hidden**: the flag's documented scope is "working directory,
environment info, memory paths, git-repo flag." Claude Code's own docs describe a **separate**
cache-scope determinant for sequential sessions: "the git status snapshot taken at startup...
carries the branch and recent commits." ao's per-task isolation gives every isolated task its
own branch (`isolation/paths.py::task_branch`), which is not in the flag's documented coverage
list. **The fix therefore reduces, but is not proven to eliminate, the cache miss for
worktree-isolated tasks** — see the design doc §1.4 for the full, honest accounting; this ADR
records the decision (opt into the flag) and its known limitation together, deliberately, so a
future reader does not need to re-derive the caveat from the design doc's revision history.

**Consequence**: ao now carries a soft dependency on an undocumented `claude` CLI minimum
version for any agent that opts in. Mitigated by (a) the opt-in default, (b) a documented,
distinct "unknown option" detection pattern in `claude_cli.py` (mirrors the existing
`_CLAUDE_QUOTA_PATTERN` convention) so a version mismatch surfaces as a clear, attributable
`TaskResult.error` rather than a generic failure.

## Decision 2 — Settlement/outcome grading runs POST-RUN (`ao report outcomes --grade`), not as
a new in-engine hook trigger point

**Context**: the design doc originally proposed a THIRD hook concept, `TaskSpec.settlement_hook`
(distinct from Epic A's `pre_hook`/`post_hook`), fired from two new `engine.py` call sites so a
deterministic grading hook would cover tasks skipped via `skip_if_outputs_exist` or resumed as
already-succeeded, not just freshly-dispatched ones (Epic A HLD §7 named this gap).

**Early-gate architect review (2026-09-21) found this in-engine design has a chain of real
correctness/performance defects**, not just style preferences:
- The proposed call site (`engine.py:1962`, right before `ctx.done.add(tid)`) is NOT the task's
  final status — five later code paths (`emit_tasks`/loop-gate manifest and injection failures,
  `engine.py:2067/2091/2103/2133/2148`) can still downgrade `succeeded` → `failed` AFTER that
  point, so a settlement grade fired there could record "succeeded" for a task the run itself
  then ends as "failed."
- For an `isolation: worktree` task, by the time either proposed call site would fire, the
  worktree has already been released (`IntegrationSpec.keep_worktrees` defaults to
  `"on_failure"`) and the shared checkout is NOT yet synced (`IntegrationSpec.sync_checkout`
  defaults to `"on_demand"`, only guaranteed synced at run end) — a deterministic grader reading
  the working tree at settle time would silently grade a stale/wrong tree, exactly the failure
  mode a grading mechanism must not have.
- Both proposed call sites run on the ENGINE'S MAIN THREAD and block on a subprocess (up to
  `HookSpec.timeout_seconds`, default 120s) — unlike Epic A's `pre_hook`/`post_hook`, which run
  on worker threads and parallelize with `max_parallel`. A settlement hook would serialize
  against wave-pool refill (stalling other workers) and, on the skip-path call site, spawn one
  blocking subprocess per skip-eligible task on every `ao resume` invocation before any real
  dispatch could proceed.
- A bare `workflow.hooks[name]` registry lookup (mirroring Epic A's own two existing call sites)
  is not resolved by `cross_validate` for `emit_tasks`-injected tasks, so an agent-authored
  manifest naming an unknown hook would raise `KeyError` and kill the run — a new main-thread
  call site inside the dispatch loop would make this worse, not better.
- Per-task wiring (`TaskSpec.settlement_hook`) cannot cover `emit_tasks`-injected tasks at all
  under the trust model Epic A's AC-15 establishes (an injected task can reference an
  already-declared hook name, never invent one, but nothing HUMAN-authored wires it to one) —
  so "grade every task" would remain untrue for the one task-origin this mechanism most needs
  to reach (a workflow-level default would close this, at the cost of yet more engine surface).

**Decision: settlement/outcome grading is a POST-RUN pass, not a new engine hook trigger
point.** `ao report outcomes <run_id> --grade <hook-name>` resolves the named hook from the
SAME `WorkflowSpec.hooks` registry Epic A already built (zero new spec surface — no
`TaskSpec.settlement_hook` field, no `HookRef.on_failure` misuse risk, no new
`$defs/settlementHookRef` schema entry), iterates every task in the already-persisted
`RunState.tasks` (dispatched AND skipped alike, uniformly, by construction — no per-task
wiring, so `emit_tasks`-injected tasks are covered automatically too), and calls
`hooks.run_hook` (Epic A's existing, unchanged execution mechanism — `kind` widened to accept
`"settlement_hook"` as a third value, the one small additive change to `hooks.py`/`models.py`)
from the CLI's own process, AFTER `run()` has completed. This structurally resolves every
defect above: there is no "final status" ambiguity (the run is over), the shared checkout has
already gone through run-end sync (correct tree to grade), nothing runs on the engine's main
thread or contends with `max_parallel`, and a missing/misnamed hook is a normal CLI error, not
an in-run crash.

**What this gives up, accepted for MVP**: no live grading signal while a run is in progress
(the dashboard cannot show a settlement grade until the run ends — B2/B4's timing/cache
visibility are unaffected, this is scoped to B3's grading only), and no path to ever gating
dispatch on a settlement grade (already Non-MVP; if a future epic needs gated settlement
grading, it is a different, harder feature than this one, and should be scoped as such rather
than retrofit onto this decision).

**Consequence**: `TaskSpec`/`TaskRunState`/`spec.py::cross_validate` gain ZERO new fields for
this feature; `engine.py` is untouched by B3 entirely. Grading results are written to a report
artifact (not into `RunState`), so there is no new resume/migration surface either.

## Alternatives considered

- In-engine `settlement_hook` as originally designed — rejected per the architect findings
  above; would have required a nontrivial rework (a `_settle_completed_task_inner` rename +
  wrapper, a distinct lower default timeout, `.get()`-guarded registry resolution, a new
  `settle_reason` context discriminator, a dedicated schema type without `on_failure`) for a
  feature whose only advantage over the post-run pass is a live in-run signal this epic's MVP
  does not need.
- Reusing `post_hook` itself with a `fire_on: [...]` mode flag — considered and rejected before
  even reaching the architect (design doc §3.3 Rev 1): the two trigger points have genuinely
  different contracts (thread, `TaskResult` availability, gating ability, cwd validity), and a
  single field carrying both would silently change `context.json`'s shape and `on_failure`'s
  meaning depending on a sibling key — landscape precedent (Airflow's separate
  `on_skipped_callback`, Dagster's distinct hook/sensor/check concepts) confirms unifying two
  genuinely different contracts into one field is the wrong direction, independent of the
  in-engine-vs-post-run question.
