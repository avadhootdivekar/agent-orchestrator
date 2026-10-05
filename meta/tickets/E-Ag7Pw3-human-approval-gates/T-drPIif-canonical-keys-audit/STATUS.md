# STATUS

- ID: `T-drPIif-canonical-keys-audit`
- Updated At: 2026-10-05
- State: Draft — Not started
- Owner: developer

## This update
- By: architect · Role: agent · Date: 2026-10-05 · Comment: Ticket created by the Gate 1 split of
  `T-csusci-signing-keys-hashing-authz` (R-06): canonical JSON + bounded parser (S-04), keys with the `pwd`
  home and `link()`-only bootstrap (S-08, CUT 3), the 6-key config-env denylist (S-08), the `cli.py`
  traceback-locals one-liner (S-05), plus `audit.py` (moved from `T-pfJiXw`, T-16) and the pure wait
  accounting (moved earlier so `views.py` can use it; union semantics per R-07). 2.5 days.
- By: architect · Role: agent · Date: 2026-10-05 · Comment: Gate 2 revision (rev 3, frozen): adds
  `approvals/gated_marker.py` (the out-of-workspace, MAC'd gated marker of HLD §9.3.4: custody-checked read,
  engine-only temp + `os.replace` write) and `ApprovalKeyStore.gated_marker_path` (S-11, "F-15-lite"), with
  `test_gated_marker.py`. Estimate 2.5 d → 3 d; merge order canonical/keys (day 2) → audit/accounting (day
  3.5) → marker (day 4), so the extra half day stays off the critical chain.

## Evidence
- None yet.

## Risks / Blockers
- Waits for `approvals/models.py` + `errors.py` from `T-AGO2L6` (end of its day 1).

## Next actions
1. `canonical.py` + `keys.py` first and merge them by the end of day 1 (unblocks `T-1MgGb4`'s records).
2. Then the denylist, the `cli.py` one-liner, `audit.py`, `accounting.py` (merge by day 3.5), and
   `gated_marker.py` last (by day 4).
