# TASK: T-Wh9Kv1-webhook-ingress

## Metadata
- Task ID: `T-Wh9Kv1-webhook-ingress`
- Epic ID: `E-Sc9Rt4-scheduler-triggers`
- Owner: developer agent (security-sensitive — `T-Se4Bk5` audits this module specifically)
- Created: 2026-09-06
- Last Updated: 2026-09-06
- Status: Draft
- Estimate: 3 days

## Requirements Mapping
- Requirement IDs: FR-12, NFR-3 (see `../EPIC.md`)

## Description
The second event trigger, and **the project's first authenticated HTTP endpoint**: a dedicated,
default-off listener that accepts HMAC-signed webhook deliveries (e.g. a git push) and enqueues
them for the engine to fire through its normal policy path.

Everything else in this project is unauthenticated because ADR-0010 D7's threat model is "one
developer, loopback only". A webhook's entire purpose is to be reachable from off-box, which
invalidates that premise for this one route — hence the HMAC. It is deliberately **not** a
down-payment on the authentication epic (roadmap §3.1): there is no identity, no session, no
authorization model.

Read HLD §8 and §13, and ADR-0014 D8. Read `ui/security.py` in full — in particular note
ADR-0012's early-gate correction #6: *importing the allow-list constants without attaching the
middleware to the ASGI app enforces nothing.*

Files you own (create/edit freely):
- `src/agent_orchestrator/service/webhook.py` (new — verification, secret resolution, nonce
  cache, rate limiter, delivery queue, and `build_webhook_app`)
- `src/agent_orchestrator/scheduler.py` (edit: add `WebhookScheduler`)
- `tests/service/test_webhook.py` (new)
- additive: remove the `NotImplementedError` guard `T-Sv5Hb3` left behind `--webhook`
- additive: `--webhook`, `--secret-env`, `--secret-file`, `--rate-per-minute` on
  `ao schedule add`

Do NOT touch: `service/hub.py`, `ui/security.py`, `ui/app.py`, `service/schedule_engine.py`
(beyond the one-line `SCHEDULERS` registration), `schedules/models.py`.

## Acceptance Criteria

### The app
1. `build_webhook_app(binding_lookup, queue, *, clock, secret_resolver) -> FastAPI` exposes
   **only** `POST /hooks/{workspace_slug}/{schedule_id}` and `GET /healthz`. `fastapi` is
   imported **inside the function body**, never at module scope, so a core install without the
   `[ui]` extra can still import this module (the rule `service/hub.py` documents).
2. The app calls `app.add_middleware(SecurityMiddleware, allowed_hosts=resolve_allowed_hosts(
   bound_host=...))` — **mounted, not merely imported**. A test asserts a disallowed `Host`
   header returns 421 through a real `TestClient`, not that a constant was imported.
3. Default off. `ao service run` opens no socket for it unless `--webhook` (or the registry
   block) enables it; binding a non-loopback host prints the same loud UNAUTHENTICATED-adjacent
   warning `ao ui --host` prints, adapted to say that the endpoint is authenticated but the
   surface is newly reachable.

### Verification (fail closed, uniform failures)
4. Verification order is exactly HLD §8.2's: enabled → body size ≤ `MAX_WEBHOOK_BODY_BYTES`
   (64 KiB) → resolve binding + secret → HMAC → timestamp window → nonce → rate limit → enqueue.
   Each step's failure has a distinct log `reason` but the **HTTP responses are deliberately
   coarse**: `404` only when webhooks are disabled entirely, `413` oversize, `503` when a
   configured secret cannot be resolved, `401` for *both* unknown-schedule and bad-signature,
   `409` duplicate delivery, `429` rate-limited, `202` accepted.
5. **Unknown schedule and bad signature both return 401**, so the endpoint is not a free oracle
   for enumerating a machine's workspaces and schedule ids. A test asserts the two responses are
   indistinguishable in status and body.
6. HMAC is SHA-256 over the **raw** body bytes (never a re-serialized parse), compared with
   `hmac.compare_digest`. Both `X-AO-Signature: sha256=<hex>` and GitHub's
   `X-Hub-Signature-256: sha256=<hex>` are accepted, so a stock GitHub push webhook works with no
   adapter.
7. `X-AO-Timestamp` is **optional** (GitHub sends none); when present, `abs(now - ts) >
   REPLAY_WINDOW_SECONDS (300)` is a 401. When absent, the nonce cache is the only replay
   defence, which is why AC8 is mandatory rather than best-effort.
8. Delivery id from `X-AO-Delivery` / `X-GitHub-Delivery`, falling back to `sha256(body)`. A
   bounded LRU (`NONCE_CACHE_SIZE = 1024`) rejects a repeat with 409.
9. Per-`(workspace, schedule)` token bucket, default `rate_per_minute = 10`, returning 429. The
   bucket is in-memory and resets on restart — documented, acceptable, and stated in the
   docstring.
10. Secret resolution: `secret_env: NAME` reads `os.environ`; `secret_file: PATH` resolves
    through the workspace-root guard and **refuses a file whose mode is group- or
    world-readable** (503 + `webhook.secret_permissions`), matching the ssh-key convention. An
    inline `secret:` key was already rejected at load time by `T-Sd1Kq7`; assert that here as a
    characterization test so a future relaxation there fails loudly.
11. **No log line, error message, or response body ever contains the secret, the expected
    signature, or the received signature.** A dedicated test captures all log output across every
    rejection path and asserts none of the three appear.

### Enqueue and fire
12. A verified delivery is **only enqueued** (a thread-safe `queue.Queue` of `WebhookDelivery`,
    bounded at `WEBHOOK_QUEUE_MAXSIZE`; a full queue returns 429). The fire happens on the next
    `ScheduleEngine.tick()` through the identical overlap / concurrency / `until` path as a cron
    fire — so a delivery flood costs one queue entry, not one agent run. A test asserts a burst
    of 50 verified deliveries against an overlapping schedule produces at most one launch.
13. `WebhookScheduler(Scheduler).next_fire(trigger, now)` returns the receipt time of the oldest
    pending delivery for that schedule, or `None`. `scheduled_for` is that receipt time truncated
    to whole seconds, so the fire key is stable across a crash within the same second.

### Tests
14. One test per rejection case: valid HMAC, wrong secret, missing signature header, GitHub
    header form, oversized body, stale timestamp, duplicate delivery id, rate-limited, unknown
    schedule, unresolvable secret, group-readable secret file. Plus AC2's real 421 test, AC5's
    indistinguishability test, AC11's log-scrubbing test, and AC12's flood test.
15. E2E: `TestClient` posts a correctly-signed delivery, then `ao schedule daemon --once` fires
    it and a run directory appears; a second identical delivery is 409 and produces no second
    run.
16. `uv run pytest -q` green with the delta reported; ruff + format clean; mypy whole-tree count
    reported; coverage ≥90 % on `service/webhook.py` (higher than the epic's 80 % floor — this is
    the security-critical module).

## Risks
- **Mounting vs importing `SecurityMiddleware`** is a mistake this project has already made once
  (ADR-0012 early-gate correction #6). AC2's test must go through a real `TestClient`.
- **Timing-safe comparison**: a `==` on the signature is a real, exploitable side channel. Use
  `hmac.compare_digest` and have `T-Se4Bk5` grep for `==` on signature values.
- **Leaking secrets into logs** is the most common way an HMAC implementation fails in practice.
  AC11 exists for that and is a blocking criterion.
- The in-memory nonce cache and rate bucket reset on restart, so a restart briefly reopens a
  replay window for deliveries within the timestamp window. Documented limitation; do not paper
  over it with a persisted cache without an ADR.
- Reaching this endpoint from a git host requires exposing a port. The docs must recommend a
  TLS-terminating reverse proxy and warn that direct exposure means an unauthenticated hub may be
  on the same machine.

## Dependencies
- `T-Sd1Kq7` (`WebhookSpec`), `T-Ev3Qm5` (engine + scheduler map), `T-Sv5Hb3` (the flags and the
  server thread this fills in), `T-Cl6Jn9` (the `add` command this extends).
- Reads (read-only): `ui/security.py` (`SecurityMiddleware`, `resolve_allowed_hosts`,
  `DEFAULT_ALLOWED_HOSTS`), `service/hub.py` (the lazy-import + middleware-mounting pattern).

## Pseudocode / Algorithm
```text
See HLD §8.2 for the normative handler. Three invariants that must survive any refactor:

  * HMAC is computed over the RAW request body bytes, never over a parsed-and-re-serialized form.
  * Unknown-schedule and bad-signature produce byte-identical 401 responses.
  * A verified delivery ENQUEUES; it never launches. All policy lives in ScheduleEngine.evaluate.

FUNCTION resolve_secret(binding, workspace_root) -> bytes | None:
  IF binding.webhook.secret_env:
      RETURN os.environ.get(binding.webhook.secret_env, "").encode() OR None
  IF binding.webhook.secret_file:
      p = safe_join(workspace_root, binding.webhook.secret_file)
      IF NOT p.is_file(): RETURN None
      IF p.stat().st_mode & 0o077:                       # group/world readable
          emit("webhook.rejected", reason="secret_permissions");  RETURN None
      RETURN p.read_bytes().strip()
  RETURN None
```

## Schemas / Interface Notes
- Interface / HTTP: `POST /hooks/{workspace_slug}/{schedule_id}` (202/401/404/409/413/429/503),
  `GET /healthz`. Headers: `X-AO-Signature` | `X-Hub-Signature-256`, `X-AO-Timestamp` (optional),
  `X-AO-Delivery` | `X-GitHub-Delivery` (optional).
- Interface / API: `service.webhook.{build_webhook_app, verify_delivery, resolve_secret,
  WebhookDelivery, NonceCache, TokenBucket, WebhookAuthError}`; `scheduler.WebhookScheduler`.
- Spec / data schema: consumes `WebhookSpec` (`secret_env` | `secret_file`, `rate_per_minute`).
- Triggers / events: implements the `webhook` kind; emits `webhook.received` and
  `webhook.rejected` (with a reason, never with secret material).
- Artifacts: none written; deliveries live in memory until the next tick.

## Handoff Boundary
- Upstream: `T-Sd1Kq7`, `T-Ev3Qm5`, `T-Sv5Hb3`, `T-Cl6Jn9`; HLD §8/§13; ADR-0014 D8; ADR-0010 D7.
- Downstream: `T-Se4Bk5` (audits this module specifically — hand it the threat model and the list
  of accepted limitations), `T-Te3Qw8` (e2e tier), `T-Dc6Zr2` (documents the reverse-proxy
  recommendation).

## Artifacts
- Docs/comments: `meta/tickets/E-Sc9Rt4-scheduler-triggers/T-Wh9Kv1-webhook-ingress/`
- Large outputs: N/A
