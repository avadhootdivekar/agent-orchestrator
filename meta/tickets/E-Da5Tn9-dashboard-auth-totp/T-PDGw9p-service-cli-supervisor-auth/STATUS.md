# STATUS

- ID: `T-PDGw9p-service-cli-supervisor-auth`
- Updated At: `2026-10-05`
- State: `Draft`
- Owner: `developer` (lane A; v2.1, was lane Q)
- Scope: `MVP` · Sprint: `S3` · Estimate: `2.5 d`

## This update
- By: architect · Role: agent · Date: 2026-10-05 · Comment: v2.1 gates folded (HLD §28.9).
  Estimate and sprint unchanged (2.5 d, S3); **lane Q → A** (HLD §24.3 rebalance, design-review
  minor 3: lane Q's S3 now holds only T-U2ERMo). New AC 1b: `ao service run --auth` with port 0 →
  exit 78 before anything is spawned (security L4, implemented in `prepare_auth`). Otherwise the
  task consumes the v2.1 `prepare_auth` unchanged; the workspace-config risks (security M3) never
  apply to the hub.
- By: architect · Role: agent · Date: 2026-10-05 · Comment: Task created in the v2 re-baseline
  (design and tickets only; **no code written**). It takes over the service-side half of v1
  `T-KOv2qD-hub-service-auth`, because the v1 task exceeded the 3-day cap (developer D-4).
  - **`ao service run`** calls the shared `prepare_auth` **before** constructing the `Supervisor`
    (developer D-9 / reviewer R-3).
  - **Probe:** a 401 from the hub becomes `HubLoginRequired`, and `list`/`status` fall back to the
    persisted state.
  - **Supervisor:** `child_env` is CLI-sourced only, and a child exiting `EXIT_CONFIG` is terminal.
    `EXIT_CONFIG` lives in root `errors.py` (reviewer R-10).
  - **systemd:** the unit gains `RestartPreventExitStatus` rendered from `EXIT_CONFIG`.

## Evidence
- None yet.

## Risks / Blockers
- None. Implement against `launch.child_env` / `launch.uvicorn_kwargs` (HLD §11.20; §16 row 6 uses
  these names). On the critical path (HLD §24.3).

## Next actions
1. developer: implement once T-jVqH8w (`prepare_auth`) and T-KOv2qD (`build_hub_app(auth=)`) land.
   Run the verification and record the results here.
