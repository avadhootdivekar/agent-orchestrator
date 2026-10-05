"""``runtime.py``: realms, the object graph, ``runtime_of``, ``audit_log_for`` (T-XchniS AC 14)."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from agent_orchestrator.auth import runtime as runtime_mod
from agent_orchestrator.auth.audit import AuditLog
from agent_orchestrator.auth.constants import (
    APP_STATE_AUTH_KEY,
    HUB_LOGIN_PATH,
    HUB_REALM_ID,
    LOCAL_PROVIDER_ID,
    WORKSPACE_ID_HEX_CHARS,
)
from agent_orchestrator.auth.errors import AuthError, ErrorCode, TooManyAttemptsError
from agent_orchestrator.auth.local_provider import LocalPasswordProvider
from agent_orchestrator.auth.model import TotpPolicy
from agent_orchestrator.auth.provider import AuthProvider, ClientInfo, Revalidation, UserView
from agent_orchestrator.auth.runtime import (
    AuthRuntime,
    Realm,
    audit_log_for,
    build_auth_runtime,
    runtime_of,
)
from agent_orchestrator.auth.sessions import InMemorySessionStore
from agent_orchestrator.auth.store import add_user

from .helpers.core import FakeClock, SeededEntropy, run_async
from .helpers.crypto import FastFakeHasher
from .helpers.provider import CLIENT, PASSWORD, fake_hash, make_settings


class FakeProvider(AuthProvider):
    provider_id = "fake"

    def check_ready(self) -> None:
        return None

    def revalidate(self, user_id: str, credential_epoch: int) -> Revalidation:
        return Revalidation.VALID

    def user_view(self, username: str) -> UserView | None:
        return None


def build(tmp_path: Path, **kw: Any) -> AuthRuntime:
    settings = kw.pop("settings", None) or make_settings(tmp_path)
    realm = kw.pop("realm", Realm("hub", 8770))
    kw.setdefault("hasher", FastFakeHasher())
    kw.setdefault("clock", FakeClock())
    kw.setdefault("entropy", SeededEntropy(1))
    return build_auth_runtime(settings, realm, **kw)


# --- Realm ----------------------------------------------------------------------------------------


def test_a_ui_realm_id_is_a_digest_of_the_resolved_workspace_not_the_port(tmp_path: Path) -> None:
    expected = "ui:" + hashlib.sha256(str(tmp_path.resolve()).encode()).hexdigest()[:12]
    assert WORKSPACE_ID_HEX_CHARS == 12
    assert Realm("ui", 8765, tmp_path).id == expected
    assert Realm("ui", 8766, tmp_path).id == expected


def test_a_ui_realm_id_follows_a_symlinked_workspace(tmp_path: Path) -> None:
    real = tmp_path / "ws"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    assert Realm("ui", 1, link).id == Realm("ui", 1, real).id


def test_different_workspaces_get_different_realm_ids(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    assert Realm("ui", 8765, tmp_path / "a").id != Realm("ui", 8765, tmp_path / "b").id


def test_the_hub_realm_id_is_fixed(tmp_path: Path) -> None:
    assert Realm("hub", 8770).id == HUB_REALM_ID == "hub"
    assert Realm("hub", 8770, tmp_path).id == "hub"  # a workspace never changes the hub


def test_a_ui_realm_needs_a_workspace_root() -> None:
    with pytest.raises(ValueError, match="workspace_root"):
        Realm("ui", 8765)


def test_cookie_names_carry_the_port_and_the_host_prefix_only_when_secure(tmp_path: Path) -> None:
    realm = Realm("ui", 8765, tmp_path)
    assert realm.cookie_name(secure=False) == "ao_sid_8765"
    assert realm.cookie_name(secure=True) == "__Host-ao_sid_8765"
    assert Realm("hub", 8770).cookie_name(secure=False) == "ao_sid_8770"


def test_login_paths(tmp_path: Path) -> None:
    assert Realm("hub", 8770).login_path == HUB_LOGIN_PATH == "/login"
    assert Realm("ui", 8765, tmp_path).login_path == "/"


def test_realm_is_immutable(tmp_path: Path) -> None:
    with pytest.raises(AttributeError):
        Realm("hub", 1).port = 2  # type: ignore[misc]


# --- build_auth_runtime -------------------------------------------------------------------------


def test_disabled_settings_are_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="enabled"):
        build(tmp_path, settings=make_settings(tmp_path, enabled=False))


def test_the_default_graph_is_local_password_and_shares_one_store_set(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, session_idle_seconds=600, session_absolute_seconds=7200)
    clock = FakeClock()
    runtime = build(tmp_path, settings=settings, clock=clock, realm=Realm("ui", 8765, tmp_path))
    assert isinstance(runtime.provider, LocalPasswordProvider)
    assert runtime.provider.provider_id == LOCAL_PROVIDER_ID
    assert runtime.settings is settings and runtime.clock is clock
    assert runtime.paths.store_dir == settings.store_dir
    assert runtime.paths.state_dir == settings.state_dir
    assert runtime.store.paths is runtime.paths and runtime.lockouts is not None
    assert isinstance(runtime.audit, AuditLog)
    assert runtime.totp is None  # T-yfrfxv wires it
    assert runtime.proxy_suspected_warned is False
    assert runtime.first_insecure_login_warned is False
    assert runtime.address_throttle.retry_after("203.0.113.9") is None


def test_the_session_manager_is_bound_to_the_realm_and_the_settings(tmp_path: Path) -> None:
    from .helpers.sessions import make_identity

    settings = make_settings(tmp_path, session_idle_seconds=600, session_absolute_seconds=7200)
    realm = Realm("ui", 8765, tmp_path)
    store = InMemorySessionStore()
    runtime = build(tmp_path, settings=settings, realm=realm, session_store=store)
    issued = runtime.sessions.issue(
        make_identity(), make_identity().next_state, client_key="k", auth_method="password"
    )
    assert issued.record.realm == realm.id
    assert store.get(issued.record.token_hash) is not None  # the injected store holds it
    times = runtime.sessions.times(issued.record)
    assert times.idle_timeout_seconds == 600
    assert (times.absolute_expires_at - runtime.clock.now_utc()).total_seconds() == 7200


def test_an_injected_provider_is_used_and_no_local_provider_is_built(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*_a: object, **_k: object) -> None:
        raise AssertionError("a scrypt hasher must not be built when a provider is injected")

    monkeypatch.setattr(runtime_mod, "BoundedScryptHasher", forbidden)
    fake = FakeProvider()
    runtime = build_auth_runtime(make_settings(tmp_path), Realm("hub", 1), provider=fake)
    assert runtime.provider is fake


def test_the_default_hasher_is_the_bounded_scrypt_one_seeded_with_the_entropy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, Any] = {}

    def factory(**kw: Any) -> FastFakeHasher:
        seen.update(kw)
        return FastFakeHasher()

    monkeypatch.setattr(runtime_mod, "BoundedScryptHasher", factory)
    entropy = SeededEntropy(9)
    build_auth_runtime(make_settings(tmp_path), Realm("hub", 1), entropy=entropy)
    assert seen == {"entropy": entropy}


def test_an_injected_audit_log_is_shared(tmp_path: Path) -> None:
    audit = AuditLog.for_state_dir(tmp_path / "state")
    runtime = build(tmp_path, audit=audit)
    assert runtime.audit is audit


def test_build_is_pure_construction_and_never_checks_readiness(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    runtime = build(tmp_path, settings=settings)
    assert not settings.store_dir.joinpath("users.json").exists()
    assert not settings.state_dir.joinpath("lockouts.json").exists()
    with pytest.raises(Exception, match="ao auth add-user"):
        runtime.provider.check_ready()  # only prepare_auth calls this


def test_two_realms_share_the_account_lockout_through_the_state_file(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, lockout_threshold=2, address_threshold=50)
    hub = build(tmp_path, settings=settings, realm=Realm("hub", 8770))
    ui = build(tmp_path, settings=settings, realm=Realm("ui", 8765, tmp_path))
    hub.store.mutate(
        lambda f: add_user(
            f, "alice", fake_hash(PASSWORD), now=hub.clock.now_utc(), entropy=SeededEntropy(2)
        ),
        create=True,
    )
    hub.provider.check_ready()
    assert isinstance(hub.provider, LocalPasswordProvider)
    assert isinstance(ui.provider, LocalPasswordProvider)
    for _ in range(2):
        with pytest.raises(AuthError) as caught:
            run_async(hub.provider.authenticate("alice", "wrong", CLIENT))
        assert caught.value.code is ErrorCode.INVALID_CREDENTIALS
    with pytest.raises(TooManyAttemptsError):
        run_async(
            ui.provider.authenticate("alice", PASSWORD, ClientInfo("198.51.100.1", False, False))
        )
    # Distinct realms audit under their own labels.
    assert {e["realm"] for e in _audit(hub)} == {"hub"}


def _audit(runtime: AuthRuntime) -> list[dict[str, Any]]:
    from .helpers.provider import read_audit

    return read_audit(runtime.paths)


def test_a_totp_policy_off_is_still_buildable(tmp_path: Path) -> None:
    runtime = build(tmp_path, settings=make_settings(tmp_path, totp=TotpPolicy.OFF))
    assert runtime.settings.totp is TotpPolicy.OFF


# --- ClientInfo ---------------------------------------------------------------------------------


def test_client_info_defaults_proxy_suspected_to_false() -> None:
    info = ClientInfo("203.0.113.9", True, False)
    assert info.proxy_suspected is False
    assert replace(info, proxy_suspected=True).proxy_suspected is True
    with pytest.raises(AttributeError):
        info.key = "x"  # type: ignore[misc]


# --- runtime_of / audit_log_for -------------------------------------------------------------------


def make_app(runtime: object | None = None, *, set_state: bool = True) -> Any:
    state = SimpleNamespace()
    if set_state:
        setattr(state, APP_STATE_AUTH_KEY, runtime)
    return SimpleNamespace(state=state)


def test_runtime_of_returns_the_app_state_value_or_none(tmp_path: Path) -> None:
    runtime = build(tmp_path)
    assert runtime_of(make_app(runtime)) is runtime
    assert runtime_of(make_app(None)) is None
    assert runtime_of(make_app(set_state=False)) is None  # an app that never installed auth


def test_audit_log_for_returns_the_runtime_audit_or_none(tmp_path: Path) -> None:
    runtime = build(tmp_path)
    assert audit_log_for(SimpleNamespace(app=make_app(runtime))) is runtime.audit
    assert audit_log_for(SimpleNamespace(app=make_app(None))) is None


def test_a_provider_without_warnings_inherits_the_empty_default() -> None:
    assert FakeProvider().startup_warnings() == []
