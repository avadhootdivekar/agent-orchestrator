# TASK: T-Rs4Qv8-named-resources-and-concurrency-groups

## Metadata
- Task ID: `T-Rs4Qv8-named-resources-and-concurrency-groups`
- Epic ID: `E-Sn7tL2-sentinel-long-running-overseer`
- Type: ENHANCEMENT (engine; spec-level, workflow-agnostic)
- Owner: `TODO`
- Created: `2026-11-12`
- Status: `Draft` (design only). Checked 2026-11-12 against clone HEAD: one pool (`ceiling`) + one soft limit
  (`live_limit`/`max_parallel`, `engine.py`, `control.py`); no per-group limit exists today.
- Priority: normal, not blocking. FinPlan is unblocked without it (fin_plan `30b3fa85`: `scripts/res-slot.sh`,
  `scripts/cargo-slot.sh`); the value of this ticket is engine visibility (Sentinel/UI) and reuse by other workflows.
- Origin: `T-Fp9Hng` E5/E6; measured in run `o-k6shov-ux-complete-500`

## Problem (generic form)
A run has one global concurrency limit (`limit`/`ceiling`, see `control.py`). When several tasks share a scarce
resource (a build machine, a database, a GPU, a rate-limited API, a licence), the only way to serialise them is prose in
the task brief ("take this flock lock"). The agent takes the lock itself, so the engine cannot see it:
- a task queued on the lock looks idle/hung to the Sentinel (idle-kill, T-Fp9Hng E2) and burns agent time polling;
- no wait-time accounting; no fairness; a leaked lock is invisible;
- the only options are "singleton" or "no limit".
Measured (FinPlan run): 68% of ~81 agent-hours were >= 2 min polling/waiting; 107/161 attempts referenced the one lock;
avg concurrency 2.97 on a 32-core host (see T-Fp9Hng E6).

## Are these generic? (decision record)
- **Named resources with capacity: yes.** Same idea as Airflow pools, Argo semaphores, GitHub Actions `concurrency`,
  make's jobserver: a task declares what it needs, the scheduler enforces capacity. Meaningful for software and
  non-software workflows. AO owns only the mechanism; the resource names/capacities are declared by the workflow/run.
- **Lanes: NOT a separate concept.** "web/Rust/Python lane with a cap of N" is a *concurrency group* = a resource whose
  capacity is a cap and that a task `requires` by tag. So: one mechanism, no lane type in the engine. What a lane means
  (which tasks, how many) and how a planner (the Overseer) fills lanes is prompt/policy and stays out of AO.
- **Not in scope:** test scope, what to chunk, cargo/target dirs, wave sizing policy (repo/run concerns, see fin_plan
  ticket `T-t8Ls3q-...`). No change to built-in templates.

## Proposed design (to be refined by an architect pass)
1. Declare in workflow/run config: `resources: {<name>: {capacity: N, scope: run|workspace}}` (default none = today).
   `scope: workspace` so two concurrent runs on one host can share a name (the real FinPlan case: one host, many runs).
2. Task spec field `requires: [<name> | <name>:<units>]` (units default 1). Also accepted in `emit_tasks` manifests
   (checked by `graph_check`: unknown resource, units > capacity, deadlock-prone cycles are rejected).
3. Engine acquires before starting the task (state `queued_for_resource`, distinct from `pending`/`running`), releases on
   any terminal state including kill/crash/engine restart (lease with owner pid/run + heartbeat; stale leases reaped).
   The agent never touches a lock.
4. Wait time is recorded (`resource_wait_seconds`) and excluded from idle/stuck/timeout accounting and from
   `running_seconds`; shown in status/UI/`ao ctl`; counts in utilisation digest so the Overseer/Sentinel can see
   "slots idle because of resource X".
5. Fairness: FIFO per resource, optional priority; no starvation of a large `units` request.
6. Optional: a built-in `disk` resource-style guard is T-Fp9Hng E5 item 1 (separate, simpler floor check).

## Review corrections (2026-11-12)
1. **Granularity: command-scoped leases are required, not only task-level `requires`.** A task-level lease holds the
   resource for the whole task, but a build unit spends most of its time editing and thinking (FinPlan median task
   22 min; a warm targeted build 1-120 s). Task-level acquisition alone would serialise more than the agent-held lock it
   replaces. Add `ao resource run <name> [--units N] -- <cmd>`: a CLI the agent wraps around ONE command; the lease is
   registered with the engine under the task id, wait time is accounted, and the Sentinel shows "waiting on <name>"
   instead of idle. Task-level `requires` stays for resources a whole task occupies (a device, a deploy target, a seat).
2. **Export the slot index** (`AO_RESOURCE_SLOT`, `AO_RESOURCE_SLOTS`) to the command/task. This is what lets a workflow
   keep a warm per-slot cache or port range (FinPlan: slot k builds in target dir k: 108 s cold vs 1 s warm).
3. **Scope `host`, and interop with non-AO processes.** Runs from different workspaces and interactive sessions share
   one host. Add `scope: host` (lease store at a configured host path), and implement leases as flock slot files at that
   path so a non-AO process using the same files (FinPlan `scripts/res-slot.sh`, same layout `<dir>/<name>.<k>.lock`)
   shares the same capacity. Locks must not be inherited by daemons the command starts.
4. Reference behaviour to copy (tested, 46 checks / 13 mutants): fin_plan `scripts/res-slot.sh` + `scripts/tests/test_res_slot.sh`.

## Acceptance Criteria
- [ ] AC1: temp-workspace run with resource `heavy` capacity 2 and 6 independent tasks each `requires: [heavy]`: never
  more than 2 run concurrently; the rest show `queued_for_resource`; total runtime ~ 3 task-lengths (test, not mock clock only).
- [ ] AC2: a queued task is NOT idle-killed and its wait is excluded from `running_seconds` (mutation check: remove the
  exclusion -> test fails).
- [ ] AC3: lease released on task kill, failure and engine crash+resume; a stale lease from a dead owner is reaped;
  no leak after `ao resume`.
- [ ] AC4: `scope: workspace` shared by two concurrent runs in one workspace respects the one capacity.
- [ ] AC5: `graph_check` rejects `requires` of an undeclared resource or units > capacity in a manifest and in `inject`.
- [ ] AC6: status/`ao ctl show`/UI list holders, queue and wait; digest exposes per-resource queue length.
- [ ] AC7: runs that declare nothing behave exactly as today (regression test on an existing workflow).
- [ ] AC8: docs: ADR (generic semantics, why no lane type) + README section; no codebase-specific text in built-in templates.
- [ ] AC9: command-scoped lease: 4 agents run `ao resource run heavy -- sleep 5` with capacity 2 -> never more than 2 at
  once; a waiting task is not idle-killed; killing the agent releases its lease; a daemon started by the command does
  not keep the lease.
- [ ] AC10: the slot index is exported; with `scope: host`, an AO lease and a plain flock holder on the same slot file
  exclude each other.

## Risks / Dependencies
- Interacts with soft barriers (T-Sn6Brr) and wave planning: a manifest with many tasks requiring one resource must not
  be treated as stuck. Needs the E3 cycle/self-dependency fix first so resource edges cannot add false cycles.
- Deadlock: tasks needing multiple resources -> acquire all-or-nothing in a fixed order.
- Multi-run host sharing (scope: workspace) needs a durable lease store; keep it under the workspace `.orchestrator/`.
- Companion (not AO): fin_plan `T-t8Ls3q-run-throughput-test-scope-lanes-per-lane-build-dirs`, landed in fin_plan `30b3fa85`
  with its own counting slots (`cargo`=3, `live`=2). Once this exists FinPlan maps those names to engine resources.
  Test Mongo and ports turned out not to need a resource: each unit gets a private Mongo port and a per-slot port base.
- Also from the same analysis, separate in `T-Fp9Hng`: "no agent polling" (turn-ended + background command, engine
  resumes the agent) - related but a different mechanism (agent lifecycle, not scheduling).
