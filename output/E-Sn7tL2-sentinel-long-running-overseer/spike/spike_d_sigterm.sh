#!/usr/bin/env bash
# T-Sn0Spk (d): what does SIGTERM / SIGINT to `ao run` do today?  Usage: spike_d_sigterm.sh TERM|INT
# Runs the CLONE's code (uv run ao) in a temp workspace against a stub `claude` (sleeps 25s, spawns grandchild).
set -u
SIG=${1:-TERM}
SPIKE=$(cd "$(dirname "$0")" && pwd)
CLONE=$(cd "$SPIKE/../../.." && pwd)
W=$(mktemp -d /tmp/ao-spike-d-XXXX)
mkdir -p "$W/specs/instructions"; echo x > "$W/specs/instructions/i.md"
cat > "$W/wf.json" <<J
{"version":"1.0","id":"spike-d","repo_set":"rs","tasks":[
 {"id":"a","agent":"ag","instruction":"specs/instructions/i.md","outputs":["out/a.txt"]},
 {"id":"b","agent":"ag","instruction":"specs/instructions/i.md","outputs":["out/b.txt"]},
 {"id":"c","agent":"ag","instruction":"specs/instructions/i.md","outputs":["out/c.txt"],"depends_on":["a"]}]}
J
cat > "$W/reposets.json" <<J
{"version":"1.0","repo_sets":{"rs":{"workspace_root":"$W","repos":[{"id":"core","path":".","role":"primary"}]}}}
J
cat > "$W/agents.json" <<J
{"version":"1.0","agents":{"ag":{"executor":"claude_cli","command_template":["$SPIKE/stub_claude.sh"]}}}
J
export AO_WORKSPACE_ROOT="$W" STUB_SLEEP=25 STUB_CHILD_PIDFILE="$W/child.pid"
cd "$CLONE"
setsid uv run ao run --workflow "$W/wf.json" --reposets "$W/reposets.json" --agents "$W/agents.json" > "$W/ao.out" 2>&1 &
sleep 6
AOPID=$(pgrep -f "bin/ao run --workflow $W/wf.json" | head -1)
echo "ao pid=$AOPID; stub procs before signal: $(pgrep -f stub_claude.sh | wc -l)"
kill -$SIG "$AOPID"
T0=$(date +%s.%N)
for i in $(seq 1 100); do kill -0 "$AOPID" 2>/dev/null || break; sleep 0.1; done
T1=$(date +%s.%N)
echo "ao alive after signal+10s: $(kill -0 "$AOPID" 2>/dev/null && echo yes || echo no); exit took $(echo "$T1 - $T0" | bc)s"
sleep 1
echo "stub_claude procs still running after ao died: $(pgrep -f stub_claude.sh | wc -l) (orphans)"
echo "grandchild alive: $(kill -0 $(cat "$W/child.pid" 2>/dev/null) 2>/dev/null && echo yes || echo no)"
RUN=$(ls -d "$W"/.orchestrator/runs/*/ | head -1)
python3 - "$RUN" <<'P'
import json,sys
s=json.load(open(sys.argv[1]+"/state.json"))
print("state.status =", s["status"], "| tasks:", {k:v["status"] for k,v in s["tasks"].items()})
P
echo "--- run.log tail:"; tail -n 4 "$RUN/run.log" | cut -c1-200
echo "--- ao.out tail:"; tail -n 5 "$W/ao.out" | cut -c1-200
# cleanup our orphans
pkill -f "$SPIKE/stub_claude.sh"; pkill -f "sleep 25"
echo "workspace: $W"
