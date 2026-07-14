# HLD (draft) — Granular task decomposition for the epic-runner (context-bounded agent sessions)

- Epic: [`E-gd8m4x-granular-task-decomposition`](../ad/tickets/E-gd8m4x-granular-task-decomposition/EPIC.md)
- Status: **Draft — design only, no implementation yet**
- Date: 2026-07-09
- Applies to: `ao-runner-finplan/workflows/epic-runner/` (workflow + instructions) with a short list of framework enablers in `agent-orchestrator`
- Related: [`guide-dynamic-task-injection.md`](guide-dynamic-task-injection.md) · [`token-budgeting-hld.md`](token-budgeting-hld.md) · [`adr/ADR-0003-settings-precedence-policy.md`](adr/ADR-0003-settings-precedence-policy.md)

## 1. Problem & goals

Today stage 7 (`task-breakdown`) fans each epic into ≤8 epic tasks, each running a fixed 5-stage pipeline (`impl1 → test1 → review → impl2 → test2`). The `impl*`/`test*` sessions run 10–50 min, accumulate huge context (tool-call transcripts + verbose code), and hit auto-compaction — exactly where quality degrades.

**Goals (priority order):**
1. **Accuracy / low hallucination**: each agent session completes with high quality and no human intervention.
2. Each independent session stays well under the context ceiling (**target: session never needs auto-compaction; proxy budget ≈ ≤100–150k tokens of accumulated context**) — *without* losing the project-wide picture and end goal.
3. Budget savings — secondary, welcome side effect.

**Working premise (user-stated, adopted):** long-session context bloat is mostly tool-call transcripts and verbose code the agent no longer needs — a *summary* of what exists (signatures + intent) serves later steps just as well as the raw history.

## 2. Approach — second-level decomposition with explicit context contracts

Split each epic task's implementation into ordered **steps** executed by fresh, independent agent sessions. Continuity between sessions is carried by two small, deliberately-written artifacts instead of accumulated transcript:

- a **context pack** (project-wide picture, stable per epic task), and
- a **step handoff** (what the previous step did, as summaries/signatures — never full code).

```
task-breakdown (stage 7, emit)                     [exists]
  └─ per epic task <tid>:
       plan-<tid>            planner agent; writes steps.md + context-pack.md;
                             EMITS step tasks (nested emission)          [new]
         ├─ step-<tid>-s01   fresh session: context-pack + step contract
         ├─ step-<tid>-s02   + s01-handoff.md → does step, writes s02-handoff.md
         ├─ …                (≤ MAX_STEPS, planner-sized)
         └─ steps-done-<tid> fixed-id aggregator (replaces impl1 output)  [new]
       test1-<tid> → review-<tid> → impl2-<tid> → test2-<tid>            [exists, unchanged]
```

Review/fix stays at epic-task level in v1 — reviewers benefit from whole-task scope; only the long dev sessions are split. If `impl2` (fix pass) also proves too long, the same pattern applies to it later.

## 3. The two context contracts (the core of the design)

### 3.1 `context-pack-<tid>.md` — "don't lose the end goal" (stable, ≤ ~2–3k tokens)
Written once by `plan-<tid>`, read by **every** step session. Sections (enforced by instruction template):
1. Epic end goal (2–3 sentences) + this task's role in it.
2. Architecture snapshot *relevant to this task*: components touched, key interfaces/types as signatures.
3. Conventions/constraints that apply (repo idioms, test framework, feature-flag/tenancy rules).
4. Definition of done for the whole task (from `tasks.md`).

This is how a 30-turn step session keeps project sight without reading `design.md` (which stays available as a listed input for on-demand lookup, but the instruction says *prefer the pack; open design.md only when the pack is insufficient*).

### 3.2 `s<NN>-handoff.md` — "summaries instead of transcripts" (per step, ≤ ~1–2k tokens)
Written by each step as a **declared output** (engine verifies existence). Sections:
1. What changed: files + new/changed **function signatures** with one-line intent each — no code bodies.
2. Decisions made & why (only ones later steps depend on).
3. State of verification: which done-check ran, result.
4. For the next step: exact starting point, known gaps/gotchas.

### 3.3 Step contract (in `steps.md`, one block per step)
Planner sizes steps so one fresh session finishes comfortably inside the `effort=medium` turn budget: one focused change (single module/component), ~≤5 files, and a **concrete done-check** the step must run (targeted compile/test subset — step agents therefore need `bypassPermissions`, per memory `acceptedits-blocks-bash-bypasspermissions-for-code-agents`). Hard cap `MAX_STEPS` per epic task (proposed: 6; runaway cap doubles as a circuit-breaker condition — see [`multi-endpoint-circuit-breaker-hld.md`](multi-endpoint-circuit-breaker-hld.md) §6 `injected_task_count`).

## 4. Manifest / wiring rules (extends breakdown-contract conventions)

- Step ids `step-<tid>-s<NN>` — globally unique via `<tid>` namespace (memory `injected-task-ids-globally-unique`).
- Linear chain: `s<NN>` depends on `s<NN-1>`; inputs = `context-pack-<tid>.md` + `steps.md` + previous handoff only. **Not** all prior handoffs — keeps input size flat; the planner must make handoffs self-sufficient.
- Fixed aggregator `steps-done-<tid>` with fixed output path (feeds `test1-<tid>` where `dev-pass-1.md` fed it before) — memory `dynamic-fanout-fixed-aggregator-contract`, emitted even for a 1-step plan.
- `plan-<tid>` must set `skip_if_outputs_exist: false` (emitter skip strands the chain — memory `skipped-emit-task-never-injects`).
- Per-run generated `breakdown-contract.md` grows a step-level section mirroring the existing literal-JSON style, since manifest bugs "fail ugly, not clean".

## 5. Framework enablers (agent-orchestrator changes — verify in LLD)

| # | Enabler | Status |
|---|---|---|
| E1 | **Nested emission**: an *injected* task with `emit_tasks: true` must itself inject on success. Engine's expansion hook (`engine.py` ~638) fires for any succeeded task, so this likely already works — but it is untested and undocumented. Needs an integration test + doc note before the design relies on it. | verify |
| E2 | **Injection caps** (`injected_task_count`, `injection_depth`) as safety rails once emitters emit emitters. | new (part of circuit-breaker epic) |
| E3 | **Per-task `model`/`effort`** so steps can run cheaper/smaller models than the planner (ADR-0003 open question 2). Workaround without it: extra agent entries (`developer-step`) in `agents.json`. | decide in ADR-0003 |
| E4 | **Token telemetry per session already exists** (`TaskResult.input_tokens`/`output_tokens`/cache fields, persisted) — metrics in §7 need no new plumbing, only a small report over run state. | exists |

## 6. Risks & mitigations

| Risk | Mitigation |
|---|---|
| Handoff quality becomes the weakest link (bad summary → next step rebuilds context by re-reading code, blowing the budget anyway) | Rigid handoff template; done-check includes "next step can start from §4 alone"; `review-<tid>` stage sees all handoffs and flags drift |
| Integration drift between steps (each compiles alone, whole doesn't) | Each step's done-check must also run the previous step's check; `test1-<tid>` remains the whole-task integration gate |
| Over-fragmentation (planner emits 12 two-minute steps; overhead dominates) | Planner sizing rules + `MAX_STEPS` cap; instruction states a *floor* too (if the task fits one session, emit exactly one step) |
| Cold prompt cache per session raises cost | Accepted — budget is the secondary goal; context pack keeps re-read volume small |
| More injected tasks → more manifest-bug surface | Literal contract file per run (existing pattern); E2 caps; ids namespaced |

## 7. Success metrics (measurable from existing run state)

- p95 per-session `input_tokens + cache_read_input_tokens` for step sessions ≪ epic-task sessions today (proxy for "never near compaction").
- Zero `error_max_turns` terminal reasons at `effort=medium` for step sessions.
- Epic-task success rate without human intervention (aggregator reached / epics started), compared before/after.
- Total run tokens per epic — expected roughly flat or better (secondary goal).

## 8. Suggested rollout

1. Land E1 verification + E2 caps in agent-orchestrator.
2. Add planner instruction (`14-plan-steps.md`), step instruction (`15-implement-step.md`), handoff/context-pack templates, and the step-level contract section to `new-epic-run.sh` generation — behind a per-run toggle (`GRANULAR=1`) so classic 5-stage runs remain available for A/B comparison.
3. Run one real epic in both modes; compare §7 metrics; then decide default.
