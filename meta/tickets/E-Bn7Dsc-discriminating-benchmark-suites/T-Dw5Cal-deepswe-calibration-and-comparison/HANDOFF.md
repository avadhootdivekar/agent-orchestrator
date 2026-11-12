# HANDOFF / live roadmap: T-Dw5Cal

Read this first after a restart, a compaction or a new session. It holds the current state, what is running, how to
resume, and the next steps. TASK.md has the pre-registered plan; STATUS.md has the dated history.

- Last updated: 2026-11-12 15:23 UTC (Claude, manager)
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
- Spend so far: about **$126.91 of $500**, about $373 left.

  | Item | Amount |
  |---|---|
  | Phase 0/1 | $33.06 |
  | Round 1, plain | $11.15 |
  | Round 1, ao (8 of 13 tasks) | $42.76 |
  | Calibration 1e (46 of 91 tasks) | $39.94 |
  
- Finplan run `o-1eyd60-ux-focused-parallel` **finished** at about 13:17 UTC: 30 waves, then closeout. Its
  `final/` has closeout, verify and push reports.
- **Both Harbor drivers are running again** (since about 13:17 UTC). They hold again automatically if a new finplan
  `ao run` starts, or if the load goes above 16.
  - Gate: no finplan `ao run` alive (`/var/tmp/ao-harbor/pilot/hold-finplan.sh` exits 1), and 1-min load <= 16.
  - Drivers: `p2-ao.sh` (log `p2-ao.out`) and `p2-plain.sh` (log `p2-plain.out`), both in `/var/tmp/ao-harbor/pilot/`.
  - Restarted 2026-11-12 ~09:50 UTC with `--hold-while` and `--max-load 16`.
- The 3 trials left in flight at the stop have finished. The restarted drivers count these results at launch time
  and do not rerun them:
  - ao `koota-pair-relation-tracking`: **failed** 37/38, $7.58. The Phase 1 ao attempt solved it.
  - plain `prometheus-typed-label-sorting`: solved 17/17, $0.40.
  - plain `psd-tools-blend-range-api`: solved 45/45, $0.45.
  - No token in the job dirs; the secrets dir is empty.
- Calibration 1e: 45 tasks left (2 in flight).
  - The `optique-conditional-option-dependencies` retry ran and failed 35/36. Its first attempt had hit the ECR
    rate limit ($0) and is archived as `.infra-1`.
- ao round 1: grading 7 done, **ao 1/7 vs fresh plain 2/7**. ao failed obsidian with the same 2 hidden tests as plain (58/60, $3.76). Regression tasks: go-critic solved ($2.19); pebble in flight; 4 left.

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
| 2 | Round 1, ao, 13 tasks | 8/13 done: grading 1/7 (yjs solved); regression 1/1 so far | $42.76 of $104 |
| 3 | Calibration 1e, plain, 91 tasks | 46/91 done; running | $39.94 of $90 |
| 4 | Rebuild sets over all 113 tasks (`sets/build_sets.py` on p1a, p1c-\*, p1e-\*) | after step 3 | $0 |
| 5 | Round 2: new grading tasks, both arms, 1 attempt each | after step 4 | reserve |
| 6 | Round 3: more attempts on grading tasks, in seeded order, until $500 | after step 5 | rest |
| 7 | Report with `compare.py` (time rule; flag attempts run under high load) and update STATUS/TASK/epic/README/KB | at the end | $0 |
| 8 | SWE-Marathon | only if >= $100 is left after steps 1-5 | |

Open owner decisions:
- "ao-lean", a third arm (right-size small tasks, no-git closeout, time budget). Proposed; waiting for the owner.
- Optional `claude setup-token`: removes the host-login waits, but a leaked token stays valid for months.

## Check log (newest first)
- 15:23 UTC: running.
  - ao pebble at about 78 min. It passed ck-01 (continue); one 23-min unit changed nothing, so the time rule may
    fail it.
  - Plain: etree 1, meriyah 0 (46/49), ink 0 (23/25), langchain 1, participle 0 (90/91), numba 1.
  - Spend $126.91.
- 14:53 UTC: running.
  - ao pebble in flight for about 48 min (the limit is 135).
  - Plain: valibot 1, opa 1, dateutil 0 (65/67), ofetch 1, sql-formatter 1.
  - Spend $122.07. Load 3.3.
- 14:23 UTC: running.
  - ao go-critic 1.
  - Plain: happy-dom-abort 1, awilix 0 (23/24), narwhals 1, abs-module-cache 1, ipython 1, bandit-taint 0
    (65/66).
  - Spend $118.90. Load 2.7.
- 13:53 UTC: running.
  - ao obsidian 0 (58/60); ao now on the regression set.
  - Plain since the resume: igel 1, optique 0 (35/36), sqlite-utils 1, dynamodb-toolbox 1, scriggo 1.
  - Spend $112.68. Load 4.5; `finplan-dev1` up.
- 13:17-13:20 UTC: **finplan run done; the gate cleared and both drivers launched by themselves.**
  - The 3 late results were counted ("finished after start"), not rerun.
  - ao: `obsidian-linter-link-format-conversion`. Plain: the `optique` retry and `igel-persist-feature-schema`.
  - Load 2; `finplan-dev1` up.
- 12:23-12:53 UTC: finplan reached w30/ck-30 (max_waves) with decision **closeout**. At 12:53 its last unit (w30-03 verify sweep) was still running; no needs-input. Load 2; drivers alive; `finplan-dev1` up. Watcher `bm0kfvyr7` reports when the run exits.
- Idle checks 10:53-11:53 UTC: held. Finplan went from w17 to w24 (ck-23 continue). Its waves are now short; w24 is "document single blocker status", so the run looks close to the end. Load 3-105, depending on finplan builds. Drivers alive, `finplan-dev1` up.
- 2026-11-12 10:23 UTC: finplan at w16/ck-15 (continue); load 1.7; both drivers holding; `finplan-dev1` up.
  - The 3 trials left in flight finished: ao koota-pair 0 (37/38, $7.58); plain prometheus 1 and psd 1.
  - Spend $100.60.
- 2026-11-12 10:05 UTC: owner asked to wait for the finplan waves.
  - Stopped both drivers. The 3 trials in flight were left running.
  - Added `--hold-while` plus restart safety to `budgeted.py`. 27 Harbor tests pass.
  - Restarted both drivers holding behind `hold-finplan.sh` and load <= 16. Finplan is at wave 13 of 30.
