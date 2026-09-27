# TASK: T-zLHc7Q-nested-expander-subdag

## Metadata
- Task ID: `T-zLHc7Q-nested-expander-subdag`
- Epic ID: `E-YAAGhk-overseer-runner-template`
- Owner: developer
- Created: 2026-09-26
- Last Updated: 2026-09-26
- Status: Draft (MVP-Should, below the sprint cut line)
- Estimate: 2 days (16 h)

## Requirements Mapping
- Requirement IDs: FR-15
- Design: `docs-md/overseer-runner-hld.md` §13.3 (Expander), §8.4 M3 (expander-check), §1.2

## Description
Enable depth-2 sub-DAGs. A wave unit of kind `expand` has agent `architect`, instruction
`30-expander.md`, `emit_tasks: true`, `task_manifest_path` `outputs/manifests/<unit-id>.json`, and
`post_hook: ov-expander-check` (`fail_task`). It emits:

- up to `sub_wave_size` leaves `<unit-id>--NN-<slug>`. These use the unit shape and never have
  `emit_tasks`.
- exactly one `<unit-id>--done`: agent `manager`, instruction `31-sub-aggregate.md`, `depends_on`
  all leaves, outputs `waves/wJJ/<unit-id>--done.md` and `progress/<unit-id>--done.json`.

The next checkpoint's `inputs` pre-declare `progress/<unit-id>--done.json`. This creates an inferred
edge once the expander injects, the same way routed-runner's aggregator connects to `full-test`.

Deliverables:
- the `expander-check` subcommand
- the expander rules in `ckpt-check`: OV-R5, and OV-R10 inputs ⊇ `--done` breadcrumbs
- the `30-expander.md` and `31-sub-aggregate.md` instructions
- the contract section (already stubbed by T-ltBLUY)
- ingestion of leaves (ids containing `--`) in `wave_units`
- flipping the documented recommendation so `max_expanders_per_wave=1` is safe. The default stays
  `0` unless dev-epic decides otherwise, and records it.

## Acceptance Criteria
1. `expander-check` pass and fail fixtures:
   - a leaf with `emit_tasks` fails (depth cap)
   - a missing `--done` fails
   - two `--done` entries fail
   - a leaf `depends_on` outside {expander} ∪ manifest fails
   - leaves > `sub_wave_size` fail
   - a wrong `--done` output path fails
2. `ckpt-check`: an expander count > `max_expanders_per_wave` fails with OV-R5. A next `ck-*` that
   lacks the `--done` breadcrumb input fails with OV-R10.
3. E2E (harness variant, `max_expanders_per_wave=1`): wave 1 has one expander that emits 2 leaves.
   `ck-01` dispatches only after `<unit>--done` succeeds, which the executor call order shows. The
   ledger contains the leaves and `--done` lines.
4. The engine barrier and inferred-edge assumption is verified by that e2e and is not re-derived.
   The CFG-3 formula accounts for expanders (a test with `exp=1` needs 147).
5. A `reviewer`-agent review is recorded in STATUS.md.

## Risks
- The inferred-edge timing between expander settle and checkpoint readiness. Mitigation: AC 3
  asserts the order from the real engine.

## Dependencies
- T-HPJcc6, T-tAKBBB, T-WruPiv (harness).

## Pseudocode / Algorithm
```text
See design §13.3 "Expander (FR-15)". wave_units(K) includes ids matching ^w{K:02d}-\d{2}-[a-z0-9-]+(--(\d{2}-[a-z0-9-]+|done))?$
```

## Schemas / Interface Notes
- The same brief and breadcrumb schemas. The brief's `kind: expand` has a `goal` describing the sub-problem.

## Handoff Boundary
- Upstream: the checkers and the harness.
- Downstream: T-gbccdr.

## Artifacts
- Tests appended to the checker and e2e suites.
