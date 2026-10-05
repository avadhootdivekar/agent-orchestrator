# TASK: T-1MgGb4-hashing-records-authz-policy

## Metadata
- Task ID: `T-1MgGb4-hashing-records-authz-policy`
- Epic ID: `E-Ag7Pw3-human-approval-gates`
- Owner: developer
- Created: 2026-10-05 (split from rev-1 `T-csusci-signing-keys-hashing-authz`, Gate 1 R-06)
- Last Updated: 2026-10-05 (rev 3)
- Status: Draft
- Estimate: 2.5 days (on the critical path; rev 3 moved W-AG-7/8 out to `T-Mdk27e` and added the marker
  check and the policy digest chain, so the estimate is unchanged)

## Requirements Mapping
- Requirement IDs: FR-4 (record build/verify, refusal codes), FR-6 (pure resume-integrity core incl. the
  marker check), NFR-2, NFR-3, NFR-4
- Design: HLD §9.4 (hashing), §9.5 (records, V0–V12, `RefusalReason`/`RefusalDetail`,
  `check_review_hashes`), §9.8 (authz), §9.9 (gate-scoped policy, `policy_digest`, `previous_sha256`,
  `check_marker`, in-state evidence, clone re-derivation, derivable `not_taken`, ancestors), §7.4
  TM-1/7/8/26/29/33/34/36; Gate 1 S-02, S-03, R-03, R-04, R-08; Gate 2 S-11, S-12, R-10

## Description
The pure, heavily unit-tested core of verification and resume integrity. No engine, CLI or UI wiring, no
validate warnings (W-AG-7 belongs to `T-Mdk27e`; W-AG-8 was withdrawn in rev 3).

- `approvals/hashing.py`: `hash_review_artifact`, `hash_review_set`, `review_digest` per §9.4 (the one
  audited payload-read surface; module docstring states the NFR-1 exception). **No budget and no
  `deferred` state** (rev 3, Gate 2 R-10): the engine bounds consume-time hashing by allowing one re-hash
  per poll (`T-pfJiXw`/`T-vwIpSw`).
- `approvals/records.py`: `build_request`, `verify_request` (`ok|key_mismatch|bad_signature`; an
  uncanonicalizable payload is `bad_signature`), `parse_decision_file`, `build_decision_record`,
  `verify_decision` implementing V1–V12 (V12 only with `check_hashes=True`) with
  `VerifyContext`/`VerifyResult` (`reason` + `detail_code`; V11 is `review_set_mismatch`),
  `check_review_hashes(record, states)` (V12 alone, pure — the store runs it as a separate step), and
  `read_bounded_nofollow(path, cap)` for V0 reuse.
- `approvals/authz.py`: `authorize`, `identity_for_cli` (from `pwd.getpwuid(os.geteuid())`, never `$USER`),
  `identity_for_principal`.
- `approvals/policy.py` (§9.9): `approval_security_view` / `approval_security_digest(spec)` (incl. the
  message hash); `quiet_graph(static)` (edges from `dag.iter_dependency_edges`, never `build_dag`);
  `compute_policy_body` (per gate: digest + sorted static forward closure); `sign_policy(...,
  previous_sha256=...)`; `policy_digest(record)` (sha256 of the full signed record); `check_policy` (`ok`
  with additions | `missing` | `key_changed` | `bad_signature` | `drift` with the documented detail
  wording, ≤ `MAX_POLICY_DETAILS`); `check_marker(marker, policy)` (`absent` | `ok` | `lagging` |
  `missing_policy` | `mismatch`, rev 3); `gated_evidence(state, gate_ids)` (**in-state facts only**: no
  `lstat`, a bare `approvals/` directory is not evidence — Gate 2 S-12); `rederive_loop_clones(static,
  injected, clone_body)`; `derivable_not_taken`; `ancestors`. These are also the helpers `T-Mdk27e` reuses
  for W-AG-7.

Files — new: `approvals/{hashing,records,authz,policy}.py`; tests
`tests/approvals/test_{hashing,records_verify,authz_matrix,policy}.py`. Shared: none.
**Exclusive files during stage B** (HLD §22.3): exactly these (rev 3: no `spec_rules.py` edit any more).

## Acceptance Criteria
1. Hashing: `ok` (sha256 matches `hashlib`), `missing`, `not_regular` (directory, FIFO, symlink swapped in
   after resolve), `too_large` (per-file cap and growth while reading), `path_rejected` (`..`, absolute,
   symlink escaping the root), `changed_during_read` (read seam); the per-request total cap. No
   `RehashBudget` or `deferred` state exists.
2. Records: one dedicated test per refusal of V1–V12 producing exactly that `reason` and `detail_code`
   (`test_every_refusal_has_reason_and_detail_code`); an accepted record; `check_hashes=False` skips only
   V12; `check_review_hashes` alone reproduces V12; strict typing (`"size": "1"` rejected); fields of a
   record that fails V3/V4 never appear in `VerifyResult.detail`; `test_depth_bomb_record_refused_malformed`;
   `test_review_set_mismatch_has_its_own_reason`.
3. Authz: every cell of HLD §9.8's matrix (parametrized).
4. Policy (Gate 1 R-03): `test_security_digest_includes_message_hash` (message, approvers, each
   `require_*`, review, timeout change the digest; agent/instruction/model of other tasks do not);
   `test_closure_static_forward` (declared, inferred and loop-id edges; gate excluded);
   `test_check_policy_verdicts` (ok, missing, key_changed, bad_signature incl. another run's policy, drift
   for: gate removed, gate no longer a gate, digest changed, closure member removed/renamed, closure member
   detached — each with its documented wording; a redundant-edge removal that keeps reachability is `ok`);
   `test_additions_reported` (new gate, new downstream task).
5. Marker check and digest chain (Gate 2 S-11, rev 3): `test_policy_digest_covers_signature` (any change
   of any field, `sig` included, changes the digest); `test_check_marker_kinds` — absent; ok; `lagging`
   when the policy's signed `previous_sha256` equals the marker digest; `missing_policy`; `mismatch` (an
   older policy, a foreign policy, a policy two re-signings ahead); `sign_policy(previous_sha256=...)`
   puts the value inside the signed payload (tampering it breaks the signature).
6. Resume-integrity helpers (Gate 1 S-02/S-03/R-04): `test_derivable_not_taken_cones_and_join_propagation`
   (cone marking only for a `succeeded` router with a `route_decisions` entry; `join: all` / `join: any`
   propagation, incl. a gate `not_taken` by join propagation; never trusts persisted `not_taken`);
   `ancestors` returns reverse reachability; `test_gated_evidence_each_kind` (the four in-state kinds; none
   from the spec alone; **a bare `approvals/` directory yields no evidence**, Gate 2 S-12);
   `test_rederive_loop_clones_detects_each_mismatch` (missing/extra clone, gate turned into an agent task,
   weakened approval, changed `depends_on`; clones of non-gated loops ignored).
7. Line coverage ≥ 90% for each new module; `ruff`/`mypy` clean; targeted tests green (full suite at the
   manager's stage-B checkpoint).
8. A `reviewer`-agent review is recorded in STATUS.md.

## Risks
- Closure and derivation must use exactly the engine's edge rules (`iter_dependency_edges`, `compute_cones`,
  `_apply_join`'s effective dependencies); a divergence would refuse legitimate resumes or miss tampering.
  Mitigation: tests build graphs with `build_dag` and `quiet_graph` and compare adjacency.
- Starts before `T-drPIif` finishes: do hashing/authz/policy first; records after `canonical.py`/`keys.py`
  are merged.

## Dependencies
- `T-AGO2L6` (`approvals/models.py`, `approvals/errors.py`).
- `T-drPIif` (`canonical.py`, `keys.py`, by the end of its first day).

## Pseudocode / Algorithm
```text
HLD §9.4 hash_review_artifact, §9.5.4 V0–V12 order (+ check_review_hashes), §9.8 authorize, §9.9.1–§9.9.6
(security view/digest, compute_policy_body, sign_policy(previous_sha256), policy_digest, check_policy,
check_marker, gated_evidence, rederive_loop_clones, derivable_not_taken, ancestors).
```

## Schemas / Interface Notes
- Interfaces: HLD §9.4, §9.5.3 (`RefusalReason`, `RefusalDetail`, `check_review_hashes`), §9.8, §9.9.
- Data: `ApprovalRequest`, `DecisionRecord`, `ApprovalPolicyRecord` (with `previous_sha256`), `GatePolicy`,
  `GatedMarker` (from `T-AGO2L6`).

## Verification
```
WT=/usr/avadhoot/mounted/agent-orchestrator/.claude/worktrees/agent-a513b4d78ac3b20bb
PY=/usr/avadhoot/mounted/agent-orchestrator/.venv/bin/python
cd $WT && $PY -m pytest -o pythonpath=src -p no:cacheprovider -q tests/approvals/test_hashing.py tests/approvals/test_records_verify.py tests/approvals/test_authz_matrix.py tests/approvals/test_policy.py
cd $WT && $PY -m pytest -o pythonpath=src -p no:cacheprovider -q --cov=agent_orchestrator.approvals --cov-report=term-missing tests/approvals
cd $WT && $PY -m ruff check src/agent_orchestrator/approvals tests/approvals && $PY -m mypy src/agent_orchestrator/approvals
```

## Handoff Boundary
- Upstream: `T-AGO2L6`, `T-drPIif`.
- Downstream: `T-pfJiXw` (store/signer/views), `T-vwIpSw` (open/poll, fresh-run policy), `T-otHPGB`
  (`begin_session` uses the policy, marker check, evidence, clone and derivation helpers), `T-Mdk27e`
  (W-AG-7 reuses `derivable_not_taken`/`quiet_graph`).

## Artifacts
- Code as listed; coverage numbers in STATUS.md.
