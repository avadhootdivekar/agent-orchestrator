# STATUS

- ID: `T-1MgGb4-hashing-records-authz-policy`
- Updated At: 2026-10-05
- State: Draft — Not started
- Owner: developer

## This update
- By: architect · Role: agent · Date: 2026-10-05 · Comment: Ticket created by the Gate 1 split of
  `T-csusci-signing-keys-hashing-authz` (R-06): hashing with the per-poll re-hash budget (S-09), records
  V0–V12 with the closed refusal codes (R-08), authz, and the pure resume-integrity core — gate-scoped
  policy (R-03), gate evidence (S-03), loop-clone re-derivation (R-04), derivable `not_taken` and gate
  ancestors (S-02 + new concern NC-1) — plus the W-AG-7/W-AG-8 helpers (S-02, S-07). 2.5 days, on the
  critical path.
- By: architect · Role: agent · Date: 2026-10-05 · Comment: Gate 2 revision (rev 3, frozen): W-AG-7/W-AG-8
  helpers and their `spec_rules` wiring moved out (W-AG-7 to the new `T-Mdk27e`, W-AG-8 withdrawn; R-10);
  `RehashBudget` and the `deferred` states removed, `check_review_hashes` added so the store can allow one
  re-hash per poll (R-10); `policy_digest`, the signed `previous_sha256` and `check_marker` added for the
  gated marker (S-11); `gated_evidence` no longer looks at the `approvals/` directory (S-12). Estimate
  unchanged (2.5 d).

## Evidence
- None yet.

## Risks / Blockers
- Waits for `approvals/models.py` (end of `T-AGO2L6`'s day 1); records wait for `T-drPIif`'s
  `canonical.py`/`keys.py`.

## Next actions
1. Hashing, authz and the policy helpers first (no key needed).
2. Records once `canonical.py`/`keys.py` are merged; then `policy_digest`/`check_marker` (rev 3). No validate
   wiring here any more (W-AG-7 belongs to `T-Mdk27e`).
