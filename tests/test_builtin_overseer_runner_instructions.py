"""Static content checks for the built-in `overseer-runner` template's MVP instruction
files (T-5ZzAZp).

Scope: `src/agent_orchestrator/templates/builtin/overseer-runner/instructions/*.md`. These
files are workspace assets (`assets: instructions/`, `keep_existing: true` in
`template.yaml`) copied byte-for-byte at instantiation time via
`agent_orchestrator.templates._materialize_asset` -- they are never Jinja-rendered with
per-run values, so they must be path-generic (no `{{ }}` token, no hardcoded run-instance
path) and are checked here purely as static text, mirroring
`test_builtin_routed_runner_assets.py`'s own instruction-content tests
(`test_instructions_are_path_generic_with_no_template_tokens`, the `INSTRUCTIONS_DIR`
marker-phrase tests) rather than duplicating that file's broader template.yaml/workflow
rendering checks (already covered for this template by
`test_builtin_overseer_runner_assets.py`).

FR-15's `30-expander.md`/`31-sub-aggregate.md` are explicitly out of scope for this task
(T-zLHc7Q may author them later) -- this file also guards that they were not created here.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
INSTRUCTIONS_DIR = (
    _REPO_ROOT
    / "src"
    / "agent_orchestrator"
    / "templates"
    / "builtin"
    / "overseer-runner"
    / "instructions"
)

# The exact 8 MVP files this task creates (ticket T-5ZzAZp table). `30-expander.md` and
# `31-sub-aggregate.md` (FR-15) are deliberately NOT in this set.
EXPECTED_INSTRUCTION_FILES = frozenset(
    {
        "00-intake.md",
        "01-git-branch-off.md",
        "10-work-unit.md",
        "11-stabilize-unit.md",
        "20-checkpoint.md",
        "40-final-verify.md",
        "41-closeout.md",
        "90-final-push.md",
    }
)

OUT_OF_SCOPE_FR15_FILES = ("30-expander.md", "31-sub-aggregate.md")

# The exact "contract wins" clause every file must carry verbatim (whitespace-normalized
# so a file may hard-wrap it across lines without failing this check), per this task's own
# build instructions.
_CONTRACT_WINS_CLAUSE = (
    "The per-run `overseer-contract.md` is authoritative for ids, paths, and JSON "
    "shapes. Where this file and the contract disagree, the contract wins."
)

_TOKEN_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_.]+)\s*\}\}")

# AC3: no instruction may hardcode a run-instance path.
_HARDCODED_RUN_PATH = "workflows/overseer-runner/runs/"


def _normalize_whitespace(text: str) -> str:
    return " ".join(text.split())


def _read(name: str) -> str:
    return (INSTRUCTIONS_DIR / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Directory shape
# ---------------------------------------------------------------------------


def test_instructions_dir_exists() -> None:
    assert INSTRUCTIONS_DIR.is_dir()


def test_exactly_the_8_mvp_instruction_files_exist() -> None:
    on_disk = {p.name for p in INSTRUCTIONS_DIR.glob("*.md")}
    assert on_disk == EXPECTED_INSTRUCTION_FILES


@pytest.mark.parametrize("name", OUT_OF_SCOPE_FR15_FILES)
def test_fr15_expander_files_not_created_by_this_task(name: str) -> None:
    assert not (INSTRUCTIONS_DIR / name).exists()


# ---------------------------------------------------------------------------
# AC1: version header + "contract wins" clause, verbatim, in every file
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(EXPECTED_INSTRUCTION_FILES))
def test_every_file_starts_with_the_version_header(name: str) -> None:
    text = _read(name)
    first_line = text.splitlines()[0]
    assert "instructions-version: 1" in first_line, name


@pytest.mark.parametrize("name", sorted(EXPECTED_INSTRUCTION_FILES))
def test_every_file_contains_the_contract_wins_clause_verbatim(name: str) -> None:
    text = _read(name)
    assert _CONTRACT_WINS_CLAUSE in _normalize_whitespace(text), name


# ---------------------------------------------------------------------------
# AC2: content-marker tests (one stable marker phrase per ticket-table bullet)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "marker",
    ["usable_bar", "prompt_sha256", "intake-check --dry-run", "hold-request.json", "needs-input"],
)
def test_00_intake_content_markers(marker: str) -> None:
    assert marker in _read("00-intake.md")


@pytest.mark.parametrize("marker", ["exit 0", "blocked", "outputs/progress/", "needs_input"])
def test_10_work_unit_content_markers(marker: str) -> None:
    assert marker in _read("10-work-unit.md")


@pytest.mark.parametrize(
    "marker",
    [
        "digest.json",
        "every signal",
        "allowed_wave_size",
        "must_close",
        "ckpt-check --dry-run",
        "check-result.json",
        "hold-request.json",
    ],
)
def test_20_checkpoint_content_markers(marker: str) -> None:
    assert marker in _read("20-checkpoint.md")


def test_20_checkpoint_reads_digest_json_first() -> None:
    """AC2's "digest.json first" bullet, checked as a connected instruction rather than
    two independent substrings."""
    text = _read("20-checkpoint.md")
    assert "read `digest.json` first" in text.lower()


@pytest.mark.parametrize("marker", ["deferred", "how to continue"])
def test_41_closeout_content_markers(marker: str) -> None:
    assert marker in _read("41-closeout.md").lower()


# ---------------------------------------------------------------------------
# AC3: no instruction hardcodes a run-instance path
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(EXPECTED_INSTRUCTION_FILES))
def test_no_hardcoded_run_instance_path(name: str) -> None:
    assert _HARDCODED_RUN_PATH not in _read(name), name


# ---------------------------------------------------------------------------
# Path-generic: no unrendered template token (mirrors routed-runner's own convention --
# these are workspace assets, never Jinja-rendered, so a `{{ }}` token here would be a
# literal, permanent typo in every scaffolded run rather than a per-run substitution)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(EXPECTED_INSTRUCTION_FILES))
def test_no_template_tokens(name: str) -> None:
    assert _TOKEN_RE.search(_read(name)) is None, name


# ---------------------------------------------------------------------------
# Flag-name cross-check against workflow.json.tmpl's own circuit_breakers (ticket's own
# "confirm the exact pause/halt flag names" requirement) -- 90-final-push.md is the only
# instruction that references these.
# ---------------------------------------------------------------------------


def test_final_push_references_the_real_pause_flag_names() -> None:
    text = _read("90-final-push.md")
    for flag in ("control/pause.flag", "control/pause-2.flag", "control/pause-3.flag"):
        assert flag in text


def test_workflow_json_tmpl_pause_flags_match_what_final_push_references() -> None:
    """Cross-check against the actual rendered breaker paths, not just this test's own
    hardcoded expectation -- guards against the two ever silently drifting apart."""
    workflow_tmpl = (
        _REPO_ROOT
        / "src"
        / "agent_orchestrator"
        / "templates"
        / "builtin"
        / "overseer-runner"
        / "workflow.json.tmpl"
    ).read_text(encoding="utf-8")
    push_text = _read("90-final-push.md")
    for flag in ("control/pause.flag", "control/pause-2.flag", "control/pause-3.flag"):
        assert flag in workflow_tmpl
        assert flag in push_text


# ---------------------------------------------------------------------------
# Kind coverage sanity: every non-stabilize, non-expand kind from the contract's
# kind->agent/instruction map is at least named somewhere in 10-work-unit.md's own
# per-kind playbook table.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kind",
    ["research", "design", "implement", "test", "review", "fix", "document", "verify"],
)
def test_10_work_unit_covers_every_non_stabilize_kind(kind: str) -> None:
    text = _read("10-work-unit.md")
    assert f"`{kind}`" in text, kind
