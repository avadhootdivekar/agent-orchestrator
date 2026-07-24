"""Token estimator — pre-run estimate for budget gating (T-n7hmwj, FR-4, NFR-1, NFR-6, NFR-7).

The estimator uses file SIZES only (os.stat via ArtifactStore.size), never file contents.
This is NFR-1 safe: no artifact payload is read.

Known limitation: byte size != char count for multi-byte UTF-8 sequences, which inflates
the estimate conservatively (acceptable — pessimism is intentional).
Directory inputs: os.stat on a directory returns the directory entry size, not recursive
content size. MVP uses top-level stat only (OPEN_QUESTION: recursive sizing).
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod

from .artifacts import ArtifactStore
from .models import EstimatorConfig, TaskContext


class TokenEstimator(ABC):
    """Pluggable pre-run token estimator (NFR-6 — injected ABC)."""

    @abstractmethod
    def estimate(self, ctx: TaskContext, cfg: EstimatorConfig) -> int:
        """Return a pessimistic token estimate for the task described by *ctx*.

        The estimate is used by BudgetManager.gate() to decide whether to admit
        the task. After the task runs, the engine reconciles against actuals.
        """
        ...


class HeuristicTokenEstimator(TokenEstimator):
    """Default chars/4 heuristic estimator.

    Algorithm (NFR-1 safe — file sizes only):
      input_bytes = sum of sizes of instruction_path + general_instruction_paths
                    + all input_paths + dynamic_input_paths
      raw = (input_bytes / chars_per_token) + output_allowance_tokens
      estimate = ceil(raw * pessimism_buffer)

    General instructions are counted because the agent genuinely reads them on every task
    (E-Ui7Kq2 FR-GI1) — omitting them would under-estimate every task in a workspace that
    configures them, letting the budget gate admit work it cannot actually afford.

    All tuning knobs come from EstimatorConfig (NFR-7 — no magic literals here).
    """

    def __init__(self, store: ArtifactStore) -> None:
        self._store = store

    def estimate(self, ctx: TaskContext, cfg: EstimatorConfig) -> int:
        """Return ceil((bytes/chars_per_token + output_allowance) * pessimism_buffer)."""
        input_bytes = self._store.size(ctx.instruction_path)
        for p in (
            *ctx.general_instruction_paths,
            *ctx.input_paths,
            *ctx.dynamic_input_paths,
        ):
            input_bytes += self._store.size(p)  # os.stat, 0 if missing — NFR-1 safe

        raw = (input_bytes / cfg.chars_per_token) + cfg.output_allowance_tokens
        return math.ceil(raw * cfg.pessimism_buffer)
