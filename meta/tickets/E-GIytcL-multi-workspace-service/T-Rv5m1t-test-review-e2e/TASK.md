# TASK: T-Rv5m1t-test-review-e2e

## Metadata
- Task ID: `T-Rv5m1t-test-review-e2e`
- Epic ID: `E-GIytcL-multi-workspace-service`
- Owner: tester + reviewer agents
- Created: 2026-08-28
- Last Updated: 2026-08-28
- Status: Done
- Estimate: < 1 day

## Requirements Mapping
- Requirement IDs: all FR/NFR (see `../EPIC.md`) — cross-cutting verification, not new
  functional scope.

## Description
Late-gate verification + final review pass. Does not implement new features; may add
missing tests or fix small gaps a `developer` handoff explicitly allows, and syncs docs.

## Acceptance Criteria
1. **Baseline vs after**: `uv run pytest -q` run once before any epic code landed
   (baseline count already captured by the dev-epic orchestrator — reuse it, do not
   re-derive by reverting code) and once after all three prior tasks land; report exact
   pass/fail/skip counts for both, and the delta attributable to `tests/service/`.
2. **Full suite green**: `uv run pytest -q` — zero new failures/errors vs. baseline
   anywhere in the repo (not just `tests/service/`) — a change to `project_config.py` or
   `cli.py` regressing an unrelated existing test is exactly the kind of regression this
   step exists to catch.
3. **Lint/format/types**: `uv run ruff check .`, `uv run ruff format --check .`,
   `uv run mypy src` all clean (zero new findings vs. baseline; report the actual
   before/after counts, do not just assert "clean").
4. **Late-gate e2e**: exercise the real end-to-end path, not just unit/integration
   tests — at minimum: `ao service add <tmp-workspace-1>`, `ao service add
   <tmp-workspace-2>`, `ao service run` started as a real background process (short-lived,
   `AO_SERVICE_CONFIG`/`AO_SERVICE_STATE_DIR` pointed at a temp dir, `[ui]` extra
   present in this env) — confirm via the hub's `/api/service/status` (or `ao service
   status`) that both children are reported serving on distinct resolved ports, confirm
   each workspace's dashboard actually answers on its port (a plain HTTP GET), then send
   the supervisor SIGTERM and confirm (a) both children stop within the grace period and
   (b) a decoy `start_new_session=True` process is still alive afterward. This is a real
   subprocess exercise, not mocked — evidence (commands run + captured output) goes in
   this task's `STATUS.md`/`HANDOFF.md`, and any script used lives under
   `scripts/helper/epics/E-GIytcL-multi-workspace-service/` per repo convention (not an
   inline multi-hundred-line shell command).
5. **Traceability re-check**: walk every MVP requirement in `../EPIC.md` and confirm it
   maps to at least one task/test that actually landed; flag (do not silently drop) any
   requirement that shipped differently than planned, with the reason.
6. **Reviewer pass**: request a `reviewer` pass across the full diff (all three prior
   tasks) against CLAUDE.md principles (pluggable/injectable design, no magic literals,
   correct error handling/logging, resume/concurrency safety) and this epic's own locked
   decisions (ADR-0012 D1-D4) — specifically confirm `KillMode=process` is present,
   confirm the `cli.py` diff is the minimal one-line registration described in
   `T-Hb3x7q`'s ticket, and confirm no file outside `service/`, `project_config.py`,
   `cli.py` (minimal edit), `tests/`, `docs-md/` was touched.
7. **Docs sync**: `meta/ROADMAP.md` gets a row/update reflecting this epic shipped
   (§1 history table entry; §3.1/§3.6 references updated if this changes their framing —
   e.g. "per-workspace systemd units" is no longer a gap once this ships). HLD/ADR status
   headers flipped from Draft/proposed to Accepted/shipped once verified. Epic
   `STATUS.md`/`EPIC.md` and this task's `STATUS.md` updated with final evidence.

## Acceptance Criteria — early-gate follow-up (2026-08-28, see ADR-0012 "Early-gate corrections")
8. Confirm every early-gate correction actually landed and is covered by a passing,
   independently-identifiable test (not folded silently into an unrelated test): singleton
   lock, registry lost-update prevention, boot-resume immediate-before-spawn idempotency
   re-check, orphan reclamation, EADDRINUSE backstop, hub `SecurityMiddleware` actually
   mounted (421 on bad Host), tier-first port-conflict tie-break, `status`/`list` reading
   `hub_port` from persisted state. List each by test name in your handoff — a missing one
   is a blocker for closing this epic, not a note for later.
9. **Attempt** the one recorded verification gap from ADR-0012's early-gate corrections:
   whether a real `systemctl --user` unit with `KillMode=process` actually leaves a
   detached grandchild process alone on `stop`, in this execution environment. First check
   feasibility (`systemctl --user status` / `loginctl` availability, a working user D-Bus
   session) — if a systemd user session is NOT available in this sandbox (common in
   containers), record that explicitly as "attempted, environment does not support a
   systemd user session" and rely on the two independent early-gate reviews' agreement on
   the underlying mechanism (already recorded in the ADR) rather than inventing a
   workaround. Do not skip the feasibility check silently.

## Risks
- Do not mark this task (or the epic) Done on unverified claims — every acceptance
  criterion above requires an actual command's output, not an agent's summary of what it
  intended to run.

## Dependencies
- `T-Gr8s2a-registry-ports-config`, `T-Sv9d4k-supervisor-boot-resume`,
  `T-Hb3x7q-hub-systemd-cli` all landed.

## Schemas / Interface Notes
- N/A (verification task).

## Handoff Boundary
- Upstream: all three implementation tasks.
- Downstream: epic completion handoff (dev-epic's final response to the user).

## Artifacts
- Docs/comments: `meta/tickets/E-GIytcL-multi-workspace-service/T-Rv5m1t-test-review-e2e/`
- Large outputs: `output/E-GIytcL-multi-workspace-service/` (test run logs, e2e transcript)

## Closing comment

By: Claude (dev-epic)
Role: manager
Date: 2026-08-28
Comment: Late-gate evidence (Parts A-C) captured by the tester agent — real `ao service run`
background-process exercise (two workspaces, distinct resolved ports, SIGTERM-vs-decoy
survival proven via `kill -0`) and a real, non-persistent `systemd-run --user` transient-unit
verification of `KillMode=process` (grandchild survived `systemctl --user stop`), closing
ADR-0012's one recorded verification gap with actual evidence. A concurrently-dispatched
final `reviewer` pass raised one "blocking" finding claiming this task never ran — traced to
a race condition (the reviewer read this ticket's STATUS.md before the tester, running in
parallel, finished writing it) and stood down after independently re-reading the file's
final content and independently sweeping the machine for the tester's real process/systemd
artifacts (found and cleaned up one incidental orphaned process from the systemd test itself
— further, unplanned corroboration that the real exercise happened). The reviewer's four
other findings were legitimate and are fixed (see epic STATUS.md addendum). Traceability
re-check, ROADMAP sync, and HLD/ADR status flips completed directly by this agent. Marking
Done.
