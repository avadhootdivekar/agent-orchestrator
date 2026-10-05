"""T-kwwJ82: the ``Principal`` contract (HLD 2.6 v2.1) and the request helpers."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from agent_orchestrator.auth.constants import SCOPE_AUTH_ENABLED_KEY, SCOPE_PRINCIPAL_KEY
from agent_orchestrator.auth.errors import AuthError, ErrorCode
from agent_orchestrator.auth.principal import (
    Principal,
    auth_enabled,
    current_principal,
    require_principal,
)

AUTH_TIME = datetime(2026, 1, 1, tzinfo=UTC)
FIELD_ORDER = [
    "username",
    "auth_method",
    "roles",
    "user_id",
    "realm",
    "session_id",
    "amr",
    "auth_time",
    "provider",
]


def make_principal(*positional: Any, **overrides: Any) -> Principal:
    kwargs: dict[str, Any] = {
        "user_id": "u" * 32,
        "realm": "hub",
        "session_id": "s" * 32,
        "amr": ("pwd",),
        "auth_time": AUTH_TIME,
        "provider": "local-password",
    }
    kwargs.update(overrides)
    return Principal(*positional, **kwargs)


def test_field_order_matches_the_contract() -> None:
    assert [f.name for f in dataclasses.fields(Principal)] == FIELD_ORDER


def test_fields_after_roles_are_keyword_only() -> None:
    by_name = {f.name: f for f in dataclasses.fields(Principal)}
    assert not any(by_name[n].kw_only for n in ("username", "auth_method", "roles"))
    assert all(by_name[n].kw_only for n in FIELD_ORDER[3:])


def test_positional_minimum_contract_constructs() -> None:
    p = make_principal("alice", "password", ["admin"])
    assert (p.username, p.auth_method, p.roles) == ("alice", "password", ["admin"])


def test_roles_defaults_to_empty_list() -> None:
    p = make_principal("alice", "password")
    assert p.roles == []
    assert isinstance(p.roles, list)


def test_additive_fields_cannot_be_passed_positionally() -> None:
    with pytest.raises(TypeError):
        Principal("alice", "password", [], "uid", "hub", "sid", ("pwd",), AUTH_TIME, "p")  # type: ignore[misc]


def test_missing_keyword_only_field_is_a_type_error() -> None:
    with pytest.raises(TypeError):
        Principal("alice", "password")  # type: ignore[call-arg]


def test_frozen_slotted_and_hashable() -> None:
    p = make_principal("alice", "password")
    with pytest.raises(dataclasses.FrozenInstanceError):
        p.username = "bob"  # type: ignore[misc]
    assert not hasattr(p, "__dict__")
    assert isinstance(hash(p), int)
    assert isinstance(p.amr, tuple)


def test_roles_are_fresh_lists_never_shared() -> None:
    p1 = make_principal("alice", "password")
    p2 = make_principal("alice", "password")
    assert p1.roles is not p2.roles
    p1.roles.append("admin")
    assert p2.roles == []


def test_equal_principals_hash_equal_and_roles_still_compare() -> None:
    p1 = make_principal("alice", "password")
    p2 = make_principal("alice", "password")
    assert p1 == p2
    assert hash(p1) == hash(p2)
    assert p1 != make_principal("alice", "password", ["admin"])
    assert make_principal("alice", "password", ["admin"]) != make_principal("alice", "password")


def test_hash_ignores_roles() -> None:
    assert hash(make_principal("alice", "password", ["x"])) == hash(
        make_principal("alice", "password", ["y"])
    )


def test_principals_usable_as_set_members() -> None:
    assert len({make_principal("alice", "password"), make_principal("alice", "password")}) == 1


def request_with(**state: Any) -> SimpleNamespace:
    return SimpleNamespace(state=SimpleNamespace(**state))


def test_no_state_means_anonymous_and_auth_off() -> None:
    request = SimpleNamespace()
    assert current_principal(request) is None
    assert auth_enabled(request) is False
    assert require_principal(request) is None


def test_auth_disabled_returns_none_even_with_a_principal_attribute() -> None:
    request = request_with(**{SCOPE_AUTH_ENABLED_KEY: False})
    assert require_principal(request) is None


def test_auth_enabled_with_principal_returns_it() -> None:
    p = make_principal("alice", "password")
    request = request_with(**{SCOPE_AUTH_ENABLED_KEY: True, SCOPE_PRINCIPAL_KEY: p})
    assert auth_enabled(request) is True
    assert current_principal(request) is p
    assert require_principal(request) is p


def test_auth_enabled_without_principal_raises_not_authenticated() -> None:
    request = request_with(**{SCOPE_AUTH_ENABLED_KEY: True})
    with pytest.raises(AuthError) as excinfo:
        require_principal(request)
    assert excinfo.value.code is ErrorCode.NOT_AUTHENTICATED
    assert excinfo.value.status == 401


def test_auth_enabled_with_explicit_none_principal_raises() -> None:
    request = request_with(**{SCOPE_AUTH_ENABLED_KEY: True, SCOPE_PRINCIPAL_KEY: None})
    with pytest.raises(AuthError):
        require_principal(request)
