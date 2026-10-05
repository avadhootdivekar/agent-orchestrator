# TASK: T-l43hCg-dashboard-approvals-backend

## Metadata
- Task ID: `T-l43hCg-dashboard-approvals-backend`
- Epic ID: `E-Ag7Pw3-human-approval-gates`
- Owner: developer
- Created: 2026-10-04
- Last Updated: 2026-10-05
- Status: Draft
- Estimate: 2.5 days (rev 1: 3 d; Gate 1 removed the hash cache and semaphore and moved the view builders to
  `approvals/views.py`, and added the file-browser denial)

## Requirements Mapping
- Requirement IDs: FR-9, NFR-3, NFR-4, NFR-9
- Design: HLD §9.14 (all, incl. §9.14.3 mapping table and §9.14.6 file-browser denial), §9.8 (matrix),
  §9.12 (signer, views), §7.4 TM-3/9/13/14/15/16/21/22/25, §26 rows 29, 30, 36; Gate 1 R-02, R-05, R-08,
  S-06, CUT 2, suggestion S-06

## Description
- `ui/approvals_principal.py`: `PrincipalView`, `read_principal(request_state)` (defensive `getattr`,
  fail-closed on a malformed username/auth method, roles tolerated) — never imports an auth package.
- `ui/approvals_service.py` (no web-framework import): `ApprovalsService` as a thin adapter over
  `approvals.views` (`scan_pending`, `build_task_view` with `channel="dashboard"` and the principal's
  identity or `None`) and `approvals.signer` (`prepare_decision` + `commit_decision`), plus `artifact`
  (review-path allowlist + sandwich hash around `FileBrowser.read_file`). **No hash cache, no semaphore,
  no 429** (Gate 1 CUT 2).
- `ui/approvals_routes.py`: `register_approval_routes(app, service)` with the 5 routes and
  `DecisionBody`; errors as `{"detail": str, "reason": str, "detail_code": str | null, ...extra}` with the
  status from `REFUSAL_HTTP_STATUS` (never from message text).
- `ui/app.py`: import + `register_approval_routes(app, ApprovalsService(service.workspace_root))` right
  before `_mount_frontend(app)`.
- `ui/security.py`: `"X-Frame-Options": "DENY"` in `_STATIC_SECURITY_HEADERS`.
- `ui/files.py` (Gate 1 S-06, OQ-14): at the end of `FileBrowser.resolve`, refuse any resolved path inside
  `.orchestrator/runs/<run_id>/approvals/` with `PathNotAllowedError` (helper `_inside_run_approvals_dir`,
  HLD §9.14.6); one helper, ready to merge with the auth epic's store denial (CE-4).

Files — new: the 3 `ui/approvals_*.py` modules; `tests/ui/test_approvals_api.py` (own autouse fixtures:
key dir from `tmp_path_factory.mktemp("approval-keys") / "keys"` — outside the `tmp_path` workspace the
dashboard fixtures use (Gate 1 R-02) — `AO_IN_AGENT` removed, hermeticity assertion). Shared: `ui/app.py`,
`ui/security.py`, `ui/files.py` (§26 rows 29, 30, 36). `ui/service.py` is **not** edited.
**Exclusive files during stage D** (HLD §22.3): exactly these.

## Acceptance Criteria
1. The POST evaluation order and the refusal mapping table of HLD §9.14.3 are reproduced by one test per
   row (422 body, 403 `anonymous` — also with empty `approvers` and with no `Origin` header, 403
   `in_agent`, 404 `not_found` incl. an unreadable `state.json`, 409 `request_invalid`, 409
   `request_mismatch`, 409 `not_pending`, 409 `expired`, 403 `unauthorized`/`require_2fa`, 409
   `already_decided` (`test_already_decided_409`), 422 `review_set_mismatch`/`comment_required`/
   `comment_too_long`, 409 `review_unavailable`, 409 `stale_hashes` with `current`, 500 `store_error`,
   201 success); `test_refusal_mapping_table_complete` asserts every reason the dashboard can raise has a
   status; after a 201 the engine (in-process, fixture) applies the decision.
2. `read_principal`: absent attribute → None; `None` → None; non-str / pattern-violating username → None +
   `approval.principal_malformed`; unknown `auth_method` → None; malformed roles → `()` (decision still
   evaluated).
3. A test middleware sets `request.state.principal`; `test_fake_principal_middleware_sets_request_state`
   proves the value reaches the route when the middleware is added after `create_app` (outermost) and the
   `SecurityMiddleware` still runs.
4. CSRF/Origin: foreign `Origin` → 403; bodied POST with `text/plain` → 415; bad `Host` → 421.
5. `GET /` (built SPA present) and `/api/health` carry `X-Frame-Options: DENY`; the existing CSP test and
   `tests/ui/test_security.py` pass unedited.
6. Artifact endpoint: a path not in the request's review list → 404; a file changed between the two hashes
   (seam) → 409 `changed_during_read`; response = `FileContent` fields + `sha256`, `open_sha256`,
   `changed_since_request`.
7. Listing: only `running` runs by default, `include_stopped=true` adds halted ones, ≤ 200 runs / 500 rows
   (`truncated` flag); a decoy row injected into `status.json` cannot be decided (404/409 from the verified
   state).
8. File browser (S-06): `test_file_browser_refuses_approvals_tree` — listing
   `.orchestrator/runs/<id>/approvals`, reading `audit.jsonl` and a decision record, and a symlink elsewhere
   in the workspace pointing into the tree are all refused; `test_file_browser_still_serves_run_dir`
   (`state.json` and the run directory listing, which shows the `approvals` name, still work); the existing
   `tests/ui` file-browser tests pass unedited.
9. Layering: `ui/approvals_service.py` and `ui/approvals_principal.py` import no `fastapi`/`starlette` and
   no auth package (cases already present in `test_import_layering.py`, owned by `T-AGO2L6`; they stop
   skipping once these modules exist).
10. `test_fixture_hermeticity` (module copy): the key dir resolves outside `tmp_path`.
11. Full `tests/ui` suite passes unedited; ruff/mypy clean; `reviewer`-agent review recorded.

## Risks
- Merge with the auth epic in `create_app`/`security.py`/`files.py` (CE-2, CE-4).
- `FileBrowser.read_file` truncates at 1 MB; the response's `truncated` flag must reach the UI (AC6).
- Each task-view request re-hashes the review set (no cache): bounded by the per-request caps; the SPA polls
  only while the tab is active.

## Dependencies
- `T-pfJiXw` (signer, views); `T-1B8hu4` (`in_agent_context`).

## Pseudocode / Algorithm
```text
HLD §9.14.1 read_principal; §9.14.3 decision order + mapping; artifact: h1=hash; content=read_file; h2=hash;
h1!=h2 -> 409; §9.14.6 _inside_run_approvals_dir on the resolved path.
```

## Schemas / Interface Notes
- Routes, bodies and responses: HLD §9.14.2–§9.14.4.

## Verification
```
WT=/usr/avadhoot/mounted/agent-orchestrator/.claude/worktrees/agent-a513b4d78ac3b20bb
PY=/usr/avadhoot/mounted/agent-orchestrator/.venv/bin/python
cd $WT && $PY -m pytest -o pythonpath=src -p no:cacheprovider -q tests/ui tests/approvals/test_import_layering.py
cd $WT && $PY -m ruff check src/agent_orchestrator/ui tests/ui && $PY -m mypy src/agent_orchestrator/ui/approvals_principal.py src/agent_orchestrator/ui/approvals_service.py src/agent_orchestrator/ui/approvals_routes.py src/agent_orchestrator/ui/app.py src/agent_orchestrator/ui/security.py src/agent_orchestrator/ui/files.py
```

## Handoff Boundary
- Upstream: `T-pfJiXw`.
- Downstream: `T-pIZq3q` (frontend consumes the 5 routes).

## Artifacts
- Code as listed; evidence in STATUS.md.
