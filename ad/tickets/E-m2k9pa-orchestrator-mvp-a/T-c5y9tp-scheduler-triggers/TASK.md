# TASK: T-c5y9tp-scheduler-triggers

## Metadata
- Task ID: `T-c5y9tp-scheduler-triggers`
- Epic ID: `E-m2k9pa-orchestrator-mvp-a`
- Owner: `TODO`
- Created: `2026-06-16`
- Last Updated: `2026-06-16`
- Status: `Draft`
- Estimate: `1–2 days`

## Requirements Mapping
- Requirement IDs: FR-5, NFR-2, NFR-3

## Description
Implement `scheduler.py`: `Scheduler` ABC, `ManualScheduler`, `CronScheduler` (croniter + injectable clock),
and an `EventScheduler` stub. (LLD §7.)

## Acceptance Criteria
1. `Scheduler.next_fire(trigger, now) -> datetime | None`.
2. `ManualScheduler` returns `None` (on-demand).
3. `CronScheduler` computes next fire after `now` honoring `timezone`, using an **injectable clock**.
4. `EventScheduler` (stub) fires when a sentinel path appears; full event system marked non-MVP.
5. Tests use a **fixed clock**: cron `0 7 * * *` from a known `now` yields the expected next datetime; manual returns None.

## Risks
- TZ/DST correctness — rely on croniter + tz-aware datetimes; test a DST boundary.

## Dependencies
- Upstream: T-r4t8wd (Trigger). Downstream: T-e4u8zx (CLI may show next fire); engine optional hook.

## Pseudocode / Algorithm
```text
CronScheduler.next_fire(t, now): croniter(t.schedule, localize(now, t.timezone)).get_next(datetime)
```

## Schemas / Interface Notes
- Interface: `Scheduler` ABC (LLD §7); injectable `clock`.
- Triggers/events: manual + cron (MVP), event (stub).

## Handoff Boundary
- Upstream: Trigger specs. Downstream: invocation layer / CLI.

## Artifacts
- Docs/comments: this folder.
