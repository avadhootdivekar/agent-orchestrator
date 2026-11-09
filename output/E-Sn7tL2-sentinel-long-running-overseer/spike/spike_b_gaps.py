"""T-Sn0Spk (b, part 1): silent-gap distribution of HEALTHY tasks from real transcripts. READ-ONLY.
Reads only <workspace>/.orchestrator/runs/*/*/attempt-*/transcript.jsonl (never workflows/**/runs).
    python3 -I spike_b_gaps.py /usr/avadhoot/mounted/ao-runner-ao/.orchestrator/runs
A "gap" = wall time between consecutive timestamped stream events (assistant/user). Events without
`timestamp` (thinking_tokens, tool_progress, rate_limit_event, ...) also grow the file, so the true
file-growth silence is <= these gaps: this is a conservative (pessimistic) liveness estimate.
"""

import datetime as dt
import glob
import json
import os
import sys

root = sys.argv[1]


def ts(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def pct(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1))))]


tool_gaps, model_gaps, all_gaps, per_task_max = [], [], [], []
n_attempts = n_healthy = 0
by_tool = {}
for f in glob.glob(os.path.join(root, "*", "*", "attempt-*", "transcript.jsonl")):
    n_attempts += 1
    prev = None
    prev_ev = None
    gaps = []
    healthy = False
    tool_names = {}
    try:
        lines = open(f, encoding="utf-8", errors="replace").read().splitlines()
    except OSError:
        continue
    for l in lines:
        try:
            d = json.loads(l)
        except ValueError:
            continue
        if d.get("type") == "result":
            healthy = d.get("subtype") == "success" and not d.get("is_error")
        if d.get("type") == "assistant":
            for c in (d.get("message") or {}).get("content") or []:
                if isinstance(c, dict) and c.get("type") == "tool_use":
                    tool_names[c.get("id")] = c.get("name")
        t = d.get("timestamp")
        if not t or d.get("type") not in ("assistant", "user"):
            continue
        t = ts(t)
        if prev is not None and t >= prev:
            kind = "tool" if d["type"] == "user" else "model"
            tn = None
            if d["type"] == "user":
                for c in (d.get("message") or {}).get("content") or []:
                    if isinstance(c, dict) and c.get("type") == "tool_result":
                        tn = tool_names.get(c.get("tool_use_id"))
            gaps.append((t - prev, kind, tn))
        prev = t
    if not healthy or not gaps:
        continue
    n_healthy += 1
    per_task_max.append(max(g for g, _, _ in gaps))
    for g, kind, tn in gaps:
        all_gaps.append(g)
        (tool_gaps if kind == "tool" else model_gaps).append(g)
        if tn:
            by_tool.setdefault(tn, []).append(g)


def summ(xs):
    return {
        "n": len(xs),
        **{f"p{p}": round(pct(xs, p), 1) for p in (50, 90, 95, 99, 99.9)},
        "max": round(max(xs), 1),
    }


out = {
    "attempts_scanned": n_attempts,
    "healthy_attempts": n_healthy,
    "all_gaps_s": summ(all_gaps),
    "model_gaps_s(assistant after X)": summ(model_gaps),
    "tool_gaps_s(user tool_result after X)": summ(tool_gaps),
    "per_task_max_gap_s": summ(per_task_max),
    "by_tool_p99_max": {
        k: {"n": len(v), "p99": round(pct(v, 99), 1), "max": round(max(v), 1)}
        for k, v in sorted(by_tool.items(), key=lambda kv: -len(kv[1]))
        if len(v) >= 20
    },
    "gaps_over_minutes": {
        m: sum(1 for g in all_gaps if g > m * 60) for m in (2, 5, 10, 15, 20, 30)
    },
}
buckets = [0, 5, 15, 30, 60, 120, 300, 600, 1200, 1e9]
out["histogram_all_gaps"] = {
    f"{int(a)}-{'inf' if b > 1e8 else int(b)}s": sum(1 for g in all_gaps if a <= g < b)
    for a, b in zip(buckets, buckets[1:])
}
print(json.dumps(out, indent=1))
