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
