# TASK: T-Hb3Adp-bench-harbor-external

## Metadata
- Task ID: `T-Hb3Adp-bench-harbor-external`
- Epic ID: `E-Bn7Dsc-discriminating-benchmark-suites`
- Owner: Claude (developer) for avadhoot
- Created: 2026-11-11
- Last Updated: 2026-11-11
- Status: In Progress (harness landed; LLM pilot blocked on credentials + owner decisions)
- Estimate: 1-2 days (harness), pilot ~$40

## Description
Owner ask (2026-11-11): our own instruments saturate (`T-Cb8Rk2`, `T-Pf4Mix`), so benchmark plain Sonnet/Opus
against `ao` (overseer-runner) on Sonnet/Opus with existing third-party agentic-coding benchmarks, run locally and
near-autonomously, ~$200-400 per arm, ~$1000 total. Harbor (the Terminal-Bench harness) runs the registry's
datasets (Terminal-Bench 2.1, DeepSWE 1.1, SWE-Marathon, SWE-bench Pro, ...) in per-task containers with the
tasks' own verifiers; it ships a claude-code agent and loads custom agents by import path.

Owner requirements (2026-11-11): use overseer-runner first; the score difference must not come from
instructions or settings: same net input (task prompt, CLAUDE.md, skills, agent/command files, memories) and the
same flags/settings (thinking effort, autocompact, ...) for both arms; decide which must stay identical and which
differ by design, ask where unclear.

## Acceptance Criteria
1. Host setup script + README (user guide for Claude and devs) in the repo: `benchmarks/harbor/setup.sh`
   (pinned Harbor, credentials file, work root, `--check`, `--smoke`), `README.md`.
2. Harbor agents `CcAgent` / `AoAgent` (`benchmarks/harbor/ao_harbor/agent.py`); the ao arm installs the wheel
   built from the checkout and runs an ao_workflow subject in the task container (`ao-bench exec-subject`),
   reporting ao's own cost/tokens to Harbor.
3. Input parity: byte-identical task prompt for both arms (`ao-bench harbor-prompt`), shared parity env and
   shared user-level Claude config, model pinned inside ao (`allowed_models`, `default_unit_model`), explicit
   identical effort; `ao-bench harbor-parity` compares the arms' session init events. README §3 lists
   identical vs different-by-design.
4. `run.sh` (one arm per call, same task list for every arm) and `summarize.py` (per-job + pairwise).
5. Fake-tier tests; no regression in `tests/bench`; live no-LLM checks (oracle smoke, `--install-only`).
6. LLM pilot (1-2 tasks per arm) with `harbor-parity` clean before any larger budget; owner reviews first.

## Notes
- `AoWorkflowSubject` gains `RunContext.ao_launcher` (default unchanged: `uv run ao` from the checkout).
- The overseer's `init_git` commits the task repo and adds a local bare origin (outside the repo); declared
  asymmetry, as in `T-Ov3Sbj`.
