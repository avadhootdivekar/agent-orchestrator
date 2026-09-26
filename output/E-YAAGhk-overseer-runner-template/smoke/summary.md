# T-23yMMB — overseer-runner live smoke run: summary

Epic: `E-YAAGhk-overseer-runner-template` · Ticket: `T-23yMMB-live-smoke-run`
Date: 2026-09-27 · `ao` snapshot: `e76caef` (this branch's HEAD at run time)

## What was run

A scratch workspace + a fresh, tiny toy git repo (`greet` CLI: one function, one CLI
entrypoint, one existing test, a README — never a sibling `ao-runner-*` repo), with a
real `origin` bare-remote, driving the `overseer-runner` template with **real
`claude_cli` agents** (`--dangerously-skip-permissions`, `--disallowedTools
ScheduleWakeup,CronCreate,Monitor`, per the finplan reference pattern in
`ao-runner-finplan/specs/agents.json`), for all 6 required roles.

`prompt.md` had two independent asks:
- **A1**: add an optional `--shout` flag to the CLI (uppercase + extra `!`), with a test.
- **A2**: document the new flag in `README.md`.

Three real attempts were made (all real spend, all counted against the $25 cap):

| Attempt | `run_budget_usd` | `wave_size` | Outcome | Real cost |
|---|---|---|---|---|
| run1 | 25 (ticket's literal value) | 3 | **Diagnosed failure** — see Finding 1 | $1.66 |
| run2 | 62 (corrected) | 3 | **Succeeded** — `outputs/final/closeout.md` exists | $3.52 |
| run3 | 57 (corrected) | 3 | **Succeeded** — `outputs/final/closeout.md` exists | $3.29 |

**Total real spend across all 3 attempts: $8.47, well within the $25 cap (AC4).**

Artifacts for all three are under this directory:
`run1-b25-diagnosed-failure/`, `run2-b62-success/`, `run3-b57-success/`.

## Finding 1 (headline): `run_budget_usd=25`/`wave_size=3` (the ticket's own literal
params) cannot survive intake, for ANY parameter combination — a real, structural gap

**What happened (run1, real agent, real spend $1.66):**
1. `git-branch-off` first failed for an unrelated, expected reason: the toy repo had no
   `origin` remote yet (my setup gap, fixed by adding a bare-repo remote — see
   `run1-b25-diagnosed-failure/git-abort.md` for the real agent's own clean diagnosis and
   correct ABORT-not-guess behavior, matching the design's intent).
2. After that fix, `intake` succeeded (a real $1.17 charter/wave1 authoring), but then
   **`ck-01`'s pre_hook failed** with `INT-4: stray hold-request.json not backed by a
   hold decision` (`run1-b25-diagnosed-failure/ck-01-prep-result.json`). The real
   `intake` agent had, on its own initiative, written a `control/hold-request.json`
   (`run1-b25-diagnosed-failure/stray-hold-request.json`) explaining:

   > "This run's budget config cannot fund any wave: with `run_budget_usd=25`,
   > `default_unit_cost_usd=8` and `default_ckpt_cost_usd=5`, projected spend for one
   > 3-unit wave is `3×8 + 5 + 3×8 tail reserve = $53` (212% of budget). That puts the
   > stage in `closeout` with `allowed_wave_size=0`."

   `intake` is not a checkpoint and never goes through the verdict/hold flow, so this
   file is legitimately "stray" from the tool's own perspective — but **the underlying
   math is correct**, independently re-derived below.

**Independently verified** (`overseer_tool.py`'s `derive_budget`/`_intake_allowed_wave_size`,
`compute_allowed_wave_size`): at intake time (no real per-unit cost data exists yet),
the projected-cost formula unconditionally uses the template's **hardcoded**
`default_unit_cost_usd=8`/`default_ckpt_cost_usd=5` (these are **not** exposed as
`ao new` params — they're literals in `overseer-config.json.tmpl`). For `wave_size=3`,
`final_push=true`: projection = `3×8 + 5 + 3×8 = 53`. Checked exhaustively:

| `wave_size` | `final_push` | projection | % of B=25 | % of B=12 (ticket's own fallback) |
|---|---|---|---|---|
| 1 | true | 37 | 148% | 308% |
| 1 | false | 29 | 116% | 242% |
| 2 | true | 45 | 180% | 375% |
| 2 | false | 37 | 148% | 308% |
| 3 | true | 53 | 212% | 442% |
| 3 | false | 45 | 180% | 375% |

**Every single combination exceeds 100% of both $25 and the ticket's own suggested $12
fallback.** `compute_allowed_wave_size` hard-returns 0 whenever the computed stage is
`closeout` — so the very first wave is unconditionally blocked, regardless of how cheap
the *real* per-unit work turns out to be (see Finding 2: real per-unit cost was ~$0.3–0.4,
i.e. ~20–25x cheaper than the $8 default). **The minimum viable `run_budget_usd` for
`wave_size=3`/`final_push=true` to even reach "explore" at intake is ~$67; ~$59 to reach
"converge."** Below ~$36 (`wave_size=1`, `final_push=false`, the cheapest possible
combination), intake cannot emit any wave at all.

**This is not a params-tuning mistake on my part — it's a hard floor set by two
constants the template does not expose for tuning.** Recorded as the top tuning
recommendation below.

## Finding 2: overseer (checkpoint) cost is 14.8-16.8% of total run cost — above the
NFR-8 ~10% target

| Run | Total cost | `ck-01` cost | Overseer % |
|---|---|---|---|
| run2 (B=62) | $3.5219 | $0.5219 | **14.8%** |
| run3 (B=57) | $3.2926 | $0.5523 | **16.8%** |

Both runs used the template default `overseer_effort=high` (I did not override it — the
ticket doesn't ask to). For a genuinely tiny, 1-wave, 3-unit toy task, a single
high-effort checkpoint invocation is a large fraction of the whole run's cost, because
the checkpoint's own per-invocation cost floor doesn't scale down with how little work
there is to judge. **Recommendation**: default `overseer_effort=medium` (or scale it by
wave/unit count) for small runs; `high` is presumably justified for larger, real epics
where the checkpoint has substantially more ledger/breadcrumb history to reason over
per invocation, so this may resolve itself naturally at real-epic scale — worth
re-checking with a bigger, longer smoke run if one is ever budgeted.

## Finding 3 (positive result): a real signal fired and was handled correctly

`run3`'s real digest raised one signal:
```json
{"type": "breadcrumb_integrity", "severity": "high", "id": "S-01-01",
 "message": "breadcrumb changed_paths entry 'toy-repo:README.md' rejected: unknown repo_id"}
```
The real `w01-02-readme-options` unit had labeled its own changed-path breadcrumb with
`toy-repo:README.md` instead of the reposets-configured repo id (`target`) — a real,
minor real-agent labeling quirk (guessing the repo id from the directory name instead
of reading the actual configured id). The real `ck-01` checkpoint correctly:
- diagnosed the mislabel precisely (repo id wrong, path/content correct and in scope),
- verified independently that the actual change was real and correct,
- responded `"accept"` with a rationale well over the high-severity minimum length,
- and the checker (`OV-R12`) correctly validated this as a well-formed response.

This is exactly the intended signal-response design working end to end with a real LLM,
not a mock. No template gap found here — logged as a **positive** result, not a finding
needing action, though it's worth optionally teaching the `10-work-unit.md`/breadcrumb
instructions to state the exact configured repo id explicitly (rather than relying on
the agent to infer it) as a cheap, low-priority hardening follow-up.

## AC-by-AC status

1. **Evidence files exist; run reached `closeout.md`, OR failure diagnosed with a
   follow-up**: DONE both — run1's failure is diagnosed above (with a structural,
   independently-re-derived root cause, not just "it failed"); run2 and run3 both
   reached real `outputs/final/closeout.md`.
2. **Overseer cost % reported; if >10%, a recommendation is recorded**: DONE — 14.8%/16.8%,
   `overseer_effort=medium` recommendation recorded above and handed to `T-gbccdr`.
3. **At least one run exercised a stage transition beyond `explore`**: NOT organically
   satisfied, and I judge it is not safely achievable within the remaining budget —
   see "Deviation" below. This is the one AC I could not close; flagging it explicitly
   rather than fabricating or forcing a result.
4. **Real spend <= $25 total, per `state.json` cumulative costs**: DONE — $8.47 total across
   all 3 attempts (independently summed from each run's real `state.json`).
5. **Findings recorded in STATUS.md, linked from epic STATUS.md**: done in the same pass
   as this summary (see `T-23yMMB-live-smoke-run/STATUS.md`).

### Deviation: AC3 not organically satisfied — why, and why I stopped rather than
spending further

Per Finding 1, intake's own gate uses **hardcoded** defaults ($8/$5) regardless of real
per-unit cost, and per Finding 1's own table, **no** `run_budget_usd` value simultaneously
(a) survives that gate for a non-trivial wave and (b) is small enough that this task's
*real* cumulative spend (~$3.3-3.5, using real ~$0.3-0.5/unit costs) would represent a
high percentage of it. Both successful runs (`run_budget_usd` chosen specifically to
clear the intake gate) settled back into `stage=explore` the moment `ck-01`'s own
digest was computed from **real** (much cheaper) per-unit cost data — this is *correct*,
intended self-correcting behavior, not a bug, but it means a real transition beyond
`explore` cannot be produced organically without either (a) a `run_budget_usd` low
enough to defeat the intake gate (impossible, per Finding 1 — the run would never start),
or (b) a genuinely larger/longer real task generating enough real cumulative spend to
approach whatever floor-level `run_budget_usd` the intake gate demands (~$36-67
depending on params) — for real per-unit costs of ~$0.3-0.5, that is roughly 100-200
real unit executions, i.e. tens of dollars beyond the ~$8.47 already spent and not a
responsible use of the remaining authorized budget for what is meant to be a smoke
test. I chose to stop and report this honestly rather than either (i) spending
significantly more chasing an organic transition that Finding 1's math shows is not
reachable at smoke-test scale, or (ii) fabricating/forcing an artificial-looking result.
**Recommendation**: either re-run this specific AC once Finding 1's default-cost-constant
fix ships (a smaller, template-appropriate `run_budget_usd` would then organically
reach `converge`/`stabilize` for a similarly tiny task), or accept the design-time
sequence-diagram evidence (HLD S12) plus the T-WruPiv/T-vmI0jI scripted-executor e2e
tests (which DO exercise real stage escalation, `explore->converge->stabilize->closeout`,
against the real `derive_budget` formula — see `tests/test_e2e_builtin_overseer_runner.py
::test_scenario_b_stage_escalation`) as sufficient evidence of the stage machine's
correctness, with this live run instead serving as evidence of real-agent fidelity to
the *design* (which Findings 1 and 3 both demonstrate clearly) rather than budget-stage
coverage specifically.

## Stage transitions observed

- run1 (intake, real agent, real math): `explore` -> **`closeout`** (blocked, $0 further
  spend, no wave emitted) — a real transition beyond `explore`, but a degenerate,
  everything-blocked one, not the graceful multi-stage degradation AC3 intends.
- run2: `explore` throughout (intake computed `converge` at 85.5% via the hardcoded-default
  projection: `_intake_allowed_wave_size` doesn't persist a "stage" anywhere checkable
  post hoc beyond allowing the wave through; `ck-01`'s own real digest — the first
  ordinarily-inspectable stage value — was `explore` at 3.3% real spend).
- run3: `explore` throughout (real spend 3.29/57 = 5.8%).

## Checker rejections

- run1: 1 non-organic, config-driven rejection (`INT-4`, Finding 1) — resolved by the
  operator (me) correcting `run_budget_usd`, not by agent self-correction within the
  same attempt.
- run2, run3: **zero** — both `intake` and `ck-01` passed their real checks on the first
  attempt (`ok: true, violations: []` in both `check-result.json` files).

## Signals raised and responses

- run2: none.
- run3: one (`S-01-01`, `breadcrumb_integrity`, `high`) — see Finding 3. Responded
  `accept` with a full rationale; validated correctly by `OV-R12`.

## Whether each ask ended usable

**Independently verified by me, not just claimed by the agent** — checked out each
run's real final branch and ran the toy repo's real test suite directly:

- run2 (`overseer/o-c1we9t-smoke2`, commits `f823ecf`/`5d7e8be`... final `60931c0`):
  `pytest tests/` -> **2 passed**; `python -m src.greet World --shout` ->
  `HELLO, WORLD!!`; default unchanged -> `Hello, World!`. README's new "Options" section
  reads correctly. **Both asks (A1, A2) ended usable.**
- run3 (`overseer/o-w59rfc-smoke3`, commits `65537f7`/`60931c0`): same independent
  check — `pytest tests/` -> **2 passed**; same functional verification. **Both asks
  ended usable.**

## Tuning recommendations (for `T-gbccdr`)

1. **(Primary, from Finding 1)** `default_unit_cost_usd=8`/`default_ckpt_cost_usd=5`
   (hardcoded in `overseer-config.json.tmpl`) are ~20-25x the real observed per-unit
   cost for a small task on the current model/effort settings, and are not exposed as
   `ao new --param` overrides. This creates a **hard, undocumented floor** on viable
   `run_budget_usd` (~$36-67 depending on `wave_size`/`final_push`) below which intake
   can never emit a first wave at all — the ticket's own suggested `run_budget_usd=12`
   fallback is unconditionally infeasible for any `wave_size`/`final_push` combination
   (see the table in Finding 1). Recommend: either expose these as template params, or
   lower the hardcoded defaults to something closer to real observed costs (e.g. $1-2),
   or at minimum document the real floor in the template's `README.md` / HLD so a future
   operator doesn't hit the same wall (and doesn't repeat the $12 suggestion verbatim in
   any future ticket).
2. **(From Finding 2)** `overseer_effort=high` costs 14.8-16.8% of total run cost for a
   trivial 1-wave/3-unit task, above the ~10% NFR-8 target. Recommend defaulting
   `overseer_effort=medium` for small runs, or noting that this ratio should be
   re-measured at real-epic scale (more units/waves per checkpoint should amortize the
   fixed per-checkpoint cost down).
3. **`GOAL_SIMILARITY_REJECT`/`stall_waves`**: **not exercised** by this smoke run (the
   task was too small/clean to trigger a repeat-work or stall pattern) — no
   recommendation can be honestly made from this evidence; T-WruPiv's/T-vmI0jI's own
   scripted e2e tests (`test_scenario_f_signal_response`, the `period_repeat` detector)
   already cover this mechanism's correctness at the unit/component level, so this is
   not a coverage gap, just something this particular live run didn't happen to surface.

## Real spend ledger (grand total)

| Attempt | Cost |
|---|---|
| run1 (git-branch-off + intake, before the diagnosed INT-4 stop) | $1.6607 |
| run2 (full, 9 tasks, succeeded) | $3.5219 |
| run3 (full, 9 tasks, succeeded) | $3.2926 |
| **Total** | **$8.4752** (of $25 authorized) |
