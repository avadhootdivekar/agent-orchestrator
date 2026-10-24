# TASK: T-Os5Lad-bench-open-scale

## Metadata
- Task ID: `T-Os5Lad-bench-open-scale`
- Epic ID: `E-Bn7Dsc-discriminating-benchmark-suites`
- Owner: Claude (architect/developer) for avadhoot
- Created: 2026-10-24
- Status: In Progress
- Budget: remainder of the T-Pf4Mix session budget (about $150), calibration only

## Description
Owner ask (2026-10-24): pass/fail tasks saturate (T-Pf4Mix), so design benchmark systems that separate haiku from opus
much more. The targets are haiku at 25–45% and opus at 70–85%, tunable, or a pure-number scale with no upper limit.
The systems span several domains, are fully auto-gradable, and come with their own test inputs. Design:
`docs-md/benchmark-open-scale-design.md`.

## Acceptance Criteria
1. Design doc covering the scoring mechanisms (ladder, rating, staircase, items) and at least six systems across
   domains, with grader architecture, knobs, the calibration rule and anti-gaming.
2. An `objective` grader: per-case feasibility checker, ladder points (unbounded), normalised score with `par`,
   per-case detail.
3. `opt-ladder` family (tour / facility / partition), deterministic reference ladder of 5 rungs, generated cases
   hidden in `grading_dir`; refcheck: reference = 1.0, no-op = 0.
4. Unit tests (checker, ladder maths, generation determinism, grader end to end with a process sandbox).
5. Calibration with anchors haiku and opus against the pre-registered band rule (design §5), if time permits.
6. Remaining systems (P2–P4 in design §8) are tracked as follow-ons.

## Pre-registration (2026-10-24, before any spend on opt-ladder)
Design §5. Anchors are haiku and opus only, on disjoint seeds, Python, scale 1–2. Accept if haiku's mean is in
[0.25, 0.45] and opus's mean is in [0.70, 0.85]. Also accept if the anchor means differ by at least 0.25 with neither
above 0.9. Move knobs only along the declared grid (size and time first, par last), and record every round.
