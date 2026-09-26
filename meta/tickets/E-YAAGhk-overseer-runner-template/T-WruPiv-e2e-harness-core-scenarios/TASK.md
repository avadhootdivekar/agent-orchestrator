# TASK: T-WruPiv-e2e-harness-core-scenarios

## Metadata
- Task ID: `T-WruPiv-e2e-harness-core-scenarios`
- Epic ID: `E-YAAGhk-overseer-runner-template`
- Owner: tester
- Created: 2026-09-26
- Last Updated: 2026-09-26
- Status: Draft
- Estimate: 3 days (24 h). The harness can start on S2 day 1; the scenarios need the checkers by day 4.

## Requirements Mapping
- Requirement IDs: FR-1, FR-2, FR-3, FR-5, FR-8, FR-9, FR-11, FR-12 (end-to-end)
- Design: `docs-md/overseer-runner-hld.md` §18 (E2E harness + core scenarios), §12.1–§12.3

## Description
Build the e2e harness and scenarios (a)–(d) in `tests/test_e2e_builtin_overseer_runner.py`, driven
from the **outer boundary**: `CliRunner` `ao new overseer-runner … --param
python_bin=<sys.executable>` followed by `ao run` / `ao resume`.

**Harness.** `ScriptedOverseerExecutor(FakeExecutor)` is test-local. It is installed with
`monkeypatch.setattr(agent_orchestrator.executors, "FakeExecutor", ScriptedOverseerExecutor)`
before `CliRunner.invoke`. This works because `DispatchExecutor()` is built per CLI invocation at
cli.py:1108/1461. The harness asserts that the scripted class actually ran. A per-scenario **script**
(a dict keyed by task id or id pattern) says what each task writes:

- intake: charter + briefs + manifest
- units: report + breadcrumb
- `ck-*`: verdict + briefs + manifest
- tail: final files

It also returns a `TaskResult` with a scripted `cost_usd`. Agents config: every
`required_agents` entry is `{"executor": "fake"}`. The real tool runs as a hook subprocess under
`sys.executable`. A scenario's script entries must pass the real checkers, so the harness includes a
helper that builds contract-valid entries.

Scenarios:
- **(a)** Two waves, then an early verified closeout. Wave 2 contains a verify unit per ask with
  verdict pass, so `ck-02` decides `closeout`.
- **(b)** Stage escalation with `run_budget_usd=100`. Scripted costs push the projected spend
  through 80/90/95. The digests show stage explore, then converge, then stabilize, then closeout.
  The stabilize wave has only stabilize/verify/document kinds, and the final checkpoint emits the
  tail.
- **(c)** `ck-01`'s first attempt emits a dangling `depends_on`. Expect `OV-R8`: `ck-01` fails, no
  injection happens, and the run is `failed`. The script's second attempt is correct. After
  `ao resume` the run proceeds to success.
- **(d)** `ck-01` decides hold: it writes the request and emits `ck-02` with an empty wave. `ck-02`
  fails with a `HOLD:` error, and `state.json` shows it with attempts 0 and cost 0. The test writes
  `control/hold-answer.md`, runs `ao resume`, and `ck-02` succeeds. The ledger `unit` line count is
  the same before and after the resumed prep (idempotency), and the `hold_requested` and
  `hold_answered` events exist.

## Acceptance Criteria
1. All four scenarios pass deterministically: three consecutive local runs, and `pytest -p
   no:randomly` if present.
2. Every scenario asserts on end-state files:
   - `outputs/final/closeout.md` exists, for (a), (b), (c), and (d) after resume
   - ledger chain valid (via the tool's `verify_ledger_chain`)
   - each `ck-*/digest.json` has the expected `stage` and `allowed_wave_size`
   - `state.json` `status == "succeeded"`
3. Scenario (b) asserts the exact stage sequence and that no unit of kind implement/research exists
   in waves emitted at stabilize or later.
4. Scenario (c) asserts that the stderr/`check-result.json` for `ck-01` contains `OV-R8`, and that
   no `w02-*` id appears in `state.injected_tasks` before the resume.
5. There are no durations or wall-clock assertions (the engine timestamps with the real clock).
6. The whole file runs in under 60 s locally. The pass/fail counts are recorded in STATUS.md, and a
   `reviewer`-agent review is recorded.

## Risks
- Harness complexity. Mitigation: the entry-builder helper reuses the contract's example shapes
  (the same fixtures T-HPJcc6 AC 4 uses).

## Dependencies
- T-eGXqXH, T-ltBLUY, T-5ZzAZp (paths), T-ABDjSj, T-C6uQJW, T-HPJcc6, T-tAKBBB. T-pYt478 is not
  needed for (a)–(d).

## Pseudocode / Algorithm
```text
class ScriptedOverseerExecutor(FakeExecutor):
    SCRIPT: ClassVar[dict]  (set per test via fixture)
    def execute(self, ctx):
        res = super().execute(ctx)                      # stubs + capture files
        spec = match(self.SCRIPT, ctx.task_id, attempt_counter[ctx.task_id]++)
        for path, obj in spec.writes(ctx): write_json_or_text(path, obj)
        if ctx.task_manifest_path and spec.manifest: write_json(ctx.task_manifest_path, spec.manifest)
        res.cost_usd = spec.cost_usd; return res
```

## Schemas / Interface Notes
- Uses the contract shapes from §13.3 and the artifact schemas from §13.4 verbatim.

## Handoff Boundary
- Upstream: all S1 tasks and the checkers.
- Downstream: T-vmI0jI (reuses the harness), T-23yMMB.

## Artifacts
- `tests/test_e2e_builtin_overseer_runner.py`; the shared harness in `tests/overseer_runner_harness.py`
