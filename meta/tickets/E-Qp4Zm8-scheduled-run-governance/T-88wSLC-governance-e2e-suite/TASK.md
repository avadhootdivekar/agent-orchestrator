# TASK: T-88wSLC-governance-e2e-suite

## Metadata
- Task ID: `T-88wSLC-governance-e2e-suite`
- Epic ID: `E-Qp4Zm8-scheduled-run-governance`
- Owner: tester agent
- Created: 2026-11-10
- Last Updated: 2026-11-11
- Status: Partial (CliRunner quota + cost-cap/usage/resume tiers landed; bug B-1 xfail; AC1/AC6/AC7 delta/AC9b open)
- Estimate: 2.5 days

## Requirements Mapping
- Requirement IDs: all FR/NFR (see `../EPIC.md`)

## Description
The cross-module integration and outer-boundary e2e tier for this epic. These scenarios are the evidence that the
owner's ask works end to end: fixed prompt, per-day/week quotas, per-run and per-week cost caps, a mid-run pause,
resume, expiry, and restart safety. All of it runs under fixed clocks with a scripted executor that reports
`cost_usd`. It never uses a real agent CLI, `$HOME` or the network.

Read HLD §18.2 and §18.3 (normative scenario list). Read the merged code of every upstream task, plus E-Sc9Rt4's
`T-Te3Qw8` suite for its fixtures (`StubSupervisor`, `AO_SERVICE_STATE_DIR`, injected-clock `daemon --once`).

Files you own:
- `tests/integration/test_governance_integration.py` (new)
- `tests/test_e2e_cli_schedule_governance.py` (new)
- `tests/fixtures/governance/` (new: workflows, prompt files, a scripted cost executor helper if E-Sc9Rt4's harness
  lacks one)

Do NOT touch: any `src/` file (file bugs back to the owning task via its STATUS), other tests except additive
fixture reuse.

## Acceptance Criteria
1. **Every** scenario bullet in HLD §18.2 and §18.3 has exactly one named test, and the test names are listed in
   STATUS with pass/fail.
2. Headline e2e (`CliRunner`): `ao schedule add` with `--quota day=2 --cost-per-run 1.00` + `--prompt-file`, then three
   injected-clock `ao schedule daemon --once` ticks across three instants ⇒ two real run directories, each with
   `--prompt-file` bytes equal to the source prompt and the fire record's sha equal to `sha256(prompt)`. The third tick
   is suppressed `quota_exhausted`. `ao schedule usage --json` shows `runs 2/2` and spend equal to the scripted sum.
3. Mid-run cap e2e: 4 serial tasks at $0.40 with `--cost-per-run 1.00` ⇒ halt after task 3, `state.json`
   `status=failed` + `builtin.cost_cap` trip, usage shows `paused` with `overshoot $0.20`. `ao schedule resume ... --add-usd
   1.00 --local` completes task 4. Plain `ao resume --run-id` on a re-capped run ⇒ zero new dispatches.
4. Restart-mid-period: compute usage, rebuild every component from disk, recompute ⇒ equal, and the next fire's
   admit/skip decision is unchanged.
5. Timezone scenarios: Europe/Berlin day boundary; America/New_York DST fall-back double fire contained by `day=1`;
   week rollover with `week_start: sunday`.
6. Backward compatibility: a workspace with no `.ao/schedules.yaml` ⇒ zero `schedule.governance.*` events. `ao run`
   without `--cost-cap-usd` ⇒ `state.json` equal to a baseline apart from the two defaulted fields.
7. The suite runs in < 60 s locally and has no sleeps. All time is injected. Run the full `pytest -q` and report the
   delta vs the epic baseline.
8. Coverage report for the new modules (≥ 80 % each) attached to STATUS.
9. **(H1/M1/M2) Added scenarios:** (a) a 1-task run that crosses its cap finishes `succeeded`, ledger `overshoot_usd > 0`,
   no pause; (b) a run with a killed (sentinel-recovered) attempt is `partial` and charged conservatively; (c) the
   `overlap: queue` drain over quota is burned in its original window.

## Risks
- Real-time sleeps creeping in via the daemon loop. Use `--once` + injected clocks only.
- The scripted cost executor is **T-et7Edf AC15's `fake_cost_script` seam** (`cost_usd` in `token_outputs`); do not build a
  second one. It must set `actuals_available=True`. Otherwise the conservative `unknown` rule (HLD §10.4)
  changes the numbers. Add one test that deliberately exercises `unknown`.

## Dependencies
- Upstream: `T-PpE5lf`, `T-nI4DBf`, `T-rwj4nV` (required); `T-8knqVe` (API cases only; skip-marked with a reason
  if it lags, then un-skip before close).
- Downstream: `T-lTW88y`.

## Pseudocode / Algorithm
```text
See HLD §18.2/§18.3; one test per bullet.
```

## Schemas / Interface Notes
- Interface: CLI boundary only for e2e; `ScheduleEngine` single-stepped for integration.
- Artifacts: temp workspaces + temp state dirs only.

## Handoff Boundary
- Upstream: every implementation task.
- Downstream: the test list + coverage is the evidence `T-lTW88y` re-verifies.

## Artifacts
- Docs/comments: `meta/tickets/E-Qp4Zm8-scheduled-run-governance/T-88wSLC-governance-e2e-suite/`
- Large outputs: `output/E-Qp4Zm8-scheduled-run-governance/` (coverage HTML, if produced)
