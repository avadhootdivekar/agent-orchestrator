"""Tests for the Host/Origin/Content-Type hardening middleware (1A.3, `ui/security.py`).

Threat model reminder (see `security.py`'s module docstring and `htmlpreview.py`'s): the
dashboard has no authentication and `POST /api/runs` is an arbitrary-code-execution-and-
spend primitive, so this closes DNS rebinding (Host) and CSRF (Origin/Content-Type) gaps
that pre-date this module.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi", reason="dashboard API needs the optional [ui] extra")
from fastapi.testclient import TestClient  # noqa: E402

from agent_orchestrator.project_config import ProjectConfig  # noqa: E402
from agent_orchestrator.ui.app import API_PREFIX, create_app  # noqa: E402
from agent_orchestrator.ui.security import (  # noqa: E402
    DEFAULT_ALLOWED_HOSTS,
    SPA_CSP,
    resolve_allowed_hosts,
)
from agent_orchestrator.ui.service import DashboardService  # noqa: E402

from .conftest import StubSupervisor  # noqa: E402


@pytest.fixture()
def hardened_client(workspace: Path, stub_supervisor: StubSupervisor) -> Iterator[TestClient]:
    """A client whose app has an EXPLICIT allowlist (bypassing the conftest-wide
    `AO_UI_ALLOWED_HOSTS=testserver` override), so tests here control the Host allowlist
    directly rather than relying on env state set up for the rest of the suite."""
    service = DashboardService(
        str(workspace),
        supervisor=stub_supervisor,  # type: ignore[arg-type]
        project_config=ProjectConfig(),
    )
    app = create_app(service, allowed_hosts=DEFAULT_ALLOWED_HOSTS)
    with TestClient(app, base_url="http://localhost") as client:
        yield client


class TestResolveAllowedHosts:
    def test_defaults_include_loopback_forms(self) -> None:
        hosts = resolve_allowed_hosts(env={})
        assert hosts == DEFAULT_ALLOWED_HOSTS

    def test_bound_host_is_added(self) -> None:
        hosts = resolve_allowed_hosts(bound_host="dashboard.local", env={})
        assert hosts is not None
        assert "dashboard.local" in hosts
        assert DEFAULT_ALLOWED_HOSTS <= hosts

    def test_env_var_adds_extra_hosts(self) -> None:
        hosts = resolve_allowed_hosts(env={"AO_UI_ALLOWED_HOSTS": "example.com, other.test"})
        assert hosts is not None
        assert {"example.com", "other.test"} <= hosts

    def test_wildcard_disables_the_check(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level("WARNING"):
            hosts = resolve_allowed_hosts(env={"AO_UI_ALLOWED_HOSTS": "*"})
        assert hosts is None
        assert any("DISABLED" in r.message for r in caplog.records)

    def test_wildcard_mixed_with_other_entries_still_disables(self) -> None:
        # "*" anywhere in the comma list disables — an operator who typos an extra host in
        # alongside "*" should not get a false sense of a still-restricted allowlist.
        assert resolve_allowed_hosts(env={"AO_UI_ALLOWED_HOSTS": "example.com,*"}) is None

    @pytest.mark.parametrize(
        "raw, normalized",
        [
            ("127.0.0.1:8765", "127.0.0.1"),
            ("[::1]:8765", "::1"),
            ("[::1]", "::1"),
            ("LOCALHOST", "localhost"),
        ],
    )
    def test_host_forms_normalize_consistently(self, raw: str, normalized: str) -> None:
        hosts = resolve_allowed_hosts(bound_host=raw, env={})
        assert hosts is not None
        assert normalized in hosts

    def test_unbracketed_ipv6_literal_is_rejected_not_normalized(self) -> None:
        # L3 doc fix: a bare "::1" (no brackets) is genuinely ambiguous with `host:port`
        # syntax -- it does NOT normalize to "::1" the way "[::1]" does. It fails closed
        # (simply isn't added), which is safe, but must not be assumed to work.
        hosts = resolve_allowed_hosts(bound_host="::1", env={})
        assert hosts == DEFAULT_ALLOWED_HOSTS  # unchanged -- the bare literal added nothing

    def test_userinfo_embedded_in_a_bound_host_is_rejected(self) -> None:
        # L3: urlsplit() would otherwise silently drop "attacker@" and normalize just the
        # trailing host, letting a crafted value in via `_normalize_host` too.
        hosts = resolve_allowed_hosts(bound_host="attacker@localhost", env={})
        assert hosts == DEFAULT_ALLOWED_HOSTS


class TestHostAllowlistMiddleware:
    def test_allowed_host_passes(self, hardened_client: TestClient) -> None:
        response = hardened_client.get(f"{API_PREFIX}/health", headers={"host": "localhost"})
        assert response.status_code == 200

    def test_unrecognized_host_is_rejected_with_421(self, hardened_client: TestClient) -> None:
        response = hardened_client.get(f"{API_PREFIX}/health", headers={"host": "evil.example"})
        assert response.status_code == 421

    def test_dns_rebinding_style_host_is_rejected(self, hardened_client: TestClient) -> None:
        # The literal attack this closes: an attacker-controlled domain whose DNS answer
        # happens to be 127.0.0.1 -- the browser's Host header still names THEIR domain.
        response = hardened_client.get(
            f"{API_PREFIX}/health", headers={"host": "attacker-controlled.example"}
        )
        assert response.status_code == 421

    def test_host_with_port_is_still_recognized(self, hardened_client: TestClient) -> None:
        response = hardened_client.get(f"{API_PREFIX}/health", headers={"host": "localhost:9999"})
        assert response.status_code == 200

    def test_wildcard_env_var_disables_the_check(
        self,
        workspace: Path,
        stub_supervisor: StubSupervisor,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("AO_UI_ALLOWED_HOSTS", "*")
        service = DashboardService(
            str(workspace),
            supervisor=stub_supervisor,  # type: ignore[arg-type]
            project_config=ProjectConfig(),
        )
        # No explicit allowed_hosts -- create_app() resolves the env var itself.
        with TestClient(create_app(service)) as client:
            response = client.get(f"{API_PREFIX}/health", headers={"host": "anything.example"})
        assert response.status_code == 200


class TestOriginAndContentType:
    def test_missing_origin_on_post_is_allowed(self, hardened_client: TestClient) -> None:
        # curl / CLI / non-browser clients never send Origin -- they are legitimate here,
        # since this server has no other auth mechanism at all.
        response = hardened_client.post(f"{API_PREFIX}/runs", json={"options": {}})
        assert response.status_code != 403

    def test_same_origin_post_is_allowed(self, hardened_client: TestClient) -> None:
        # `hardened_client`'s base_url is "http://localhost" (no explicit port), so the
        # Host header this request actually carries is "localhost" with no port -- the
        # Origin must match that exactly (scheme + host + port), not just share a host.
        response = hardened_client.post(
            f"{API_PREFIX}/runs",
            json={"options": {}},
            headers={"origin": "http://localhost"},
        )
        assert response.status_code != 403

    def test_same_origin_post_is_allowed_with_a_matching_explicit_port(
        self, hardened_client: TestClient
    ) -> None:
        response = hardened_client.post(
            f"{API_PREFIX}/runs",
            json={"options": {}},
            headers={"origin": "http://localhost:8765", "host": "localhost:8765"},
        )
        assert response.status_code != 403

    def test_origin_with_a_different_port_than_the_request_is_rejected(
        self, hardened_client: TestClient
    ) -> None:
        # L3: host-only comparison used to accept this (same host, wrong port/scheme is
        # not actually the same origin).
        response = hardened_client.post(
            f"{API_PREFIX}/runs",
            json={"options": {}},
            headers={"origin": "http://localhost:9999", "host": "localhost:8765"},
        )
        assert response.status_code == 403

    def test_origin_with_a_different_scheme_than_the_request_is_rejected(
        self, hardened_client: TestClient
    ) -> None:
        response = hardened_client.post(
            f"{API_PREFIX}/runs",
            json={"options": {}},
            headers={"origin": "https://localhost", "host": "localhost"},
        )
        assert response.status_code == 403

    def test_origin_with_embedded_userinfo_is_rejected(self, hardened_client: TestClient) -> None:
        # L3: urlsplit() silently drops a "user@" prefix and normalizes the trailing
        # host -- must be rejected outright rather than normalized past.
        response = hardened_client.post(
            f"{API_PREFIX}/runs",
            json={"options": {}},
            headers={"origin": "http://evil.example@localhost"},
        )
        assert response.status_code == 403

    def test_cross_origin_post_is_rejected_with_403(self, hardened_client: TestClient) -> None:
        response = hardened_client.post(
            f"{API_PREFIX}/runs",
            json={"options": {}},
            headers={"origin": "http://evil.example"},
        )
        assert response.status_code == 403

    def test_cross_origin_delete_is_rejected_with_403(self, hardened_client: TestClient) -> None:
        response = hardened_client.delete(
            f"{API_PREFIX}/runs/some-run", headers={"origin": "http://evil.example"}
        )
        assert response.status_code == 403

    def test_get_is_never_origin_checked(self, hardened_client: TestClient) -> None:
        # GET is not a mutating method -- a cross-origin Origin header on a read must not
        # be blocked (that would break nothing security-relevant and only annoy readers).
        response = hardened_client.get(
            f"{API_PREFIX}/health", headers={"origin": "http://evil.example"}
        )
        assert response.status_code == 200

    def test_wrong_content_type_on_a_bodied_post_is_rejected_with_415(
        self, hardened_client: TestClient
    ) -> None:
        response = hardened_client.post(
            f"{API_PREFIX}/runs",
            content=b'{"options": {}}',
            headers={"content-type": "text/plain"},
        )
        assert response.status_code == 415

    def test_form_urlencoded_content_type_is_rejected_with_415(
        self, hardened_client: TestClient
    ) -> None:
        # The concrete <form>-CSRF vector: a browser form cannot set Content-Type to
        # application/json, only these simple types.
        response = hardened_client.post(
            f"{API_PREFIX}/runs",
            content=b"workflow_path=x",
            headers={"content-type": "application/x-www-form-urlencoded"},
        )
        assert response.status_code == 415

    def test_correct_json_content_type_on_a_bodied_post_is_allowed(
        self, hardened_client: TestClient
    ) -> None:
        response = hardened_client.post(f"{API_PREFIX}/runs", json={"options": {}})
        assert response.status_code != 415

    def test_bodyless_post_is_exempt_from_the_content_type_check(
        self, hardened_client: TestClient
    ) -> None:
        # Existing, legitimate dashboard behaviour (`POST /runs/{id}/resume` with no body)
        # must keep working -- see _requires_json_content_type's docstring for why this is
        # still safe (the Origin check above independently covers the cross-origin case).
        response = hardened_client.post(f"{API_PREFIX}/runs/nope/resume")
        assert response.status_code != 415

    def test_bodyless_delete_is_exempt_from_the_content_type_check(
        self, hardened_client: TestClient
    ) -> None:
        response = hardened_client.delete(f"{API_PREFIX}/runs/nope")
        assert response.status_code != 415

    def test_content_type_with_charset_suffix_is_still_accepted(
        self, hardened_client: TestClient
    ) -> None:
        response = hardened_client.post(
            f"{API_PREFIX}/runs",
            content=b'{"options": {}}',
            headers={"content-type": "application/json; charset=utf-8"},
        )
        assert response.status_code != 415


class TestSecurityHeaders:
    def test_static_headers_are_present_on_every_response(
        self, hardened_client: TestClient
    ) -> None:
        response = hardened_client.get(f"{API_PREFIX}/health")
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["Referrer-Policy"] == "no-referrer"
        assert response.headers["Cross-Origin-Opener-Policy"] == "same-origin"
        assert response.headers["Cross-Origin-Resource-Policy"] == "same-origin"

    def test_json_responses_do_not_carry_the_spa_csp(self, hardened_client: TestClient) -> None:
        response = hardened_client.get(f"{API_PREFIX}/health")
        assert "content-security-policy" not in {k.lower() for k in response.headers}

    def test_spa_document_response_carries_the_csp(self, hardened_client: TestClient) -> None:
        response = hardened_client.get("/")
        if response.status_code == 200:  # built frontend present
            assert response.headers.get("content-security-policy") == SPA_CSP
        else:
            # No built frontend in this checkout -- the 503 placeholder is JSON, not the
            # SPA document, so it correctly carries no CSP either way.
            assert response.status_code == 503


class TestExistingRoutesStillWork:
    """Existing tests/ui/* behaviour (bodyless resume/delete, JSON POSTs) is exercised by
    test_api_integration.py against the conftest-wide allowlist; this class only pins the
    two exemptions this middleware specifically had to preserve, using this file's own
    explicit-allowlist client."""

    def test_resume_without_a_body_still_returns_a_non_415(
        self, hardened_client: TestClient
    ) -> None:
        assert hardened_client.post(f"{API_PREFIX}/runs/nope/resume").status_code != 415

    def test_delete_without_a_body_still_returns_a_non_415(
        self, hardened_client: TestClient
    ) -> None:
        assert hardened_client.delete(f"{API_PREFIX}/runs/nope").status_code != 415
