# STATUS

- ID: `T-pfJiXw-decision-store-signer-views`
- Updated At: 2026-10-05
- State: Draft — Not started
- Owner: developer

## This update
- By: architect · Role: agent · Date: 2026-10-04 · Comment: Ticket created from HLD §9.6, §9.7, §9.12.
- By: architect · Role: agent · Date: 2026-10-05 · Comment: Gate 1 revision. Renamed from
  `T-pfJiXw-decision-store-audit-signer`: `audit.py` moved to `T-drPIif`; gained `views.py` (R-05),
  `find_valid_record` + `already_decided` (suggestion S-06), closed refusal codes (R-08), scan deferral for
  the re-hash budget (S-09), `state.json` parse errors → `not_found` (S-04); flood/suppression events cut
  (CUT 4). Still 3 days.
- By: architect · Role: agent · Date: 2026-10-05 · Comment: Gate 2 revision (rev 3, frozen): the scan no
  longer takes a byte budget; it allows at most one consume-time re-hash per poll (`OncePerPollRehash`,
  `may_rehash`, outcome `needs_rehash` that leaves records untouched; R-10), V12 via `check_review_hashes`;
  the shared test fixtures `fake_clock` / `make_gated_workflow` / `human_decides` are appended to
  `tests/approvals/conftest.py` here (stage C runs alone; R-09). Estimate unchanged (3 d).

## Evidence
- None yet.

## Risks / Blockers
- Waits for `T-drPIif` and `T-1MgGb4`.

## Next actions
1. Store (incl. the one-re-hash-per-poll gating and `find_valid_record`), then the signer with
   `decidability`, then views, then the shared fixtures in `conftest.py`.
