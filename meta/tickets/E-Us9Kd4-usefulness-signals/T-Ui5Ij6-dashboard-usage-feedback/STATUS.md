# STATUS

- ID: `T-Ui5Ij6-dashboard-usage-feedback`
- Updated At: 2026-10-02
- State: Done
- Owner: dev-epic

## This update
- By: dev-epic | Role: manager | Date: 2026-10-02 | Comment: ticket opened, work delegated.

## Backend (By: developer | Role: developer | Date: 2026-10-02)
Done: `ui/service.py` (DashboardService.usage_report / get_feedback / add_run_feedback / run_signals + typed errors) and thin routes in `ui/app.py`. All validation/aggregation reuses `feedback.py`, `usage.py`, `implicit_signals.py`, `survival.py` (no duplicates). Tests: `tests/ui/test_feedback_api.py`, `tests/ui/test_usage_api.py`.
Sync `def` routes run in Starlette's threadpool (off the event loop). Survival bounds: at most 2 concurrent survival computations (else 429); `/api/usage?survival=true` with no run_id uses the newest 50 runs (`survival_runs_capped: true` when truncated); per-unit caps come from `survival.py`.

Error mapping: 400 validation (bad run id/ref/run_id count/semantic feedback errors), 404 unknown run/task, 409 entry cap, 422 bad body shape (pydantic), 429 survival busy, 500 corrupt feedback.json (file left untouched); middleware gives 403 cross-origin, 415 non-JSON body, 421 bad Host.

### Response shapes
- `GET /api/usage?run_id=R&run_id=R2&survival=false&ref=` -> `usage_report_payload(...)` (same as `ao report-usage --json`: `groups[]` with computed `mean_cost_usd/retry_rate/review_fail_rate/survival_rate/fb_bad_rate/reviewer_disagreement_rate`, `outcomes`, `skipped[]`, `feedback_errors`, `runs_rated`, `survival_available`, `survival_unavailable_reason`, `survival_ref`, `run_signals[]`) plus `survival_runs_capped: bool`. `ref` requires `survival=true`.
- `GET /api/runs/{id}/feedback` -> `{run_id, entries[], effective[], tasks: {task_id: entry|null}}`; entry = `{ts, scope: run|task, task_id|null, rating: good|ok|bad, reasons[], note|null, source: cli|dashboard}`; `tasks[t]` = task entry else run entry else null.
- `POST /api/runs/{id}/feedback` body `{scope, task_id?, rating, reasons: [], note?}` (extra fields rejected; `source` forced `dashboard`) -> 201 `{entry, feedback: <same shape as GET>}`.
- `GET /api/runs/{id}/signals?survival=false&ref=` -> `{run_id, signals: RunSignals{landed, landed_reason, followup_commits, followup_confidence, followup_reason, reverted_commits, run_status, killed, tripped_breakers, breaker_pauses, breaker_kills}, survival: {requested, available, reason|null, ref|null, total: TaskSurvival|null, tasks: TaskSurvival[]}}`.

## Frontend
- By: developer (frontend agent) | Role: developer | Date: 2026-10-02 | Comment: Frontend half implemented in `ui/src`: new Usage nav view (`components/Usage.tsx`: group table, run filter, survival toggle, coverage line, outcomes, flags as text), RunDetail run-level rating + per-task rate disclosure (`FeedbackControls.tsx`, `FeedbackPanels.tsx`), feedback history (text-only notes), implicit-signals panel with on-demand survival. Types follow the landed backend (`/signals` -> `{run_id, signals, survival:{requested,available,reason,ref,total,tasks}}`). vitest 101/101 pass (new: usage.test.tsx, feedback.test.tsx), `make ui-typecheck` clean, `make ui-build` regenerated `src/agent_orchestrator/ui/static` (index-BeKx9SWQ.js replaced by index-Cjj4p2eT.js; CSS unchanged). Smoke on a fabricated workspace with headless Chrome driven over CDP: Usage table, survival toggle, run detail, feedback history (HTML/script note rendered as plain text) and survival-unavailable state all rendered.

## Security hardening (By: developer | Role: developer | Date: 2026-10-02)
Security-review fixes, tests in `tests/test_hardening_usage_signals.py`:
- `survival.py`: git stdout capped at `MAX_DIFF_BYTES` (16 MiB; default `_BoundedRunner` streams and kills git, injected runners are length-checked) -> unit reported `truncated: bytes>N`, a too-large blob skips that file. `compute_survival(clock=, budget_s=SURVIVAL_BUDGET_S=20)`: past the deadline git calls stop, the report is `unavailable` ("time budget exceeded"), never raises; dashboard/usage paths inherit it. `git.py` unchanged.
- shas from state.json (`landed_ranges`/`start_heads`/`end_heads`/`squash_commits`/integration heads) must fullmatch `[0-9a-f]{7,64}` (`is_valid_sha`) before reaching git argv in `survival.py` and `implicit_signals.py`; invalid => unit unavailable + low confidence. `--end-of-options` added to rev-list/log calls.
- `feedback.py`: `RUN_ID_RE` used with `fullmatch` (`r1\n` rejected); `_atomic_write` uses a unique tmp name opened `O_CREAT|O_EXCL|O_NOFOLLOW`, cleans up on failure; symlinked `feedback.json` refused (lstat) with a message that omits absolute paths.
- `ui/app.py`: `DashboardStoreError` -> 500 with generic detail; full text logged at ERROR.
