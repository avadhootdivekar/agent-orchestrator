"""`ao-bench gen` (HLD §4.1/§7): materialise a generated suite directory.

Writes ``suite.json`` (one task per family x instance, ``source.type: generated``, grader
``blackbox``), a generic instruction file per run contract under ``tasks/_generated/`` and a
``README.md``.  Deterministic: the same arguments always produce byte-identical files, and task ids
(``<family>-<lang>-s<seed>``) are stable.  **Nothing hidden is written**: probes and the reference
implementation are regenerated from ``(family, seed, difficulty, language)`` each time a task is
materialised, into the grader-owned dir.
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

import typer

from ..errors import BenchError
from ..spec import GENERATED_LANGUAGES, KNOWN_TIERS
from .base import DIFFICULTY_AXES, FAMILY_REGISTRY, Difficulty, Family, dump_json
from .materialize import generate_instance
from .skeleton import DEFAULT_DEPS, allow_from_deps

DEFAULT_LANGUAGES = "python,typescript,go"
DEFAULT_DIFFICULTY = "entities=2,traps=2,concurrency=1,scale=1"
DEFAULT_PARITY = "../../parity/default"
DEFAULT_TIER = "medium"
DEFAULT_TIMEOUT_SECONDS = 3600
SUITE_VERSION = "1.0"
INSTRUCTION_DIR = "tasks/_generated"
LANGUAGE_SHORT = {"python": "py", "typescript": "ts", "go": "go"}
EXIT_USAGE_ERROR = 1
_SUITE_ID_RE = re.compile(r"[^a-z0-9-_]+")

_INSTRUCTION_COMMON = """\
# Task: build the service described in `SPEC.md`

This directory is an empty project skeleton. Read `SPEC.md` completely, then implement what it
describes.

- `SPEC.md` is the only specification. Every statement in it is a requirement and is checked:
  the normal cases, error responses, ordering, behaviour under concurrent use, and behaviour as
  the amount of data grows.
- The service is started with `./run.sh`; the run contract in `SPEC.md` says what it must do once
  started. Keep `run.sh` working (you may edit it, but it must still start your service).
- Use only the dependencies allowed in `SPEC.md` (the project manifest already lists them). No
  network access or external service will be available when the service is run.
- Test your work before you finish: start the service through `./run.sh` and exercise it.
- Do not modify `SPEC.md`. There is nothing to commit or hand in; leave the finished project in
  this directory.
"""

_INSTRUCTION_TAIL = {
    "http": "\nThe service is checked over HTTP, on the port in the `PORT` environment variable.\n",
    "jsonl": "\nThe service is checked through its standard input and output, one JSON object per "
    "line.\n",
}


_INSTRUCTION_OPTIMIZE = """\
# Task: build the optimisation solver described in `SPEC.md`

This directory is an empty project skeleton. Read `SPEC.md` completely, then implement what it
describes.

- `SPEC.md` is the only specification. Any feasible answer is accepted; answers are scored by
  quality (the objective in `SPEC.md`) against reference solvers of increasing strength, with no
  upper limit. Late, infeasible or malformed answers score 0.
- The solver is started with `./run.sh`; keep `run.sh` working (you may edit it, but it must still
  start your solver).
- Use only the dependencies allowed in `SPEC.md` (the project manifest already lists them). No
  network access or external service will be available when the solver is run.
- Test your work before you finish: start the solver through `./run.sh`, feed it instances of the
  stated sizes and check feasibility, objective and time.
- Do not modify `SPEC.md`. There is nothing to commit or hand in; leave the finished project in
  this directory.
"""


_INSTRUCTION_DEBUG = """\
# Task: find and fix the bugs in the service described in `SPEC.md`

This directory already contains a complete implementation of `SPEC.md` (see `run.sh` and the
`app/` package), but it has several bugs: in places it does not behave as `SPEC.md` requires.

- `SPEC.md` is the only specification. Every statement in it is a requirement and is checked:
  the normal cases, error responses, ordering and behaviour as the amount of data grows.
- Find and fix every deviation from `SPEC.md`. Fix the existing code rather than rewriting it
  from scratch; keep `./run.sh` starting the service and keep the run contract.
- Use only the dependencies allowed in `SPEC.md`. No network access or external service will be
  available when the service is run.
- Test your work before you finish: start the service through `./run.sh` and exercise it.
- Do not modify `SPEC.md`. There is nothing to commit or hand in; leave the fixed project in this
  directory.
"""


def instruction_text(contract: str, kind: str = "build") -> str:
    head = {"debug": _INSTRUCTION_DEBUG, "optimize": _INSTRUCTION_OPTIMIZE}.get(
        kind, _INSTRUCTION_COMMON
    )
    return head + _INSTRUCTION_TAIL[contract]


def instruction_relpath(contract: str, kind: str = "build") -> str:
    suffix = contract if kind == "build" else f"{kind}-{contract}"
    return f"{INSTRUCTION_DIR}/instruction-{suffix}.md"


def parse_difficulty(text: str) -> Difficulty:
    """``entities=2,traps=2,...`` -> Difficulty (missing axes keep their defaults)."""
    raw: dict[str, int] = {}
    for part in filter(None, (p.strip() for p in text.split(","))):
        key, sep, value = part.partition("=")
        if not sep or key.strip() not in DIFFICULTY_AXES:
            raise ValueError(
                f"bad --difficulty item {part!r}; expected axis=int, axes: {DIFFICULTY_AXES}"
            )
        try:
            raw[key.strip()] = int(value)
        except ValueError as exc:
            raise ValueError(
                f"bad --difficulty item {part!r}: {value!r} is not an integer"
            ) from exc
    return Difficulty.from_mapping(raw)


def _parse_languages(text: str) -> list[str]:
    langs = [x.strip() for x in text.split(",") if x.strip()]
    if not langs:
        raise ValueError("--languages must name at least one language")
    unknown = sorted(set(langs) - GENERATED_LANGUAGES)
    if unknown:
        raise ValueError(f"unknown language(s) {unknown}; known: {sorted(GENERATED_LANGUAGES)}")
    return langs


def task_id(family: str, language: str, seed: int) -> str:
    return f"{family}-{LANGUAGE_SHORT[language]}-s{seed}"


def build_suite(
    *,
    suite_id: str,
    families: list[str],
    count: int,
    languages: list[str],
    base_seed: int,
    difficulty: Difficulty,
    tier: str,
    parity: str,
    command: str,
) -> tuple[dict[str, Any], dict[str, str]]:
    """Return (suite.json body, {relpath: instruction text}).  Pure; validates every instance."""
    tasks: list[dict[str, Any]] = []
    contracts: set[tuple[str, str]] = set()
    # The sandbox reads `stack.<language>.deps` (third-party packages it installs into the image);
    # the runner does not hand the suite `stack` to graders, so every task's grader carries a copy.
    stack = {
        lang: {"deps": list(DEFAULT_DEPS[lang])}
        for lang in sorted({languages[n % len(languages)] for n in range(len(families) * count)})
    }
    k = 0
    for family in families:
        contract = FAMILY_REGISTRY[family].contract
        kind = FAMILY_REGISTRY[family].instruction_kind
        contracts.add((contract, kind))
        for i in range(count):
            language = languages[k % len(languages)]
            k += 1
            seed = base_seed + i
            deps = list(DEFAULT_DEPS[language])
            generate_instance(  # fail early
                family, seed, difficulty, language, allow_from_deps(language, deps)
            )
            tasks.append(
                {
                    "id": task_id(family, language, seed),
                    "category": "feature",
                    "instruction": instruction_relpath(contract, kind),
                    "source": {
                        "type": "generated",
                        "family": family,
                        "seed": seed,
                        "difficulty": difficulty.as_dict(),
                        "language": language,
                        "deps": deps,
                    },
                    "grader": {
                        "type": FAMILY_REGISTRY[family].grader_type,
                        "sandbox": {"stack": copy.deepcopy(stack)},
                    },
                    "family": family,
                    "language": language,
                    "difficulty": difficulty.as_dict(),
                    "tags": ["generated", family, language, contract, tier],
                }
            )
    ids = [t["id"] for t in tasks]
    if len(set(ids)) != len(ids):
        raise ValueError(f"duplicate task ids {sorted(i for i in ids if ids.count(i) > 1)}")
    suite = {
        "version": SUITE_VERSION,
        "id": suite_id,
        "domain": "software",
        "description": (
            "Generated behavioural-spec suite (Tier 1): each task is a seeded spec -> service "
            "build graded black-box by hidden probes (core / trap / perf). Generated by "
            f"`{command}`; see README.md. Probes and the reference implementation are not stored "
            "here: they are regenerated from the task's `source` at materialise time."
        ),
        "tier": tier,
        "defaults": {"timeout_seconds": DEFAULT_TIMEOUT_SECONDS},
        "parity": parity,
        "stack": stack,
        "tasks": tasks,
    }
    instructions = {instruction_relpath(c, k): instruction_text(c, k) for c, k in sorted(contracts)}
    return suite, instructions


def render_readme(suite: dict[str, Any], command: str) -> str:
    rows = [
        f"| `{t['id']}` | {t['family']} | {t['language']} | {t['source']['seed']} | "
        f"{', '.join(f'{a}={v}' for a, v in t['difficulty'].items())} |"
        for t in suite["tasks"]
    ]
    table = "\n".join(
        ["| Task | Family | Language | Seed | Difficulty |", "|---|---|---|---|---|", *rows]
    )
    return f"""# {suite["id"]}

Generated suite (Tier 1, behavioural-spec tasks) -- see
[`docs-md/benchmark-discriminating-suites-hld.md`](../../../docs-md/benchmark-discriminating-suites-hld.md)
and [`docs-md/bench-probe-dsl.md`](../../../docs-md/bench-probe-dsl.md).

Regenerate (byte-identical):

```
{command}
```

## Tasks

{table}

## How it works

- Each task's `source` (`type: generated`, family, seed, difficulty, language) is all that is
  stored. `materialize_workspace` calls the generated provider, which writes the agent-visible
  skeleton + `SPEC.md` into the run's `repo/` and the grader-owned `instance.json`, `probes.json`
  and Python `reference/` into the run's grading dir (never under the agent workspace).
- Grading is `grader.type: blackbox`: the artefact is started with `run.sh` (in the per-language
  Docker image, or the tagged `process` fallback) and driven with the hidden probes. Score =
  0.50 core + 0.35 trap + 0.15 perf; `solved` needs every core probe and score >= 0.8.
- `stack` lists the third-party dependencies per language (`stack.<language>.deps`); it is copied
  into each task's `source.deps` (skeleton manifest + `SPEC.md` allow-list) and into
  `grader.sandbox.stack` (what the sandbox installs), so all three always agree.
- Rotate seeds (`--seed`) per campaign; keep a few anchor instances fixed (HLD section 7).
"""


def _suite_id_from(out: Path) -> str:
    return _SUITE_ID_RE.sub("-", out.resolve().name.lower()).strip("-_") or "generated-suite"


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def gen_command(
    out: str = typer.Option(..., "--out", help="Suite dir to (re)generate into."),
    family: list[str] = typer.Option(
        [],
        "--family",
        help="Family to include (repeatable). Default: crud-relational and job-queue.",
    ),
    count: int = typer.Option(2, "--count", min=1, help="Instances per family."),
    languages: str = typer.Option(
        DEFAULT_LANGUAGES,
        "--languages",
        help="Comma-separated languages, assigned round-robin across the generated tasks.",
    ),
    seed: int = typer.Option(
        0, "--seed", help="Base seed; instance i of a family uses seed BASE+i."
    ),
    difficulty: str = typer.Option(
        DEFAULT_DIFFICULTY, "--difficulty", help="axis=int pairs (each 0-3) for every instance."
    ),
    tier: str = typer.Option(DEFAULT_TIER, "--tier", help="Suite tier."),
    parity: str = typer.Option(
        DEFAULT_PARITY, "--parity", help="Parity bundle path, relative to the suite dir."
    ),
    suite_id: str | None = typer.Option(
        None, "--id", help="Suite id (default: derived from the --out directory name)."
    ),
) -> None:
    """Materialise a generated suite (suite.json + instructions + README; nothing hidden)."""
    try:
        default = sorted(n for n, cls in FAMILY_REGISTRY.items() if cls.in_default_set)
        families = sorted(set(family)) if family else default
        unknown = sorted(set(families) - set(FAMILY_REGISTRY))
        if unknown:
            raise ValueError(f"unknown family {unknown}; known: {sorted(FAMILY_REGISTRY)}")
        langs = _parse_languages(languages)
        diff = parse_difficulty(difficulty)
        if tier not in KNOWN_TIERS:
            raise ValueError(f"unknown tier {tier!r}; known: {sorted(KNOWN_TIERS)}")
        out_dir = Path(out)
        sid = suite_id or _suite_id_from(out_dir)
        command = (
            f"ao-bench gen --out <suite dir> --id {sid} "
            f"{' '.join(f'--family {f}' for f in families)} "
            f"--count {count} --languages {','.join(langs)} --seed {seed} "
            f"--difficulty {','.join(f'{a}={getattr(diff, a)}' for a in DIFFICULTY_AXES)} "
            f"--tier {tier} --parity {parity}"
        )
        suite, instructions = build_suite(
            suite_id=sid,
            families=families,
            count=count,
            languages=langs,
            base_seed=seed,
            difficulty=diff,
            tier=tier,
            parity=parity,
            command=command,
        )
    except (ValueError, BenchError) as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(EXIT_USAGE_ERROR) from exc

    _write(out_dir / "suite.json", dump_json(suite))
    for rel, text in instructions.items():
        _write(out_dir / rel, text)
    _write(out_dir / "README.md", render_readme(suite, command))
    typer.echo(f"Wrote {len(suite['tasks'])} task(s) to {out_dir / 'suite.json'}")
    for t in suite["tasks"]:
        typer.echo(f"  {t['id']}")


__all__ = ["gen_command", "build_suite", "parse_difficulty", "task_id", "json", "Family"]
