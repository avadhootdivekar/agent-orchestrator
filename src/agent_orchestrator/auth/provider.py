"""Auth provider seam (HLD section 11.15.1; L3).

Seeded by T-kwwJ82 with ONLY the frozen :class:`VerifiedIdentity` that ``SessionManager.issue``
needs. T-XchniS later adds ``ClientInfo``, ``UserView``, ``Revalidation`` and the
``AuthProvider`` ABC to this module and must not redefine this dataclass.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

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
