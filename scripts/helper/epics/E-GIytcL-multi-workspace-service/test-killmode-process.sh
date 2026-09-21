#!/usr/bin/env bash
# Test: systemd KillMode=process does not kill detached grandchild processes

set -e

echo "=== Part C: systemd KillMode=process verification ==="
echo ""

# Verify systemd-run is available
if ! command -v systemd-run &> /dev/null; then
    echo "BLOCKED: systemd-run not found in PATH"
    exit 1
fi

# Verify user systemd session
if ! systemctl --user status &> /dev/null; then
    echo "BLOCKED: systemd user session not available"
    exit 1
fi

UNIT_NAME="ao-epic-killmode-test"
PID_FILE="/tmp/${UNIT_NAME}.pid"
SCRATCH_DIR="/tmp/ao-killmode-test-$$"
mkdir -p "$SCRATCH_DIR"

# Cleanup function
cleanup() {
    echo "=== CLEANUP ==="
    # Stop the unit if still running
    if systemctl --user is-active "$UNIT_NAME" &> /dev/null; then
        echo "Stopping transient unit $UNIT_NAME..."
        systemctl --user stop "$UNIT_NAME" 2>/dev/null || true
        sleep 1
    fi
    # Kill grandchild if still alive
    if [ -f "$PID_FILE" ]; then
        GRANDCHILD_PID=$(cat "$PID_FILE")
        if kill -0 "$GRANDCHILD_PID" 2>/dev/null; then
            echo "Killing grandchild $GRANDCHILD_PID..."
            kill "$GRANDCHILD_PID" 2>/dev/null || true
        fi
    fi
    # Clean up
    rm -f "$PID_FILE"
    rm -rf "$SCRATCH_DIR"
    echo "Cleanup complete."
}

trap cleanup EXIT

echo "1. Creating transient systemd unit with KillMode=process"
echo "   Unit: $UNIT_NAME"
echo ""

# Create a transient service with KillMode=process that spawns a setsid-detached child
# The main process stays alive via sleep, grandchild is detached via setsid
systemd-run \
    --user \
    --collect \
    --unit="$UNIT_NAME" \
    -p KillMode=process \
    /bin/sh -c "setsid sh -c 'sleep 120 &' & echo GRANDCHILD_PID=\$(jobs -p) > $PID_FILE; sleep 300"

sleep 2

echo "2. Waiting for unit to start and reading grandchild PID..."
RETRIES=5
while [ $RETRIES -gt 0 ] && [ ! -f "$PID_FILE" ]; do
    sleep 1
    RETRIES=$((RETRIES - 1))
done

if [ ! -f "$PID_FILE" ]; then
    echo "ERROR: PID file not created. Unit status:"
    systemctl --user status "$UNIT_NAME" || true
    exit 1
fi

# Extract the actual grandchild PID (it's written as GRANDCHILD_PID=<pid>)
GRANDCHILD_PID=$(grep -oP '(?<=GRANDCHILD_PID=)\d+' "$PID_FILE" | head -1)

if [ -z "$GRANDCHILD_PID" ]; then
    # Try to find it from the running processes instead
    # Look for sleep 120 process started by this unit
    GRANDCHILD_PID=$(pgrep -f "sleep 120" | head -1)
fi

if [ -z "$GRANDCHILD_PID" ]; then
    echo "ERROR: Could not determine grandchild PID"
    echo "PID file contents:"
    cat "$PID_FILE"
    exit 1
fi

echo "Grandchild PID: $GRANDCHILD_PID"

# Verify it's alive
if ! kill -0 "$GRANDCHILD_PID" 2>/dev/null; then
    echo "ERROR: Grandchild process is not alive"
    exit 1
fi

echo "Grandchild alive: $(ps -p $GRANDCHILD_PID -o comm=)"

echo ""
echo "3. Stopping the transient unit..."
systemctl --user stop "$UNIT_NAME"
sleep 1

echo ""
echo "4. Verifying grandchild survived the stop..."
if kill -0 "$GRANDCHILD_PID" 2>/dev/null; then
    echo "SUCCESS: Grandchild $GRANDCHILD_PID is still alive after unit stop"
    echo "Process info: $(ps -p $GRANDCHILD_PID -o pid=,cmd=)"
else
    echo "ERROR: Grandchild died when unit was stopped (KillMode=process not working)"
    exit 1
fi

echo ""
echo "5. Checking unit is no longer listed..."
if systemctl --user list-units --all | grep -q "ao-epic-killmode-test"; then
    echo "Warning: Unit still in list (might still be visible if recently stopped)"
else
    echo "Unit cleanup confirmed"
fi

echo ""
echo "=== ALL SYSTEMD KILLMODE CHECKS PASSED ==="
echo ""
echo "Summary:"
echo "  - Transient service created with KillMode=process"
echo "  - Service spawned a setsid-detached grandchild (PID $GRANDCHILD_PID)"
echo "  - systemctl --user stop ao-epic-killmode-test was called"
echo "  - Grandchild remained alive after the stop"
echo "  - This confirms KillMode=process only kills the main process, not cgroup children"
