#!/usr/bin/env python3
"""Example post_hook grading script for `specs/examples/workflow-hooks.json` (E-AMSSHX).

Reads `AO_HOOK_CONTEXT_PATH` (paths only -- the orchestrator's own NFR-1 invariant; this
script never receives file *content* from the engine) and checks that every path the graded
task declared in `outputs` actually exists on disk. Writes
`{"score": <0.0|1.0>, "detail": {"solved": <bool>, ...}}` to `AO_HOOK_RESULT_PATH` and exits
0 if solved else 1 -- per the HLD (D3), the EXIT CODE is the hook's pass/fail verdict; the
JSON `score`/`detail` are additive, informational detail only and never override it.

Controllable for demos/e2e tests (documented here since HLD §7's Epic-B-facing scripts need
exactly this shape): set `AO_EXAMPLE_GRADE_FORCE_FAIL=1` in the environment to force a
failing grade regardless of the actual output paths, so a scenario can exercise the
`on_failure="ignore"` vs `on_failure="fail_task"` policies without needing a genuinely broken
upstream task.
"""

from __future__ import annotations

import json
import os
import sys


def main() -> int:
    context_path = os.environ.get("AO_HOOK_CONTEXT_PATH")
    result_path = os.environ.get("AO_HOOK_RESULT_PATH")
    if not context_path or not result_path:
        # Missing required env vars -- fail closed; no result file to write.
        return 1

    with open(context_path, encoding="utf-8") as f:
        context = json.load(f)

    output_paths = context.get("output_paths") or []
    force_fail = os.environ.get("AO_EXAMPLE_GRADE_FORCE_FAIL") == "1"
    missing = [p for p in output_paths if not os.path.exists(p)]
    solved = not force_fail and not missing

    result = {
        "score": 1.0 if solved else 0.0,
        "detail": {
            "solved": solved,
            "checked_paths": output_paths,
            "missing_paths": missing,
        },
    }
    with open(result_path, "w", encoding="utf-8") as f:
        json.dump(result, f)

    return 0 if solved else 1


if __name__ == "__main__":
    sys.exit(main())
