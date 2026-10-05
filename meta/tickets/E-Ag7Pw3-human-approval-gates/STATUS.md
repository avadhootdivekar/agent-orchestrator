# STATUS

- ID: `E-Ag7Pw3-human-approval-gates`
- Updated At: 2026-10-05
- State: **Draft rev 2** — design package revised after the early design Gate 1 (dev-security FAIL,
  reviewer PASS-WITH-CHANGES). HLD + LLD rev 2, ADR-0020 rev 2 (D1–D13), 15 task tickets. Awaiting the
  re-gate (reviewer + dev-security) and the parent session's confirmation of OQ-1, OQ-2 (modified:
  gate-scoped policy instead of whole-DAG freeze), OQ-12 (6-key denylist), OQ-13 (gate evidence), OQ-14
  (file-browser denial), OQ-15 (traceback locals) and CE-1 (the result cache never settles a gate).
  0/14 implementation tasks started.
- Owner: architect → manager

## This update
- By: architect · Role: agent · Date: 2026-10-05 · Comment: Gate 1 revision applied (rev 2). Every
  finding of `GATE1-FINDINGS-AND-DECISIONS.md` is mapped to where it is resolved in the HLD's new **§0.5
  Gate 1 changelog** (ACCEPT/MODIFY applied, the one REJECT — the reviewer's attribution note — recorded
  with its reason).
  - Resume integrity rebuilt (dev-security S-01/S-02/S-03, reviewer R-03/R-04): resume detected from
    `run_state is not None`, never from `spec_sessions`; in-file data can only add checks; gate-scoped
    signed policy (per gate: security digest incl. message hash + static forward closure) replaces the
    whole-DAG freeze; a missing policy with gate evidence fails closed, "adding a gate is allowed, removing
    one is not"; succeeded/skipped/not_taken gates **and not_taken gate ancestors** re-derived or reset;
    loop clones re-derived with the engine's `_clone_body` and clone gates always use the static base spec.
  - Hardening: bounded strict parser (S-04); no traceback locals + `repr=False` key (S-05); file-browser
    denial of `approvals/` (S-06); W-AG-8 + authoring rule for unreviewed downstream files (S-07); `pwd`
    home + 6-key config-env denylist (S-08); per-poll cross-gate re-hash budget (S-09);
    `GitRepo.version`/`probe` marked (S-10).
  - Scheduling and structure: `_open_ready_gates` pre-pass so gates never need a slot (R-01); hermetic
    test key dir outside every workspace (R-02); shared `approvals/views.py` (R-05); `T-csusci` split into
    `T-drPIif-canonical-keys-audit` (2.5 d) and `T-1MgGb4-hashing-records-authz-policy` (2.5 d) (R-06);
    approval-wait stat with the wall-time end marker and union semantics (R-07); closed refusal codes + one
    mapping table (R-08); suggestions S-01..S-11 (S-08 modified as T-15); CUT 2/3/4/5; parallelisation map
    with exclusive files per ticket.
  - Claims corrected: HLD header, §7.3 (A1 row), §7.4 preamble and TM-5/7/21, RR-5, and the ADR threat
    table no longer claim "stopped deterministically" without the residuals. New residuals RR-12 (route
    verdicts), RR-13 (emitted-task rewiring), RR-14 (unreviewed downstream files), RR-15 (metadata in run
    files); new attack paths TM-29..TM-34; closed-at-Gate-1 table.
  - New concerns found while applying (HLD §0.5): NC-1 gate ancestors, NC-2 metadata in `state.json`/
    `run.log` remains exposed through an unauthenticated dashboard, NC-3 copied-request binding, NC-4 the
    interim resume path must fail closed, NC-5 one `lstat` for gate-free runs, NC-6 parallel waits need a
    union, NC-7 gated loop bodies cannot change while iterations exist.
  - Plan: 33 dev-days (S1 18.5 / S2 14.5), critical path 18 days (chain 17.5), every task ≤ 3 days;
    `T-pfJiXw` renamed to `T-pfJiXw-decision-store-signer-views`; estimates changed for T-AGO2L6 (3),
    T-1B8hu4 (1.5), T-nmL0HP (2.5), T-l43hCg (2.5), T-ZPGoSN (1.5); the design task is now 3 d.
- By: architect · Role: agent · Date: 2026-10-04 · Comment: Design package created.
  - `docs-md/human-approval-gates-hld.md` — sections 0–26: resolved design questions Q-A..Q-R (§0.3),
    tensions found in the brief (§0.4), threat model with 28 attack paths, control-strength ladder and
    residual risks (§7), architecture and dependency rules (§8), per-module LLD M1–M15 with pseudocode,
    contracts, schemas and edge cases (§9), sequence diagrams (§12), event catalog (§15), rollout and
    cross-epic requirements (§16), test plan mapped to the brief's required tests (§18), acceptance matrix
    (§19), sprint plan with capacity math (§22), shared-file touchpoint inventory (§26).
  - `docs-md/adr/ADR-0020-human-approval-gates-signed-decisions.md` — D1–D11, threat-model summary,
    residual risk.
  - Tickets: this epic + 14 task folders (`TASK.md` + `STATUS.md`).
  - Phase-4 consultations (HLD §23.4): `developer` — feasible, no BLOCKER; one MAJOR (loop-clone gates
    lose inferred ordering → new fatal rule AG-4) and MINORs folded in (string sentinel `agent: str = ""`
    instead of `str | None`; isolation warnings skipped for gates; `_wait_or_settle_gates` so idle DRAIN
    ticks do not re-prepare budget-blocked tasks; lazy poll-interval resolution for NFR-1; `current_task`;
    `WorkspaceRunLock` documented; orphan handling; resume detection via `spec_sessions` — reversed at
    Gate 1) plus a **new threat TM-28** (agent-writable `.ao/config.yaml` `env:` could redirect the key dir)
    closed by a config-env denylist in `apply_project_config_env` (3 keys in rev 1, 6 since Gate 1).
    `tester` — workable; determinism wording, explicit gate-free `status.json` test, hermeticity
    self-check, middleware ordering (verified empirically), child-path check folded in. `reviewer` and
    `dev-security` deliberately left to the manager's independent gates.
- Deviations from the manager's recommended answers (each justified in HLD §0.3): Q-A (anonymous refused,
  per binding decision 1's own wording), Q-F (extended with a signed policy — gate-scoped since Gate 1),
  Q-M (no new config key), Q-P (no sidebar badge); extensions: Q-H (adds AG-4), Q-B (adds a config-env
  denylist — a small `project_config.py` edit the brief hoped to avoid, required to close TM-28).

## Evidence
- Design-only change: no production code or test files changed. Every engine/UI claim in the HLD was
  checked against the source at base `bb6d8a0`; line numbers are approximate and each implementing task
  must re-verify them.
- Rev 1 design-time checks (scratch scripts outside the repo): full suite on the untouched base 5041 passed /
  8 skipped (2 known bench failures deselected) — matches the brief; vitest 34 files / 406 tests;
  contract-pinning suites (exact-key tests + NFR-2 gate) 191 passed; the rev-1 schema fragment 24/24; a
  pydantic mirror confirms the validator assumptions; the real `create_app` confirms principal propagation
  through an outer test middleware and that routes registered after `_mount_frontend` are shadowed.
- Rev 2 design-time checks (2026-10-05): rev-2 schema fragment (null parity) 29/29 designed verdicts and the
  rewritten §13 example validates; the wrap serializer keeps all 10 example specs byte-identical
  (`canonical_spec_json` and `model_dump_json`); `json.loads` raises `RecursionError`/`ValueError` on the
  S-04 payloads while the proposed `parse_strict` and pydantic's `model_validate_json` both fail cleanly.
  Details: `T-FjxjlV-design-package/STATUS.md`.
- Consistency checks on the package (scratch script): code fences balanced, every table row has its
  header's cell count, no emoji, no stale rev-1 strings outside the changelog's history mentions, every
  `§` reference resolves (except §27, which the docs refresh adds).

## Risks / Blockers
- Not blocked. Implementation must not start before the re-gate passes and the parent session confirms the
  decision points.
- RR-1 (same-uid key read) and RR-5 (complete erasure of in-workspace gate evidence) are accepted,
  documented residual risks, not defects.
- Merge risk with the sibling epics is concentrated in `engine.py` (`_prepare_and_maybe_dispatch`, `run()`,
  `__init__`), `models.py` (`TaskSpec`, `RunState`), `project_config.py`, `ui/files.py` and the UI bundle
  (HLD §26, §16.4).
- Schedule: the 18-day critical path is counted in estimate days (R-20).

## Next actions
1. Manager: run the re-gate (`reviewer` + `dev-security`) on rev 2, starting from HLD §0.5; route any
   BLOCKER/CRITICAL/HIGH back to the architect (resume via SendMessage).
2. Parent session: confirm the decision points of HLD §23.3.
3. Start Sprint 1 with `T-AGO2L6-spec-model-validation` (its `approvals/models.py` + `errors.py` unblock
   `T-drPIif` and `T-1MgGb4` on day 2).

## Manager log
- By: manager · Role: agent · Date: 2026-10-05 · Comment: Baselines on untouched base bb6d8a0 recorded (pytest 5041 passed / 8 skipped / 2 pre-existing bench failures `tests/bench/test_dev_core_suite.py` + `tests/bench/test_dev_medium_suite.py` fake-pass; ruff: 1 pre-existing I001 + 1 format diff in `output/E-YAAGhk-.../repro_emit_lost_on_breaker_trip.py`; mypy src: 4 pre-existing errors in `_version.py`; vitest 34 files / 406 tests). Design package received from architect. Independent early gates launched: `reviewer` (design quality, feasibility vs real code, scope) and `dev-security` (adversarial). No implementation starts before both return PASS / PASS-WITH-CHANGES and the architect has applied the required changes.

- By: user (relayed by developer) · Role: user · Date: 2026-10-05 · Comment: Owner decisions: gate approval requires auth (anonymous refused, OQ-1); resume policy is per-gate with no removing/weakening (OQ-2); OQ-12..OQ-15 and CE-1 stand as designed. Implementation waits for the design re-gate; auth (E-Da5Tn9) has landed, so the `Principal` contract is now available.
