# Built-in template: `overseer-runner`

## Summary

`overseer-runner` takes one open-ended `prompt.md` — which may hold several distinct asks — and
works it to completion in bounded **waves**. Each wave is a dynamically injected sub-DAG of work
units; a periodic **checkpoint** task (`ck-JJ`) closes every wave by reading a deterministic
digest of run state (budget, cadence, loop/rework signals, progress), judging alignment, and
emitting exactly one of: the next wave plus the next checkpoint, a stabilization wave plus the
next checkpoint, a human-input hold, or the fixed close-out tail. Budget is staged
(`explore → converge → stabilize → closeout`, at `converge_pct`/`stabilize_pct`/`closeout_pct` of
`run_budget_usd`) so the run steers itself toward a **usable** deliverable well before the hard
`run_cost_usd` backstop at 100% ever trips. See `docs-md/overseer-runner-hld.md` for the full
design; `overseer-contract.md` (rendered per instance) is the authoritative, machine-checkable
contract every checkpoint's manifest and verdict must satisfy.

## The DAG shape

Only two tasks are statically declared in `workflow.json`: `git-branch-off` (creates the run's
branch) and `intake` (turns `prompt.md` into a locked charter and emits wave 1 plus `ck-01`).
**Everything else — every wave unit, every later checkpoint, and the close-out tail — is
emitted at run time**, by `intake` or by whichever checkpoint decides to keep going.

There is deliberately **no static tail task**. A statically-declared `final`/`closeout` task
could only `depends_on: intake` (later checkpoint/unit ids don't exist yet at spec-authoring
time), so it would become dispatch-ready right after `intake` settles and fail immediately with
`missing_inputs` — its real input (the last checkpoint's verdict) has no producer yet, and won't
for however many waves the run actually takes (D8 in `ADR-0016`). So the close-out chain
(`final-verify → closeout → final-push`) is itself part of the terminal emission, produced by
whichever checkpoint decides `closeout`, not a fixed node in the graph.

## Required agents

- **architect** — dispatches `intake`, and (once FR-15 lands) `kind: expand` units.
- **developer** — dispatches `implement`/`fix`/`document`/`stabilize` wave units.
- **git-operator** — dispatches `git-branch-off` and the tail's `final-push`.
- **manager** — dispatches every `ck-JJ` checkpoint and the tail's `closeout` (and, once FR-15
  lands, sub-aggregator units).
- **reviewer** — dispatches `review` wave units.
- **tester** — dispatches `test`/`verify` wave units and the tail's `final-verify`.

## Params

All 17 are declared in `template.yaml`, which is the source of truth for current defaults/enums
(duplicating every value here would drift) — `ao new overseer-runner --help`-style inspection is
`ao templates show overseer-runner` (or just read `template.yaml`).

| param | what it controls |
|---|---|
| `repo_set` | required; the repo(s) every stage operates on |
| `run_budget_usd` | total run spend cap (the `run-budget-backstop` breaker's threshold) |
| `task_budget_usd` | per-task spend cap (the `task-budget-cap` breaker's threshold) |
| `converge_pct` / `stabilize_pct` / `closeout_pct` | budget-stage thresholds, percent of `run_budget_usd` |
| `wave_size` | target work-unit count per wave ("every N tasks" cadence) |
| `max_waves` | hard cap on explore/converge waves before a forced close-out |
| `wave_max_minutes` | time-approximate cadence cap, from observed median unit duration |
| `max_attempts_per_item` | rework attempts allowed on one work item before it must be deferred |
| `max_expanders_per_wave` | FR-15 opt-in sub-DAG expansion per wave; `0` (default) disables it |
| `max_injected_tasks` | structural fan-out cap (the `runaway-fanout` breaker's threshold) |
| `final_push` | whether the close-out tail includes a final git push |
| `overseer_effort` | effort level the checkpoint task itself dispatches at |
| `overseer_model` | optional per-checkpoint model override (see "The `overseer_model` rule" in the contract) |
| `default_unit_model` | optional default model for wave work units; per-kind (`kind_map[kind].model`) and per-brief `model` win (OV-R9m, see `docs-md/model-selection.md`) |
| `allowed_models` | optional comma-separated allowlist for unit models (empty = any well-formed id) |
| `python_bin` | interpreter every hook uses to invoke `tools/overseer_tool.py` |
| `branch_policy` | optional free-text guidance for `git-branch-off`'s branch decision (see "Branch policy" below) |

## Branch policy

`branch_policy` (default empty) is free text the `git-branch-off` task's `git-operator` agent
reads from `overseer-config.json` and applies alongside a **deterministic fact-gathering pass it
always runs first, regardless of policy text**: `git fetch` + `git merge-base --is-ancestor HEAD
origin/main` (the correct "is this branch's work already merged" check — comparing commit hashes
or branch names is unreliable and the instruction explicitly forbids it), a `git status
--porcelain` dirty-tree check, an upstream-tracking check, and a check for whether the current
branch is literally `main`. The agent never guesses at git state from the policy text alone.

Empty (the default) means the **auto-detect policy**: branch fresh off `main` when starting from
`main`, otherwise sync and continue on the current non-main branch — this is exactly the
template's original, unconditional behavior before this param existed. A non-empty value is
illustrative free text, not a fixed enum the tool parses mechanically — for example
`"always branch fresh off main"`, `"keep working on the current branch unless it's already
merged"`, or `"never create a branch, fail if dirty"` — the agent combines the gathered facts with
the policy's intent to decide. **Never destructive**: if honoring the policy would require
discarding uncommitted or unpushed work (a genuine policy/reality conflict), the agent writes
`git-abort.md` and stops rather than forcing it — since `git-branch-off` runs before `intake` ever
locks a charter, there is no checkpoint yet for the overseer's own hold mechanism to gate through,
so this task's existing abort-and-halt path (already resumable, already a human-in-the-loop pause
point) is reused instead of the checkpoint hold pattern. See
`instructions/01-git-branch-off.md` for the exact procedure.

## Tunable vs fixed

Params above are knobs an operator plausibly sets per run, at `ao new` time. A second set of
values — `stall_waves`, `stabilize_wave_size`, `max_stabilize_passes`, `sub_wave_size`,
`default_unit_cost_usd`, `default_ckpt_cost_usd`, and `kind_map` — are detector/heuristic
internals baked into `overseer-config.json.tmpl` as constants, not exposed as `ao new --param`
flags. They are still **editable by hand** in the rendered `overseer-config.json` before your
first `ao run` (the tool re-reads that file on every hook invocation; there is no separate
compiled state), so a workspace that wants, say, a longer `stall_waves` before flagging
stagnation can just edit the file after `ao new` and before running.

## Budget stages and graceful degradation

Walking the design doc's worked example on the $2000 default budget:

1. **Spent $1,450, next wave projected to $1,650** (≥ 80% of $2,000) → stage moves
   `explore → converge`. The checkpoint may only `continue`/`redirect` **existing** work items
   (no new scope) — see the contract's `†` footnote.
2. **Spent $1,760, projected ≥ 90%** → stage moves `converge → stabilize` (latched — stages only
   ever move forward). The checkpoint emits a stabilization wave: make the build green, cut
   half-done work, make docs reflect reality — against each ask's usable bar, never new scope.
3. **Spent $1,905, projected ≥ 95%** → stage moves `stabilize → closeout`, `must_close` is set.
   The checkpoint must emit `final-verify → closeout → final-push` regardless of what else is
   unfinished.
4. The hard `run-budget-backstop` breaker at $2,000 (100%) only fires if the stages above
   *failed* to degrade gracefully — a crash, a runaway unit, or a malformed manifest that never
   reached the checker.

## Limits & breaker rationale

| id | condition | threshold | action | mode |
|---|---|---|---|---|
| `human-input-gate` / `-2` / `-3` | `stop_file` | `control/pause[-N].flag` | pause | — |
| `operator-kill` / `-2` | `stop_file` | `control/halt[-N].flag` | stop | — |
| `run-deadline` | `run_wall_clock_seconds` | 432000 (5 days) | stop | recommend |
| `run-active-cap` | `run_active_seconds` | 432000 (5 days) | stop | recommend |
| `systemic-breakage` | `consecutive_failures` | 3 | fail | recommend |
| `runaway-fanout` | `injected_task_count` | `max_injected_tasks` (default 160) | fail | **hard** |
| `task-budget-cap` | `task_cost_usd` | `task_budget_usd` (default $75) | fail | recommend |
| `run-budget-backstop` | `run_cost_usd` | `run_budget_usd` (default $2,000) | stop | **hard** |

**Why `run-budget-backstop` is hard at 100%, not `recommend`.** A `recommend`-mode breaker's
bounded auto-extension adds the *original threshold* again (`apply_breaker_extension`): a 95%
`recommend` breaker on a $2,000 budget (threshold $1,900) would auto-extend to $3,800 — 190% of
the budget — on its first repeat trip. That contradicts a fixed budget outright. The 80/90/95%
points are agent-level self-governance stages (above); 100% is the one wall, and an operator who
wants more money decides explicitly (see "Resuming after a budget trip").

**Why `runaway-fanout` is hard.** Its bound is structural and already mechanically enforced by
the manifest checker (`OV-CFG-3` at config-load time, `OV-R4`/`OV-R5`/`OV-R10` once the M3
checker lands) — a trip here means the checker itself was bypassed or is broken, not a legitimate
near-miss to extend past.

**The latch + unit-gate interaction (FR-18).** Circuit breakers **latch**: `evaluate_breakers`
records a trip at most once per id, ever, per run (`breakers.py:603`). So after any breaker trips
— including `run-budget-backstop` at 100% — a *plain* `ao resume` no longer has any engine-side
budget wall for that id. This template closes that gap at the template level: every wave unit
carries `pre_hook: {"use": "ov-unit-gate"}`, which re-checks spend against the effective budget
and refuses at $0 (`BUDGET:`) on *every* dispatch, re-arming containment per unit rather than
relying on the once-only engine breaker.

## Human-in-the-loop (hold)

A checkpoint may decide `hold`: it emits the next checkpoint with an empty wave and writes
`control/hold-request.json`. That next checkpoint's `ckpt-prep` pre-hook then fails with a
message prefixed `HOLD:` (exit 2, $0 — no LLM was dispatched) until you write your answer to
`control/hold-answer.md` and run `ao resume`. Because this reads to the engine as an ordinary
task failure, it also increments the run's `consecutive_failures` streak — a documented, accepted
limitation of the MVP (NFR-X6 is the planned first-class `paused` run status that would avoid
this; not built yet).

## Resuming after a budget trip

A **plain** `ao resume` after the `run-budget-backstop` breaker trips does **not** by itself run
a graceful close-out. Because breakers latch (above), the engine wall is gone, but the FR-18 unit
gate immediately refuses the first pending wave unit at $0 with a `BUDGET:` failure — the run
halts again, one step closer, but still without a `closeout.md`. From there you have two real
options:

1. **Close out without spending more.** Run
   `overseer_tool.py request-closeout --workspace-root <ws> --instance-dir <inst> --reason "<text>"`,
   then `ao resume`. Every still-pending wave unit is skipped at $0 (synthetic `no_op`
   report + breadcrumb), and the next checkpoint is forced (`must_close`) to emit the close-out
   tail.
2. **Actually continue working.** Write `control/budget-override.json`
   (`{"run_budget_usd": <new total>, "reason": "..."}`) **and** run
   `ao resume --extend-breaker run-budget-backstop --extend-by-same` (or
   `--extend-by-seconds <amount>` for a specific bump) — the override in `control/` is honored
   only when the engine-recorded extension (`state.breaker_overrides["run-budget-backstop"]`) is
   already at least the override's new total. Both must be present; the override alone is a
   no-op refusal, and the flag alone doesn't change what the tool treats as the budget.

## Engine gaps designed around

- **G1** (no cadence primitive) — cadence is structural: each wave holds ≤ N units plus one
  checkpoint that recurses; time is approximated by sizing the next wave from observed unit
  durations.
- **G2** (breaker actions are flat — no "run X then halt") — graceful degradation is agent-level
  self-governance (the budget stage machine above); the one hard breaker is only the backstop.
- **G3** (Monitor ABC is too shallow to drive this) — the checkpoint is an ordinary
  author-defined `manager` task with an explicit contract, not a Monitor.
- **G4** (grading is post-run only) — mid-run evaluation happens inside the checkpoint task's own
  `pre_hook`/`post_hook` pair, the already-sanctioned pattern; nothing from the rejected
  mid-run-settlement design comes back.
- **G5** (emission could be lost on a breaker trip or crash at an emitter's settle) — fixed
  directly in the engine, not worked around at the template level; see
  `docs-md/adr/ADR-0016-overseer-runner-cadence-and-budget-governance.md` decision D7.

## Completion marker

`outputs/final/closeout.md` is what actually marks a run as done — it is written by the tail's
`closeout` task and states, per ask, what's done, what's usable, what isn't, and how to
continue. A run whose `ao status` reads `succeeded` but has no `outputs/final/closeout.md` is
anomalous and should be treated as incomplete regardless of engine status.

## Parallelism & isolation

**`max_parallel` overshoot.** Spend visibility only covers *settled* tasks — an in-flight unit's
cost isn't counted until it finishes. With `max_parallel = P`, the budget backstop can therefore
overshoot by roughly `P × (median unit cost)` before it trips, since up to `P` units can be
mid-flight and uncounted at once. Keep `max_parallel` modest relative to `run_budget_usd` if you
want the backstop's overshoot bound to stay small.

**Isolation is opt-in, off by default** — same convention as `routed-runner`: `workflow.json`'s
`defaults.isolation` renders as `"none"`, and `git-branch-off` / the tail's `final-push` are
pinned `"isolation": "none"` explicitly (both make real git operations against the shared,
synced checkout). `intake` and every `ck-JJ` checkpoint are **engine-forced** to `"none"`
regardless of any default, because they are `emit_tasks` tasks (`resolve_task_isolation`
excludes routers/emitters/loop-gates unconditionally) — checkpoints must see the real, current
workspace state to compute their digest. To isolate wave units, either set
`"isolation": "worktree"` on individual emitted unit entries (the contract's unit shape accepts
an optional `isolation` field per entry) or set `defaults.isolation: "worktree"` in your rendered
`workflow.json` before running. As with `routed-runner`, add a top-level `integration` block
(`verify_command`, `resolver_agent`, `resolvers`) if you do — see that template's README
"Parallel isolation" section for the mechanics, which apply unchanged here.

## Re-rendering the tool for a fix

`ao new` is idempotent per instance id: `ao new overseer-runner <existing-id> --param
repo_set=<...> [other params]` (verified against `templates.instantiate()`/`cli.py`'s `new`
command — there is no separate `--force` flag; re-scaffolding onto an *existing* id is the
mechanism) re-renders every non-`keep_existing` file in place, including `workflow.json`,
`overseer-config.json`, `overseer-contract.md`, and `tools/overseer_tool.py`, while `prompt.md`
and `workflows/overseer-runner/instructions/` (both `keep_existing: true`) are left untouched.
This is how an operator picks up a template bugfix (for example a corrected tool rule) on a
live run instance. You must re-supply every param you want kept (including the required
`repo_set`) — `instantiate()` does not remember a prior invocation's values. Only do this while
the run is halted between attempts, never while a task is actually in flight, and never expect it
to change already-locked facts about the run in progress (the charter lock only checks
`prompt.md`'s and `charter.json`'s hashes, not the tool's own version).

## Preflight

**Current epic status: implemented and runnable end to end.** `intake-prep`/`intake-check`,
`ckpt-prep`/`ckpt-check`, and `unit-gate` are all real and shipped (`T-ABDjSj`/`T-C6uQJW`/
`T-HPJcc6`/`T-tAKBBB`). The full intake → wave → checkpoint → (hold | budget-stage | close-out
tail) cycle is proven both by scripted e2e tests exercising the real checker/hook subprocesses
(`tests/test_e2e_builtin_overseer_runner.py`, `tests/test_e2e_overseer_runner_failures.py`) and
by a live run with real `claude_cli` agents (`T-23yMMB`; see
`output/E-YAAGhk-overseer-runner-template/smoke/summary.md`). The one exception: **`expand`-kind
units and `max_expanders_per_wave > 0` are NOT implemented** — `workflow.json.tmpl` declares an
`ov-expander-check` hook, but no `expander-check` subcommand exists in `tools/overseer_tool.py`
(FR-15 is deferred, MVP-Should, below this epic's cut line). Leave `max_expanders_per_wave` at
its default `0`; setting it higher will fail at run time, not at `ao validate` time.

**Budgets are independent limits, not a coupled constraint.** `run_budget_usd`, `task_budget_usd`,
`wave_size` and `default_unit_cost_usd` are NOT required to satisfy
`wave_size x unit cost <= run_budget_usd`: units rarely spend their full estimate, so no
combination of them is rejected up front and no wave is shrunk or skipped because the *planned*
spend would exceed the budget. The stage machine (explore -> converge -> stabilize -> closeout)
advances on **actual settled spend** vs `converge_pct`/`stabilize_pct`/`closeout_pct`, and
`run_cost_usd` (hard stop) / `task_cost_usd` stay the enforcement. `wave_size` is limited only by
`wave_max_minutes` (time cap) and, in `stabilize`, `stabilize_wave_size`; closeout plans no new
units. The digest still reports `stage_projected`/`budget_cap` as informational numbers.

`python_bin` (default `python3`) must resolve to Python ≥ 3.11 on the PATH the `ao`
service's hooks inherit — every hook (`ov-intake-prep`, `ov-ckpt-prep`, `ov-unit-gate`, …)
invokes it directly. Confirm with `python3 --version` on the machine/service account that will
actually run the workflow, before your first `ao run`. A wrong or missing interpreter fails
every hook closed (the run halts immediately, resumably) rather than silently degrading.

## Recommended `--autocompact`

Like `routed-runner`, this template ships **no** `agents.recommended.json` asset (NFR-X9) — one
would collide with `routed-runner`'s own workspace-root file (both `keep_existing: true`, first
writer wins) if a workspace uses both templates. The recommendation is inline instead:

- **`manager` → `500000`** (an explicit override of `routed-runner`'s own recommendation for the
  same role, where its aggregator use is lightweight): this template dispatches `manager` for
  every `ck-JJ` checkpoint, which reads the full ledger, digest, and prior reports each time —
  materially broader context per turn than a one-shot aggregation.
- **The rest follow `routed-runner`'s existing split**: `architect` → `500000` (broader
  design/synthesis context); `developer`, `reviewer`, `tester` → `180000` (narrower, repetitive
  dev-cycle work). `git-operator` is excluded (short git plumbing, unlikely to approach either
  threshold), matching `routed-runner`'s own rationale.

Set these via `--autocompact <value>` in each role's `extra_args` in your own `agents.json` —
see `routed-runner/README.md`'s "Recommended agent command_template hygiene" section for the
full mechanics (`extra_args` merges additively; it never replaces `command_template`).

## A note on global `--model`/`AO_MODEL`

A global `--model`/`AO_MODEL` set at `ao run`/`ao resume` time overwrites **every** dispatched
agent's own configured model — this is a known, live precedence defect (see
`docs-md/adr/ADR-0003-settings-precedence-policy.md`'s "status quo" discussion, and the project's
`project_model_override_clobbers_agents` learning): task-level and per-agent model choices are
supposed to be a fill-in, never a clobber, but today's invocation-level global override still
silently wins over both. For this template, that means a global `--model`/`AO_MODEL` at run time
can silently override this run's `overseer_model` param and any per-checkpoint `model` an
emitted `ck-*` entry sets. If you rely on `overseer_model`, do not also pass a global
`--model`/`AO_MODEL` for this workflow's `ao run`/`ao resume` invocations.
