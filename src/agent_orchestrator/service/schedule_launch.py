"""T-Lp4Wt6: the mechanism half of a scheduled fire (HLD §6.6 / §13.3, ADR-0014 D5/D6/D9).

:class:`ScheduledLauncher` implements :class:`~.schedule_engine.ScheduledLauncher`'s Protocol: it
turns one firing :class:`~.schedule_engine.FireDecision` into a detached ``ao run`` through the
existing :class:`~..ui.processes.ProcessSupervisor` path, with a two-phase intent record that
makes the fire at-most-once:

1. write ``intended`` (fsync-durable) **before anything else**;
2. resolve the workflow (root-guarded path, or a template instance), launch, and
3. update the record to ``launched`` (``launch_id``/``pid``/``run_id``) -- or ``launch_failed``.

A failed fire never raises (it must not kill the engine tick).

Security (``.ao/schedules.yaml`` is untrusted workspace content): this module adds **zero argv
surface**. Options go through ``RunArgs.as_options()`` (⊆ the ``ALLOWED_OPTIONS`` allow-list), the
run id through ``launch_run``'s explicit ``run_id`` keyword (never an ``options`` entry -- those are
silently dropped), the prompt through ``prompt=`` (written to a file by the supervisor, never
argv). ``reposets``/``agents`` are deliberately not passed (``ao run`` falls back to the
workspace's own config; the child's cwd is the workspace root).

Template instances: ``instance_id`` tokens ``{date}`` (UTC ``%Y%m%d``) and ``{rand6}`` give a fresh
instance per fire. An ``instance_id`` **without** a token re-uses the same instance on every fire
(``templates.instantiate`` is idempotent) -- right for "keep pushing this epic", surprising for
"nightly benchmark"; this is documented, not guessed. An omitted ``instance_id`` passes the
schedule id as the slug so the template's own ``id_pattern`` builds the instance id.

The launcher must be built with the engine's own ``state_store``, ``fire_store``, ``event_log`` and
its per-workspace ``process_supervisor`` (see ``schedule_engine`` docstring): the supervisor is the
only object that can reap its children, and the state store is a single-writer cache.
"""

from __future__ import annotations

import hashlib
import logging
import os
import secrets
import string
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from ..artifacts import ArtifactPathError, LocalFsArtifactStore
from ..project_config import ProjectConfig, find_project_config, load_project_config
from ..spec import load_workflow
from ..templates import TemplateInfo, discover_templates, instantiate
from ..ui.processes import ProcessSupervisor, _render_options
from .fire_store import (
    SCHEDULES_SUBDIR,
    FireAlreadyRecordedError,
    FireIntentRecord,
    FireIntentStore,
    ScheduleEventLog,
    ScheduleStateStore,
)
from .governance_gate import (
    GOVERNANCE_LOCK_NAME,
    REASON_GOVERNANCE_ERROR,
    FireRecordLedger,
    GateResult,
    PromptSnapshot,
    UsageLedger,
    check_and_reserve,
    skeleton,
)
from .schedule_engine import FireDecision

logger = logging.getLogger(__name__)

RUN_ID_PREFIX: Final = "ao"
RUN_ID_TS_FORMAT: Final = "%Y%m%dT%H%M%SZ"
RUN_ID_FIRE_KEY_CHARS: Final = 6  # fire-key prefix making the id unique per fire (w14 H1)
INSTANCE_DATE_FORMAT: Final = "%Y%m%d"
RAND6_LEN: Final = 6
RAND6_ALPHABET: Final = string.ascii_lowercase + string.digits  # matches templates' rand6 regex

EVENT_FIRED: Final = "schedule.fired"
EVENT_FIRE_FAILED: Final = "schedule.fire_failed"
EVENT_GOV_SKIPPED: Final = "schedule.governance.skipped"
EVENT_GOV_RESERVED: Final = "schedule.governance.reserved"
EVENT_GOV_UNSAFE: Final = "schedule.governance.launch_unsafe"
EVENT_GOV_ERROR: Final = "schedule.governance.error"
COST_CAP_OPTION: Final = "cost_cap_usd"
COST_CAP_FLAG: Final = "--cost-cap-usd"
REASON_COST_CAP_NOT_FORWARDED: Final = "cost_cap_not_forwarded"
SHA_PREFIX_LEN: Final = 12

ProcessSupervisorFactory = Callable[[str], ProcessSupervisor]
ProjectConfigLoader = Callable[[str], ProjectConfig | None]
TemplateLoader = Callable[[str, ProjectConfig | None], list[TemplateInfo]]
Clock = Callable[[], datetime]
Rand6 = Callable[[], str]
OptionsRenderer = Callable[[dict[str, object]], list[str]]


class CostCapNotForwardedError(Exception):
    """The reserved ``--cost-cap-usd`` would not reach the child's argv (fail closed, HLD §8.4)."""


class LaunchResolutionError(Exception):
    """The binding's workflow/template/prompt could not be safely resolved (a named reason)."""


# -- named helpers (asserted by T-Te3Qw8) -------------------------------------------------------


def scheduled_run_id(schedule_id: str, scheduled_for: datetime, fire_key: str) -> str:
    """``ao-<schedule_id>-<scheduled_for as %Y%m%dT%H%M%SZ>-<fire_key[:6]>``, with *scheduled_for*
    converted to UTC first (F11) so the literal ``Z`` is truthful. Naive datetimes are rejected.

    The timestamp only has one-second resolution while fire keys hash the full instant (plus
    event discriminators), so two distinct fires in one second would otherwise share an id and
    the second child would exit ``EXIT_RUN_EXISTS`` (4) while recorded ``launched`` (w14 H1)."""
    if scheduled_for.tzinfo is None or scheduled_for.utcoffset() is None:
        raise ValueError("scheduled_for must be timezone-aware")
    stamp = scheduled_for.astimezone(UTC).strftime(RUN_ID_TS_FORMAT)
    return f"{RUN_ID_PREFIX}-{schedule_id}-{stamp}-{fire_key[:RUN_ID_FIRE_KEY_CHARS]}"


def _default_rand6() -> str:
    return "".join(secrets.choice(RAND6_ALPHABET) for _ in range(RAND6_LEN))


def render_instance_id(
    instance_id: str | None, scheduled_for: datetime, *, rand6: Rand6 = _default_rand6
) -> str | None:
    """Render ``{date}`` (UTC ``%Y%m%d`` of *scheduled_for*) and ``{rand6}`` in *instance_id*.
    ``None`` stays ``None`` (the caller falls back to the template's own ``id_pattern``)."""
    if instance_id is None:
        return None
    date = scheduled_for.astimezone(UTC).strftime(INSTANCE_DATE_FORMAT)
    out = instance_id.replace("{date}", date)
    while "{rand6}" in out:  # each occurrence gets its own draw
        out = out.replace("{rand6}", rand6(), 1)
    return out


def _default_project_config_loader(root: str) -> ProjectConfig | None:
    path = find_project_config(Path(root))
    return load_project_config(path) if path is not None else None


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _argv_forwards_cap(argv: list[str], reservation: float) -> bool:
    """The cost-cap flag is present AND carries the reserved value (pre- and post-spawn, L4)."""
    try:
        return argv[argv.index(COST_CAP_FLAG) + 1] == str(reservation)
    except (ValueError, IndexError):
        return False


class ScheduledLauncher:
    """Implements the engine Protocol: ``launch(decision) -> FireIntentRecord``."""

    def __init__(
        self,
        state_dir: Path,
        *,
        process_supervisor_factory: ProcessSupervisorFactory,
        project_config_loader: ProjectConfigLoader = _default_project_config_loader,
        template_loader: TemplateLoader = discover_templates,
        clock: Clock,
        fire_store: FireIntentStore | None = None,
        state_store: ScheduleStateStore | None = None,
        event_log: ScheduleEventLog | None = None,
        rand6: Rand6 = _default_rand6,
        usage_ledger: UsageLedger | None = None,
        governance_lock_path: Path | None = None,
        options_renderer: OptionsRenderer = _render_options,
    ) -> None:
        self._supervisor_factory = process_supervisor_factory
        self._config_loader = project_config_loader
        self._template_loader = template_loader
        self._clock = clock
        self._rand6 = rand6
        # Production wiring passes the engine's own stores; the defaults exist for standalone use
        # (run-now --local) where no engine instance is alive in this process.
        self._fires = fire_store or FireIntentStore(state_dir, clock=clock)
        self._state = state_store or ScheduleStateStore(state_dir)
        self._events = event_log or ScheduleEventLog(state_dir, clock=clock)
        self._ledger: UsageLedger = usage_ledger or FireRecordLedger(self._fires)
        self._lock_path = governance_lock_path or (
            Path(state_dir) / SCHEDULES_SUBDIR / GOVERNANCE_LOCK_NAME
        )
        self._render = options_renderer

    # -- public ---------------------------------------------------------------------------------

    def launch(self, decision: FireDecision) -> FireIntentRecord:
        d = decision
        if d.fire_key is None or d.binding is None or d.scheduled_for is None:
            raise ValueError("a launchable FireDecision needs fire_key, binding and scheduled_for")
        # Phase 1: durable intent BEFORE anything else -- the at-most-once guarantee. A governed
        # decision gets it from check_and_reserve (the ONE intended write, reservation inside).
        snapshot: PromptSnapshot | None = None
        reservation: float | None = None
        try:
            if d.effective_limits is None:
                self._write_plain_intent(d)
            else:
                verdict = self._reserve(d)
                if not verdict.admit:
                    return self._fires.get(d.fire_key, d.workspace_root) or self._fail(
                        d, RuntimeError(verdict.reason or "governance skip")
                    )
                snapshot, reservation = verdict.prompt, verdict.reservation_usd
        except FireAlreadyRecordedError:
            existing = self._fires.get(d.fire_key, d.workspace_root)
            if existing is None:  # pragma: no cover - raced with a prune
                raise
            logger.warning(
                "fire %s already recorded (%s); not relaunching", d.fire_key, existing.status
            )
            return existing

        try:
            workflow_path, prompt = self._resolve(d, snapshot)
            run_id = scheduled_run_id(d.schedule_id, d.scheduled_for, d.fire_key)
            # Curated subset of the allow-listed options; run_id is the explicit keyword (F1).
            options: dict[str, object] = dict(d.binding.run_args.as_options())
            if reservation is not None:
                options[COST_CAP_OPTION] = reservation
                self._verify_cost_cap_rendered(options, reservation)  # pre-spawn
            supervisor = self._supervisor_factory(d.workspace_root)
            kwargs: dict[str, object] = {}
            workspace = self._workspace_arg(workflow_path, d.workspace_root)
            if workspace is not None:
                kwargs["workspace"] = workspace
            rec = supervisor.launch_run(
                workflow_path=workflow_path,
                prompt=prompt,
                options=options,
                run_id=run_id,
                **kwargs,  # type: ignore[arg-type]
            )
            if reservation is not None and not _argv_forwards_cap(rec.argv, reservation):  # belt
                self._cancel_unsafe(supervisor, rec.run_id or run_id)
                raise CostCapNotForwardedError(REASON_COST_CAP_NOT_FORWARDED)
        except CostCapNotForwardedError:
            return self._fail_unsafe(d)
        except Exception as exc:  # noqa: BLE001 - a failed fire must not kill the engine tick
            return self._fail(d, exc)

        try:
            launched = self._fires.update(
                d.fire_key,
                status="launched",
                launch_id=rec.launch_id,
                pid=rec.pid,
                run_id=run_id,
                argv_digest=_sha256(" ".join(rec.argv)),
            )
        except Exception:  # noqa: BLE001
            # The child is already running and the intent carries its deterministic run_id, so
            # leave the record ``intended``: reconcile_orphans resolves it to ``launched`` via the
            # run dir / pid (w14 M1). Never re-raise: the engine would record a failed fire.
            logger.exception("could not stamp launched on %s; reconcile will resolve", d.fire_key)
            stored = self._fires.get(d.fire_key, d.workspace_root)
            if stored is None:  # pragma: no cover - raced with a prune
                raise
            return stored
        # The child is running: bookkeeping trouble from here on must not flip the record.
        try:
            now = self._clock()
            self._state.record_fire(
                d.workspace_root,
                d.schedule_id,
                scheduled_for=d.scheduled_for,
                run_id=run_id,
                fire_at=now,
                fire_key=d.fire_key,
            )
            self._events.emit(
                EVENT_FIRED,
                workspace=d.workspace_root,
                schedule_id=d.schedule_id,
                fire_key=d.fire_key,
                scheduled_for=d.scheduled_for,
                run_id=run_id,
                lateness_seconds=(now - d.scheduled_for).total_seconds(),
            )
        except Exception:  # noqa: BLE001
            logger.exception("post-launch bookkeeping failed for %s", d.fire_key)
        return launched

    # -- governance -----------------------------------------------------------------------------

    def _write_plain_intent(self, d: FireDecision) -> None:
        assert d.fire_key is not None and d.scheduled_for is not None
        self._fires.write(
            FireIntentRecord(
                fire_key=d.fire_key,
                workspace_root=d.workspace_root,
                schedule_id=d.schedule_id,
                scheduled_for=d.scheduled_for,
                effective_due=d.effective_due,
                created_at=self._clock(),
                status="intended",
                run_id=scheduled_run_id(d.schedule_id, d.scheduled_for, d.fire_key),  # M1
            )
        )

    def _reserve(self, d: FireDecision) -> GateResult:
        """Authoritative gate + the single ``intended`` write (HLD §8.2). Fails closed: if the
        gate itself raises, the fire is suppressed ``governance_error`` and never launched."""
        assert d.binding is not None and d.fire_key and d.scheduled_for and d.effective_limits
        try:
            g = check_and_reserve(
                binding=d.binding,
                limits=d.effective_limits,
                workspace_root=d.workspace_root,
                schedule_id=d.schedule_id,
                fire_key=d.fire_key,
                scheduled_for=d.scheduled_for,
                effective_due=d.effective_due,
                ledger=self._ledger,
                fires=self._fires,
                lock_path=self._lock_path,
                now=self._clock(),
                run_id=scheduled_run_id(d.schedule_id, d.scheduled_for, d.fire_key),
            )
        except FireAlreadyRecordedError:
            raise
        except Exception as exc:  # noqa: BLE001 - fail closed
            logger.exception("governance check failed for %s", d.fire_key)
            self._events.emit(
                EVENT_GOV_ERROR,
                workspace=d.workspace_root,
                schedule_id=d.schedule_id,
                fire_key=d.fire_key,
                reason=REASON_GOVERNANCE_ERROR,
                detail=str(exc),
            )
            try:
                self._fires.write_suppressed(
                    d.fire_key,
                    REASON_GOVERNANCE_ERROR,
                    workspace_root=d.workspace_root,
                    schedule_id=d.schedule_id,
                    scheduled_for=d.scheduled_for,
                    effective_due=d.effective_due,
                    governance=skeleton(
                        d.effective_limits,
                        d.binding.timezone or "UTC",
                        d.binding.week_start or "monday",
                        {"error": str(exc)},
                    ),
                )
            except FireAlreadyRecordedError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception("could not record governance_error for %s", d.fire_key)
            return GateResult(False, REASON_GOVERNANCE_ERROR, None, {"error": str(exc)})
        if g.admit:
            self._events.emit(
                EVENT_GOV_RESERVED,
                workspace=d.workspace_root,
                schedule_id=d.schedule_id,
                fire_key=d.fire_key,
                reserved_usd=g.reservation_usd,
                run_cost_cap_usd=g.reservation_usd,
                prompt_sha256=(g.prompt.sha256 or "")[:SHA_PREFIX_LEN] if g.prompt else None,
            )
        else:
            self._events.emit(
                EVENT_GOV_SKIPPED,
                workspace=d.workspace_root,
                schedule_id=d.schedule_id,
                fire_key=d.fire_key,
                scheduled_for=d.scheduled_for,
                reason=g.reason,
                **{k: v for k, v in g.detail.items() if k != "reason"},
            )
        return g

    def _verify_cost_cap_rendered(self, options: dict[str, object], reservation: float) -> None:
        """Pre-spawn: render with the same table the supervisor uses; refuse if the flag would be
        silently dropped (``_render_options`` drops unregistered keys)."""
        if not _argv_forwards_cap(self._render(options), reservation):
            raise CostCapNotForwardedError(REASON_COST_CAP_NOT_FORWARDED)

    @staticmethod
    def _cancel_unsafe(supervisor: ProcessSupervisor, run_id: str) -> None:
        try:
            supervisor.cancel(run_id)
        except Exception:  # noqa: BLE001 - best effort; the record still goes launch_failed
            logger.exception("could not cancel unsafe launch %s", run_id)

    def _fail_unsafe(self, d: FireDecision) -> FireIntentRecord:
        assert d.fire_key is not None
        self._events.emit(
            EVENT_GOV_UNSAFE,
            workspace=d.workspace_root,
            schedule_id=d.schedule_id,
            fire_key=d.fire_key,
            reason=REASON_COST_CAP_NOT_FORWARDED,
        )
        return self._record_failure(d, REASON_COST_CAP_NOT_FORWARDED)

    # -- resolution -----------------------------------------------------------------------------

    def _resolve(
        self, d: FireDecision, snapshot: PromptSnapshot | None = None
    ) -> tuple[str, str | None]:
        """``(workflow_path, prompt_text)``; raises :class:`LaunchResolutionError`. A governed fire
        passes its *snapshot*: the bytes read (and pinned) under the lock are exactly what runs."""
        binding = d.binding
        assert binding is not None and d.scheduled_for is not None
        store = LocalFsArtifactStore(d.workspace_root)
        if snapshot is not None:
            prompt = snapshot.text
        else:
            prompt = self._read_prompt(store, binding.prompt_file, binding.prompt_sha256)

        if binding.template is not None:
            tmpl = self._find_template(binding.template, d.workspace_root)
            slug = render_instance_id(binding.instance_id, d.scheduled_for, rand6=self._rand6)
            result = instantiate(
                tmpl,
                store.root,
                slug_or_id=slug if slug is not None else d.schedule_id,
                params=dict(binding.params),
                prompt_text=prompt,
            )
            return result.workflow_path, prompt

        assert binding.workflow is not None
        workflow_path = self._guarded_path(store, binding.workflow, "workflow")
        if not os.path.isfile(workflow_path):
            raise LaunchResolutionError(f"workflow not found: {binding.workflow}")
        return workflow_path, prompt

    @staticmethod
    def _guarded_path(store: LocalFsArtifactStore, rel: str, what: str) -> str:
        # The load-time shape check lives in T-Sd1Kq7; this is the second wall (root guard).
        if os.path.isabs(rel) or rel.startswith("~"):
            raise LaunchResolutionError(f"{what} path must be workspace-relative: {rel!r}")
        try:
            return store.resolve(rel)
        except ArtifactPathError as exc:
            raise LaunchResolutionError(f"{what} path escapes the workspace root: {rel!r}") from exc

    def _read_prompt(
        self, store: LocalFsArtifactStore, prompt_file: str | None, pinned_sha: str | None
    ) -> str | None:
        if prompt_file is None:
            return None
        path = self._guarded_path(store, prompt_file, "prompt_file")
        try:
            text = Path(path).read_text(encoding="utf-8")
        except OSError as exc:
            raise LaunchResolutionError(f"prompt_file unreadable: {prompt_file} ({exc})") from exc
        if pinned_sha is not None and _sha256(text) != pinned_sha:
            raise LaunchResolutionError(f"prompt_file {prompt_file} does not match prompt_sha256")
        return text

    def _find_template(self, name: str, root: str) -> TemplateInfo:
        """F4: by NAME through discovery only -- never ``load_template``'s directory-path branch."""
        if not name or os.sep in name or "/" in name or name.startswith(("~", ".")):
            raise LaunchResolutionError(f"template must be a name, not a path: {name!r}")
        for info in self._template_loader(root, self._config_loader(root)):
            if info.name == name:
                return info
        raise LaunchResolutionError(f"template not found by name: {name!r}")

    @staticmethod
    def _workspace_arg(workflow_path: str, root: str) -> str | None:
        """ADR-0022: forward ``--workspace`` only for a no-repo-set workflow (matches the
        dashboard's ``_no_repo_workspace``); a workflow that cannot be loaded gets none."""
        try:
            return root if load_workflow(workflow_path).repo_set is None else None
        except Exception:  # noqa: BLE001 - ao run itself reports an invalid workflow
            return None

    # -- failure --------------------------------------------------------------------------------

    def _fail(self, d: FireDecision, exc: Exception) -> FireIntentRecord:
        reason = f"{type(exc).__name__}: {exc}"
        return self._record_failure(d, reason)

    def _record_failure(self, d: FireDecision, reason: str) -> FireIntentRecord:
        assert d.fire_key is not None
        logger.warning("scheduled launch %s/%s failed: %s", d.workspace_root, d.schedule_id, reason)
        record: FireIntentRecord | None = None
        for step in (
            lambda: self._fires.update(d.fire_key or "", status="launch_failed", reason=reason),
            lambda: self._state.record_failure(d.workspace_root, d.schedule_id, reason),
            lambda: self._events.emit(
                EVENT_FIRE_FAILED,
                workspace=d.workspace_root,
                schedule_id=d.schedule_id,
                fire_key=d.fire_key,
                scheduled_for=d.scheduled_for,
                detail=reason,
            ),
        ):
            try:
                result = step()
                if isinstance(result, FireIntentRecord):
                    record = result
            except Exception:  # noqa: BLE001 - never escape; keep going so every step is tried
                logger.exception("recording launch failure for %s", d.fire_key)
        return (
            record
            or self._fires.get(d.fire_key, d.workspace_root)
            or FireIntentRecord(
                fire_key=d.fire_key,
                workspace_root=d.workspace_root,
                schedule_id=d.schedule_id,
                scheduled_for=d.scheduled_for or self._clock(),
                effective_due=d.effective_due,
                created_at=self._clock(),
                status="launch_failed",
                reason=reason,
            )
        )
