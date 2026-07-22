"""SWE-bench Verified importer (E-Bt4Xk9 T-Sw5Hd9, FR-5, ADR-0009 D2).

Regenerates a committed ao-bench suite -- `suite.json` + `instances.json` + one
`tasks/<instance-id>/instruction.md` per instance -- from a PINNED HuggingFace dataset
revision plus a pinned, explicit instance-id list. Never reads the live/unpinned
dataset head: `revision` is always a caller-supplied HF dataset commit sha (AC5).

`datasets` (HuggingFace) is imported lazily, INSIDE `_load_rows_from_hf` only, so
`agent_orchestrator.bench.cli` (and every other `bench/` module) stays importable
without the optional `swebench` extra installed (SI-1 / NFR-1) -- see also
`swebench_provider.py`'s module docstring: that module never needs `datasets` at all,
only `git`, because every task's `source` is resolved against this module's OWN
committed `instances.json` output, not a fresh dataset fetch.

Design note -- why `source` is minimal: a task's `source` is deliberately just
`{"type": "swebench", "instance_id": ...}` rather than also embedding `repo` /
`base_commit` / `dataset` / `revision` (an earlier draft, see this task's TASK.md
pseudocode). The single pinned `instances.json` written alongside `suite.json` is the
one place that per-instance metadata lives; `SweBenchWorkspaceProvider.prepare` looks
it up there (suite-relative, `bench/workspace.py`'s existing `suite_base_dir` seam) at
run time. This avoids two copies of the same pinned metadata (`suite.json` and
`instances.json`) drifting apart, and keeps `suite.json` itself small/stable.

Determinism (AC5/AC6): instance ids are de-duplicated and SORTED before any row is
fetched or any file is written -- re-running this importer for the same
`(dataset, revision, instance_ids)` always reproduces byte-identical `suite.json` /
`instances.json` / instruction files, regardless of the input list's order. Per-
instance Docker image size is the one inherently-live piece of metadata (a registry
query, `--probe-images`, default on) -- it is NOT part of the pinned/reproducible
guarantee the way dataset content is; pass `--no-probe-images` for an offline/no-
docker regen (size left `null`).
"""

from __future__ import annotations

import json
import subprocess
from collections import Counter
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import typer

from .errors import BenchError, SpecValidationError

# ---------------------------------------------------------------------------
# Pinned production defaults -- the exact values `benchmarks/suites/swe-verified-mini`
# was generated with (ground-truth probes, epic T-Sw5Hd9). CLI options below default
# to these so re-running the importer with no overrides reproduces the committed
# suite; every value can still be overridden per invocation to build a different
# pinned subset/suite dir.
# ---------------------------------------------------------------------------
DEFAULT_DATASET = "princeton-nlp/SWE-bench_Verified"
PINNED_REVISION = "c104f840cc67f8b6eec6f759ebc8b2693d585d4a"

_SUITE_ID = "swe-verified-mini"
_SUITE_VERSION = "1.0"
_SUITE_DOMAIN = "software"
_SUITE_TIER = "large"
# Matches benchmarks/tiers.json's tiers.large.default_timeout_seconds (90 min): a
# suite.json can't reference that file directly, so this is a deliberate duplicate --
# keep in sync if the large tier's default_timeout_seconds ever changes.
_SUITE_DEFAULT_TIMEOUT_SECONDS = 5400

_SUITE_FILENAME = "suite.json"
_INSTANCES_FILENAME = "instances.json"
_TASKS_DIRNAME = "tasks"
_INSTRUCTION_FILENAME = "instruction.md"

_TASK_CATEGORY = "bugfix"  # every SWE-bench Verified instance is a real reported issue
# Grader type declared here; T-Sg6Jf2 implements + registers the actual SweBenchGrader
# (this task only needs `ao-bench validate` to accept the closed-list name -- see
# spec.py's KNOWN_GRADER_TYPES comment).
_GRADER_TYPE = "swebench"

_DOCKER_MANIFEST_TIMEOUT_SECONDS = 60

_INSTRUCTION_PREAMBLE = """\
# Task: resolve this issue in the checked-out repository

You are working inside a real, checked-out copy of an open-source project's
repository, at the exact commit where the issue below was reported.

- Make the minimal source-code change(s) needed to resolve the issue.
- After changing code, run the project's relevant tests (e.g. with `pytest`) to check
  your fix; explore the repository and its existing tests as needed.
- Do not modify test files unless the issue explicitly asks for a test change.
- Do not touch any `.grading` directory, `.git/config`, or run `git commit` /
  `git reset` / `git checkout` yourself -- your changes are graded by diffing the
  working tree against the original checkout; committing or reverting would erase
  that diff.
- Do not modify anything outside this repository.

## Issue

"""

# Fields every SWE-bench Verified row must carry (ground-truth column list).
_REQUIRED_ROW_FIELDS = (
    "repo",
    "instance_id",
    "base_commit",
    "problem_statement",
    "FAIL_TO_PASS",
    "PASS_TO_PASS",
    "environment_setup_commit",
    "difficulty",
)


class ImportResult:
    """Small return value for `import_swebench` -- avoids a pydantic model for what
    the CLI only ever reads back to print a summary."""

    def __init__(self, suite_path: Path, instances_path: Path, task_count: int) -> None:
        self.suite_path = suite_path
        self.instances_path = instances_path
        self.task_count = task_count


def _swebench_image_ref(instance_id: str) -> str:
    """SWE-bench's per-instance eval-image naming convention (verified probe, ground
    truth): `__` in the instance id becomes `_1776_` in the image tag."""
    return f"docker.io/swebench/sweb.eval.x86_64.{instance_id.replace('__', '_1776_')}:latest"


def _probe_image_size_bytes(image_ref: str, *, warn: Callable[[str], None]) -> int | None:
    """Best-effort `docker manifest inspect` layer-size sum for *image_ref*, WITHOUT
    pulling the image. Never raises: a missing `docker` binary, network hiccup, or an
    unexpected manifest shape (e.g. a multi-arch manifest list) is reported via *warn*
    and returns None -- image size is audit/selection metadata (instance selection
    bias toward smaller images), not something an import must fail over.
    """
    try:
        result = subprocess.run(  # noqa: S603 -- fixed argv, no shell, bounded timeout
            ["docker", "manifest", "inspect", image_ref],
            capture_output=True,
            text=True,
            timeout=_DOCKER_MANIFEST_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        warn(f"could not probe image size for {image_ref}: {exc}")
        return None
    if result.returncode != 0:
        warn(
            f"docker manifest inspect {image_ref} exited {result.returncode}: "
            f"{result.stderr.strip()}"
        )
        return None
    try:
        manifest = json.loads(result.stdout)
        layers = manifest["layers"]
        if not isinstance(layers, list):
            raise TypeError("'layers' is not a list")
        return sum(int(layer["size"]) for layer in layers)
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        warn(f"could not parse manifest for {image_ref} (unexpected shape): {exc}")
        return None


def _as_str_list(value: Any) -> list[str]:
    """FAIL_TO_PASS/PASS_TO_PASS come back from the HF dataset as a JSON-ENCODED
    STRING (the SWE-bench dataset's own on-disk convention), not a native list;
    normalize both that shape and an already-a-list shape (e.g. a jsonl test fixture
    that pre-parsed it) into a plain `list[str]`.
    """
    if isinstance(value, str):
        parsed = json.loads(value)
    else:
        parsed = value
    if not isinstance(parsed, list):
        raise SpecValidationError(f"Expected a list or JSON-list string, got: {value!r}")
    return [str(v) for v in parsed]


def _load_rows_from_hf(
    dataset: str, revision: str, instance_ids: Sequence[str]
) -> dict[str, dict[str, Any]]:
    """Default row loader (AC5): pull *dataset*'s "test" split at the pinned
    *revision* from HuggingFace and return the requested rows keyed by instance_id.

    Lazy import (module docstring) -- this is the ONLY function in `bench/` that ever
    imports `datasets`. Raises `BenchError` with an actionable install hint if the
    optional extra is not installed.
    """
    try:
        from datasets import load_dataset
    except ImportError as exc:
        raise BenchError(
            "The 'datasets' package is required to import from HuggingFace; install"
            " the optional extra: `uv sync --extra swebench`"
            " (or `pip install 'agent-orchestrator[swebench]'`)."
        ) from exc
    ds = load_dataset(dataset, split="test", revision=revision)
    wanted = set(instance_ids)
    by_id: dict[str, dict[str, Any]] = {}
    for row in ds:
        iid = row["instance_id"]
        if iid in wanted:
            by_id[iid] = row
    return by_id


def _build_instance_record(
    row: dict[str, Any], *, probe_image_sizes: bool, warn: Callable[[str], None]
) -> dict[str, Any]:
    """Normalize one dataset row into the pinned `instances.json` shape."""
    for field in _REQUIRED_ROW_FIELDS:
        if row.get(field) in (None, ""):
            raise SpecValidationError(
                f"Instance {row.get('instance_id', '?')!r}: missing required field {field!r}"
            )
    instance_id = row["instance_id"]
    image_ref = _swebench_image_ref(instance_id)
    size_bytes = _probe_image_size_bytes(image_ref, warn=warn) if probe_image_sizes else None
    return {
        "instance_id": instance_id,
        "repo": row["repo"],
        "base_commit": row["base_commit"],
        "environment_setup_commit": row["environment_setup_commit"],
        "difficulty": row["difficulty"],
        "image": {"name": image_ref, "size_bytes": size_bytes},
        "fail_to_pass": _as_str_list(row["FAIL_TO_PASS"]),
        "pass_to_pass": _as_str_list(row["PASS_TO_PASS"]),
        "problem_statement": row["problem_statement"],
    }


def _instruction_text(problem_statement: str) -> str:
    return _INSTRUCTION_PREAMBLE + problem_statement.rstrip() + "\n"


def _build_selection_notes(instances: list[dict[str, Any]]) -> str:
    """Auto-derived (not hand-written prose, so it can never drift from the data it
    describes): a difficulty/repo distribution summary over *instances* plus the
    determinism/regeneration contract. Every instance id/repo/difficulty is already
    listed verbatim in the `instances` array itself -- this is a human-readable
    roll-up on top, not a second source of truth (AC6).
    """
    by_difficulty = Counter(i["difficulty"] for i in instances)
    by_repo = Counter(i["repo"] for i in instances)
    difficulty_summary = ", ".join(
        f"{count}x {diff!r}" for diff, count in sorted(by_difficulty.items())
    )
    repo_summary = ", ".join(f"{repo} x{count}" for repo, count in sorted(by_repo.items()))
    return (
        f"{len(instances)} pinned instances. Difficulty mix: {difficulty_summary}. "
        f"Repos: {repo_summary}. Selection is biased toward smaller Docker images and "
        "faster targeted test runs (see each instance's image.size_bytes); dataset + "
        "revision above are pinned and committed -- regenerate via `ao-bench "
        "import-swebench --dataset <dataset> --revision <revision> --instances "
        "<same ids> --out <this dir>` for byte-identical output."
    )


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


def import_swebench(
    *,
    dataset: str,
    revision: str,
    instance_ids: Sequence[str],
    out_dir: Path | str,
    probe_image_sizes: bool = True,
    rows_loader: Callable[[str, str, Sequence[str]], dict[str, dict[str, Any]]] | None = None,
    warn: Callable[[str], None] | None = None,
) -> ImportResult:
    """Regenerate `<out_dir>/{suite.json,instances.json,tasks/<id>/instruction.md}`.

    *rows_loader* defaults to `_load_rows_from_hf` (the real HuggingFace path); tests
    inject a network-free fake here (dependency injection -- CLAUDE.md's pluggable-
    boundaries rule) so this function's own logic (id validation, determinism, file
    writing) is fully testable without the network or the `datasets` package.

    Raises `SpecValidationError` for: an empty *instance_ids*, an instance id absent
    from the loaded rows, or a row missing a required field. Raises `BenchError` if
    `datasets` is not installed (only reachable via the default `rows_loader`).
    """
    warn = warn or (lambda _msg: None)
    loader = rows_loader or _load_rows_from_hf

    unique_ids = sorted(set(instance_ids))
    if not unique_ids:
        raise SpecValidationError("import-swebench: no instance ids given")
    if len(unique_ids) != len(instance_ids):
        # Not fatal (de-duplication is safe and deterministic), but surfaced so a
        # caller notices a copy/paste mistake in their pinned id list.
        warn(
            f"import-swebench: {len(instance_ids) - len(unique_ids)} duplicate"
            " instance id(s) ignored"
        )

    rows_by_id = loader(dataset, revision, unique_ids)
    missing = [iid for iid in unique_ids if iid not in rows_by_id]
    if missing:
        raise SpecValidationError(
            f"import-swebench: instance id(s) not found in {dataset}@{revision}: {missing}"
        )

    out_path = Path(out_dir)
    instances: list[dict[str, Any]] = []
    tasks: list[dict[str, Any]] = []
    for iid in unique_ids:  # already sorted -- stable, deterministic task/instance order
        record = _build_instance_record(
            rows_by_id[iid], probe_image_sizes=probe_image_sizes, warn=warn
        )
        instances.append(record)

        instruction_rel = f"{_TASKS_DIRNAME}/{iid}/{_INSTRUCTION_FILENAME}"
        instruction_path = out_path / instruction_rel
        instruction_path.parent.mkdir(parents=True, exist_ok=True)
        instruction_path.write_text(_instruction_text(record["problem_statement"]))

        tasks.append(
            {
                "id": iid,
                "category": _TASK_CATEGORY,
                "instruction": instruction_rel,
                "source": {"type": "swebench", "instance_id": iid},
                "grader": {"type": _GRADER_TYPE},
                "tags": [record["repo"].split("/")[-1], "swebench"],
            }
        )

    suite = {
        "version": _SUITE_VERSION,
        "id": _SUITE_ID,
        "domain": _SUITE_DOMAIN,
        "description": (
            f"Pinned {len(tasks)}-instance subset of {dataset} (large tier, E-Bt4Xk9"
            " T-Sw5Hd9). Per-instance metadata (repo/base_commit/tests/image) is"
            f" committed in {_INSTANCES_FILENAME}; regenerate via `ao-bench"
            " import-swebench`."
        ),
        "tier": _SUITE_TIER,
        "defaults": {"timeout_seconds": _SUITE_DEFAULT_TIMEOUT_SECONDS},
        "tasks": tasks,
    }
    instances_doc = {
        "dataset": dataset,
        "revision": revision,
        "_notes": _build_selection_notes(instances),
        "instances": instances,
    }

    suite_path = out_path / _SUITE_FILENAME
    instances_path = out_path / _INSTANCES_FILENAME
    _write_json(suite_path, suite)
    _write_json(instances_path, instances_doc)
    return ImportResult(suite_path=suite_path, instances_path=instances_path, task_count=len(tasks))


def _parse_instance_ids(value: str) -> list[str]:
    """*value* is either a comma-separated list of instance ids, or a path to a file
    with one instance id per line (blank lines and `#`-prefixed comments ignored).
    """
    path = Path(value)
    if path.is_file():
        lines = path.read_text().splitlines()
        return [line.strip() for line in lines if line.strip() and not line.strip().startswith("#")]
    return [part.strip() for part in value.split(",") if part.strip()]


def import_swebench_command(
    dataset: str = typer.Option(DEFAULT_DATASET, "--dataset", help="HuggingFace dataset name."),
    revision: str = typer.Option(
        PINNED_REVISION,
        "--revision",
        help="Pinned HF dataset revision (a commit sha) -- never the live/unpinned head.",
    ),
    instances: str = typer.Option(
        ...,
        "--instances",
        help="Comma-separated instance ids, or a path to a file with one id per line.",
    ),
    out: str = typer.Option(
        ..., "--out", help="Output suite dir, e.g. benchmarks/suites/swe-verified-mini/."
    ),
    probe_images: bool = typer.Option(
        True,
        "--probe-images/--no-probe-images",
        help="Probe each instance's Docker image size via `docker manifest inspect`"
        " (no pull; requires network + docker). Disable for an offline/no-docker regen.",
    ),
) -> None:
    """Import a pinned SWE-bench Verified subset into an ao-bench suite (T-Sw5Hd9, FR-5).

    Regenerates `<out>/{suite.json,instances.json,tasks/*/instruction.md}`
    deterministically from a pinned `(dataset, revision)` and an explicit instance-id
    list -- re-running with the same inputs reproduces byte-identical output. Lazily
    requires the optional `swebench` extra (`uv sync --extra swebench`) for the
    `datasets` HuggingFace download; not needed for anything else in `ao-bench`.
    """
    try:
        ids = _parse_instance_ids(instances)
        result = import_swebench(
            dataset=dataset,
            revision=revision,
            instance_ids=ids,
            out_dir=Path(out),
            probe_image_sizes=probe_images,
            warn=lambda msg: typer.echo(f"WARNING: {msg}", err=True),
        )
    except (SpecValidationError, BenchError) as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(1) from exc

    typer.echo(f"Wrote {result.task_count} task(s):")
    typer.echo(f"  {result.suite_path}")
    typer.echo(f"  {result.instances_path}")
