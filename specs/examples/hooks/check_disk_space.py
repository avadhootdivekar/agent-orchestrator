#!/usr/bin/env python3
"""Example pre_hook script for `specs/examples/workflow-hooks.json` (E-AMSSHX).

A trivial precondition check: confirms the current working directory's filesystem has at
least MIN_FREE_BYTES of free disk space before the task's agent is dispatched. Pure stdlib
(`shutil`), no network access, no third-party deps -- runs in CI unmodified. Demonstrates the
"gate a task on an external precondition" pre_hook use case (the orchestrator runs this
BEFORE the agent executor, per HLD §5-6) rather than being a pure no-op stub.

Writes no AO_HOOK_RESULT_PATH JSON -- this hook's verdict is the exit code alone (D3), no
extra detail needed for a pass/fail disk-space check.
"""

from __future__ import annotations

import shutil
import sys

# Deliberately tiny so this demo hook always passes in a normal dev/CI environment; a real
# workflow would size this to the task's actual expected disk footprint.
MIN_FREE_BYTES = 1_000_000  # 1 MB


def main() -> int:
    usage = shutil.disk_usage(".")
    if usage.free < MIN_FREE_BYTES:
        print(
            f"insufficient disk space: {usage.free} bytes free, need {MIN_FREE_BYTES}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
