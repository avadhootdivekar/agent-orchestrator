# STATUS

- ID: `E-Bt4Xk9-complex-benchmark-tiers`
- Updated At: 2026-07-22
- State: **Draft** (design complete; delivery not started)
- Owner: architect agent (design) → developer/tester agents (delivery)

## This update
- By: Claude · Role: manager · Date: 2026-07-22 · Comment: **OQ-1..OQ-4 resolved by orchestrator**
  (user's budget envelope covers them; decisions surfaced in the final report for veto).
  OQ-1: medium caps $150/run, $50/subject **accepted** — sits proportionally between the
  user-specified small ($10) and large ($800/$100) figures. OQ-2: large run **N=10 ×
  {claude-sonnet, claude-opus, ao-epic-sonnet}** accepted as the baseline matrix (the user's
  literal ask); `ao-epic-plus-sonnet` joins the large tier only if medium shows it earns its
  overhead and budget/wall-clock allow. OQ-3: **host-checkout agent + Docker-for-grading-only
  accepted** (standard predictions-style SWE-bench eval; agent-in-container is a documented
  follow-on). OQ-4: **run-host outbound network confirmed by direct probes today** (HF dataset
  CDN reachable, Docker Hub manifests + a real 2.58 GB image pull OK, gold-patch
  `run_evaluation` on `sympy__sympy-20154` returned resolved=1/1 in 18 s). Wave A started.
- Epic designed and decomposed: 11 tasks (≤3d each), dependency-ordered into 5 parallel-safe waves with disjoint file-ownership per wave.
- ADR-0009 drafted (Proposed): tier model, SWE-bench Verified subset (vs Aider-polyglot), USD budget enforcement semantics, parallel bench runner.
- PLAN run matrix appended to EPIC.md: medium (~$75) + bounded large (~$155) ≈ **$230 expected**, hard-ceilinged well under the $800/$100 envelope.

## Evidence
- Existing harness read end-to-end (`runner.py`, `spec.py`, `subjects.py`, `graders.py`, `workspace.py`, `metrics.py`, `cli.py`, schemas, `dev-core` suite, subject configs).
- Environment feasibility (Docker 28.3.0, ~37 GB free disk, `swebench`/dataset reachable) taken as ground truth from the orchestrator's probing.

## Risks / Blockers
- R1 disk (37 GB) on the large tier; R2 SWE-bench Docker flakiness; R3 budget×parallelism correctness; R5 optional-dep leakage. See EPIC.md.
- Open questions OQ-1..OQ-4 (medium caps, large N/subject set, host-checkout grading, run-host network) need user/orchestrator confirmation before the PLAN run executes; design/delivery of the framework is not blocked on them.

## Next actions
1. Start Wave A: `T-Tr1Km8` (tier model) + `T-Ep8Lq6` (ao-epic-plus subject) in parallel.
2. Confirm OQ-1..OQ-4 with the user before executing the PLAN run matrix.
