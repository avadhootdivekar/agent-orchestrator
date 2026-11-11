"""Stratified random sample of DeepSWE v1.1 tasks for calibration (T-Dw5Cal Phase 1c).

Reads the task definitions downloaded with `harbor download datacurve/deep-swe-1-1 -o DIR`
and draws N tasks proportionally across languages (at least one per language), with a fixed
seed so the sample is reproducible. Tasks already attempted are excluded.

    python3 sample_deepswe.py --tasks-dir /var/tmp/ao-harbor/datasets --n 12 \
        --seed 20261111 --exclude sqlfmt-create-table-ddl-formatting --out tasks.txt
"""

from __future__ import annotations

import argparse
import random
import tomllib
from collections import defaultdict
from pathlib import Path


def load_tasks(tasks_dir: Path) -> dict[str, dict[str, str]]:
    """task_id -> metadata for every DeepSWE task.toml under *tasks_dir* (any depth <= 3)."""
    tasks: dict[str, dict[str, str]] = {}
    for toml_path in sorted(tasks_dir.glob("**/task.toml")):
        if len(toml_path.relative_to(tasks_dir).parts) > 4:
            continue
        meta = tomllib.loads(toml_path.read_text()).get("metadata", {})
        task_id = meta.get("task_id")
        if task_id and meta.get("language"):
            tasks[task_id] = {
                "language": meta["language"],
                "category": meta.get("category", ""),
                "repo": meta.get("repository_url", "").removeprefix("https://github.com/"),
            }
    return tasks


def allocate(counts: dict[str, int], n: int) -> dict[str, int]:
    """Largest-remainder proportional allocation of *n* over strata, at least 1 each."""
    total = sum(counts.values())
    if n < len(counts):
        raise ValueError(f"n={n} is smaller than the number of strata ({len(counts)})")
    quotas = {k: n * c / total for k, c in counts.items()}
    alloc = {k: max(1, int(q)) for k, q in quotas.items()}
    by_remainder = sorted(counts, key=lambda k: (-(quotas[k] - int(quotas[k])), k))
    i = 0
    while sum(alloc.values()) < n:
        key = by_remainder[i % len(by_remainder)]
        if alloc[key] < counts[key]:
            alloc[key] += 1
        i += 1
    while sum(alloc.values()) > n:  # only when the 1-per-stratum floor overshoots
        key = max((k for k in alloc if alloc[k] > 1), key=lambda k: alloc[k] - quotas[k])
        alloc[key] -= 1
    return alloc


def sample(tasks: dict[str, dict[str, str]], n: int, seed: int, exclude: set[str]) -> list[str]:
    strata: dict[str, list[str]] = defaultdict(list)
    for task_id, meta in sorted(tasks.items()):
        if task_id not in exclude:
            strata[meta["language"]].append(task_id)
    alloc = allocate({k: len(v) for k, v in strata.items()}, n)
    rng = random.Random(seed)
    picked: list[str] = []
    for language in sorted(strata):
        picked += sorted(rng.sample(strata[language], alloc[language]))
    return picked


def main() -> None:
    ap = argparse.ArgumentParser(description="Stratified random sample of DeepSWE tasks.")
    ap.add_argument("--tasks-dir", type=Path, required=True)
    ap.add_argument("--n", type=int, required=True)
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--exclude", action="append", default=[])
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    tasks = load_tasks(args.tasks_dir)
    picked = sample(tasks, args.n, args.seed, set(args.exclude))
    lines = [
        f"# DeepSWE 1.1 calibration sample: n={args.n} seed={args.seed} "
        f"stratified by language, excluding {sorted(args.exclude)}",
        *(f"{t}  # {tasks[t]['language']} {tasks[t]['repo']}" for t in picked),
    ]
    args.out.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
