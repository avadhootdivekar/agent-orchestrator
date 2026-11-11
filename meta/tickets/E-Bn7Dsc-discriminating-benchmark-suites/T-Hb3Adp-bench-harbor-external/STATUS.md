# STATUS

- ID: `T-Hb3Adp-bench-harbor-external`
- Updated At: 2026-11-11
- State: **In Progress** (AC1-AC5 done; AC6 pilot running: plain arm verified, ao arm fixes in test)

## This update
- By: Claude · Role: developer · Date: 2026-11-11 · Comment: first LLM pilot (host-login auth).
  - Oracle 5/5 PASS (TB 2.1 dna-assembly, make-mips-interpreter, regex-chess; DeepSWE sqlfmt, koota).
  - Plain Sonnet 5.5 on `dna-assembly`: reward 1.0, $0.25, 1.9 min. Per-job secrets files deleted after each job.
  - ao arm defects found and fixed: (1) `ao-bench` needs `benchmarks/schemas/`, which the wheel does not package
    -> uploaded to the installed package's expected path; (2) ao confines repos to the workspace root, and `/app`
    sat outside `/tmp/ao-harbor-ws` (`Path escapes workspace root: /app`; `/` cannot be a root either) -> the
    task dir is moved into `/ao-ws/repo` with a symlink at its path, restored before grading.
  - Owner ask: ao arm in local workspace-only mode, no git changes -> shared prompt line (both arms),
    `init_git: false`, explicit `branch_policy` for overseer's git-branch-off (a deterministic no-git mode is
    E-Rr5Lnc `T-Ng1Wsp`).
  - `run.sh` builds the ao wheel from a clean `git archive` of `AO_WHEEL_REF` (default HEAD) so a live ao run
    committing in this checkout never leaks into the ao under test.
  - Direction change: TB 2.1 near saturated for Sonnet 5.5 / Opus 5.5; DeepSWE calibration continues in
    `T-Dw5Cal-deepswe-calibration-and-comparison`.
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
