# HANDOFF / live roadmap: T-Dw5Cal

Read this first after a restart, a compaction or a new session. It holds the current state, what is running, how to
resume, and the next steps. TASK.md has the pre-registered plan; STATUS.md has the dated history.

- Last updated: 2026-11-12 19:35 UTC (Claude, manager)
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
- Login thresholds: plain `--min-token-hours 0.5` (tasks take 13 min at most), ao 1.5 (runs take up to 95 min).
  - The token lives about 8 h and refreshes only near expiry.
  - `--refresh-host-login` triggers the refresh once the token has expired and the pool is idle.

## State now
- Spend so far: about **$177.20 of $500**, about $323 left.

  | Item | Amount |
  |---|---|
  | Phase 0/1 | $33.06 |
  | Round 1, plain | $11.15 |
  | Round 1, ao | $61.66 |
  | Calibration 1e (91 tasks, done) | $71.33 |
  | Round 2 | running |

- **Round 2 is running** (started 2026-11-12 ~19:30 UTC).
  - Tasks: the 26 new grading tasks from the full calibration, in seeded order:
    `benchmarks/harbor/sets/deepswe-v1.1-p2-r2.txt`.
  - ao: `p2r2-ao.sh` (log `p2r2-ao.out`, prefix `p2-r2-ao-sonnet`), 2 lanes, limit $170. Expected about $150
    (ao's average on grading tasks is $5.79).
  - plain: `p2r2-plain.sh` (log `p2r2-plain.out`, prefix `p2-r2-cc-sonnet`), 1 lane, limit $45. Expected about $26.
  - At most 3 DeepSWE containers. Both limits together still leave at least $108, which keeps the Marathon option
    open.
  - Gate: `hold-finplan.sh` (any finplan `ao run`) and load <= 16. Login thresholds as above.
- **Calibration (113 tasks) done:** 80 solved and 33 failed, 0 excluded.
  - Sets file: `benchmarks/harbor/sets/deepswe-v1.1-sonnet-5.5.json`, built from p1a, p1c-\* and p1e-\* only.
    The comparison attempts are not used for selection.
  - Grading set: the 7 round-1 tasks plus 26 new ones.
- **Round 1 done:** plain 7/13 vs ao 5/13. ao was better on 0 tasks and plain on 2 (bandit, pebble). Details:
  STATUS.md.
  - Note on ao's pebble loss: its own test file collided with the hidden `test.patch`, so the package no longer
    built (0/59, 0/44).
- **Disk (2026-11-12 19:28 UTC):** `/usr` (Docker root) was 95% full.
  - The cause was finplan Rust slot targets in `/usr/avadhoot/finplan-slot-target`, 118 GB.
  - Idle slots `cargo-1` and `cargo-2` (65 GB) were removed while holding their `res-slot.sh` flock. Slot 0 was in
    use by a finplan session and left alone. The owner allowed clearing Rust targets earlier.
  - Now 89 GB free (82%).
- Finplan run `o-1eyd60` finished at 13:17 UTC. A finplan Claude session still runs cargo builds outside `ao run`;
  the load gate covers them.

## How to check and resume
```bash
cd /var/tmp/ao-harbor/pilot
tail -n 5 p2r2-ao.out p2r2-plain.out      # [wait-*] / [launch] / [done] / [end]
./hold-finplan.sh; echo $?                 # 0 = a finplan ao run is alive (hold)
uptime; df -h /usr | tail -1               # launches need 1-min load <= 16; watch disk
ps -eo pid,args | grep -E '[b]udgeted.py'  # drivers alive?
```
- If a driver died, restart it with `./p2r2-ao.sh >> p2r2-ao.out 2>&1` (or the plain one). Resume is safe even
  with trials still running:
  - finished jobs are counted;
  - trials still running hold a lane and a cap until their result lands;
  - killed or $0-infra jobs are archived to `<job>.infra-<n>` and rerun.
- To stop new launches without losing running trials, SIGKILL the driver's bash and `budgeted.py` by explicit PID.
  Never `pkill -f` a pattern that appears in your own command line.
- Per-task logs: `pilot/<prefix>/<task>.log`. Jobs: `/var/tmp/ao-harbor/jobs/<prefix>-<task>`.
- Round 1 logs: `p2-ao.out` and `p2-plain.out` (pools ended).

## Roadmap (pre-registered; details in TASK.md Phase 2)
| # | Step | Status | Budget |
|---|---|---|---|
| 1 | Round 1, plain fresh, 13 tasks | **done**: 7/13 solved; 3 verdicts flipped vs Phase 1 | $11.15 of $30 |
| 2 | Round 1, ao, 13 tasks | **done**: grading 1/7, regression 4/6. Plain was better on 2 tasks, ao on none, 11 ties | $61.66 of $104 |
| 3 | Calibration 1e, plain, 91 tasks | **done**: all 113 tasks, 80 solved and 33 failed | $71.33 of $90 |
| 4 | Rebuild sets over all 113 tasks (`sets/build_sets.py` on p1a, p1c-\*, p1e-\*) | **done**: grading 33, regression 80 | $0 |
| 5 | Round 2: 26 new grading tasks, both arms, 1 attempt each | **running** | ao $170 limit, plain $45 limit |
| 6 | Round 3: more attempts on grading tasks, in seeded order, until $500 | after step 5 | rest |
| 7 | Report with `compare.py` (time rule; flag attempts run under high load) and update STATUS/TASK/epic/README/KB | at the end | $0 |
| 8 | SWE-Marathon | only if >= $100 is left after steps 1-5 | |

Open owner decisions:
- "ao-lean", a third arm (right-size small tasks, no-git closeout, time budget). Proposed; waiting for the owner.
- Optional `claude setup-token`: removes the host-login waits, but a leaked token stays valid for months.

## Check log (newest first)
- 19:20-19:35 UTC: **calibration done** ($71.33): 113 tasks, 80 solved and 33 failed. Sets rebuilt; no token
  leaks.
  - Disk was 95% full. Cleared idle finplan Rust slots 1 and 2 (65 GB) under their flock; now 89 GB free.
  - **Round 2 started:** 26 tasks; ao on 2 lanes, plain on 1.
- 19:23 UTC: calibration nearly done (`go-genai` is the last task in flight); plain calibration about $70.9. Load 7.8.
- 18:53 UTC: calibration 84/91 (60 solved, 24 failed); spend $172.20. The ao lane is idle until round 2.
- 18:40 UTC: **round 1 complete.** `compare.py` (time rule; no time fails):
  | Arm | Solved | $/attempt | Median min |
  |---|---|---|---|
  | plain | 7/13 | $0.86 | 6.1 |
  | ao | 5/13 | $4.74 | 19.8 |
  - Paired: ao better on 0 tasks, plain better on 2 (bandit, pebble), 11 ties; sign test p 0.5.
  - Report: scratchpad `r1-compare.json`. STATUS has the table.
- 18:23 UTC: ao eicrud 1 (14/14, $3.46); happy-dom in flight. Calibration 78/91 (56 solved). Spend $164.20.
  Load 12.
- 17:53 UTC: ao kombu 1 (76/76, $3.18); eicrud in flight. Calibration 72/91 (51 solved). Spend $156.41.
  Load 5.3.
- 17:12-17:23 UTC: the host login refreshed by itself (8 h). Both pools resumed: ao kombu; plain actionlint 1,
  koota-query, dasel. Calibration 65/91; spend $148.21.
- 16:53 UTC: both pools idle at their login thresholds; token 0.39 h, so `[refresh-login]` is due around 17:13.
  - Plain since 16:23: cliffy 1, kea 0 (11/12). Plain calibration $51.62; total spend $147.63.
- 16:23 UTC: ao fastapi 1 (137/137, $3.38); ao waits for the login (token 0.89 h, refresh at expiry).
  - Plain: task-graph 1, superjson 1, fd 1, dynamodb-cond 1; calibration 58/91.
  - Spend $142.97. Load 1.1.
- 15:53 UTC: ao `fastapi` in flight; the next ao task waits for the login (1.39 h left).
  - Plain: `mnamer` (left in flight) finished, `oxvg` (left in flight) is running, and a new plain launch is due.
  - Load 3.0; `finplan-dev1` up.
- 15:45 UTC: ao reached its 1.5 h login threshold after launching `fastapi`. The next ao task waits for the
  token to refresh at expiry, in about 1.5 h.
  - Plain restarted with threshold 0.5 h on the fixed code: 2 trials still in flight were counted, and nothing
    extra was launched.
- 15:35 UTC: plain hit `[wait-login]` (token 1.75 h left). The token refreshes only near expiry, so plain would
  have idled for about 1.7 h.
  - Restarted plain with `--min-token-hours 1.0`; lowered to 0.5 at 15:45. ao stays at 1.5.
  - The restart launched 2 new tasks beside the 2 left in flight: 4 plain containers for about 15 min. Memory was
    fine (43 GB available).
  - Fixed: trials left in flight now hold a lane and a cap in `budgeted.py`. Takes effect on the next driver
    start; 27 tests pass.
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
