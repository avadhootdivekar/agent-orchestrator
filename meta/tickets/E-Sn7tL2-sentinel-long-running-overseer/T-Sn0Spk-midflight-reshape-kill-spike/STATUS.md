# STATUS

- ID: `T-Sn0Spk-midflight-reshape-kill-spike`
- Updated At: `2026-11-09`
- State: `Done`
- Owner: `developer (claude-sonnet-5-5)`

## This update
- By: claude-opus-5-5 · Role: architect · Date: 2026-11-09 · Comment: Task created from epic breakdown (owner-approved design).

- By: claude-sonnet-5-5 · Role: developer · Date: 2026-11-09 · Comment: Spike complete; recommendation **go-with-changes**.
  Scripts + raw outputs under `output/E-Sn7tL2-sentinel-long-running-overseer/spike/`; ACs 1–4 met (see RESULT.md).

## Evidence
- `output/E-Sn7tL2-sentinel-long-running-overseer/spike/RESULT.md` (a)–(d) with commands, numbers, code refs.
- (a) 7 scenarios: 0 double dispatch; 6 invariants (I1 persistence of edits, I2 inferred edges, I3 duplicate producers, I4 non-pending
  edits, I5 cycle-on-copy, I6 cones/loops + run() locals); addendum shows `depends_on`-only trim leaves inferred edge.
- (b) killpg verified (grandchild dies in ~1 ms; SIGKILL rung for TERM-ignoring tree); real claude: 27 s zero file growth during a
  30 s tool call; 675 healthy attempts / 34 196 gaps: p99 78 s, p99.9 580 s, max 600.4 s ⇒ `idle_kill_minutes` 15 (floor 12).
- (c) tick 0.35 ms, reshape 1.9 ms, `RunState.save` 10.3 ms @1k tasks (831 KB).
- (d) SIGTERM: instant death, `state=running`, orphans. SIGINT not conclusively measured.
- Gates: spike scripts `ruff check`/`ruff format --check` clean (own `ruff.toml`); no `src/` change, so product `pytest`/`mypy`
  unaffected by this task (another unit's uncommitted edits exist in the working tree; not run/claimed here).

## Risks / Blockers
- (d) SIGINT behaviour not measured under a pty; (a) used an in-place-swap hook rather than a real `run()` change.

## Next actions
1. T-Sn1Adr: fold the spike spec changes (FR-5/6/8/10/12, NFR-8) into ADR-0024/HLD.
2. T-Sn6Brr: implement I1–I6; T-Sn3Tck/T-Sn4Rec: signal handling + `killpg` ladder.
