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


def _scrub_record(record: logging.LogRecord) -> None:
    """Redact *record* in place, once: ``msg`` becomes the redacted formatted message."""
    if getattr(record, _RECORD_MARKER, False):
        return
    try:
        message = record.getMessage()
    except Exception as exc:  # a __str__ / %-format failure must never leak raw arguments
        message = UNFORMATTABLE_MESSAGE
        _logger.error(
            "log record could not be formatted (%s); message withheld", type(exc).__name__
        )
    record.msg = redact(message)
    record.args = None
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
