# TASK: T-C6uQJW-tool-loop-progress-detectors

## Metadata
- Task ID: `T-C6uQJW-tool-loop-progress-detectors`
- Epic ID: `E-YAAGhk-overseer-runner-template`
- Owner: developer
- Created: 2026-09-26
- Last Updated: 2026-09-26
- Status: Draft
- Estimate: 3 days (24 h)

## Requirements Mapping
- Requirement IDs: FR-6, FR-7 (ask_starvation, prompt_changed), NFR-2
- Design: `docs-md/overseer-runner-hld.md` §8.3, §8.4 M2

## Description
Implement module M2 in `tools/overseer_tool.py` as pure functions over ledger lines, path history,
and previous verdicts. The functions are `detect_period`, `detect_mirror`, and `detect_signals`,
and they produce `signals[]` plus the `progress{}` digest section. Signals:

- `period_repeat`, `mirror_flipflop`
- `content_oscillation`, which is **trim-first**: if this task slips, ship the rest and file a
  follow-up
- `breadcrumb_integrity`, `stall`, `repeated_failure`, `attempt_cap`, `ask_starvation`,
  `blocked_units`, `prompt_changed`

`wave_signature_repeat` is deferred (NFR-X10). Signal ids `S-KK-NN` are assigned in a deterministic
order: (type, work_item, path). Thresholds are named constants: `MAX_PERIOD`,
`MIRROR_HALF_LENGTHS`, `MIN_REPS_PERIODIC`, and `MIN_REPS_SINGLE`. The detector config values come
from `overseer-config.json`: `stall_waves` and `max_attempts_per_item`.

- Inputs: ledger lines (T-ABDjSj format), `path-history.json`, `ck-*/verdict.json` (`criteria[]`),
  `charter.json`, and `prompt.md`
- Outputs: the `signals[]` list and the `progress{}` dict, merged into the digest by `ckpt_prep`

## Acceptance Criteria
1. `detect_period` table tests. The token strings below are shorthand for `kind:verdict` tokens.
   - `[r:f, x:p, r:f, x:p]` gives `(2, 2)`
   - `[a,b,c,d,a,b,c,d]` gives `(4, 2)`
   - `[a,a,a]` gives `(1, 3)`
   - `[a,a]` gives None
   - `[a,b,a]` gives None
   - `[a,b,c,a,b]` gives None
   - the smallest period wins: `[a,b,a,b,a,b,a,b]` gives `(2, 4)`, not `(4, 2)`
2. `detect_mirror`: `[a,b,b,a]` gives 2; `[a,b,c,c,b,a]` gives 3; `[a,a,a,a]` gives None (the first
   half must have ≥ 2 distinct tokens); `[a,b,a,b]` gives None.
3. `content_oscillation` on a real temp git repo fixture: a file changed A→B→A across three
   checkpoints gives one medium signal, and two such files give high. A file deleted and then
   restored counts (`<absent>` is a state). A path the unit omitted from `changed_paths` but that
   `git diff` shows is still detected.
4. `stall`: no newly done item and non-increasing `criteria_met` for `stall_waves` consecutive waves
   gives high. Hold waves (zero units) are skipped in the count.
5. `attempt_cap` fires at `max_attempts_per_item`. `repeated_failure` fires when the last 2 units on
   an item have verdict `fail`. `ask_starvation` fires when a non-met, non-deferred ask has had no
   units in the last `stall_waves` waves. `blocked_units` fires for outcome `blocked` or
   `needs_input: true`. `prompt_changed` fires on a sha mismatch against the charter.
6. `breadcrumb_integrity` is emitted (high) for each entry that M1 rejected.
7. Signal ids and ordering are deterministic: shuffling the input ledger line order within a wave
   gives an identical `signals[]` list.
8. `progress{}` has the per_ask units, cost, done items, and last wave touched, plus work item
   totals and done counts, matching hand-computed fixtures.
9. The M2 lines are ≥ 90% covered. `ruff`/`mypy` are clean. A `reviewer`-agent review is recorded
   in STATUS.md.

## Risks
- False positives (e.g. a legitimate revert is flagged as an oscillation). Mitigation: severities,
  plus the overseer may `accept` with a rationale.
- The git fixture tests need `git` in CI. It is already required by the isolation test suites.

## Dependencies
- T-ABDjSj: the ledger and path-history formats (frozen by day 2 of that task).

## Pseudocode / Algorithm
```text
See design §8.4 M2 (detect_period, detect_mirror, detect_signals) and the §8.3 rule table.
returns_to_earlier(hist): any k where hist[k] == hist[j] for some j <= k-2 and hist[k-1] != hist[k]
```

## Schemas / Interface Notes
- Signal object: `{id, type, severity: low|medium|high, work_item?, ask_id?, path?, evidence{}, message}` (§13.4 digest)

## Handoff Boundary
- Upstream: T-ABDjSj.
- Downstream: T-tAKBBB (OV-R12 requires answers to these signal ids), T-vmI0jI scenario (f).

## Artifacts
- Tests: `tests/test_overseer_tool_detectors.py`
