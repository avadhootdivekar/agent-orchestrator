# STATUS

- ID: `T-LYf6DJ-activity-endpoint`
- Updated At: 2026-10-02
- State: Done
- Owner: dev-epic

## This update
- By: dev-epic · Role: manager · Date: 2026-10-02 · Comment: Done.

## Evidence
- GET /api/runs/{id}/activity (ui/app.py, service.py, runs.py load_activity); TaskStat.agent/model/effort; RunSummary.running_tasks (cap 20); tests/ui/test_activity.py TestActivityEndpoint (404 for unknown/traversal), tests/ui/test_activity_e2e.py (real `ao ui` subprocess over HTTP, growing transcript); tests/ui/test_run_graph_endpoint.py pinned-key set updated.

## Next actions
1. None (Phase 1 shipped).
