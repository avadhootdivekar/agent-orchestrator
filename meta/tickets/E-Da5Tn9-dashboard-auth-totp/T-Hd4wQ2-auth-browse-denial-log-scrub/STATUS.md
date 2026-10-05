# STATUS

- ID: `T-Hd4wQ2-auth-browse-denial-log-scrub`
- Updated At: `2026-10-05`
- State: `Done`
- Owner: `developer` (lane Q)
- Scope: `MVP` · Sprint: `S1` · Estimate: `2 d`

## This update
- By: architect · Role: agent · Date: 2026-10-05 · Comment: Task created in the v2.1 re-baseline
  (HLD §24, §28.9). It takes the file-browser denial (from T-jVqH8w), the `paths.py` denial helpers
  (from T-8NQP8J) and `scrub.py` (from T-CsT5gk), so those three tasks stay within 3 days after
  the gate fixes, and so the generic `denied_paths` mechanism lands in S1 ahead of the
  approval-gates epic (cross-epic row X2, design-review M1). New in v2.1: `~/.config/ao/service.env`
  is denied too (security L7). Design and tickets only; **no code written**.

- By: developer · Role: developer · Date: 2026-10-05 · Comment: Implemented items 1-4 end to end.
  `auth/paths.py` gains `default_denied_paths` / `entry_is_denied` (plus a private `_contains`
  shared with `is_within`, so regular entries cost no `Path.resolve`); `ui/files.py` gains
  `denied_paths`, the one `_is_denied` helper (called once in `resolve`) and denial-aware
  `list_dir`; `ui/service.py` gains `denied_paths=None` (defaults to `default_denied_paths()`);
  new `auth/scrub.py`. `service.env` (security L7) is denied under `~/.config` and
  `$XDG_CONFIG_HOME`. `test_files.py` / `test_service.py` untouched. AC 4 "auth-on" variant uses a
  service constructed with the explicit `denied_paths` that `ao ui` passes (the stub-runtime
  variant is left to T-G7qByZ/T-jVqH8w, which own app wiring; denial lives in the service layer so
  the app mode is irrelevant).

## Evidence
- `.venv/bin/python -m pytest -q tests/ui` -> 629 passed, 2 skipped (includes the 36 new denial tests;
  `tests/ui/test_files.py`, `test_service.py` unmodified).
- `.venv/bin/python -m pytest -q tests/auth/test_paths_denial.py tests/auth/test_scrub.py
  tests/auth/test_paths.py tests/auth/test_import_boundary.py tests/ui/test_file_browser_denial.py
  --cov=agent_orchestrator.auth.scrub --cov=agent_orchestrator.auth.paths` -> 133 passed;
  `paths.py` 100 %, `scrub.py` 100 %.
- `.venv/bin/ruff check` + `ruff format --check` on the 4 src and 3 test files: clean.
- `.venv/bin/mypy src`: only the 4 pre-existing `_version.py` errors.
- NFR-1: auth-off behaviour is unchanged except the deliberate store/state/`service.env` denial
  (`TestOneDenyHelper.test_no_denied_paths_means_unchanged_behaviour`, plus the unmodified suites).

## Risks / Blockers
- None (not blocked). The cross-epic merge rule (X2: one deny helper, this epic lands first) is for the manager to
  relay to the approval-gates epic.

## Next actions
1. T-jVqH8w: pass `AuthLaunch.denied_paths` to `DashboardService(denied_paths=...)`;
   T-U2ERMo / `prepare_auth`: call `scrub.install_log_redaction()` when auth is on.
2. Approvals epic: add its predicate inside `FileBrowser._is_denied` (cross-epic row X2).
