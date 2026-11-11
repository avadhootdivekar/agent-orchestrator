#!/usr/bin/env python3
"""Summarize and compare Harbor jobs (one job per arm), stdlib only.

    python3 benchmarks/harbor/summarize.py JOB_DIR [JOB_DIR ...] [--markdown] [--json]

Per job: trials, attempted (infra errors excluded), solved (reward >= 1), mean reward,
total and per-trial cost, $/solved, median agent minutes, exceptions. With two or more
jobs: a per-task matrix and, for each pair, the tasks both attempted with who solved them
(discordant counts are what separate two arms; with few of them, no claim is safe).

Exception policy: an agent timeout is a legitimate attempt (the arm ran out of time,
reward 0). Any other trial exception (image build, verifier timeout, API/auth errors,
agent harness crash) is infra and excluded from rates, listed separately. Read the
excluded trials before trusting a comparison.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

ATTEMPT_EXCEPTIONS = frozenset({"AgentTimeoutError"})
SOLVED_REWARD = 1.0


@dataclass
class Trial:
    task: str
    reward: float | None
    exception: str | None
    cost_usd: float | None
    input_tokens: int | None
    output_tokens: int | None
    agent_seconds: float | None

    @property
    def infra_error(self) -> bool:
        return self.exception is not None and self.exception not in ATTEMPT_EXCEPTIONS

    @property
    def solved(self) -> bool:
        return not self.infra_error and (self.reward or 0.0) >= SOLVED_REWARD


@dataclass
class JobSummary:
    job: str
    agent: str
    model: str
    trials: int
    attempted: int
    solved: int
    mean_reward: float | None
    cost_usd: float
    cost_per_trial: float | None
    cost_per_solved: float | None
    median_agent_minutes: float | None
    infra_errors: dict[str, int] = field(default_factory=dict)


def _seconds(phase: dict | None) -> float | None:
    if not phase or not phase.get("started_at") or not phase.get("finished_at"):
        return None
    start = datetime.fromisoformat(phase["started_at"].replace("Z", "+00:00"))
    end = datetime.fromisoformat(phase["finished_at"].replace("Z", "+00:00"))
    return (end - start).total_seconds()


def _reward(verifier_result: dict | None) -> float | None:
    rewards = (verifier_result or {}).get("rewards") or {}
    if "reward" in rewards:
        return float(rewards["reward"])
    return float(statistics.mean(rewards.values())) if rewards else None


def load_trials(job_dir: Path) -> tuple[list[Trial], str, str]:
    trials: list[Trial] = []
    agent = model = "?"
    for result_path in sorted(job_dir.glob("*/result.json")):
        data = json.loads(result_path.read_text())
        info = data.get("agent_info") or {}
        agent = info.get("name") or agent
        model = ((info.get("model_info") or {}).get("name")) or model
        agent_result = data.get("agent_result") or {}
        exc = data.get("exception_info") or None
        trials.append(
            Trial(
                task=data.get("task_name") or result_path.parent.name,
                reward=_reward(data.get("verifier_result")),
                exception=(exc or {}).get("exception_type") if exc else None,
                cost_usd=agent_result.get("cost_usd"),
                input_tokens=agent_result.get("n_input_tokens"),
                output_tokens=agent_result.get("n_output_tokens"),
                agent_seconds=_seconds(data.get("agent_execution")),
            )
        )
    return trials, agent, model


def summarize(job_dir: Path) -> tuple[JobSummary, list[Trial]]:
    trials, agent, model = load_trials(job_dir)
    attempted = [t for t in trials if not t.infra_error]
    solved = [t for t in attempted if t.solved]
    costs = [t.cost_usd for t in trials if t.cost_usd is not None]
    total_cost = float(sum(costs))
    minutes = [t.agent_seconds / 60 for t in attempted if t.agent_seconds is not None]
    errors: dict[str, int] = {}
    for t in trials:
        if t.infra_error:
            errors[str(t.exception)] = errors.get(str(t.exception), 0) + 1
    rewards = [t.reward or 0.0 for t in attempted]
    return (
        JobSummary(
            job=job_dir.name,
            agent=agent,
            model=model,
            trials=len(trials),
            attempted=len(attempted),
            solved=len(solved),
            mean_reward=round(statistics.mean(rewards), 3) if rewards else None,
            cost_usd=round(total_cost, 2),
            cost_per_trial=round(total_cost / len(costs), 2) if costs else None,
            cost_per_solved=round(total_cost / len(solved), 2) if solved and costs else None,
            median_agent_minutes=round(statistics.median(minutes), 1) if minutes else None,
            infra_errors=errors,
        ),
        trials,
    )


def _cell(trial: Trial | None) -> str:
    if trial is None:
        return "-"
    if trial.infra_error:
        return "ERR"
    return "PASS" if trial.solved else f"{trial.reward or 0:.2f}"


def render(summaries: list[tuple[JobSummary, list[Trial]]], markdown: bool) -> str:
    sep = " | "
    lines: list[str] = []
    head = ["job", "agent", "model", "trials", "attempted", "solved", "mean", "cost $",
            "$/trial", "$/solved", "med min", "infra errors"]  # fmt: skip
    lines.append(sep.join(head))
    if markdown:
        lines.insert(0, "")
        lines[-1] = "| " + lines[-1] + " |"
        lines.append("|" + "---|" * len(head))
    for s, _ in summaries:
        row = [s.job, s.agent, s.model, s.trials, s.attempted, s.solved, s.mean_reward,
               s.cost_usd, s.cost_per_trial, s.cost_per_solved, s.median_agent_minutes,
               ", ".join(f"{k}:{v}" for k, v in s.infra_errors.items()) or "-"]  # fmt: skip
        text = sep.join("-" if v is None else str(v) for v in row)
        lines.append(f"| {text} |" if markdown else text)
    if len(summaries) < 2:
        return "\n".join(lines)

    by_job = [{t.task: t for t in trials} for _, trials in summaries]
    tasks = sorted(set().union(*by_job))
    lines += ["", "per task: " + sep.join(s.job for s, _ in summaries)]
    for task in tasks:
        lines.append(f"{task}: " + sep.join(_cell(m.get(task)) for m in by_job))
    lines += ["", "pairs (tasks both attempted): both / A only / B only / neither"]
    for i in range(len(summaries)):
        for j in range(i + 1, len(summaries)):
            a, b = by_job[i], by_job[j]
            common = [t for t in tasks if t in a and t in b
                      and not a[t].infra_error and not b[t].infra_error]  # fmt: skip
            both = sum(a[t].solved and b[t].solved for t in common)
            a_only = sum(a[t].solved and not b[t].solved for t in common)
            b_only = sum(b[t].solved and not a[t].solved for t in common)
            lines.append(
                f"{summaries[i][0].job} vs {summaries[j][0].job}: n={len(common)} "
                f"{both} / {a_only} / {b_only} / {len(common) - both - a_only - b_only}"
            )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("jobs", nargs="+", type=Path, help="Harbor job dirs")
    parser.add_argument("--markdown", action="store_true")
    parser.add_argument("--json", action="store_true", help="per-job summaries as JSON")
    args = parser.parse_args(argv)
    missing = [str(j) for j in args.jobs if not j.is_dir()]
    if missing:
        print(f"not a job dir: {missing}", file=sys.stderr)
        return 2
    summaries = [summarize(j) for j in args.jobs]
    if args.json:
        print(json.dumps([asdict(s) for s, _ in summaries], indent=2))
    else:
        print(render(summaries, args.markdown))
    return 0


if __name__ == "__main__":
    sys.exit(main())
