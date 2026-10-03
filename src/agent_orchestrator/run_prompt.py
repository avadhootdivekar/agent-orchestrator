"""Capture of the run prompt into a ``RunPrompt`` value (E-Us9Kd4 FR-13).

This lives OUTSIDE the engine on purpose: the engine never reads payload artifacts (NFR-1),
so the CLI layer reads the workflow's ``prompt_path`` file and hands the Orchestrator a plain
value. The dashboard service reuses :func:`file_sha256` to detect a prompt file edited after
the run started.
"""

from __future__ import annotations

import codecs
import hashlib
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from .models import (
    MAX_PROMPT_BYTES,
    PROMPT_SOURCE_CLI_PROMPT,
    PROMPT_SOURCE_CLI_PROMPT_FILE,
    PROMPT_SOURCE_WORKFLOW_FILE,
    PromptSource,
    RunPrompt,
)

_READ_CHUNK_BYTES = 64 * 1024


def prompt_source(prompt: str | None, prompt_file: str | None) -> PromptSource:
    """Map the ``ao run`` flags to a ``RunPrompt.source`` (no flag => hand-written file)."""
    if prompt is not None:
        return PROMPT_SOURCE_CLI_PROMPT
    if prompt_file is not None:
        return PROMPT_SOURCE_CLI_PROMPT_FILE
    return PROMPT_SOURCE_WORKFLOW_FILE


def file_sha256(path: Path) -> str | None:
    """Streaming sha256 of the file's bytes (bounded memory); ``None`` if unreadable."""
    digest = hashlib.sha256()
    try:
        with path.open("rb") as fh:
            while chunk := fh.read(_READ_CHUNK_BYTES):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def _truncate_utf8(data: bytes, limit: int) -> bytes:
    """Cut *data* to at most *limit* bytes without splitting a multi-byte character."""
    return data[:limit].decode("utf-8", errors="ignore").encode("utf-8")


def capture_run_prompt(
    resolved_path: Path,
    declared_path: str,
    source: PromptSource,
    clock: Callable[[], datetime],
) -> RunPrompt | None:
    """Read *resolved_path* (bounded) into a ``RunPrompt``; ``None`` if it is not a readable file.

    Streams the file once: keeps only the first ``MAX_PROMPT_BYTES`` bytes, but hashes and
    character-counts the WHOLE file so ``sha256``/``chars`` describe the full text.
    """
    digest = hashlib.sha256()
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    head = bytearray()
    total = 0
    chars = 0
    try:
        with resolved_path.open("rb") as fh:
            while chunk := fh.read(_READ_CHUNK_BYTES):
                digest.update(chunk)
                total += len(chunk)
                chars += len(decoder.decode(chunk))
                if len(head) <= MAX_PROMPT_BYTES:
                    head.extend(chunk[: MAX_PROMPT_BYTES + 1 - len(head)])
            chars += len(decoder.decode(b"", final=True))
    except OSError:
        return None

    truncated = total > MAX_PROMPT_BYTES
    kept = _truncate_utf8(bytes(head), MAX_PROMPT_BYTES) if truncated else bytes(head)
    return RunPrompt(
        text=kept.decode("utf-8", errors="replace"),
        truncated=truncated,
        chars=chars,
        source=source,
        path=declared_path,
        sha256=digest.hexdigest(),
        captured_at=clock().isoformat(),
    )
