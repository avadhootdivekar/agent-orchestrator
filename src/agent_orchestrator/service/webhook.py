"""Webhook ingress (``T-Wh9Kv1``, HLD §8): the project's first authenticated HTTP endpoint.

A dedicated, default-off listener (``ao service run --webhook``) that accepts HMAC-signed
deliveries (e.g. a git push) and **persists** each verified one as an fsync'd
``<state_dir>/schedules/requests/<uuid>.json`` request (``kind: webhook``, F14). The fire happens
on the next ``ScheduleEngine`` tick through :class:`WebhookSource` -- the identical overlap /
concurrency / ``until`` path as a cron fire -- so a delivery flood costs request files, never
agent runs, and an acknowledged (202) delivery survives a crash.

Invariants that must survive any refactor
-----------------------------------------
* HMAC is SHA-256 over the **raw** body bytes, compared with :func:`hmac.compare_digest`.
* Unknown schedule and bad signature produce byte-identical 401 responses.
* A verified delivery enqueues; it never launches.
* No log line, error message or response body contains the secret, the expected signature or
  the received signature.

Accepted limitations (hand-off to ``T-Se4Bk5``)
-----------------------------------------------
* Replay defence. The ``X-AO-Signature`` scheme signs ``"{timestamp}.{body}"`` (the timestamp is
  required and authenticated); GitHub's ``X-Hub-Signature-256`` signs the body only. Both are
  de-duplicated on a per-schedule hash of the *signed material*, so a captured delivery cannot be
  replayed by changing the (unsigned) delivery-id header; the delivery id is a second nonce.
* The nonce cache and per-schedule token bucket are **in memory** and reset on restart, so a
  restart briefly reopens a replay window: for ``X-AO-*`` senders only inside
  ``REPLAY_WINDOW_SECONDS``, for GitHub-style senders (no timestamp) for any previously captured
  delivery. Persisting them needs an ADR. The nonce LRU is global (bounded), so a holder of one
  schedule's secret can evict other schedules' entries within the rate limits.
* An unknown workspace slug answers slightly faster than a known slug with an unknown schedule
  (a timing oracle on guessable slugs), and 503 ``secret_unresolvable`` reveals that the schedule
  exists. Both are accepted; registry/schedule lookups are not cached (every request re-reads
  ``schedules.yaml`` in a worker thread).
* Slow-client defence: ``limit_concurrency`` and a body read deadline bound the body phase, but
  uvicorn has no header-phase timeout, so a slowloris on the *headers* needs the reverse proxy.
* ``X-AO-Signature`` senders must send ``X-AO-Timestamp`` (breaking change vs. the first cut).
* ``SecurityMiddleware`` is mounted (Host allow-list, Origin check, JSON ``Content-Type`` on a
  body-carrying POST), so senders must post ``application/json`` (GitHub: set the webhook's
  content type to ``application/json``, not the form-encoded default).
* Direct off-loopback exposure should sit behind a TLS-terminating reverse proxy; a hub with
  auth disabled may be on the same machine. A non-loopback bind needs the Host names callers use
  in ``AO_WEBHOOK_ALLOWED_HOSTS`` (comma-separated; independent of ``AO_UI_ALLOWED_HOSTS``).
* ``fastapi`` is imported inside :func:`build_webhook_app` only, so a core install without the
  ``[ui]`` extra can still import this module (same rule as ``service/hub.py``).
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import threading
import uuid
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..schedules.store import ScheduleStore
from .registry import ServiceRegistry
from .schedule_engine import (
    REQUESTS_SUBDIR,
    Candidate,
    ResolvedBinding,
)
from .supervisor import _slug_for_root

if TYPE_CHECKING:
    from fastapi import FastAPI

    from .fire_store import ScheduleState

logger = logging.getLogger(__name__)

MAX_WEBHOOK_BODY_BYTES = 64 * 1024
REPLAY_WINDOW_SECONDS = 300
NONCE_CACHE_SIZE = 1024
WEBHOOK_QUEUE_MAXSIZE = 64  # pending ``webhook`` request files per schedule; beyond -> 429
REQUEST_KIND_WEBHOOK = "webhook"
SCHEDULES_SUBDIR = "schedules"
SIGNATURE_HEADERS = ("x-ao-signature", "x-hub-signature-256")
DELIVERY_HEADERS = ("x-ao-delivery", "x-github-delivery")
TIMESTAMP_HEADER = "x-ao-timestamp"
SIGNATURE_PREFIX = "sha256="
MAX_LOGGED_FIELD = 128
MIN_WEBHOOK_SECRET_BYTES = 16  # shorter keys (e.g. ``secret_env: HOME``) are refused -> 503
WEBHOOK_REQUEST_TTL_SECONDS = 24 * 3600  # a persisted request older than this is dropped (L5)
# Listener bounds (M2): uvicorn has no read timeouts of its own.
WEBHOOK_LIMIT_CONCURRENCY = 64
WEBHOOK_TIMEOUT_KEEP_ALIVE_SECONDS = 5
WEBHOOK_BODY_READ_TIMEOUT_SECONDS = 10.0
WEBHOOK_ALLOWED_HOSTS_ENV = "AO_WEBHOOK_ALLOWED_HOSTS"
NON_LOOPBACK_WARNING = (
    "WARNING: webhook listener binding non-loopback host {host!r} is newly reachable off-box. It "
    "is authenticated by HMAC only, independently of dashboard auth. Put it behind a "
    "TLS-terminating reverse proxy, and set " + WEBHOOK_ALLOWED_HOSTS_ENV + " to the Host names "
    "callers use (otherwise every off-box delivery is refused with 421)."
)

Clock = Callable[[], datetime]
Emit = Callable[..., None]


# -- errors / value types ------------------------------------------------------------------------


class WebhookAuthError(Exception):
    """A delivery was rejected. ``reason`` is for the log only; ``status`` is the (coarse) HTTP
    status. The message never carries secret material."""

    def __init__(self, reason: str, status: int) -> None:
        super().__init__(reason)
        self.reason = reason
        self.status = status


class WebhookQueueFullError(Exception):
    """Too many pending webhook requests for one schedule (``WEBHOOK_QUEUE_MAXSIZE``)."""


@dataclass(frozen=True)
class WebhookTarget:
    """What :func:`build_webhook_app`'s ``binding_lookup`` resolves a URL to."""

    workspace_root: str
    binding: ResolvedBinding


@dataclass(frozen=True)
class WebhookDelivery:
    """A verified delivery, as persisted."""

    workspace_slug: str
    schedule_id: str
    delivery_id: str
    received_at: datetime


# -- helpers -------------------------------------------------------------------------------------


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _clip(value: str) -> str:
    """Attacker-controlled text for a log line: bounded and without control characters."""
    return "".join(c if c.isprintable() else "?" for c in value[:MAX_LOGGED_FIELD])


def _default_emit(event: str, **fields: Any) -> None:
    level = logging.INFO if event == "webhook.received" else logging.WARNING
    logger.log(level, "%s %s", event, json.dumps(fields, default=str, sort_keys=True))


def _fsync_dir(path: Path) -> None:
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _requests_dir(state_dir: Path) -> Path:
    return Path(state_dir) / SCHEDULES_SUBDIR / REQUESTS_SUBDIR


def _parse_received(raw: object) -> datetime | None:
    if not isinstance(raw, str):
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


# -- secrets -------------------------------------------------------------------------------------


def resolve_secret(
    target: WebhookTarget, *, emit: Emit = _default_emit, env: Mapping[str, str] | None = None
) -> bytes | None:
    """The HMAC key for *target*, or ``None`` when it cannot be resolved (-> 503).

    ``secret_env`` reads the environment; ``secret_file`` is resolved against the workspace root
    (a path that escapes it, e.g. through a symlink, is refused) and a group/world-readable file
    is refused (``webhook.secret_permissions``), matching the ssh-key convention. Never logs or
    returns anything but the key itself.
    """
    spec = target.binding.webhook
    if spec is None:
        return None
    env = os.environ if env is None else env
    sid = _clip(target.binding.id)
    if spec.secret_env:
        return _checked_secret(env.get(spec.secret_env, "").encode("utf-8"), sid, emit)
    if spec.secret_file:
        try:
            root = Path(target.workspace_root).resolve()
            path = (root / spec.secret_file).resolve()
            if path != root and root not in path.parents:
                emit("webhook.rejected", reason="secret_outside_workspace", schedule_id=sid)
                return None
            if not path.is_file():
                return None
            if path.stat().st_mode & 0o077:
                emit("webhook.secret_permissions", reason="secret_permissions", schedule_id=sid)
                return None
            return _checked_secret(path.read_bytes().strip(), sid, emit)
        except OSError:
            return None
    return None


def _checked_secret(value: bytes, sid: str, emit: Emit) -> bytes | None:
    """*value*, or ``None`` when empty or shorter than ``MIN_WEBHOOK_SECRET_BYTES`` (L3)."""
    if not value:
        return None
    if len(value) < MIN_WEBHOOK_SECRET_BYTES:
        emit("webhook.rejected", reason="secret_too_short", schedule_id=sid)
        return None
    return value


def resolve_webhook_allowed_hosts(
    bound_host: str | None, env: Mapping[str, str] | None = None
) -> frozenset[str] | None:
    """Host allow-list for the webhook listener: loopback + the bound host +
    ``AO_WEBHOOK_ALLOWED_HOSTS`` (comma-separated). Deliberately ignores ``AO_UI_ALLOWED_HOSTS``
    (so opening the webhook never widens the dashboards) and has no ``*`` escape hatch."""
    from ..ui.security import AO_UI_ALLOWED_HOSTS_ENV, resolve_allowed_hosts

    env = os.environ if env is None else env
    extra = ",".join(
        p for p in env.get(WEBHOOK_ALLOWED_HOSTS_ENV, "").split(",") if p.strip() != "*"
    )
    return resolve_allowed_hosts(bound_host=bound_host, env={AO_UI_ALLOWED_HOSTS_ENV: extra})


def webhook_uvicorn_kwargs() -> dict[str, Any]:
    """Slow-client / connection bounds for the webhook ``uvicorn.Config`` (M2)."""
    return {
        "limit_concurrency": WEBHOOK_LIMIT_CONCURRENCY,
        "timeout_keep_alive": WEBHOOK_TIMEOUT_KEEP_ALIVE_SECONDS,
    }


# -- verification --------------------------------------------------------------------------------


class NonceCache:
    """Bounded LRU of delivery ids (replay defence). Thread-safe; in-memory (see module doc)."""

    def __init__(self, maxsize: int = NONCE_CACHE_SIZE) -> None:
        self._maxsize = maxsize
        self._seen: OrderedDict[str, None] = OrderedDict()
        self._lock = threading.Lock()

    def __contains__(self, key: object) -> bool:
        with self._lock:
            return key in self._seen

    def add(self, key: str) -> None:
        with self._lock:
            self._seen[key] = None
            self._seen.move_to_end(key)
            while len(self._seen) > self._maxsize:
                self._seen.popitem(last=False)

    def __len__(self) -> int:
        with self._lock:
            return len(self._seen)


class TokenBucket:
    """Per-key token bucket with an injected clock. Capacity == ``rate_per_minute`` (a burst of
    that size is allowed, then one token per ``60/rate`` seconds). In memory: resets on restart."""

    def __init__(self, clock: Clock = _utc_now) -> None:
        self._clock = clock
        self._state: dict[tuple[str, str], tuple[float, float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: tuple[str, str], rate_per_minute: int) -> bool:
        now = self._clock().timestamp()
        with self._lock:
            tokens, last = self._state.get(key, (float(rate_per_minute), now))
            tokens = min(
                float(rate_per_minute), tokens + max(0.0, now - last) * rate_per_minute / 60
            )
            if tokens < 1.0:
                self._state[key] = (tokens, now)
                return False
            self._state[key] = (tokens - 1.0, now)
            return True


def compute_signature(secret: bytes, body: bytes, timestamp: str | None = None) -> str:
    """``sha256=<hex>``: over the raw body (GitHub form) or, with *timestamp*, over
    ``"{timestamp}.{body}"`` (the ``X-AO-Signature`` form: the timestamp is authenticated)."""
    message = body if timestamp is None else timestamp.encode("ascii") + b"." + body
    return SIGNATURE_PREFIX + hmac.new(secret, message, hashlib.sha256).hexdigest()


def _header(headers: Mapping[str, str], names: tuple[str, ...]) -> str | None:
    for name in names:
        value = headers.get(name)
        if value is not None:
            return value
    return None


@dataclass(frozen=True)
class VerifiedDelivery:
    """A delivery whose HMAC (and, for ``X-AO-*``, timestamp) checked out."""

    delivery_id: str
    # False when the sender supplied no delivery id (``delivery_id`` is then a body hash and adds
    # no nonce beyond ``replay_key``).
    id_supplied: bool
    # sha256 of the signed material: stable across a replay whatever headers the replayer sets.
    replay_key: str


def verify_delivery(
    headers: Mapping[str, str], body: bytes, secret: bytes, now: datetime
) -> VerifiedDelivery:
    """HMAC + timestamp checks. Raises :class:`WebhookAuthError` (401).

    *headers* must be case-insensitive (Starlette's are) or already lower-cased. Signatures are
    compared with :func:`hmac.compare_digest`. ``X-AO-Signature`` signs ``"{ts}.{body}"`` and
    requires ``X-AO-Timestamp`` within ``REPLAY_WINDOW_SECONDS``; ``X-Hub-Signature-256`` signs
    the body only (GitHub). The delivery-id header is *not* signed, so replay protection keys on
    :attr:`VerifiedDelivery.replay_key`, never on the id alone (H1).
    """
    x_ao = headers.get(SIGNATURE_HEADERS[0])
    received = x_ao if x_ao is not None else headers.get(SIGNATURE_HEADERS[1])
    stamp_raw: str | None = None
    if x_ao is not None:
        stamp_raw = (headers.get(TIMESTAMP_HEADER) or "").strip()
        if not stamp_raw.isascii() or not stamp_raw.isdigit():
            # checked before the HMAC only for shape; a forged stamp still fails the signature
            raise WebhookAuthError("bad_timestamp", 401)
    expected = compute_signature(secret, body, stamp_raw)
    if received is None or not hmac.compare_digest(
        received.strip().encode("utf-8", "replace"), expected.encode("ascii")
    ):
        raise WebhookAuthError("bad_signature", 401)
    if stamp_raw is not None:
        try:
            skew = abs(now.timestamp() - int(stamp_raw))
        except (ValueError, OverflowError):
            raise WebhookAuthError("bad_timestamp", 401) from None
        if skew > REPLAY_WINDOW_SECONDS:
            raise WebhookAuthError("stale", 401)
    signed = body if stamp_raw is None else stamp_raw.encode("ascii") + b"." + body
    replay_key = hashlib.sha256(signed).hexdigest()
    delivery = _header(headers, DELIVERY_HEADERS)
    delivery_id = delivery.strip() if delivery is not None and delivery.strip() else ""
    return VerifiedDelivery(
        delivery_id or hashlib.sha256(body).hexdigest(), bool(delivery_id), replay_key
    )


# -- persisted queue (F14) -----------------------------------------------------------------------


class WebhookRequestQueue:
    """Writes/reads ``requests/<uuid>.json`` (``kind: webhook``) under the service state dir."""

    def __init__(
        self,
        state_dir: Path,
        *,
        maxsize: int = WEBHOOK_QUEUE_MAXSIZE,
        ttl_seconds: int = WEBHOOK_REQUEST_TTL_SECONDS,
    ) -> None:
        self._dir = _requests_dir(state_dir)
        self._maxsize = maxsize
        self._ttl = timedelta(seconds=ttl_seconds)

    def pending(
        self, workspace_slug: str, schedule_id: str, now: datetime | None = None
    ) -> list[tuple[datetime, Path, str]]:
        """Pending, unexpired requests for one schedule, oldest first:
        ``(received_at, path, delivery_id)``. Requests older than the TTL are ignored (L5: a
        schedule disabled for a while must not fire its stale backlog on re-enable) and are
        deleted by the next :meth:`enqueue`."""
        out: list[tuple[datetime, Path, str]] = []
        if not self._dir.is_dir():
            return out
        cutoff = (now or _utc_now()) - self._ttl
        for path in self._dir.glob("*.json"):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if (
                not isinstance(raw, dict)
                or raw.get("kind") != REQUEST_KIND_WEBHOOK
                or raw.get("workspace_slug") != workspace_slug
                or raw.get("schedule_id") != schedule_id
            ):
                continue
            received = _parse_received(raw.get("received_at"))
            if received is None or received < cutoff:
                continue
            out.append((received, path, str(raw.get("delivery_id", ""))))
        out.sort(key=lambda t: (t[0], t[1].name))
        return out

    def _purge_expired(self, now: datetime) -> None:
        cutoff = now - self._ttl
        for path in self._dir.glob("*.json"):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                received = (
                    _parse_received(raw.get("received_at")) if isinstance(raw, dict) else None
                )
                if (
                    isinstance(raw, dict)
                    and raw.get("kind") == REQUEST_KIND_WEBHOOK
                    and received is not None
                    and received < cutoff
                ):
                    path.unlink(missing_ok=True)
            except (OSError, ValueError):
                continue

    def enqueue(self, delivery: WebhookDelivery) -> Path:
        """Atomically + durably write one request; :class:`WebhookQueueFullError` past the bound."""
        self._dir.mkdir(parents=True, exist_ok=True)
        self._purge_expired(delivery.received_at)
        pending = self.pending(delivery.workspace_slug, delivery.schedule_id, delivery.received_at)
        if len(pending) >= self._maxsize:
            raise WebhookQueueFullError(delivery.schedule_id)
        final = self._dir / f"{uuid.uuid4().hex}.json"
        tmp = final.with_suffix(".tmp")
        payload = {
            "kind": REQUEST_KIND_WEBHOOK,
            "workspace_slug": delivery.workspace_slug,
            "schedule_id": delivery.schedule_id,
            "received_at": delivery.received_at.astimezone(UTC).isoformat(),
            "delivery_id": delivery.delivery_id,
        }
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(payload))
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, final)
        _fsync_dir(self._dir)
        return final


class WebhookSource:
    """Per-binding ``TriggerSource`` for ``kind: webhook`` (F6).

    ``observe`` is pure and read-only: the oldest pending request's receipt time (truncated to
    whole seconds so the fire key is stable across a crash within the same second), or ``None``.
    The candidate carries the delivery id as ``discriminator`` (two deliveries in the same second
    must not share a fire key) and ``skip_catch_up`` (``received_at`` is data, not a due slot, so a
    late drain must not burn it as ``missed``) -- the same two fixes as ``file_watch`` (M1).
    ``commit`` deletes that request file -- the engine calls it only once the fire key is burned
    or launched, so a concurrency skip cannot lose a delivery.
    """

    def __init__(self, state_dir: Path, workspace_root: str, binding: ResolvedBinding) -> None:
        self._queue = WebhookRequestQueue(state_dir)
        self._slug = _slug_for_root(str(Path(workspace_root).resolve()))
        self._schedule_id = binding.id

    def observe(
        self, binding: ResolvedBinding, state: ScheduleState, now: datetime
    ) -> Candidate | None:
        pending = self._queue.pending(self._slug, binding.id, now)
        if not pending:
            return None
        received, path, delivery_id = pending[0]
        return Candidate(
            scheduled_for=received.replace(microsecond=0),
            payload={"delivery_id": delivery_id, "request": path.name},
            discriminator=delivery_id,
            skip_catch_up=True,
        )

    def commit(self, binding: ResolvedBinding, candidate: Candidate) -> None:
        name = (candidate.payload or {}).get("request") if candidate.payload else None
        if not isinstance(name, str) or os.sep in name or name.startswith("."):
            return
        (self._queue._dir / name).unlink(missing_ok=True)


# -- binding lookup ------------------------------------------------------------------------------


def make_binding_lookup(
    registry_path: Path,
    *,
    registry_factory: Callable[[Path], Any] = ServiceRegistry,
    store_factory: Callable[[str], ScheduleStore] = ScheduleStore,
) -> Callable[[str, str], WebhookTarget | None]:
    """``(workspace_slug, schedule_id) -> WebhookTarget | None`` over the live registry: only
    schedule-opted-in workspaces, only enabled ``kind: webhook`` bindings. Anything else is
    ``None`` (-> the same 401 as a bad signature)."""

    def lookup(slug: str, schedule_id: str) -> WebhookTarget | None:
        try:
            entries = registry_factory(registry_path).load().workspaces
        except Exception:  # noqa: BLE001 - corrupt registry: fail closed
            return None
        for entry in entries:
            if not getattr(entry, "schedules", False):
                continue
            if _slug_for_root(str(Path(entry.root).resolve())) != slug:
                continue
            try:
                bindings = store_factory(entry.root).load().bindings
            except Exception:  # noqa: BLE001 - unreadable/invalid schedules.yaml: fail closed
                return None
            for b in bindings:
                if b.id == schedule_id and b.kind == "webhook" and b.enabled and b.webhook:
                    return WebhookTarget(str(Path(entry.root).resolve()), b)
            return None
        return None

    return lookup


# -- the ASGI app --------------------------------------------------------------------------------

_UNAUTHORIZED_BODY = {"detail": "unauthorized"}


def build_webhook_app(
    binding_lookup: Callable[[str, str], WebhookTarget | None],
    queue: WebhookRequestQueue,
    *,
    clock: Clock = _utc_now,
    secret_resolver: Callable[[WebhookTarget], bytes | None] | None = None,
    emit: Emit = _default_emit,
    bound_host: str | None = None,
    enabled: bool = True,
    nonce_cache: NonceCache | None = None,
    rate_limiter: TokenBucket | None = None,
) -> FastAPI:
    """The listener app: exactly ``POST /hooks/{workspace_slug}/{schedule_id}`` and
    ``GET /healthz``. NOT wrapped in the dashboard session ``AuthMiddleware`` (HMAC is its auth,
    F18); ``SecurityMiddleware`` IS mounted (ADR-0012 correction #6).

    Status map (HLD §8.2): 404 disabled, 413 oversize, 408 body read timed out, 503 secret
    unresolvable, 401 unknown schedule *and* bad/stale signature (indistinguishable), 409
    duplicate, 429 rate-limited or queue full, 202 accepted.
    """
    from fastapi import FastAPI, Request
    from fastapi.concurrency import run_in_threadpool
    from fastapi.responses import JSONResponse

    from ..ui.security import SecurityMiddleware

    resolver = secret_resolver or (lambda t: resolve_secret(t, emit=emit))
    nonces = nonce_cache if nonce_cache is not None else NonceCache()
    bucket = rate_limiter if rate_limiter is not None else TokenBucket(clock)
    lock = threading.Lock()

    app = FastAPI(title="ao webhook", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(SecurityMiddleware, allowed_hosts=resolve_webhook_allowed_hosts(bound_host))

    def reject(status: int, reason: str, ws: str, sid: str) -> JSONResponse:
        emit("webhook.rejected", reason=reason, workspace_slug=_clip(ws), schedule_id=_clip(sid))
        if status == 401:
            return JSONResponse(_UNAUTHORIZED_BODY, status_code=401)
        return JSONResponse({"detail": reason}, status_code=status)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    async def hook(workspace_slug: str, schedule_id: str, request: Request) -> JSONResponse:
        ws, sid = workspace_slug, schedule_id
        if not enabled:
            return JSONResponse({"detail": "webhooks disabled"}, status_code=404)

        declared = request.headers.get("content-length")
        if declared is not None and declared.isdigit() and int(declared) > MAX_WEBHOOK_BODY_BYTES:
            return reject(413, "oversize", ws, sid)
        chunks: list[bytes] = []
        total = 0
        try:
            async with asyncio.timeout(WEBHOOK_BODY_READ_TIMEOUT_SECONDS):
                async for chunk in request.stream():
                    total += len(chunk)
                    if total > MAX_WEBHOOK_BODY_BYTES:
                        return reject(413, "oversize", ws, sid)
                    chunks.append(chunk)
        except TimeoutError:
            return reject(408, "body_timeout", ws, sid)
        body = b"".join(chunks)

        # Registry / schedules.yaml reads and the queue's directory scan are blocking I/O: keep
        # them off the listener's event loop (L4).
        target = await run_in_threadpool(binding_lookup, ws, sid)
        secret = await run_in_threadpool(resolver, target) if target is not None else None
        if target is None:
            # Burn comparable CPU so unknown-vs-bad-signature is not a timing oracle.
            hmac.compare_digest(compute_signature(b"\0", body).encode(), b"x")
            return reject(401, "unknown_schedule", ws, sid)
        if secret is None:
            return reject(503, "secret_unresolvable", ws, sid)

        now = clock()
        try:
            verified = verify_delivery(request.headers, body, secret, now)
        except WebhookAuthError as exc:
            return reject(exc.status, exc.reason, ws, sid)

        def accept() -> JSONResponse:
            # check -> rate -> persist -> remember, atomically under one lock. Nonces are scoped
            # per schedule and cover the (unsigned) delivery id *and* the signed material.
            scope = f"{target.workspace_root}\0{sid}\0"
            keys = [f"{scope}sig\0{verified.replay_key}"]
            if verified.id_supplied:
                keys.append(f"{scope}id\0{verified.delivery_id}")
            with lock:
                if any(k in nonces for k in keys):
                    return reject(409, "duplicate", ws, sid)
                rate = target.binding.webhook.rate_per_minute if target.binding.webhook else 10
                if not bucket.allow((target.workspace_root, sid), rate):
                    return reject(429, "rate_limited", ws, sid)
                try:
                    queue.enqueue(
                        WebhookDelivery(
                            workspace_slug=ws,
                            schedule_id=sid,
                            delivery_id=verified.delivery_id,
                            received_at=now,
                        )
                    )
                except WebhookQueueFullError:
                    return reject(429, "queue_full", ws, sid)
                except OSError:
                    return reject(503, "enqueue_failed", ws, sid)
                for k in keys:
                    nonces.add(k)
            emit(
                "webhook.received",
                workspace_slug=_clip(ws),
                schedule_id=_clip(sid),
                delivery=_clip(verified.delivery_id),
            )
            return JSONResponse({"accepted": True, "schedule_id": sid}, status_code=202)

        return await run_in_threadpool(accept)

    # Under ``from __future__ import annotations`` a lazily imported ``Request`` cannot be
    # resolved from the string annotation (FastAPI would treat it as a query param -> 422), so
    # the real types are attached explicitly before registering the route.
    hook.__annotations__ = {
        "workspace_slug": str,
        "schedule_id": str,
        "request": Request,
        "return": JSONResponse,
    }
    app.post("/hooks/{workspace_slug}/{schedule_id}", response_model=None)(hook)
    return app


__all__ = [
    "MAX_WEBHOOK_BODY_BYTES",
    "MIN_WEBHOOK_SECRET_BYTES",
    "NON_LOOPBACK_WARNING",
    "NONCE_CACHE_SIZE",
    "REPLAY_WINDOW_SECONDS",
    "WEBHOOK_ALLOWED_HOSTS_ENV",
    "WEBHOOK_BODY_READ_TIMEOUT_SECONDS",
    "WEBHOOK_LIMIT_CONCURRENCY",
    "WEBHOOK_QUEUE_MAXSIZE",
    "WEBHOOK_REQUEST_TTL_SECONDS",
    "VerifiedDelivery",
    "NonceCache",
    "TokenBucket",
    "WebhookAuthError",
    "WebhookDelivery",
    "WebhookQueueFullError",
    "WebhookRequestQueue",
    "WebhookSource",
    "WebhookTarget",
    "build_webhook_app",
    "compute_signature",
    "make_binding_lookup",
    "resolve_secret",
    "resolve_webhook_allowed_hosts",
    "verify_delivery",
    "webhook_uvicorn_kwargs",
]
