# STATUS

- ID: `T-Wh9Kv1-webhook-ingress`
- Updated At: 2026-11-11
- State: Partial (core implemented, awaiting commit review; two items blocked on T-Cl6Jn9)
- Owner: developer agent

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
