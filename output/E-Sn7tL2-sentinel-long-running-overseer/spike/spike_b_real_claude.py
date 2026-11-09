"""T-Sn0Spk (b, part 3): does a REAL `claude -p --output-format stream-json` transcript keep growing during a long
silent tool call? One haiku run (cents). Polls os.stat size every 0.5s and prints growth timeline.
    python3 -I spike_b_real_claude.py
"""

import json
import os
import subprocess
import tempfile
import time

d = tempfile.mkdtemp(prefix="ao-spike-b-real-")
tp = os.path.join(d, "transcript.jsonl")
prompt = "Use the Bash tool exactly once to run exactly this command: timeout 35 bash -c 'until [ -f /nonexistent-flag ]; do sleep 2; done'; echo done Then reply with the single word: finished."
argv = [
    "claude",
    "-p",
    prompt,
    "--model",
    "haiku",
    "--max-turns",
    "4",
    "--output-format",
    "stream-json",
    "--verbose",
    "--permission-mode",
    "bypassPermissions",
    "--allowedTools",
    "Bash",
    "--disallowedTools",
    "Task",
]
with open(tp, "w") as tf:
    t0 = time.monotonic()
    proc = subprocess.Popen(
        argv,
        cwd=d,
        stdout=tf,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )
    last = -1
    changes = []
    while proc.poll() is None and time.monotonic() - t0 < 150:
        sz = os.stat(tp).st_size
        if sz != last:
            changes.append((round(time.monotonic() - t0, 1), sz))
            last = sz
        time.sleep(0.5)
    if proc.poll() is None:
        os.killpg(os.getpgid(proc.pid), 9)
        proc.wait()
types = []
for l in open(tp):
    try:
        e = json.loads(l)
    except ValueError:
        continue
    types.append((e.get("type"), e.get("subtype")))
gaps = [round(b[0] - a[0], 1) for a, b in zip(changes, changes[1:])]
print(
    json.dumps(
        {
            "rc": proc.returncode,
            "wall_s": round(time.monotonic() - t0, 1),
            "size_change_times_s": [c[0] for c in changes],
            "max_size_silence_s": max(gaps) if gaps else None,
            "n_size_changes": len(changes),
            "tool_result_snippet": [
                str(c)[:160]
                for l in open(tp)
                for e in [json.loads(l)]
                if e.get("type") == "user"
                for c in (
                    e["message"]["content"] if isinstance(e["message"]["content"], list) else []
                )
            ],
            "workdir": d,
            "event_types": [f"{a}/{b}" if b else a for a, b in types],
        },
        indent=1,
    )
)
