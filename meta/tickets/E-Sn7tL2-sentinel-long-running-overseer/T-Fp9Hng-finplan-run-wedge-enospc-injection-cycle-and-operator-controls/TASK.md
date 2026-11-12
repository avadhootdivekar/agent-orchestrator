# TASK: T-Fp9Hng-finplan-run-wedge-enospc-injection-cycle-and-operator-controls

## Metadata
- Task ID: `T-Fp9Hng-finplan-run-wedge-enospc-injection-cycle-and-operator-controls`
- Epic ID: `E-Sn7tL2-sentinel-long-running-overseer`
- Type: BUG (5 defects) + ENHANCEMENT, found in the SAME live run as T-Rk4Wn7, 2026-11-11
- Owner: `TODO`
- Created: `2026-11-11`
- Status: `Draft` (analysis only; nothing implemented. Not verified against current clone HEAD - the live engine is the uv-tool snapshot `0.1.0 (8bcacc0.dirty)`)

## Run under observation
- Workspace `/usr/avadhoot/mounted/ao-runner-finplan`, template `overseer-runner`, run id
  `o-k6shov-ux-complete-500-20261110T113138Z` (instance `workflows/overseer-runner/runs/o-k6shov-ux-complete-500/`,
  state `.orchestrator/runs/<run-id>/`). Sentinel + sentinel-llm on. ~$207 of $500 spent at the time of writing.
- Follow-on to `T-Rk4Wn7` (D1-D4 there are about the first failure on 2026-11-10; this ticket is the second
  incident, 2026-11-11).

## Defects

### E1 - Engine wedges (alive, 0.2% CPU, no progress) after ENOSPC; state frozen; sentinel lane disabled
Sequence: `/usr` filled (one finished task's private `CARGO_TARGET_DIR` was 98 GB and never deleted; others 20 GB+).
At 11:58:54 UTC `run.log`: `sentinel.error: OSError(28, 'No space left on device'); sentinel lane disabled for this run`;
logging itself then fails (`logging.StreamHandler.emit` ENOSPC tracebacks in the launch log). After that:
- engine PID stayed alive for ~55 min, all 4 threads in `futex_wait`, CPU 0.2%;
- `state.json` last saved 11:58:38 (never again), `run.log` last write 11:58:54;
- agents that finished AFTER that (`w18-06`, `w18-07`: `attempt-1/result.json` at 12:04 / 12:02) were never settled;
  `state.json` kept them `running`; five tasks looked "53 min idle" because the engine clock was frozen, not the agents;
- the Sentinel was disabled by the same error, so no idle/stuck/alarm logic ran either;
- the run never ended `failed`; nothing alerted. It looked like "tasks stuck on a lock".
Impact: ~54 min of lost wall-clock; wrongly diagnosed as lock contention.
Asks:
1. Treat ENOSPC (and any `OSError` on state/log/transcript write) as a first-class run condition: pause dispatch,
   write a `needs-input`/alarm record (best-effort, small), exit non-zero or enter a clearly-labelled `paused: disk_full`
   state - never silently hang.
2. Atomic state saves (write temp + fsync + rename) so a failed save cannot leave a truncated `state.json`
   (here it survived; see `ao_enospc_disk_full_run_corruption_rca` precedent in the finplan memory).
3. A disk watchdog in the engine tick: below a configurable free-space floor (e.g. `defaults.min_free_gb`) stop
   dispatching new tasks and raise a sentinel alarm; the run prompt's "keep >= 15 GB free" is only advisory today.
4. A liveness heartbeat for the ENGINE itself (`status.json.engine_heartbeat_at`) that the UI/`ao status` compares
   to now, so "engine alive but not ticking" is visible and distinct from "task idle".

### E2 - Sentinel idle-kill is blind to a task's child-process work; turn-ended agent kept alive by a background child
Observed on `w14-04-search-key-stamp-all-writers` (`claude -p`, PID up 55 min): the agent ran a background
`until grep -q EXIT= full.log; do sleep 15; done` and ended its turn (transcript last events: `result`/`end_turn`,
text "I'll continue when it finishes"). The `claude -p` process stays alive while that background shell child lives,
so the engine shows `running`, with no `result.json`. Meanwhile the child tree was doing real work
(`flock ... cargo test --workspace`, `full.log` growing to 1.2 MB). `last_activity_at` stayed at the turn end
(09:59:30), and at 25 min idle the Sentinel killed it: `kill_log: {task: w14-04, reason: "idle", source: "mechanical", at: 10:24:35}`.
Asks:
1. Count descendant-process activity (CPU time delta, or growth of files the task owns) as liveness before an idle
   kill; veto the idle kill if any descendant is runnable/consuming CPU.
2. Detect and label the state "agent turn ended, N background children still running" (last transcript event is
   `result` + live descendants) distinctly from "working" and from "stuck"; warn instead of kill, or kill only
   after a longer, separate bound.
3. Document/guard the agent-side cause: in `claude -p` there is no re-invocation on background-task completion;
   instructions should require foreground commands with a timeout (see finplan memory `ao-full-tester-schedulewakeup-gotcha`).
4. `last_activity_at` semantics: currently set at some ticks, in one case equal to the engine tick not the task's own
   last output; verify what it measures (see E1: all running tasks shared the identical value 11:58:23/38).

### E3 - Injected manifest with a self-dependency crashes the engine silently; checker does not catch cycles / soft barrier
`ck-13` (checkpoint, `emit_tasks`) succeeded at 08:47:48 and its post-hook `ov-ckpt-check` PASSED, yet the manifest
`outputs/manifests/ck-13.json` listed `w14-01-...` with `depends_on: ["ck-13", "w14-01-..."]` (itself). The engine
then logged only `ERROR: Cycle detected: ['ck-14','w14-01-...','w14-03-...','w14-10-...']` and the process EXITED;
`state.json` stayed `status: running`, `ck-13: running` (never persisted as succeeded) for 40 min until noticed.
A second defect surfaced on re-validation: after a resume the regenerated digest had `soft_barrier: true` and the
checker (OV-R10) then required `"barrier": "soft"` on `ck-14`, which the same manifest (written earlier under a
different digest) lacked - the post-hook outcome depended on when the digest was produced.
Asks:
1. `ov-ckpt-check` / `intake-check` must run the same DAG validation as the engine (self-dependency, cycle, unknown id,
   dependency on a not-yet-emitted id) so the failure is a normal `post_hook` failure with a fix-it message for the
   overseer, not an engine exit.
2. Engine: an injection/validation error must fail the INJECTING task (or hold the run) with a persisted reason, and
   must save state first - never exit leaving `running`. Log it at the task level, not only the CLI tail.
3. Make the soft-barrier requirement stable across the checkpoint (read the digest value once at prep and pin it in
   `check-result`), or have `ckpt-check` auto-add/accept the barrier.
Operator workaround used: removed the self-dependency in the manifest JSON, added `barrier: soft` to `ck-14`, `ao resume`.

### E4 - No supported way for the operator to give a mid-run directive or force a stabilize/wrap-up
Needed: "wrap up ASAP, 1-2 salvage waves, then close out". What exists / fails:
- `ao resume` has no `--prompt`; the run prompt is locked: appending to `prompt.md` made `ov-ckpt-prep` fail the next
  checkpoint with `OV-INT-1: prompt.md sha256 does not match charter.lock.json` (run ended `failed`; `ck-18` failed at $0).
- `overseer_tool.py request-closeout` refuses unless `state.json` status is `failed|cancelled` (OV-RUNNING) and produces
  `must_close` => only `final-verify -> closeout -> final-push`, no salvage wave.
- The stage is derived only from % budget spent vs `converge_pct/stabilize_pct/closeout_pct` in `overseer-config.json`;
  the only way to reach `stabilize` early was hand-editing those percentages (1/2) and putting the operator note into
  `branch_policy`.
Asks: (a) `ao resume --operator-note <file|text>` (appended to a per-run, hashed-but-unlocked `operator-notes.md` that
checkpoint/unit instructions are told to read); (b) `request-closeout --stabilize [--passes N]` that sets a
`forced_stage: stabilize` event honoured by `compute_must_close`/`stage_of` and is allowed while the run is stopped OR
at the next checkpoint barrier; (c) document the supported levers in `overseer-contract.md`.

### E5 - Heavy-resource policy lives only in LLM prose; no engine/template support (lock, test scope, scratch cleanup)
- `flock /tmp/finplan-heavy.lock` is an instruction sentence the Overseer copies into every unit brief; each agent
  calls `flock cargo ...` itself. Nothing measures lock wait, enforces release, or shows "waiting on lock" in status.
  A task waiting on the lock is indistinguishable from a hung one (see E1/E2). Host: 32 cores, 62 GB RAM, load 3-5, i.e.
  one global lock leaves the machine mostly idle.
- Unit scope: a build unit (`w14-04`) ran `cargo test --workspace` (336 test binaries, 30+ min) itself; sweeps are
  repeated per unit and again per wave. Cost: wall-clock and disk (98 GB private target dir for one task).
- Per-task private `CARGO_TARGET_DIR`/scratch dirs are an instruction ("delete after"); a task that finished or was
  killed leaked 98 GB and filled the disk (root cause of E1).
Asks (template `overseer-runner` + engine hooks):
1. `unit-gate` pre-hook: refuse to dispatch a unit when free disk < floor (configurable, default 20 GB); log wait.
2. Post-hook / task epilogue hook to remove paths a unit declares in a `scratch:` list (target dirs, containers),
   including on kill/fail.
3. Template guidance (instructions `10-work-unit.md`, `20-checkpoint.md`, contract): build units run only targeted
   tests (changed crate/test file/one jest or Playwright spec + their red-first/mutation controls); workspace-wide
   sweeps only in `sweep`/`final-verify` units. Keep the Done gate unchanged (done-check still needs whole-suite
   evidence) - only WHEN it is produced changes.
4. Optional engine feature: named resource locks declared on a task (`resources: [heavy-build]`) acquired by the
   engine (not the LLM), with wait time recorded and excluded from idle/stuck/timeout accounting, and a configurable
   concurrency (e.g. N heavy lanes) instead of a hard singleton.
5. Prefer a shared cargo target per lane (or sccache) over per-task private targets when a lane builds the same
   workspace; document in the overseer-runner template.

### E6 - Throughput: the run was serialised by a global heavy lock, waited more than it worked, and barriered on stragglers

Measured on run `o-k6shov-ux-complete-500` (2026-11-10 11:31 -> 2026-11-11 ~16:00 UTC; `state.json` + `w*/attempt-*/transcript.jsonl`):
- 151 task attempts, 78.7 task-hours in 26.5 h wall = avg concurrency 2.97 (peak 10). Concurrency was 1 for 31% of wall-clock and 0 for 14% (about 3.8 h = the three incidents E1/E3 plus T-Rk4Wn7 stranding).
- 68% of agent time (55 of ~81 task-hours across 161 attempts) was in Bash commands >= 2 min that were polling/waiting (`until grep -q EXIT= log; do sleep 10; done`). 107 of 161 attempts reference `/tmp/finplan-heavy.lock`: Rust build/test is effectively concurrency 1. Not split: lock queueing vs real compile time.
- Wave tails (only the slowest task running): w03 109 min, w05 86 min, w09 73 min, w14 63 min. `wave_size` 15 configured, real waves 4-10 tasks. Soft barriers only worked from w14.
- Median task 22 min vs 14-15 min in o-12ln4x / o-kpor5a (heavier strong-test requirement). Output: ~9 net Done tickets in 27 h; no net speed gain vs o-12ln4x (213 tasks / 23.6 h).

Where the fixes live (not all are AO):
- AO engine/templates (this repo): items 1b, 3, 5 below and `templates/builtin/overseer-runner/{instructions/10-work-unit.md,20-checkpoint.md,overseer-contract.md,README.md}` for 2, 4.
- Run workspace (ao-runner-finplan `workflows/overseer-runner/runs/<run>/{prompt.md,overseer-config.json}`): per-run lane and wave-size policy for 3, 4. This workspace is not a git repo.
- fin_plan repo: per-lane `CARGO_TARGET_DIR`/sccache/worktree convention and test-scope wording in `CLAUDE.md` and `scripts/native-live-stack.sh` (1a, 2). Needs its own fin_plan ticket on the product branch.

Proposed changes (highest payoff first):
1. Remove the single global heavy lock. (a) fin_plan: per-lane `CARGO_TARGET_DIR`, shared sccache, one git worktree per lane (host 32 cores / 62 GB). (b) AO: engine-managed named resource slots (build slot, test-Mongo slot, ports) declared in the manifest, replacing the flock-in-brief-prose convention; a unit gets its slot before start, so no agent time is spent polling.
2. Targeted tests per build unit (touched test binary / Playwright spec only); the whole-workspace sweep only in dedicated sweep and final-verify units, run in parallel with the next build wave. Strong-test and Opus-review gates unchanged.
3. Rolling waves: soft barrier as the default (needs the OV-R10 stability fix from E3); per-task time box (suggest 45 min) after which the unit is split or its tail deferred; make configured `wave_size` effective.
4. Lanes in the Overseer prompt/manifest (web/UI, Rust, Python ingestion, tests+docs) with a per-lane cap and the 75% build ratio; tests and docs for a landed task run as follow-up units while development continues.
5. No agent polling: the agent starts the command in the background, ends its turn with a "waiting on X" status, and the engine resumes it on completion (also the `w14-04` fix from E2). Pure AO work.

Safety: item 3 and 4 raise parallel load on Mongo, ports and disk; item 1 must come with the E5 disk floor and scratch cleanup, or concurrent cargo lanes refill the disk (see memory: 78 GB in 10 min).

## Acceptance Criteria
- [ ] AC1 (E1): test with a temp workspace on a size-limited tmpfs: fill it mid-run; engine pauses/alarms/exits with a
  persisted reason, state.json still valid JSON, no silent hang >1 tick.
- [ ] AC2 (E1): `status.json` exposes `engine_heartbeat_at`; UI/`ao status` flags a stale heartbeat.
- [ ] AC3 (E2): a task whose agent turn ended but whose descendant keeps CPU/IO is NOT idle-killed; the status shows
  "turn ended, N bg children"; a truly silent task is still killed (mutation-check the veto).
- [ ] AC4 (E3): manifest with self-dependency / cycle is rejected by `ov-ckpt-check` with a specific message;
  engine given such a manifest fails the injecting task and persists state instead of exiting `running`.
- [ ] AC5 (E3): soft-barrier requirement is the same at check time as at digest time.
- [ ] AC6 (E4): `ao resume --operator-note` and `request-closeout --stabilize` work on a temp run; the next checkpoint
  digest shows `stage: stabilize`, `allowed_decisions` per contract, and the note text; `prompt.md` hash untouched.
- [ ] AC7 (E5): `unit-gate` disk floor test; scratch-cleanup epilogue test on a killed task; template instruction text
  updated + guard test that the briefs no longer ask for per-unit workspace sweeps.

- [ ] (E6) A replay or synthetic multi-lane run shows heavy build/test units from different lanes running concurrently (no shared global flock), agent time spent in >= 2 min polling drops below 25% of task time, and no wave ends with a single task running more than 20% of wave wall-time except by design; strong-test/Opus gates still run.

## Risks / Dependencies
- Self-hosting rule: engine edits need a human `install.sh`; test only against temp workspaces. Land via branch + PR.
- E2's veto interacts with T-Rk4Wn7 D2 (peer baseline): both change `sentinel_tick` liveness; coordinate.
- E5.4 (engine-managed resource locks) is a larger design; split into its own ticket/ADR if accepted.

## Evidence (where to look) - all under `/usr/avadhoot/mounted/ao-runner-finplan/`
- Engine log: `.orchestrator/runs/o-k6shov-ux-complete-500-20261110T113138Z/run.log` (ENOSPC line at 11:58:54; `ck-13`
  succeeded 08:47:48; `Cycle detected` is only in `.orchestrator/ui/logs/resume-ux-complete-500.log` line ~1374).
- Resume logs: `.orchestrator/ui/logs/resume2-ux-complete-500.log` (OV-R10 failure), `resume4-...` (OV-INT-1), `resume5-...`.
- `state.json` (`sentinel.kill_log` has the `w14-04` idle kill 10:24:35), `status.json`, `sentinel/ledger.jsonl`.
- Task evidence: `<run>/w14-04-search-key-stamp-all-writers/attempt-1/transcript.jsonl` (turn end 09:59:19, background
  `until grep` loop); `<run>/w18-06-.../attempt-1/result.json`, `w18-07-.../result.json` (finished while state said running).
- Manifest: `workflows/overseer-runner/runs/o-k6shov-ux-complete-500/outputs/manifests/ck-13.json` (pre-edit copy:
  scratchpad `bak/ck-13.manifest.json` of the filing session); `outputs/checkpoints/ck-13/check-result.json` (OV-R10).
- Lock: `/tmp/finplan-heavy.lock`; unit briefs `outputs/waves/w18/briefs/*.json` (the flock sentence).
- Code pointers (clone `src/agent_orchestrator/`): `engine.py` (injection ~2775-2800, `_inject` ~5417, manifest read
  ~2470), `sentinel_tick.py` (liveness/idle rule), template tool
  `templates/builtin/overseer-runner/tools/overseer_tool.py` (`stage_of`, `compute_must_close`, `request-closeout`,
  `ckpt-check`/OV-INT-1/OV-R10) and `overseer-contract.md` ("Budget stages").
