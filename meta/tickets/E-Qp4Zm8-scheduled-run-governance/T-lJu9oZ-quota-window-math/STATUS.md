# STATUS

- ID: `T-lJu9oZ-quota-window-math`
- Updated At: 2026-11-11
- State: Done (implemented, committed on ad/enhancements-10nov; pending review/merge)
- Owner: TBD (assigned at sprint start)

## This update
- By: developer agent (run unit `w07-01-a2-quota-window-math`) · Role: developer · Date: 2026-11-11
- Implemented `src/agent_orchestrator/schedules/windows.py` (`Window`, `Period`, `WeekStart`, `window_for`, `windows_for`,
  `local_midnight`, key formatters `day_key/week_key/month_key` + `*_KEY_FORMAT` constants) and
  `tests/schedules/test_windows.py`. `schedules/` already existed (E-Sc9Rt4 T-Sd1Kq7); no other schedules module touched.
- Label is authoritative (AC11) documented in the module docstring, incl. the fall-back-after-midnight caveat.

## Evidence
- `pytest tests/schedules/test_windows.py`: 70 passed; whole `tests/schedules`: 178 passed. Coverage on windows.py: 100 %.
- `ruff check` + `ruff format --check` clean; `mypy` clean on both files.
- Corpus: Berlin/New_York spring (23 h) and fall (25 h) days incl. fold 0/1, Havana 2024-03-10 (05:00Z), Beirut 2024-03-31,
  Santiago 2024-04-07, Lord_Howe month (Apr 2024 / Oct 2024), week_start monday/sunday on Sun/Mon/Sat, Dec->Jan,
  2028-02, 31-day month, containment/adjacency sweep over 7 zones x 3 periods x 2 week starts, `datetime.now` patched to raise.

## Risks / Blockers
- None.

## Next actions
1. Review/merge. 2. Hand `Window`/`windows_for` to `T-Uz0Uol` and `T-PpE5lf`.

## Review follow-up (w08-02 Lows, applied in w09-02)
- L1 done: docstring names Moncton 1993 / St_Johns 1987 / Casey 2010; `test_label_wins_when_fall_back_crosses_midnight` pins it.
- L2 done (docs only): HLD s7 reworded to "7 consecutive local dates, fewer if a date is skipped (Apia 2011)". The AC7 sentence in TASK.md is left unchanged (out of this unit's touch list).
- L3 done: keys built from `date.isoformat()`; test pins `day:0999-01-02`. `*_KEY_FORMAT` constants removed (no other users).
- L4 done: `test_no_clock_reads` also patches `date.today`; added Sunday-week DST test (167 h). Sweep bases for Santiago spring/Lord Howe Oct not added (optional, rejected: scan showed both correct).
- L5/L6 not in scope (L5 belongs to gate/ledger tickets; L6 nit left).
- Evidence: 72 passed, windows.py 100 % coverage, ruff/format/mypy clean.
