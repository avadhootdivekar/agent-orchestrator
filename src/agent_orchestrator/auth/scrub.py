"""Log redaction, defence in depth (HLD section 11.13, L2; FR-20, invariant S11).

The primary control is never logging secrets; this module catches what slips through: cookie
values, session-proof header values, ``otpauth://`` URIs, password hashes, TOTP secrets,
recovery codes / enrollment tokens and ``password=...`` style key-value forms.

Known false positives (accepted; redaction must err toward hiding): a 32-character upper-case
run of ``A-Z2-7`` (a Base32-looking identifier), a ``XXXX-XXXX-XXXX-XXXX`` shaped identifier, and
any ``code=``, ``token=`` or ``secret=`` key-value pair (``status code=200`` becomes
``status code=[REDACTED]``). A key-value value extends to the next whitespace. Exception
tracebacks attached to a record (``exc_info``) are not rewritten.

Stdlib only (L2): no web framework, no other auth module.
"""

from __future__ import annotations

import logging
import re

REDACTED = "[REDACTED]"
UNFORMATTABLE_MESSAGE = "[unformattable log message]"

# Set on the installed factory (idempotent install) and on each processed record (a record that
# went through a factory or a filter once is not rewritten again).
_FACTORY_MARKER = "_ao_log_redaction_factory"
_RECORD_MARKER = "_ao_redacted"

SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"((?:__Host-)?ao_sid_\d+=)[A-Za-z0-9_-]{16,}"),  # cookie values
    re.compile(r"((?i:x-ao-session-proof)\s*[:=]\s*)[A-Za-z0-9_-]{16,}"),  # proof header values
    re.compile(r"otpauth://\S+"),  # otpauth URIs
    re.compile(r"\$scrypt\$\S+"),  # password hash strings
    re.compile(r"\b[A-Z2-7]{32}\b"),  # base32 TOTP secrets (20 bytes)
    re.compile(r"\b[0-9A-HJKMNP-TV-Z]{4}(?:-[0-9A-HJKMNP-TV-Z]{4}){3}\b"),  # recovery / enrollment
    re.compile(
        r'("?(?:password|current_password|new_password|code|recovery_code|enrollment_token'
        r'|secret|token|session_proof)"?\s*[:=]\s*)("[^"]*"|\S+)',
        re.IGNORECASE,
    ),
)

# Arg types that cannot carry a secret and that positional formatters read as numbers.
_KEEP_TYPES = (int, float)

_logger = logging.getLogger(__name__)  # deliberately NOT an auth_logger: no filter on itself


def redact(text: str) -> str:
    """Replace every secret-looking span of *text* with ``[REDACTED]``.

    A pattern with a capture group keeps group 1 (the key or cookie name) and redacts the value;
    a pattern without one redacts the whole match. Text without secrets is returned unchanged.
    """
    for pattern in SECRET_PATTERNS:
        if pattern.groups:
            text = pattern.sub(lambda m: m.group(1) + REDACTED, text)
        else:
            text = pattern.sub(REDACTED, text)
    return text


def _redact_args(args: object) -> object:
    """Redact the string members of a record's ``args``, keeping the tuple/mapping *shape*.

    Formatters other than ``%``-style ones read ``args`` positionally (uvicorn's ``AccessFormatter``
    unpacks a 5-tuple), so replacing the args with ``None`` or ``()`` would break them.
    """
    if isinstance(args, tuple):
        return tuple(redact(a) if isinstance(a, str) else a for a in args)
    if isinstance(args, dict):
        return {k: redact(v) if isinstance(v, str) else v for k, v in args.items()}
    return args


def _redact_in_place(record: logging.LogRecord, redacted_message: str) -> None:
    """Redact msg/args keeping the args shape; fall back to a flat message if a secret survives."""
    original = (record.msg, record.args)
    record.msg = redact(record.msg) if isinstance(record.msg, str) else record.msg
    record.args = _redact_args(record.args)  # type: ignore[assignment]
    try:
        message = record.getMessage()
        if redact(message) == message:
            return
    except Exception:  # noqa: BLE001 - fall through to the flat form
        pass
    record.msg, record.args = original
    _set_flat_message(record, redacted_message)


def _set_flat_message(record: logging.LogRecord, redacted_message: str) -> None:
    """Replace the record's message with *redacted_message*, keeping a tuple ``args`` shape.

    Per-arg and formatted-line redaction can disagree (``GET /x?code= HTTP/1.1``: the line-level
    ``\\s*\\S+`` runs past the empty value, the lone ``?code=`` arg has nothing to match). A flat
    ``args=()`` would then break formatters that unpack ``args`` (uvicorn's access formatter), one
    traceback per request. So a tuple keeps its length and its numeric members (the status code
    stays an int; any other object could hide a secret in its ``__str__``); the redacted line
    goes in the first string slot, every other slot becomes empty (it could hold a fragment of
    the secret), and the template consumes them all.
    """
    args = record.args
    if isinstance(args, tuple) and args:
        carrier = next((i for i, a in enumerate(args) if isinstance(a, str)), None)
        if carrier is not None:
            record.msg = " ".join("%s" if i == carrier else "%.0s" for i in range(len(args)))
            record.args = tuple(
                redacted_message if i == carrier else (a if isinstance(a, _KEEP_TYPES) else "")
                for i, a in enumerate(args)
            )
            return
    record.msg, record.args = redacted_message, ()  # empty args: msg is never %-formatted


def _scrub_record(record: logging.LogRecord) -> None:
    """Redact *record* in place, once.

    A record whose formatted message has nothing to redact is left untouched (so uvicorn's access
    records keep their positional ``args``); otherwise msg/args are redacted in place.
    """
    if getattr(record, _RECORD_MARKER, False):
        return
    try:
        message = record.getMessage()
    except Exception as exc:  # a __str__ / %-format failure must never leak raw arguments
        _logger.error(
            "log record could not be formatted (%s); message withheld", type(exc).__name__
        )
        record.msg, record.args = UNFORMATTABLE_MESSAGE, ()
    else:
        redacted = redact(message)
        if redacted != message:
            _redact_in_place(record, redacted)
    setattr(record, _RECORD_MARKER, True)


class SecretRedactingFilter(logging.Filter):
    """Logger filter that redacts each record it sees; never drops a record."""

    def filter(self, record: logging.LogRecord) -> bool:
        _scrub_record(record)
        return True


def auth_logger(name: str) -> logging.Logger:
    """``logging.getLogger(name)`` with exactly one :class:`SecretRedactingFilter` attached."""
    logger = logging.getLogger(name)
    if not any(isinstance(f, SecretRedactingFilter) for f in logger.filters):
        logger.addFilter(SecretRedactingFilter())
    return logger


def install_log_redaction() -> None:
    """Redact every log record in the process, including non-propagating loggers (uvicorn).

    Wraps the current ``LogRecord`` factory (handler filters would miss loggers that do not
    propagate). Idempotent: a marker on the installed factory stops double wrapping, and the
    per-record marker makes a stacked wrapper harmless. The previous factory is always called.
    """
    previous = logging.getLogRecordFactory()
    if getattr(previous, _FACTORY_MARKER, False):
        return

    def factory(*args: object, **kwargs: object) -> logging.LogRecord:
        record = previous(*args, **kwargs)
        _scrub_record(record)
        return record

    setattr(factory, _FACTORY_MARKER, True)
    logging.setLogRecordFactory(factory)
