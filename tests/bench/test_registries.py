"""Unit tests for bench/registries.py: empty registries + register helpers."""

from __future__ import annotations

import pytest

from agent_orchestrator.bench import registries


class _DummySubject:
    pass


class _DummyGrader:
    pass


def test_grader_registry_populated_on_import() -> None:
    # T-Grd7Vx: importing bench.graders registers every MVP grader type.
    import agent_orchestrator.bench.graders  # noqa: F401

    assert {"pytest", "command", "file_assertion", "fake"} <= set(registries.GRADER_REGISTRY)


def test_register_subject_adds_entry() -> None:
    registries.register_subject("dummy_subject", _DummySubject)
    try:
        assert registries.SUBJECT_REGISTRY["dummy_subject"] is _DummySubject
    finally:
        del registries.SUBJECT_REGISTRY["dummy_subject"]


def test_register_subject_duplicate_raises() -> None:
    registries.register_subject("dummy_subject_dup", _DummySubject)
    try:
        with pytest.raises(ValueError, match="already registered"):
            registries.register_subject("dummy_subject_dup", _DummySubject)
    finally:
        del registries.SUBJECT_REGISTRY["dummy_subject_dup"]


def test_register_grader_adds_entry() -> None:
    registries.register_grader("dummy_grader", _DummyGrader)
    try:
        assert registries.GRADER_REGISTRY["dummy_grader"] is _DummyGrader
    finally:
        del registries.GRADER_REGISTRY["dummy_grader"]


def test_register_grader_duplicate_raises() -> None:
    registries.register_grader("dummy_grader_dup", _DummyGrader)
    try:
        with pytest.raises(ValueError, match="already registered"):
            registries.register_grader("dummy_grader_dup", _DummyGrader)
    finally:
        del registries.GRADER_REGISTRY["dummy_grader_dup"]
