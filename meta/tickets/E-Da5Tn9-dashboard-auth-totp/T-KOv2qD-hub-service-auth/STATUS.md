# STATUS

- ID: `T-KOv2qD-hub-service-auth`
- Updated At: `2026-10-05`
- State: `Draft`
- Owner: `developer` (lane A)
- Scope: `MVP` · Sprint: `S3` · Estimate: `2.5 d`

## This update
- By: architect · Role: agent · Date: 2026-10-05 · Comment: TASK.md aligned with the HLD v2 cross-check (HLD §28.8): hub enumeration now uses the HLD §13.2 `totp=None` configuration rule instead of `xfail`; T-KQ6ZrY owns the full-configuration check.
- By: architect · Role: agent · Date: 2026-10-05 · Comment: v2 rescope after the Phase-4
  consultations (HLD §28).
  - **Narrowed to the hub app** (estimate 3 d → 2.5 d). The `ao service run` / probe / list /
    status / install hint, supervisor `child_env` + exit-78 handling and systemd edits moved to the
    new `T-PDGw9p-service-cli-supervisor-auth` (developer D-4: the v1 task exceeded the 3-day cap).
  - **`hub_page.py`** now lives in `auth/http/` (layering R4).
  - **`register_hub_auth_routes`** also registers the auth-on `GET /`, so `hub.py` keeps only lazy
    imports and avoids the `Request` annotation trap (developer D-2).
  - **Policies** come from `HUB_ROUTE_POLICIES` instead of decorators (reviewer R-7).
  - **The login page** gains the enrollment-token form (dev-security #5).
  - **`pyproject.toml` artifacts** are added unconditionally (reviewer R-12).
  - **Wheel listing:** agents must not run `uv`, so the implementer or CI produces it.
- By: architect · Role: agent · Date: 2026-10-04 · Comment: Task created as part of the
  E-Da5Tn9 design package (design and tickets only; **no code written**). It originally covered the
  hub, the `ao service` CLI, the supervisor and systemd edits (HLD §16 #5–#8); v2 keeps #5 and #16.

## Evidence
- None yet. Record the wheel listing for `auth/assets/hub-auth.{js,css}` here.

## Risks / Blockers
- None. Merge-order note: if T-KQ6ZrY lands later, the hub enumeration's three second-factor rows
  stay `xfail(strict=True)` until it does (AC-7).

## Next actions
1. developer: implement once T-rpKCjP and T-R7JhTL land. Capture the auth-off golden HTML
   **before** editing `hub.py`, then run the verification.
