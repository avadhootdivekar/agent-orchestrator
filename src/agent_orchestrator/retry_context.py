"""Retry helpers: task-written outcome marker and bounded prior-attempt context.

Deterministic, paths-and-small-strings only (NFR-1). The marker is untrusted agent output:
size-capped and schema-checked, never executed or interpolated into a shell.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)

OUTCOME_FILENAME = "outcome.json"
OUTCOME_SCHEMA = "ao.task.outcome/v1"
PRIOR_ATTEMPTS_FILENAME = "prior-attempts.md"
MAX_MARKER_BYTES = 4096
MAX_REASON_CHARS = 300
MAX_ERROR_CHARS = 500
MAX_PRIOR_ATTEMPTS_BYTES = 4096

_HINTS = {
    "timeout": (
        "previous attempt timed out; do not re-run the same long-running command; "
        "narrow the scope or use a bounded alternative"
    ),
    "max_turns": (
        "previous attempt ran out of turns; do less per step, skip exploration, "
        "and write the required outputs early"
    ),
    "exit_nonzero": "previous attempt exited non-zero; fix the cause in its error first",
    "other": "previous attempt failed; avoid repeating its blocking step",
}


@dataclass(frozen=True)
class AttemptRecord:
    attempt: int
    status: str
    exit_code: int | None
    error: str | None
    output_dir: str


def read_no_retry_marker(attempt_output_dir: str) -> str | None:
    """Return the reason when the attempt wrote a valid ``retry: false`` marker, else None."""
    path = os.path.join(attempt_output_dir, OUTCOME_FILENAME)
    try:
        if os.path.getsize(path) > MAX_MARKER_BYTES:
            return None
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("schema") != OUTCOME_SCHEMA:
        return None
    if data.get("retry") is not False:
        return None
    reason = data.get("reason")
    return reason[:MAX_REASON_CHARS] if isinstance(reason, str) else ""


def classify_failure(record: AttemptRecord) -> str:
    if record.status == "timed_out" or record.error == "timeout":
        return "timeout"
    result_path = os.path.join(record.output_dir, "result.json")
    try:
        with open(result_path, encoding="utf-8") as fh:
            if "error_max_turns" in fh.read(MAX_MARKER_BYTES * 16):
                return "max_turns"
    except OSError:
        pass
    if record.exit_code not in (None, 0):
        return "exit_nonzero"
    return "other"


def write_prior_attempts(attempt_output_dir: str, records: list[AttemptRecord]) -> str:
    """Write the bounded ``prior-attempts.md`` into *attempt_output_dir*; return its path."""
    lines = ["# Prior attempts (newest first)", ""]
    for i, rec in enumerate(reversed(records)):
        kind = classify_failure(rec)
        err = " ".join((rec.error or "").split())[:MAX_ERROR_CHARS]
        if i == 0:
            lines.append(
                f"- attempt {rec.attempt}: status={rec.status} exit_code={rec.exit_code} "
                f"class={kind}"
            )
            lines.append(f"  error: {err}")
            lines.append(f"  hint: {_HINTS[kind]}")
            lines.append(
                f"  files: {rec.output_dir}/stderr.txt, {rec.output_dir}/transcript.jsonl, "
                f"{rec.output_dir}/result.json"
            )
        else:
            lines.append(f"- attempt {rec.attempt}: status={rec.status} class={kind}")
    text = "\n".join(lines) + "\n"
    data = text.encode("utf-8")[:MAX_PRIOR_ATTEMPTS_BYTES].decode("utf-8", errors="ignore")
    os.makedirs(attempt_output_dir, exist_ok=True)
    path = os.path.join(attempt_output_dir, PRIOR_ATTEMPTS_FILENAME)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(data)
    return path
