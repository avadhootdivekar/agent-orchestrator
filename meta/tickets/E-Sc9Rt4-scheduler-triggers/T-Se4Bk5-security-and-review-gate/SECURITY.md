# T-Se4Bk5 — security gate (partial pass, w20-02)

- By: reviewer agent (overseer unit w20-02-a1-security-gate) · Role: reviewer · Date: 2026-11-11
- Reviewed state: **committed HEAD `a3d0195`** on `ad/enhancements-10nov` (plus this pass's fix
  `b92f427`). At review time the carry-over units (w18-01, w18-04, w19-01) had **uncommitted** edits in
  `webhook.py`, `schedules/models.py`, `schedule_launch.py`, `schedule_engine.py`, `fire_store.py`,
  `run_options.py` and others, and they were still changing them during the review. Those edits were read
  as a diff and marked **pending** below. They are not reviewed as landed code.
- Method: HEAD was extracted with `git archive HEAD` into a temp tree. The probes ran against that tree,
  so the dirty working tree could not affect the results. Probe scripts:
  `output/E-Sc9Rt4-scheduler-triggers/se4bk5-probes/` (`p1`..`p5`).
- Scope of this pass: HMAC / scheme pinning / replay, allow-lists, file locking and path handling
  (AC1–AC5, part of AC2 and AC7a). **Not done in this pass:** AC6 (run-id guard re-verification), AC7
  (threat-model paragraph), AC8–AC13 (`REVIEW.md` architecture pass), AC14 (full quality gate) and AC16
  (cross-ticket sync).

## Findings

### Blocking

**S-1. At HEAD, the signature scheme is not pinned, so an `X-AO-Signature` delivery can be replayed
indefinitely as a GitHub-form delivery.** `verify_delivery` (HEAD `webhook.py` ~L345) accepts whichever
header is present. An `X-AO-Signature` over `"{ts}.{body}"` is also a valid `X-Hub-Signature-256` over the
body `"{ts}." + body`, and the GitHub form has no timestamp check. Probe `p1_downgrade.py`: the X-AO form a
week later returns `stale`, while the same signature sent as `x-hub-signature-256` with body `ts.body` is
**ACCEPTED**. Only the in-memory nonce cache (`replay_key` equal) stops it, and that cache is reset on
restart and evictable from the global LRU. The body is never parsed, so a non-JSON body does not stop the
attack. **Fix pending (uncommitted, w19-01 N1):** `webhook.scheme: ao|github`, default `ao`; the other
scheme's header is refused with `wrong_scheme` 401. Handed to the w19-01 owner (T-Wh9Kv1). It must land
before this gate closes, and the re-run of `p1` must print the downgrade as rejected.

### Should-fix

**S-2. The ungoverned launch path reads `prompt_file` with a plain `read_text`, so a FIFO hangs the
engine thread and the read has no size cap.** `ScheduledLauncher._read_prompt` (`schedule_launch.py`
~L455). Probe `p2_fifo.py`: the read is still blocked after 3 s. Today this is **latent**: D-2 forces every
loaded binding to have effective limits, so `launch()` always takes the governed path, and
`governance_gate.read_prompt` already opens with `O_NONBLOCK`, checks `fstat` for `S_ISREG`, and caps at
`MAX_PROMPT_BYTES`. Proposed change: make `_read_prompt` reuse `governance_gate._read_regular_file`, or
delete the ungoverned branch. Handed to the T-PpE5lf/T-Ap1Xs3 owner. Not fixed here because
`schedule_launch.py` had live uncommitted edits.

**S-3. A NUL byte in a path field passes the load-time shape check and then raises `ValueError` at use.**
Probe `p3_paths.py`: `"a\x00b"` is ACCEPTED at load for `workflow`, `prompt_file`, `watch.paths`,
`webhook.secret_file` and `until.*`. `template` rejects it. Probe `p4_nul.py`: `read_prompt`,
`LocalFsArtifactStore.resolve` and `webhook.resolve_secret` all raise `ValueError`. Every case fails
closed: the launcher records `governance_error`, and the webhook answers 500.
**Fixed here (`b92f427`)** for the governance gate: `read_prompt` now returns
`prompt_missing / invalid_path` (`DETAIL_INVALID_PATH`), with tests in
`tests/service/test_security_gate_se4bk5.py`. Without the fix the NUL test fails against HEAD; with it the
test passes. **Remaining, handed over:**
- (a) Reject NUL and control characters in `_require_workspace_relative` (`schedules/models.py`), so the
  rejection happens at load.
- (b) Catch `ValueError` in `webhook.resolve_secret` (currently 500 instead of 503).

Both files had live uncommitted edits.

**S-4. The `webhook.secret_file` read has a resolve-then-read TOCTOU.** `resolve_secret` does `resolve()`,
then the containment check, `is_file()`, `stat()`, and finally `read_bytes()`. Each step re-walks the
path, so a symlink swapped in after the check is followed. Proposed change: open once with
`O_RDONLY|O_NOFOLLOW|O_NONBLOCK`, then `fstat` (regular file, mode `& 0o077 == 0`), then read. This is the
same idiom as `_read_regular_file`. Handed to T-Wh9Kv1.

**S-5. The webhook module has a second fsync/atomic-write idiom that swallows errors silently.**
`webhook._fsync_dir` duplicates `statefile._fsync_dir` and has `except OSError: pass` with no log line.
`WebhookRequestQueue.enqueue` is a hand-rolled tmp+fsync+replace, which is the AC9 "second atomic-write
idiom", and it leaves the `.tmp` behind if the write fails. Proposed change: use
`statefile.atomic_write_bytes(..., durable=True)`. Handed to T-Wh9Kv1.

**S-6. There is a magic default for the rate limit.** `build_webhook_app.accept` uses
`... if target.binding.webhook else 10`, which duplicates the `WebhookSpec.rate_per_minute` default. Name
it, or drop the fallback (the lookup only returns bindings that have `webhook` set).

### Documented bounded limitations (confirmed, not new)

**S-7. With `scheme: github` there is no time bound on replay.** Nonces are in memory, so a restart
re-admits any captured GitHub-form delivery once. This is documented in the pending webhook docstring.
Persisting the nonces needs an ADR (follow-up).

**S-8. The pending N2 header deadline subclasses uvicorn's private `H11Protocol` internals**
(`handle_events`, `cycle`, `on_response_complete`). This breaks silently on a uvicorn upgrade. When it
lands, pin a uvicorn range or add a contract test that a socket stalled before its headers is closed.

**S-9. `ScheduleStore` follows symlinks for `.ao/`, `.ao/schedules.yaml.lock` and the payload.**
`mutate`/`save` run from the operator's own CLI in the operator's own workspace, so this is the same
trust as editing the file. `save` also writes non-durably (`durable=False`). Accepted.

**S-10 (pending, re-verify).** The uncommitted w18-04 edit in `run_options.py` adds `COST_CAP_OPTION` to
`OPTIONS`. `allowed_options()` grows from 14 to 15 entries and includes `cost_cap_usd` (measured at HEAD
vs. the working tree). That makes AC2's "ALLOWED_OPTIONS unchanged" false once it lands. It is benign for
schedules, because `RUN_ARG_FORBIDDEN` still refuses `run_args.cost_cap_usd` (verified). When it lands,
re-check the dashboard path: a cap only lowers spend.

## Evidence that passed (HEAD)

- **HMAC (AC3).**
  - `grep compare_digest webhook.py` finds L355 (verify) and L616 (unknown-schedule CPU burn).
  - `grep -E "signature\s*==|==\s*signature|sig\s*=="` is empty.
  - The HMAC covers the raw bytes read from the stream (`b"".join(chunks)`).
  - `p5_app.py` with a real ASGI `TestClient`:
    - unknown schedule and bad signature both return `401 {"detail":"unauthorized"}`, byte-identical,
      with equal content-length;
    - a stale timestamp returns 401, a good delivery 202, a duplicate 409;
    - a foreign Host returns **421**, so `SecurityMiddleware` is mounted (`add_middleware` at L577).
  - Nonce LRU is bounded (`NONCE_CACHE_SIZE`). Token bucket is per `(root, sid)`. A group/world-readable
    `secret_file` is refused (`st_mode & 0o077`).
- **Secret leaks (AC4).** Method: a root logging handler at DEBUG plus every response body, across the
  unknown, bad-signature, stale, ok, duplicate and foreign-Host paths (17 log lines). A substring search
  found no occurrence of the secret, the expected hex signature, or the received signature.
- **Allow-lists (AC1/AC2).**
  - `run_args` refuses `sentinel.llm.powers`, `sentinel.max_parallel_ceiling`, `run_id`, `reposets`
    (extra forbidden) and `cost_cap_usd` (`RUN_ARG_FORBIDDEN`). `max_parallel: 999` is refused by its
    bound (≤64).
  - The binding refuses `reposets`, `agents`, `env` and `command` as fields.
  - `_build_run_args_fields` raises at import if a forbidden or unknown name enters
    `SCHEDULE_RUN_ARG_NAMES`.
  - `grep -rn "Popen\|subprocess" schedules service/schedule_*` matches only a docstring
    (`schedule_engine.py:1326`); nothing builds an argv.
- **Path handling (AC1/AC5).** `p3_paths.py` checked `..`, `../x`, `a/../../x`, absolute, `~`, `~root`,
  `C:/`, backslash-`..` and 5000 chars over `workflow`, `prompt_file`, `template`, `watch.paths`,
  `webhook.secret_file`, `until.artifact_exists` and `until.gate_file`:
  - every escaping shape is rejected at load;
  - `template` accepts only a bare name;
  - `%2e%2e` is accepted, but no layer URL-decodes it, so it is a literal in-root name (tested);
  - NUL: see S-3.

  There are second walls at use time:
  - `LocalFsArtifactStore.resolve` (launcher `_guarded_path`);
  - `read_prompt` with `is_relative_to` after `resolve()` (symlink-out is refused, existing test);
  - `watch.py`, which never follows directory symlinks and skips symlinks that resolve out of the root.
- **File locking.**
  - `ScheduleStore.mutate`: `flock(LOCK_EX)` on a companion `.lock`, with a re-read under the lock.
  - Governance: `flock` on `<state_dir>/schedules/usage.lock` around re-gate plus the intended write.
  - Supervisor: `LOCK_EX|LOCK_NB` single-instance lock.
  - `state.json` has a documented single-writer, lock-free design (`fire_store.py` L16), relying on the
    engine lock.
- **Error swallowing.** `grep -E "except.*:\s*pass"` is empty. In the multi-line form the one silent case
  in scope is `webhook._fsync_dir` (S-5).
- **Tests.** `uv run pytest -q tests/service tests/schedules`: **1195 passed, 1 failed**
  (`test_fire_store.py::test_hld_event_table_is_complete`). That test compares `fire_store.py` with
  `docs-md/scheduler-triggers-hld.md`, and both have carry-over uncommitted edits that were in progress.
  It is not caused by this pass. `ruff check`, `ruff format --check` and `mypy` are clean on the files
  this pass touched.
