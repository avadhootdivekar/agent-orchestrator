# STATUS

- ID: `T-Wh9Kv1-webhook-ingress`
- Updated At: 2026-11-11
- State: Partial — all ACs met incl. AC1 combined auth test and AC7/AC8 github-scheme replay gate; only persistent nonces (T-Se4Bk5 / ADR) open
- Owner: developer agent

## Reconciliation 2026-11-11 (By: developer · Role: developer · unit `w24-07-a1-task-status-sync` · docs only)
- Checked against branch `ad/enhancements-10nov` at HEAD `1d5c6f9`. Any older line below that says "not started", "uncommitted" or "in the working tree" is superseded by this block where they disagree.
- State: **Partial — core + CLI flags delivered; github-scheme replay bound and combined-auth test open**
- Met: AC1-6, 9-16 incl. AC15/AC15b (`ao schedule add --webhook`, daemon `--once` leg); two security reviews fixed (H1/M1-M3/N1-N6); real-socket listener contract test.
- **Unmet / open**: **AC7/AC8** for the `github` scheme (no time-bounded replay defence; nonce cache is in memory and resets on restart - T-Se4Bk5 S-7, accepted limitation, persistent nonces not built); **AC1** combined hub-auth + webhook test (only structural; SECURITY.md has a written confirmation, no test).
- Non-AC: L4 registry/schedules mtime caching, `webhook.expired` event on TTL purge. Header "two items blocked on T-Cl6Jn9" is stale (T-Cl6Jn9 landed).

## This update
- `service/webhook.py` landed: `build_webhook_app` (SecurityMiddleware mounted; only `POST /hooks/{slug}/{id}` + `GET /healthz`), `verify_delivery` (constant-time HMAC over raw bytes, X-AO/X-Hub headers, optional timestamp window), `resolve_secret` (env / workspace-guarded 0600 file), `NonceCache`, `TokenBucket`, `WebhookRequestQueue` (fsync'd `requests/*.json`, bounded per schedule), `WebhookSource` (observe/commit), `make_binding_lookup`.
- `service/cli.py`: `NotImplementedError` guard replaced by `build_service_webhook_app`; `build_schedule_engine` registers the `webhook` TriggerSource.
- Not done: `ao schedule add --webhook/--secret-env/--secret-file/--rate-per-minute` and the `ao schedule daemon --once` leg of AC15 -- no `ao schedule` command exists yet (T-Cl6Jn9). AC15 is covered by a second `ScheduleEngine` instance reading the persisted request (recording launcher, no real run dir).
- Not done: AC "correctly signed request succeeds with dashboard auth enabled" is asserted only structurally (the app has no AuthMiddleware, no cookie needed); no combined hub+webhook auth test.
- Behaviour note: SecurityMiddleware requires `Content-Type: application/json` on body POSTs, so GitHub webhooks must use the JSON content type (documented in the module docstring; T-Dc6Zr2 should carry it).

## Evidence
- `tests/service/test_webhook.py` (33 tests) + updated `test_service_schedule_integration.py`; `pytest tests/service` 539 passed; coverage `service/webhook.py` 95%; ruff check/format clean; `mypy src` clean.

## Risks / Blockers
- Remaining work blocked on `T-Cl6Jn9` (the `ao schedule` CLI).

## Next actions
1. After T-Cl6Jn9: add the `ao schedule add` webhook flags and the daemon `--once` e2e (AC15).
2. Hand the threat model and the accepted limitations (in-memory nonce/bucket reset on restart; hub on the same machine is still unauthenticated) to `T-Se4Bk5` for audit.

## Update 2026-11-11 — security review fixes (w15-02 findings H1, M1-M3, L1-L7)
- **H1** replay bypass: `X-AO-Signature` now signs `"{ts}.{body}"` and *requires* `X-AO-Timestamp` (breaking for `X-AO-*` senders; `compute_signature(secret, body, timestamp=None)`); nonces are per `(workspace, schedule)` and cover the sha256 of the signed material (`VerifiedDelivery.replay_key`) as well as the unsigned delivery id, so a forged delivery id cannot replay a captured delivery. GitHub form stays body-only, defended by the body-hash nonce (in-memory: restart reopens it; recorded in the module's accepted limitations). Note: the nonce hashes `ts.body` (not bare `body`) for X-AO so an identical body re-signed with a new timestamp is a legitimate new delivery.
- **M1** `WebhookSource.observe` sets `discriminator=delivery_id` and `skip_catch_up=True` (same-second deliveries both fire; late drain still fires). No engine/watch edit needed.
- **M2** `webhook_uvicorn_kwargs()` (`limit_concurrency=64`, `timeout_keep_alive=5`) applied in `service/cli.py`; body read wrapped in `asyncio.timeout(WEBHOOK_BODY_READ_TIMEOUT_SECONDS)` -> 408. Header-phase slowloris still needs the reverse proxy (documented).
- **M3** `AO_WEBHOOK_ALLOWED_HOSTS` (comma-separated, no `*`), independent of `AO_UI_ALLOWED_HOSTS`; `NON_LOOPBACK_WARNING` names it (+ L6 wording: HMAC independent of dashboard auth, reverse proxy). Registry setting `schedules.webhook.allowed_hosts` not added (registry model not touched): T-Dc6Zr2 to document the env var and the JSON content-type requirement.
- **Lows**: L1 store load fails closed on any exception (timing oracle between unknown slug / unknown schedule and the 503 existence oracle recorded as accepted); L2 oversized/negative timestamp -> 401; L3 `MIN_WEBHOOK_SECRET_BYTES=16` -> 503 `secret_too_short`; L4 blocking lookup/enqueue run in a worker thread (mtime caching of registry/schedules NOT done -> follow-up); L5 `WEBHOOK_REQUEST_TTL_SECONDS` (24h) ignored in `pending()` and purged on enqueue; L6 warning text; L7 per-schedule nonce scope (global LRU churn by a secret holder recorded as accepted).
- Not done / follow-ups: AC1 combined hub-auth + webhook test, real-uvicorn stalled-socket integration test, `ao schedule add --webhook ...` flags (AC15b), L4 caching.
- Evidence: `tests/service/test_webhook.py` 52 tests + `test_webhook_cli_wiring.py` 2; 13 mutation checks, all killed (M2 kwargs mutation re-checked by the CLI wiring test); `webhook.py` coverage 95%; ruff + `mypy src` clean (tests/service mypy: no errors in own files).
  By: developer · Role: developer · Date: 2026-11-11

## Update 2026-11-11 — re-review fixes (w18-02 Opus re-review: N1-N6) + AC15/AC15b (w19-01)
- **N1 (High)** scheme downgrade: new `WebhookSpec.scheme` (`ao` default | `github`, in `specs/schedules.schema.json` + HLD example). `verify_delivery(..., scheme)` accepts only that scheme's header and answers 401 `wrong_scheme` when the *other* signature header is present at all, so a captured `X-AO-Signature` can no longer be re-sent in GitHub form over `"{ts}." + body` (probes D1/D1b). Default `ao` is the timestamp-authenticated scheme; GitHub senders must now set `scheme: github` (breaking for existing github bindings; feature unreleased). Module "accepted limitations" rewritten: `ao` replay bound = `REPLAY_WINDOW_SECONDS`; `github` has **no** time bound (nonce only).
- **N2 (Medium)** idle-socket lockout: `webhook_http_protocol()` (uvicorn h11 subclass) closes a connection whose headers are not complete `WEBHOOK_HEADER_TIMEOUT_SECONDS` (10 s) after open and after each keep-alive response; `limit_concurrency` raised 64 -> 256. Residual (documented, in the non-loopback warning): a client that continuously re-opens `WEBHOOK_LIMIT_CONCURRENCY` sockets can still starve the listener, so a non-loopback bind **requires** a reverse proxy with per-client connection limits. Real-uvicorn tests: `tests/service/test_webhook_listener.py` (idle + header-trickle sockets closed; LIMIT-1 idle sockets no longer 503 after the deadline; prompt request not cut off, keep-alive re-armed).
- **N3 (Medium)** nonce churn: nonce caches are per `(workspace, schedule)` and sized `nonce_capacity(rate)` = every nonce the token bucket can admit inside the replay window, so other schedules' (or the same schedule's legitimate) traffic cannot evict an `ao`-window entry. **Not done / handed to T-Se4Bk5:** persisting nonces (restart still reopens: <= 5 min for `ao`, unbounded for `github`) needs an ADR.
- **N4 (Low)** fallback id/discriminator is now `replay_key` (hash of `ts.body`) instead of the body hash, so same-body deliveries re-signed in the same second no longer share a fire key (probe C1: 2 accepted/1 launched -> 2 launched). A >128-char delivery id is stored as its sha256.
- **N5 (Low)** `secret_env` naming an ambient variable (`PATH`, `HOME`, `USER`, `LANG`, ... and `LC_*`/`XDG_*`/`SYSTEMD_*`/`DBUS_*`/`SSH_*`) -> 503 `secret_ambient_env`. A mandatory `AO_HOOK_` prefix was rejected as needlessly breaking; deny-list chosen.
- **N6 (Low)** a registry/`schedules.yaml` read failure emits one `webhook.lookup_failed` (`source`, exception class only) before failing closed (same 401).
- **Suggestions**: rate fallback literal removed (uses `spec.rate_per_minute`); delivery-id clipping done. NOT done (recorded): `fire_store.fire_key` docstring wording (file has another unit's uncommitted edits), `webhook.expired` event on TTL purge, L4 mtime caching.
- **AC15b** `ao schedule add ID --webhook (--secret-env NAME | --secret-file PATH) [--rate-per-minute N] [--webhook-scheme ao|github] --workflow/--template ...` (`schedules/cli.py`); bad combinations are usage errors.
- **AC15** `tests/schedules/test_webhook_cli_e2e.py`: `ao schedule add --webhook` -> signed POST through the real listener app with the real registry lookup -> `ao schedule daemon --once` -> exactly one fire intent and a real run directory, request file consumed, second tick fires 0, replay 409; a bad signature never reaches the daemon (all through `CliRunner`, fixed clock).
- Evidence: `test_webhook.py` 67 tests (15 new, each mutation-checked: scheme pin, fallback discriminator, ambient deny-list, lookup log, per-schedule nonce scope, id digest, and header-deadline timer all killed), `test_webhook_listener.py` 3, `test_webhook_cli_e2e.py` 10, wiring tests updated; `tests/service tests/schedules`: 1142 passed, 3 failed (1 pre-existing/other-unit: `test_fire_store::test_hld_event_table_is_complete` for `schedule.governance.resumed|pause_expired|resume_refused` from w18-01's uncommitted work; the other 2 were mine and are fixed: stubbed-uvicorn fixture now preloads the real h11 module). `ruff check .`, `mypy src` clean.
- Still open: AC1 combined hub-auth + webhook test; persistent nonces (T-Se4Bk5); T-Dc6Zr2 must document `scheme`, `AO_WEBHOOK_ALLOWED_HOSTS`, JSON content type, and the proxy requirement.
  By: developer · Role: developer · Date: 2026-11-11

## Update 2026-11-11 — AC7/AC8 github-scheme replay gate + AC1 combined auth test (w26-06)
- **AC7 (github scheme)**: GitHub signs the body only and sends no timestamp, so there is still no *signed* time bound. `verify_delivery` now applies the AC7 window to an **optional** `X-AO-Timestamp` on `scheme: github` bindings: present + malformed -> 401 `bad_timestamp`; present + `abs(now-ts) > REPLAY_WINDOW_SECONDS` -> 401 `stale`; absent -> accepted (GitHub's normal form). It is unsigned (not in the HMAC), so it is a gate for stamping senders/proxies only and is checked *after* the HMAC (no oracle). Module "accepted limitations" docstring updated to say exactly this.
- **AC8 (github scheme)**: unchanged mechanism, now pinned by tests: replay with a forged delivery id + fresh stamp is still 409 (body-hash nonce); absent timestamp -> 202 then 409 on replay.
- **AC1**: `test_ac1_signed_webhook_succeeds_while_hub_auth_is_enforced` builds the real auth-enabled hub (`tests/auth/helpers/hub.make_hub`): anonymous hub request denied, the webhook app accepts a signed delivery with no cookie, rejects a badly signed one, and carries no `AuthMiddleware`.
- **Still open (not built, deliberately)**: persistent nonces — the github scheme has no *signed* replay bound, so a restart (or LRU eviction) still reopens replay of a captured delivery; needs an ADR (T-Se4Bk5 S-7). Non-AC: L4 mtime caching, `webhook.expired` event.
- Evidence: 9 new tests in `tests/service/test_webhook.py` (stale/future/malformed stamp, in-window, absent, forged-id replay, HMAC-before-window ordering, ao-window edge, AC1); mutation (github stamp ignored) killed 4 tests. `test_webhook*.py` + `test_webhook_cli_e2e.py`: 109 passed. `ruff check`/`format --check` clean; `mypy src` clean (233 files).
  By: developer · Role: developer · Date: 2026-11-11
