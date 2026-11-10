# TASK: T-lJu9oZ-quota-window-math

## Metadata
- Task ID: `T-lJu9oZ-quota-window-math`
- Epic ID: `E-Qp4Zm8-scheduled-run-governance`
- Owner: developer agent
- Created: 2026-11-10
- Last Updated: 2026-11-10
- Status: Draft
- Estimate: 1.5 days

## Requirements Mapping
- Requirement IDs: FR-2, NFR-3 (see `../EPIC.md`); owner decision D-4

## Description
Pure calendar-window math: given an aware instant, a period (`day|week|month`), an IANA timezone and a week start,
return the window that contains it. This is the only place window boundaries are computed. The ledger
(`T-Uz0Uol`), the gate (`T-PpE5lf`), the CLI (`T-rwj4nV`) and the dashboard (`T-8knqVe`) all call it.

Read HLD §7 and ADR-0025 D2 first. **No dependency on E-Sc9Rt4**, so this can start immediately.

Files you own (create/edit freely):
- `src/agent_orchestrator/schedules/windows.py` (new). If the `schedules/` package does not exist yet (E-Sc9Rt4
  `T-Sd1Kq7` not merged), create `schedules/__init__.py` as an **empty** file only and say so in STATUS. `T-Sd1Kq7`
  owns its contents.
- `tests/schedules/test_windows.py` (new)

Do NOT touch: `scheduler.py`, `models.py`, any other `schedules/` module, `service/`, `engine.py`.

## Acceptance Criteria
1. `window_for(instant, period, tz, week_start) -> Window` and `windows_for(instant, periods, tz, week_start) ->
   dict[Period, Window]` exist with the HLD §7 signatures. `Window` is a frozen dataclass `(period, key, start, end)`.
   `start`/`end` are aware **UTC**; `start` is inclusive and `end` is exclusive.
2. Keys are `day:YYYY-MM-DD`, `week:YYYY-MM-DD` (the local date the week starts) and `month:YYYY-MM`. They are named
   module constants/format functions, with no ad-hoc f-strings at call sites.
3. Naive `instant` ⇒ `ValueError("instant must be timezone-aware")`. Unknown period ⇒ `ValueError`.
4. For every `t` tested, `w.start <= t < w.end`, and `window_for(w.end, ...)` is the *next* window
   (`key` differs, `start == previous end`).
5. DST: Europe/Berlin and America/New_York spring-forward days are 23 h and fall-back days are 25 h. Each is exactly one
   window. An instant in the repeated fall-back hour (`fold=0` and `fold=1`) belongs to that day.
6. Midnight DST gap: America/Havana 2024-03-10 starts at 01:00 local (`05:00Z`). This is asserted against the value
   computed in the HLD (`local_midnight` returns the first existing instant of the date).
7. Week start: `monday` and `sunday` both tested on a Sunday, a Monday and a Saturday. A week window is always 7 local
   dates.
8. Month: Dec→Jan rollover, February in a leap year (2028-02) and a 31-day month. Australia/Lord_Howe (30-min DST
   shift) month containing its transition.
9. Pure: the module performs no I/O and no clock reads (a test asserts `datetime.now` is never called by patching it to
   raise). Stdlib only (`zoneinfo`, `datetime`).
10. ≥ 95 % line coverage on `schedules/windows.py`; ruff + format clean; mypy clean for the module.
11. **(L5) The label is authoritative.** The window **key (local-date label)** decides membership; `start <= t < end` is
    derived to agree with it. Document this in the module docstring. The corpus adds America/Santiago 2024-04-07 (the
    24:00 transition) and Asia/Beirut 2024-03-31 (midnight gap) in addition to Havana. A comment records that label and
    bounds could diverge only for a fall-back after local midnight, which no tested zone hits.

## Risks
- `zoneinfo` gap/fold semantics are subtle. Do not "fix" `local_midnight` with manual offset arithmetic. Rely on the
  round-trip described in the HLD and pin it with AC6.
- If `tzdata` is missing on a platform, `ZoneInfo` raises. That is E-Sc9Rt4's load-time validation. This module
  receives a constructed `ZoneInfo`.

## Dependencies
- None upstream. Downstream: `T-Uz0Uol`, `T-PpE5lf`, `T-rwj4nV`, `T-8knqVe`.

## Pseudocode / Algorithm
```text
See HLD §7 `window_for` / `local_midnight` (normative).
week offset = (d.weekday() - (0 if monday else 6)) mod 7
month next  = date(y + (m == 12), 1 if m == 12 else m + 1, 1)
```

## Schemas / Interface Notes
- Interface: `schedules.windows.{Window, Period, WeekStart, window_for, windows_for, local_midnight}`
- Spec / data schema: N/A (pure)
- Triggers / events: N/A
- Artifacts: none

## Handoff Boundary
- Upstream: HLD §7, ADR-0025 D2.
- Downstream: the ledger stores instants and calls `windows_for` at query time. Do not add caching here.

## Artifacts
- Docs/comments: `meta/tickets/E-Qp4Zm8-scheduled-run-governance/T-lJu9oZ-quota-window-math/`
- Large outputs: N/A
