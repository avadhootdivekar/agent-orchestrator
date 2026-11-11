# STATUS

- ID: `T-Dw5Cal-deepswe-calibration-and-comparison`
- Updated At: 2026-11-11
- State: **In Progress**: Phase 0 and Phase 1 done. Waiting for the owner's Phase 2 budget and the max_waves
  decision.
- Owner: Claude (manager) for avadhoot

## This update
- By: avadhoot · Role: user · Date: 2026-11-11 · Comment:
  - First pass on DeepSWE: build a solvable baseline (regression tests) and an unsolved set (grading).
  - Verify that the complete comparison works, then move to SWE-Marathon.
  - DeepSWE verification budget is **$100**.
  - Check the first run after the current ao-arm Harbor pilot finishes.
- By: Claude · Role: manager · Date: 2026-11-11 · Comment: task opened with the roadmap in TASK.md. TB 2.1 is
  dropped as a primary suite (near saturated for Sonnet 5.5 and Opus 5.5).
- By: Claude · Role: manager · Date: 2026-11-11 · Comment: Phase 0 done.
  - `pilot3-tb21-dna-ao-sonnet-nogit` reward 1.0, parity clean, adapter committed 8150ccd.
  - The first 1a/1b launch (both arms in parallel, `-n 2`) was killed by host memory pressure during agent
    setup. $0 spent; job dirs kept as `*.killed-oom`.
- By: avadhoot · Role: user · Date: 2026-11-11 · Comment:
  - The finplan ao run is done.
  - `finplan-dev1` (dev cluster) must not go down.
  - Rerun the tests at least sequentially if memory allows.
- By: Claude · Role: manager · Date: 2026-11-11 · Comment: **Phase 1 done.**
  - Plain Sonnet 5.5 calibration: 22 DeepSWE tasks, one attempt each. 15 solved (regression set), 7 failed
    (grading set), 0 infra exclusions after retries.
  - Results file: `benchmarks/harbor/sets/deepswe-v1.1-sonnet-5.5.json`.
  - The ao arm is verified on DeepSWE on 3 tasks: reward, cost and ao run state recorded; `harbor-parity` is
    clean after fixes; no token in job files; secrets deleted.
  - Four harness defects found and fixed along the way (see below).
  - DeepSWE spend: **$33.06 of $100**.
  - Host memory: one DeepSWE container per lane, 2 lanes. `finplan-dev1` untouched.

## Phase 1 results

Plain Sonnet 5.5 (Claude Code 2.1.321, effort high, cap $8/task, no web, dataset timeout):

| | Tasks | Solved | Failed | $/attempt (mean / max) | Agent minutes (mean / max) |
|---|---|---|---|---|---|
| Go | 6 | 6 | 0 | $0.80 / $1.07 | 7.6 / 11.7 |
| Python | 7 | 5 | 2 | $0.79 / $2.03 | 5.9 / 12.9 |
| TypeScript | 7 | 4 | 3 | $0.82 / $1.85 | 5.1 / 9.6 |
| JavaScript | 1 | 0 | 1 | $1.31 | 8.8 |
| Rust | 1 | 0 | 1 | $0.46 | 4.4 |
| **All** | **22** | **15 (68 %)** | **7** | **$0.81 / $2.03** | **6.2 / 12.9** |

- The $8 cap never bound: the maximum was $2.03. The cap stays $8 for Phase 2, as planned.
- **Grading set** (failed once): `sqlfmt-create-table-ddl-formatting`, `koota-pair-relation-tracking`,
  `yjs-map-conflict-detection`, `bandit-incremental-cache-control`, `pest-character-class-coalescing`,
  `koota-deferred-mutation-buffer`, `obsidian-linter-link-format-conversion`.
- **All 7 failures are narrow**: 1-2 hidden tests (f2p 30/32, 37/38, 8/9, 87/88, 103/104, 70/71, 58/60).
  - In the inspected cases (sqlfmt, koota) the implementation is complete. The missing tests check exact
    output or behaviour the instruction does not spell out.
  - So "failed" may partly mean "guessed a hidden convention differently", the same effect T-Pf4Mix found on
    Exercism.
  - Phase 2 repeats will show whether these failures are stable.

ao + Sonnet 5.5 (wheel 5626232, harbor subject), verification runs:

| Task | Plain | ao | ao $ / min | ao run |
|---|---|---|---|---|
| sqlfmt-create-table-ddl-formatting | 0 (30/32) | 0 (30/32, same 2 p2p) | $5.84 / 30 | **failed at ck-03** (max_waves trap) |
| koota-pair-relation-tracking | 0 (37/38) | **1** (38/38) | $4.95 / 20 | succeeded |
| claude-code-by-agents-recursive-delegation | **1** (7/7) | 0 (2/7) | $1.97 / 7 | succeeded |

- ao costs about $4.25 per attempt, 3-6 times plain. One gain and one loss on three single attempts is noise.

## Spend ledger (list-price equivalent, subscription token)
| Job | Arm | Tasks | Cap/task | Spent | Note |
|---|---|---|---|---|---|
| pilot1-tb21-dna-cc-sonnet | plain Sonnet 5.5 | 1 | $10 | $0.25 | reward 1.0 (T-Hb3Adp pilot) |
| pilot1/pilot2 ao | ao + Sonnet 5.5 | 1 | $10 | $0.00 | harness errors before any LLM call (fixed) |
| pilot3-tb21-dna-ao-sonnet-nogit | ao + Sonnet 5.5 | 1 | $10 | $2.05 | reward 1.0, 9.5 min, Phase 0 gate |
| p1a/p1b `*.killed-oom` | both | 2+2 | $8 | $0.00 | killed in agent setup (memory pressure) |
| p1a `*.netblocked` | plain | 1 | $8 | $0.00 | harness defect 1 (below) |
| p1a `*.nocommit` | plain | 1 | $8 | $2.43 | harness defect 2 (below); excluded |
| p1a-deepswe-cc-sonnet | plain | 2 | $8 | $3.88 | both narrow fails |
| p1b-deepswe-ao-sonnet | ao | 2 | $8 | $10.79 | see the ao table |
| p1c-deepswe-cc-sonnet-* (20 jobs) | plain | 20 | $8 | $13.99 | 13 solved, 5 failed. eicrud's first attempt hit a public ECR pull rate limit ($0, infra) and was retried: solved |
| p1d-deepswe-ao-sonnet-parity-* | ao | 1 | $8 | $1.97 | tool-parity fix verified (parity exit 0) |
| **DeepSWE total** | | | | **$33.06** | of the $100 verification budget |

## Harness defects found and fixed in Phase 1
1. **Inference host blocked (480f8fe).** DeepSWE's agent phase is `no-network`, and Harbor cannot infer the API
   host for an OAuth login, so every API call failed (ECONNRESET). Fix: `run.sh` passes
   `--allow-agent-host api.anthropic.com`.
2. **Uncommitted fix graded as empty (f78ae44).** The shared wrapper says no git changes, and DeepSWE grades
   `git diff base HEAD`, so a completed fix scored f2p 0 on a 0-byte patch. Fix: both arms commit the agent's
   working tree after the run.
3. **Tool parity (17def35).** On offline tasks Harbor removes WebSearch/WebFetch from the plain arm only. Fix:
   ao agents get the same `disallowed_tools`. `harbor-parity` now also ignores ao's run summarizer (a fixed
   Haiku engine session with no repo access).
4. **Spend control and races (2306791).**
   - `budgeted.py` puts a hard total limit on a pool: it starts a task only while spent + in-flight x cap +
     cap stays within the limit.
   - `run.sh` writes the shared prompt template atomically and deletes the token file on INT/TERM.

## Evidence
- Oracle: `/var/tmp/ao-harbor/jobs/oracle-pilot-tb21`, `oracle-pilot-deepswe` (5/5 PASS).
- Phase 0: `/var/tmp/ao-harbor/jobs/pilot3-tb21-dna-ao-sonnet-nogit`.
- Phase 1 jobs:
  - `/var/tmp/ao-harbor/jobs/p1a-deepswe-cc-sonnet`
  - `/var/tmp/ao-harbor/jobs/p1b-deepswe-ao-sonnet`
  - `/var/tmp/ao-harbor/jobs/p1c-deepswe-cc-sonnet-*`
  - `/var/tmp/ao-harbor/jobs/p1d-deepswe-ao-sonnet-parity-*`
  - Logs: `/var/tmp/ao-harbor/pilot/`.
- Sets: `benchmarks/harbor/sets/deepswe-v1.1-sonnet-5.5.json` (built by `sets/build_sets.py`). Sample:
  `sets/deepswe-v1.1-calib-1c.txt` (`sample_deepswe.py`, seed 20261111, language-stratified, plus the 2 pilot
  tasks).
- No `sk-ant-oat` string in any job dir or ao tarball; `/var/tmp/ao-harbor/secrets/` is empty.

## Risks / Blockers
- **Owner decision before Phase 2: the max_waves trap (T-Mw4Clo, 5th occurrence).**
  - The harbor subject's `max_waves=3` forces closeout after wave 3. If wave 3 was not a verify wave, OV-R14
    rejects the closeout and the ao run fails (sqlfmt).
  - The grade still counts (the repo is graded), but ao loses its final verify.
  - Options: (a) fix T-Mw4Clo in ao (a forced closeout schedules a verify instead of failing) and pin the new
    wheel; (b) raise `max_waves` in the harbor subject (spend stays bounded by the $8 cap).
- The grading set may partly measure hidden-convention guessing (above), not difficulty.
- Host memory is shared with other workspaces: keep DeepSWE at 2 lanes or fewer.
- Public ECR rate-limits anonymous pulls: retry excluded tasks (`budgeted.py` resumes).

## Next actions
1. Owner: Phase 2 budget and the max_waves decision (proposal in TASK.md Phase 2).
2. Phase 2:
   - grading set (7) + a stratified regression sample (5), fresh attempts, about 3 per task per arm;
   - same cap, wheel, effort and prompt; `harbor-parity` on the first pair;
   - `budgeted.py` with a hard total limit.
3. Then Phase 3 (SWE-Marathon, CPU tasks).
