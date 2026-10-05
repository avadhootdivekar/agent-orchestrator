"""Auth provider seam (HLD section 11.15.1; L3).

Seeded by T-kwwJ82 with ONLY the frozen :class:`VerifiedIdentity` that ``SessionManager.issue``
needs. T-XchniS later adds ``ClientInfo``, ``UserView``, ``Revalidation`` and the
``AuthProvider`` ABC to this module and must not redefine this dataclass.
"""

from __future__ import annotations

from dataclasses import dataclass

from .constants import LOCAL_PROVIDER_ID
from .model import SessionState


@dataclass(frozen=True)
class VerifiedIdentity:
    user_id: str
    username: str
    roles: tuple[str, ...]  # a tuple on purpose: only ``principal_for`` makes a list
    credential_epoch: int  # FROM THE SAME SNAPSHOT that supplied the verified hash (D10)
    store_id: str
    next_state: SessionState  # FULL | PARTIAL_SECOND_FACTOR | PARTIAL_ENROLL
    provider: str = LOCAL_PROVIDER_ID
