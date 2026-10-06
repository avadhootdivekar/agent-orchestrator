# TASK: T-pdLR96-adversarial-e2e-test-pass

## Metadata
- Task ID: `T-pdLR96-adversarial-e2e-test-pass`
- Epic ID: `E-Ag7Pw3-human-approval-gates`
- Owner: tester
- Created: 2026-10-04
- Last Updated: 2026-10-05
- Status: Draft
- Estimate: 2 days

## Requirements Mapping
- Requirement IDs: NFR-1, NFR-3, NFR-4, NFR-8, FR-11; threat-model rows TM-1..TM-34 that have an MVP
  mitigation
- Design: HLD §7.4, §15, §18, §19, §22.3 (serialized checkpoints)

## Description
Independent verification pass after all implementation tasks:

1. `tests/approvals/test_adversarial.py`: one test per mitigated attack path, named `test_tm<N>_<slug>`,
   driving the real engine/CLI/dashboard (FakeExecutor, fixed clocks), e.g. an "agent" writing an unsigned
   approve file (TM-1), `ao approve` under the marker (TM-2), curl-style POST without `Origin`/principal
   (TM-3), `state.json` flip + resume (TM-5), request weakening (TM-6), spec edit before resume — remove,
   weaken, rename, detach (TM-7), replay into a new request dir (TM-8), swap between view and decide
   (TM-9), drift (TM-10), audit edit/flood (TM-11), manifest smuggling (TM-12), script payload in message
   (TM-13), XFO (TM-14), CSRF (TM-15), decision-file flood and many decided gates (TM-16), bootstrap race
   and no-hard-links (TM-17), insecure key dir incl. `$HOME` as workspace (TM-18), expiry vs restart
   (TM-19), `$USER` spoof (TM-20), file-browser access to the approvals tree (TM-21), symlinked request dir
   (TM-23), losing-race order (TM-24), decoy listing (TM-25), cross-run record (TM-26), post-approval change
   detection (TM-27), workspace-config key redirection incl. `HOME` (TM-28), gate and gate-ancestor status
   flips (TM-29), truncated `spec_sessions` (TM-30), parser bombs in records, audit lines and `state.json`
   (TM-31), reviewed downstream instruction changed while waiting (TM-32), loop-clone tampering (TM-33),
   policy deletion with evidence (TM-34) — plus `test_only_a_verified_record_settles_a_gate`.
2. `tests/approvals/test_events.py`: the event-catalog test of HLD §15 (one fixture run per fail-closed
   verdict).
3. The implementation-completion check of HLD §18.3 (collect-only + name match) and the layering check:
   no case of `tests/approvals/test_import_layering.py` is still skipped.
4. Full quality gates and evidence (this is the final serialized checkpoint of HLD §22.3).

Files — new: the two test files. Shared: none (production code only through bug reports to the owning
developer; the tester may fix a trivial test-only issue). **Exclusive files during stage F** (HLD §22.3): the
two test files; this task runs the final serialized full-suite checkpoint.

## Acceptance Criteria
1. Every TM row with an MVP mitigation (TM-1..TM-34) has a passing test; rows whose mitigation is "none"
   (RR-1 etc.) are listed in STATUS with the reason (no test).
2. `test_events.py` passes: the set of engine `event` names in `run.log` equals the engine rows of §15 for
   the fixture runs; every MAC'd audit line verifies.
3. Every test name in HLD §18.3/§19 exists and passes (missing names filed as defects); no layering case
   skipped.
4. Full pytest: no failure beyond the two known bench failures of the baseline; counts recorded in STATUS
   (passed/skipped/failed) next to the baseline 5041/8/2.
5. `ruff check .` shows only the known I001; `ruff format --check` clean on changed files; `mypy` on all
   changed modules shows no new error (baseline: 4 errors in `_version.py`).
6. Coverage of `agent_orchestrator.approvals` ≥ 90% (number recorded).
7. `cd ui && npx vitest run` green (counts recorded vs baseline 34 files / 406 tests).
8. Every bug found is filed back to the owning task's STATUS with a reproduction; the epic STATUS rollup is
   updated.

## Risks
- Process-based tests (bootstrap race, subprocess e2e) can be slow; keep each under 60 s with timeouts.

## Dependencies
- `T-AGO2L6`, `T-1B8hu4`, `T-drPIif`, `T-1MgGb4`, `T-pfJiXw`, `T-vwIpSw`, `T-otHPGB`, `T-ZPGoSN`,
  `T-nmL0HP`, `T-l43hCg`, `T-pIZq3q`.

## Pseudocode / Algorithm
```text
n/a (test pass)
```

## Schemas / Interface Notes
- Test names and mapping: HLD §18.3; TM rows §7.4.

## Verification
```
WT=/usr/avadhoot/mounted/agent-orchestrator/.claude/worktrees/agent-a513b4d78ac3b20bb
PY=/usr/avadhoot/mounted/agent-orchestrator/.venv/bin/python
cd $WT && $PY -m pytest -o pythonpath=src -p no:cacheprovider -q
cd $WT && $PY -m pytest -o pythonpath=src -p no:cacheprovider -q --cov=agent_orchestrator.approvals --cov-report=term-missing tests/approvals tests/ui/test_approvals_api.py
cd $WT && $PY -m ruff check . && $PY -m mypy src/agent_orchestrator
cd $WT/ui && npx vitest run
```

## Handoff Boundary
- Upstream: all implementation tasks.
- Downstream: `T-mfdlOc` (security review starts from this evidence).

## Artifacts
- Test files; evidence (counts, coverage) in STATUS.md; long logs under `output/E-Ag7Pw3-human-approval-gates/`.
