"""Auth provider seam (HLD section 11.15.1; L3).

Seeded by T-kwwJ82 with the frozen :class:`VerifiedIdentity` that ``SessionManager.issue`` needs;
``Revalidation`` was added by T-G7qByZ (the middleware branches on it). T-XchniS adds
``ClientInfo``, ``UserView`` and the :class:`AuthProvider` ABC -- the only seam type: no
protocols, no registry. FastAPI-free.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar

from .constants import LOCAL_PROVIDER_ID
from .model import SessionState


class Revalidation(StrEnum):
    """Tri-state outcome of ``AuthProvider.revalidate`` (HLD 11.15.1).

    Added by T-G7qByZ because ``AuthMiddleware`` step 6 branches on it; T-XchniS extends this
    module with the rest of the seam and must not redefine it.
    """

    VALID = "valid"
    REVOKED = "revoked"
    UNAVAILABLE = "unavailable"  # the store is unreadable: answer 503 and KEEP the session


@dataclass(frozen=True)
class VerifiedIdentity:
    user_id: str
    username: str
    roles: tuple[str, ...]  # a tuple on purpose: only ``principal_for`` makes a list
    credential_epoch: int  # FROM THE SAME SNAPSHOT that supplied the verified hash (D10)
    store_id: str
    next_state: SessionState  # FULL | PARTIAL_SECOND_FACTOR | PARTIAL_ENROLL
    provider: str = LOCAL_PROVIDER_ID


@dataclass(frozen=True)
class ClientInfo:
    """What the guard and the transport checks need to know about the caller (HLD 11.15.1)."""

    key: str  # canonical_client_key(peer) (D9)
    # v2.1 (security M2): loopback peer AND loopback Host AND no Forwarded / X-Forwarded-* header.
    # Computed by ``routes.client_info``; this module only defines the shape.
    is_loopback: bool
    secure: bool  # scope["scheme"] == "https"
    # v2.1: no trusted proxies configured AND a loopback peer sent a forwarding header or a
    # non-loopback Host (D17). Reported in E1 transport; triggers one WARNING per process.
    proxy_suspected: bool = False


@dataclass(frozen=True)
class UserView:
    """The non-secret projection of a user record: no hash, seed or token state."""

    user_id: str
    username: str
    roles: tuple[str, ...]
    totp_enrolled: bool
    recovery_codes_remaining: int | None
    totp_required: bool


class AuthProvider(ABC):
    """The one seam type of the auth layer (FastAPI-free)."""

    provider_id: ClassVar[str]

    @abstractmethod
    def check_ready(self) -> None:
        """Raise ``AuthNotReadyError`` (actionable) / ``UnsafePermissionsError`` when not ready."""

    @abstractmethod
    def revalidate(self, user_id: str, credential_epoch: int) -> Revalidation:
        """Tri-state: is the session's identity still the stored one? Synchronous and cheap."""

    @abstractmethod
    def user_view(self, username: str) -> UserView | None:
        """The non-secret view of ``username``, or ``None`` when there is no such user."""

    def startup_warnings(self) -> list[str]:
        return []
