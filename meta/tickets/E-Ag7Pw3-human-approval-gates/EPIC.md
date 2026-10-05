# EPIC: E-Ag7Pw3-human-approval-gates

## Metadata
- Epic ID: `E-Ag7Pw3-human-approval-gates`
- Title: Human approval gates — a DAG node that pauses a run until a human approves or rejects, with
  HMAC-signed, content-bound decisions, an explicit same-uid threat model, CLI and dashboard surfaces
- Owner: architect (design) → manager (delivery)
- Created: 2026-10-04
- Last Updated: 2026-10-05
- Status: **Draft rev 2** — design package revised after the early design Gate 1 (dev-security FAIL,
  reviewer PASS-WITH-CHANGES; every finding mapped in HLD §0.5). Awaiting the re-gate and the parent
  session's confirmation of the decision points (HLD §23.3). No implementation task has started.

## Summary
- Goal: let a workflow author put an approval gate on any DAG node ("review the plan before the expensive
  fan-out", "confirm before the destructive/merge step"). The gate costs nothing while waiting (never a
  slot), survives crash/resume/boot-resume without trusting agent-writable files to skip any check, cannot
  be satisfied by an agent writing a file or running `ao approve` naively, and binds every decision to
  SHA-256 hashes of what the human reviewed. Residual risk (a same-uid process can read the key; complete
  erasure of in-workspace gate evidence; agent-authored routing verdicts; work a gate does not cover) is
  documented, not hidden.
- Scope In: `TaskSpec.approval` + validation (pydantic, JSON Schema, `cross_validate`); new package
  `src/agent_orchestrator/approvals/` (17 modules); small listed edits to the engine, models, spec,
  artifacts, runstate, CLI (incl. the traceback-locals one-liner), outcomes, xdg, `project_config.py`
  (6-key env denylist), spawn sites, the dashboard (incl. the `ui/files.py` approvals-tree denial);
  `ao approvals list|show`, `ao approve`, `ao reject`; 5 dashboard routes; frontend status/panel/review
  tab/wait stat; tests; HLD; ADR-0020; docs refresh.
- Scope Out: quorum/N-of-M, delegation, notifications, park-and-exit, reject→alternate route,
  auto-approve rules, expiry reminders (brief non-MVP); policy storage outside the workspace (F-15);
  signed routing verdicts; dashboard authentication itself (sibling `E-Da5Tn9`); the result cache
  (sibling `E-Rc4Hk8`); new `ao run`/`ao resume` options; new `.ao/config.yaml` keys; `ui/service.py`.
  Full list with reasons: HLD §1.4.

## Design
- HLD + LLD (sections 0–26, rev 2): [`docs-md/human-approval-gates-hld.md`](../../../docs-md/human-approval-gates-hld.md)
  — start with §0.5 (Gate 1 changelog).
- ADR: [`docs-md/adr/ADR-0020-human-approval-gates-signed-decisions.md`](../../../docs-md/adr/ADR-0020-human-approval-gates-signed-decisions.md) (D1–D13)
- Threat model: HLD §7 (attack-path table TM-1..TM-34, control-strength ladder §7.5, residual risks
  RR-1..RR-15 and the "closed at Gate 1" table §7.6)
- Shared-file touchpoint inventory: HLD §26 (rows 1–36 incl. 19b, 25b)

## Requirements

### Functional (MVP)
- **FR-1** Spec: a task with `approval: ApprovalSpec` is an approval gate (no agent dispatch; `agent` optional
  exactly when `approval` is set; `"approval": null` is an ordinary task); gate-shape rules in pydantic +
  JSON Schema + `cross_validate`; fatal AG-1..AG-5; `ao validate` warnings W-AG-1..W-AG-10; gate-free tasks
  serialize byte-identically. (HLD §9.1)
- **FR-2** Gate lifecycle: when deps settle, the engine opens a signed request on `RunState.approvals` in a
  pre-pass before FILL (whatever the free capacity); the task becomes `awaiting_approval`; no
  executor/`max_parallel` slot is ever held; independent tasks continue. (§9.10)
- **FR-3** Outcomes: approve → `succeeded`; reject/timeout (`on_timeout: reject`) → `failed`, never
  retried, never self-healed, halts the run (resumable). (§9.10.4)
- **FR-4** Signed decisions: HMAC-SHA256 records bound to run/task/request/review hashes; engine checks
  V0–V12 incl. a consume-time re-hash (bounded per poll); closed refusal codes; refused records audited
  and never blocking. (§9.5, §9.6)
- **FR-5** Key custody: per-user key outside the workspace, home from `pwd` (never `$HOME`), 0700/0600,
  owner/symlink/workspace checks, race-safe engine-only `link()` bootstrap, overrides only from the real
  environment (a workspace `.ao/config.yaml` cannot set `AO_APPROVAL_KEY_DIR`, `XDG_CONFIG_HOME`,
  `AO_IN_AGENT`, `HOME`, `USER`, `LOGNAME` — TM-28), `key_id` in records, no key material in
  logs/exceptions/tracebacks. (§9.3)
- **FR-6** Resume/boot-resume: resume detected from the engine's own knowledge; re-enter with the same
  `request_id`/expiry; honour decisions signed before expiry while down (applied before anything else is
  dispatched); re-derive gate (and gate-ancestor) statuses; signed **gate-scoped** policy refuses removed or
  weakened gates and removed/renamed/detached downstream work; a missing policy with gate evidence fails
  closed; persisted loop clones re-derived; new request after reject/expire. (§9.9, §9.10.6)
- **FR-7** Routes/loops/emit: gates the routing rules mark `not_taken` are never requested; one request per
  loop iteration with the static base gate's spec; agent-authored manifests cannot declare `approval`.
  (§9.1.5–§9.1.7)
- **FR-8** CLI: `ao approvals list|show`, `ao approve`, `ao reject` with marker/TTY/`--yes`/
  `--expect-digest` rules, exit codes 0–11 from the refusal mapping table, `--json`, identity from the real
  uid, built on the shared views. (§9.13)
- **FR-9** Dashboard API: 5 routes; principal read via the contract only; authorization matrix; one refusal
  mapping table; existing Origin/Host/Content-Type middleware; `X-Frame-Options: DENY`; the file browser
  refuses run `approvals/` trees. (§9.14)
- **FR-10** Frontend: `awaiting_approval` tone/glyph/legend; pending panel; `approval` review tab with
  hashes, drift and two-step confirm; Review links; "Approval wait" stat next to wall time. (§9.15)
- **FR-11** Observability: event catalog in `run.log` and MAC'd `audit.jsonl`. (§9.7, §15)
- **FR-12** Surfaces: `status.json` `pending_approvals`/counts (gated runs only), `current_task` fallback,
  `ao status` trailer, `RunSummary.approval_wait_seconds` (union of waits, never above wall time),
  `ao report-timing`, usage/outcomes/activity rules. (§9.2.4–§9.2.6)
- **FR-13** `AO_IN_AGENT=1` at every agent-influenced spawn point (8 edit sites / 10 spawn points), always
  the last env assignment, never in the engine's own env. (§9.11)

### Non-functional
- **NFR-1** Gate-free workflows byte-identical (no key access, no new files/log lines, same `status.json`,
  same spec hashes, same scheduling; one `lstat` of `<run_dir>/approvals` is the only added I/O).
- **NFR-2** Determinism: injected clock/sleeper/cancel; deterministic consume order and round-robin.
- **NFR-3** Bounded I/O on every untrusted read; per-poll caps incl. the cross-gate re-hash budget.
- **NFR-4** Fail closed on any integrity doubt; never derive "skip this check" from agent-writable files.
- **NFR-5** Pre-epic `state.json` loads unchanged; one-way upgrade documented for gated runs.
- **NFR-6** No new runtime dependency.
- **NFR-7** Honest security posture: speed bumps never called boundaries; residual risk explicit.
- **NFR-8** Quality gates: no new pytest failures vs baseline (5041 passed / 8 skipped / 2 known bench
  failures); ruff (only the known I001); mypy on changed modules; vitest green; ≥ 90% line coverage of
  `agent_orchestrator.approvals`.
- **NFR-9** Minimal, listed shared-file footprint (HLD §26).

### Non-MVP (deferred; reasons in HLD §1.4)
Quorum (F-1) · park-and-exit (F-2) · reject→route (F-3) · directory review (F-4) · post-approval
artifact pinning (F-5) · graph-node badge (F-6) · sidebar badge (F-7) · `ao approve --wait` (F-8) · key
rotation command (F-9) · role-based approvers (F-10) · CSP `frame-ancestors` (F-11) · engine heartbeat
while waiting (F-12) · gate scope for injected tasks (F-13) · join-any gates (F-14) · approval policy
stored outside the workspace (F-15) · signed routing verdicts · delegation · notifications · auto-approve
rules · expiry reminders · separate-user broker / WebAuthn (R-AG-1).

## Task List

Team assumption: 4 developers × 2 sprints (capacity math in HLD §22.1: 153.6 h committed per sprint).
Every task has one owner and a `reviewer`-agent review in its acceptance criteria. "Start–end" is in
working days from the start of Sprint 1 (S1 = 0–10, S2 = 10–20). Dependencies are strict (each layer is
verified before the next builds on it). Rev 2 (Gate 1 R-06): rev 1's `T-csusci-signing-keys-hashing-authz`
was split into `T-drPIif` and `T-1MgGb4`; `T-pfJiXw` was renamed (audit moved out, views moved in).

| # | Task | Owner | Est. | Depends on | Start–end | Status |
|---|---|---|---|---|---|---|
| 0 | `T-FjxjlV-design-package` — HLD/LLD, ADR-0020, tickets, Phase-4 record, Gate 1 revision | architect | 3 d | — | before S1 | Draft rev 2 (awaiting re-gate) |
| 1 | `T-AGO2L6-spec-model-validation` — models (incl. rev-2 models and constants), schema + null parity, wrap serializer, AG-1..AG-5, W-AG-1..6/9/10, containment, structural isolation, layering test | developer | 3 d | design re-gate | 0–3 | Not started |
| 2 | `T-drPIif-canonical-keys-audit` — canonical + bounded parser, keys (`pwd` home, `link()` only), xdg, 6-key denylist, `cli.py` one-liner, audit, wait accounting | developer | 2.5 d | T-AGO2L6 (`approvals/models.py`) | 1–3.5 | Not started |
| 3 | `T-1MgGb4-hashing-records-authz-policy` — hashing + budget, records + refusal codes, authz, gate-scoped policy + resume-integrity helpers, W-AG-7/8 | developer | 2.5 d | T-AGO2L6; T-drPIif (canonical/keys) | 1–3.5 | Not started |
| 4 | `T-1B8hu4-agent-marker-spawn-sites` — `AO_IN_AGENT` at 8 edit sites / 10 spawn points, set last | developer | 1.5 d | T-AGO2L6 complete | 3–4.5 | Not started |
| 5 | `T-pfJiXw-decision-store-signer-views` — store (deferral, `find_valid_record`), signer (`decidability`, codes, `already_decided`), views | developer | 3 d | T-drPIif, T-1MgGb4 | 3.5–6.5 | Not started |
| 6 | `T-vwIpSw-engine-gate-lifecycle` — extraction first, driver factory, pre-pass, budget, fail-closed driver check, settle, NFR-1 golden | developer | 3 d | T-pfJiXw | 6.5–9.5 | Not started |
| 7 | `T-l43hCg-dashboard-approvals-backend` — principal reader, service over views/signer, routes + mapping table, XFO, file-browser denial | developer | 2.5 d | T-pfJiXw | 6.5–9 | Not started |
| 8 | `T-otHPGB-engine-resume-policy` — resume integrity (`begin_session`), re-entry/supersede, boot-resume, loops/routes | developer | 3 d | T-vwIpSw | 9.5–12.5 | Not started |
| 9 | `T-nmL0HP-cli-approval-commands` — CLI over views/signer + subprocess e2e | developer | 2.5 d | T-pfJiXw (e2e: T-vwIpSw) | 10–12.5 | Not started |
| 10 | `T-ZPGoSN-consumer-surfaces-accounting` — status.json, `ao status`, report-timing, `RunSummary` wait, outcomes/activity | developer | 1.5 d | T-vwIpSw; T-nmL0HP registration | 10.5–12 | Not started |
| 11 | `T-pIZq3q-dashboard-approvals-frontend` — UI + wait stat + bundle | developer | 3 d | T-l43hCg | 10–13 | Not started |
| 12 | `T-pdLR96-adversarial-e2e-test-pass` — adversarial suite (TM-1..TM-34), event catalog, completion checks, full gates | tester | 2 d | tasks 1–11 | 13–15 | Not started |
| 13 | `T-mfdlOc-security-review-remediation` — as-built dev-security review + fixes | dev-security + developer | 2 d | T-pdLR96 | 15–17 | Not started |
| 14 | `T-wH71UE-docs-refresh` — reconcile docs with the code | architect | 1 d | T-mfdlOc | 17–18 | Not started |

Sprint 1: 18.5 dev-days (148 h). Sprint 2: 14.5 dev-days (116 h) + 37.6 h buffer. Implementation total
33 dev-days. Critical path: dependency chain 17.5 days; 18 days with the capacity-feasible schedule (2 days
of slack in the 20-day window; units caveat R-20).

Task tree (critical path in bold):

```
T-FjxjlV (design, re-gate)
 └─ **T-AGO2L6** ──┬─ T-1B8hu4
                   ├─ T-drPIif ─────────────┐
                   └─ **T-1MgGb4** ─────────┴─ **T-pfJiXw** ──┬─ **T-vwIpSw** ──┬─ **T-otHPGB** ──┐
                                                              │                 ├─ T-ZPGoSN ──────┤
                                                              ├─ T-nmL0HP ──────┘ (e2e)           ├─ **T-pdLR96** ── **T-mfdlOc** ── **T-wH71UE**
                                                              └─ T-l43hCg ── T-pIZq3q ────────────┘
```

Parallelisation map (HLD §22.3, adopted from the Gate 1 reviewer): A `T-AGO2L6` alone on its files
(models merged at day 1) · B `T-drPIif` ‖ `T-1MgGb4` ‖ `T-1B8hu4` (after A merged) · C `T-pfJiXw` · D
`T-vwIpSw` ‖ `T-l43hCg` (‖ `T-nmL0HP` allowed) · E `T-otHPGB` ‖ `T-ZPGoSN` ‖ `T-pIZq3q` ‖ `T-nmL0HP` · F
`T-pdLR96` → `T-mfdlOc` → `T-wH71UE`. Never in parallel: `T-nmL0HP` with `T-ZPGoSN` (`cli.py`), `T-vwIpSw`
with `T-otHPGB` (`engine_glue.py`), `T-AGO2L6` with `T-1B8hu4` (`spec.py`). Each ticket lists its exclusive
files; parallel agents run only targeted tests; the full suite runs at serialized checkpoints after each
stage; `tests/approvals/test_import_layering.py` has one owner (`T-AGO2L6`).

### Rollup
| State | Count | Tasks |
|---|---|---|
| Done | 0 | — |
| In Progress | 0 | — |
| Draft rev 2 (awaiting re-gate) | 1 | T-FjxjlV |
| Not started | 14 | all others |
| Blocked | 0 | — |

## Risks and Dependencies
- RR-1 (stated plainly): a same-uid process can read the 0600 key and forge decisions. Not mitigated in
  the MVP; roadmap R-AG-1 (separate OS user / broker / WebAuthn).
- RR-5: an agent that erases every in-workspace trace of gate activity before a resume can edit gates away;
  F-15 (policy outside the workspace) is the fix, not in the MVP.
- RR-12/RR-14: routing verdicts are agent-authored; a gate binds an edge, not the behaviour behind it —
  authoring rules + W-AG-7/W-AG-8.
- Parent-session decision points (HLD §23.3): **OQ-1** anonymous dashboard decisions refused; **OQ-2
  (modified): gate-scoped policy instead of whole-DAG freeze**; **OQ-12** 6-key config-env denylist
  (`project_config.py` edit); **OQ-13** gate-evidence rule; **OQ-14** file-browser denial of `approvals/`
  (CE-4); **OQ-15** `pretty_exceptions_show_locals=False` in `cli.py`; **CE-1** the result cache must never
  settle a gate.
- Merge conflicts with the sibling epics in `engine.py`, `models.py`, `cli.py`, `project_config.py`,
  `xdg.py`, `ui/app.py`, `ui/security.py`, `ui/files.py` and the committed UI bundle (HLD §26, §16.4).
- Schedule: the critical path is counted in estimate days (R-20); staff the chain with the most
  experienced developer.
- Dependency: `E-Da5Tn9` provides `request.state.principal`.

## Links
- Design doc: `docs-md/human-approval-gates-hld.md` (Gate 1 changelog: §0.5)
- ADR: `docs-md/adr/ADR-0020-human-approval-gates-signed-decisions.md`
- Sprint plan: HLD §22
- Epic brief and Gate 1 findings (manager): session scratchpad `EPIC-BRIEF.md`,
  `GATE1-FINDINGS-AND-DECISIONS.md` (not committed)
- Output artifacts (screenshots, evidence): `output/E-Ag7Pw3-human-approval-gates/` (created by later tasks)
