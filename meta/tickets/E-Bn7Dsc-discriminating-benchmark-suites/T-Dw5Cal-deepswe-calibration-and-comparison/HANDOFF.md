# HANDOFF / live roadmap: T-Dw5Cal

Read this first after a restart, a compaction or a new session. It holds the current state, what is running, how to
resume, and the next steps. TASK.md has the pre-registered plan; STATUS.md has the dated history.

- Last updated: 2026-11-12 10:05 UTC (Claude, manager)
- Branch: `ad/enhancements-10nov` (in sync with origin, unmerged; keep committing here)

## Owner rules in force
- Budget: **$500 total** for T-Dw5Cal (calibration, runs, everything). $8 cap per task, same for both arms.
- ao gets generous limits: subject `wave_size 6`, `max_waves 12`, agent timeout x1.5.
- Time rule: a solve fails if it takes longer than max(the other arm's median + 60 min, 2x the other arm's median).
  `compare.py` applies it.
- **2026-11-12: let the finplan ao run finish its waves, then retrigger the Harbor tasks once the load is low.**
  Check status every 30 min. `finplan-dev1` must never go down.
- Host token only, passed in a per-job 0600 file that is deleted afterwards. Never copy the refresh token. After
  each batch, grep the job dirs for `sk-ant-oat`.

## State now
- Spend so far: about **$92.17 of $500**, about $408 left.

  | Item | Amount |
  |---|---|
  | Phase 0/1 | $33.06 |
  | Round 1, plain | $11.15 |
  | Round 1, ao (5 of 13 tasks) | $29.22 |
  | Calibration 1e (22 of 91 tasks) | $18.74 |
  | 3 trials in flight at the stop | not yet counted |

- Finplan run `o-1eyd60-ux-focused-parallel` (`ao run` PID 289906) is at wave 13 of 30, checkpoint ck-12 said
  "continue". Its Rust builds pushed the host load to 160 earlier.
- **Both Harbor drivers are holding** (`[wait-hold]`). They launch only when the gate below clears.
  - Gate: no finplan `ao run` alive (`/var/tmp/ao-harbor/pilot/hold-finplan.sh` exits 1), and 1-min load <= 16.
  - Drivers: `p2-ao.sh` (log `p2-ao.out`) and `p2-plain.sh` (log `p2-plain.out`), both in `/var/tmp/ao-harbor/pilot/`.
  - Restarted 2026-11-12 ~09:50 UTC with `--hold-while` and `--max-load 16`.
- The 3 trials in flight at the stop were left running: ao `koota-pair-relation-tracking`, plain
  `prometheus-typed-label-sorting` and `psd-tools-blend-range-api`. The drivers log `[wait-inflight]` for them and
  count their results when they finish; they do not run them again.
- Calibration 1e: 69 tasks left, including the `optique-conditional-option-dependencies` retry (ECR rate limit,
  $0, archived as `.infra-1`).
- ao round 1: 8 tasks left: `koota-pair` (in flight), `obsidian-linter-link-format-conversion`, and the 6
  regression tasks.

## How to check and resume
```bash
cd /var/tmp/ao-harbor/pilot
tail -n 5 p2-ao.out p2-plain.out          # [wait-hold] / [launch] / [done] / [end]
./hold-finplan.sh; echo $?                 # 0 = finplan ao run still alive (hold)
uptime                                     # launches need 1-min load <= 16
ps -eo pid,args | grep -E '[b]udgeted.py'  # both drivers alive?
```
- If a driver died, restart it at any time with `./p2-ao.sh >> p2-ao.out 2>&1` (or the plain one). Resume is
  safe even with trials still running:
  - finished jobs are skipped;
  - running ones are deferred;
  - killed or $0-infra ones are archived to `<job>.infra-<n>` and rerun.
- To stop new launches without losing running trials, SIGKILL the `p2-*.sh` bash and its `budgeted.py`, using
  explicit PIDs. Never `pkill -f` a pattern that appears in your own command line.
- Per-task logs: `pilot/<prefix>/<task>.log`. Jobs: `/var/tmp/ao-harbor/jobs/<prefix>-<task>`.

## Roadmap (pre-registered; details in TASK.md Phase 2)
| # | Step | Status | Budget |
|---|---|---|---|
| 1 | Round 1, plain fresh, 13 tasks | **done**: 7/13 solved; 3 verdicts flipped vs Phase 1 | $11.15 of $30 |
| 2 | Round 1, ao, 13 tasks | 5/13 done (yjs solved; bandit, sqlfmt, pest, koota-deferred failed); held | $29.22 of $104 |
| 3 | Calibration 1e, plain, 91 tasks | 22/91 done; held | $18.74 of $90 |
| 4 | Rebuild sets over all 113 tasks (`sets/build_sets.py` on p1a, p1c-\*, p1e-\*) | after step 3 | $0 |
| 5 | Round 2: new grading tasks, both arms, 1 attempt each | after step 4 | reserve |
| 6 | Round 3: more attempts on grading tasks, in seeded order, until $500 | after step 5 | rest |
| 7 | Report with `compare.py` (time rule; flag attempts run under high load) and update STATUS/TASK/epic/README/KB | at the end | $0 |
| 8 | SWE-Marathon | only if >= $100 is left after steps 1-5 | |

Open owner decisions:
- "ao-lean", a third arm (right-size small tasks, no-git closeout, time budget). Proposed; waiting for the owner.
- Optional `claude setup-token`: removes the host-login waits, but a leaked token stays valid for months.

## Check log (newest first)
- 2026-11-12 10:05 UTC: owner asked to wait for the finplan waves.
  - Stopped both drivers. The 3 trials in flight were left running.
  - Added `--hold-while` plus restart safety to `budgeted.py`. 27 Harbor tests pass.
  - Restarted both drivers holding behind `hold-finplan.sh` and load <= 16. Finplan is at wave 13 of 30.
