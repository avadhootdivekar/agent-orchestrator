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
