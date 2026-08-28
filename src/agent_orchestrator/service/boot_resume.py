"""Boot-resume: scan for dashboard-launched runs orphaned by a dead engine process, decide
(with cross-boot bookkeeping) whether an automatic ``ao resume`` is safe, and hand approved
candidates back to the supervisor to act on (HLD §6.4, ADR-0012 D4 + early-gate correction #1).

This module owns **Scan** + **Decide**. **Act** -- launching
``ProcessSupervisor.launch_resume`` and the immediately-before-spawn idempotency re-check
(ADR-0012 early-gate correction #1 / this ticket's AC16) -- is owned by ``Supervisor.start()``
in ``supervisor.py``, since it needs the supervisor's own ``boot_id`` and must run strictly
once per boot, never on a later monitor-loop tick (HLD §6.1 step 4).
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel

from ..ui.processes import ProcessSupervisor
from ..ui.runs import RunNotFoundError, RunRepository

logger = logging.getLogger(__name__)

# Bookkeeping knobs (AC2) -- named constants, not magic literals; both overridable via
# `BootResumeGuard`'s constructor so tests can assert the exact boundary (quarantine after
# the Nth attempt, cooldown expiry) without waiting on/faking out real production values.
MAX_AUTO_RESUME_ATTEMPTS = 3
COOLDOWN_SECONDS = 60.0

BOOT_RESUME_FILENAME = "boot_resume.json"


def _normalize_root(root: str) -> str:
    """Mirror ``service/registry.py``'s own root normalization (``str(Path(x).resolve())``).

    That module does not export a helper for it (normalization is inlined in ``add``/
    ``save``), so this is a deliberate one-line mirror of the exact same expression rather
    than inventing a second, possibly-diverging normalization scheme (AC3).
    """
    return str(Path(root).resolve())


@dataclass(frozen=True)
class ResumeCandidate:
    """One dashboard-launched run whose owning PID is dead but whose persisted ``RunState``
    never reached a terminal status -- i.e. the engine was killed mid-run, not finished."""

    workspace_root: str
    run_id: str


def scan_resumable_runs(
    workspace_root: str,
    *,
    supervisor_factory: Callable[[str], ProcessSupervisor] = ProcessSupervisor,
    repo_factory: Callable[[str], RunRepository] = RunRepository,
) -> list[ResumeCandidate]:
    """Scan (HLD §6.4) -- reuses ``ProcessSupervisor.reconcile()`` + ``RunRepository.
    load_state`` rather than reimplementing PID liveness.

    Only considers runs launched *through the dashboard* (they have a persisted
    ``LaunchRecord``, hence a known PID to have checked) -- a bare terminal ``ao run`` has
    no PID this service can observe and is out of scope: a documented limitation of reusing
    this mechanism, not a gap in it.

    Factories are injectable specifically so tests can substitute a ``ProcessSupervisor``
    whose ``reconcile()`` is driven by fixture ``LaunchRecord``s with fake PIDs, without
    spawning anything real (AC1).
    """
    root = _normalize_root(workspace_root)
    supervisor = supervisor_factory(root)
    repo = repo_factory(root)

    candidates: list[ResumeCandidate] = []
    for record in supervisor.reconcile():
        if record.run_id is None or record.finished_at is None:
            continue  # still alive, or never observed a run directory -- nothing to do here
        try:
            state = repo.load_state(record.run_id)
        except RunNotFoundError:
            logger.warning(
                "boot-resume scan: run %r launch-record exists but its run directory is "
                "missing/unreadable in workspace %r -- skipping (defensive, not expected)",
                record.run_id,
                root,
            )
            continue  # run directory pruned/missing since -- defensive, not expected
        if state.status == "running":
            candidates.append(ResumeCandidate(workspace_root=root, run_id=record.run_id))
    return candidates


class Decision(StrEnum):
    """``BootResumeGuard.decide`` outcomes, named for the HLD §6.4 rule that produced them."""

    APPROVE = "approve"
    SKIP_ALREADY_THIS_BOOT = "skip_already_this_boot"
    SKIP_COOLDOWN = "skip_cooldown"
    SKIP_QUARANTINED = "skip_quarantined"


class BootResumeBookkeepingEntry(BaseModel):
    """Persisted per ``f"{workspace_root}|{run_id}"`` bookkeeping key (AC2/AC3)."""

    attempts: int = 0
    last_boot_id: str | None = None
    last_attempted_at: str | None = None
    cooldown_until: str | None = None
    os_boot_id: str | None = None
    """Best-effort OS boot id (AC21) recorded alongside the attempt purely as rationale for
    ``ao service status`` to display -- advisory only, never consulted by ``.decide()``: a
    missing/unreadable boot id (e.g. non-Linux) must never block a real reboot's resume."""


class BootResumeState(BaseModel):
    entries: dict[str, BootResumeBookkeepingEntry] = {}


class BootResumeGuard:
    """Cross-boot bookkeeping + decision for automatic resume attempts (HLD §6.4).

    State is loaded from/persisted to ``<state_dir>/boot_resume.json``. ``record_attempt``
    persists immediately (atomic write-then-rename, mirroring ``registry.py``'s pattern) so
    a crash between two resume attempts cannot lose the count (AC2).

    ``clock`` must return timezone-aware ``datetime``s (default ``datetime.now(UTC)``) --
    ``cooldown_until`` is persisted via ``isoformat()`` and compared with ``<``, which raises
    on a naive/aware mismatch.
    """

    def __init__(
        self,
        state_dir: Path,
        *,
        clock: Callable[[], datetime] | None = None,
        max_attempts: int = MAX_AUTO_RESUME_ATTEMPTS,
        cooldown_seconds: float = COOLDOWN_SECONDS,
    ) -> None:
        self._state_dir = state_dir
        self._clock = clock or (lambda: datetime.now(UTC))
        self._max_attempts = max_attempts
        self._cooldown_seconds = cooldown_seconds
        self._state = self._load()

    @property
    def _path(self) -> Path:
        return self._state_dir / BOOT_RESUME_FILENAME

    @staticmethod
    def _key(candidate: ResumeCandidate) -> str:
        return f"{_normalize_root(candidate.workspace_root)}|{candidate.run_id}"

    def _load(self) -> BootResumeState:
        if not self._path.is_file():
            return BootResumeState()
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            return BootResumeState.model_validate(data)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            logger.warning(
                "boot-resume bookkeeping at %s is corrupt/unreadable (%s) -- starting clean; "
                "this resets attempts/cooldown counters, so a poisoned run could re-attempt "
                "sooner than intended",
                self._path,
                exc,
            )
            return BootResumeState()  # corrupt/unreadable -- start clean, never block boot

    def _save(self) -> None:
        self._state_dir.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(self._state.model_dump(mode="json"), indent=2), encoding="utf-8")
        os.replace(tmp, self._path)  # atomic, same write-then-rename as registry.py

    def decide(self, candidate: ResumeCandidate, boot_id: str) -> Decision:
        """HLD §6.4 rule order: per-boot dedup -> max-attempts quarantine -> cooldown ->
        approve."""
        entry = self._state.entries.get(self._key(candidate))
        if entry is None:
            return Decision.APPROVE
        if entry.last_boot_id == boot_id:
            return Decision.SKIP_ALREADY_THIS_BOOT
        if entry.attempts >= self._max_attempts:
            return Decision.SKIP_QUARANTINED
        if entry.cooldown_until is not None:
            cooldown_until = datetime.fromisoformat(entry.cooldown_until)
            if self._clock() < cooldown_until:
                return Decision.SKIP_COOLDOWN
        return Decision.APPROVE

    def record_attempt(
        self, candidate: ResumeCandidate, boot_id: str, *, os_boot_id: str | None = None
    ) -> None:
        """Record one resume attempt and persist immediately.

        Callers must only call this for an attempt actually made: the boot-resume Act step's
        immediately-before-spawn idempotency re-check (AC16) must skip a candidate that is no
        longer valid WITHOUT calling this, or an attempt that never happened would burn a real
        slot from the attempts/cooldown budget.
        """
        key = self._key(candidate)
        entry = self._state.entries.get(key) or BootResumeBookkeepingEntry()
        now = self._clock()
        entry.attempts += 1
        entry.last_boot_id = boot_id
        entry.last_attempted_at = now.isoformat()
        entry.cooldown_until = (now + timedelta(seconds=self._cooldown_seconds)).isoformat()
        entry.os_boot_id = os_boot_id
        self._state.entries[key] = entry
        self._save()


__all__ = [
    "BOOT_RESUME_FILENAME",
    "COOLDOWN_SECONDS",
    "MAX_AUTO_RESUME_ATTEMPTS",
    "BootResumeBookkeepingEntry",
    "BootResumeGuard",
    "BootResumeState",
    "Decision",
    "ResumeCandidate",
    "scan_resumable_runs",
]
