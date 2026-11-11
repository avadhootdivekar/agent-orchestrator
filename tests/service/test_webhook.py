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


def body_for(tag: str) -> bytes:
    """A distinct body per delivery (an identical signed body is a replay by design)."""
    return json.dumps({"ref": "refs/heads/main", "tag": tag}).encode()


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now


def wh_binding(sid: str = "hook", *, enabled: bool = True, **spec: Any) -> ScheduleBinding:
    spec.setdefault("secret_env", SECRET_ENV)
    extra = {
        k: spec.pop(k) for k in ("overlap", "catch_up", "catch_up_window_seconds") if k in spec
    }
    return binding(
        sid,
        kind="webhook",
        interval_seconds=None,
        enabled=enabled,
        webhook=WebhookSpec(**spec),
        **extra,
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
        if sig_header == "X-AO-Signature":
            # the X-AO scheme signs "{ts}.{body}" and requires the timestamp header
            ts = h.setdefault("X-AO-Timestamp", str(int(self.clock.now.timestamp())))
            h[sig_header] = compute_signature(secret.encode(), body, ts)
        elif sig_header:
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


@pytest.fixture()
def gh(tmp_path: Path) -> Harness:
    """A binding pinned to GitHub's ``X-Hub-Signature-256`` scheme (body-only signature)."""
    return Harness(tmp_path, wh_binding(scheme="github"))


# -- accept paths --------------------------------------------------------------------------------


def test_valid_hmac_accepted_and_persisted(h: Harness) -> None:
    r = h.post()
    assert r.status_code == 202 and r.json() == {"accepted": True, "schedule_id": "hook"}
    [req] = h.requests()
    rec = json.loads(req.read_text())
    assert rec["kind"] == "webhook" and rec["schedule_id"] == "hook"
    assert rec["workspace_slug"] == h.slug and rec["received_at"] == T0.isoformat()
    assert [e for e, _ in h.events] == ["webhook.received"]


def test_github_header_form_accepted(gh: Harness) -> None:
    r = gh.post(sig_header="X-Hub-Signature-256", headers={"X-GitHub-Delivery": "gh-1"})
    assert r.status_code == 202
    assert json.loads(gh.requests()[0].read_text())["delivery_id"] == "gh-1"


def test_delivery_id_falls_back_to_signed_material_hash(h: Harness) -> None:
    """N4: the fallback id is the hash of ``ts.body`` (not the body), so it is timestamp-unique."""
    import hashlib

    h.post()
    ts = _ts(h).encode()
    assert (
        json.loads(h.requests()[0].read_text())["delivery_id"]
        == hashlib.sha256(ts + b"." + BODY).hexdigest()
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


def test_missing_signature_401(h: Harness, gh: Harness) -> None:
    # w21-05 review: send a valid timestamp so the request reaches the signature branch
    # (with no timestamp it was refused as bad_timestamp and never tested the missing-sig path).
    r = h.client.post(
        f"/hooks/{h.slug}/hook",
        content=BODY,
        headers={"Content-Type": "application/json", "X-AO-Timestamp": str(int(T0.timestamp()))},
    )
    assert r.status_code == 401 and h.events[-1][1]["reason"] == "bad_signature"
    assert gh.post(sig_header=None).status_code == 401
    assert gh.events[-1][1]["reason"] == "bad_signature" and gh.requests() == []


def test_signature_over_raw_bytes_not_reserialised(gh: Harness) -> None:
    # w21-05 review: use the github (body-only) scheme so the HMAC check is actually reached;
    # on the default ao binding this header was refused as wrong_scheme before any HMAC.
    sig = compute_signature(SECRET.encode(), b'{"a":1}')
    url = f"/hooks/{gh.slug}/hook"
    hdrs = {"Content-Type": "application/json", "X-Hub-Signature-256": sig}
    assert gh.client.post(url, content=b'{"a": 1}', headers=hdrs).status_code == 401  # same JSON
    assert gh.events[-1][1]["reason"] == "bad_signature"
    assert gh.client.post(url, content=b'{"a":1}', headers=hdrs).status_code == 202  # control


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


@pytest.mark.parametrize(("offset", "code"), [(300, 202), (-300, 202), (301, 401), (-301, 401)])
def test_replay_window_boundary_both_directions(h: Harness, offset: int, code: int) -> None:
    # w21-05 review: pins the inclusive edge and the abs() (future skew) of the replay window.
    r = h.post(headers={"X-AO-Timestamp": str(int(T0.timestamp()) + offset)})
    assert r.status_code == code
    if code == 401:
        assert h.events[-1][1]["reason"] == "stale"


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
    codes = [
        h.post(body_for(f"d{i}"), headers={"X-AO-Delivery": f"d{i}"}).status_code for i in range(3)
    ]
    assert codes == [202, 202, 429]
    h.clock.now += timedelta(seconds=30)  # 1 token back
    assert h.post(body_for("d3"), headers={"X-AO-Delivery": "d3"}).status_code == 202
    # the rate-limited delivery did not burn its nonce
    h.clock.now += timedelta(seconds=60)
    assert h.post(body_for("d2"), headers={"X-AO-Delivery": "d2"}).status_code == 202


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
    codes = [
        h.post(body_for(f"q{i}"), headers={"X-AO-Delivery": f"q{i}"}).status_code for i in range(3)
    ]
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
            "X-Hub-Signature-256": compute_signature(SECRET.encode(), BODY),
        },
    )
    assert r.status_code == 415


def test_token_bucket_burst_capped_at_rate_after_long_idle() -> None:
    # w21-05 review: a long idle must not bank more than rate_per_minute tokens.
    c = Clock()
    b = TokenBucket(c)
    assert b.allow(("w", "a"), 2) and b.allow(("w", "a"), 2) and not b.allow(("w", "a"), 2)
    c.now += timedelta(hours=1)
    assert b.allow(("w", "a"), 2) and b.allow(("w", "a"), 2) and not b.allow(("w", "a"), 2)


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
    ts = str(int(T0.timestamp()))
    good = compute_signature(SECRET.encode(), BODY, ts)
    wrong = compute_signature(b"attacker-key", BODY, ts)
    T = {"X-AO-Timestamp": ts}
    responses = []
    cases: list[dict[str, Any]] = [
        dict(headers={"X-AO-Signature": wrong, **T}),
        dict(headers={"X-AO-Signature": good, "X-AO-Timestamp": "1"}),
        dict(headers={"X-AO-Signature": good, "X-AO-Delivery": "dup", **T}),
        dict(headers={"X-AO-Signature": good, "X-AO-Delivery": "dup", **T}),
        dict(headers={}),
    ]
    for kw in cases:
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
            headers={"Content-Type": "application/json", "X-AO-Signature": good, **T},
        )
    )
    assert [r.status_code for r in responses] == [401, 401, 202, 409, 401, 503]
    # w21-05 review: positive control -- the default emitter logged, so the scan is not vacuous
    assert "webhook.rejected" in caplog.text and "webhook.received" in caplog.text
    blob = caplog.text + "".join(r.text for r in responses)
    for forbidden in (SECRET, good, wrong, "attacker-key", good.split("=")[1], wrong.split("=")[1]):
        assert forbidden not in blob


# -- WebhookSource + engine ----------------------------------------------------------------------


def test_source_observe_is_pure_and_commit_deletes(h: Harness) -> None:
    src = WebhookSource(h.state, h.root, h.binding)
    assert src.observe(h.binding, None, T0) is None  # type: ignore[arg-type]
    h.clock.now = T0 + timedelta(seconds=5, microseconds=700)
    h.post(body_for("a"), headers={"X-AO-Delivery": "a"})
    h.clock.now += timedelta(seconds=10)
    h.post(body_for("b"), headers={"X-AO-Delivery": "b"})
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
        assert h.post(body_for(f"f{i}"), headers={"X-AO-Delivery": f"f{i}"}).status_code == 202
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


# -- review fixes (T-Wh9Kv1 security review w15-02) ----------------------------------------------


def _ts(h: Harness) -> str:
    return str(int(h.clock.now.timestamp()))


def test_h1_captured_x_ao_delivery_replayed_with_forged_delivery_id_is_409(h: Harness) -> None:
    ts = _ts(h)
    assert h.post(headers={"X-AO-Timestamp": ts, "X-AO-Delivery": "real"}).status_code == 202
    for forged in ("forged-1", "forged-2"):  # same signed body+ts, attacker-chosen unsigned id
        r = h.post(headers={"X-AO-Timestamp": ts, "X-AO-Delivery": forged})
        assert r.status_code == 409
    assert len(h.requests()) == 1


def test_h1_captured_github_delivery_replayed_with_forged_delivery_id_is_409(gh: Harness) -> None:
    kw: dict[str, Any] = dict(sig_header="X-Hub-Signature-256")
    assert gh.post(headers={"X-GitHub-Delivery": "gh-a"}, **kw).status_code == 202
    assert gh.post(headers={"X-GitHub-Delivery": "gh-forged"}, **kw).status_code == 409
    assert len(gh.requests()) == 1


def test_h1_stripped_or_forged_timestamp_on_x_ao_scheme_is_401(h: Harness) -> None:
    body_only = compute_signature(
        SECRET.encode(), BODY
    )  # what a captured GitHub-form sig looks like
    base = {"Content-Type": "application/json", "X-AO-Signature": body_only}
    url = f"/hooks/{h.slug}/hook"
    # timestamp stripped
    assert h.client.post(url, content=BODY, headers=base).status_code == 401
    # a fresh timestamp bolted on to an old (body-only / other-ts) signature
    fresh = {**base, "X-AO-Timestamp": _ts(h)}
    assert h.client.post(url, content=BODY, headers=fresh).status_code == 401
    # a validly signed old delivery cannot be refreshed by changing only the timestamp header
    old = str(int(T0.timestamp()) - 10)
    signed = compute_signature(SECRET.encode(), BODY, old)
    r = h.client.post(
        url,
        content=BODY,
        headers={**base, "X-AO-Signature": signed, "X-AO-Timestamp": _ts(h)},
    )
    assert r.status_code == 401
    assert h.requests() == []


def test_h1_same_body_new_timestamp_is_a_new_delivery_for_x_ao(h: Harness) -> None:
    assert h.post().status_code == 202
    h.clock.now += timedelta(seconds=5)  # legitimately re-signed identical body
    assert h.post().status_code == 202


def test_l2_oversized_timestamp_is_401_not_500(h: Harness) -> None:
    assert h.post(headers={"X-AO-Timestamp": "9" * 400}).status_code == 401
    assert h.post(headers={"X-AO-Timestamp": "-5"}).status_code == 401
    assert h.requests() == []


def test_l7_nonces_are_scoped_per_schedule(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    other = wh_binding("other")
    base_lookup = h._lookup

    def lookup(slug: str, sid: str) -> WebhookTarget | None:
        if slug == h.slug and sid == "other":
            return WebhookTarget(h.root, other)
        return base_lookup(slug, sid)

    h.app = build_webhook_app(lookup, h.queue, clock=h.clock, emit=lambda *a, **k: None)
    h.client = TestClient(h.app, base_url=HOST)
    assert h.post(headers={"X-AO-Delivery": "same"}).status_code == 202
    assert h.post(sid="other", headers={"X-AO-Delivery": "same"}).status_code == 202
    assert h.post(headers={"X-AO-Delivery": "same"}).status_code == 409


# M1 -------------------------------------------------------------------------------------------


def test_m1_source_candidate_has_discriminator_and_skip_catch_up(h: Harness) -> None:
    h.post(body_for("a"), headers={"X-AO-Delivery": "id-a"})
    c = WebhookSource(h.state, h.root, h.binding).observe(h.binding, None, T0)  # type: ignore[arg-type]
    assert c is not None and c.discriminator == "id-a" and c.skip_catch_up is True


def test_m1_two_deliveries_in_the_same_second_both_fire(tmp_path: Path) -> None:
    env = env_for(tmp_path, [wh_binding(rate_per_minute=1000)])
    env.engine.register_trigger_source(
        "webhook", lambda root, b: WebhookSource(env.tmp / "state", root, b)
    )
    h = Harness(tmp_path)
    h.root, h.slug, h.binding = env.root, _slug_for_root(env.root), env.binding("hook")
    for tag in ("x", "y"):  # same wall-clock second, distinct deliveries
        assert h.post(body_for(tag), headers={"X-AO-Delivery": tag}).status_code == 202
    env.tick(T0 + timedelta(seconds=1))
    env.end_run("run-1")  # one active run at a time: let the first finish
    env.tick(T0 + timedelta(seconds=2))
    assert len(env.launcher.decisions) == 2
    assert h.requests() == []


@pytest.mark.parametrize("late", [timedelta(seconds=120), timedelta(hours=6)])
def test_m1_late_drain_still_fires_with_catch_up_skip(tmp_path: Path, late: timedelta) -> None:
    env = env_for(
        tmp_path, [wh_binding(rate_per_minute=1000, catch_up="skip", catch_up_window_seconds=3600)]
    )
    env.engine.register_trigger_source(
        "webhook", lambda root, b: WebhookSource(env.tmp / "state", root, b)
    )
    h = Harness(tmp_path)
    h.root, h.slug, h.binding = env.root, _slug_for_root(env.root), env.binding("hook")
    assert h.post().status_code == 202
    env.tick(T0 + late)  # daemon was down: drained long after receipt
    assert len(env.launcher.decisions) == 1
    assert h.requests() == []


# M2 -------------------------------------------------------------------------------------------


def test_m2_uvicorn_kwargs_bound_connections() -> None:
    kw = wh.webhook_uvicorn_kwargs()
    assert kw["limit_concurrency"] == wh.WEBHOOK_LIMIT_CONCURRENCY > 0
    assert kw["timeout_keep_alive"] == wh.WEBHOOK_TIMEOUT_KEEP_ALIVE_SECONDS > 0
    assert kw["http"] is wh.webhook_http_protocol()  # N2: the header-deadline protocol


def test_m2_stalled_body_is_408(h: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio
    from collections.abc import MutableMapping

    monkeypatch.setattr(wh, "WEBHOOK_BODY_READ_TIMEOUT_SECONDS", 0.05)
    sent: list[MutableMapping[str, Any]] = []

    async def run() -> None:
        first = True

        async def receive() -> MutableMapping[str, Any]:
            nonlocal first
            if first:
                first = False
                return {"type": "http.request", "body": b"{", "more_body": True}
            await asyncio.sleep(30)  # the slowloris: one byte, then silence
            return {"type": "http.disconnect"}

        async def send(msg: MutableMapping[str, Any]) -> None:
            sent.append(msg)

        scope = {
            "type": "http",
            "method": "POST",
            "path": f"/hooks/{h.slug}/hook",
            "raw_path": f"/hooks/{h.slug}/hook".encode(),
            "query_string": b"",
            "headers": [(b"host", b"localhost"), (b"content-type", b"application/json")],
            "scheme": "http",
            "server": ("localhost", 80),
            "client": ("127.0.0.1", 1),
            "http_version": "1.1",
        }
        await asyncio.wait_for(h.app(scope, receive, send), timeout=5)

    asyncio.run(run())
    assert sent[0]["status"] == 408
    assert h.requests() == [] and h.events[-1][1]["reason"] == "body_timeout"


# M3 / L6 --------------------------------------------------------------------------------------


def test_m3_non_loopback_bind_refuses_foreign_host_until_webhook_hosts_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(wh.WEBHOOK_ALLOWED_HOSTS_ENV, raising=False)
    monkeypatch.setenv("AO_UI_ALLOWED_HOSTS", "hooks.example.com")  # must NOT widen the webhook
    h = Harness(tmp_path, bound_host="0.0.0.0")
    foreign = TestClient(h.app, base_url="http://hooks.example.com")
    url = f"/hooks/{h.slug}/hook"
    hdrs = {"Content-Type": "application/json"}
    assert foreign.post(url, content=BODY, headers=hdrs).status_code == 421

    monkeypatch.setenv(wh.WEBHOOK_ALLOWED_HOSTS_ENV, "hooks.example.com")
    h2 = Harness(tmp_path, bound_host="0.0.0.0")
    ok = TestClient(h2.app, base_url="http://hooks.example.com:8771")
    ts = _ts(h2)
    sig = compute_signature(SECRET.encode(), BODY, ts)
    r = ok.post(url, content=BODY, headers={**hdrs, "X-AO-Signature": sig, "X-AO-Timestamp": ts})
    assert r.status_code == 202
    # an unlisted host is still refused
    other = TestClient(h2.app, base_url="http://evil.example.com")
    assert other.post(url, content=BODY, headers=hdrs).status_code == 421


def test_m3_webhook_hosts_has_no_wildcard_escape(monkeypatch: pytest.MonkeyPatch) -> None:
    hosts = wh.resolve_webhook_allowed_hosts(
        "127.0.0.1", {wh.WEBHOOK_ALLOWED_HOSTS_ENV: "*,a.example"}
    )
    assert hosts is not None and "a.example" in hosts and "*" not in hosts
    only_star = wh.resolve_webhook_allowed_hosts(None, {wh.WEBHOOK_ALLOWED_HOSTS_ENV: "*"})
    assert only_star is not None and only_star == wh.resolve_webhook_allowed_hosts(None, {})


def test_l6_warning_text_names_hmac_independence_proxy_and_hosts_setting() -> None:
    text = wh.NON_LOOPBACK_WARNING.format(host="0.0.0.0")
    for needle in (
        "HMAC",
        "independently of dashboard auth",
        "reverse proxy",
        wh.WEBHOOK_ALLOWED_HOSTS_ENV,
        "0.0.0.0",
    ):
        assert needle in text


# L3 / L5 / L1 ---------------------------------------------------------------------------------


def test_l3_short_secret_is_refused_503(h: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(SECRET_ENV, "short")
    assert len("short") < wh.MIN_WEBHOOK_SECRET_BYTES
    assert h.post(secret="short").status_code == 503 and h.requests() == []
    assert h.events[-1][1]["reason"] == "secret_unresolvable"
    assert "secret_too_short" in [f.get("reason") for _, f in h.events]
    monkeypatch.setenv(SECRET_ENV, "x" * wh.MIN_WEBHOOK_SECRET_BYTES)
    assert h.post(secret="x" * wh.MIN_WEBHOOK_SECRET_BYTES).status_code == 202


def test_l3_short_secret_file_refused(tmp_path: Path) -> None:
    t = _file_target(tmp_path, 0o600)
    (Path(t.workspace_root) / "sec.txt").write_text("tiny\n")
    assert resolve_secret(t) is None


def test_l5_expired_requests_are_ignored_and_purged(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    assert h.post().status_code == 202
    src = WebhookSource(h.state, h.root, h.binding)
    ttl = timedelta(seconds=wh.WEBHOOK_REQUEST_TTL_SECONDS)
    assert src.observe(h.binding, None, T0 + ttl - timedelta(seconds=1)) is not None  # type: ignore[arg-type]
    assert src.observe(h.binding, None, T0 + ttl + timedelta(seconds=1)) is None  # type: ignore[arg-type]
    h.clock.now = T0 + ttl + timedelta(seconds=5)  # schedule re-enabled long after
    assert h.post(body_for("new")).status_code == 202
    assert len(h.requests()) == 1  # the stale one was purged on enqueue


def test_l1_unexpected_store_error_fails_closed(tmp_path: Path) -> None:
    from types import SimpleNamespace

    class Reg:
        def __init__(self, p: Path) -> None: ...

        def load(self) -> Any:
            return SimpleNamespace(workspaces=[SimpleNamespace(root=str(tmp_path), schedules=True)])

    class Boom:
        def __init__(self, root: str) -> None: ...

        def load(self) -> Any:
            raise PermissionError("schedules.yaml")

    look = make_binding_lookup(tmp_path / "r.yaml", registry_factory=Reg, store_factory=Boom)  # type: ignore[arg-type]
    assert look(_slug_for_root(str(tmp_path.resolve())), "x") is None


# -- re-review fixes (w18-02: N1-N6) -------------------------------------------------------------


def _mk(path: Path) -> Path:
    path.mkdir()
    return path


def _downgrade_headers(ts: str, body: bytes) -> tuple[bytes, dict[str, str]]:
    """What a replayer builds from a captured X-AO delivery: the same signature re-sent in GitHub
    form over the forged body ``"{ts}." + body`` (a valid body-only sig, no timestamp check)."""
    sig = compute_signature(SECRET.encode(), body, ts)
    forged = ts.encode() + b"." + body
    assert compute_signature(SECRET.encode(), forged) == sig  # the algebra the attack relies on
    return forged, {
        "Content-Type": "application/json",
        "X-Hub-Signature-256": sig,
        "X-GitHub-Delivery": "attacker-fresh",
    }


def test_n1_scheme_downgrade_replay_is_401_even_on_a_fresh_app(tmp_path: Path) -> None:
    """D1/D1b: after a restart (fresh nonce state) a captured X-AO delivery must not be accepted
    in GitHub form, days later."""
    h = Harness(tmp_path)
    ts = _ts(h)
    assert h.post(headers={"X-AO-Timestamp": ts}).status_code == 202
    forged, hdrs = _downgrade_headers(ts, BODY)
    h.clock.now += timedelta(days=3)
    for victim in (h, Harness(_mk(tmp_path / "restarted"))):  # same process, and a restarted one
        victim.clock.now = h.clock.now
        r = victim.client.post(f"/hooks/{victim.slug}/hook", content=forged, headers=hdrs)
        assert r.status_code == 401
    assert len(h.requests()) == 1  # only the original delivery was ever persisted


def test_n1_downgrade_stays_wrong_scheme_even_when_the_nonce_cache_is_empty(
    tmp_path: Path,
) -> None:
    """The ``ao`` pin, not the nonce cache, refuses a GitHub-form replay of an X-AO capture: evict
    everything from a tiny cache and the forged request is still ``wrong_scheme``/401."""
    h = Harness(tmp_path, nonce_cache_factory=lambda rate: NonceCache(2))
    ts = _ts(h)
    assert h.post(headers={"X-AO-Timestamp": ts}).status_code == 202
    forged, hdrs = _downgrade_headers(ts, BODY)
    for i in range(3):  # 2 keys per delivery vs capacity 2: the original entry is gone
        assert h.post(body_for(str(i))).status_code == 202
    h.events.clear()
    r = h.client.post(f"/hooks/{h.slug}/hook", content=forged, headers=hdrs)
    assert r.status_code == 401


def test_n1_pinned_scheme_rejects_the_other_header_in_both_directions(
    h: Harness, gh: Harness
) -> None:
    ts = _ts(h)
    ao_sig = compute_signature(SECRET.encode(), BODY, ts)
    gh_sig = compute_signature(SECRET.encode(), BODY)
    base = {"Content-Type": "application/json"}
    # ``ao`` binding: a valid GitHub-form signature (alone, or next to a valid X-AO one) is refused
    r = h.client.post(
        f"/hooks/{h.slug}/hook", content=BODY, headers={**base, "X-Hub-Signature-256": gh_sig}
    )
    assert r.status_code == 401
    both = {**base, "X-Hub-Signature-256": gh_sig, "X-AO-Signature": ao_sig, "X-AO-Timestamp": ts}
    assert h.client.post(f"/hooks/{h.slug}/hook", content=BODY, headers=both).status_code == 401
    # ``github`` binding: a valid X-AO signature is refused
    r = gh.client.post(
        f"/hooks/{gh.slug}/hook",
        content=BODY,
        headers={**base, "X-AO-Signature": ao_sig, "X-AO-Timestamp": ts},
    )
    assert r.status_code == 401
    assert h.requests() == [] and gh.requests() == []
    assert {f.get("reason") for _, f in h.events + gh.events} == {"wrong_scheme"}


def test_n1_default_scheme_is_ao_and_unknown_scheme_is_a_load_error() -> None:
    assert WebhookSpec(secret_env=SECRET_ENV).scheme == "ao"
    assert WebhookSpec(secret_env=SECRET_ENV, scheme="github").scheme == "github"
    with pytest.raises(ValidationError):
        WebhookSpec(secret_env=SECRET_ENV, scheme="both")  # type: ignore[arg-type]


def test_n3_nonce_caches_are_per_schedule_and_sized_for_the_replay_window(tmp_path: Path) -> None:
    for rate in (1, 10, 600):
        admitted = rate * (1 + wh.REPLAY_WINDOW_SECONDS // 60)  # burst + one window of refill
        assert wh.nonce_capacity(rate) >= admitted * wh.NONCE_KEYS_PER_DELIVERY
    made: list[int] = []

    def factory(rate: int) -> NonceCache:
        made.append(rate)
        return NonceCache(wh.nonce_capacity(rate))

    h = Harness(tmp_path, nonce_cache_factory=factory)
    assert h.post(body_for("a")).status_code == 202
    assert h.post(body_for("b")).status_code == 202
    assert made == [10]  # one cache for the schedule, created lazily at its own rate


def test_n3_other_schedule_traffic_cannot_evict_a_replayable_entry(tmp_path: Path) -> None:
    env = env_for(tmp_path, [wh_binding("a"), wh_binding("b", rate_per_minute=100000)])
    h = Harness(tmp_path)
    by_id = {b.id: b for b in (env.binding("a"), env.binding("b"))}
    h.root, h.slug = env.root, _slug_for_root(env.root)
    h._lookup = lambda slug, sid: (  # type: ignore[method-assign]
        WebhookTarget(env.root, by_id[sid]) if slug == h.slug and sid in by_id else None
    )
    h.app = build_webhook_app(h._lookup, h.queue, clock=h.clock, emit=lambda e, **f: None)
    h.client = TestClient(h.app, base_url=HOST)
    h.queue._maxsize = 10**6  # type: ignore[attr-defined]
    assert h.post(sid="a").status_code == 202
    for i in range(600):  # > the old global LRU (1024 entries / 2 keys per delivery)
        assert h.post(body_for(str(i)), sid="b").status_code == 202
    assert h.post(sid="a").status_code == 409  # a's own entry is still there


    assert {f.get("reason") for _, f in h.events} == {"wrong_scheme"}
    assert len(h.requests()) == 4  # nothing from the forged request was persisted


def test_n1_github_binding_replay_is_409_until_evicted_then_accepted(tmp_path: Path) -> None:
    """Eviction genuinely happens (tiny cache, ``github`` binding where the body-only signature has
    no timestamp bound): the replay is 409 while its nonce is cached and 202 once evicted. This
    pins that the cache is the *only* barrier there -- hence ``nonce_capacity`` sizing (next)."""
    h = Harness(
        tmp_path,
        wh_binding(scheme="github", rate_per_minute=100000),
        nonce_cache_factory=lambda rate: NonceCache(4),
    )
    hdrs = {"X-GitHub-Delivery": "d-0"}
    assert h.post(sig_header="X-Hub-Signature-256", headers=hdrs).status_code == 202
    assert h.post(sig_header="X-Hub-Signature-256", headers=hdrs).status_code == 409  # cached
    for i in range(3):  # 2 keys each -> pushes d-0's two keys out of the 4-slot LRU
        h.post(
            body_for(str(i)),
            sig_header="X-Hub-Signature-256",
            headers={"X-GitHub-Delivery": f"e{i}"},
        )
    assert h.post(sig_header="X-Hub-Signature-256", headers=hdrs).status_code == 202  # evicted


def test_n1_github_binding_default_cache_holds_every_admittable_nonce(tmp_path: Path) -> None:
    """With the real ``nonce_capacity`` sizing, a burst the bucket admits (rate=5) cannot evict
    the first delivery: its replay is still 409, and traffic past the burst is 429, not stored."""
    h = Harness(tmp_path, wh_binding(scheme="github", rate_per_minute=5))
    first = {"X-GitHub-Delivery": "d-0"}
    assert h.post(sig_header="X-Hub-Signature-256", headers=first).status_code == 202
    for i in range(4):
        r = h.post(
            body_for(str(i)),
            sig_header="X-Hub-Signature-256",
            headers={"X-GitHub-Delivery": f"e{i}"},
        )
        assert r.status_code == 202
    assert h.post(body_for("x"), sig_header="X-Hub-Signature-256").status_code == 429
    assert h.post(sig_header="X-Hub-Signature-256", headers=first).status_code == 409
def test_n4_same_body_two_timestamps_same_second_both_launch(tmp_path: Path) -> None:
    """C1: with no delivery id the fallback discriminator must not collide for a re-signed body."""
    env = env_for(tmp_path, [wh_binding(rate_per_minute=1000, overlap="allow")])
    env.engine.register_trigger_source(
        "webhook", lambda root, b: WebhookSource(env.tmp / "state", root, b)
    )
    h = Harness(tmp_path)
    h.root, h.slug, h.binding = env.root, _slug_for_root(env.root), env.binding("hook")
    now_ts = int(h.clock.now.timestamp())
    for ts in (now_ts - 1, now_ts):  # same body, same server second, different signed timestamps
        assert h.post(headers={"X-AO-Timestamp": str(ts)}).status_code == 202
    env.tick(T0 + timedelta(seconds=1))
    env.end_run("run-1")  # one active run at a time: let the first finish
    env.tick(T0 + timedelta(seconds=2))
    assert len(env.launcher.decisions) == 2
    assert h.requests() == []


def test_long_delivery_id_is_stored_as_a_digest(h: Harness) -> None:
    import hashlib

    long_id = "d" * 5000
    assert h.post(headers={"X-AO-Delivery": long_id}).status_code == 202
    stored = json.loads(h.requests()[0].read_text())["delivery_id"]
    assert stored == hashlib.sha256(long_id.encode()).hexdigest()


@pytest.mark.parametrize("name", ["PATH", "HOME", "LC_ALL", "XDG_RUNTIME_DIR", "LANG"])
def test_n5_ambient_env_var_is_never_a_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    monkeypatch.setenv(name, "/long/ambient/value/that/is/over/sixteen/bytes")
    h = Harness(tmp_path, wh_binding(secret_env=name))
    r = h.post(secret="/long/ambient/value/that/is/over/sixteen/bytes")
    assert r.status_code == 503 and h.requests() == []
    assert "secret_ambient_env" in [f.get("reason") for _, f in h.events]


def test_n5_dedicated_env_var_still_works(h: Harness) -> None:
    assert h.post().status_code == 202


def test_n6_lookup_failure_is_logged_once_without_content_and_fails_closed(tmp_path: Path) -> None:
    from types import SimpleNamespace

    class Boom:
        def __init__(self, root: str) -> None: ...

        def load(self) -> Any:
            raise PermissionError("/secret/path/schedules.yaml")

    class Reg:
        def __init__(self, p: Path) -> None: ...

        def load(self) -> Any:
            return SimpleNamespace(workspaces=[SimpleNamespace(root=str(tmp_path), schedules=True)])

    class BadReg:
        def __init__(self, p: Path) -> None: ...

        def load(self) -> Any:
            raise ValueError("corrupt: hunter2")

    events: list[tuple[str, dict[str, Any]]] = []

    def emit(e: str, **f: Any) -> None:
        events.append((e, f))

    slug = _slug_for_root(str(tmp_path.resolve()))
    for reg, store, source, err in (
        (Reg, Boom, "schedules", "PermissionError"),
        (BadReg, Boom, "registry", "ValueError"),
    ):
        events.clear()
        look = make_binding_lookup(
            tmp_path / "r.yaml",
            registry_factory=reg,  # type: ignore[arg-type]
            store_factory=store,  # type: ignore[arg-type]
            emit=emit,
        )
        assert look(slug, "x") is None
        assert events == [("webhook.lookup_failed", {"source": source, "error": err})]
