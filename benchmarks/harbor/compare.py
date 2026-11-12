#!/usr/bin/env python3
"""Compare two Harbor arms over many jobs and attempts, with the owner's time rule. Stdlib only.

    python3 benchmarks/harbor/compare.py \
        --arm plain='/var/tmp/ao-harbor/jobs/p2-r1-cc-sonnet-*' \
        --arm ao='/var/tmp/ao-harbor/jobs/p2-r1-ao-sonnet-*' \
        [--sets sets/deepswe-v1.1-sonnet-5.5.json] [--markdown] [--json OUT]

Each `--arm NAME=GLOB` collects every trial (result.json) of the matching job dirs, so an arm
may be one job per task (budgeted.py) and several attempts per task. The first arm is the
baseline (A), the second the variant (B).

Time rule (owner, 2026-11-12): a solve only counts if it was not too slow compared with the
other arm on the same task. With `ref` = the other arm's median agent minutes on that task,
a solved attempt taking more than max(ref + SLACK, RATIO x ref) minutes counts as failed
("time-fail"). Defaults SLACK=60, RATIO=2: short tasks may differ by an hour, long tasks by
at most 2x. Both arms are judged the same way; with no attempt by the other arm, no rule.

Exceptions follow summarize.py: an agent timeout is an attempt (reward 0); any other trial
exception is infra, excluded and listed.

Statistics: per arm, attempt-level solve rate with a Wilson 95% interval. Paired over tasks
both arms attempted: per-task solve rates, B-better / A-better / tie counts, an exact
two-sided sign test on the untied tasks, and a seeded paired-bootstrap 95% interval for the
mean per-task difference (B - A). With fewer than ~10 untied tasks, no claim is safe.
"""

from __future__ import annotations

import argparse
import glob
import json
import math
import random
import statistics
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from summarize import Trial, load_trials  # noqa: E402

DEFAULT_SLACK_MINUTES = 60.0
DEFAULT_RATIO = 2.0
BOOTSTRAP_RESAMPLES = 10_000
BOOTSTRAP_SEED = 20261112
WILSON_Z = 1.96


@dataclass
class Attempt:
    task: str
    solved_raw: bool
    minutes: float | None
    cost_usd: float
    timeout: bool
    time_fail: bool = False

    @property
    def solved(self) -> bool:
        return self.solved_raw and not self.time_fail


@dataclass
class Arm:
    name: str
    attempts: dict[str, list[Attempt]] = field(default_factory=dict)
    infra: list[tuple[str, str]] = field(default_factory=list)


def task_key(name: str) -> str:
    return name.split("/")[-1]


def load_arm(name: str, pattern: str) -> Arm:
    arm = Arm(name)
    for job in sorted(glob.glob(pattern)):
        trials: list[Trial] = load_trials(Path(job))[0]
        for t in trials:
            task = task_key(t.task)
            if t.infra_error:
                arm.infra.append((task, str(t.exception)))
                continue
            arm.attempts.setdefault(task, []).append(
                Attempt(
                    task=task,
                    solved_raw=t.solved,
                    minutes=None if t.agent_seconds is None else t.agent_seconds / 60,
                    cost_usd=float(t.cost_usd or 0.0),
                    timeout=t.exception == "AgentTimeoutError",
                )
            )
    return arm


def median_minutes(attempts: list[Attempt]) -> float | None:
    minutes = [a.minutes for a in attempts if a.minutes is not None]
    return statistics.median(minutes) if minutes else None


def time_limit(ref_minutes: float, slack: float, ratio: float) -> float:
    return max(ref_minutes + slack, ratio * ref_minutes)


def apply_time_rule(a: Arm, b: Arm, slack: float, ratio: float) -> None:
    for this, other in ((a, b), (b, a)):
        for task, attempts in this.attempts.items():
            ref = median_minutes(other.attempts.get(task, []))
            if ref is None:
                continue
            limit = time_limit(ref, slack, ratio)
            for att in attempts:
                att.time_fail = att.solved_raw and att.minutes is not None and att.minutes > limit


def wilson(k: int, n: int, z: float = WILSON_Z) -> tuple[float, float] | None:
    if n == 0:
        return None
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (round(max(0.0, centre - half), 3), round(min(1.0, centre + half), 3))


def sign_test_p(wins: int, losses: int) -> float | None:
    """Exact two-sided binomial sign test (p = 0.5) on untied pairs."""
    n = wins + losses
    if n == 0:
        return None
    k = min(wins, losses)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / 2**n
    return round(min(1.0, 2 * tail), 4)


def bootstrap_ci(diffs: list[float]) -> tuple[float, float] | None:
    if not diffs:
        return None
    rng = random.Random(BOOTSTRAP_SEED)
    n = len(diffs)
    means = sorted(statistics.fmean(rng.choices(diffs, k=n)) for _ in range(BOOTSTRAP_RESAMPLES))
    return (
        round(means[int(0.025 * BOOTSTRAP_RESAMPLES)], 3),
        round(means[int(0.975 * BOOTSTRAP_RESAMPLES) - 1], 3),
    )


def rate(attempts: list[Attempt]) -> float:
    return sum(a.solved for a in attempts) / len(attempts)


def arm_summary(arm: Arm) -> dict:
    flat = [a for atts in arm.attempts.values() for a in atts]
    solved = sum(a.solved for a in flat)
    cost = sum(a.cost_usd for a in flat)
    minutes = [a.minutes for a in flat if a.minutes is not None]
    return {
        "arm": arm.name,
        "tasks": len(arm.attempts),
        "attempts": len(flat),
        "solved_raw": sum(a.solved_raw for a in flat),
        "time_fails": sum(a.time_fail for a in flat),
        "solved": solved,
        "solve_rate": round(solved / len(flat), 3) if flat else None,
        "wilson95": wilson(solved, len(flat)),
        "timeouts": sum(a.timeout for a in flat),
        "cost_usd": round(cost, 2),
        "cost_per_attempt": round(cost / len(flat), 2) if flat else None,
        "cost_per_solved": round(cost / solved, 2) if solved else None,
        "median_minutes": round(statistics.median(minutes), 1) if minutes else None,
        "infra_excluded": len(arm.infra),
    }


def paired(a: Arm, b: Arm, tasks: list[str]) -> dict:
    diffs = [rate(b.attempts[t]) - rate(a.attempts[t]) for t in tasks]
    b_better = sum(d > 0 for d in diffs)
    a_better = sum(d < 0 for d in diffs)
    return {
        "tasks": len(tasks),
        f"{b.name}_better": b_better,
        f"{a.name}_better": a_better,
        "ties": len(tasks) - b_better - a_better,
        "sign_test_p": sign_test_p(b_better, a_better),
        "mean_diff": round(statistics.fmean(diffs), 3) if diffs else None,
        "bootstrap95": bootstrap_ci(diffs),
    }


def compare(a: Arm, b: Arm, labels: dict[str, str], slack: float, ratio: float) -> dict:
    apply_time_rule(a, b, slack, ratio)
    common = sorted(set(a.attempts) & set(b.attempts))
    rows = []
    for task in sorted(set(a.attempts) | set(b.attempts)):
        row: dict = {"task": task, "set": labels.get(task, "")}
        for arm in (a, b):
            atts = arm.attempts.get(task, [])
            row[arm.name] = {
                "solved": sum(x.solved for x in atts),
                "solved_raw": sum(x.solved_raw for x in atts),
                "attempts": len(atts),
                "median_minutes": None
                if median_minutes(atts) is None
                else round(median_minutes(atts) or 0.0, 1),
                "mean_cost": round(statistics.fmean(x.cost_usd for x in atts), 2) if atts else None,
            }
        rows.append(row)
    by_set: dict[str, dict] = {"all": paired(a, b, common)}
    for label in sorted({labels.get(t, "") for t in common} - {""}):
        by_set[label] = paired(a, b, [t for t in common if labels.get(t) == label])
    return {
        "time_rule": {"slack_minutes": slack, "ratio": ratio},
        "arms": [arm_summary(a), arm_summary(b)],
        "paired": by_set,
        "per_task": rows,
        "infra": {a.name: a.infra, b.name: b.infra},
    }


def set_labels(path: Path | None) -> dict[str, str]:
    if path is None:
        return {}
    doc = json.loads(path.read_text())
    labels = {t: "regression" for t in doc.get("regression_set", [])}
    labels.update({t: "grading" for t in doc.get("grading_set", [])})
    return labels


def _fmt(v: object) -> str:
    return "-" if v is None else str(v)


def render_markdown(report: dict) -> str:
    a_name, b_name = (s["arm"] for s in report["arms"])
    rule = report["time_rule"]
    out = [
        f"Time rule: a solve slower than max(other arm's median + {rule['slack_minutes']:g} min, "
        f"{rule['ratio']:g} x other arm's median) counts as failed.",
        "",
        "| arm | tasks | attempts | solved (raw) | time-fails | solve rate | Wilson 95% | timeouts "
        "| $ total | $/attempt | $/solved | median min | infra excl. |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for s in report["arms"]:
        cells = [s["arm"], s["tasks"], s["attempts"], f"{s['solved']} ({s['solved_raw']})",
                 s["time_fails"], s["solve_rate"], s["wilson95"], s["timeouts"], s["cost_usd"],
                 s["cost_per_attempt"], s["cost_per_solved"], s["median_minutes"],
                 s["infra_excluded"]]  # fmt: skip
        out.append("| " + " | ".join(_fmt(c) for c in cells) + " |")
    out += [
        "",
        f"| paired over tasks | n | {b_name} better | {a_name} better | ties | sign-test p "
        f"| mean diff ({b_name} - {a_name}) | bootstrap 95% |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for label, p in report["paired"].items():
        cells = [label, p["tasks"], p[f"{b_name}_better"], p[f"{a_name}_better"], p["ties"],
                 p["sign_test_p"], p["mean_diff"], p["bootstrap95"]]  # fmt: skip
        out.append("| " + " | ".join(_fmt(c) for c in cells) + " |")
    out += [
        "",
        f"| task | set | {a_name} solved/att (raw) | {a_name} min | {a_name} $ "
        f"| {b_name} solved/att (raw) | {b_name} min | {b_name} $ |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for row in report["per_task"]:
        cells = [row["task"], row["set"] or "-"]
        for name in (a_name, b_name):
            r = row[name]
            cells += [f"{r['solved']}/{r['attempts']} ({r['solved_raw']})",
                      r["median_minutes"], r["mean_cost"]]  # fmt: skip
        out.append("| " + " | ".join(_fmt(c) for c in cells) + " |")
    infra = [
        f"{arm}: {task} {exc}" for arm, items in report["infra"].items() for task, exc in items
    ]
    if infra:
        out += ["", "Infra-excluded trials: " + "; ".join(infra)]
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("--arm", action="append", required=True, help="NAME=JOB_DIR_GLOB (twice)")
    ap.add_argument("--sets", type=Path, help="calibration sets JSON (grading/regression labels)")
    ap.add_argument("--slack-minutes", type=float, default=DEFAULT_SLACK_MINUTES)
    ap.add_argument("--ratio", type=float, default=DEFAULT_RATIO)
    ap.add_argument("--markdown", action="store_true")
    ap.add_argument("--json", type=Path, help="write the full report as JSON")
    args = ap.parse_args(argv)
    if len(args.arm) != 2 or any("=" not in spec for spec in args.arm):
        ap.error("give exactly two --arm NAME=GLOB")
    (a_name, a_glob), (b_name, b_glob) = (spec.split("=", 1) for spec in args.arm)
    a, b = load_arm(a_name, a_glob), load_arm(b_name, b_glob)
    report = compare(a, b, set_labels(args.sets), args.slack_minutes, args.ratio)
    if args.json:
        args.json.write_text(json.dumps(report, indent=2) + "\n")
    print(render_markdown(report) if args.markdown else json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
