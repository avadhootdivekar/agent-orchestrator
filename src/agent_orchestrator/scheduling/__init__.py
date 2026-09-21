"""Soft overlap-aware co-scheduling (E-Wk9Tz3 FR-10, HLD §9.1, §11 M8).

`overlap.py` is pure (no git, no filesystem, no clock — importable and testable without
git, per M8's deliberately narrow dependency on `models.py` only). The hotspot signal
that feeds `rank_wave`'s `hotspots` argument lives in `isolation/hotspots.py` instead
(it needs git + file IO, so it stays out of this package to keep this one pure).
"""

from __future__ import annotations

from .overlap import (
    BASELINE_OVERLAP_WEIGHT,
    glob_intersection,
    overlap_score,
    rank_wave,
)

__all__ = [
    "BASELINE_OVERLAP_WEIGHT",
    "glob_intersection",
    "overlap_score",
    "rank_wave",
]
