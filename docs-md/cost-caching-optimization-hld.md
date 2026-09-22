# HLD — Cost & Caching Optimization (Epic B)

- Epic: `E-1cecSx-cost-caching-optimization`
- Status: **Reviewed (Rev 2)** — incorporates early-gate `reviewer` + `architect` findings, see
  §8.
- Author: `dev-epic` agent, 2026-09-21 (Rev 1); revised 2026-09-21 (Rev 2, post early-gate)
- Related: `docs-md/task-lifecycle-hooks-hld.md` (Epic A, task lifecycle hooks — landed on this
  branch, see §7 "Forward-compatibility for Epic B"), `docs-md/task-isolation-hld.md` (E-Wk9Tz3,
  per-task worktrees), `docs-md/parallel-execution-hld.md` (ADR-0007, `max_parallel`),
  `docs-md/adr/ADR-0006-per-agent-config-over-run-level-flags.md` (governs B1's layering),
  `docs-md/adr/ADR-0015-prompt-cache-scope-and-post-run-grading.md` (**NEW, Rev 2** — records
  both of this epic's durable decisions), `meta/tickets/E-9h3m7k-accurate-usage-metrics` (cache
  token fields already captured), `src/agent_orchestrator/bench/graders.py`,
  `src/agent_orchestrator/hooks.py`

## 0. Scope

Four workstreams, in priority order:

1. **B1 — Claude prompt-caching audit.** Does the per-task worktree path (or any other
   per-task-unique value) leak into content that breaks Anthropic's prompt cache across
   isolated tasks? Fix if real, document precisely if not.
2. **B2 — Timing/profiling.** Run-level top-N slowest tasks (cheap, data already exists) +
   within-task activity-type breakdown (needs a feasibility check first).
3. **B3 — Outcome/accuracy metrics (MVP = local counts + a deterministic grading post-hook).**
   Cross-user telemetry is explicitly OUT of scope (future epic).
4. **B4 — Dashboard cache-stat visibility.** On-demand/expandable only, never a new default
   column — the same constraint applies to B2's timing surfacing.

---

## 1. B1 — Prompt-caching audit

### 1.1 What Anthropic's cache key actually is (confirmed, not assumed)

Source: `claude-api` skill's bundled `shared/prompt-caching.md` (Anthropic's own reference,
verified consistent with the live docs fetched in §1.3 below).

> Prompt caching is a prefix match. Any change anywhere in the prefix invalidates everything
> after it... Render order is `tools -> system -> messages`.

ao's `ClaudeCliExecutor` (`src/agent_orchestrator/executors/claude_cli.py`) shells out to the
`claude` CLI binary (`subprocess.Popen`) rather than calling the Messages API directly — **ao
cannot place its own `cache_control` breakpoints**; the CLI owns request construction entirely.
The only levers available to ao are: (a) keep whatever content ao feeds into that invocation
byte-stable across tasks/runs, (b) avoid leaking path/timestamp/id values into that content, and
(c) any CLI flag Claude Code itself exposes for cache behavior.

### 1.2 Audit of ao's own code — zero leaks found

Traced every place a per-task-unique value (worktree path, task id, run id, dispatch cycle,
timestamp) could reach content Claude Code loads as instructions:

| Path | Finding | Evidence |
|---|---|---|
| `executors/prompt.py::build_prompt` | Interpolates **paths and ids only**, never file content, into the `-p` prompt argument (the *first user message* — `messages`, the last-rendered, most-volatile-allowed segment per §1.1's render order). This is legitimate, *expected* per-task variance, not a caching defect — every task's instruction genuinely differs by design. | Module docstring: "this module interpolates paths and ids only and never opens an instruction, input, or artifact file." `models.py:618` on `WorkflowSpec.general_instructions`: "Paths only — never contents (NFR-1)." |
| CLAUDE.md / project instructions | Never generated or rewritten per-task by ao. It is a single git-tracked file; `git worktree add` checks out the identical committed blob into every worktree of the same commit — byte-identical content across all worktrees of one run. | `grep -rn "CLAUDE.md" src/` — zero writes, only comment references citing CLAUDE.md's own rules. |
| `general_instruction_paths` | Rendered as a **path list string** (`" ".join(paths)`), never read/inlined. | `prompt.py::render_general_instructions`. |
| Tool-policy argv (`--disallowedTools`) | Derived solely from `AgentSpec.disallowed_tools`/`forced_disallowed_tools` — static, workflow-authored config, not from `ctx.cwd`/task id/run id. | `claude_cli.py::_apply_tool_policy`. |
| `--model`/`--max-turns` | Derived from `AgentSpec.model`/`.max_turns`/`.effort` — static per-agent config. | `claude_cli.py::execute`. |
| Subprocess `cwd`/`env` | `ctx.cwd` (the worktree root) and `ctx.env` (`AO_ISOLATION`/`AO_TASK_BRANCH`/...) ARE per-task-unique, but these are `subprocess.Popen` launch parameters — not bytes inside the Messages API request `tools`/`system`/`messages` fields ao's own code constructs. | `claude_cli.py:517-528`. |
| `isolation/*` (worktree creation, integrator, escalation) | No file-content generation with interpolated worktree/task/run text; `escalation.py:304`'s `write_text` is a verbatim copy of an existing static instruction file, not an interpolated template. | `grep -n "write_text" src/agent_orchestrator/isolation/*.py`. |

**Conclusion of the code audit: ao's own fed-in content is byte-stable across tasks.** No leak
exists in anything ao authors, writes, or interpolates.

### 1.3 The real, structural finding — in Claude Code's own default preset, not ao's code

Fetched directly from Anthropic's official Claude Code docs (`code.claude.com/docs/en/prompt-caching`,
"Cache scope" section, 2026-09-21):

> In Claude Code, the cache is effectively scoped to one machine and directory. Each conversation
> carries the working directory, platform, shell, and OS version, and the system prompt names your
> auto memory paths, so two sessions in different directories build different prefixes and miss
> each other's cache. **That includes worktrees of the same repository, since each worktree has
> its own working directory.**

And from `code.claude.com/docs/en/agent-sdk/modifying-system-prompts` ("Improve prompt caching
across users and machines"):

> By default, two sessions that use the same `claude_code` preset and `append` text still cannot
> share a prompt cache entry if they run from different working directories. This is because the
> preset embeds per-session context in the system prompt ahead of your `append` text: the working
> directory, whether it's a git repository, the platform, the active shell, the OS version, and
> auto memory paths. Any difference in that context produces a different system prompt and a
> cache miss. CLAUDE.md content doesn't affect the system prompt cache because the SDK injects it
> into the conversation, not the system prompt.

**Finding: a real cache-prefix divergence exists, but its root cause is entirely internal to the
`claude` CLI's own default system-prompt construction — not a bug ao introduces.** Every isolated
task dispatched under Epic A's sibling task-isolation feature (`E-Wk9Tz3`,
`.claude/worktrees/<task-id>/...` in this repo's own dogfood usage, or the equivalent per-run
worktree root in `isolation/paths.py::worktree_root`) runs `claude -p` from a **distinct absolute
directory**. Per Claude Code's own documented behavior, this alone — independent of anything ao
does with CLAUDE.md/general instructions/prompt content, all confirmed static in §1.2 — makes
every isolated task's system prompt byte-different from every other isolated task's, which is a
full `tools`+`system` cache miss on every isolated dispatch. This precisely matches (and confirms,
with an authoritative primary source) the leak the epic asked this audit to either find-and-fix or
rule out with evidence — it is real, just not where a first guess ("ao must be interpolating the
path somewhere") would look.

`ao`'s captured historical transcripts (`playground/.tmp/bench/2026-07-22-*/.../transcript.jsonl`,
real dispatched-and-captured runs) corroborate the *mechanism*: every `system`/`init` stream-json
event Claude Code emits carries a `cwd` field verbatim (e.g.
`"/usr/avadhoot/mounted/agent-orchestrator/playground/.tmp/bench/.../repo"`), confirming Claude
Code's harness is aware of, and — per the official docs above — embeds, the literal working
directory as part of its own request construction. No historical run in this repo happened to
exercise two worktree-isolated tasks sharing one CLAUDE.md within the same bench session (every
captured ao-epic-* bench run in this repo ran with isolation off, so cwd was identical across a
run's own tasks) — so there is no on-disk `cache_read_input_tokens` A/B pair to cite for *this
specific* repo's history. The finding above rests on Anthropic's own primary documentation
(quoted verbatim), which is authoritative for how the `claude` binary itself behaves and is not
something a local transcript sample could contradict or need to re-derive.

### 1.4 The fix — a documented, official CLI lever, not custom cache-key engineering

Claude Code ships exactly the flag for this case, non-interactive/headless mode only:

> `--exclude-dynamic-system-prompt-sections` — Move per-machine sections from the system prompt
> (working directory, environment info, memory paths, git-repo flag) into the first user message.
> Improves prompt-cache reuse across different users and machines running the same task. Only
> applies with the default system prompt; ignored when `--system-prompt` or `--system-prompt-file`
> is set. Use with `-p` for scripted, multi-user workloads.

(`code.claude.com/docs/en/cli-reference`; SDK equivalent `excludeDynamicSections` /
`exclude_dynamic_sections` documented at `.../agent-sdk/modifying-system-prompts`.) This is
**exactly** the shape of fix the epic anticipated ("don't interpolate the worktree path into
CLAUDE.md/system-prompt text, only into args/env that aren't part of the cached prefix") — except
the content restructuring already exists inside Claude Code itself; ao's job is to opt into it via
the one flag hook Anthropic provides for this precise purpose.

**Tradeoff (documented by Anthropic, carried forward here verbatim):** with the flag on, the
working directory/git-repo flag/platform/shell/OS version/auto-memory-paths still reach Claude,
just as part of the first user message instead of the system prompt — "Instructions in the user
message carry marginally less weight... Claude may rely on them less strongly when reasoning about
the current directory." **This is low-risk for ao's own usage pattern specifically**: ao's
`build_prompt` already gives the agent every input/output/repo path as **explicit, absolute text**
in the user prompt (NFR-1 invariant) — ao's tasks never rely on Claude inferring the cwd from
system-prompt salience; every path the agent needs is already spelled out. There is no ao-specific
correctness dependency on the system prompt's cwd authority.

**Version-compatibility risk (why this is opt-in, not a silent default injection):** ao does not
pin or probe the installed `claude` CLI's version anywhere (`install.sh` installs it as an
external dependency). If an older CLI does not recognize this flag, unconditionally injecting it
into every dispatch would break every task on that installation with an "unknown option" error —
an unacceptable regression for a caching *optimization*. Per CLAUDE.md's "Safe by default"
principle and "smallest correct change," this ships as a new, explicit, **opt-in**
`AgentSpec.exclude_dynamic_system_prompt_sections: bool = False` field (default `False` — every
existing config's serialized shape and dispatch argv is byte-identical, matching this codebase's
established NFR-2-style convention for additive fields). `claude_cli.py` injects the flag only
when set, using the same "explicit caller flag wins" convention already used for
`--output-format`/`--disallowedTools` (skipped if the agent's own `command_template`/`extra_args`
already sets `--system-prompt`, `--system-prompt-file`, or the flag itself).

**Recommendation recorded for operators** (not auto-applied by the engine — no coupling from
`isolation/`/`engine.py` into this AgentSpec field, keeping the change additive-only per the
epic's narrow change-scope policy): set `exclude_dynamic_system_prompt_sections: true` on every
agent used in a workflow that runs with task isolation (`isolation: worktree`) — that is precisely
the "scripted, multi-user [multi-worktree] workloads" case Anthropic's own docs name as the
intended use case for this flag.

**Layering decision recorded in ADR-0006** (Rev 2, architect finding): this is a per-agent field,
not a run-level CLI flag, precisely because ADR-0006 already settled this question — cache-scope
behavior doesn't vary per-task the way `model`/`effort` legitimately do, so it belongs where every
other argv-shaping per-agent knob already lives. A bare `extra_args:
["--exclude-dynamic-system-prompt-sections"]` would have worked with zero new code; a dedicated
field is chosen instead because it is schema-validated/discoverable in `agents.schema.json` and
because the injector's own "skip if a custom `--system-prompt` is already set" guard only applies
to the dedicated code path, not a raw `extra_args` string. See ADR-0015 decision 1 for the full
option comparison (path normalization / taking over the system prompt / this flag) and precedent
survey (ccache `hash_dir`, Bazel path mapping, Gradle path-sensitivity).

**Known limitation (Rev 2, reviewer finding C1) — this fix addresses ONE of TWO documented
cache-scope determinants, not both.** Claude Code's own docs ("Cache scope," quoted in full
above) name two SEPARATE mechanisms in the same paragraph: (a) "the working directory,
platform, shell, and OS version" plus "auto memory paths" — this is what
`--exclude-dynamic-system-prompt-sections` documents itself as moving ("working directory,
environment info, memory paths, git-repo flag"); and (b), a DIFFERENT sentence, specific to
resuming/continuing a session: "Sequential sessions share the prefix only when the git status
snapshot taken at startup matches, since each conversation also carries the **branch and recent
commits** from that snapshot." Branch and recent-commits are **absent** from the flag's own
documented coverage list.

ao's per-task worktree isolation gives every isolated task its own git branch
(`isolation/paths.py::task_branch(run_id, task_id)`, confirmed via `grep` — used at
`isolation/worktrees.py:313` and `engine.py`'s env-building) — by design, since that is what
makes per-task isolation isolated. This means: **even with the flag on, two sibling
worktree-isolated tasks in the same run almost certainly still diverge on the "git status
snapshot" component** (different branch name at minimum, plausibly a different recent-commit
view too), which per Claude Code's own documentation is tracked SEPARATELY from the
working-directory/environment-info sections the flag addresses.

**Honest accounting: the fix reduces, but is not proven to eliminate, the cache miss for
worktree-isolated tasks.** It unconditionally helps every OTHER case this epic's audit
identified (a run-level `--model`/config sweep across non-isolated tasks from different
machines/CI runners, the "multi-user, multi-machine" case Anthropic's own docs name as the
flag's intended use) and it is still the correct, minimal, evidence-backed lever available
(ao cannot open or patch the `claude` binary to unify the branch/commit-snapshot component of
its cache key). What it does NOT do is fully restore cross-task cache sharing within one
isolated run, and this doc will not claim otherwise. **Verification boundary, disclosed
explicitly**: confirming the residual branch-driven miss (or its absence) empirically requires
a real, paid `claude` CLI A/B dispatch (flag off vs. on, two isolated tasks, same base commit,
different branches) — this session ran ONE unrelated connectivity check that incurred a real
$0.19 charge (recorded in `T-lue4Rz`'s STATUS.md) and deliberately did not run further paid
experiments purely for this doc's validation, to avoid unauthorized additional spend on the
user's account. This is recorded as a genuine, open verification gap for an operator to close
with a real run when they adopt the flag — not asserted as either "works" or "doesn't work"
beyond what the citations above support.

**Stretch item reframed (Rev 2, architect finding, required)**: §5's Stretch item ("CLI flag
auto-detection of `claude --version` to safely default `exclude_dynamic_system_prompt_sections`
on for isolated workflows") as originally worded would be a **silent global clobber** of
per-agent intent — exactly the live `--model` defect ADR-0003/ADR-0006 exist to prevent (see
`meta/learnings.md`'s `project_model_override_clobbers_agents` entry). Corrected: if ever built,
auto-detection may ONLY be a **fill-in default** — applied where an agent declares no explicit
value for this field, never overwriting an explicit `false`. Recorded here so this is right
before anyone builds it, not discovered after.

**Deferred, disclosed (Rev 2, architect finding, recommended — not implemented this epic)**: an
advisory (non-fatal) warning when `spec.validate_isolation`/`_cross_validate_isolation` resolves
`isolation: worktree` for a task whose agent does not set this flag — turning a silent,
permanent cost leak into something visible at `ao validate` time. Not built in this epic (would
require plumbing the `agents` registry into a warning path that currently only
`_cross_validate_isolation` — not `validate_isolation` itself — has access to; a real, scoped
change, not a one-line addition). Recorded as a concrete, well-specified follow-up rather than
silently dropped — see the epic ticket's Non-MVP list.

**Version-incompatibility diagnosability (Rev 2, architect finding, implemented)**: an operator
who opts in on a `claude` CLI too old to recognize the flag would otherwise see every dispatch
of that agent fail with an unlabeled CLI parse error, indistinguishable from any other failure
and liable to be laundered through `RetryPolicy`/self-heal, burning budget on a failure no retry
can fix. `claude_cli.py` gains a sibling to its existing `_CLAUDE_QUOTA_PATTERN` "single source
of truth" convention: `_CLAUDE_UNKNOWN_OPTION_PATTERN`, matched against combined stdout/stderr
when `exclude_dynamic_system_prompt_sections` was set, folded into `TaskResult.error` with an
explicit, attributable message pointing at the flag and this doc.

### 1.5 `max_parallel` vs. caching — accepted trade-off, not a defect

From the same Anthropic reference (`shared/prompt-caching.md`, "Concurrent-request timing"):

> A cache entry becomes readable only after the first response **begins streaming**. N parallel
> requests with identical prefixes all pay full price — none can read what the others are still
> writing.

`max_parallel` (ADR-0007, `E-IasNXu`) dispatches N tasks concurrently on worker threads. When
those N tasks share a byte-identical prefix (the common case once §1.4's fix is applied — same
CLAUDE.md, same tools, same static content), the first response to *begin streaming* is the only
one that can write the cache entry; every other concurrently-in-flight request has already sent
its own request before that write lands, so none of them read it. This is **structural, not a bug,
and not something to build around**: serializing request submission to "warm" the cache before
firing the rest would directly fight `max_parallel`'s entire purpose (wall-clock parallelism) for
a caching win that only pays off on repeated, sequential invocations of the same prefix. **Recorded
as an accepted, documented trade-off.** Operators who want both parallelism and cache reuse on a
recurring workflow (e.g. a nightly job with a stable prefix) get it "for free" on their *second and
later* runs (a fresh cache entry from run N's first-streaming request is readable within the TTL
window by run N+1's tasks) — not within one wave's own parallel fan-out.

---

## 2. B2 — Timing/profiling

### 2.1 Run-level top-N slowest tasks — cheap, ship it

Exact field names (confirmed against current `models.py`, **not** `wall_time`/`actual_time` as
originally assumed — those fields don't exist): `TaskRunState.started_at`/`ended_at` (ISO 8601
strings, set together in `engine.py::_settle_completed_task`). Per-task wall time =
`ended_at - started_at`, the exact same derivation `models.py::compute_run_active_seconds`
already performs (summed, run-wide) for the `run_active_seconds` breaker. A new
`reporting.py::top_n_slowest_tasks(state, n)` reuses the identical per-task duration
computation, unsummed, sorted descending, capped at N — a pure function over already-persisted
`RunState`, zero new capture needed. Surfaced via a new on-demand CLI report command (§2.3) and
the dashboard's expandable run-detail view (§4) — never a new default table column, per the
locked-in B4 constraint that also applies here.

### 2.2 Within-task breakdown by activity type — feasibility CONFIRMED, build it

Investigated (not assumed) whether `claude_cli.py`'s transcript capture has per-tool-call
timestamps to bucket by activity type, by reading the actual capture code AND a sample of real
captured transcripts on disk (`playground/.tmp/bench/2026-07-22-*/.../transcript.jsonl` — real
historical dispatches, not synthetic fixtures):

- Every `assistant`/`user` stream-json event carries a top-level `timestamp` field, ISO 8601 with
  millisecond precision (e.g. `"2026-07-22T11:05:48.049Z"`), confirmed by direct inspection of a
  real captured transcript.
- An `assistant` event's `message.content` carries `tool_use` blocks with a `name` field (`Read`,
  `Edit`, `Bash`, ...) — the activity-type signal.
- Data is real and present: **feasibility confirmed, this is buildable**, not a gap to defer.

**Design:** `reporting.py::task_activity_breakdown(transcript_path)` parses `transcript.jsonl`
(reusing `claude_cli.py::parse_transcript_events`, not re-implementing JSONL parsing a second
time — CLAUDE.md DRY rule), and for each `assistant` event carrying `tool_use` blocks, buckets the
elapsed wall time between that event's `timestamp` and the next event's `timestamp` into an
activity category derived from the tool name(s) invoked:

| Category | Tool names |
|---|---|
| `file-edit` | `Edit`, `Write`, `MultiEdit`, `NotebookEdit` |
| `build-or-test` | `Bash` (heuristic: command text matches a test/build keyword list — `pytest`, `npm run build`, `make`, `go build`, `go test`, `cargo build`, `cargo test`, `mvn`, `ruff`, `mypy`) |
| `shell-other` | `Bash` (no build/test keyword match) |
| `search-or-read` | `Read`, `Grep`, `Glob` |
| `other-tool` | anything else |
| `thinking-or-text` | time between events with no `tool_use` block at all (model reasoning/response generation, not attributable to a specific tool) |

This is an **approximation** (turn-level granularity — a single assistant turn can carry >1
parallel `tool_use` block, in which case the elapsed time is attributed to the whole set, not
split further; and the elapsed-time-to-next-event heuristic includes normal model
"thinking"/generation latency inside a tool-attributed bucket when a tool call and its result
are adjacent events) — documented as such in the function's docstring and in this doc, not
oversold as exact per-call profiling. It is precise enough to answer "is this task's time mostly
in edits, mostly in test runs, or mostly in search/exploration" — the operator-facing question B2
exists to answer.

### 2.3 Surfacing

New on-demand CLI report (`ao report-timing --run-id <run_id> [--top N]`, additive to `cli.py`) prints
both the run-level top-N table and, with `--task <task_id>`, the activity breakdown for one task.
Dashboard: an expandable per-task detail panel (§4) gains a "Timing" section, matching the
existing expandable-detail pattern the dashboard already uses for other per-task data — **not** a
new default column on the main task table (same constraint as B4).

---

## 3. B3 — Outcome/accuracy metrics (MVP: local counts + grading post-hook)

### 3.1 Local-only retry/review-loop/breakdown-frequency counts — MVP, cheap

All derivable from existing, already-persisted `RunState`/`TaskRunState` fields (confirmed
against current `models.py`):

| Metric | Source |
|---|---|
| Retry count per task | `TaskRunState.attempts` (last dispatch's in-call retry count) + `TaskIntegrationState.resolver_attempts`/`.reruns` (T2/T3 conflict-ladder escalation counts) |
| Redispatch/requeue count per task | `TaskRunState.dispatch_cycle` (monotonic across self-heal/T2/T3/quota requeues, R-21) |
| Self-heal ("review-loop") count per task | `len([d for d in state.monitor_decisions if d.consult_point == "task_failure" and d.subject_id == task_id])` — mirrors the existing pattern `models.py::count_monitor_heal_retries` already uses |
| Breaker-trip / breakdown frequency (run-wide) | `RunState.tripped_breakers` (already persisted, FR-CB4) |

New `outcomes.py::task_outcome_summary(state) -> list[TaskOutcomeSummary]` (pure function, one row
per task) aggregates these — same "derive, don't duplicate persisted bookkeeping" convention as
`compute_run_usage_totals`/`compute_run_active_seconds`. Surfaced via `ao report-outcomes
--run-id <run_id>` and the dashboard (§4).

### 3.2 Deterministic grading post-hook — MVP, direct implementation of Epic A's contract

Generalizes `bench/graders.py`'s `Grader`/`CommandGrader` shape into a standalone script per HLD
§7's 5-step recipe — **no engine change needed for this half** (Epic A's dispatch-scoped
`post_hook` mechanism already fires it correctly for every freshly-(re)dispatched task):

New `specs/examples/hooks/grade_command.py` (Rev 2: co-located with Epic A's own example hooks —
`specs/examples/hooks/check_disk_space.py`/`grade.py` — not under `scripts/helper/`, which is
this repo's convention for throwaway/epic-scoped tooling, not a spec-referenced production hook
script; an earlier draft of this doc used `scripts/helper/hooks/grade_task.py`, corrected):
1. Reads `AO_HOOK_CONTEXT_PATH` (task id, output paths, agent status/exit_code — everything
   `hooks.py::run_hook`'s `context.json` already writes).
2. Adapts `bench.graders.CommandGrader`/`PytestGrader` (imported directly, reused not
   reimplemented) against a caller-supplied verify command, configured via env vars
   (`AO_GRADE_COMMAND`/`AO_GRADE_MODE`/...) so one script instance works for every task that
   wires it — deterministic, scripted, **not** LLM-based, respecting HLD §7's explicit
   non-LLM/non-billable boundary for this mechanism.
3. Writes `{"score": <0.0-1.0>, "detail": {"solved": bool, ...}}` to `AO_HOOK_RESULT_PATH`.
4. Exits `0` if solved, `1` otherwise.
5. Wired via `"post_hook": {"use": "grade", "on_failure": "ignore"}` (observe-only starting
   pattern, per HLD §7) on whichever task(s) a workflow author wants graded at DISPATCH time, or
   via `ao report-outcomes --grade grade` for POST-RUN grading (§3.3) — the same script serves
   both call shapes unchanged.

### 3.3 Post-settlement/outcome grading — REVISED, Rev 2: a post-run pass, not a new in-engine
hook trigger point (see ADR-0015 decision 2)

**Rev 1 of this section proposed an in-engine `TaskSpec.settlement_hook` mechanism, fired from
two new `engine.py` call sites.** The correction to Epic A HLD §7 that motivated it — that a hook
fired only from inside `_settle_completed_task` would NOT cover the `skip_if_outputs_exist`/
resumed-already-succeeded path — is **confirmed correct** (both early-gate passes independently
verified this against the current `engine.py`: `_prepare_and_maybe_dispatch`'s should_skip branch,
line 1007, returns `DispatchPrep(signal="skipped")` before any worker dispatch, and
`_settle_completed_task` is never reached for that task on that cycle). **What Rev 1 got wrong was
the FIX, not the diagnosis.** Early-gate architect review found the in-engine design carries a
chain of real correctness and performance defects, not style preferences:

1. **Wrong call site, not just risky.** The proposed insertion point (`engine.py:1962`, right
   before `ctx.done.add(tid)`) is not `ts.status`'s FINAL value — five later paths
   (`emit_tasks`/loop-gate manifest-read and injection failures, `engine.py:2067/2091/2103/
   2133/2148`) can still downgrade `succeeded → failed` after that point. A settlement grade
   fired there could record "succeeded" for a task the run itself then ends as "failed" — a
   correctness defect in the one thing this mechanism exists to get right.
2. **Isolated-task staleness.** By the time either proposed call site would fire, an isolated
   task's worktree is already released (`IntegrationSpec.keep_worktrees` default `"on_failure"`)
   and the shared checkout is NOT yet synced (`IntegrationSpec.sync_checkout` default
   `"on_demand"`, only guaranteed synced at run end — `engine.py`'s own comment at the R-2 gate
   states a declared isolated output "is NEVER visible to `self._store` until a barrier/run-end
   sync happens"). A deterministic grader reading the working tree at settle time would silently
   grade the WRONG tree for exactly the isolated-worktree workflows this epic's B1 workstream is
   already about.
3. **Main-thread blocking.** Both proposed call sites run on `_settle_completed_task`'s main
   thread (ADR-0007 D3: "main thread, sole writer"), unlike Epic A's `pre_hook`/`post_hook` which
   run on worker threads. A grading subprocess (default up to 120s) would serialize against wave
   refill under `max_parallel`, and the skip-path site would spawn one blocking subprocess per
   skip-eligible task on every `ao resume` invocation before any real dispatch could proceed.
4. **Unguarded registry lookup on a new main-thread site.** Epic A's own `pre_hook`/`post_hook`
   call sites already use a bare `workflow.hooks[name]` subscript, unresolved by `cross_validate`
   for `emit_tasks`-injected tasks — a pre-existing gap this epic would have widened by adding a
   third unguarded lookup inside the dispatch loop itself.
5. **Per-task wiring cannot reach `emit_tasks`-injected tasks at all** under Epic A's AC-15 trust
   model (an injected task may reference an already-declared hook name, never invent one — but
   nothing human-authored wires *which* injected tasks get a `settlement_hook` in the first
   place), so "grade every task" would remain untrue for exactly the task-origin most likely to
   need it.

**Decision (ADR-0015 decision 2): settlement/outcome grading is a POST-RUN reporting pass, not a
new engine hook trigger point.** New CLI: `ao report-outcomes --run-id <run_id> --grade <hook-name>`.

```text
ao report-outcomes --run-id <run_id> --grade grade   # resolves "grade" against WorkflowSpec.hooks
```

- Resolves `<hook-name>` against the SAME `WorkflowSpec.hooks` registry Epic A already built —
  **zero new spec surface**: no `TaskSpec.settlement_hook` field, no new `HookRef`/
  `on_failure`-misuse risk, no `$defs/settlementHookRef` schema entry, no `spec.py::cross_validate`
  change. A misresolved `<hook-name>` is a normal CLI argument error (`.get()`-guarded, reported
  to the operator), never an in-run crash.
- Iterates **every** task in the already-persisted `RunState.tasks` — dispatched AND skipped
  alike, uniformly, by construction. `emit_tasks`-injected tasks (persisted in
  `RunState.injected_tasks`) are covered automatically too, closing the exact gap per-task wiring
  could not (finding 5 above) — with NO per-task authoring burden.
- For each task, builds a context dict shaped like `engine.py`'s existing `_hook_context_fields`
  (task id, `TaskSpec.instruction`/`.inputs`/`.outputs` resolved via `self._store`,
  `TaskRunState.status`/`.output_artifact_path`, plus a `settle_reason: "dispatched" |
  "skipped"` discriminator so the grading script can tell the two apart — `post_hook`'s
  `context.json` never needed this discriminator since it only ever fires on a genuine
  dispatch), and calls `hooks.run_hook` (Epic A's existing, UNCHANGED execution mechanism —
  bounded subprocess, `AO_HOOK_CONTEXT_PATH`/`AO_HOOK_RESULT_PATH`, never raises) with
  `kind="settlement_hook"`.
- **`hooks.py`/`models.py` gain the one small, additive change this needed all along**:
  `HookOutcome.kind` and `run_hook`'s `kind` parameter widen from the closed
  `Literal["pre_hook", "post_hook"]` to `Literal["pre_hook", "post_hook", "settlement_hook"]`
  (non-breaking: existing callers keep passing the same two literals; JSON/pydantic Literal
  widening is backward-compatible for any already-serialized `HookOutcome`). This is the ONLY
  change to `hooks.py`; `run_hook`'s own logic treats `kind` as an opaque string used solely for
  the returned `HookOutcome.kind` field, so nothing else in that module changes.
- Runs entirely from the CLI's own process, AFTER `run()` returns — so `engine.py` is untouched
  by B3 (no new call sites, no helper method, no wrapper, no change to
  `_settle_completed_task`/`_prepare_and_maybe_dispatch` at all) and the shared checkout has
  already gone through run-end sync by the time grading reads it (resolves finding 2 above
  structurally, not by special-casing isolated tasks).
- Results are written to a new run-scoped artifact
  (`<run_dir>/settlement_grades.json` — one entry per task: `task_id`, `settle_reason`,
  `HookOutcome`) and printed as a table by the CLI command — **NOT written into `RunState`/
  `TaskRunState`**. This is a deliberate simplification this design earns by moving off the
  engine's own persistence: no new resume/migration surface, no "most-recent-cycle-only" caveat
  to define, no interaction with `runstate.prepare_resume` at all.
- **Strictly observational by construction, not by convention**: since grading never touches
  `RunState`, there is no `on_failure`/gating semantic to misuse, misconfigure, or silently
  ignore — the entire class of "operator sets `on_failure: fail_task` and expects a gate" footgun
  (a real concern the Rev 1 in-engine design had) cannot arise, because there is no `on_failure`
  field on this path at all.
- Re-running `ao report-outcomes --grade` for the same `<run_id>` simply re-grades and
  overwrites `settlement_grades.json` — idempotent by construction (a fresh CLI invocation, not
  an engine-internal re-fire-on-resume concern), and the operator controls exactly when it runs
  (never implicitly on every `ao resume`, resolving the Rev 1 design's "25 subprocess spawns on
  every resume" cost concern (architect finding B-4/W6) by removing the implicit trigger
  entirely).
- The same `specs/examples/hooks/grade_command.py` script (§3.2) works unchanged as this path's
  grading script — it already reads `AO_HOOK_CONTEXT_PATH`/writes `AO_HOOK_RESULT_PATH` generically,
  with no assumption about which call site invoked it.

**Change-scope boundary (Rev 2):** `hooks.py` (Literal widening only, additive, two-word diff),
`models.py` (`HookOutcome.kind` Literal widening — same change, other half), a new CLI command in
`cli.py`, a new module function (this epic's `reporting.py` or `outcomes.py` — implementer's
choice, kept small either way). **`engine.py` is untouched by B3 entirely** — a stronger
guarantee than Rev 1's "one helper + two call sites," and the one both early-gate reviews most
wanted for the epic's highest-risk item.

### 3.4 Cross-user anonymized opt-in telemetry — OUT of scope

Confirmed out of scope per the epic's own instructions (carved out as a separate future epic in a
prior planning pass). Not built, not stubbed. Noted here only as a forward pointer for whoever
picks it up next.

---

## 4. B4 — Dashboard cache-stat visibility

Dashboard backend lives in `src/agent_orchestrator/ui/*.py` (FastAPI, `ui/service.py`/`ui/app.py`/
`ui/runs.py`); the rendered frontend is a separate Vite/React source tree at `ui/` (repo root,
`ui/src/`, `ui/package.json`) that builds into the bundled static assets served from
`src/agent_orchestrator/ui/static/`. `TaskRunState.cumulative_cache_creation_input_tokens`/
`.cumulative_cache_read_input_tokens` already exist (E-9h3m7k) but nothing computes or surfaces a
hit-rate/effectiveness figure from them yet.

New pure function `reporting.py::cache_effectiveness(ts_or_totals) -> CacheEffectiveness` (hit
rate = `cache_read / (cache_read + cache_creation + input_tokens)`, guarding the zero-denominator
case) — backend-only computation, then exposed through the existing `run_detail`/task-detail REST
response shape (`ui/service.py::run_detail`, `ui/runs.py`) as an additional field on the
already-existing per-task detail payload, **not** a new top-level list/table endpoint. Frontend:
the epic's own constraint ("wire this in consistently with its existing patterns — don't invent a
parallel display mechanism") means the actual frontend implementation task starts by locating the
dashboard's existing expandable/detail-drill-down pattern in `ui/src/` (task delegated to find and
reuse it, not assume its shape) and adds a cache-stats (and, per §2.3, timing) section inside that
existing expand affordance — never a new default column on the main task table.

---

## 5. Requirements traceability (MVP / Non-MVP / Stretch)

### MVP (must-ship, each with a verification method)

| ID | Requirement | Type | Task | Verification |
|---|---|---|---|---|
| FR-B1-1 | Audit + (if found) fix the prompt-cache leak for isolated-task worktrees | Functional | T-lue4Rz | Unit test asserting `--exclude-dynamic-system-prompt-sections` is injected iff the new AgentSpec flag is set; design-doc evidence (§1), including the C1 branch/git-status honest-accounting caveat |
| NFR-B1-1 | Fix must not change argv/behavior for any existing agent config (flag default `False`) | Non-functional (compat) | T-lue4Rz | Regression test: existing `ClaudeCliExecutor` argv-construction tests still pass unchanged |
| FR-B2-1 | Run-level top-N slowest tasks derivable and reportable | Functional | T-J1b0FN-a | Unit test on a synthetic multi-task `RunState` |
| FR-B2-2 | Within-task activity-type breakdown from a real transcript | Functional | T-J1b0FN-b | Unit test against a COMMITTED fixture `transcript.jsonl` under `tests/` (Rev 2: not a `.gitignore`d `playground/.tmp/` path — architect finding B-8) |
| FR-B3-1 | Local retry/review-loop/breakdown-frequency counts derivable and reportable | Functional | T-Ar8HJF | Unit test on a synthetic `RunState` with self-heal/T2 records |
| FR-B3-2 | Deterministic grading hook script, wired via existing `post_hook` mechanism | Functional | T-Ar8HJF | Integration test: example workflow + `grade_command.py`, engine run, assert `HookOutcome.score` recorded |
| FR-B3-3 | Post-run settlement/outcome grading (`ao report-outcomes --grade`) covers both dispatched AND skipped tasks, including `emit_tasks`-injected ones | Functional | T-Ar8HJF | Integration test: one run with a dispatched task, a `skip_if_outputs_exist`-skipped task (pre-seeded outputs), and (if feasible within scope) an injected task, all graded by one `--grade` invocation |
| FR-B4-1 | Cache hit-rate/effectiveness surfaced on-demand (expandable), not a default column | Functional | T-h1KdlK-backend (+ T-h1KdlK-frontend, separately gated) | Backend unit test + REST contract test on `cache_effectiveness`; frontend change reviewed against existing pattern, gated separately per architect finding 4c |

**Rev 2 removals (no longer applicable under ADR-0015 decision 2):** NFR-B3-1 ("settlement_hook
never mutates task status") is now vacuously true by construction — grading never touches
`RunState` at all under the post-run design, so there is nothing left to test for this
requirement; removed rather than kept as a no-op row.

### Non-MVP (deferred, with a stated later-validation method)

- Cross-user anonymized opt-in telemetry (§3.4) — future epic; validate via an opt-in flag +
  aggregation service design when scoped.
- Consolidating this codebase's bounded-subprocess-with-timeout implementations (pre-existing
  debt, noted again here since `grade_command.py` reusing `bench/graders.py`'s `_run_command`
  keeps the count at five, not a new sixth instance — not this epic's job to fix regardless, per
  its own narrow-scope policy).
- **(Rev 2, deferred, disclosed — architect finding, recommended not required)**: an advisory
  `validate_isolation`/`_cross_validate_isolation` warning when an `isolation: worktree` task's
  agent does not set `exclude_dynamic_system_prompt_sections` (§1.4). Not built this epic —
  needs the `agents` registry plumbed into a warning path `validate_isolation` itself does not
  currently have access to; a real, scoped follow-up, not a one-line addition.
- **(Rev 2, deferred)**: live, in-run settlement-grading signal (the one capability ADR-0015
  decision 2 explicitly gives up by choosing a post-run pass). If a future epic needs a grade
  visible WHILE a run is in progress, it is a materially different, harder feature (would need
  to solve findings 1-4 in §3.3, not just re-adopt Rev 1's design) and should be scoped as such.

### Stretch (not required to ship)

- CLI flag auto-detection of `claude --version` to safely default
  `exclude_dynamic_system_prompt_sections` on for isolated workflows without operator opt-in —
  **Rev 2: MUST be a fill-in default if ever built (ADR-0006), never a clobber of an explicit
  `false`** (architect finding 1.3, corrected in §1.4). Not required to ship — the opt-in field
  alone satisfies the MVP acceptance criterion.
- Per-tool-call (rather than per-turn) activity attribution for B2.2, if Claude Code ever emits
  finer-grained timestamps.

---

## 6. End-to-end demonstration plan (late gate)

**Rev 2**: rewritten now that settlement grading runs post-run (ADR-0015 decision 2) — this
also resolves the Rev 1 plan's isolation-staleness defect (architect finding 2), since grading
now runs after run-end sync regardless of which tasks were isolated.

A new example workflow spec, `specs/examples/cost-caching-demo.json` (or similar), with:
- Two tasks, `isolation: worktree`, at least one agent with
  `exclude_dynamic_system_prompt_sections: true` (B1 exercised in argv construction, verifiable
  via a captured `transcript.jsonl`/argv record even under the `fake`/deterministic executor used
  for CI — a live `claude` CLI cache assertion is out of reach for CI, documented as such).
- A `post_hook: {use: grade, on_failure: ignore}` on one task (B3.2, dispatch-scoped, Epic A's
  existing mechanism, unchanged).
- A `skip_if_outputs_exist: true` task, pre-seeded with existing outputs so the run exercises the
  skip path.
- Run via `ao run`/the engine's own CLI entrypoint (outer-boundary e2e, per CLAUDE.md's testing
  rule), then `ao report-timing --run-id <run_id>`, `ao report-outcomes --run-id <run_id>` (B2/B3.1 surfacing), and
  `ao report-outcomes --run-id <run_id> --grade grade` (B3.2/B3.3 post-run grading — graded once, covering
  BOTH the dispatched task, via its `post_hook`, AND, separately, via the `--grade` pass, every
  task including the skipped one).
- Evidence collected: `HookOutcome.score` present on the dispatch-scoped `post_hook` result
  (from `TaskResult.post_hook_result`, Epic A's existing field, unchanged) AND on the post-run
  `settlement_grades.json` for BOTH the dispatched and skipped tasks; timing report showing
  per-task durations; outcomes report showing retry/dispatch-cycle counts; dashboard REST
  response (`run_detail`) carrying the new cache-effectiveness field.

---

## 7. Change-scope boundary summary (parallel-epics policy)

**Rev 2**: `engine.py` removed from this table entirely — ADR-0015 decision 2 means B3 no longer
touches it. `hooks.py` added (one small additive change).

**Module split — `reporting.py` vs. `outcomes.py`, deliberately kept separate (early-gate
architect suggestion 5a considered and NOT adopted, disclosed):** the architect suggested
collapsing both into one `reporting.py` module ("these are the same kind of thing — pure
derived aggregates over `RunState`... two top-level modules on day one is premature
partitioning"). By the time this suggestion arrived, `reporting.py` (timing + cache
effectiveness, B2/B4) and `outcomes.py` (retry/review-loop/breakdown-frequency counts, B3.1)
were already written, independently unit-tested (17 tests total), and `ruff`/`mypy`-clean.
Weighed the merge against re-verifying a working, tested split for a non-blocking stylistic
preference, and kept them separate: the two modules answer genuinely different operator
questions (*"where did the time/money go"* vs. *"how many times did this task need help"*), each
is small and focused (well under the size that would make discovery/navigation harder split than
merged), and `models.py` already sets the precedent that this codebase is comfortable with
several small "derive, don't duplicate" modules/sections rather than one growing catch-all. Both
files are imported by the same `ao report` CLI command group either way, so the split is
invisible to the operator-facing surface the suggestion was actually optimizing for. Recorded
here as a deliberate, reasoned divergence, not a silent skip — mirrors Epic A's own precedent
for recording a non-blocking suggestion it chose not to take.

| File / area | Change | Boundary |
|---|---|---|
| `src/agent_orchestrator/executors/claude_cli.py` | `_ensure_exclude_dynamic_sections` argv helper (mirrors `_ensure_disallowed_tools`) + `_CLAUDE_UNKNOWN_OPTION_PATTERN` detection (§1.4) | Additive only; no change to existing flag-injection functions |
| `src/agent_orchestrator/models.py` | `AgentSpec.exclude_dynamic_system_prompt_sections: bool = False`; `HookOutcome.kind` Literal widened to include `"settlement_hook"` | Additive only; the Literal widening is the ONLY change touching an Epic A type |
| `src/agent_orchestrator/hooks.py` | `run_hook`'s `kind` parameter Literal widened to match `HookOutcome.kind` | The ONLY change to this module; `run_hook`'s own logic is untouched |
| `src/agent_orchestrator/reporting.py` (NEW) | Timing (`top_n_slowest_tasks`, `task_activity_breakdown`) + cache-effectiveness pure functions (B2/B4) | New file |
| `src/agent_orchestrator/outcomes.py` (NEW) | Retry/review-loop/breakdown-frequency aggregate pure functions (B3.1), plus the post-run grading orchestration function `ao report-outcomes --grade` calls into (B3.2/B3.3 — grading is an outcome/accuracy concept, the natural home) | New file — kept separate from `reporting.py`, see rationale above |
| `src/agent_orchestrator/cli.py` | New `ao report-timing`/`ao report-outcomes [--grade NAME]` subcommands | Additive only; no change to existing commands |
| `src/agent_orchestrator/ui/*.py`, `ui/src/*` | Expose cache-effectiveness (+ timing) on the existing per-task detail payload/view | Must reuse the existing expandable-detail pattern; no new default column |
| `specs/examples/hooks/grade_command.py` (NEW) | Generalized deterministic grading hook script (co-located with Epic A's own example hooks, not under `scripts/helper/` — a spec-referenced script, not throwaway tooling) | New file; does not modify Epic A's own `grade.py`/`check_disk_space.py` |
| `specs/examples/` | One new example workflow | New files only |
| `docs-md/adr/ADR-0015-*.md` (NEW) | Records both of this epic's durable decisions | New file |
| `tests/` | New test files, including a COMMITTED fixture transcript for FR-B2-2 (architect finding B-8) | New files only; no weakening of existing tests |
| `docs-md/` | This document | — |

Out of scope: `engine.py` (Rev 2: zero changes, see §3.3/ADR-0015), `dag.py`, `scheduler.py`,
`breakers.py`, `monitoring.py`, `isolation/*` (B1's audit read but did not need to change
isolation code), `bench/*` (read as reference, not modified — `bench/graders.py` is imported
by, not edited by, the new standalone script), `spec.py` (Rev 2: zero changes — no new
cross-validate rule needed, since there is no new spec-surface field), `specs/workflow.schema.json`
(Rev 2: zero changes, same reason), Epic A's `pre_hook`/`post_hook` call sites themselves.

---

## 8. Early-gate review — outcome

Both `reviewer` and `architect` ran on 2026-09-21 against Rev 1 of this doc, both tracing
claims against the actual current code (not taking the doc on faith) and, for the `reviewer`,
independently re-fetching Anthropic's live docs rather than trusting Rev 1's quotes.

**`reviewer` verdict**: approve with changes for §1.4/§3.3/§7 (no section rejected outright).
**`architect` verdict**: approve with changes for §1 (B1), approve with changes for §5/§6/§7
general findings; **needs rework** for §3.3's original in-engine `settlement_hook` design
specifically (not the epic as a whole).

**What changed as a direct result, all incorporated above:**

- **Critical/blocking (reviewer C1, architect Section 1a/1.2-1.5)**: B1's claim overstated what
  the fix restores — Claude Code's cache scope has a SEPARATE branch/git-status determinant the
  flag does not address, and ao's per-task branches guarantee that determinant differs across
  isolated tasks → **fixed**: §1.4 rewritten with the honest two-mechanism accounting, ADR-0006
  cited for the layering decision, the Stretch auto-detect item reframed as a fill-in default
  (never a clobber), a version-incompatibility detection pattern added, and an advisory
  `validate_isolation` warning recorded as a scoped, disclosed follow-up (not built this epic).
- **Critical/blocking (reviewer C2, architect Section 3 B-1 through B-7, "needs rework")**: the
  original in-engine `settlement_hook` design had a wrong call site (not the final `ts.status`),
  a real isolated-task staleness bug (grading a released/unsynced worktree), main-thread
  blocking against `max_parallel`, an unguarded registry lookup risk, a silent `on_failure`
  footgun, an undefined capture-dir/context shape, and could not reach `emit_tasks`-injected
  tasks → **fixed by a full redesign, not a patch**: ADR-0015 decision 2 replaces the in-engine
  mechanism with a post-run `ao report-outcomes --grade` pass (§3.3 rewritten). This resolves
  every one of those findings structurally (wrong-call-site and staleness become impossible when
  grading runs after the run ends and after run-end sync; main-thread blocking and registry-crash
  risk disappear because there is no new engine call site at all; the `on_failure` footgun
  disappears because there is no `on_failure` field on this path; `emit_tasks`-injected tasks are
  covered automatically because the pass iterates `RunState.tasks` uniformly, no per-task wiring
  needed) rather than requiring the wrapper-function/lower-timeout/discriminator-field patchwork
  the architect's "if kept" fallback would have needed. `engine.py`'s change-scope footprint for
  this epic goes from "one helper + two call sites" to **zero**.
- **Warnings/suggestions (reviewer W1-W7)**: mostly resolved as a side effect of the ADR-0015
  redesign (W1 cancelled/drain interaction, W3 main-thread serialization, W4 save-ordering, W6
  resume-cost scaling all become non-issues once grading is a separate, operator-invoked CLI
  pass rather than an implicit engine trigger). W5 (on_failure footgun) is resolved structurally
  (no `on_failure` field exists on this path). W2 (skip-path context resolution) is resolved by
  building context from `TaskSpec`/`RunState` directly in the post-run pass, which already has
  full access to both, rather than needing to duplicate `_run_with_retries`'s runtime locals. W7
  (empirical local confirmation) — attempted partially: this session's B1 connectivity check
  incurred one real, small ($0.19) API charge, disclosed in `T-lue4Rz`'s STATUS.md; a full
  isolated-task A/B comparison was deliberately NOT run to avoid further unauthorized spend on
  the user's account purely for documentation validation — recorded as an open, honest
  verification gap for an operator to close (§1.4), not asserted either way beyond what the
  cited documentation supports.
- **Non-blocking, adopted**: fixture transcript for B2.2 committed under `tests/` rather than
  cited from a gitignored `playground/.tmp/` path (architect B-8); `top_n_slowest_tasks`'
  deterministic tiebreak (already implemented before this finding landed — confirmed compliant);
  `task_activity_breakdown`'s signature clarified to read-then-parse (`parse_transcript_events`
  takes text, not a path); ticket restructuring (`T-J1b0FN` split into 2.1/2.2, `T-Ar8HJF`
  descoped to remove all engine risk under the ADR-0015 redesign, `T-h1KdlK` AC split into
  backend/frontend gates).
- **Non-blocking, considered and NOT adopted (disclosed, with reasoning)**: architect suggestion
  5a (collapse `reporting.py`/`outcomes.py` into one module) — kept separate, see §7's inline
  rationale; the self-heal-count reuse suggestion (5b, call `count_monitor_heal_retries` instead
  of a fresh filter) — NOT adopted because that function's narrower `decision == "retry"` filter
  answers a different question than `outcomes.py`'s `self_heal_retry_count` field, and silently
  reusing it would produce a wrong number under a right-sounding name (documented inline in
  `outcomes.py`).
- **Landscape survey (architect)**: ccache/Bazel/Gradle precedent confirmed the vendor-flag
  opt-in pattern for B1 (§1.4/ADR-0015 decision 1); Airflow's `on_skipped_callback` (added
  specifically because in-process callbacks don't fire for skipped tasks — the SAME gap this
  epic's B3.3 exists to close), Dagster's hook/sensor/check three-way split, and Prefect's
  in-process-hooks-vs-Automations split all independently confirm that a settlement/outcome
  observer belongs OUTSIDE the in-process execution hook, which is exactly the direction ADR-0015
  decision 2 moved (a post-run pass is further outside than even a "settled-state sensor" would
  be, and is the right amount of "outside" for an MVP with no live-signal requirement). Argo
  Workflows' unified `hooks:` counter-precedent was weighed and found inapplicable (Argo's hooks
  all run in one controller-side execution context; this codebase's worker-thread/main-thread/
  post-run split has no equivalent in Argo's model).

No item from either review was left unaddressed; the one item genuinely marked "needs rework"
(§3.3's original design) was not patched but replaced with a structurally different design that
resolves the underlying findings by construction rather than by special-casing each one.
_To be filled in after the `reviewer`/`architect` pass._
