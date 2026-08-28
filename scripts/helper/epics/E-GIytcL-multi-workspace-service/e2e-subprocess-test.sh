#!/usr/bin/env bash
# E2E subprocess test for multi-workspace service
# Tests real `ao service run` with real child processes and SIGTERM handling

set -e

REPO_ROOT="/usr/avadhoot/mounted/agent-orchestrator"
SCRATCHPAD="/tmp/claude-1000/-usr-avadhoot-mounted-agent-orchestrator/8b8264b3-8332-4076-83bb-024a95e545ce/scratchpad"
TEST_DIR="${SCRATCHPAD}/ao-e2e-test-$$"
mkdir -p "$TEST_DIR"

# Export test env for all commands
export AO_SERVICE_CONFIG="${TEST_DIR}/service.yaml"
export AO_SERVICE_STATE_DIR="${TEST_DIR}/state"

# Cleanup function
cleanup() {
    echo "=== CLEANUP ==="
    # Kill supervisor if still running
    if [ -n "$SUPERVISOR_PID" ] && kill -0 "$SUPERVISOR_PID" 2>/dev/null; then
        echo "Killing supervisor $SUPERVISOR_PID..."
        kill "$SUPERVISOR_PID" 2>/dev/null || true
        sleep 1
    fi
    # Kill decoy if still running
    if [ -n "$DECOY_PID" ] && kill -0 "$DECOY_PID" 2>/dev/null; then
        echo "Killing decoy $DECOY_PID..."
        kill "$DECOY_PID" 2>/dev/null || true
    fi
    # Verify everything is gone
    if pgrep -P 1 sleep | grep -q . 2>/dev/null; then
        echo "Warning: some processes still running, force-killing..."
        pkill -f "setsid sh -c 'sleep 120'" || true
    fi
    # Clean test directory
    rm -rf "$TEST_DIR"
    echo "Cleanup complete."
}

trap cleanup EXIT

# Create workspace directories
WS1="${TEST_DIR}/ws1"
WS2="${TEST_DIR}/ws2"
mkdir -p "$WS1" "$WS2"
echo "Created workspaces: $WS1, $WS2"

# Add workspaces via CLI
cd "$REPO_ROOT"
echo "=== Adding workspaces ==="
uv run python -m agent_orchestrator.cli service add "$WS1"
uv run python -m agent_orchestrator.cli service add "$WS2"

# List to confirm
echo "=== Listing workspaces ==="
uv run python -m agent_orchestrator.cli service list

# Choose a hub port
HUB_PORT=18770

# Start supervisor in background, redirect to log
LOG_FILE="${TEST_DIR}/supervisor.log"
echo "=== Starting supervisor on hub port $HUB_PORT ==="
uv run python -m agent_orchestrator.cli service run --hub-port "$HUB_PORT" > "$LOG_FILE" 2>&1 &
SUPERVISOR_PID=$!
echo "Supervisor started with PID $SUPERVISOR_PID"

# Wait for it to boot
sleep 3

# Check if supervisor is still alive
if ! kill -0 "$SUPERVISOR_PID" 2>/dev/null; then
    echo "ERROR: Supervisor died immediately. Log:"
    cat "$LOG_FILE"
    exit 1
fi

echo "=== Waiting for supervisor to fully boot ==="
sleep 2

# Verify via API that both workspaces are serving
echo "=== Checking supervisor status via API ==="
RETRIES=5
while [ $RETRIES -gt 0 ]; do
    STATUS=$(curl -s "http://127.0.0.1:${HUB_PORT}/api/service/status" || echo '{}')
    if echo "$STATUS" | grep -q '"root"'; then
        echo "Status API OK:"
        echo "$STATUS" | python3 -m json.tool 2>/dev/null | head -40 || echo "$STATUS"
        break
    fi
    echo "Retrying status API (${RETRIES} left)..."
    sleep 1
    RETRIES=$((RETRIES - 1))
done

# Extract workspace ports from status
WS1_PORT=$(echo "$STATUS" | python3 -c "
import sys, json
data = json.load(sys.stdin)
for ws in data.get('workspaces', []):
    if ws['root'] == '$WS1':
        print(ws['port'])
        break
" 2>/dev/null || echo '')

WS2_PORT=$(echo "$STATUS" | python3 -c "
import sys, json
data = json.load(sys.stdin)
for ws in data.get('workspaces', []):
    if ws['root'] == '$WS2':
        print(ws['port'])
        break
" 2>/dev/null || echo '')

if [ -z "$WS1_PORT" ] || [ -z "$WS2_PORT" ]; then
    echo "ERROR: Could not extract ports from status"
    echo "Status was: $STATUS"
    exit 1
fi

echo "WS1 port: $WS1_PORT, WS2 port: $WS2_PORT"

# Verify dashboards respond
echo "=== Testing workspace dashboards ==="
WS1_CODE=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:${WS1_PORT}/")
WS2_CODE=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:${WS2_PORT}/")
echo "WS1 dashboard HTTP code: $WS1_CODE"
echo "WS2 dashboard HTTP code: $WS2_CODE"

if [ "$WS1_CODE" != "200" ] || [ "$WS2_CODE" != "200" ]; then
    echo "ERROR: Dashboard responses not 200"
    exit 1
fi

# Spawn a decoy detached process (setsid for true detachment)
echo "=== Spawning decoy process ==="
setsid sh -c 'sleep 120' &
DECOY_PID=$!
disown $DECOY_PID
echo "Decoy PID: $DECOY_PID"

# Verify decoy is alive
if ! kill -0 "$DECOY_PID" 2>/dev/null; then
    echo "ERROR: Decoy is not alive"
    exit 1
fi

# Send SIGTERM to supervisor (not a process group)
echo "=== Sending SIGTERM to supervisor ($SUPERVISOR_PID) ==="
kill -TERM "$SUPERVISOR_PID"

# Wait for supervisor to exit (up to 15s)
WAIT_TIME=0
while [ $WAIT_TIME -lt 15 ]; do
    if ! kill -0 "$SUPERVISOR_PID" 2>/dev/null; then
        echo "Supervisor exited after $WAIT_TIME seconds"
        break
    fi
    sleep 1
    WAIT_TIME=$((WAIT_TIME + 1))
done

if kill -0 "$SUPERVISOR_PID" 2>/dev/null; then
    echo "ERROR: Supervisor did not exit after SIGTERM"
    kill -9 "$SUPERVISOR_PID" 2>/dev/null || true
    exit 1
fi

# Verify children are gone (dashboards no longer respond)
echo "=== Verifying children stopped ==="
sleep 1
WS1_CODE=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:${WS1_PORT}/" 2>&1 || echo 'FAIL')
WS2_CODE=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:${WS2_PORT}/" 2>&1 || echo 'FAIL')
echo "WS1 dashboard after SIGTERM: $WS1_CODE (should not be 200)"
echo "WS2 dashboard after SIGTERM: $WS2_CODE (should not be 200)"

if [ "$WS1_CODE" = "200" ] || [ "$WS2_CODE" = "200" ]; then
    echo "ERROR: Dashboards still responding after SIGTERM"
    exit 1
fi

# Verify decoy is still alive
echo "=== Verifying decoy still alive ==="
if kill -0 "$DECOY_PID" 2>/dev/null; then
    echo "SUCCESS: Decoy process $DECOY_PID is still alive"
else
    echo "ERROR: Decoy process died"
    exit 1
fi

echo "=== ALL CHECKS PASSED ==="
echo ""
echo "Summary:"
echo "  - Supervisor spawned and ran both workspaces on distinct ports"
echo "  - Both dashboards responded to HTTP GET"
echo "  - Decoy process spawned and alive"
echo "  - SIGTERM to supervisor killed children but left decoy alive"
echo "  - Supervisor exited cleanly"
