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
`market-surveyor`, `reviewer`, `reviewer-opus`, `tester` — the workspace's `agents:`
config must resolve all ten (`manager` is used only by the epic route's dynamically
injected aggregator task, never by a statically-declared task, but it is part of this
template's contract per `breakdown-contract.md`).

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
