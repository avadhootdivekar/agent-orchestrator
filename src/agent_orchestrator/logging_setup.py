"""Structured JSON logging helpers for the agent orchestrator.

Provides a JSONFormatter (one JSON object per line) and per-run handler
lifecycle management so each Orchestrator.run() writes its own run.log
without leaking handlers across runs.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import MutableMapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_PACKAGE_LOGGER = "agent_orchestrator"

# Registry mapping run_id -> (file_handler, stream_handler) so attach is idempotent.
_run_handlers: dict[str, tuple[logging.Handler, logging.Handler]] = {}


class JSONFormatter(logging.Formatter):
    """Render a LogRecord as one JSON object per line.

    Fields always present: ts (ISO-8601 UTC), level, logger, msg.
    Fields present when set via extra=: run_id, task_id, event, attempt,
    status, exit_code, plus any other keys in record.__dict__ that arrive
    through extra={...} and are not standard LogRecord attributes.
    Exceptions are serialised into an ``exc`` field.
    """

    # Standard LogRecord attributes to exclude from the catch-all extra scan.
    _SKIP = frozenset(
        {
            "name",
            "msg",
            "args",
            "levelname",
            "levelno",
            "pathname",
            "filename",
            "module",
            "exc_info",
            "exc_text",
            "stack_info",
            "lineno",
            "funcName",
            "created",
            "msecs",
            "relativeCreated",
            "thread",
            "threadName",
            "processName",
            "process",
            "message",
            "taskName",
        }
    )

    # Explicit optional fields we promote to top-level if present.
    _PROMOTE = ("run_id", "task_id", "event", "attempt", "status", "exit_code")

    def format(self, record: logging.LogRecord) -> str:
        base: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }

        for key in self._PROMOTE:
            val = getattr(record, key, None)
            if val is not None:
                base[key] = val

        # Catch-all: any extra keys injected by callers via extra={...}
        for key, val in record.__dict__.items():
            if key not in self._SKIP and key not in base:
                base[key] = val

        if record.exc_info:
            base["exc"] = self.formatException(record.exc_info)

        return json.dumps(base, default=str)


def attach_run_handler(run_id: str, log_path: str) -> logging.Handler:
    """Attach a per-run FileHandler + console StreamHandler to the package logger.

    Idempotent: calling a second time with the same run_id is a no-op and
    returns the existing file handler.

    Parameters
    ----------
    run_id:
        Unique identifier for the run (used as handler name).
    log_path:
        Absolute or workspace-relative path for the JSON-lines log file.

    Returns
    -------
    logging.Handler
        The FileHandler (needed for detach_run_handler or direct close).
    """
    if run_id in _run_handlers:
        return _run_handlers[run_id][0]

    Path(os.path.dirname(os.path.abspath(log_path))).mkdir(parents=True, exist_ok=True)

    formatter = JSONFormatter()

    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setFormatter(formatter)
    fh.name = f"ao-run-file-{run_id}"

    sh = logging.StreamHandler()
    sh.setFormatter(formatter)
    sh.name = f"ao-run-console-{run_id}"

    pkg_logger = logging.getLogger(_PACKAGE_LOGGER)
    pkg_logger.addHandler(fh)
    pkg_logger.addHandler(sh)
    if pkg_logger.level == logging.NOTSET:
        pkg_logger.setLevel(logging.INFO)

    _run_handlers[run_id] = (fh, sh)
    return fh


def detach_run_handler(run_id: str) -> None:
    """Remove and close both handlers attached for *run_id*.

    Safe to call even if *run_id* was never attached (no-op, no error).
    """
    pair = _run_handlers.pop(run_id, None)
    if pair is None:
        return

    pkg_logger = logging.getLogger(_PACKAGE_LOGGER)
    fh, sh = pair
    pkg_logger.removeHandler(fh)
    fh.close()
    pkg_logger.removeHandler(sh)
    sh.close()


class _MergingAdapter(logging.LoggerAdapter):
    """LoggerAdapter whose process() merges call-site extra with adapter extra.

    The stdlib LoggerAdapter.process() replaces extra rather than merging it,
    which means call-site ``extra={"event": "..."}`` would be lost when the
    adapter already has ``{"run_id": "..."}`` baked in.  This subclass fixes that.
    """

    def process(
        self, msg: str, kwargs: MutableMapping[str, Any]
    ) -> tuple[str, MutableMapping[str, Any]]:
        # self.extra may be Mapping[str, object] | None per the superclass typing.
        adapter_extra: dict[str, Any] = dict(self.extra) if self.extra else {}
        call_extra: dict[str, Any] = dict(kwargs.get("extra") or {})
        kwargs["extra"] = {**adapter_extra, **call_extra}
        return msg, kwargs


def get_run_logger(
    run_id: str,
    task_id: str | None = None,
) -> logging.LoggerAdapter:
    """Return a LoggerAdapter that injects run_id (and optionally task_id) into every record.

    Call-site ``extra={"event": "..."}`` is merged with the adapter's built-in
    fields — neither side overwrites the other.

    Parameters
    ----------
    run_id:
        The current run identifier.
    task_id:
        Optional task identifier; when provided it is injected into every record.
    """
    base: dict[str, str] = {"run_id": run_id}
    if task_id is not None:
        base["task_id"] = task_id

    return _MergingAdapter(
        logging.getLogger(_PACKAGE_LOGGER),
        extra=base,
    )
