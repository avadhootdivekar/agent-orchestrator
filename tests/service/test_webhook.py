"""T-Wh9Kv1: webhook ingress. Fixed clocks, real ``TestClient`` through the mounted middleware."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from agent_orchestrator.schedules.models import ScheduleBinding, WebhookSpec
from agent_orchestrator.service import webhook as wh
from agent_orchestrator.service.schedule_engine import Candidate
from agent_orchestrator.service.supervisor import _slug_for_root
from agent_orchestrator.service.webhook import (
    MAX_WEBHOOK_BODY_BYTES,
    NonceCache,
    TokenBucket,
    WebhookRequestQueue,
    WebhookSource,
    WebhookTarget,
    build_webhook_app,
    compute_signature,
    make_binding_lookup,
    resolve_secret,
)

from ..schedules._fixtures import minimal_limits
from .test_schedule_engine import T0, binding, env_for

SECRET = "s3cr3t-value-xyz"
SECRET_ENV = "AO_TEST_WEBHOOK_SECRET"
BODY = b'{"ref": "refs/heads/main"}'
HOST = "http://localhost"


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now


def wh_binding(sid: str = "hook", *, enabled: bool = True, **spec: Any) -> ScheduleBinding:
    spec.setdefault("secret_env", SECRET_ENV)
    return binding(
        sid, kind="webhook", interval_seconds=None, enabled=enabled, webhook=WebhookSpec(**spec)
    )


class Harness:
    def __init__(self, tmp: Path, b: ScheduleBinding | None = None, **kw: Any) -> None:
        self.root = str((tmp / "ws").resolve())
        Path(self.root).mkdir(exist_ok=True)
        self.slug = _slug_for_root(self.root)
        self.binding = b or wh_binding()
        self.state = tmp / "state"
        self.clock = Clock()
        self.queue = WebhookRequestQueue(self.state)
        self.events: list[tuple[str, dict[str, Any]]] = []
        self.app = build_webhook_app(
            self._lookup,
            self.queue,
            clock=self.clock,
            emit=lambda e, **f: self.events.append((e, f)),
            **kw,
        )
        self.client = TestClient(self.app, base_url=HOST)

    def _lookup(self, slug: str, sid: str) -> WebhookTarget | None:
        if slug == self.slug and sid == self.binding.id:
            return WebhookTarget(self.root, self.binding)
        return None

    def post(
        self,
        body: bytes = BODY,
        *,
        secret: str = SECRET,
        sig_header: str | None = "X-AO-Signature",
        slug: str | None = None,
        sid: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        h = {"Content-Type": "application/json", **(headers or {})}
        if sig_header:
            h[sig_header] = compute_signature(secret.encode(), body)
        return self.client.post(
            f"/hooks/{slug or self.slug}/{sid or self.binding.id}", content=body, headers=h
        )

    def requests(self) -> list[Path]:
        d = self.state / "schedules" / "requests"
        return sorted(d.glob("*.json")) if d.is_dir() else []


@pytest.fixture(autouse=True)
def _secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(SECRET_ENV, SECRET)


@pytest.fixture()
def h(tmp_path: Path) -> Harness:
    return Harness(tmp_path)


# -- accept paths --------------------------------------------------------------------------------


def test_valid_hmac_accepted_and_persisted(h: Harness) -> None:
    r = h.post()
    assert r.status_code == 202 and r.json() == {"accepted": True, "schedule_id": "hook"}
    [req] = h.requests()
    rec = json.loads(req.read_text())
    assert rec["kind"] == "webhook" and rec["schedule_id"] == "hook"
    assert rec["workspace_slug"] == h.slug and rec["received_at"] == T0.isoformat()
    assert [e for e, _ in h.events] == ["webhook.received"]


def test_github_header_form_accepted(h: Harness) -> None:
    r = h.post(sig_header="X-Hub-Signature-256", headers={"X-GitHub-Delivery": "gh-1"})
    assert r.status_code == 202
    assert json.loads(h.requests()[0].read_text())["delivery_id"] == "gh-1"


def test_delivery_id_falls_back_to_body_hash(h: Harness) -> None:
    import hashlib

    h.post()
    assert (
        json.loads(h.requests()[0].read_text())["delivery_id"] == hashlib.sha256(BODY).hexdigest()
    )


def test_healthz_and_only_two_routes(h: Harness) -> None:
    assert h.client.get("/healthz").json() == {"status": "ok"}
    paths = {r.path for r in h.app.routes}  # type: ignore[attr-defined]
    assert paths == {"/healthz", "/hooks/{workspace_slug}/{schedule_id}"}


def test_disabled_is_404(tmp_path: Path) -> None:
    assert Harness(tmp_path, enabled=False).post().status_code == 404


def test_fresh_timestamp_ok(h: Harness) -> None:
    r = h.post(headers={"X-AO-Timestamp": str(int(T0.timestamp()) + 299)})
    assert r.status_code == 202


# -- rejections ----------------------------------------------------------------------------------


def test_wrong_secret_401(h: Harness) -> None:
    assert h.post(secret="nope").status_code == 401 and h.requests() == []


def test_missing_signature_401(h: Harness) -> None:
    assert h.post(sig_header=None).status_code == 401


def test_signature_over_raw_bytes_not_reserialised(h: Harness) -> None:
    sig = compute_signature(SECRET.encode(), b'{"a":1}')
    r = h.client.post(
        f"/hooks/{h.slug}/hook",
        content=b'{"a": 1}',  # same JSON, different bytes
        headers={"Content-Type": "application/json", "X-AO-Signature": sig},
    )
    assert r.status_code == 401


def test_oversize_body_413_by_content_length_and_stream(h: Harness) -> None:
    big = b"x" * (MAX_WEBHOOK_BODY_BYTES + 1)
    assert h.post(big).status_code == 413
    exact = b"y" * MAX_WEBHOOK_BODY_BYTES
    assert h.post(exact).status_code == 202

    def chunks() -> Any:  # chunked: no Content-Length, the stream cap must trip
        for _ in range(3):
            yield b"z" * (MAX_WEBHOOK_BODY_BYTES // 2)

    r = h.client.post(
        f"/hooks/{h.slug}/hook",
        content=chunks(),
        headers={"Content-Type": "application/json", "X-AO-Signature": "sha256=00"},
    )
    assert r.status_code == 413


def test_stale_and_bad_timestamp_401(h: Harness) -> None:
    stale = str(int(T0.timestamp()) - 301)
    assert h.post(headers={"X-AO-Timestamp": stale}).status_code == 401
    assert h.post(headers={"X-AO-Timestamp": "abc"}).status_code == 401
    assert h.requests() == []


def test_duplicate_delivery_409(h: Harness) -> None:
    assert h.post().status_code == 202
    assert h.post().status_code == 409
    assert len(h.requests()) == 1
    assert h.events[-1][1]["reason"] == "duplicate"


def test_nonce_cache_is_a_bounded_lru() -> None:
    n = NonceCache(maxsize=2)
    for k in ("a", "b", "c"):
        n.add(k)
    assert "a" not in n and "b" in n and "c" in n and len(n) == 2


def test_rate_limited_429_then_refills(tmp_path: Path) -> None:
    h = Harness(tmp_path, wh_binding(rate_per_minute=2))
    codes = [h.post(headers={"X-AO-Delivery": f"d{i}"}).status_code for i in range(3)]
    assert codes == [202, 202, 429]
    h.clock.now += timedelta(seconds=30)  # 1 token back
    assert h.post(headers={"X-AO-Delivery": "d3"}).status_code == 202
    # the rate-limited delivery did not burn its nonce
    h.clock.now += timedelta(seconds=60)
    assert h.post(headers={"X-AO-Delivery": "d2"}).status_code == 202


def test_token_bucket_per_key() -> None:
    c = Clock()
    b = TokenBucket(c)
    assert b.allow(("w", "a"), 1) and not b.allow(("w", "a"), 1) and b.allow(("w", "b"), 1)


def test_unknown_schedule_and_bad_signature_indistinguishable(h: Harness) -> None:
    unknown = h.post(sid="nope")
    bad = h.post(secret="wrong")
    unknown_ws = h.post(slug="nowhere")
    assert unknown.status_code == bad.status_code == unknown_ws.status_code == 401
    assert unknown.content == bad.content == unknown_ws.content
    assert unknown.headers["content-type"] == bad.headers["content-type"]


def test_unresolvable_secret_503(h: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(SECRET_ENV)
    assert h.post().status_code == 503 and h.requests() == []


def test_queue_full_429(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wh, "WEBHOOK_QUEUE_MAXSIZE", 2)
    h = Harness(tmp_path, wh_binding(rate_per_minute=100))
    h.queue = h.queue.__class__(h.state, maxsize=2)
    app = build_webhook_app(h._lookup, h.queue, clock=h.clock, emit=lambda *a, **k: None)
    h.client = TestClient(app, base_url=HOST)
    codes = [h.post(headers={"X-AO-Delivery": f"q{i}"}).status_code for i in range(3)]
    assert codes == [202, 202, 429]


def test_enqueue_oserror_503(h: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a: Any, **k: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(h.queue, "enqueue", boom)
    assert h.post().status_code == 503


# -- secret resolution ---------------------------------------------------------------------------


def _file_target(tmp_path: Path, mode: int, rel: str = "sec.txt") -> WebhookTarget:
    root = tmp_path / "ws"
    root.mkdir(exist_ok=True)
    f = root / "sec.txt"
    f.write_text(SECRET + "\n")
    f.chmod(mode)
    return WebhookTarget(str(root), wh_binding(secret_env=None, secret_file=rel))


def test_secret_file_ok_and_group_readable_refused(tmp_path: Path) -> None:
    events: list[str] = []
    emit = lambda e, **f: events.append(e)  # noqa: E731
    assert resolve_secret(_file_target(tmp_path, 0o600), emit=emit) == SECRET.encode()
    assert resolve_secret(_file_target(tmp_path, 0o640), emit=emit) is None
    assert events == ["webhook.secret_permissions"]


def test_secret_file_group_readable_is_503_through_app(tmp_path: Path) -> None:
    t = _file_target(tmp_path, 0o644)
    h = Harness(tmp_path, t.binding)
    assert h.post().status_code == 503
    assert "webhook.secret_permissions" in [e for e, _ in h.events]


def test_secret_file_missing_and_escape(tmp_path: Path) -> None:
    t = _file_target(tmp_path, 0o600, rel="missing.txt")
    assert resolve_secret(t) is None
    outside = tmp_path / "outside.txt"
    outside.write_text(SECRET)
    outside.chmod(0o600)
    link = Path(t.workspace_root) / "link.txt"
    os.symlink(outside, link)
    t2 = WebhookTarget(t.workspace_root, wh_binding(secret_env=None, secret_file="link.txt"))
    assert resolve_secret(t2) is None


def test_inline_secret_rejected_at_load_time() -> None:
    with pytest.raises(ValidationError, match="inline 'secret' is not allowed"):
        WebhookSpec.model_validate({"secret": "x"})


# -- security posture ----------------------------------------------------------------------------


def test_disallowed_host_is_421_through_real_client(h: Harness) -> None:
    c = TestClient(h.app, base_url="http://evil.example.com")
    r = c.post(f"/hooks/{h.slug}/hook", content=BODY, headers={"Content-Type": "application/json"})
    assert r.status_code == 421


def test_non_json_content_type_rejected_by_mounted_middleware(h: Harness) -> None:
    r = h.client.post(
        f"/hooks/{h.slug}/hook",
        content=BODY,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "X-AO-Signature": compute_signature(SECRET.encode(), BODY),
        },
    )
    assert r.status_code == 415


def test_works_without_any_session_cookie(h: Harness) -> None:
    # HMAC is the whole auth (F18): no AuthMiddleware, so no cookie is needed or consulted.
    assert not h.client.cookies
    assert h.post().status_code == 202
    names = {m.cls.__name__ for m in h.app.user_middleware}  # type: ignore[attr-defined]
    assert names == {"SecurityMiddleware"}


def test_no_secret_or_signature_in_any_log_or_response(
    h: Harness, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    # default emitter -> stdlib logging, so exercise it too
    app = build_webhook_app(h._lookup, h.queue, clock=h.clock)
    c = TestClient(app, base_url=HOST)
    good = compute_signature(SECRET.encode(), BODY)
    wrong = compute_signature(b"attacker-key", BODY)
    responses = []
    for kw in (
        dict(headers={"X-AO-Signature": wrong}),
        dict(headers={"X-AO-Signature": good, "X-AO-Timestamp": "1"}),
        dict(headers={"X-AO-Signature": good, "X-AO-Delivery": "dup"}),
        dict(headers={"X-AO-Signature": good, "X-AO-Delivery": "dup"}),
        dict(headers={}),
    ):
        responses.append(
            c.post(
                f"/hooks/{h.slug}/hook",
                content=BODY,
                headers={"Content-Type": "application/json", **kw["headers"]},
            )
        )
    os.environ.pop(SECRET_ENV)
    responses.append(
        c.post(
            f"/hooks/{h.slug}/hook",
            content=BODY,
            headers={"Content-Type": "application/json", "X-AO-Signature": good},
        )
    )
    assert [r.status_code for r in responses] == [401, 401, 202, 409, 401, 503]
    blob = caplog.text + "".join(r.text for r in responses)
    for forbidden in (SECRET, good, wrong, "attacker-key", good.split("=")[1], wrong.split("=")[1]):
        assert forbidden not in blob


# -- WebhookSource + engine ----------------------------------------------------------------------


def test_source_observe_is_pure_and_commit_deletes(h: Harness) -> None:
    src = WebhookSource(h.state, h.root, h.binding)
    assert src.observe(h.binding, None, T0) is None  # type: ignore[arg-type]
    h.clock.now = T0 + timedelta(seconds=5, microseconds=700)
    h.post(headers={"X-AO-Delivery": "a"})
    h.clock.now += timedelta(seconds=10)
    h.post(headers={"X-AO-Delivery": "b"})
    c1 = src.observe(h.binding, None, T0)  # type: ignore[arg-type]
    assert c1 is not None and c1.scheduled_for == T0 + timedelta(seconds=5)
    assert len(h.requests()) == 2 and src.observe(h.binding, None, T0) == c1  # type: ignore[arg-type]
    src.commit(h.binding, c1)
    assert len(h.requests()) == 1
    c2 = src.observe(h.binding, None, T0)  # type: ignore[arg-type]
    assert c2 is not None and c2.scheduled_for == T0 + timedelta(seconds=15)
    src.commit(h.binding, Candidate(scheduled_for=T0, payload={"request": "../evil"}))
    src.commit(h.binding, Candidate(scheduled_for=T0))  # no payload: no-op


def test_source_scoped_to_schedule(h: Harness, tmp_path: Path) -> None:
    h.post()
    ob = wh_binding("other")
    assert WebhookSource(h.state, h.root, ob).observe(ob, None, T0) is None  # type: ignore[arg-type]
    elsewhere = WebhookSource(h.state, str(tmp_path / "ws2"), h.binding)
    assert elsewhere.observe(h.binding, None, T0) is None  # type: ignore[arg-type]


def _engine_env(tmp_path: Path) -> tuple[Any, Harness]:
    env = env_for(tmp_path, [wh_binding(rate_per_minute=1000)])
    env.engine.register_trigger_source(
        "webhook", lambda root, b: WebhookSource(env.tmp / "state", root, b)
    )
    h = Harness(tmp_path)
    h.root, h.slug, h.binding = env.root, _slug_for_root(env.root), env.binding("hook")
    h.clock.now = T0
    return env, h


def test_flood_against_overlapping_schedule_launches_at_most_once(tmp_path: Path) -> None:
    env, h = _engine_env(tmp_path)
    for i in range(50):
        assert h.post(headers={"X-AO-Delivery": f"f{i}"}).status_code == 202
    assert len(h.requests()) == 50
    env.tick(T0 + timedelta(seconds=1))
    assert len(env.launcher.decisions) == 1  # run stays active: every later tick overlap-skips
    for i in range(2, 60):
        env.tick(T0 + timedelta(seconds=i))
    assert len(env.launcher.decisions) == 1
    assert h.requests() == []  # drained one per tick, each burned or launched


def test_crash_between_202_and_tick_loses_nothing_and_second_delivery_409(tmp_path: Path) -> None:
    env, h = _engine_env(tmp_path)
    assert h.post().status_code == 202
    # "crash": the listener and the first engine are gone; a fresh engine reads the persisted file
    env2 = env.make_engine()
    env2.register_trigger_source("webhook", lambda r, b: WebhookSource(env.tmp / "state", r, b))
    env.launcher.env = env  # recording launcher writes through env.engine; reuse it
    env.engine = env2
    fired = env.tick(T0 + timedelta(seconds=1))
    assert len(fired) == 1 and fired[0].schedule_id == "hook"
    assert h.requests() == []
    assert h.post().status_code == 409  # replay of the identical delivery
    env.tick(T0 + timedelta(seconds=40))
    assert len(env.launcher.decisions) == 1


# -- binding lookup ------------------------------------------------------------------------------


def test_binding_lookup_filters(tmp_path: Path) -> None:
    env = env_for(
        tmp_path,
        [
            wh_binding("on"),
            wh_binding("off", enabled=False),
            binding("cron", limits=minimal_limits()),
        ],
    )
    from types import SimpleNamespace

    class Reg:
        def __init__(self, p: Path) -> None: ...

        def load(self) -> Any:
            return SimpleNamespace(workspaces=[SimpleNamespace(root=env.root, schedules=True)])

    look = make_binding_lookup(tmp_path / "r.yaml", registry_factory=Reg)
    slug = _slug_for_root(env.root)
    got = look(slug, "on")
    assert got is not None and got.binding.id == "on" and got.workspace_root == env.root
    assert look(slug, "off") is None and look(slug, "cron") is None
    assert look(slug, "missing") is None and look("other", "on") is None

    class Opt(Reg):
        def load(self) -> Any:
            return SimpleNamespace(workspaces=[SimpleNamespace(root=env.root, schedules=False)])

    assert make_binding_lookup(tmp_path / "r.yaml", registry_factory=Opt)(slug, "on") is None

    class Bad(Reg):
        def load(self) -> Any:
            raise ValueError("corrupt")

    assert make_binding_lookup(tmp_path / "r.yaml", registry_factory=Bad)(slug, "on") is None
    (Path(env.root) / ".ao").mkdir(exist_ok=True)
    (Path(env.root) / ".ao" / "schedules.yaml").write_text("schedules: [: bad")
    assert look(slug, "on") is None


def test_module_importable_without_fastapi_at_module_scope() -> None:
    src = Path(wh.__file__).read_text().split("def build_webhook_app")[0]
    assert "\nimport fastapi" not in src and "\nfrom fastapi" not in src
