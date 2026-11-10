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
* The nonce cache and per-schedule token bucket are **in memory** and reset on restart, so a
  restart briefly reopens a replay window for deliveries inside ``REPLAY_WINDOW_SECONDS`` (and,
  for senders that send no ``X-AO-Timestamp``, for any previously captured delivery). Persisting
  them needs an ADR.
* ``SecurityMiddleware`` is mounted (Host allow-list, Origin check, JSON ``Content-Type`` on a
  body-carrying POST), so senders must post ``application/json`` (GitHub: set the webhook's
  content type to ``application/json``, not the form-encoded default).
* Direct off-loopback exposure should sit behind a TLS-terminating reverse proxy; a hub with
  auth disabled may be on the same machine.
* ``fastapi`` is imported inside :func:`build_webhook_app` only, so a core install without the
  ``[ui]`` extra can still import this module (same rule as ``service/hub.py``).
"""

from __future__ import annotations

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
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..schedules.errors import ScheduleFileError
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
        value = env.get(spec.secret_env, "")
        return value.encode("utf-8") or None
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
            return path.read_bytes().strip() or None
        except OSError:
            return None
    return None


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


def compute_signature(secret: bytes, body: bytes) -> str:
    """``sha256=<hex>`` over the raw body bytes."""
    return SIGNATURE_PREFIX + hmac.new(secret, body, hashlib.sha256).hexdigest()


def _header(headers: Mapping[str, str], names: tuple[str, ...]) -> str | None:
    for name in names:
        value = headers.get(name)
        if value is not None:
            return value
    return None


def verify_delivery(headers: Mapping[str, str], body: bytes, secret: bytes, now: datetime) -> str:
    """HMAC + timestamp checks; returns the delivery id. Raises :class:`WebhookAuthError` (401).

    *headers* must be case-insensitive (Starlette's are) or already lower-cased. The signature
    is compared with :func:`hmac.compare_digest` over the raw *body*.
    """
    received = _header(headers, SIGNATURE_HEADERS)
    expected = compute_signature(secret, body)
    if received is None or not hmac.compare_digest(
        received.strip().encode("utf-8", "replace"), expected.encode("ascii")
    ):
        raise WebhookAuthError("bad_signature", 401)
    ts = headers.get(TIMESTAMP_HEADER)
    if ts is not None:
        try:
            stamp = int(ts.strip())
        except ValueError:
            raise WebhookAuthError("bad_timestamp", 401) from None
        if abs(now.timestamp() - stamp) > REPLAY_WINDOW_SECONDS:
            raise WebhookAuthError("stale", 401)
    delivery = _header(headers, DELIVERY_HEADERS)
    if delivery is None or not delivery.strip():
        return hashlib.sha256(body).hexdigest()
    return delivery.strip()


# -- persisted queue (F14) -----------------------------------------------------------------------


class WebhookRequestQueue:
    """Writes/reads ``requests/<uuid>.json`` (``kind: webhook``) under the service state dir."""

    def __init__(self, state_dir: Path, *, maxsize: int = WEBHOOK_QUEUE_MAXSIZE) -> None:
        self._dir = _requests_dir(state_dir)
        self._maxsize = maxsize

    def pending(self, workspace_slug: str, schedule_id: str) -> list[tuple[datetime, Path, str]]:
        """Pending requests for one schedule, oldest first: ``(received_at, path, delivery_id)``."""
        out: list[tuple[datetime, Path, str]] = []
        if not self._dir.is_dir():
            return out
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
            if received is None:
                continue
            out.append((received, path, str(raw.get("delivery_id", ""))))
        out.sort(key=lambda t: (t[0], t[1].name))
        return out

    def enqueue(self, delivery: WebhookDelivery) -> Path:
        """Atomically + durably write one request; :class:`WebhookQueueFullError` past the bound."""
        if len(self.pending(delivery.workspace_slug, delivery.schedule_id)) >= self._maxsize:
            raise WebhookQueueFullError(delivery.schedule_id)
        self._dir.mkdir(parents=True, exist_ok=True)
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
        pending = self._queue.pending(self._slug, binding.id)
        if not pending:
            return None
        received, path, delivery_id = pending[0]
        return Candidate(
            scheduled_for=received.replace(microsecond=0),
            payload={"delivery_id": delivery_id, "request": path.name},
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
            except ScheduleFileError:
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

    Status map (HLD §8.2): 404 disabled, 413 oversize, 503 secret unresolvable, 401 unknown
    schedule *and* bad/stale signature (indistinguishable), 409 duplicate, 429 rate-limited or
    queue full, 202 accepted.
    """
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse

    from ..ui.security import SecurityMiddleware, resolve_allowed_hosts

    resolver = secret_resolver or (lambda t: resolve_secret(t, emit=emit))
    nonces = nonce_cache if nonce_cache is not None else NonceCache()
    bucket = rate_limiter if rate_limiter is not None else TokenBucket(clock)
    lock = threading.Lock()

    app = FastAPI(title="ao webhook", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(
        SecurityMiddleware, allowed_hosts=resolve_allowed_hosts(bound_host=bound_host)
    )

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
        async for chunk in request.stream():
            total += len(chunk)
            if total > MAX_WEBHOOK_BODY_BYTES:
                return reject(413, "oversize", ws, sid)
            chunks.append(chunk)
        body = b"".join(chunks)

        target = binding_lookup(ws, sid)
        secret = resolver(target) if target is not None else None
        if target is None:
            # Burn comparable CPU so unknown-vs-bad-signature is not a timing oracle.
            hmac.compare_digest(compute_signature(b"\0", body).encode(), b"x")
            return reject(401, "unknown_schedule", ws, sid)
        if secret is None:
            return reject(503, "secret_unresolvable", ws, sid)

        now = clock()
        try:
            delivery_id = verify_delivery(request.headers, body, secret, now)
        except WebhookAuthError as exc:
            return reject(exc.status, exc.reason, ws, sid)

        with lock:  # check -> rate -> persist -> remember, atomically (no await inside)
            if delivery_id in nonces:
                return reject(409, "duplicate", ws, sid)
            rate = target.binding.webhook.rate_per_minute if target.binding.webhook else 10
            if not bucket.allow((target.workspace_root, sid), rate):
                return reject(429, "rate_limited", ws, sid)
            try:
                queue.enqueue(
                    WebhookDelivery(
                        workspace_slug=ws, schedule_id=sid, delivery_id=delivery_id, received_at=now
                    )
                )
            except WebhookQueueFullError:
                return reject(429, "queue_full", ws, sid)
            except OSError:
                return reject(503, "enqueue_failed", ws, sid)
            nonces.add(delivery_id)
        emit(
            "webhook.received",
            workspace_slug=_clip(ws),
            schedule_id=_clip(sid),
            delivery=_clip(delivery_id),
        )
        return JSONResponse({"accepted": True, "schedule_id": sid}, status_code=202)

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
    "NONCE_CACHE_SIZE",
    "REPLAY_WINDOW_SECONDS",
    "WEBHOOK_QUEUE_MAXSIZE",
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
    "verify_delivery",
]
