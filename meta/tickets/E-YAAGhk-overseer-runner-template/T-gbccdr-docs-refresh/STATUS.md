# STATUS

- ID: `T-gbccdr-docs-refresh`
- Updated At: 2026-09-27
- State: Done (AC1-AC3 met with evidence below; AC4, the epic `EPIC.md`/`STATUS.md` rollup, is owned by dev-epic per the assignment and was not touched here)
- Owner: architect

## This update
- Ticket created by the architect design pass (Rev 2, after the Phase-4 consultations). Sprint: S2 (last).
- 2026-09-27: reconciled all 6 docs against the implemented code on `ad/overseer-runner-workflow` @
  `0fb60aa`. Added HLD §26 "Deviations from design" (DV-1..DV-15), set the HLD to Implemented and
  ADR-0016 to Accepted (implemented) with a follow-up list, and updated the templates HLD, the
  injection guide, the workflow-authoring skill, and the routed-runner README.

By: architect · Role: architect · Date: 2026-09-26 · Comment: Created from `docs-md/overseer-runner-hld.md` Rev 2. The ACs are pass/fail and agent-executable, and the task is sized ≤3 days. Not started.

By: architect · Role: architect · Date: 2026-09-27 · Comment: Done. I re-derived every citation
below from the code at `0fb60aa` (grep plus reading the file). None is copied from the assignment
prompt or from other tickets. I checked the budget-floor numbers by calling the shipped
`derive_budget` directly, not by hand math. I edited only the 6 in-scope doc files plus this
ticket's TASK.md/STATUS.md. No code, tests, other tickets, or epic files were touched. I found 5
deviations that were not in the dev-epic list (see "New findings" below). Two of them need
follow-up edits to shipped template text that is outside this ticket's scope (DV-15).

## Files changed (6 in scope, `git diff --stat`: 503 insertions, 38 deletions)

| File | What changed |
|---|---|
| `docs-md/overseer-runner-hld.md` | Status set to **Implemented**, with a pointer to §26. New **§26 Deviations from design** (DV-1..DV-15). Inline "as shipped" corrections: FR-15 not implemented; FR-16/§8.5/§12.3 add `--extend-by-same/--extend-by-seconds`; §8.5 states that a plain resume never reaches close-out; NFR-8 measured 14.8-16.8%; §8.2 `spent` source = state.json; M4 table (30/31 not shipped); M5 delivery (commit `2387503`, real line numbers); §7 G5 row marks its line numbers as pre-fix; §12.3 OV-R8→OV-R10; §13.1 `run_budget_usd` floor and `overseer_effort` notes; §13.3 bare `HOLD:/BUDGET:/FANOUT:` prefixes; §13.4 shipped config field list; §15 `task.injected` timing corrected; §16 delivery; §18 scenario (c) = OV-R10, (e) split into e/e1/e2, live-run result, harness `actuals_available` gotcha; §19 FR-15 not delivered; §21 post-implementation note; §23 new floor risk row, overseer-cost risk materialized, G5 blast-radius outcome, OPEN_QUESTION-1 answered; §25 marked done |
| `docs-md/adr/ADR-0016-overseer-runner-cadence-and-budget-governance.md` | Status set to **Accepted (implemented)**. D3 notes that `overseer_model` shipped. D7 consequences annotated (no test changed; commit on the epic branch, not its own PR). New section "Implementation outcome and follow-ups" (5 outcome notes, 10 follow-ups) |
| `docs-md/workflow-templates-hld.md` | Builtin path corrected to `templates/builtin/<name>/`, and the builtin list now includes `overseer-runner`. New §2.10 `overseer-runner`, with a pointer to its README, the HLD §26, ADR-0016, the budget floor, and the FR-15 caveat. `branch_policy` param noted for both builtins. Testing section lists the overseer-runner suites |
| `docs-md/guide-dynamic-task-injection.md` | New section "Settle ordering: injection is persisted before circuit breakers are evaluated (G5)", with real line numbers, what changed, the log-timing caveat, and the **resume consequence** (a latched breaker no longer re-halts, but a per-task guard such as `ov-unit-gate` re-fails pending work; a plain resume does not push it through). New paragraph on recursive emission (no static tail). The "Resume works" gotcha now covers G5. The "no built-in cap" gotcha is corrected to point at the `injected_task_count` breaker |
| `.claude/skills/workflow-authoring/SKILL.md` | "Static vs. dynamic": new "Recursive waves" subsection (no static tail after recursion, fixed emitter ids, a post-hook checker) and "When to reach for a builtin template" (`routed-runner` vs `overseer-runner` vs hand-authored, with the budget-floor and `overseer_effort=medium` cautions). "Common failure modes": #10 static tail after recursion, #11 breaker latch on resume, #12 budget below the template's projection floor. Quick decision guide: new row |
| `src/agent_orchestrator/templates/builtin/routed-runner/README.md` | A short note under the `task-breakdown` bullet: G5 closed its latent emitter-boundary exposure |

## Evidence — real file:line citations (re-derived at `0fb60aa`)

**G5 ordering (`src/agent_orchestrator/engine.py`, last touched by commit `2387503`)**
- `_settle_completed_task` def: L1380.
- G5 comment block: L1993-2008. Emit block guard `if task.emit_tasks and ts.status == "succeeded":` at L2014. `read_task_manifest` at L2016. `self._inject(...)` at L2052. `injected = True` at L2065.
- The single `self._runstate.save(state)` after injection: **L2067**.
- `evaluate_breakers(...)`: **L2088**. The halt return on a non-consultable or halted trip: L2117.
- `task.injected` log plus `SettleResult("reshaped")`: L2129-2137. This is *after* breakers, which is the basis for DV-5's log-timing correction. `_inject` def at L4105 logs nothing.
- Breaker latch: `src/agent_orchestrator/breakers.py:603` (`if spec.id in already_tripped: continue  # latch`).
- `prepare_resume` keeps succeeded tasks whose outputs are present: `src/agent_orchestrator/runstate.py:218` (docstring rule 2; logic at about L247-250).

**Hook names (`src/agent_orchestrator/templates/builtin/overseer-runner/workflow.json.tmpl`)**
- `ov-intake-prep` L14, `ov-intake-check` L25, `ov-ckpt-prep` L36, `ov-ckpt-check` L47, `ov-expander-check` L58 (its subcommand is **not implemented**, DV-7), `ov-unit-gate` L69. `intake` uses them at L109-110.
- Tool subcommands (`tools/overseer_tool.py::_build_parser` L4362-4383, `_DISPATCH` L4386-4393): `intake-prep, intake-check, ckpt-prep, ckpt-check, unit-gate, request-closeout`. There is no `expander-check`.

**Rule ids (`tools/overseer_tool.py`)**
- OV-R8: `_check_entry_r8_depends_on` L3342-3369. Unit/expander entries only (`RuleViolation("R8", …)` at L3350 and later).
- OV-R10 (next checkpoint): `_check_next_checkpoint_shape` L2490. The `depends_on` check is at L2580-2589. It is called from `_check_r10_terminal_shape` L2780.
- `OV-` prefix: `RuleViolation.formatted_rule` L2269-2270. `Violation` default message `OV-<id>` L151-159.
- Bare prefixes: `HOLD` L1402-1408; `BUDGET` L4244-4250 (message omits `--extend-by-*`, DV-12); `FANOUT` L4253-4259.
- INT-4 (stray hold request) L1380. INT-1 `verify_charter_lock` L1433. INT-3 ledger cap `LEDGER_MAX_BYTES` L55, check at L803-809. R12 `_check_r12_verdict` L2969. R13c `GOAL_SIMILARITY_REJECT` L3439.
- e2e asserts OV-R10 for scenario (c): `tests/test_e2e_builtin_overseer_runner.py` L1374 (the rationale comment is at L1150-1158).

**Signal type (`tools/overseer_tool.py`)**
- `period_repeat`: `_period_mirror_signals` L1589-1623, with `"type": "period_repeat"` at **L1598**. `detect_period` L1486. Constants `MAX_PERIOD=4`, `MIRROR_HALF_LENGTHS=(2,3)`, `MIN_REPS_PERIODIC=2`, `MIN_REPS_SINGLE=3` at L1472-1475.
- Other shipped signal types: `repeated_failure` L1634, `attempt_cap` L1654, `blocked_units` L1683, `content_oscillation` L1895, `breadcrumb_integrity` L1926, `ask_starvation` L2024, `prompt_changed` L2050, `stall` L2090.
- `wave_signature_repeat`: **0 occurrences** in `overseer_tool.py` (see the grep in the sweep below).

**Param defaults and hardcoded cost constants**
- `overseer-config.json.tmpl` L26 `"default_unit_cost_usd": 8`, L27 `"default_ckpt_cost_usd": 5`. Other constants: L22-25 (`stall_waves 2`, `stabilize_wave_size 4`, `max_stabilize_passes 2`, `sub_wave_size 4`), L28 `runs_root`.
- `template.yaml` defaults, verified to match HLD §13.1: `run_budget_usd "2000"`, `task_budget_usd "75"`, `converge/stabilize/closeout_pct "80"/"90"/"95"`, `wave_size "6"`, `max_waves "12"`, `wave_max_minutes "90"`, `max_attempts_per_item "3"`, `max_expanders_per_wave "0"`, `max_injected_tasks "160"`, `final_push "true"`, `overseer_effort "high"` (enum medium/high/xhigh), `overseer_model ""`, `python_bin "python3"`, `branch_policy ""`.
- Budget floor: `_intake_allowed_wave_size` L3763-3794 → `derive_budget` L324 → `compute_allowed_wave_size` L259-272 (`return 0  # closeout`). `time_cap_from_median_duration(None, …)` returns `DEFAULT_TIME_CAP_TASKS`, L227-236.
- Floor numbers were computed by importing the shipped tool and calling `derive_budget` at $0.01 steps. Output:
  ```
  1 False floor 30.53 explore_at 36.26 B=25: ('closeout', 0) B=12: ('closeout', 0)
  1 True floor 38.95 explore_at 46.26 B=25: ('closeout', 0) B=12: ('closeout', 0)
  3 True floor 55.79 explore_at 66.26 B=25: ('closeout', 0) B=12: ('closeout', 0)
  6 False floor 72.64 explore_at 86.26 B=25: ('closeout', 0) B=12: ('closeout', 0)
  6 True floor 81.06 explore_at 96.26 B=25: ('closeout', 0) B=12: ('closeout', 0)
  run2 B=62 ('converge', 3) run3 B=57 ('stabilize', 3)
  ```
- `read_prev_stage` falls back to `explore` with no prior digest: L1124-1131 (DV-13).
- `unit_gate` L4232-4259. `request_closeout` L4301-4354.
- CLI `--extend-breaker` requires one of `--extend-by-seconds`/`--extend-by-same`: `src/agent_orchestrator/cli.py` L1237-1257.
- Harness gotcha: `tests/overseer_runner_harness.py` L190-199 (`result.actuals_available = True`). Engine gating: `models.py:868`, `engine.py:1613`, `engine.py:3015`.

## Deviations from design (summary; full text in `docs-md/overseer-runner-hld.md` §26)

- **DV-1**: `default_unit_cost_usd=8`/`default_ckpt_cost_usd=5` are hardcoded and are not params. They put a hard floor on `run_budget_usd` of `(wave_size*8+5+tail*8)*100/closeout_pct`: $81.05 at defaults, $55.79 at `wave_size=3`/`final_push=true`. $25 and $12 are infeasible for every combination.
- **DV-2**: overseer cost was 14.8% and 16.8% of spend at `overseer_effort=high`, above NFR-8's ~10%. Recommend `medium` for small runs.
- **DV-3**: no organic live stage transition beyond `explore`. The stage machine is covered by `test_scenario_b_stage_escalation` (explore 15% → converge 35% → stabilize 45% → closeout 60%).
- **DV-4**: dangling `depends_on` on the next checkpoint is OV-R10, not OV-R8.
- **DV-5**: G5 shipped as commit `2387503` on the epic branch, not as its own PR. No existing test changed. `task.injected` is logged only after breakers pass.
- **DV-6**: confirmed. A plain resume after a backstop trip does not reach close-out. It needs `request-closeout`, or an override plus `--extend-breaker … --extend-by-*`.
- **DV-7**: FR-15 is not shipped. There is no `expander-check` subcommand and no 30/31 instructions, although the `ov-expander-check` hook and `kind_map.expand` exist. Keep `max_expanders_per_wave: 0`.
- **DV-8**: security. N1 fixed (ledger cap); N2 (`kind_map` not locked) and N3 (`git diff` argv) are follow-ups; both fall under NFR-X11.
- **DV-9**: OV-R12 does not validate the verdict's `checkpoint`/`stage`.
- **DV-10**: `ao new` has no rollback (engine-wide, pre-existing).
- **DV-11**: `HOLD:`/`BUDGET:`/`FANOUT:` are bare prefixes, not `OV-HOLD`.
- **DV-12**: `--extend-breaker` needs `--extend-by-same|--extend-by-seconds`. HLD Rev 2 and the tool's `BUDGET:` message omit it.
- **DV-13**: the intake stage is computed only to cap wave 1. It is not latched, and OV-R11 is not applied at intake.
- **DV-14**: `branch_policy` param and extra config fields, a mid-epic amendment.
- **DV-15**: stale "not yet implemented" text remains in the shipped template README and contract (outside this ticket's scope).

### New findings (not in the dev-epic assignment list)
1. **DV-5 log timing.** HLD §15 claimed that G5 emits `task.injected` "before any `breaker.trip`". It does not: the log line is at L2129-2137, after breakers. On a trip, the injection is persisted but not logged.
2. **DV-7 FR-15 wiring residue.** The `ov-expander-check` hook and `kind_map.expand` → the missing `30-expander.md` ship even though the subcommand and instructions do not exist. `max_expanders_per_wave ≥ 1` would fail at run time, not at validate time.
3. **DV-12.** The `--extend-by-*` flag is missing from the HLD text and from the tool's own `BUDGET:` refusal message. The template README already has the correct form.
4. **DV-13.** The intake-time stage is not latched, and OV-R11 is not applied at intake. This explains the live run's "self-correction". Also, smoke `summary.md` says "below ~$36 no wave" for `wave_size=1`/`final_push=false`. That figure is the `explore` threshold; the real no-wave floor is $30.53.
5. **DV-15 (needs a follow-up edit outside this ticket's scope).** `overseer-runner/README.md` L243-250 ("Current epic status: not yet runnable end to end … subcommands … don't exist … yet") and `overseer-contract.md.tmpl` L284-286 ("**Forthcoming** … not implemented yet in this epic") are stale. The contract text is rendered into every run and read by the agents. Separately, `EPIC.md` FR-6 (L46) still lists `wave_signature_repeat` among the signals. That is for dev-epic to correct, since this ticket may not edit EPIC.md.

## AC3 grep sweep (the 6 edited files; the shell `grep` is `ugrep`, so bounded regexes ran through Python `re`)

```
$ grep -n "wave_signature_repeat" <6 files>
docs-md/overseer-runner-hld.md:65   FR-6 row: "... (§8.3; `wave_signature_repeat` deferred, NFR-X10)"
docs-md/overseer-runner-hld.md:98   NFR-X10 row (deferred detector)
docs-md/overseer-runner-hld.md:343  "~~`wave_signature_repeat`~~ | Deferred to NFR-X10 (Rev 2)"
docs-md/overseer-runner-hld.md:1276 §23.3 critic row: "`wave_signature_repeat` deferred"
docs-md/overseer-runner-hld.md:1517 §26 DV-15: "... it is not shipped (NFR-X10), and the shipped signal is `period_repeat`"
-> every hit is qualified as deferred/not shipped; 0 in ADR/templates-HLD/guide/SKILL/routed README.
$ grep -c wave_signature_repeat src/agent_orchestrator/templates/builtin/overseer-runner/tools/overseer_tool.py
0

python re.search(r"plain.{0,40}resume.{0,80}(reach|reaches|reaching).{0,20}close", line, re.I):
  overseer-runner-hld.md:687  §8.5: "... **A plain `ao resume` never reaches close-out on its own** ..."   (negated)
  overseer-runner-hld.md:1415 "### DV-6 — Plain `ao resume` after a backstop trip does **not** reach close-out"   (negated)
python re.search(r"resume.{0,60}(forced|forces|force).{0,30}(close|tail)", line, re.I):
  overseer-runner-hld.md:808  §12.3: "operator: request-closeout → resume → ... → ck forced closeout → tail"   (conditioned on request-closeout)
  overseer-runner-hld.md:1118 §18 (e): "... → `request-closeout` → resume → ... forced to closeout"   (conditioned on request-closeout)
$ grep -nE "\(R8\)|OV-R8\)" <6 files>
(no matches)
```
I also read every line containing both "resume" and "close" in the 6 files (11 lines). None claims that a plain resume reaches close-out on its own. (The one exception is the §23.3 row 6 historical record, which correctly says plain resume "has no wall", not that it closes out.)

## Tests run (confirms no doc-coupled test broke; no code changed)
- `.venv/bin/pytest -q tests/test_builtin_overseer_runner_assets.py tests/test_builtin_routed_runner_assets.py tests/test_e2e_builtin_routed_runner.py` → **96 passed**.
- `.venv/bin/pytest -q -x` (full suite) → **4455 passed, 8 skipped, 0 failed** (174 s), identical to the T-vmI0jI baseline.

## Risks / Blockers
- No blockers.
- DV-15 residue: two shipped template files still say that implemented checkers are missing. This is outside this ticket's edit scope, needs a small follow-up edit, and is flagged for dev-epic.
- `EPIC.md` FR-6 lists `wave_signature_repeat`. dev-epic owns EPIC.md.

## Next actions
1. dev-epic: epic rollup (AC4). Fix `EPIC.md` FR-6's `wave_signature_repeat` mention. Decide whether to open a follow-up for DV-15 and the ADR-0016 follow-up list.
2. Follow-ups are listed in ADR-0016, "Implementation outcome and follow-ups" (1-10).

By: dev-epic · Role: manager · Date: 2026-09-27 · Comment: Independently re-verified this ticket's
work before accepting it — re-derived the key citations myself directly against the real code
rather than trusting the report: G5's `_inject`/`injected = True`/single `save()`/`evaluate_breakers`
line numbers in `engine.py` (confirmed exact and in the claimed order), the `OV-R8`/`OV-R10` real
call sites in `overseer_tool.py`, the `period_repeat` signal type location, and the hardcoded
`default_unit_cost_usd=8`/`default_ckpt_cost_usd=5` lines in `overseer-config.json.tmpl` — all
confirmed accurate. Also independently confirmed the NEW finding about `ov-expander-check`/
`expander-check`: the hook is declared in `workflow.json.tmpl` but genuinely has no matching
subcommand in `_build_parser()` (`overseer_tool.py` ~L4362-4378 lists only `intake-prep`,
`intake-check`, `ckpt-prep`, `ckpt-check`, `unit-gate`, `request-closeout` — no `expander-check`),
confirming this is real, dormant (inert while `max_expanders_per_wave=0`, its safe default), and
correctly scoped to FR-15/T-zLHc7Q's deferral. Ran the full suite myself post-verification: 4455
passed/8 skipped/0 failed, unchanged.

Closed out DV-15 myself (all 3 items, previously outside this ticket's edit scope):
- Fixed `src/agent_orchestrator/templates/builtin/overseer-runner/README.md`'s "Preflight" section
  (was "not yet runnable end to end"; now states the template is implemented/runnable, calls out
  the real `expander-check` gap, and adds the DV-1 budget-floor operator guidance).
- Fixed `overseer-contract.md.tmpl`'s "Forthcoming"/"Self-check" sections (was telling the AGENT
  itself, at render time, that `intake-check`/`ckpt-check` don't exist yet and to skip the
  self-check dry-run — this could have misled a real agent into skipping a step that now genuinely
  works; corrected to "Implemented" with the same `expander-check` exception noted).
- Fixed `EPIC.md`'s FR-6 line (dropped the never-shipped `wave_signature_repeat`, added the
  actually-shipped `breadcrumb_integrity`, and pointed to NFR-X10 for the deferred signal).
- Updated this HLD's own DV-15 entry (both the summary table row and the full write-up) to record
  that these are now fixed, not still-open follow-ups.

Re-ran the asset/e2e test suites after each fix (`test_builtin_overseer_runner_assets.py`,
`test_builtin_overseer_runner_instructions.py`, `test_e2e_builtin_overseer_runner.py`,
`test_e2e_builtin_overseer_runner_simple.py`, `test_e2e_overseer_runner_failures.py` — 134 passed)
and the full suite once more at the end (4455 passed/8 skipped/0 failed, unchanged) to confirm the
template-file edits introduced no regressions. No separate reviewer-agent pass requested for this
docs-only ticket (judgment call, consistent with the `T-3FlD46`/`T-vmI0jI` precedent): dev-epic's
own line-by-line citation re-verification plus the DV-15 fixes constitute the review.
