# Guide: dynamic task injection (spawning tasks at run time)

A static `workflow.json` fixes its DAG at author time. Sometimes the *shape* of the work isn't
known until an agent has actually looked at the problem — "review this repo and spawn one deep
review per module that needs it" or "fix each finding and verify it, but I don't know how many
findings there'll be until I've reviewed the code." This guide covers `emit_tasks` +
`task_manifest_path`, the mechanism for that, with two fully worked examples matching common
review-driven patterns. For bounded, sequential re-execution (a fixed loop body repeating until a
gate says stop, e.g. "review → fix → review again"), see the `loops[]` construct referenced in the
[Advanced workflows](../README.md#advanced-workflows) table instead — this guide is about
*fan-out*, not iteration.

---

## The mechanism, in short

Any task can declare:

```json
{
  "id": "scope-review",
  "agent": "reviewer",
  "instruction": "instructions/scope-review.md",
  "emit_tasks": true,
  "task_manifest_path": "output/scope-manifest.json",
  "depends_on": []
}
```

`emit_tasks` and `task_manifest_path` must be set together (schema-enforced). When this task
succeeds, the engine reads the file at `task_manifest_path` — which the **agent itself** wrote as
part of doing its job — and injects every task listed there into the running DAG, then recomputes
execution order and keeps going. The manifest file format is fixed:

```json
{
  "tasks": [
    {
      "id": "some-new-task",
      "agent": "developer",
      "instruction": "instructions/developer.md",
      "depends_on": ["scope-review"],
      "inputs": ["output/some-input.md"],
      "outputs": ["output/some-output.md"]
    }
  ]
}
```

Each entry accepts the same fields as an ordinary task in `workflow.json` (`id`, `agent`,
`instruction`, `inputs`, `outputs`, `depends_on`, `retries`, `timeout_seconds`,
`skip_if_outputs_exist`, and even `emit_tasks`/`task_manifest_path` again, for nested emission).
Only `id`, `agent`, `instruction` are required.

The engine never inlines file contents into the DAG — it only ever reads *paths*, consistent with
the project's context-hygiene rule. The manifest is just another artifact the agent produces.

---

## The one thing that makes fan-out actually work: fix the aggregator's contract, not its inputs

The hard part of "spawn N things, then do one more thing after all of them" is that a
**statically-authored** downstream task can't declare `depends_on` on task IDs that don't exist yet
at author time — validation would reject an unresolvable reference. And it can't rely on the
engine's inferred input/output-path matching either, because that only works for a path that's
*fixed* — and if N sub-tasks each write to a different path, there's no single fixed path to match
against.

The fix, used in both examples below: **the emitting agent's instruction also pins the id and
output path of a single aggregator task**, and requires that aggregator to be included in the same
manifest write, with `depends_on` listing every dynamically-decided sibling. The count of siblings
is dynamic; the aggregator's own identity is not. A static downstream task can then depend on that
fixed contract two ways:

- an **inferred edge** — declare `inputs: ["output/review-summary.md"]` (the aggregator's fixed
  output path) and the engine wires the edge automatically once the DAG is rebuilt post-injection, or
- an **explicit edge** on the *emitter* (`depends_on: ["scope-review"]`) purely to guarantee the
  static task never runs before injection has even happened — the inferred edge above is what
  provides the real ordering once the aggregator exists.

This is the same trick the `sum-of-array` playground example uses for its N=1 case (`integrate`
depends on `architect-breakdown` for pre-injection ordering, and on
`output/tasks/t1/review.md` for the real post-injection edge) — the two examples below generalize
it to N decided at run time.

**Also always emit the aggregator, even for N=0.** If the emitting agent finds nothing to fan out
to, it must still write the aggregator entry (with `depends_on` on just the emitter itself, and a
trivial "nothing to do" output) — otherwise the fixed output path never gets created and the
downstream static task's input-existence check fails the run.

---

## Example 1 — reviewer fans out into N sub-reviewers, then aggregates

**Use case**: "if the reviewer wants to spawn more reviewers for detailed review of some
particular modules dynamically." A scoping task decides which modules warrant a deep review — a
count not known ahead of time — spawns one reviewer per module, then aggregates.

Runnable, schema-validated files: [`specs/examples/workflow-dynamic-fanout.json`](../specs/examples/workflow-dynamic-fanout.json),
[`specs/examples/instructions/scope-review.md`](../specs/examples/instructions/scope-review.md).

`workflow.json` (static — this is the *entire* authored DAG; everything else is injected):

```json
{
  "version": "1.0",
  "id": "dynamic-fanout-aggregate",
  "repo_set": "default-set",
  "tasks": [
    {
      "id": "scope-review",
      "agent": "reviewer",
      "instruction": "specs/examples/instructions/scope-review.md",
      "outputs": [],
      "emit_tasks": true,
      "task_manifest_path": "output/scope-manifest.json",
      "depends_on": []
    },
    {
      "id": "publish",
      "agent": "tester",
      "instruction": "specs/examples/instructions/test.md",
      "inputs": ["output/review-summary.md"],
      "outputs": ["output/published.md"],
      "depends_on": ["scope-review"]
    }
  ]
}
```

`scope-review`'s instruction file tells the reviewer agent to inspect the repo, pick N modules,
and write `output/scope-manifest.json` — for a 3-module run, something like:

```json
{
  "tasks": [
    {
      "id": "subreview-auth",
      "agent": "reviewer",
      "instruction": "specs/examples/instructions/test.md",
      "depends_on": ["scope-review"],
      "inputs": ["src/auth"],
      "outputs": ["output/reviews/auth.md"]
    },
    {
      "id": "subreview-billing",
      "agent": "reviewer",
      "instruction": "specs/examples/instructions/test.md",
      "depends_on": ["scope-review"],
      "inputs": ["src/billing"],
      "outputs": ["output/reviews/billing.md"]
    },
    {
      "id": "subreview-scheduler",
      "agent": "reviewer",
      "instruction": "specs/examples/instructions/test.md",
      "depends_on": ["scope-review"],
      "inputs": ["src/scheduler"],
      "outputs": ["output/reviews/scheduler.md"]
    },
    {
      "id": "aggregate-review",
      "agent": "reviewer",
      "instruction": "specs/examples/instructions/test.md",
      "depends_on": ["subreview-auth", "subreview-billing", "subreview-scheduler"],
      "inputs": ["output/reviews/auth.md", "output/reviews/billing.md", "output/reviews/scheduler.md"],
      "outputs": ["output/review-summary.md"]
    }
  ]
}
```

Note the whole batch — all four entries — is written in one file by `scope-review`, and injected
atomically in one call, so `aggregate-review` can validly `depends_on` its three siblings even
though none of them existed when `workflow.json` was authored. `publish` (static, authored up
front) picks up the aggregator's output automatically via the inferred `inputs` edge — it never
needed to know there'd be 3 sub-reviews, or even that there'd be an `aggregate-review` task with
that exact id, just the *path* its instruction promised to always produce.

---

## Example 2 — reviewer dynamically triggers a developer → tester fix pipeline

**Use case**: "if the reviewer actually wants to invoke developer to do code changes and then
tester to actually build/deploy/test the changes." The reviewer doesn't just report findings — for
auto-fixable ones, it kicks off a fix-and-verify chain per finding, count unknown up front.

Runnable, schema-validated files: [`specs/examples/workflow-dynamic-pipeline.json`](../specs/examples/workflow-dynamic-pipeline.json),
[`specs/examples/instructions/review-and-triage.md`](../specs/examples/instructions/review-and-triage.md).

`workflow.json` (static):

```json
{
  "version": "1.0",
  "id": "dynamic-review-fix-pipeline",
  "repo_set": "default-set",
  "tasks": [
    {
      "id": "review",
      "agent": "reviewer",
      "instruction": "specs/examples/instructions/review-and-triage.md",
      "outputs": ["output/review.md"],
      "emit_tasks": true,
      "task_manifest_path": "output/fix-manifest.json",
      "depends_on": []
    },
    {
      "id": "final-report",
      "agent": "tester",
      "instruction": "specs/examples/instructions/test.md",
      "inputs": ["output/verify-summary.md"],
      "outputs": ["output/final-report.md"],
      "depends_on": ["review"]
    }
  ]
}
```

For 2 auto-fixable findings, `review`'s manifest at `output/fix-manifest.json` looks like:

```json
{
  "tasks": [
    { "id": "fix-null-check", "agent": "developer", "instruction": "instructions/developer.md",
      "depends_on": ["review"], "inputs": ["output/review.md"],
      "outputs": ["output/fixes/null-check.md"] },
    { "id": "verify-null-check", "agent": "tester", "instruction": "instructions/tester.md",
      "depends_on": ["fix-null-check"], "inputs": ["output/fixes/null-check.md"],
      "outputs": ["output/verified/null-check.md"] },

    { "id": "fix-missing-index", "agent": "developer", "instruction": "instructions/developer.md",
      "depends_on": ["review"], "inputs": ["output/review.md"],
      "outputs": ["output/fixes/missing-index.md"] },
    { "id": "verify-missing-index", "agent": "tester", "instruction": "instructions/tester.md",
      "depends_on": ["fix-missing-index"], "inputs": ["output/fixes/missing-index.md"],
      "outputs": ["output/verified/missing-index.md"] },

    { "id": "verify-summary", "agent": "tester", "instruction": "instructions/tester.md",
      "depends_on": ["verify-null-check", "verify-missing-index"],
      "inputs": ["output/verified/null-check.md", "output/verified/missing-index.md"],
      "outputs": ["output/verify-summary.md"] }
  ]
}
```

Each finding becomes its own `fix-<id>` → `verify-<id>` chain (a two-hop pipeline, not a single
task — this generalizes directly to more hops, e.g. `fix` → `build` → `deploy` → `verify`, by
adding more agents to each chain in the manifest). `verify-summary` is the fixed aggregator, same
role as `aggregate-review` in example 1. `final-report` is static and depends on it via the same
inferred-edge trick. If `review` finds zero auto-fixable issues, it still emits a `verify-summary`
entry — `depends_on: ["review"]`, no `inputs` — that just notes there was nothing to fix, so
`final-report`'s input path always exists.

---

## Gotchas

- **Injected tasks skip the strict schema/reference checks static tasks get.** `workflow.json`'s
  authored tasks are validated against `workflow.schema.json` (rejects unknown fields, bad id
  patterns, unknown agent names) *and* cross-validated (checks every `depends_on` target actually
  exists). Tasks read out of a manifest only get Pydantic's field-level type checking — a typo'd
  agent name or a `depends_on` id that doesn't match anything is **not** caught at manifest-read
  time.
- **A `depends_on` id that doesn't resolve to any real task crashes the run, ungracefully.** The DAG
  builder creates a phantom node for an unknown dependency id rather than rejecting it up front;
  the engine then hits an uncaught `KeyError` trying to run it, which surfaces as a raw traceback
  instead of a clean `failed` run. Double-check that every id an emitted task's `depends_on`
  references was actually included in the same or an earlier manifest batch — this is the most
  common way a hand-rolled instruction file breaks in practice.
- **Injected task ids must be globally unique** across the whole run (not just within one
  manifest) — colliding with any existing static, injected, or prior-batch id fails the run
  cleanly (`InjectionError`, not a crash). Namespace by something the emitting agent controls and
  knows is unique, e.g. `subreview-<module-name>` or `fix-<finding-id>`, not a bare counter that
  could collide across re-runs or nested emitters.
- **There's no built-in cap on fan-out width or nesting depth.** If runaway fan-out is a concern,
  bound it in the instruction file itself (e.g. "emit at most 8 sub-review tasks; if more than 8
  modules qualify, pick the 8 highest-risk ones and note the rest were skipped").
- **Token/rate budgets apply to injected tasks exactly like static ones.** If `workflow.json`
  declares a `budget` block, a wide fan-out consumes it proportionally and can trip
  `on_exhaustion` mid-run — size the budget for the worst-case N, not the typical case, if the
  fan-out width is agent-decided.
- **Resume works correctly with partially-completed fan-outs.** Injected tasks are persisted in run
  state, so if a run is interrupted with 2 of 3 sub-reviews done, `ao resume` skips the completed
  ones and continues from there — you don't lose the fan-out on resume.

---

## Where else to look

- [`specs/examples/workflow-dynamic.json`](../specs/examples/workflow-dynamic.json) — the minimal
  schema-shape reference for `emit_tasks`/`task_manifest_path` (no worked instruction contract).
- [`playground/sum-of-array/`](../playground/sum-of-array/) — a fully worked, actually-tested N=1
  pipeline (`architect-breakdown` emits a fixed 3-task chain), runnable against both a fake
  executor and a real Claude CLI.
- [`docs-md/e2e-playground-testing.md`](e2e-playground-testing.md) — the design doc behind the
  playground harness, including the open question (OQ-2) this guide's fixed-aggregator pattern
  resolves for the N>1 case.
- [`docs-md/token-budgeting-hld.md`](token-budgeting-hld.md) — budget/rate-limit semantics
  referenced in the Gotchas section above.
