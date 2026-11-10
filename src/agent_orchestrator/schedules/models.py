"""Pydantic model of ``<workspace>/.ao/schedules.yaml`` (HLD ``scheduler-triggers-hld.md`` §5.1).

The file is *untrusted input* (it can be hand-edited, written by ``ao schedule`` or, later, a
dashboard), so everything is closed (``extra="forbid"``), path-bearing fields are
workspace-relative only, ``template`` is a bare name, and ``run_args`` is a curated subset of the
run-options registry (§5.1.1) -- there is deliberately no free-form ``args``/``command`` field.

Errors: every validator raises :class:`ScheduleFileError`; :meth:`ScheduleFile.from_data`
re-raises pydantic's aggregate as one ``ScheduleFileError`` that names the source file, the
schedule id and the offending field.
"""

from __future__ import annotations

import re
import string
from typing import TYPE_CHECKING, Annotated, Any, Final, Literal, TypeVar
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    create_model,
    field_validator,
    model_validator,
)

from ..models import Trigger
from ..run_options import Option, allowed_bool_options, allowed_options, get_option
from .errors import DuplicateScheduleIdError, ScheduleFileError

SCHEDULE_FILE_VERSION: Final = "1.0"
MIN_INTERVAL_SECONDS: Final = 60
MAX_JITTER_SECONDS: Final = 3600
# The minted run id is ``ao-<id>-<16 chars>`` and must fit the 128-char run-id limit (F12).
MAX_SCHEDULE_ID_LEN: Final = 64
SCHEDULE_ID_PATTERN: Final = r"^[a-z0-9][a-z0-9-_]*$"
# A bare template *name*: ``templates.load_template`` accepts a directory path before name
# lookup, so anything with ``/``, ``.`` or ``~`` could load an arbitrary directory (F4).
TEMPLATE_NAME_PATTERN: Final = r"^[A-Za-z0-9][A-Za-z0-9_-]*$"
ENV_NAME_PATTERN: Final = r"^[A-Za-z_][A-Za-z0-9_]*$"
INSTANCE_ID_TOKENS: Final = frozenset({"date", "rand6"})

# The MVP subset of ``run_options`` a schedule may set (owner decision F3, 2026-11-10). Excludes
# ``sentinel.llm.*`` (``powers=act`` lets the LLM lane kill/inject tasks; ``budget_usd`` raises
# spend) and ``sentinel.max_parallel_ceiling``; ``cost_cap_usd`` / ``run_id`` are not run args.
SCHEDULE_RUN_ARG_NAMES: Final[tuple[str, ...]] = (
    "model",
    "turn_budget",
    "effort",
    "thinking_effort",
    "autocompact",
    "max_attempts",
    "max_turns",
    "max_parallel",
    "budget_total",
    "quota_max_wait",
    "quota_poll_interval",
    "self_heal",
    "sentinel.enabled",
)

# Binding fields that fall back to ``ScheduleFile.defaults`` when left ``None``.
INHERITABLE_FIELDS: Final[tuple[str, ...]] = (
    "timezone",
    "overlap",
    "catch_up",
    "catch_up_window_seconds",
    "jitter_seconds",
    "grace_seconds",
    "max_concurrent",
)

Kind = Literal["cron", "interval", "file_watch", "webhook"]
Overlap = Literal["skip", "queue", "allow"]
CatchUp = Literal["once", "skip"]


def _binding_error(schedule_id: str, field: str, message: str) -> ScheduleFileError:
    return ScheduleFileError(
        f"schedule {schedule_id!r}: field {field!r}: {message}",
        schedule_id=schedule_id,
        field=field,
    )


def _require_workspace_relative(value: str) -> str:
    """Reject absolute paths, ``~`` and any ``..`` segment (resolution/containment itself is
    ``LocalFsArtifactStore``'s job -- this is only the load-time shape check)."""
    segments = value.replace("\\", "/").split("/")
    if not value or value.startswith(("/", "~")) or ".." in segments or value[1:2] == ":":
        raise ScheduleFileError(
            f"path {value!r} must be workspace-relative (no absolute path, '~' or '..' segment)"
        )
    return value


def _check_timezone(name: str) -> str:
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError) as exc:
        raise ScheduleFileError(f"unknown IANA timezone {name!r}") from exc
    return name


# -- run_args -----------------------------------------------------------------------------------


class _RunArgsBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    def as_options(self) -> dict[str, object]:
        """The explicitly-set args as ``launch_run(options=...)`` expects: registry option name
        (dotted for ``sentinel.enabled``) -> value. Unset (``None``) args are omitted so the
        workspace's own config/env chain still applies to them."""
        return dict(self.model_dump(by_alias=True, exclude_none=True))


def _run_arg_annotation(opt: Option) -> Any:
    if opt.type == "int":
        return int
    if opt.type == "float":
        return float
    if opt.type == "bool":
        return bool
    if opt.type == "enum":
        literal: Any = Literal
        return literal[opt.choices]
    return str


def _build_run_args_fields() -> dict[str, Any]:
    registry = set(allowed_options()) | set(allowed_bool_options())
    missing = [n for n in SCHEDULE_RUN_ARG_NAMES if n not in registry]
    if missing:  # import-time drift guard: the registry is the source of truth
        raise RuntimeError(
            f"SCHEDULE_RUN_ARG_NAMES names options absent from run_options: {missing}"
        )
    fields: dict[str, Any] = {}
    for name in SCHEDULE_RUN_ARG_NAMES:
        # Dotted keys are not valid identifiers -> field name with '_', alias = registry name.
        fields[name.replace(".", "_")] = (
            _run_arg_annotation(get_option(name)) | None,
            Field(default=None, alias=name),
        )
    return fields


if TYPE_CHECKING:

    class RunArgs(_RunArgsBase):
        """Closed set of run options a schedule may set (types/choices from the registry)."""

else:
    RunArgs = create_model(
        "RunArgs",
        __base__=_RunArgsBase,
        __doc__="Closed set of run options a schedule may set (types/choices from the registry).",
        **_build_run_args_fields(),
    )


# -- nested specs -------------------------------------------------------------------------------


class UntilSpec(BaseModel):
    """Inter-run stop condition (§6.8). Any satisfied condition ends the schedule."""

    model_config = ConfigDict(extra="forbid")

    max_runs: int | None = Field(default=None, ge=1)
    artifact_exists: str | None = None
    gate_file: str | None = None
    gate_field: str = "done"
    gate_equals: bool | int | str = True

    @field_validator("artifact_exists", "gate_file")
    @classmethod
    def _relative(cls, v: str | None) -> str | None:
        return None if v is None else _require_workspace_relative(v)


def _default_watch_events() -> list[Literal["created", "modified"]]:
    return ["created", "modified"]


class WatchSpec(BaseModel):
    """File-watch source (§6.6): workspace-relative glob paths, polled."""

    model_config = ConfigDict(extra="forbid")

    paths: list[str] = Field(min_length=1)
    on: list[Literal["created", "modified"]] = Field(
        default_factory=_default_watch_events, min_length=1
    )
    debounce_seconds: int = Field(default=30, ge=0)
    poll_seconds: int = Field(default=30, ge=1)

    @field_validator("paths")
    @classmethod
    def _relative(cls, v: list[str]) -> list[str]:
        return [_require_workspace_relative(p) for p in v]


class WebhookSpec(BaseModel):
    """Authenticated webhook source (§7.3): the secret is referenced, never inlined."""

    model_config = ConfigDict(extra="forbid")

    secret_env: str | None = Field(default=None, pattern=ENV_NAME_PATTERN)
    secret_file: str | None = None
    rate_per_minute: int = Field(default=10, ge=1)

    @model_validator(mode="before")
    @classmethod
    def _no_inline_secret(cls, data: Any) -> Any:
        if isinstance(data, dict) and "secret" in data:
            raise ScheduleFileError(
                "inline 'secret' is not allowed; use 'secret_env' (an env var name) or "
                "'secret_file' (a workspace-relative path)",
                field="webhook.secret",
            )
        return data

    @field_validator("secret_file")
    @classmethod
    def _relative(cls, v: str | None) -> str | None:
        return None if v is None else _require_workspace_relative(v)

    @model_validator(mode="after")
    def _exactly_one_secret(self) -> WebhookSpec:
        if (self.secret_env is None) == (self.secret_file is None):
            raise ScheduleFileError("set exactly one of 'secret_env' or 'secret_file'")
        return self


class ScheduleDefaults(BaseModel):
    """Values every binding inherits unless it overrides them (§5.1)."""

    model_config = ConfigDict(extra="forbid")

    timezone: str = "UTC"
    overlap: Overlap = "skip"
    catch_up: CatchUp = "once"
    catch_up_window_seconds: int = Field(default=86400, ge=0)
    jitter_seconds: int = Field(default=0, ge=0, le=MAX_JITTER_SECONDS)
    grace_seconds: int = Field(default=60, ge=0)
    max_concurrent: int = Field(default=1, ge=1)

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v: str) -> str:
        return _check_timezone(v)


# -- binding ------------------------------------------------------------------------------------


class ScheduleBinding(BaseModel):
    """One schedule. Inheritable fields are ``None`` here; use ``ScheduleFile.bindings`` to
    get them resolved against the file's ``defaults``."""

    model_config = ConfigDict(extra="forbid")

    id: Annotated[
        str, StringConstraints(pattern=SCHEDULE_ID_PATTERN, max_length=MAX_SCHEDULE_ID_LEN)
    ]
    enabled: bool = True
    kind: Kind
    schedule: str | None = None
    interval_seconds: int | None = None
    timezone: str | None = None
    workflow: str | None = None
    template: Annotated[str, StringConstraints(pattern=TEMPLATE_NAME_PATTERN)] | None = None
    params: dict[str, str] = Field(default_factory=dict)
    instance_id: str | None = None
    prompt_file: str | None = None
    run_args: RunArgs = Field(default_factory=lambda: RunArgs())
    overlap: Overlap | None = None
    catch_up: CatchUp | None = None
    catch_up_window_seconds: int | None = Field(default=None, ge=0)
    jitter_seconds: int | None = Field(default=None, ge=0, le=MAX_JITTER_SECONDS)
    grace_seconds: int | None = Field(default=None, ge=0)
    max_concurrent: int | None = Field(default=None, ge=1)
    until: UntilSpec | None = None
    watch: WatchSpec | None = None
    webhook: WebhookSpec | None = None

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v: str | None) -> str | None:
        return None if v is None else _check_timezone(v)

    @field_validator("workflow", "prompt_file")
    @classmethod
    def _relative(cls, v: str | None) -> str | None:
        return None if v is None else _require_workspace_relative(v)

    @model_validator(mode="after")
    def _cross_field(self) -> ScheduleBinding:
        sid = self.id

        def need(cond: bool, field: str, message: str) -> None:
            if not cond:
                raise _binding_error(sid, field, message)

        # Kind-specific requirements; fields of another kind are rejected rather than silently
        # ignored, so a typo'd ``kind`` cannot leave a schedule that never fires.
        specific: dict[str, tuple[Kind, bool]] = {
            "schedule": ("cron", self.schedule is not None),
            "interval_seconds": ("interval", self.interval_seconds is not None),
            "watch": ("file_watch", self.watch is not None),
            "webhook": ("webhook", self.webhook is not None),
        }
        for field, (kind, present) in specific.items():
            need(not present or self.kind == kind, field, f"only valid with kind={kind}")
            need(present or self.kind != kind, field, f"required when kind={kind}")
        if self.interval_seconds is not None:
            need(
                self.interval_seconds >= MIN_INTERVAL_SECONDS,
                "interval_seconds",
                f"must be >= {MIN_INTERVAL_SECONDS} (got {self.interval_seconds})",
            )
        if self.schedule is not None:
            need(
                bool(croniter.is_valid(self.schedule)),
                "schedule",
                f"invalid cron expression {self.schedule!r}",
            )
        need(
            (self.workflow is None) != (self.template is None),
            "workflow",
            "exactly one of 'workflow' / 'template' must be set",
        )
        if self.template is None:
            need(not self.params, "params", "only valid with 'template'")
            need(self.instance_id is None, "instance_id", "only valid with 'template'")
        if self.instance_id is not None:
            need(
                self._instance_id_ok(self.instance_id),
                "instance_id",
                f"{self.instance_id!r} must be an id pattern using only the "
                f"{sorted(INSTANCE_ID_TOKENS)} tokens",
            )
        return self

    @staticmethod
    def _instance_id_ok(value: str) -> bool:
        try:
            parts = list(string.Formatter().parse(value))
        except ValueError:
            return False
        literal = ""
        for text, name, spec, conv in parts:
            if name is not None and (name not in INSTANCE_ID_TOKENS or spec or conv):
                return False
            literal += text + ("x" if name else "")
        return re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", literal) is not None

    def to_trigger(self) -> Trigger:
        """Adapt to the engine's :class:`Trigger` -- ``cron`` and ``interval`` kinds ONLY.

        ``Trigger.type`` has no ``file_watch``/``webhook`` member by design: event kinds are
        served by the engine's ``TriggerSource`` protocol (``observe``/``commit``, HLD §7.1),
        not by ``Scheduler.next_fire``. Calling this for an event kind raises ``ValueError``.
        The timezone is the binding's own (unresolved) value, else ``UTC``; resolve defaults
        first (``ScheduleFile.bindings``) when inheritance matters.
        """
        if self.kind == "cron":
            return Trigger(type="cron", schedule=self.schedule, timezone=self.timezone or "UTC")
        if self.kind == "interval":
            return Trigger(type="interval", interval_seconds=self.interval_seconds)
        raise ValueError(
            f"schedule {self.id!r}: kind={self.kind} has no Trigger form (event kinds use "
            "TriggerSource, not Scheduler)"
        )


ResolvedBinding = ScheduleBinding
"""A binding whose :data:`INHERITABLE_FIELDS` were filled from the file's defaults (never
``None`` for those fields); same class, so consumers need not know which layer a value came from."""

_T = TypeVar("_T", bound="ScheduleFile")


def _first_schedule_index(loc: tuple[Any, ...]) -> int | None:
    if len(loc) >= 2 and loc[0] == "schedules" and isinstance(loc[1], int):
        return loc[1]
    return None


class ScheduleFile(BaseModel):
    """The whole ``schedules.yaml``."""

    model_config = ConfigDict(extra="forbid")

    version: str = SCHEDULE_FILE_VERSION
    defaults: ScheduleDefaults = Field(default_factory=ScheduleDefaults)
    schedules: list[ScheduleBinding] = Field(default_factory=list)

    @field_validator("version")
    @classmethod
    def _major(cls, v: str) -> str:
        if v.split(".")[0] != SCHEDULE_FILE_VERSION.split(".")[0]:
            raise ScheduleFileError(
                f"unsupported schedules file version {v!r} (this ao reads "
                f"{SCHEDULE_FILE_VERSION.split('.')[0]}.x)"
            )
        return v

    @model_validator(mode="after")
    def _unique_ids(self) -> ScheduleFile:
        seen: set[str] = set()
        for b in self.schedules:
            if b.id in seen:
                raise DuplicateScheduleIdError(
                    f"duplicate schedule id {b.id!r}", schedule_id=b.id, field="id"
                )
            seen.add(b.id)
        return self

    @property
    def bindings(self) -> list[ResolvedBinding]:
        """Every binding with ``None`` inheritable fields resolved against ``defaults`` --
        the single place the inheritance rule lives."""
        return [
            b.model_copy(
                update={
                    f: getattr(self.defaults, f)
                    for f in INHERITABLE_FIELDS
                    if getattr(b, f) is None
                }
            )
            for b in self.schedules
        ]

    def to_data(self) -> dict[str, Any]:
        """Plain-data form for YAML (aliases kept, ``None`` omitted -- ``None`` means inherit)."""
        return self.model_dump(mode="json", by_alias=True, exclude_none=True)

    @classmethod
    def from_data(cls: type[_T], data: Any, *, source: str = "<schedules>") -> _T:
        """Validate raw (YAML-parsed) *data*; raise :class:`ScheduleFileError` naming *source*,
        the schedule id (where determinable) and the field."""
        try:
            return cls.model_validate(data if data is not None else {})
        except ValidationError as exc:
            raise _to_file_error(exc, data, source) from exc


def _raw_schedule_id(data: Any, idx: int) -> str:
    rows = data.get("schedules") if isinstance(data, dict) else None
    if isinstance(rows, list) and idx < len(rows) and isinstance(rows[idx], dict):
        raw_id = rows[idx].get("id")
        if raw_id is not None:
            return str(raw_id)
    return f"#{idx}"


def _to_file_error(exc: ValidationError, data: Any, source: str) -> ScheduleFileError:
    """Fold pydantic's aggregate into one error naming source file, schedule id and field."""
    messages: list[str] = []
    cls: type[ScheduleFileError] = ScheduleFileError
    first_id: str | None = None
    first_field: str | None = None
    for err in exc.errors():
        loc = tuple(err["loc"])
        original = err.get("ctx", {}).get("error")
        idx = _first_schedule_index(loc)
        sid = _raw_schedule_id(data, idx) if idx is not None else None
        field_path = ".".join(map(str, loc[2:] if idx is not None else loc))
        if isinstance(original, DuplicateScheduleIdError):
            cls = DuplicateScheduleIdError
        if isinstance(original, ScheduleFileError) and original.schedule_id is not None:
            text = str(original)  # binding-level validator: already names id and field
            sid = original.schedule_id
            field_path = original.field or field_path
        else:
            body = str(original) if isinstance(original, ScheduleFileError) else err["msg"]
            text = (
                (f"schedule {sid!r}: " if sid else "")
                + (f"field {field_path!r}: " if field_path else "")
                + body
            )
        first_id = first_id or sid
        first_field = first_field or field_path or None
        messages.append(text)
    return cls(f"{source}: " + "; ".join(messages), schedule_id=first_id, field=first_field)
