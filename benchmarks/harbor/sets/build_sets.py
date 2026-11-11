"""Build a calibration set file (regression / grading / excluded) from Harbor job dirs.

One attempt per task (T-Dw5Cal Phase 1). Classification:
  solved   reward 1                      -> regression-set candidate
  failed   reward 0, no exception        -> grading-set candidate ("failed once")
  excluded no verifier reward / exception -> infra error, not classified (rerun it)
A task attempted in several jobs uses the latest non-excluded trial; every trial is listed.

    python3 build_sets.py --tasks-dir /var/tmp/ao-harbor/datasets/deep-swe-1-1 \
        --jobs '/var/tmp/ao-harbor/jobs/p1a-deepswe-cc-sonnet' \
        --jobs '/var/tmp/ao-harbor/jobs/p1c-deepswe-cc-sonnet-*' \
        --meta model=claude-sonnet-5-5 --out deepswe-v1.1-sonnet-5.5.json
"""

from __future__ import annotations

import argparse
import glob
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from sample_deepswe import load_tasks


def _minutes(span: dict[str, Any] | None) -> float | None:
    if not span or not span.get("started_at") or not span.get("finished_at"):
        return None
    start = datetime.fromisoformat(span["started_at"].replace("Z", "+00:00"))
    end = datetime.fromisoformat(span["finished_at"].replace("Z", "+00:00"))
    return round((end - start).total_seconds() / 60, 1)


def trial_row(result_path: Path) -> dict[str, Any]:
    d = json.loads(result_path.read_text())
    rewards = (d.get("verifier_result") or {}).get("rewards") or {}
    exc = (d.get("exception_info") or {}).get("exception_type")
    reward = rewards.get("reward")
    if reward is None or exc:
        klass = "excluded"
    else:
        klass = "solved" if reward == 1 else "failed"
    task = (d.get("task_name") or "").split("/")[-1]
    return {
        "task": task,
        "class": klass,
        "reward": reward,
        "partial": rewards.get("partial"),
        "f2p": f"{rewards.get('f2p_passed')}/{rewards.get('f2p_total')}",
        "p2p": f"{rewards.get('p2p_passed')}/{rewards.get('p2p_total')}",
        "cost_usd": (d.get("agent_result") or {}).get("cost_usd"),
        "agent_minutes": _minutes(d.get("agent_execution")),
        "trial_minutes": _minutes(d),
        "exception": exc,
        "finished_at": d.get("finished_at"),
        "trial_dir": str(result_path.parent),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Build a DeepSWE calibration set file.")
    ap.add_argument("--tasks-dir", type=Path, required=True)
    ap.add_argument("--jobs", action="append", required=True, help="job dir glob; repeatable")
    ap.add_argument("--meta", action="append", default=[], help="key=value for the header")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    meta_tasks = load_tasks(args.tasks_dir)
    trials: list[dict[str, Any]] = []
    for pattern in args.jobs:
        for job in sorted(glob.glob(pattern)):
            for result in sorted(Path(job).glob("*/result.json")):
                row = trial_row(result)
                row.update(meta_tasks.get(row["task"], {}))
                trials.append(row)

    by_task: dict[str, dict[str, Any]] = {}
    for row in sorted(trials, key=lambda r: r.get("finished_at") or ""):
        current = by_task.get(row["task"])
        if current is None or row["class"] != "excluded" or current["class"] == "excluded":
            by_task[row["task"]] = row
    classified = sorted(
        by_task.values(), key=lambda r: (r["class"], r.get("language", ""), r["task"])
    )
    spent = sum(r["cost_usd"] or 0 for r in trials)
    doc = {
        "schema": "ao.bench.harbor-calibration/v1",
        **dict(kv.split("=", 1) for kv in args.meta),
        "attempts_per_task": 1,
        "summary": {
            "tasks": len(classified),
            "solved": sum(r["class"] == "solved" for r in classified),
            "failed": sum(r["class"] == "failed" for r in classified),
            "excluded": sum(r["class"] == "excluded" for r in classified),
            "spent_usd": round(spent, 2),
        },
        "regression_set": [r["task"] for r in classified if r["class"] == "solved"],
        "grading_set": [r["task"] for r in classified if r["class"] == "failed"],
        "excluded": [r["task"] for r in classified if r["class"] == "excluded"],
        "tasks": classified,
        "all_trials": trials,
    }
    args.out.write_text(json.dumps(doc, indent=2) + "\n")
    print(json.dumps(doc["summary"]), "grading:", doc["grading_set"])


if __name__ == "__main__":
    main()
