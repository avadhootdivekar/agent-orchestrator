"""python -m agent_orchestrator.validate <specs_dir>

Validates all example specs found under the given directory.
"""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> None:
    specs_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("specs")

    from .config import load_agents, load_reposets
    from .spec import load_workflow

    errors: list[str] = []

    for f in sorted(specs_dir.glob("examples/workflow*.json")):
        try:
            load_workflow(f)
        except Exception as e:
            errors.append(f"{f}: {e}")

    for f in sorted(specs_dir.glob("examples/reposet*.json")):
        try:
            load_reposets(f)
        except Exception as e:
            errors.append(f"{f}: {e}")

    for f in sorted(specs_dir.glob("examples/agents*.json")):
        try:
            load_agents(f)
        except Exception as e:
            errors.append(f"{f}: {e}")

    if errors:
        for err in errors:
            print(f"FAIL: {err}", file=sys.stderr)
        sys.exit(1)

    print(f"OK: validated {specs_dir}")


if __name__ == "__main__":
    main()
