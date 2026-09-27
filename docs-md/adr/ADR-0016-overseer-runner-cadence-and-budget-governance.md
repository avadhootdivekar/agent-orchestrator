# ADR-0016 — Overseer-runner: structural cadence, agent-level budget governance, template-shipped deterministic tool, and emit-before-breakers settle ordering

- Status: **Accepted (implemented)**. Accepted as a design on 2026-09-26. Implemented in
  `E-YAAGhk-overseer-runner-template` on `ad/overseer-runner-workflow` and reconciled on 2026-09-27
  (`T-gbccdr-docs-refresh`; see "Implementation outcome and follow-ups" at the end and
  `docs-md/overseer-runner-hld.md` §26). D1-D9 shipped as decided. FR-15's nested expanders (under
  D1) did not ship.
- Deciders: `architect`, with Phase-4 consultations (manager, developer, reviewer, tester,
  dev-security, dev-critic). The record is in `docs-md/overseer-runner-hld.md` §23.3
- Related: ADR-0004 (guardrail modes `hard|recommend`), ADR-0007 (barrier scheduling for
  emitters), ADR-0015 D2 (post-run grading; the in-engine mid-run settlement hook was rejected),
  `docs-md/lld-run-control-routing-breakers.md` (ADR-RC-004: flat breaker actions)

## Context

The user wants a generic template that decomposes an open-ended `prompt.md` at run time. It must
periodically check alignment, loops, and progress, and it must degrade gracefully to a *usable*
deliverable at about 80/90/95% of a fixed budget. The engine has no cadence primitive. Its breaker
actions all mean "halt". The Monitor ABC cannot see history or inject tasks. Mid-run grading was
rejected (ADR-0015 D2). The user fixed three scoping decisions: same-run recursive `emit_tasks`
(no child runs), a full epic, and delivery as a new builtin template.

## D1 — Cadence is structural: recursive emit waves, not an engine primitive or `LoopSpec`

- **Options**: (a) a new schema `cadence:` block plus an engine timer; (b) `LoopSpec` with a gate;
  (c) each checkpoint emits ≤ N units plus the next checkpoint, which depends on all of them.
- **Decision**: (c). **Reason**: zero engine code, "every N tasks" holds by construction, and it is
  bounded by `max_waves` and `injected_task_count`. `LoopSpec` is ruled out because `spec.py` rejects
  a loop gate with `emit_tasks=True`, so its gate cannot inject variable work.
- **Consequences**: time cadence is approximate (adaptive wave sizing). Preemptive timers are
  Non-MVP (NFR-X1). No static tail can exist (D8).

## D2 — Graceful degradation is agent-level self-governance on *projected* spend, with one hard 100% backstop

- **Options**: (a) three `run_cost_usd` breakers at 80/90/95% (`recommend`); (b) a new `drain`
  breaker action; (c) a deterministic stage machine (explore→converge→stabilize→closeout) computed at
  each checkpoint, where the LLM's decisions are mechanically restricted per stage, plus one
  `run_cost_usd` breaker at 100%, `mode: "hard"`.
- **Decision**: (c). **Reason**: every breaker action halts (ADR-RC-004), so (a) can only stop, and
  it can't stabilize anything. `recommend`'s auto-extension *adds the original threshold again*, so a
  95% `recommend` breaker lets spend reach 190%, which contradicts a fixed budget. (b) is an engine
  epic whose value (c) mostly delivers.
- **Consequences**: overshoot past 100% is bounded by in-flight work (≈ `max_parallel` × unit
  cost). Going beyond the budget is an explicit operator act (`--extend-breaker` plus a
  budget-override file, and the override is honored only when both exist). Breakers **latch**
  (`breakers.py:603`), so after any trip a plain `ao resume` has no engine wall. Every wave unit
  therefore carries a `pre_hook` `ov-unit-gate`, which refuses new work at $0 once spend reaches the
  effective budget (see D9).

## D3 — The overseer is an ordinary `emit_tasks` agent task using the existing `manager` role; the Monitor ABC is not used

- **Reason**: the Monitor sees only trip/failure summaries and cannot inject. A task can read
  anything under `workspace_root` (including `status.json`) and can emit. A new `overseer` role
  would force every workspace's `agents.json` to change and add nothing the instruction plus the
  `overseer_effort` param don't give.
- **Consequences**: the overseer shares `manager`'s executor/model config. Workspaces that want a
  different model for it can change `manager`, or a future param can add a per-checkpoint `model`.
  (**Implemented:** the `overseer_model` param shipped in Rev 2. OV-R10 requires `model` on `ck-*`
  exactly when it is set.)

## D4 — Deterministic logic lives in a stdlib tool shipped *per run instance* by the template, invoked via `pre_hook`/`post_hook`

- **Options**: (a) all logic in LLM instructions; (b) `ao overseer …` core CLI subcommands;
  (c) a template-local script rendered into each instance (`tools/overseer_tool.py`).
- **Decision**: (c). **Reason**: budget math, cycle detection, and manifest validation must be
  deterministic and unit-tested, so (a) is out. Keeping the tool per instance pins it to that run's
  contract version (the same rule as `routed-runner`'s per-run `breakdown-contract.md`), and core
  `ao` gains no template-specific surface.
- **Migration triggers** (widened in Rev 2, per dev-critic). Promote the pure functions (budget
  math, detectors, rule checks) to a versioned `agent_orchestrator` module plus a CLI when any of
  these happens:
  1. A second template, or any workspace-local workflow such as finplan's epic-runner, copies
     *any* detector or the stage math.
  2. The engine gains a native cadence/drain primitive.
  3. A **security** fix to the tool must reach existing live runs. Per-instance copies don't
     auto-update. Until migration, the documented remedy is to re-render the instance's tool before
     resuming.

  To keep a later extraction mechanical, the tool keeps its pure functions in a section with no
  I/O, separate from its CLI/IO shell.
- **Extraction into the package now was declined.** The hook would then have to run under `ao`'s
  own (uv-tool) interpreter rather than any `python3`, and per-run pinning is a deliberate property.
- **API status**: the `ao.overseer.*/v1` schemas and `OV-*` rule ids are **internal to this
  template**, with no stability promise to other templates or tools. They are namespaced so they
  never collide with engine rule ids (V1–V13, R-21, …).
- **Consequences**: it relies on `python3` ≥3.11 on the hook `PATH` (`python_bin` param). The tool
  source must not contain `{{ ident }}` (rendered via `_render`).

## D5 — The loop signature comes from the planner, and outcomes are engine-enforced declared outputs

- **Decision**: the emitter writes each unit's brief (`work_item`, `kind`, `ask_ids`) *before*
  dispatch. The unit writes only its outcome breadcrumb, a **declared output** (so the engine's
  missing-output check enforces it). A tool-owned, append-only ledger is folded at the checkpoint
  barrier (single writer). A shared JSONL that agents append to directly was rejected: agent file
  tools rewrite whole files, so parallel units would race and lose lines.

## D6 — Human-in-the-loop hold is a failing `pre_hook` of the next checkpoint, not `stop_file` breakers

- **Reason**: `stop_file` breakers latch once per run (an already-tripped id never re-halts), and a
  flag touched by an emitter trips *at the emitter's settle* (see D7). A pre-hook gate costs $0, can
  re-arm indefinitely, and names its reason in the task error.
- **Consequences**: a hold shows as a `failed` checkpoint. It counts toward `consecutive_failures`
  (threshold 3, `recommend`) and would count as a failure in outcome/ao-bench grading. Mitigation:
  the error text carries a fixed `HOLD:` prefix that downstream consumers can match. **Planned
  replacement**: an engine-level `paused` run status with a reason (NFR-X6). It's a follow-up epic
  candidate, to be raised if FR-17 or field use shows holds are common.
- The hold is recorded as hash-chained ledger events (`hold_requested`, `hold_answered`). Deleting
  `hold-request.json` therefore fails closed (OV-INT-2), and a forged request without a hold decision
  also fails (OV-INT-4).

## D7 — Engine: persist `emit_tasks` injection in the same save as the emitter's success, *before* circuit-breaker evaluation

- **Context (reproduced)**: `_settle` saves the emitter as `succeeded`, evaluates breakers, and
  returns `halt` before injecting. `prepare_resume` keeps succeeded-with-outputs tasks, so the manifest
  is never read again, and the resumed run ends `succeeded` with the emission silently lost (repro:
  `output/E-YAAGhk-overseer-runner-template/repro_emit_lost_on_breaker_trip.py`). A process crash
  between the two saves loses it the same way.
- **Options**: (a) at resume, re-read manifests of succeeded emitters that have no injected
  children (heuristic, fragile); (b) inject first, then save once, then evaluate breakers.
- **Decision**: (b). **Reason**: it's the smallest change, it removes the crash window, and it
  mirrors the existing precedent that the router-success hook runs before breakers. The breaker
  still halts before any injected task dispatches, so containment is unchanged. The difference is
  that the halt is now resumable *with* the emission.
- **Consequences**:
  - `injected_task_count` sees an emission at the same boundary, so a runaway is detected one
    boundary earlier. Two named tests change, and the release note says so. (**As shipped:** no
    existing test changed; see follow-ups.)
  - A manifest-read error halts that boundary without evaluating breakers.
  - `routed-runner`'s latent exposure at `task-breakdown` is fixed too.
  - This change ships as its own PR ahead of the template. (**As shipped:** it is commit `2387503`
    on the epic branch, by user decision; see follow-ups.)
  - **Latch semantics are unchanged.** A plain resume after a trip now dispatches the persisted
    injected tasks, where before it dispatched nothing (because the emission was lost). Re-arming a
    tripped hard breaker on a plain resume would change resume semantics for every workflow.
    **Follow-up candidate**, not in this epic: `ao resume` refuses when a tripped `mode: hard`
    breaker hasn't been extended. This template doesn't depend on it (D9).

## D8 — No static tail; the close-out chain is part of the terminal emission

- **Reason**: a static final task could only `depends_on` `intake`. It would become ready
  immediately and fail with `missing_inputs`, because its input's producer is injected K levels
  later. **Consequence**: the checker requires every checkpoint emission to contain exactly one of
  {next checkpoint, tail}. The run's completion marker is `outputs/final/closeout.md`.

## D9 — Governance integrity is tamper-evident; budget containment is re-armed per unit (Rev 2)

- **Context**: Phase-4 security and critic reviews established three facts.
  1. There is no trust boundary between tasks. With `isolation: none`, every agent can write the
     whole workspace, including `control/`, `outputs/ledger.jsonl`, and even `state.json`.
  2. Breakers latch once per id per run.
  3. The unit `instruction` path was not pinned, which opened a prompt-injection redirection route.
- **Decision**:
  1. **Pin**: OV-R6 requires `agent` and `instruction` to match exactly the `kind_map` in the
     rendered config.
  2. **Evidence**: the ledger is hash-chained (OV-INT-3). Checkpoint decisions, hold
     requests/answers, and honored overrides are tool-written ledger events. A missing hold
     request after a hold decision (OV-INT-2), or a stray request (OV-INT-4), fails closed.
  3. **Override**: `control/budget-override.json` is honored only when
     `state.json.breaker_overrides["run-budget-backstop"]` is at least the override amount, which
     requires an operator `--extend-breaker`.
  4. **Containment**: every wave unit carries `pre_hook: ov-unit-gate` (OV-R9), a $0 re-armable
     refusal once spend reaches the effective budget or injections exceed `max_injected_tasks`.
- **Consequences**: a determined adversarial agent could still rewrite the whole chain consistently.
  That is accepted (NFR-X11). The engine-side backstop and per-task cap remain the only hard cost
  containment. The design aims to make honest-but-confused LLM behavior fail loudly and
  deliberate tampering visible, not impossible.

## Implementation outcome and follow-ups (2026-09-27, T-gbccdr)

All MVP tasks are Done. The decisions held, and the code matches D1-D9. The full list of deviations,
with file:line evidence, is in `docs-md/overseer-runner-hld.md` §26. The ones that bear on this ADR:

- **D2, confirmed by e2e**: after a `run-budget-backstop` trip, a plain `ao resume` does **not**
  reach close-out. Every pending unit re-fails `ov-unit-gate` with `BUDGET:` at $0. Close-out needs
  `overseer_tool.py request-closeout --reason …`, or continuing needs `control/budget-override.json`
  plus `ao resume --extend-breaker run-budget-backstop --extend-by-same` (or `--extend-by-seconds
  <usd>`; the CLI requires one of the two).
- **D2, field finding (T-23yMMB live smoke run)**: the stage machine's intake projection uses the
  hardcoded `default_unit_cost_usd=8`/`default_ckpt_cost_usd=5` and the configured `wave_size`.
  That creates a hard floor: intake cannot emit wave 1 unless
  `run_budget_usd > (wave_size*8 + 5 + tail*8) * 100 / closeout_pct`, which is about $81 at the
  defaults. Real toy-task unit cost was about $0.3-0.4. The decision stands. The constants are the
  problem.
- **D3, field finding**: checkpoint cost was 14.8-16.8% of run spend at `overseer_effort=high` on a
  toy run, above the ~10% NFR-8 target. Use `overseer_effort=medium` for small runs.
- **D4**: the rule ids shipped as specified, with one naming correction. A dangling `depends_on` on
  the *next checkpoint* is **OV-R10** (`_check_next_checkpoint_shape`). OV-R8 covers unit entries
  only. The operator-facing refusals print bare `HOLD:`/`BUDGET:`/`FANOUT:` prefixes.
- **D7**: the ordering shipped as decided (`engine.py::_settle_completed_task`: inject at
  L2014-2065, single save at L2067, `evaluate_breakers` at L2088). It was delivered as commit
  `2387503` on the epic branch, not as a separate PR. No existing test changed. The `task.injected`
  log line is still written only after breakers pass (L2129-2137), so a trip at the emitter boundary
  persists the injection without logging it.

**Follow-ups (not in this epic):**
1. Expose `default_unit_cost_usd`/`default_ckpt_cost_usd` as template params, or lower them to about
   $1-2, so small budgets can run (D2).
2. Re-measure the overseer cost share on a real epic-scale run. Consider defaulting
   `overseer_effort` to `medium` (D3).
3. Integrity-lock `overseer-config.json`'s `kind_map` at intake, as `charter.lock.json` locks the
   charter (security N2, D9).
4. Add `--` (or hex-SHA validation) before the revision argument in `_git_changed_paths`'s
   `git diff` (security N3).
5. Make OV-R12 also validate the verdict's `checkpoint`/`stage` echo fields.
6. Add `--extend-by-same|--extend-by-seconds` to `unit-gate`'s `BUDGET:` message.
7. Fix the stale "not yet implemented" text in the template README "Preflight" and in
   `overseer-contract.md.tmpl` "Forthcoming".
8. FR-15 nested expanders (`T-zLHc7Q`). Until then, keep `max_expanders_per_wave: 0`, because the
   `ov-expander-check` hook points at a subcommand that does not exist yet.
9. Carried forward from D7: `ao resume` should refuse when a tripped `mode: hard` breaker has not
   been extended (engine-wide). NFR-X6: a first-class `paused` run status for holds (D6).
10. Engine-wide: `ao new` has no rollback on a partial scaffolding failure (pre-existing).
