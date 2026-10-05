# STATUS

- ID: `E-Da5Tn9-dashboard-auth-totp`
- Updated At: `2026-10-05`
- State: `Draft` (design v2 complete; implementation not started)
- Owner: `manager`

## This update
- By: manager · Role: agent · Date: 2026-10-05 · Comment: Design v2 accepted for the independent gates. Decisions on the architect's open questions (owner-delegate):
  - **OQ-8 (Principal contract): `roles` stays `list[str]`**, exactly as the owner's brief and the approval-gates epic expect (a fresh empty list per Principal, so `principal.roles == []` holds; dataclass hash must exclude it, e.g. `field(hash=False)`). The additive fields (`user_id`, `realm`, `session_id`, `amr`, `auth_time`, `provider`) are accepted; `amr` may remain a tuple. HLD §2.6, ADR-0021 D6, T-kwwJ82 and AC-11 are to be amended accordingly.
  - **OQ-9: the D25 session proof stays in the MVP** (it is the first item to cut if the schedule slips: HLD §24.1).
  - **OQ-1:** no cross-realm SSO/handoff in this epic (first follow-up). **OQ-2:** idle 30 min / absolute 12 h. **OQ-3..7, OQ-10:** architect defaults accepted.
  - The independent design review (reviewer) and security review (dev-security) were launched; their findings are folded into HLD §28 by the architect before any code is written.
- By: architect · Role: agent · Date: 2026-10-05 · Comment: Ticket-authoring cross-check
  complete. All 22 tickets are written to v2 scope. Every HLD inconsistency the ticket authors
  reported is fixed in the HLD (§28.8), and the affected TASK/STATUS files are aligned. Results:
  - `check_tickets` consistency pass: 22 tasks, 55.0 dev-days, IDs valid, `Role: agent` everywhere;
  - one new non-blocking open question, OQ-10 (OIDC follow-up seams).
- By: architect · Role: agent · Date: 2026-10-05 · Comment: Design package **v2** published:
  - HLD + LLD in `docs-md/dashboard-auth-hld.md` (§0–§28, all required sections present);
  - `docs-md/adr/ADR-0021-dashboard-authentication-and-totp.md` v2 (D1–D11, Proposed);
  - 22 task folders, each with `TASK.md` and `STATUS.md`, all in state `Draft` (5 new in v2).

  v2 folds in every Phase-4 consultation; dispositions are in HLD §28. No BLOCKER, MAJOR or HIGH
  finding remains open. The HTTP contract (HLD §2, now including `session_proof` and
  `X-AO-Session-Proof`) and the Python identity contract (HLD §2.6 v2) are **frozen for parallel
  work**, subject to OQ-8 and OQ-9 below. Changes need a manager-approved entry here.
- By: architect · Role: agent · Date: 2026-10-04 · Comment: Design package v1 created (17 tasks),
  superseded by v2.

## Rollup

| Task | State | Sprint | Notes |
|---|---|---|---|
| T-kzEzwy-auth-foundation | Draft | S1 | **new (v2)**; owns the root hermetic test fixture; start first |
| T-s6sJmB-auth-crypto-primitives | Draft | S1 | — |
| T-8NQP8J-auth-user-store | Draft | S1 | v2 adds `fsutil.py`, `user_id`, CAS, enrollment tokens |
| T-kwwJ82-auth-sessions-policy-principal | Draft | S1 | Waits on OQ-8 before freezing `principal.py` |
| T-PlEROT-auth-settings-layering | Draft | S1 | v2: tighten-only; hermetic fixture moved to T-kzEzwy |
| T-CsT5gk-auth-throttle-audit-scrub | Draft | S1 | v2 adds `lockouts.py` (state dir) |
| T-XchniS-auth-local-provider-runtime | Draft | S2 | v2: login side + guard + runtime |
| T-yfrfxv-auth-provider-second-factor | Draft | S2 | **new (v2)**, split from T-XchniS |
| T-G7qByZ-auth-middleware-app-integration | Draft | S1→S2 | Start in S1 on a stub runtime (critical path) |
| T-QJ1vyQ-auth-csrf-fetch-metadata | Draft | S2 | **new (v2)**, split from T-G7qByZ; owns the proof check (OQ-9) |
| T-rpKCjP-auth-http-routes | Draft | S2 | v2: core routes only |
| T-KQ6ZrY-auth-routes-second-factor | Draft | S3 | **new (v2)**, split from T-rpKCjP |
| T-j9dfsw-ao-auth-cli | Draft | S2 | v2 adds `enrollment-token` |
| T-jVqH8w-ao-ui-auth-wiring | Draft | S3 | v2 owns `launch.py` (`prepare_auth`) |
| T-KOv2qD-hub-service-auth | Draft | S3 | v2: hub app only |
| T-PDGw9p-service-cli-supervisor-auth | Draft | S3 | **new (v2)**, split from T-KOv2qD |
| T-R7JhTL-hub-login-page | Draft | S1 | Frontend lane, parallel |
| T-pQ73eO-spa-auth-gate-login | Draft | S1 | Frontend lane, parallel |
| T-vCgsU6-spa-enroll-account-qr | Draft | S2 | Frontend lane |
| T-U2ERMo-auth-e2e-regression-sweep | Draft | S3 | — |
| T-2wE08U-auth-security-review | Draft | S3 | — |
| T-otjIkJ-auth-docs-refresh-closure | Draft | S3 | Post-implementation docs refresh |

Counts: 22 tasks · 0 Done · 0 In Progress · 0 Blocked · 22 Draft.

## Evidence
- Empirical design evidence (HLD §25.4):
  - scrypt `maxmem` and timings;
  - RFC vectors;
  - Chrome 138 behaviour for Origin, Fetch Metadata and cross-port cookies;
  - uvicorn proxy-header defaults;
  - Starlette state and middleware order;
  - the FastAPI `include_router` opacity that the developer consultation reproduced.
- Consultation record: HLD §28 (developer, reviewer, tester, dev-security, dev-critic).

## Risks / Blockers
- **No blockers.** Owner or manager decisions with non-blocking defaults:
  - **OQ-8:** confirm the `Principal` v2 shape (`roles: tuple[str, ...]` plus additive fields) with
    the approval-gates epic **before T-kwwJ82 freezes `principal.py`**.
  - **OQ-9:** ship the D25 session proof in the MVP. The default is yes. Deferring it saves about
    1.5 dev-days, and the README must then state the cookie-replay residual.
  - **OQ-1:** handoff vs SSO timing. Default: hub-run handoff as the first follow-up.
  - **OQ-2:** 30-minute idle timeout with non-sliding polling. Default: keep.
  - **OQ-10:** provider-contributed PUBLIC policies and a redirect-flow proof handoff. Not in the
    MVP; it is the OIDC follow-up's first task.
- Coordinate merge order with the sibling epics that touch `ui/app.py`, `cli.py` and `service/*`
  (R13).

## Next actions
1. manager: run the independent design review and security review on HLD v2 and ADR-0021 v2. Record
   their findings in HLD §28 (or a STATUS comment) and any contract changes here.
2. manager or owner: decide OQ-8 (with the approvals epic) and OQ-9.
3. manager: start Sprint 1:
   - lane B: #0 T-kzEzwy first;
   - then lanes A, B, C and Q: #1–#5, and #8 on a stub runtime;
   - lane F in parallel: #16 and #17.
