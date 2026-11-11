# REVIEW: T-Wh9Kv1-webhook-ingress

By: reviewer (Opus) · Role: reviewer · Date: 2026-11-11 · unit `w27-06-a1-webhook-review`

## Scope
- Code: `src/agent_orchestrator/service/webhook.py` at branch `ad/enhancements-10nov` HEAD `dbbe396` (last webhook
  change `8d0b4ee`, w26-06). Tests: `tests/service/test_webhook.py`, `test_webhook_listener.py`,
  `test_webhook_cli_wiring.py`, `tests/schedules/test_webhook_cli_e2e.py`. Read-only: `ui/security.py`
  (`resolve_allowed_hosts`).
- Against: `TASK.md` AC1–AC16, HLD §8 / §13 / §21 (D2, D3) / §23, `docs-md/guide-scheduled-runs.md`.
- Focus (brief): the HMAC schemes, the Host allow-lists, the github replay window added in w26-06, and nonce handling.
- Nothing in product code was changed by this review.

## Summary
The module is in good shape. All the security-critical invariants hold in code: HMAC over raw bytes with
`hmac.compare_digest`, the scheme pinned per binding (the other scheme's header gets 401 `wrong_scheme`), unknown
schedule and bad signature return byte-identical 401s, a verified delivery only persists a request, and the
allow-list has no `*` escape hatch. All 16 ACs are met. **One confirmed Medium defect (W-1):** the per-schedule
nonce cache is sized for a one-sided 300 s window, but the timestamp check is two-sided (`abs(now - ts) <= 300`).
So when the sender's clock runs ahead, legitimate traffic can evict an `ao` delivery's nonce while its signed
timestamp is still valid, and a captured delivery is accepted again (reproduced below). The w26-06 github
timestamp gate does what AC7 literally says. It is unsigned, though, so it adds no defence against an attacker
(they can just remove it). The real github replay gap is still S-7 (persistent nonces, needs an ADR).

## Evidence (run by this review, temp dirs only)
- `uv run pytest -q tests/service/test_webhook.py tests/service/test_webhook_listener.py tests/service/test_webhook_cli_wiring.py tests/schedules/test_webhook_cli_e2e.py --cov=agent_orchestrator.service.webhook`
  → **109 passed**, `webhook.py` coverage **98 %** (AC16 floor 90 %).
- `ruff check` + `ruff format --check` on those 5 files: clean. `mypy src/agent_orchestrator/service/webhook.py`: no issues.
- Probe for W-1 (`/tmp/wh-probe/probe_nonce_skew.py`, not committed). It uses the test `Harness`: a real
  `TestClient` through the mounted middleware, a fixed clock, rate 10/min, and request files deleted after each
  post to simulate the engine drain.
  - Sender clock +299 s: delivery D (`X-AO-Timestamp = T0+299`, id `captured`) → 202. Then 65 legitimate deliveries
    at 6 s spacing, each 202. At T0+395 an exact replay of D (its stamp is 96 s old, inside the window) → **202 accepted**.
  - Control, sender clock +0 s: the same replay is stale → 401. So the eviction only shows up when the sender's clock
    is ahead. The threshold is about 6 s (`9 + (300+skew)/6 > 60` deliveries fills the 120-key cache).

## Critical (block Done)
None.

## Warnings (should fix)
**W-1 (Medium): the `ao`-scheme nonce cache is sized for half the effective replay window.**
- Location: `webhook.py:371-376` (`nonce_capacity`) vs `webhook.py:472-478` (`abs(now - ts) > REPLAY_WINDOW_SECONDS`).
- Observation: capacity is `rate × (1 + ceil(300/60)) × 2` = 120 keys at rate 10. That covers deliveries admitted
  in the 300 s *after* a stamp. But a stamp up to 300 s in the future is accepted, so a delivery stays replayable for
  up to 600 s after it was first accepted. In that time the bucket admits about 110 deliveries (220 keys).
- Why it matters: it breaks the guarantee that the module docstring (lines 36-39), HLD §21 D3 and the N3 fix make:
  "other schedules' (or the same schedule's legitimate) traffic cannot evict an entry inside the `ao` window". A
  captured `ao` delivery fires a second run. That run is still bounded by overlap/cap/`until`, but it is exactly what
  AC8 is meant to stop.
- Next step: size for the two-sided window, `admitted = rate × (1 + ceil(2 × REPLAY_WINDOW_SECONDS / 60))`, and
  update HLD §21 D3's formula text. Add a regression test shaped like the probe above (future-skewed stamp, 65
  drained legitimate deliveries, then replay → 409). Mutation-check it against the current one-sided formula.

## Suggestions / Low
- **L-1 (Low): a malformed github `X-AO-Timestamp` is rejected *before* the HMAC.** Location: `webhook.py:460-465`.
  The w26-06 commit message and STATUS say the stamp is "checked after the HMAC (no oracle)". That is true for the
  window check but not for the shape check. The response is the same 401, so this is not an oracle. But an unsigned
  request with a junk stamp is logged as `bad_timestamp` instead of `bad_signature`, which misleads operator
  triage. Next step: move the github shape check below the HMAC (the window check already parses it and raises
  `bad_timestamp`), or correct the claim. The `ao` pre-HMAC shape check has a comment saying it is intentional; this
  one does not.
- **L-2 (Low, doc): the module docstring covers only restart for github replay, not LRU eviction.** Location:
  `webhook.py:32-39`. For `scheme: github` there is no window, so after `nonce_capacity(rate)/2` (60 at rate 10)
  later deliveries a captured delivery replays again even without a restart. The w26-06 STATUS block says so; the
  docstring (the S-7 hand-off text) and `guide-scheduled-runs.md` do not. Next step: add "or after
  `nonce_capacity` later deliveries evict it" to the github bullet. This belongs with S-7.
- **D-1 (Low, doc drift): HLD §8.2 and the user guide do not describe the w26-06 optional github stamp.** HLD
  §8.2 pseudocode `ts = headers.get("X-AO-Timestamp") IF scheme == "ao" ELSE NULL` and the note "Timestamp is …
  absent for `scheme: github`", plus `docs-md/guide-scheduled-runs.md:88-90`, still describe github as never reading
  `X-AO-Timestamp`. The code now holds an optional unsigned stamp to the window (401 `stale`/`bad_timestamp`).
  Not fixed here because `scheduler-triggers-hld.md` carries another unit's uncommitted edits. Next step: route
  to `T-Dc6Zr2`, one sentence in each place.
- **S-1 (design note): the github stamp gate only constrains cooperative senders.** It is unsigned, so anyone
  replaying a captured delivery just omits it. It is harmless and does no damage (no new nonce, since `replay_key`
  is still `sha256(body)` on github, and it is checked after the HMAC for the window). It should not be cited as
  replay defence. The AC7 wording is met to the letter; the ticket already treats persistent nonces (S-7, ADR) as
  the real fix.
- **S-2: `WebhookSource.commit` reaches into `self._queue._dir`** (`webhook.py:610`). Next step: a
  `WebhookRequestQueue.delete(name)` method that does the name sanitisation, so the queue owns its layout.
- **S-3: one global `lock` serialises nonce check + rate limit + `enqueue` (fsync and a full `requests/*.json`
  scan + purge) across all schedules** (`webhook.py:689, 750-774`). It is correct and keeps check→persist→remember
  atomic. Throughput is bounded by per-schedule rate limits, so this only matters with many schedules. Next step,
  only if it shows up: a per-`(root, sid)` lock.

## Checked, nothing to flag
- **HMAC schemes:** `compute_signature` uses the raw body for github and `"{ts}." + body` for `ao`.
  `compare_digest` on bytes. The other scheme's header present → 401 before the HMAC (N1 downgrade closed).
  `ao` requires a digits-only ASCII stamp. Huge stamps are caught (`ValueError`/`OverflowError` → 401). Delivery
  ids over 128 chars are digested. `replay_key` covers the signed material, so forging an id does not get past
  dedup (tests `..._forged_id_and_fresh_stamp_is_409`).
- **Allow-lists:** `resolve_webhook_allowed_hosts` builds loopback + bound host + `AO_WEBHOOK_ALLOWED_HOSTS`. It
  drops `*` entries and never reads `AO_UI_ALLOWED_HOSTS`. `SecurityMiddleware` is *mounted* (421 test through a
  real `TestClient`). The non-loopback warning names the env var and the proxy requirement (AC3).
- **Uniform failures / leakage:** the 401 body is a shared constant. The unknown-schedule path burns an HMAC.
  `reject()` logs only reason + clipped slug/id. Every `emit` site checked: none passes the secret or either
  signature (AC11 test covers every rejection path).
- **Order (AC4):** enabled → size (declared + streamed, 408 on body timeout) → lookup + secret → HMAC → window →
  nonce → rate → enqueue, under the lock. Matches HLD §8.2, except for L-1.
- **Secrets (AC10):** env deny-list (N5), workspace-root guard, `O_NOFOLLOW` + `fstat`, `0o077` refused, 4 KiB
  cap, 16-byte minimum.
- **Persistence (AC12/13/15):** `atomic_write_bytes(durable=True)`, per-schedule bound, TTL ignore + purge,
  `observe` pure with `discriminator`/`skip_catch_up`, and `commit` sanitises the name.
- **Review dimensions:** SOLID/KISS ok (one module, clear seams). DRY ok. Magic literals are all named constants.
  Pluggability ok (lookup/resolver/clock/emit/nonce factory/rate limiter injected). Determinism ok (injected clock,
  no direct time on the decision path apart from the `_utc_now` default). Errors/logging ok (one `emit` per
  rejection, lookup failures logged once, fail closed). Testability good. Concurrency covered by the lock.
  Spec/DAG: N/A (no DAG logic).

## Testing notes
- Missing: the W-1 regression test (future-skewed stamp + eviction). Also a github malformed-stamp + bad-signature
  test, which pins L-1 whichever way it is resolved.
- Mocking seams are already sufficient (clock, lookup, resolver, `nonce_cache_factory`, `rate_limiter`). The probe
  needed none of them beyond the existing `Harness`.

## Verdict: can T-Wh9Kv1 be marked Done?
**Yes, Done stands, with W-1 as a tracked should-fix follow-up.** All 16 ACs are verified in code and by the
109 passing tests (98 % coverage, lint/format/types clean on the module). There is no Critical finding. W-1 is a
real but narrow gap: it needs the sender's clock ahead by more than about 6 s, sustained max-rate traffic and a
captured delivery, and the worst outcome is one extra run through the normal guards. It does contradict the
module's own N3 guarantee, so fix it before the `T-Se4Bk5` clean-tree gate re-runs. If the owner wants "Done" to
mean "no open security findings in the module", then this task is **not Done until W-1 lands**. The fix is one
line plus a test. L-1/L-2/D-1 are documentation-level. S-7 (persistent nonces) stays an accepted limitation
pending its ADR.
