"""Example handlers exercised by tests/test_registry.py and the held-out suite.

These are already correct and complete -- nothing here needs to change; they exist so
the registry/dispatch contract can be exercised with realistic handler functions
instead of bare lambdas.
"""

from __future__ import annotations

_GREETINGS = {"en": "Hello", "fr": "Bonjour", "es": "Hola"}


def make_greeter(language: str):
    """Return a handler that greets in `language` if it matches the payload's
    "language" key, else declines (returns None) so dispatch tries the next handler.
    """

    def handler(payload: dict) -> str | None:
        if payload.get("language") == language:
            return _GREETINGS.get(language)
        return None

    return handler


def catch_all(payload: dict) -> str:
    """Lowest-priority fallback: always resolves with a generic greeting."""
    return "Hi"
