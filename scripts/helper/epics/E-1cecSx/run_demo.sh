#!/usr/bin/env bash
# E-1cecSx-cost-caching-optimization late-gate demonstration (T-UJElTR).
#
# Sets up a scratch workspace with a `fake`-executor workflow (zero cost, deterministic) and
# runs the real `ao` CLI end to end: `ao run` -> `ao report-timing [--task]` ->
# `ao report-outcomes [--grade]`. This is the human-readable companion to
# tests/test_e2e_cli_cost_caching.py (which asserts the same path programmatically) -- run this
# script to SEE the actual reports a user would look at, not just a pass/fail assertion.
#
# Usage: bash scripts/helper/epics/E-1cecSx/run_demo.sh
# Output: printed to stdout; also safe to redirect, e.g.
#   bash scripts/helper/epics/E-1cecSx/run_demo.sh > output/E-1cecSx-cost-caching-optimization/demo-run-output.txt 2>&1

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"
cd "$REPO_ROOT"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

echo "=== Scratch workspace: $WORK ==="
mkdir -p "$WORK/specs/instructions" "$WORK/repo" "$WORK/output"
cat > "$WORK/specs/instructions/dispatched_task.md" <<'EOF'
# dispatched_task
EOF
cat > "$WORK/specs/instructions/skip_task.md" <<'EOF'
# skip_task
EOF
echo "pre-existing output" > "$WORK/output/skip.txt"

cat > "$WORK/workflow.json" <<EOF
{
  "version": "1.0",
  "id": "cost-caching-demo-wf",
  "repo_set": "rs",
  "hooks": {
    "grade": {
      "type": "command",
      "command": ["python3", "$REPO_ROOT/specs/examples/hooks/grade.py"],
      "timeout_seconds": 30
    }
  },
  "tasks": [
    {
      "id": "dispatched_task",
      "agent": "ag",
      "instruction": "specs/instructions/dispatched_task.md",
      "outputs": ["output/dispatched.txt"],
      "post_hook": {"use": "grade", "on_failure": "ignore"}
    },
    {
      "id": "skip_task",
      "agent": "ag",
      "instruction": "specs/instructions/skip_task.md",
      "outputs": ["output/skip.txt"],
      "skip_if_outputs_exist": true
    }
  ]
}
EOF

cat > "$WORK/reposets.json" <<EOF
{
  "version": "1.0",
  "repo_sets": {
    "rs": {
      "workspace_root": "$WORK",
      "repos": [{"id": "core", "path": "repo", "role": "primary"}]
    }
  }
}
EOF

cat > "$WORK/agents.json" <<'EOF'
{"version": "1.0", "agents": {"ag": {"executor": "fake"}}}
EOF

export AO_WORKSPACE_ROOT="$WORK"
export HOME="$WORK/home"
export AO_STATE_DIR="$WORK/ao-state"
mkdir -p "$HOME"

SPEC_ARGS=(--workflow "$WORK/workflow.json" --reposets "$WORK/reposets.json" --agents "$WORK/agents.json")

echo
echo "=== ao run ==="
"$REPO_ROOT/.venv/bin/ao" run "${SPEC_ARGS[@]}"

RUN_ID="$(basename "$(find "$WORK/.orchestrator/runs" -mindepth 1 -maxdepth 1 -type d | head -1)")"
echo
echo "=== run_id: $RUN_ID ==="

echo
echo "=== ao report-timing --run-id $RUN_ID ==="
"$REPO_ROOT/.venv/bin/ao" report-timing --run-id "$RUN_ID" "${SPEC_ARGS[@]}"

echo
echo "=== ao report-outcomes --run-id $RUN_ID ==="
"$REPO_ROOT/.venv/bin/ao" report-outcomes --run-id "$RUN_ID" "${SPEC_ARGS[@]}"

echo
echo "=== ao report-outcomes --run-id $RUN_ID --grade grade (B3.2/B3.3, ADR-0015 decision 2) ==="
"$REPO_ROOT/.venv/bin/ao" report-outcomes --run-id "$RUN_ID" --grade grade "${SPEC_ARGS[@]}"

echo
echo "=== settlement_grades.json (written report artifact) ==="
cat "$WORK/.orchestrator/runs/$RUN_ID/settlement_grades.json"
echo
