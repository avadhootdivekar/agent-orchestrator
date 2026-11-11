# STATUS

- ID: `T-Hb3Adp-bench-harbor-external`
- Updated At: 2026-11-11
- State: **In Progress** (AC1-AC5 done; AC6 blocked: credentials for containers + owner decisions)

## This update
- By: avadhoot · Role: user · Date: 2026-11-11 · Comment: decisions. Spend: match the whole-run total, not ao's
  per-subtask cost ("if the ao run gets $100, the Claude run gets $100"). Timeout: the dataset's own, both arms.
  Web tools: enabled in both. Pilot: Sonnet, cc + ao, 2 Terminal-Bench 2.1 + 2 DeepSWE tasks.
- By: Claude · Role: developer · Date: 2026-11-11 · Comment: implemented the spend decision as one
  `run.sh --budget-usd` per benchmark task for both arms (plain: `claude --max-budget-usd`; ao: whole-run
  `run_budget_usd`). Install-only smoke re-run after the refactor: clean.
- By: Claude · Role: developer · Date: 2026-11-11 · Comment: harness landed on `ad/enhancements-10nov`.
  - Host: Harbor 0.25.0 installed (uv tool, Python 3.12); Docker 28.3 on /usr (114 GB free), 32 cores, 62 GB RAM,
    no usable GPU (T400 4 GB).
  - Live no-LLM checks: oracle on hello-world solved (1.0); the first try hit Harbor's 120 s verifier timeout
    during a Wi-Fi dip (apt at 243 kB/s), so README/setup use `--verifier-timeout-multiplier` for the smoke.
    `AoAgent --install-only` on hello-world: Claude Code 2.1.321 + uv + ao wheel + `ao-python`, ~4.5 min, no error.
  - Datasets downloaded and checked: TB 2.1 (89, light), DeepSWE 1.1 (113, 2 CPU / 8 GB / 90 min), SWE-Marathon
    (20; 5 need a GPU, 15 CPU tasks need 16-32 GB and 4-10 h).
  - Tests: `tests/bench/test_harbor_exec.py` 13 passed; `tests/bench` 1365 passed / 22 skipped; ruff clean; mypy
    adds no errors (benchmarks/harbor/ excluded: it runs in Harbor's venv).
  - Prompt identity verified against Harbor's own Jinja renderer (`cmp`: identical).
  - Blocked: a credential in `~/.config/ao-harbor/env` (owner), then a 1-2 task pilot per arm + `harbor-parity`.
