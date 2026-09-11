"""Fixture quality tests for conflict repo builders (E-Wk9Tz3 T-Ee3Mn8).

AC-2 of T-Ee3Mn8: Each conflict fixture is tested to verify it actually produces the
intended git outcome. A fixture that silently stops conflicting would make the whole
ladder suite vacuous.

These tests are **deterministic unit tests** (no real git subprocess, everything pre-staged),
so they're fast and can be run in isolation to verify the fixtures themselves before using
them in integration tests.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from tests.isolation.conftest import (
    CONFLICT_KINDS,
    SEMANTIC_KIND,
    make_conflict_repo,
    make_rerere_taught_repo,
    semantic_verify_argv,
)


class TestConflictFixtures:
    """Verify each conflict fixture produces its intended behavior."""

    def _checkout_and_rebase_theirs_onto_ours(
        self, repo: Path, ours: str, theirs: str
    ) -> tuple[int, str, str]:
        """Helper: check out theirs and rebase onto ours, return (exit_code, stdout, stderr)."""
        import subprocess

        # Checkout theirs
        result = subprocess.run(
            ["git", "checkout", "-q", theirs],
            cwd=repo,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"Failed to checkout {theirs}: {result.stderr}"

        # Rebase onto ours
        result = subprocess.run(
            ["git", "rebase", ours],
            cwd=repo,
            capture_output=True,
            text=True,
        )
        return result.returncode, result.stdout, result.stderr

    def test_clean_conflict_fixture_no_conflict(self, tmp_path: Path) -> None:
        """clean fixture: disjoint hunks rebase without conflict."""
        repo, base_sha, ours, theirs = make_conflict_repo(tmp_path, "clean")

        exit_code, stdout, stderr = self._checkout_and_rebase_theirs_onto_ours(repo, ours, theirs)

        # clean fixture should rebase without conflict
        assert exit_code == 0, f"clean fixture failed to rebase: {stderr}"

    def test_union_conflict_fixture_produces_conflict(self, tmp_path: Path) -> None:
        """union fixture: both append to same position -> conflict."""
        repo, base_sha, ours, theirs = make_conflict_repo(tmp_path, "union")

        exit_code, stdout, stderr = self._checkout_and_rebase_theirs_onto_ours(repo, ours, theirs)

        # union should produce a conflict
        assert exit_code != 0, (
            f"union fixture should produce conflict, but rebase succeeded: {stderr}"
        )
        assert "conflict" in stderr.lower() or "CONFLICT" in stderr

    def test_true_conflict_fixture_produces_conflict(self, tmp_path: Path) -> None:
        """true_conflict fixture: same line rewritten differently -> conflict."""
        repo, base_sha, ours, theirs = make_conflict_repo(tmp_path, "true_conflict")

        exit_code, stdout, stderr = self._checkout_and_rebase_theirs_onto_ours(repo, ours, theirs)

        # true_conflict should produce a conflict
        assert exit_code != 0, f"true_conflict fixture should produce conflict: {stderr}"
        assert "conflict" in stderr.lower() or "CONFLICT" in stderr

    def test_add_add_conflict_fixture_produces_conflict(self, tmp_path: Path) -> None:
        """add_add fixture: both add same file with different content."""
        repo, base_sha, ours, theirs = make_conflict_repo(tmp_path, "add_add")

        exit_code, stdout, stderr = self._checkout_and_rebase_theirs_onto_ours(repo, ours, theirs)

        # add_add should produce a conflict
        assert exit_code != 0, f"add_add fixture should produce conflict: {stderr}"

    def test_delete_modify_conflict_fixture_produces_conflict(self, tmp_path: Path) -> None:
        """delete_modify fixture: one deletes, other modifies same file."""
        repo, base_sha, ours, theirs = make_conflict_repo(tmp_path, "delete_modify")

        exit_code, stdout, stderr = self._checkout_and_rebase_theirs_onto_ours(repo, ours, theirs)

        # delete_modify should produce a conflict
        assert exit_code != 0, f"delete_modify fixture should produce conflict: {stderr}"

    def test_binary_conflict_fixture_produces_conflict(self, tmp_path: Path) -> None:
        """binary fixture: both modify same binary file."""
        repo, base_sha, ours, theirs = make_conflict_repo(tmp_path, "binary")

        exit_code, stdout, stderr = self._checkout_and_rebase_theirs_onto_ours(repo, ours, theirs)

        # binary should produce a conflict
        assert exit_code != 0, f"binary fixture should produce conflict: {stderr}"

    def test_lock_conflict_fixture_produces_conflict(self, tmp_path: Path) -> None:
        """mechanical_lock fixture: both branches overwrite the same lockfile key
        non-additively -> a real conflict that a UNION resolver must decline (not purely
        additive), the shape a `regenerate` rule is meant to handle instead."""
        repo, base_sha, ours, theirs = make_conflict_repo(tmp_path, "lock")

        exit_code, stdout, stderr = self._checkout_and_rebase_theirs_onto_ours(repo, ours, theirs)

        assert exit_code != 0, f"lock fixture should produce conflict: {stderr}"
        assert "conflict" in stderr.lower()
        content = (repo / "lock.json").read_text()
        assert "<<<<<<<" in content and ">>>>>>>" in content

    def test_semantic_conflict_fixture_rebases_cleanly_but_verify_fails_once_landed(
        self, tmp_path: Path
    ) -> None:
        """semantic fixture: disjoint files, each branch individually clean -> no git
        conflict at all, but `semantic_verify_argv()` fails only once BOTH have landed."""
        repo, base_sha, ours, theirs = make_conflict_repo(tmp_path, "semantic")

        exit_code, stdout, stderr = self._checkout_and_rebase_theirs_onto_ours(repo, ours, theirs)
        assert exit_code == 0, f"semantic fixture must rebase cleanly: {stderr}"

        # Both changes are present after the clean rebase -- verify fails only now.
        both = subprocess.run(semantic_verify_argv(), cwd=repo, capture_output=True, text=True)
        assert both.returncode != 0, "verify should fail once BOTH changes have landed"

        # Either alone (checking out `ours` in isolation, pre-rebase): verify passes.
        subprocess.run(["git", "checkout", "-q", ours], cwd=repo, capture_output=True)
        one_side = subprocess.run(semantic_verify_argv(), cwd=repo, capture_output=True, text=True)
        assert one_side.returncode == 0, "verify should pass with only one side's change present"

    def test_rerere_taught_repo_conflicts_without_rerere_and_replays_with_it(
        self, tmp_path: Path
    ) -> None:
        """rerere_repeat fixture: the IDENTICAL conflict genuinely stops without rerere,
        and is auto-staged (though the rebase process still asks for one final,
        no-op-content `--continue`) when rerere is enabled with a pre-taught cache.

        The repo already has a populated `.git/rr-cache` (the teaching step) -- git
        enables rerere BY DEFAULT whenever that directory exists, so proving the
        "without rerere" baseline genuinely still conflicts requires an EXPLICIT
        ``-c rerere.enabled=false`` override, not just omitting the flag (which the
        shared `_checkout_and_rebase_theirs_onto_ours` helper does).
        """
        repo, base_sha, ours, theirs = make_rerere_taught_repo(tmp_path)

        # Without rerere (explicitly disabled): a genuine, unresolved conflict.
        subprocess.run(["git", "checkout", "-q", theirs], cwd=repo, capture_output=True)
        disabled = subprocess.run(
            ["git", "-c", "rerere.enabled=false", "rebase", ours],
            cwd=repo,
            capture_output=True,
            text=True,
        )
        assert disabled.returncode != 0, (
            f"rerere fixture should conflict without rerere: {disabled.stderr}"
        )
        assert "<<<<<<<" in (repo / "f.txt").read_text()
        subprocess.run(["git", "rebase", "--abort"], cwd=repo, capture_output=True)

        # With rerere enabled: the taught resolution auto-replays and stages -- no
        # unmerged path left, and the content matches the taught resolution exactly.
        subprocess.run(["git", "checkout", "-q", theirs], cwd=repo, capture_output=True)
        rerere_cfg = ["-c", "rerere.enabled=true", "-c", "rerere.autoupdate=true"]
        subprocess.run(
            ["git", *rerere_cfg, "rebase", ours], cwd=repo, capture_output=True, text=True
        )
        unmerged = subprocess.run(
            ["git", "diff", "--name-only", "--diff-filter=U"],
            cwd=repo,
            capture_output=True,
            text=True,
        ).stdout
        assert unmerged.strip() == "", "rerere should leave no unmerged path"
        assert (repo / "f.txt").read_text() == "a-resolved\nb\nc\n"


class TestConflictFixtureCoverage:
    def test_conflict_kinds_matches_the_ac2_fixture_table(self) -> None:
        """T-Ee3Mn8 AC-2 names nine kinds. `rerere_repeat` is covered by the dedicated
        `make_rerere_taught_repo` builder (its "teach then replay" shape doesn't fit
        `make_conflict_repo`'s plain two-branch-of-raw-file-writes signature); `semantic`
        is deliberately kept OUT of `CONFLICT_KINDS` itself (it produces no git conflict
        by design, and at least one existing importer,
        `tests/isolation/test_integrator.py`'s `_CONFLICTING_KINDS`, assumes every
        non-"clean" member of that tuple genuinely conflicts) -- so `CONFLICT_KINDS`
        covers the other seven, plus the separate `SEMANTIC_KIND` constant.
        """
        expected = {
            "clean",
            "union",
            "true_conflict",
            "add_add",
            "delete_modify",
            "binary",
            "lock",
        }
        assert set(CONFLICT_KINDS) == expected
        assert SEMANTIC_KIND == "semantic"
        assert SEMANTIC_KIND not in CONFLICT_KINDS


class TestConflictFixtureShapes:
    """Verify fixture metadata and structure."""

    def test_conflict_repo_returns_correct_shape(self, tmp_path: Path) -> None:
        """make_conflict_repo returns (repo, base_sha, ours_branch, theirs_branch)."""
        repo, base_sha, ours, theirs = make_conflict_repo(tmp_path, "clean")

        # Verify all pieces exist and are correct shape
        assert isinstance(repo, Path)
        assert repo.exists()
        assert isinstance(base_sha, str)
        assert len(base_sha) == 40  # SHA-1 hex
        assert isinstance(ours, str)
        assert isinstance(theirs, str)
        assert ours != "main"  # Named branches
        assert theirs != "main"
        assert ours != theirs

    def test_base_sha_is_common_ancestor(self, tmp_path: Path) -> None:
        """base_sha from fixture is the common ancestor of both branches."""
        repo, base_sha, ours, theirs = make_conflict_repo(tmp_path, "true_conflict")

        import subprocess

        # Get merge-base of ours and theirs
        result = subprocess.run(
            ["git", "merge-base", ours, theirs],
            cwd=repo,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        merge_base = result.stdout.strip()

        # Should match the provided base_sha
        assert merge_base == base_sha, f"merge-base {merge_base} != fixture base_sha {base_sha}"

    def test_main_branch_untouched_after_fixture(self, tmp_path: Path) -> None:
        """After fixture creation, main branch is still checked out and points to base."""
        repo, base_sha, ours, theirs = make_conflict_repo(tmp_path, "clean")

        import subprocess

        # Check which branch is current
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=repo,
            capture_output=True,
            text=True,
        )
        current = result.stdout.strip()
        assert current == "main", f"Expected main to be checked out, got {current}"

        # main should point to base_sha
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo,
            capture_output=True,
            text=True,
        )
        head_sha = result.stdout.strip()
        assert head_sha == base_sha, f"main HEAD {head_sha} != base_sha {base_sha}"


class TestConflictFixtureDeterminism:
    """Verify fixtures are deterministic (same output for same kind)."""

    def test_same_kind_produces_same_base_sha(self, tmp_path: Path) -> None:
        """Same fixture kind produces same base_sha."""
        repo1, base1, _, _ = make_conflict_repo(tmp_path / "run1", "clean")
        repo2, base2, _, _ = make_conflict_repo(tmp_path / "run2", "clean")

        # base_sha should be identical (deterministic fixture)
        assert base1 == base2, "Fixture should be deterministic"

    def test_conflict_repo_fixture_is_idempotent(self, tmp_path: Path) -> None:
        """Repeated calls with same input produce equivalent state."""
        repo, base_sha, ours, theirs = make_conflict_repo(tmp_path, "union")

        import subprocess

        # Count commits reachable from ours
        result1 = subprocess.run(
            ["git", "rev-list", "--count", "main..ours"],
            cwd=repo,
            capture_output=True,
            text=True,
        )
        count1 = int(result1.stdout.strip())

        # Should be exactly 1 (base + 1 commit on ours)
        assert count1 == 1, f"Expected 1 commit on ours, got {count1}"

        # Same for theirs
        result2 = subprocess.run(
            ["git", "rev-list", "--count", "main..theirs"],
            cwd=repo,
            capture_output=True,
            text=True,
        )
        count2 = int(result2.stdout.strip())
        assert count2 == 1, f"Expected 1 commit on theirs, got {count2}"
