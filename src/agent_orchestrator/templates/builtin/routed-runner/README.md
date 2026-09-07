# Built-in template: `routed-runner`

Turns a raw, possibly vague `prompt.md` into a **completed, tested change of any
supported type**, with one `ao new` call. A `classify` router reads the prompt and
drives only the matching pipeline; the untaken routes never run, never dispatch, never
bill, and never fail the run (they end `not_taken`).

This is the finplan-free generalization of the `ao-runner-finplan` epic-runner
(`workflows/epic-runner/new-epic-run.sh`) — same DAG, same routes, same breakers, same
breakdown contract, with every project-specific reference removed. See
`docs-md/workflow-templates-hld.md` §2.8 for the design rationale.

## Route table

| Type | Pipeline | Endpoint (route sink) |
|------|----------|-----------------------|
| `bug` | triage/repro → fix → regression-test → review → push | `bug-push` |
| `epic` | refine → survey → design → review → design-final → task-breakdown ⇒ per-task fan-out → aggregate → full-test → push | `epic-push` |
| `task` | plan (design-lite) → implement → test → review → fix → re-test → push | `task-push` |
| `documentation` | plan → write → review → push | `doc-push` |
| `testing` | gap-analysis → write-tests → run → push | `testing-push` |

Shared **head** (always runs, `skip_if_outputs_exist: false`): `git-branch-off` →
`classify` (router). The `type` param forces a route by writing
`outputs/forced-type.txt`, which `classify` echoes without analysis (it still runs, so
routing fires deterministically — a *skipped* router never routes, per the engine's
router hook); omit `type` to auto-classify from `prompt.md`.

## Required agents

`architect`, `architect-opus`, `developer`, `full-tester`, `git-operator`, `manager`,
`market-surveyor`, `merge-resolver`, `reviewer`, `reviewer-opus`, `tester` — the
workspace's `agents:` config must resolve all eleven (`manager` is used only by the
epic route's dynamically injected aggregator task, never by a statically-declared
task; `merge-resolver` is used only when a task opts into `isolation: worktree` and
`integration.ladder` reaches its `"llm"` tier — see "Parallel isolation" below — but
both are part of this template's contract per `breakdown-contract.md`/`template.yaml`).

## Params

- `type` (optional, enum `bug|epic|task|documentation|testing`) — force the route;
  omit to let `classify` decide from the prompt.
- `repo_set` (**required**, no default) — key into the workspace's reposet config; the
  repo(s) every stage operates on. Every workspace names its own; there is no sensible
  cross-workspace default.
- `task_budget_usd` (optional, default `75`) — per-task real-spend USD cap.
- `run_budget_usd` (optional, default `1500`) — per-run real-spend USD cap.

## Limits & breaker rationale

Generous by policy — the run should stop on real problems, not on tight defaults.

- **Budgets** (real measured spend, not estimates): `task_cost_usd ≥ task_budget_usd`
  fails the task, `run_cost_usd ≥ run_budget_usd` stops the run. Cumulative across
  retry attempts. Both are `mode:"recommend"`: the zero-cost rule-based monitor grants
  ONE bounded auto-extension (doubling the cap) before a repeat trip hard-stops for
  real — unattended long epics don't stall on a legitimate near-miss.
- **Time**: `run_wall_clock_seconds ≥ 432000` (5-day calendar deadline — pause/quota
  gaps count, since `started_at` is frozen across resume) and
  `run_active_seconds ≥ 432000` (5 days of summed settled-task work time, immune to
  pause/resume gaps). Also `mode:"recommend"`.
- **Retries**: every task gets `max_attempts: 3` with 60s backoff. Claude
  quota-exhaustion waits consume neither attempts nor budget.
- **Guards**: `consecutive_failures ≥ 3` (systemic breakage — stop burning budget on a
  broken branch/env) and `injected_task_count > 115` (runaway fan-out guard: the
  breakdown contract caps epic tasks at 20, so 20 × 5 pipeline stages + 1 aggregator =
  101 legitimate injections; 115 keeps slack above that ceiling without masking a true
  runaway). Both `mode:"recommend"`.
- **Pause gates are staged** (`human-input-gate[-2][-3]`, `operator-kill[-2]`) because a
  tripped `stop_file` breaker **latches** for the life of the run — an already-tripped
  id never re-halts a resumed run, and `stop_file` breakers have no threshold, so they
  can't be `--extend-breaker`'d. Each gate is one-shot per run; agents/operators use the
  first flag that doesn't exist yet.
- **`task-breakdown` and `full-test`/`test-run` keep `skip_if_outputs_exist: false`**
  deliberately: a *skipped* `emit_tasks` task never re-injects its manifest, which
  would strand the aggregator and fail the run on any re-run; the final verdict tasks
  must always re-verify rather than trust a stale report from an earlier attempt.
  `git-branch-off` and `classify` are `skip_if_outputs_exist: false` for the same
  reason — a skipped router never routes, and branch state must be re-verified on
  every run.
- **Per-route terminal push, not one shared sink**: `ao validate` requires every route
  to own a *sink* (a task with no successors) inside its exclusive cone. A single
  shared push (`join: "any"`) is reachable from every route, so it sits in *no* cone
  and leaves every route sink-less — validation rejects it. Five per-route push tasks
  (sharing one instruction, `90-final-push.md`) each sit in their own cone as that
  route's sink; only the taken route's push actually executes at run time.

## Human-in-the-loop

Any agent that hits a genuine blocker writes `<run>/needs-input/<task-id>.md`
describing exactly what it needs, then touches the first `<run>/control/pause*.flag`
that doesn't exist yet. The matching `human-input-gate[-N]` breaker trips at the next
task boundary and the run pauses, resumable via `ao run --resume`/`ao resume` (per the
core templates module). Delete the flag after answering, before resuming.

## Instructions materialize per workspace

`instructions/` is copied into the workspace once, at
`workflows/routed-runner/instructions/`, with `keep_existing: true` — so a workspace
can hand-tune a stage's behavior for its own conventions without the template
overwriting those edits on the next `ao new` call. The instructions are deliberately
path-generic: each agent gets its concrete paths from its prompt's `Repos`/inputs list
rather than from anything hardcoded here, so the same instruction set works regardless
of which repo(s) `repo_set` points at.

## Task-breakdown contract

The epic route's `task-breakdown` stage (`07-task-breakdown.md`) doesn't know the
implementation DAG until the design exists, so it runs with `emit_tasks: true` and
injects one 5-stage pipeline per epic task (implement → write tests → review → fix →
re-test) plus exactly one aggregator with a **fixed id and output path**
(`aggregate-epic-tasks` → `all-tasks-complete.md`). `full-test` waits on that fixed
path without knowing the fan-out width in advance. `breakdown-contract.md` (rendered
per-instance from `breakdown-contract.md.tmpl`) gives the breakdown agent the exact
ids/paths/JSON shapes to emit — a malformed manifest (dangling `depends_on`, renamed
aggregator) crashes or hangs the run, so the contract is authoritative over the general
instruction where they'd ever disagree.

Each emitted entry may also set `effort` (`low`/`medium`/`high`/`xhigh`) and `model`,
which win over the dispatched agent's own values for that one task (ADR-0003 decision 2).
The contract defaults `impl`/`test` passes to `"effort": "medium"` — a task sized to
finish in roughly 10 minutes — and leaves the breakdown agent free to mark a genuinely
larger `<tid>` `"high"`/`"xhigh"` and/or pin a different `model`, instead of forcing an
artificial split. See the contract's "Effort & model per task" section.

Each entry may also set `touches` (a best-effort glob-list hint) and `isolation`
(`none`/`worktree`/`inherit`) — see the contract's "`touches` & `isolation` per task"
section and "Parallel isolation" below.

## Parallel isolation

This template ships with isolation **off by default** — `defaults.isolation: "none"`
in `workflow.json.tmpl`, and `git-branch-off` is pinned to `"isolation": "none"`
explicitly (it creates the epic branch itself, so it must always run in the shared,
synced checkout). Rendering with default params is byte-identical to a workflow with
no isolation fields at all, and `ao validate` reports no isolation-related warnings.

**What `isolation: "worktree"` does.** Setting it on a task (directly, or via
`defaults.isolation: "worktree"`) runs that task alone in a private git worktree on
branch `ao/<run_id>/<task_id>`, instead of the shared checkout. Sibling tasks running
concurrently never see each other's uncommitted state. When the task finishes, the
engine auto-commits anything left staged, then integrates (rebase + land) the branch
back onto the run's integration head — see `docs-md/task-isolation-hld.md` §7-§9 for
the full mechanics. Worktrees live under `$AO_STATE_DIR/worktrees/<workspace>/<run
id>/<task id>/<repo>` (default `~/.local/state/ao`, override via `AO_STATE_DIR` or
`AO_WORKTREE_ROOT`) — never inside your checkout.

**How to opt in.** This template does not parameterize isolation (opt-in stays
opt-in by design — see the risk note in this ticket's `TASK.md`). To use it, edit your
own instantiated `workflow.json` after `ao new`:

1. Add a top-level `integration` block (optional fields shown; omit what you don't
   need — every field defaults sensibly per `specs/workflow.schema.json`):

   ```json
   "integration": {
     "verify_command": ["make", "test"],
     "resolver_agent": "merge-resolver",
     "resolvers": { "union": ["**/*.md", "**/registrations.txt"] }
   }
   ```

   `verify_command` is the argv (never a shell string) run in a task's worktree after
   rebase, before landing — leaving it unset falls back to a built-in structural check.
   `resolver_agent` names the agent used when the resolver ladder reaches its `"llm"`
   tier (this template's `merge-resolver` entry, which must declare
   `disallowed_tools: ["WebFetch", "WebSearch"]` in your `agents:` config — S-2; the
   engine force-injects that pair onto the T2 dispatch regardless, but a workspace
   agent entry that already lists them keeps `ao validate` free of the V10 warning).
   `resolvers.union` lists append-only-registration files (see
   `conflict-friendly-coding.md`, rule 2) that a mechanical union merge can resolve
   without ever invoking the LLM tier.
2. Set `"isolation": "worktree"` on the specific task ids you want isolated. This
   template's per-task-type instructions are isolation-aware (worktree-tolerant
   branch checks, no `git push` directive) for the epic route's dynamically-injected
   fan-out (`impl1-<tid>`/`test1-<tid>`/`review-<tid>`/`impl2-<tid>`/`test2-<tid>`,
   via `08`-`11-*.md`), the single-task route (`task-impl`/`task-test`/`task-review`/
   `task-fix`/`task-retest`, via `31`-`35-*.md`), the bug route
   (`bug-fix`/`bug-test`/`bug-review`, via `21`-`23-*.md`), the documentation route
   (`doc-plan`/`doc-write`/`doc-review`, via `40`-`42-*.md`), and the testing route
   (`test-gap-analysis`/`test-write`/`test-run`, via `50`-`52-*.md`).

   Do **not** set a blanket `defaults.isolation: "worktree"` — two things are not
   covered by isolating "everything": `bug-triage` (`20-bug-triage.md`) is not yet
   isolation-aware (its branch-safety check would STOP the task on an
   `ao/<run_id>/<task_id>` branch, a real but self-contained failure — it never
   commits or pushes anything, so nothing is lost, but the task fails and needs a
   follow-up before it is isolation-safe); and the five route-terminal push tasks
   (`bug-push`/`epic-push`/`task-push`/`doc-push`/`testing-push`) are explicitly
   pinned `"isolation": "none"` in `workflow.json.tmpl` and must stay that way —
   `90-final-push.md` makes the route's real `git push`, which must run against the
   shared, synced checkout (see that file's own "This task always runs unisolated"
   note). Two tasks are code-forced to `"none"` regardless of any default —
   `classify` (a router) and `task-breakdown` (`emit_tasks`) — because
   `models._is_structural_task`/V4 always exclude router/`emit_tasks`/loop-gate
   tasks; `git-branch-off` is **not** code-forced the same way — it stays `"none"`
   solely because of the explicit pin above, a template-authoring choice, not an
   engine guarantee.
3. Copy the packaged `conflict-friendly-coding.md` (find it via
   `python -c "import agent_orchestrator.templates as t, pathlib;
   print(pathlib.Path(t.__file__).parent / 'instructions' /
   'conflict-friendly-coding.md')"`) into your workspace, then add that
   **workspace-relative** copy to `general_instructions` — `.ao/config.yaml`,
   `AO_GENERAL_INSTRUCTIONS`, or `--general-instruction` — so every isolated task
   follows the append-only/new-file/focused-diff rules that make the union resolver
   correct. A copy, not the install path, because `general_instructions` resolves
   through the same workspace-root path guard as every other artifact path (NFR-1) —
   an install-tree path is never inside your workspace_root.

**Unreviewed replays (S-5).** With `verify_command` unset, a `rerere`-tier conflict
replay is functionally **unreviewed** — nothing runs to confirm the mechanical replay
actually kept the code working. Set a real `verify_command` (build + test, or your
project's own smoke check) on any repo where isolation is more than a convenience.

**Cold rebuilds and shared caches (NFR-6).** `git worktree add` copies tracked files
only — an ignored build-output directory (e.g. a 100+ GB Rust `target/`) is never
duplicated — but each worktree still starts from a *cold* build cache, which can
dominate wall-clock time on heavy-build repos. In order of preference:
1. Point your build tool at an out-of-tree, worktree-independent cache directory
   (e.g. Cargo's `CARGO_TARGET_DIR` via `.cargo/config.toml`, or `sccache`/`ccache`) so
   concurrent worktrees share one warm cache; the build tool's own locking serializes
   concurrent writers rather than corrupting the cache.
2. Keep heavy stages (`full-test`) at `isolation: none` — they already run as
   barriers in the shared, synced checkout, where the warm cache lives.
3. Isolate the fast-moving repo in a multi-repo `repo_set` and leave the heavy one
   shared.

## Existing run instances keep their old contract

`breakdown-contract.md` is generated **per run**, at `ao new` time. A run directory
created before this template gained `touches`/`isolation` keeps whatever contract it
was scaffolded with — this template does not retroactively migrate existing run
instances.
