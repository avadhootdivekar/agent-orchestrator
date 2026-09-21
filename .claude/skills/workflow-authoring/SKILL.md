---
name: workflow-authoring
description: Use when decomposing a complex task into an `ao` workflow spec (DAG) — sizing tasks, choosing a static task list vs. `emit_tasks` dynamic expansion, deciding on `isolation`/`max_parallel`, placing `pre_hook`/`post_hook` and post-run grading, and avoiding known DAG-authoring failure modes. Reach for this BEFORE writing or reviewing a `workflow.json`/`workflow.yaml` spec for `ao run`/`ao validate`, or before answering "how should I break this epic into ao tasks."
---

# Skill: Workflow authoring (`ao` DAG decomposition)

Teaches how to decompose a complex task into a well-formed `ao` workflow spec: a `WorkflowSpec`
(`specs/workflow.schema.json`) whose `tasks[]` form a DAG of `TaskSpec` entries, each pointing an
`agent` (from `specs/agents.schema.json`) at an `instruction` file, with `inputs`/`outputs` as
artifact paths and `depends_on` (or inferred input/output-path matches) wiring the edges. This
skill is about **decomposition judgment**, not a full CLI/schema reference — for the exhaustive
field list, read `specs/workflow.schema.json` directly; for CLI flags, `ao --help` / `ao <cmd>
--help`.

> **Every concrete field/flag name below was checked against `specs/workflow.schema.json`,
> `specs/agents.schema.json`, and `src/agent_orchestrator/` source at the time this skill was
> written (2026-09).** HLDs under `docs-md/` describe *design intent* and can drift from what
> shipped — when in doubt, re-check the schema file and source over any HLD prose, this skill
> included. If you find this skill inaccurate, update it (mirroring `repo-intel/SKILL.md`'s own
> self-improvement convention).

---

## Quick decision guide

| You're deciding... | Rule of thumb |
|---|---|
| How big should one task be? | One task = one artifact-boundary contract you can name before writing the instruction. See "Task sizing" below — not a token/day count. |
| Static task list or `emit_tasks`? | Static, unless the *number or identity* of downstream tasks can only be known after an upstream task runs. See "Static vs. dynamic". |
| `isolation: worktree` or `none`? | `none` (the default) unless tasks run with `max_parallel > 1` AND their declared outputs can genuinely overlap, or you want per-task git isolation for other reasons (rebase-safe parallel dev). See "Isolation & parallelism". |
| Where does `pre_hook`/`post_hook` fit? | `pre_hook` for a cheap, fast-failing precondition gate (fail the task, don't waste an agent dispatch). `post_hook` for deterministic post-processing/scoring that shouldn't cost an LLM turn. See "Hooks & grading". |
| Where does grading fit? | Post-run, via `ao report-outcomes --grade <hook-name>` — not wired into the run itself. See "Hooks & grading". |
| My run has file contention between parallel tasks | Either make `outputs`/`touches` genuinely disjoint, or opt into `isolation: worktree`. `ao` does **not** detect or prevent this for you at `isolation: none`. See "Common failure modes" #4. |

---

## Task sizing

There is no single right task size — CLAUDE.md's "tasks ≤3 days" is an *architect-planning
ceiling*, not a floor or a target. Size each task by asking two questions, not a duration:

1. **Can you name its artifact-boundary contract before writing the instruction?** A task's
   `inputs`/`outputs` are how the engine wires the DAG (`build_dag` also infers edges from
   matching input/output paths, not only `depends_on` — see "Common failure modes" #5) and how
   `skip_if_outputs_exist`/resume decide whether a task's work is already done. If you can't
   state "this task reads X and Y, and its only job is to produce Z," the task is either too
   broad (bundling unrelated concerns — split it) or too narrow (an artifact-less
   micro-step — merge it into its neighbor).
2. **Is it a safe retry/resume unit?** Every task retries as a whole (`retries`/`max_attempts`)
   and a resumed run (`ao resume`) replays from the last completed artifact set, not from inside
   a task. A task that does two independent things means a transient failure in the second thing
   re-does the first thing's (possibly expensive) work too. Prefer splitting "do A, then verify
   A, then do B" into separate tasks so a retry doesn't redo A when only B's verification failed.

**Precedent worth reusing** (`docs-md/granular-task-decomposition-hld.md`, still-backlog design,
not shipped tooling — but the *sizing heuristic* is sound and worth applying by hand even without
its automated step-planner): size a unit of work to "one focused change, roughly one module, ~≤5
files," sized to finish inside a generous agent turn budget. The same doc names the two failure
modes at either extreme, both worth avoiding when you size tasks by hand:
- **Over-fragmentation**: many tiny tasks where per-task overhead (agent startup, context
  hand-off, artifact I/O) dominates real work, and the "handoff" between tasks (what the next
  task reads to know where the previous one left off) becomes the weakest link — a vague
  `outputs` artifact forces the next task to re-derive context it should have been handed
  directly.
- **Under-fragmentation**: one task that does too much accumulates integration drift risk (each
  piece would have compiled/passed alone; the whole doesn't) and forces a full re-run of
  everything on any partial failure.

If a task's own instruction would need to say "first do X, then do Y, then do Z" where X/Y/Z have
*independently checkable* done-states, that's usually three tasks with real `depends_on`/artifact
edges between them, not one task with an internal checklist the engine can't see or resume into.

---

## Static task list vs. dynamic (`emit_tasks`)

`TaskSpec.emit_tasks: true` (with `task_manifest_path`) lets a task write a manifest of further
tasks that the engine injects into the run at execution time (dynamic fan-out/routing decided at
run time, not spec-authoring time).

**Use a static task list when** you already know, at spec-authoring time, exactly which tasks
exist and how they depend on each other — the common case. It validates fully at `ao validate`
time (schema + cross-validation), is easier to reason about, and every id/dependency is checked
before anything runs.

**Use `emit_tasks` when** the *number or identity* of downstream tasks depends on something only
an upstream task can determine — e.g. "review N files, where N isn't known until a scoping task
runs" (`specs/examples/workflow-dynamic-fanout.json` is exactly this shape: a `scope-review` task
emits an unknown-N set of reviewer tasks). Rules that matter here (see "Common failure modes" for
the failure-mode framing of each):
- An emitter task **must** set `skip_if_outputs_exist: false`. The skip path never reaches the
  injection hook, so a skipped emitter on a resumed/re-run workflow silently strands every
  downstream consumer of the fan-out.
- For unknown-N fan-out, emit a **fixed-id, fixed-path aggregator** alongside the dynamic
  siblings (even when N=0) so a static downstream task can attach to it via a normal
  `depends_on`/inferred-path edge, rather than needing to know the dynamic siblings' ids.
- Namespace emitted task ids so they stay globally unique for the run, not just unique within
  one manifest.
- **Injected tasks are not re-validated the way static ones are** — an injected manifest with a
  bad `depends_on`/`agent`/hook reference does not fail the same clean way a static authoring
  mistake would at `ao validate` time. Be more careful constructing an emitted manifest than a
  static task list; a bad reference here degrades ungracefully rather than failing fast. (Tracked
  as a real engine gap, not something this skill can work around: see
  `meta/tickets/E-Grpp0X-injected-task-dag-validation-gap/`.)

**Routing** (`WorkflowSpec.branches[].routes`, `router_task_id`/`verdict_path`) is a related but
distinct mechanism — a router task writes `{"routes": [...]}` and the engine activates only the
named route's cone. Use routing when you have a **fixed, known set of alternative paths** (e.g.
"if review passes, deploy; if it fails, open a fix loop") rather than a variable-N fan-out.
`ao validate` requires **every route to own a real sink task inside its own exclusive cone** — a
`join` task shared across multiple routes does not satisfy this; give each route its own terminal
task (or have each route's last task itself be the join-eligible one via `join: "any"`/`"all"`
on a downstream task that depends on tasks from more than one route).

---

## Isolation & parallelism

`max_parallel` is a **run-level setting**, not a workflow-spec field — set it via
`ao run --max-parallel N`, `AO_MAX_PARALLEL`, or `.ao/config.yaml`'s `max_parallel`. Default is
`1` (serial). `TaskSpec.isolation` (`"none" | "worktree" | "inherit"`, default `"inherit"` →
resolves `defaults.isolation`, workflow-level default `"none"`) is a **spec-level, per-task**
field.

**Default (`isolation: none`) is fine when:**
- `max_parallel` stays at `1` (serial — there's nothing to collide with), or
- co-scheduled tasks' `outputs` (and realistic incidental writes) are genuinely disjoint by
  construction.

**Reach for `isolation: worktree` when** you run with `max_parallel > 1` and co-scheduled tasks
*could* touch overlapping files — `ao` does **not** check for output overlap between co-scheduled
tasks at `isolation: none`; keeping outputs disjoint is entirely the spec author's responsibility,
and a live consumer workflow has already hit real file contention this way. `TaskSpec.touches`
(glob hints) only ever *prefers* non-overlapping co-scheduling — it is a soft signal, **never a
gate**, and may be incomplete. `isolation: worktree` runs the task in its own git worktree and
integrates by squash + rebase, which is what actually prevents the collision (at the cost of
rebase/conflict-resolution overhead — see `docs-md/task-isolation-hld.md` for the full mechanism
and its conflict ladder). One caveat carried in `meta/ROADMAP.md` §4: isolation suppresses git
*hooks* but not `filter`/`merge` git-attribute drivers — don't run an isolated task against a repo
with an expensive or untrusted driver configured.

**Caching vs. parallelism — a real trade-off to weigh, not a bug to work around** (ADR-0015 §1.5):
when `max_parallel` dispatches N tasks that share a byte-identical prompt prefix (same
instructions/tools/static content — the common, desired case once cache-scope is set up
correctly), only the **first response to begin streaming** can write the cache entry; every other
concurrently-in-flight request has already been sent and cannot read what the first is still
writing. All N pay full cache-miss cost **within that one wave**. This is structural (confirmed
by Anthropic's own prompt-caching docs), not something to build around — serializing submission to
"warm" the cache would fight `max_parallel`'s entire purpose. You get the caching win "for free"
on a workflow's **second and later runs** (a fresh cache entry from run N's first-streaming
request is readable by run N+1's tasks within the TTL window), not within one run's own parallel
fan-out. When you *do* use `isolation: worktree` (which puts each task in a different working
directory), also set `AgentSpec.exclude_dynamic_system_prompt_sections: true` on the agents
involved — otherwise Claude Code's default system prompt bakes the per-worktree cwd into the
cached prefix itself, defeating cross-task cache reuse for an unrelated reason (this is
independent of the parallelism trade-off above; it applies even serially across different
worktrees).

---

## Hooks & grading

`pre_hook`/`post_hook` (`TaskSpec` fields, referencing a named entry in `WorkflowSpec.hooks` via
`{"use": "<name>"}`) run a deterministic **argv command** (never a shell string) around a task's
agent dispatch — see `specs/examples/workflow-hooks.json` for the shape.

- **`pre_hook`**: use for a cheap, fast precondition check that should stop the task *before*
  spending an agent turn — e.g. disk-space/quota checks, "does the expected input file actually
  exist and look sane." Default `on_failure` for a hook itself is left to the hook definition;
  `HookRef.on_failure` can override per use-site. A failing `pre_hook` should usually
  `fail_task` (the schema's `on_failure: "fail_task"|"ignore"` per hook/per-use) — don't waste an
  LLM dispatch on a precondition you already know is false.
- **`post_hook`**: use for deterministic post-processing that doesn't need an LLM — e.g. a
  scoring/grading script, a format check, a cheap sanity assertion on the produced artifact.
  Prefer `on_failure: "ignore"` for anything advisory (e.g. a grading hook shouldn't fail the
  task over a low score) and `"fail_task"` only when the hook is a genuine correctness gate.
- **Don't** use hooks for anything that needs judgment/LLM reasoning — that's what the task's own
  `agent` dispatch is for. Hooks are for cheap, deterministic, scriptable checks only.

**Post-run grading** is a *separate* mechanism from `post_hook`, deliberately not wired into the
run itself: `ao report-outcomes --run-id <id> --grade <hook-name>` resolves `<hook-name>` from the
workflow's own `hooks` registry and grades every **settled** task uniformly — dispatched and
`skip_if_outputs_exist`-skipped tasks alike — with zero engine involvement. Reach for this when
you want an accuracy/quality signal over a whole run's outcomes *after the fact* (e.g. for a
benchmark or an A/B comparison), rather than gating individual tasks on it during the run. A hook
used for `--grade` doesn't need a `post_hook`/`pre_hook` reference anywhere in `tasks[]` — it only
needs to exist in `WorkflowSpec.hooks`.

---

## Common failure modes to avoid

Grounded in this repo's own incident/learning history (`meta/learnings.md`,
`meta/learning-compact.md`, `meta/ROADMAP.md` §4) — check a draft spec against this list before
calling it done:

1. **Emitter strands its consumers on resume.** An `emit_tasks: true` task with
   `skip_if_outputs_exist: true` never re-injects its manifest on a skip. Always
   `skip_if_outputs_exist: false` on emitters.
2. **Unknown-N fan-out with no fixed attachment point.** Downstream static tasks need a
   fixed-id/fixed-path aggregator to depend on — they can't `depends_on` ids that don't exist yet
   at authoring time.
3. **A route with no real sink in its own cone.** `ao validate` rejects a shared `join` task as a
   route's sink; give each route its own terminal task.
4. **Parallel tasks with overlapping declared outputs, `isolation: none`.** Silently unchecked;
   the spec author must keep outputs disjoint or opt into `isolation: worktree`. This is the
   single highest-value thing to double-check before shipping any spec with `max_parallel > 1`.
5. **Accidental cycles from matching input/output paths.** `build_dag` infers edges from matching
   `inputs`/`outputs` paths, not just `depends_on` — if you clone/template a task (e.g. per-loop
   iteration), clear its `inputs`/`outputs` on the clone or it can wire a spurious edge back to an
   unrelated task and produce a false `CycleError`.
6. **A hand-constructed `emit_tasks` manifest with a bad `depends_on`.** Unlike a static task
   list, this is not caught cleanly by validation today (see `E-Grpp0X-injected-task-dag-validation-gap`)
   — double-check emitted manifests' `depends_on`/`agent` references by hand.
7. **One task doing two independently-retriable things.** See "Task sizing" — a partial failure
   re-does the whole task, including the part that already succeeded.

---

## Worked example

`specs/examples/workflow-doc-audit.json` (with `specs/examples/instructions/`) is a full worked
decomposition following this skill's own guidance — read it alongside this skill to see the
rules above applied to a real, moderately-complex task. Schema/validation evidence for that
example lives in its own ticket
(`meta/tickets/E-DOiDqE-workflow-authoring-skill/T-PLsJdO-worked-example/`).

---

## Self-improvement

If you find this skill inaccurate against current `specs/workflow.schema.json` /
`specs/agents.schema.json` / `src/agent_orchestrator/` behavior, update this file — the same
convention `repo-intel/SKILL.md` follows. Report a genuinely new `ao` gap you hit while following
this skill (not fixed here) the way C0's audit did: as its own ticket under `meta/tickets/`, not
folded into whatever you were actually doing.
