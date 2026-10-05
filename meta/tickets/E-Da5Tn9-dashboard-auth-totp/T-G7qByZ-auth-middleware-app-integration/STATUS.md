# STATUS

- ID: `T-G7qByZ-auth-middleware-app-integration`
- Updated At: `2026-10-05`
- State: `Draft`
- Owner: `developer` (lane C)
- Scope: `MVP` · Sprint: `S1→S2` · Estimate: `3 d`

## This update
- By: architect · Role: agent · Date: 2026-10-05 · Comment: **v2 rescope.**
  - **Lane and sprint:** lane B / S2 → lane C / S1→S2. The task builds against a duck-typed stub
    runtime in S1. Only the final wiring (about 0.5 d) waits for T-XchniS; this is the critical-path
    assumption in HLD §24.3.
  - **Classification** uses the per-app **policy table** instead of `@public_route` decorators.
    Auth routes are flat (developer D-1 BLOCKER: `include_router` hides route objects; reviewer R-7).
    `ui/app.py` gets no decorator edits.
  - **Split** (developer D-4): CSRF/Fetch Metadata, the body cap and proof enforcement moved to the
    new `T-QJ1vyQ-auth-csrf-fetch-metadata`. This task leaves marked hooks for them.
  - **New here:**
    - byte-exact `http/responses.py` shared with the routes (reviewer R-9);
    - duplicate-cookie rejection (dev-security #2);
    - tri-state revalidation, where UNAVAILABLE → 503 and the session is kept (developer D-7);
    - `root_path`-safe paths (dev-security #8);
    - `no-store` on every non-PUBLIC response (dev-security #7);
    - navigation sliding restricted to same-origin or typed navigations;
    - the Principal v2 shape (OQ-8).
  - **Precision, reported to the architect:** this task's step-5 hook *computes* `proof_ok` (which
    the E1/E10 routes need), and T-QJ1vyQ adds only the enforcement branch. This removes a hidden
    T-rpKCjP → T-QJ1vyQ dependency.
- By: architect · Role: agent · Date: 2026-10-04 · Comment: Task created as part of the E-Da5Tn9
  design package (design and tickets only; **no code written**). This task enforces deny by
  default. The exact allowlist is in HLD §13.2.

## Evidence
- None yet.

## Risks / Blockers
- None. OQ-8 (the `Principal.roles` tuple) must be confirmed before AC-6 freezes the field shape.

## Next actions
1. developer: start in S1 on the stub runtime. Do the final wiring after T-XchniS. Run the
   verification, including `tests/ui` unmodified, and record the results and the AC-14 benchmark
   here.
