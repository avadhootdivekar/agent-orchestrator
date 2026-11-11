# STATUS

- ID: `T-Dw5Cal-deepswe-calibration-and-comparison`
- Updated At: 2026-11-11
- State: **In Progress** (Phase 0 done; Phase 1a/1b running sequentially, `-n 1`)
- Owner: Claude (manager) for avadhoot

## This update
- By: avadhoot · Role: user · Date: 2026-11-11 · Comment: first pass on DeepSWE to get a filtered solvable
  baseline (regression tests) and an unsolved set (grading); verify the complete comparison works, then move
  to SWE-Marathon. DeepSWE verification budget **$100**; check the first run after the current ao-arm Harbor
  pilot finishes.
- By: Claude · Role: manager · Date: 2026-11-11 · Comment: task opened with the roadmap in TASK.md. TB 2.1
  dropped as a primary suite (near saturated for Sonnet 5.5 / Opus 5.5).
- By: Claude · Role: manager · Date: 2026-11-11 · Comment: Phase 0 done (`pilot3-tb21-dna-ao-sonnet-nogit`
  reward 1.0, parity clean, adapter committed 8150ccd). First 1a/1b launch (both arms in parallel, `-n 2`) was
  killed by host memory pressure during agent setup, $0 spent; job dirs kept as `*.killed-oom`.
- By: avadhoot · Role: user · Date: 2026-11-11 · Comment: finplan ao run done; `finplan-dev1` (dev cluster) must
  not go down; rerun the tests at least sequentially if memory allows.
- By: Claude · Role: manager · Date: 2026-11-11 · Comment: 50 GB available, rerunning 1a then 1b one container at
  a time (`/var/tmp/ao-harbor/pilot/p1-sequential.sh`), same flags apart from `-n 1`.

- By: Claude · Role: manager · Date: 2026-11-11 · Comment: two harness defects found on the first DeepSWE
  trials and fixed (inference host blocked by the offline agent policy; uncommitted fix graded as an empty patch).
  Note: on DeepSWE the agent has no web access in either arm (dataset policy, enforced by Harbor), unlike TB 2.1;
  this is the leaderboard condition and keeps parity. 1a/1b relaunched sequentially with both fixes.

## Spend ledger (list-price equivalent, subscription token)
| Job | Arm | Tasks | Cap/task | Spent | Note |
|---|---|---|---|---|---|
| pilot1-tb21-dna-cc-sonnet | plain Sonnet 5.5 | 1 | $10 | $0.25 | reward 1.0 (T-Hb3Adp pilot) |
| pilot1/pilot2 ao | ao + Sonnet 5.5 | 1 | $10 | $0.00 | harness errors before any LLM call (fixed) |
| pilot3-tb21-dna-ao-sonnet-nogit | ao + Sonnet 5.5 | 1 | $10 | $2.05 | reward 1.0, 9.5 min, Phase 0 gate |
| p1a/p1b `*.killed-oom` | both | 2+2 | $8 | $0.00 | killed in agent setup (memory pressure) |
| p1a `*.netblocked` | plain Sonnet 5.5 | 1 | $8 | $0.00 | infra: DeepSWE agent phase is `no-network` and Harbor allowlists no inference host for an OAuth login -> ECONNRESET x10 (fixed 480f8fe: `--allow-agent-host api.anthropic.com`) |
| p1a `*.nocommit` | plain Sonnet 5.5 | 1 | $8 | $2.43 | infra: sqlfmt fixed in 14.6 min but left uncommitted (shared wrapper says no git); DeepSWE grades `git diff base HEAD` -> 0-byte patch, f2p 0/32. Excluded. Fixed f78ae44 (harness commits the worktree after the agent, both arms) |

DeepSWE budget ($100): $2.43 spent so far.

## Evidence
- Oracle: `/var/tmp/ao-harbor/jobs/oracle-pilot-tb21`, `oracle-pilot-deepswe` (5/5 PASS).
- Phase 0: `/var/tmp/ao-harbor/jobs/pilot3-tb21-dna-ao-sonnet-nogit`.

## Risks / Blockers
- None blocking; see TASK.md Risks (host memory shared with other workspaces).

## Next actions
1. Phase 1a/1b results: reward, cost, minutes, `harbor-parity`, token-leak grep, secrets deleted.
2. Size 1c from the measured 1a cost.
