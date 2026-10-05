# STATUS

- ID: `T-rpKCjP-auth-http-routes`
- Updated At: `2026-10-05`
- State: `Draft`
- Owner: `developer` (lane Q; v2.1, was lane A)
- Scope: `MVP` · Sprint: `S2` · Estimate: `3 d` (v2.1; was 2.5 d)

## This update
- By: architect · Role: agent · Date: 2026-10-05 · Comment: v2.1 gates folded (HLD §28.9).
  - **Estimate 2.5 → 3 d; lane A → Q** (HLD §24.2/§24.3 rebalance, design-review minor 3).
  - **Security M2:** `client_info(scope, runtime)` with the strict loopback rule (loopback peer +
    loopback `Host` + no forwarding headers), `proxy_suspected`, one WARNING per process, and the
    additive E1 field `transport.proxy_suspected` (manager-approved contract change). New AC 13 =
    AC-43 (`test_client_info.py`, the security review's test gate 3).
  - **Design-review M2:** list-valued `register_route_builder`; this task creates
    `auth/http/routes_second_factor.py` as a registered no-op stub that T-KQ6ZrY then owns; the hub
    routes go to T-KOv2qD's `hub_routes.py`; real-route enumeration in its own
    `test_route_enumeration_real_routes.py` (T-G7qByZ's files are no longer edited here).
  - **Design-review minor 6:** rule R1a + `test_routes_annotations.py`.
- By: architect · Role: agent · Date: 2026-10-05 · Comment: **v2 rescope.**
  - **Estimate and sprint:** 3 d / S2→S3 → 2.5 d / S2.
  - **Split** (developer D-4): the second-factor routes (E3–E7) and the provider-seam test moved to
    the new `T-KQ6ZrY-auth-routes-second-factor`. This task keeps the core routes (E1, E2, E8, E9,
    E10), the callable route-builder registry (reviewer R-5), `assert_flat_auth_routes` (developer
    D-1 BLOCKER: flat routes only), and the error handler with `cause_for_log` (reviewer R-10).
  - **New in v2:**
    - `session_proof` in the issuing bodies;
    - status and logout honour the proof (D25, dev-security #2);
    - `Clear-Site-Data` on logout (dev-security #7);
    - realm ids stable across ports (dev-critic C-2);
    - route-level revocation races (dev-security #1).
  - **Precisions, reported to the architect:**
    - E2 replaces the presented session only when `proof_ok`, so that a missing proof never
      destroys anything;
    - `read_json_object` gains an additive `optional_body` keyword for the optional E10 body.
- By: architect · Role: agent · Date: 2026-10-04 · Comment: Task created as part of the E-Da5Tn9
  design package (design and tickets only; **no code written**). It implements the frozen HLD §2
  contract exactly. It may start on a fake provider before T-XchniS merges.

## Evidence
- None yet.

## Risks / Blockers
- None. It runs on lane Q in parallel with T-QJ1vyQ (lane C) after T-G7qByZ. Its proof cases
  depend only on the `proof_ok` value that T-G7qByZ computes. It is on the critical path (HLD
  §24.3). OQ-8 and OQ-9 are DECIDED.

## Next actions
1. developer: implement after T-XchniS and T-G7qByZ, run the verification, and record the
   conformance-table results here.
