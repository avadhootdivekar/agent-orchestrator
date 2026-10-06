"""The hub's static login page and signed-in bar (HLD 11.18, 17.6; L4 but PURE: stdlib only).

The login page is a fixed string with no inline script, no ``on*`` attributes and no ``<style>``
(the dashboard-side CSP stays untouched); its behaviour lives in ``/auth-assets/hub-auth.js``.
Only :func:`render_signed_in_bar` interpolates data, and it escapes every value.
"""

# ruff: noqa: E501  (the HLD 17.6 page skeleton is kept verbatim, long lines included)
from __future__ import annotations

from html import escape
from importlib import resources
from typing import Final

HUB_ASSETS_PACKAGE: Final = "agent_orchestrator.auth"
HUB_ASSETS_DIR: Final = "assets"
HUB_ASSET_URL_PREFIX: Final = "/auth-assets/"
HUB_ASSET_TYPES: Final[dict[str, str]] = {
    "hub-auth.js": "text/javascript; charset=utf-8",
    "hub-auth.css": "text/css; charset=utf-8",
}

_HEAD_TAGS: Final = (
    f'<link rel="stylesheet" href="{HUB_ASSET_URL_PREFIX}hub-auth.css">'
    f'<script src="{HUB_ASSET_URL_PREFIX}hub-auth.js" defer></script>'
)

_LOGIN_PAGE: Final = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sign in · Agent Orchestrator Service</title>
{_HEAD_TAGS}</head>
<body><main id="ao-auth" data-page="login">
  <h1>Agent Orchestrator Service</h1>
  <p id="ao-transport-warning" class="ao-warn" hidden>This connection is not encrypted. Your password and codes can be read on the network.</p>
  <div id="ao-error" role="alert" aria-live="assertive" hidden></div>
  <form id="ao-login-form" novalidate>
    <label>Username <input id="ao-username" name="username" autocomplete="username" required></label>
    <label>Password <input id="ao-password" name="password" type="password" autocomplete="current-password" required></label>
    <button type="submit">Sign in</button></form>
  <form id="ao-totp-form" novalidate hidden>
    <label id="ao-code-label">Authentication code <input id="ao-code" inputmode="numeric" autocomplete="one-time-code" maxlength="7"></label>
    <label id="ao-recovery-label" hidden>Recovery code <input id="ao-recovery" autocomplete="off" maxlength="24"></label>
    <button type="button" id="ao-toggle-recovery">Use a recovery code instead</button>
    <button type="submit">Verify</button> <button type="button" id="ao-signout">Sign out</button></form>
  <section id="ao-enroll" hidden>
    <h2>Set up two-factor authentication</h2>
    <p>Add this account to your authenticator app with "Enter a setup key", then type the 6-digit code it shows.
       (QR codes are shown in workspace dashboards.)</p>
    <form id="ao-enroll-token-form" novalidate><label>Enrollment token (from your operator: <code>ao auth enrollment-token &lt;you&gt;</code>)
      <input id="ao-enroll-token" autocomplete="off" maxlength="24"></label><button type="submit">Continue</button></form>
    <p>Setup key: <code id="ao-enroll-secret"></code> <button type="button" id="ao-copy-secret">Copy</button></p>
    <p>Setup URI: <code id="ao-enroll-uri"></code> <button type="button" id="ao-copy-uri">Copy</button></p>
    <form id="ao-enroll-form" novalidate><label>Code <input id="ao-enroll-code" inputmode="numeric" autocomplete="one-time-code"></label>
      <button type="submit">Confirm</button></form></section>
  <section id="ao-recovery-codes" hidden>
    <h2>Save your recovery codes</h2><p>Each code works once. They are shown only now.</p>
    <ol id="ao-recovery-list"></ol><button type="button" id="ao-copy-codes">Copy all</button>
    <label><input type="checkbox" id="ao-codes-ack"> I have stored these codes</label>
    <button type="button" id="ao-continue" disabled>Continue</button></section>
  <noscript>Signing in requires JavaScript.</noscript>
</main></body></html>
"""


def render_login_page() -> str:
    """The static sign-in page (HLD 17.6), including the enrollment-token form."""
    return _LOGIN_PAGE


def hub_head_tags() -> str:
    """The stylesheet and deferred script tags the signed-in index adds before ``</head>``."""
    return _HEAD_TAGS


def render_signed_in_bar(username: str, auth_method: str) -> str:
    """The "Signed in as" bar with the logout button; both values are HTML-escaped."""
    return (
        '<div id="ao-account-bar">Signed in as '
        f"<strong>{escape(username)}</strong> "
        f"<small>({escape(auth_method)})</small> "
        '<button type="button" id="ao-logout">Sign out</button></div>\n'
    )


def read_hub_asset(name: str) -> bytes:
    """The bytes of a shipped hub asset. ``KeyError`` unless ``name`` is in ``HUB_ASSET_TYPES``.

    The allowlist check comes first, so a traversal-shaped name never reaches the filesystem.
    """
    if name not in HUB_ASSET_TYPES:
        raise KeyError(name)
    return resources.files(HUB_ASSETS_PACKAGE).joinpath(HUB_ASSETS_DIR, name).read_bytes()
