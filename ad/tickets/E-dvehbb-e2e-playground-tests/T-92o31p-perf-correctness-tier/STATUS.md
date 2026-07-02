# STATUS

- ID: `T-92o31p-perf-correctness-tier`
- Updated At: 2026-07-01
- State: Draft
- Owner: tester

## This update
- Drafted by architect. Phase-2 Tier-4: always-green perf envelope (Fake) + opt-in correctness spot-check (real).

## Evidence
- TODO (implementation): `tests/playground/test_performance.py`, `tests/playground/test_correctness_real.py`.

## Risks / Blockers
- CI timing flake → generous named bound; correctness is best-effort opt-in.

## Next actions
1. Add parametrized perf test with named budget + attempt-count check.
2. Add gated correctness test executing the generated solution on a fixed input matrix.
