# EPIC: E-gd8m4x-granular-task-decomposition

## Metadata
- Epic ID: `E-gd8m4x-granular-task-decomposition`
- Title: Granular task decomposition — context-bounded step sessions for the epic-runner (cross-repo)
- Owner: architect
- Created: 2026-07-09
- Last Updated: 2026-07-09 (epic drafted from accepted design session)
- Status: Draft (Proposed / backlog)

## Summary
- Goal: Split each epic task's long `impl*` session into ordered **steps** executed by fresh, independent agent sessions, so no session approaches the context ceiling / auto-compaction (proxy budget ≈ ≤100–150k tokens) while keeping the project-wide picture. Continuity is carried by two small deliberately-written artifacts — a **context pack** (stable per epic task) and a **step handoff** (summaries/signatures, never full code) — instead of accumulated transcript. Primary goal: accuracy / low hallucination with no human intervention; budget savings are a welcome secondary effect.
- **Cross-repo**: the workflow, instructions, and scaffold live in **`ao-runner/workflows/epic-runner/`**; a short list of **framework enablers** lives here in **`agent-orchestrator`**. This epic ticket tracks both sides; runner-side candidate tasks land in `ao-runner`.
- Scope In (MVP): planner step + step sessions + the two context contracts + breakdown-contract step section + fixed aggregator + `GRANULAR=1` toggle + A/B metrics report; framework enablers verified/consumed from epics `E-rc7k2v` and `E-st5p3q`.
- Scope Out (non-MVP): splitting `impl2` (fix pass) into steps (v1 keeps review/fix at epic-task level); making granular mode the default before A/B evidence; anything beyond the toggle-gated one-epic comparison.

## Traceability (design source — decisions locked 2026-07-09)
- HLD: [`docs-md/granular-task-decomposition-hld.md`](../../../docs-md/granular-task-decomposition-hld.md) — §2 step decomposition, §3 context contracts (3.1 context-pack, 3.2 handoff, 3.3 step contract), §4 manifest/wiring, §5 framework enablers E1–E4, §6 risks, §7 metrics, §8 rollout.
- Related ADR (per-task model/effort): [`docs-md/adr/ADR-0003-settings-precedence-policy.md`](../../../docs-md/adr/ADR-0003-settings-precedence-policy.md) resolved decision 2 / §5 E3.
- Related HLD (injection caps): [`docs-md/multi-endpoint-circuit-breaker-hld.md`](../../../docs-md/multi-endpoint-circuit-breaker-hld.md) §6 (`injected_task_count`, `injection_depth`).

## Cross-repo split
| Where | What |
|---|---|
| `agent-orchestrator` (framework enablers) | E1 nested-emission verification, E2 injection caps (from `E-rc7k2v`), E3 per-task `model`/`effort` (from `E-st5p3q`). E4 token telemetry already exists. |
| `ao-runner` (workflow + instructions + scaffold) | planner instruction `14-plan-steps.md`, step instruction `15-implement-step.md`, context-pack/handoff/step-contract templates, `new-epic-run.sh` generation changes, `GRANULAR=1` toggle. |

## The two context contracts (HLD §3 — the core of the design)
- **`context-pack-<tid>.md`** (stable, ≤ ~2–3k tokens; written once by `plan-<tid>`, read by every step): epic end goal + this task's role; architecture snapshot as signatures; conventions/constraints; whole-task definition of done. [§3.1]
- **`s<NN>-handoff.md`** (per step, ≤ ~1–2k tokens; declared output, engine-verified): what changed (files + new/changed signatures + one-line intent, no bodies); decisions later steps depend on; verification state; exact next-step starting point. [§3.2]
- **Step contract** in `steps.md` (one block per step): one focused change (single module, ~≤5 files) sized to finish inside the `effort=medium` turn budget, with a concrete done-check; hard cap `MAX_STEPS` (proposed 6); a *floor* too (one-session task ⇒ exactly one step). [§3.3]

## MVP vs Non-MVP split
- **MVP (this epic)** — planner step (`plan-<tid>`) emits step tasks (nested emission) and writes `steps.md` + `context-pack.md`; step sessions (`step-<tid>-sNN`) consume context-pack + step contract + previous handoff and write their own handoff; fixed aggregator `steps-done-<tid>` feeds `test1-<tid>`; breakdown-contract grows a step-level section; `GRANULAR=1` toggle keeps the classic 5-stage path for A/B; metrics report over run state.
- **Non-MVP** — splitting `impl2`/fix into steps; defaulting granular mode on (decide after A/B); step-level review.

## Requirements (traceable to granular HLD)
### Functional
- FR-G1 Planner step: `plan-<tid>` (planner agent) writes `steps.md` + `context-pack-<tid>.md` and EMITS step tasks via nested emission; sets `skip_if_outputs_exist: false` (emitter skip strands the chain). [§2, §4]
- FR-G2 Step sessions: `step-<tid>-s<NN>` fresh sessions; inputs = context-pack + `steps.md` + previous handoff only (not all prior handoffs); each does one focused change, runs its done-check, and writes `s<NN>-handoff.md` as a declared output. [§2, §3.3, §4]
- FR-G3 Context-pack contract: rigid template with the four §3.1 sections, read by every step; `design.md` stays a listed input for on-demand lookup only. [§3.1]
- FR-G4 Handoff contract: rigid template with the four §3.2 sections; summaries/signatures only, never code bodies. [§3.2]
- FR-G5 Breakdown-contract extension: per-run generated `breakdown-contract.md` grows a step-level section mirroring the literal-JSON style (manifest bugs "fail ugly, not clean"). [§4]
- FR-G6 Fixed aggregator: `steps-done-<tid>` with a fixed output path (feeds `test1-<tid>` where `dev-pass-1.md` did), emitted even for a 1-step plan. [§4]
- FR-G7 `GRANULAR=1` toggle: `new-epic-run.sh` generation is toggle-gated; classic 5-stage runs remain available for A/B. [§8]
- FR-G8 A/B metrics: a report over existing run state computes §7 metrics (p95 per-session input+cache_read tokens, `error_max_turns` count at `effort=medium`, epic-task success-rate without human intervention, total run tokens per epic). [§7, §5 E4]
### Framework enablers (from other epics — HLD §5)
- FR-G-E1 Nested emission verified: an injected `emit_tasks: true` task itself injects on success — integration test + doc note. **Delivered by `E-rc7k2v`.** [E1]
- FR-G-E2 Injection caps (`injected_task_count`, ideally `injection_depth`) as safety rails once emitters emit emitters. **Delivered by `E-rc7k2v`** (`injected_task_count` MVP; `injection_depth` non-MVP there). [E2]
- FR-G-E3 Per-task `model`/`effort` so steps run cheaper/smaller models than the planner. **Delivered by `E-st5p3q`** (FR-S5); interim workaround: `developer-step` agent entries. [E3]

## Candidate task list (high-level — NOT tickets yet; each ≤ 3 days)
Tag: `[fw]` = agent-orchestrator, `[runner]` = ao-runner.
- `[fw]` (dep `E-rc7k2v`) Confirm nested-emission enabler (E1) landed and injection caps (E2) are available — integration checkpoint, not re-implementation. (FR-G-E1, FR-G-E2)
- `[fw]` (dep `E-st5p3q`) Confirm per-task `model`/`effort` (E3) available; else stand up `developer-step` agent entries in `agents.json` as the interim path. (FR-G-E3)
- `[runner]` Planner instruction `14-plan-steps.md`: sizing rules + `MAX_STEPS` cap (6) + step floor; emits step manifest; writes `context-pack` + `steps.md`; `skip_if_outputs_exist: false`. (FR-G1, §3.3)
- `[runner]` Step instruction `15-implement-step.md`: read context-pack + step contract + previous handoff; one focused change; run done-check (needs `bypassPermissions`); write `s<NN>-handoff.md`. (FR-G2)
- `[runner]` Context-pack + handoff + step-contract templates (rigid §3.1 / §3.2 / §3.3 sections). (FR-G3, FR-G4)
- `[runner]` Extend `new-epic-run.sh` generation: step-level `breakdown-contract.md` section; `steps-done-<tid>` fixed aggregator wiring feeding `test1-<tid>`; emitted even for a 1-step plan. (FR-G5, FR-G6)
- `[runner]` `GRANULAR=1` toggle in generation so classic 5-stage runs remain for A/B. (FR-G7)
- `[fw/runner]` A/B metrics report over run state (E4 telemetry already exists): p95 per-session tokens, `error_max_turns` count, epic success rate, total tokens per epic. (FR-G8)
- `[runner]` Pilot: run one real epic in both modes; compare §7 metrics; recommend default; docs refresh (granular HLD → shipped). (§8)

## Dependencies between epics
- **Depends on `E-rc7k2v` (run-control)** for: nested-emission verification (E1) and injection caps (E2 — `injected_task_count`; `injection_depth` desirable). These must land before granular mode is relied upon in anger.
- **Depends on `E-st5p3q` (settings precedence)** for: per-task `model`/`effort` (E3) so step sessions run cheaper models. Interim workaround (no hard block): extra `developer-step` agent entries in `agents.json`.
- Cross-repo: framework enablers here in `agent-orchestrator`; instructions/scaffold in `ao-runner`. Runner-side work can start against the interim workarounds and tighten once the enablers land.

## Risks and Dependencies
- R1: Handoff quality is the weakest link — a bad summary makes the next step re-read code and blow the budget anyway. Mitigation (HLD §6): rigid handoff template; done-check asserts "next step can start from §4 alone"; `review-<tid>` sees all handoffs and flags drift.
- R2: Integration drift between steps (each compiles alone, whole doesn't). Mitigation: each step's done-check re-runs the previous step's check; `test1-<tid>` stays the whole-task integration gate.
- R3: Over-fragmentation (many two-minute steps; overhead dominates). Mitigation: planner sizing rules + `MAX_STEPS` cap + a one-step floor.
- R4: More injected tasks → more manifest-bug surface. Mitigation: literal per-run contract file, E2 injection caps, namespaced ids (`step-<tid>-s<NN>`).
- R5: Enabler sequencing — if E1/E2/E3 slip, granular mode is gated behind workarounds (or paused). Tracked as the cross-epic dependency above.

## Links
- HLD: `docs-md/granular-task-decomposition-hld.md`
- Related: `docs-md/adr/ADR-0003-settings-precedence-policy.md` (E3), `docs-md/multi-endpoint-circuit-breaker-hld.md` (E1/E2)
- Cross-repo home for runner-side work: `ao-runner/workflows/epic-runner/`
- LLD / sprint plan / task tickets: **separate phase** (not in this epic doc)
- Large outputs (if any): `output/E-gd8m4x-granular-task-decomposition/`
