"""Portfolio report across suites (T-Pf4Mix AC4): one markdown view over many runs.

`build_comparison` compares subjects on ONE suite. A portfolio campaign runs several suites
(generated families, external Exercism samples, brownfield mutants), so this module pools every
loaded run and rolls the tasks up by band:

- **suite** and **area** (the task's family) and **language** -- mean score, solved/attempted, cost,
  $/solved per subject;
- **item difficulty** -- each task (that every listed subject attempted) is placed by how many
  subjects solved it, 0..N. A portfolio that discriminates has mass in the middle bands; all tasks
  in band N is saturation, all in band 0 is a floor. The bands are computed from the results, so
  they describe the instrument and are never used to choose or re-weight tasks (no fitting).

Repeats of one (suite, subject) are pooled per task (mean score, solved if solved in most repeats).
Infra errors, budget skips and audit-flagged tasks are not attempts (`results.is_attempted`). The
cheap-and-quick "suspect" heuristic is NOT applied: a cheap model failing fast is a real failure.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from .results import is_attempted, load_run
from .runner import BenchTaskRecord


@dataclass
class TaskOutcome:
    """One (suite, task, subject) pooled over repeats."""

    suite: str
    task_id: str
    family: str
    language: str
    score: float
    solved: bool
    cost: float
    wall: float


@dataclass
class Cell:
    tasks: int = 0
    solved: int = 0
    score_sum: float = 0.0
    cost: float = 0.0
    wall: float = 0.0

    def add(self, o: TaskOutcome) -> None:
        self.tasks += 1
        self.solved += o.solved
        self.score_sum += o.score
        self.cost += o.cost
        self.wall += o.wall

    def render(self) -> str:
        if not self.tasks:
            return "—"
        per_solved = f"${self.cost / self.solved:.2f}" if self.solved else "n/a"
        return (f"{self.score_sum / self.tasks:.3f} · {self.solved}/{self.tasks} · "
                f"${self.cost:.2f} · {per_solved}/solved")  # fmt: skip


def _pool(suite: str, recs: list[BenchTaskRecord]) -> TaskOutcome | None:
    attempted = [r for r in recs if is_attempted(r)]
    if not attempted:
        return None
    first = attempted[0]
    return TaskOutcome(
        suite=suite,
        task_id=first.task_id,
        family=first.family or "unknown",
        language=first.language or "unknown",
        score=sum(r.score for r in attempted) / len(attempted),
        solved=2 * sum(r.solved for r in attempted) > len(attempted),
        cost=sum(r.cost_usd or 0.0 for r in attempted) / len(attempted),
        wall=sum(r.wall_clock_seconds or 0.0 for r in attempted) / len(attempted),
    )


def load_outcomes(run_dirs: Iterable[str | Path]) -> dict[str, dict[tuple[str, str], TaskOutcome]]:
    """subject id -> (suite, task id) -> outcome, pooled over every run dir given."""
    grouped: dict[str, dict[tuple[str, str], list[BenchTaskRecord]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for run_dir in run_dirs:
        run = load_run(run_dir)
        for t in run.tasks:
            grouped[run.subject.id][(run.suite_id, t.task_id)].append(t)
    out: dict[str, dict[tuple[str, str], TaskOutcome]] = {}
    for subject, tasks in grouped.items():
        pooled = {key: _pool(key[0], recs) for key, recs in tasks.items()}
        out[subject] = {k: v for k, v in pooled.items() if v is not None}
    return out


def _table(title: str, label: str, keys: list[str], subjects: list[str],
           cells: dict[str, dict[str, Cell]]) -> list[str]:  # fmt: skip
    lines = [f"## {title}", "", "Cell: mean score · solved/attempted · cost · $/solved.", ""]
    lines.append(f"| {label} | " + " | ".join(subjects) + " |")
    lines.append("|---|" + "---|" * len(subjects))
    for key in keys:
        lines.append(f"| {key} | " + " | ".join(cells[key][s].render() for s in subjects) + " |")
    return [*lines, ""]


def render_portfolio(outcomes: dict[str, dict[tuple[str, str], TaskOutcome]],
                     subjects: list[str] | None = None) -> str:  # fmt: skip
    subjects = subjects or sorted(outcomes)
    by: dict[str, dict[str, dict[str, Cell]]] = {
        dim: defaultdict(lambda: defaultdict(Cell)) for dim in ("suite", "area", "language")
    }
    overall: dict[str, Cell] = defaultdict(Cell)
    for s in subjects:
        for o in outcomes.get(s, {}).values():
            overall[s].add(o)
            by["suite"][o.suite][s].add(o)
            by["area"][o.family][s].add(o)
            by["language"][o.language][s].add(o)

    common = set.intersection(*(set(outcomes.get(s, {})) for s in subjects)) if subjects else set()
    bands: dict[int, list[tuple[str, str]]] = defaultdict(list)
    for key in sorted(common):
        bands[sum(outcomes[s][key].solved for s in subjects)].append(key)

    lines = [
        "# Portfolio report",
        "",
        f"Subjects: {', '.join(subjects)}. Tasks pooled over repeats; infra errors, budget skips "
        "and audit-flagged tasks are not attempts.",
        "",
    ]
    lines += _table("Overall", "subject", ["all"], subjects, {"all": overall})
    for dim, title in (("suite", "Per suite"), ("area", "Per area (family)"),
                       ("language", "Per language")):  # fmt: skip
        lines += _table(title, dim, sorted(by[dim]), subjects, by[dim])
    n = len(subjects)
    lines += [
        "## Item difficulty (tasks every subject attempted)",
        "",
        f"Band k = solved by k of the {n} subjects. Mass in the middle bands = discrimination; all "
        f"in band {n} = saturation; all in band 0 = floor.",
        "",
        "| band (solved by) | tasks | share | per suite |",
        "|---|---|---|---|",
    ]
    total = max(1, len(common))
    for k in range(n, -1, -1):
        keys = bands.get(k, [])
        per_suite: dict[str, int] = defaultdict(int)
        for suite, _task in keys:
            per_suite[suite] += 1
        suites = ", ".join(f"{s}: {c}" for s, c in sorted(per_suite.items())) or "—"
        lines.append(f"| {k}/{n} | {len(keys)} | {len(keys) / total:.0%} | {suites} |")
    lines += ["", "### Tasks that separate subjects (bands 1..N-1)", ""]
    for k in range(n - 1, 0, -1):
        for suite, task in bands.get(k, []):
            who = [s for s in subjects if outcomes[s][(suite, task)].solved]
            lines.append(f"- `{suite}/{task}` — solved by {', '.join(who)}")
    if not any(bands.get(k) for k in range(1, n)):
        lines.append("- none")
    return "\n".join(lines) + "\n"


def portfolio_command_impl(run_dirs: list[str], subjects: list[str], out: str | None) -> str:
    text = render_portfolio(load_outcomes(run_dirs), subjects or None)
    if out:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(text)
    return text


__all__ = ["TaskOutcome", "load_outcomes", "portfolio_command_impl", "render_portfolio"]
