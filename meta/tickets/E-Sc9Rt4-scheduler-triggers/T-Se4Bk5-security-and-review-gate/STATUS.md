# STATUS

- ID: `T-Se4Bk5-security-and-review-gate`
- Updated At: 2026-11-11
- State: In Progress (security findings S-1..S-6 closed; architecture pass AC6-AC16 not started)
- Owner: reviewer agent (overseer w20-02)

## This update
- By: reviewer agent (w20-02-a1-security-gate) · Role: reviewer · Date: 2026-11-11
- Comment: I ran the security pass on the four areas in this unit's brief: HMAC/scheme/replay,
  allow-lists, file locking and path handling. It ran against committed HEAD `a3d0195`, with probes on a
  clean `git archive` tree. Full write-up: [`SECURITY.md`](SECURITY.md); probes:
  `output/E-Sc9Rt4-scheduler-triggers/se4bk5-probes/`.
- I landed one small fix, **`b92f427`** (S-3, partial): a NUL-byte `prompt_file` now gets the named refusal
  `prompt_missing/invalid_path` in `governance_gate.read_prompt`. Tests:
  `tests/service/test_security_gate_se4bk5.py`, 31 parametrized cases, including path-shape fuzz over
  `workflow`, `prompt_file` and `template`.

## Findings summary (counts match SECURITY.md)
- Blocking: 1.
  - **S-1**: the HEAD webhook accepts either signature header, so an X-AO delivery can be replayed with
    no time bound in GitHub form. The fix is **pending** as uncommitted w19-01 N1 (`webhook.scheme`) and
    must land before this task closes.
- Should-fix: 5.
  - S-2: FIFO/unbounded read in the ungoverned `_read_prompt` (latent; handed over).
  - S-3: NUL at load and in the webhook secret path. Fixed in part here; the rest is handed over.
  - S-4: `secret_file` TOCTOU.
  - S-5: duplicate fsync/atomic-write idiom with a silent `except OSError: pass` in `webhook.py`.
  - S-6: magic `10` fallback for the rate limit.
- Limitations: 4.
  - S-7: no time bound on github-scheme replay across a restart.
  - S-8: pending N2 header deadline depends on uvicorn internals.
  - S-9: `ScheduleStore` follows symlinks.
  - S-10: pending `cost_cap_usd` in `ALLOWED_OPTIONS`; re-verify AC2 when it lands.
- I did not fix the handed-over findings here, because the files they live in (`webhook.py`,
  `schedules/models.py`, `schedule_launch.py`) had live uncommitted edits from carry-over units.

## Evidence
- Passed at HEAD, with details in SECURITY.md:
  - `compare_digest` is used and there is no `==` on any signature;
  - unknown and bad-signature 401s are byte-identical;
  - a real **421** shows the middleware is mounted;
  - no secret or signature appears in any log line or response;
  - run_args and binding allow-lists hold;
  - every escaping path shape is rejected at load;
  - no subprocess call in the schedule modules;
  - flock on the store and the governance lock.
- `uv run pytest -q tests/service tests/schedules`: 1195 passed, 1 failed
  (`test_hld_event_table_is_complete`). Its subject files, `fire_store.py` and the HLD, have carry-over
  uncommitted edits.

## Risks / Blockers
- S-1 is blocking until the w19-01 N1 change is committed. After that, re-run
  `se4bk5-probes/p1_downgrade.py` against the new HEAD.

## Next actions (unfinished — this was the last work wave)
1. Re-verify S-1 and S-8 against HEAD once the w19-01 webhook commit lands, and S-10 once the w18-04
   commit lands.
2. Hand off S-2, S-3(a)(b), S-4, S-5 and S-6 to their owning tasks (T-Wh9Kv1, T-PpE5lf/T-Ap1Xs3,
   T-Sd1Kq7), with the exact changes given in SECURITY.md.
3. Not started: AC6 (run-id guard), AC7 (threat-model delta paragraph), AC8–AC13 (`REVIEW.md`
   architecture pass), AC14 (whole-tree quality gate), AC16 (EPIC/ADR sync).

## w21 update
- By: developer agent (w21-01-a1-security-fixes) · Role: developer · Date: 2026-11-11
- S-1 blocking: the scheme pin was already committed; probe p1 re-run prints `wrong_scheme` for the downgrade. S-2..S-5 fixed with regression tests, S-6 already gone. Details: SECURITY.md "w21 update".
- Evidence: `uv run pytest -q tests/service tests/schedules`: 1248 passed + 2 failed before I fixed one of my own w20 tests (it loaded a NUL path through the validator that now rejects it); the other failure is `tests/schedules/test_cost_cap_cli_e2e.py::test_prototype`, an untracked file from another unit's in-progress cost-cap work, not mine. After the fix, the two security test files pass (45).
- Still NOT done: AC6, AC7, AC8-AC13 (REVIEW.md), AC14 whole-tree gate (tree currently has another unit's uncommitted edits: ruff/mypy/format failures in `schedules/cli.py` and `test_cost_cap_cli_e2e.py`, and `tests/auth` openapi test failing), AC16. S-8 and S-10 re-verify pending.
