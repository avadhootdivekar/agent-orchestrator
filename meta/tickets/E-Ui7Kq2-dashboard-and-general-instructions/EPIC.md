# EPIC: E-Ui7Kq2-dashboard-and-general-instructions

## Metadata
- Epic ID: `E-Ui7Kq2-dashboard-and-general-instructions`
- Title: `Browser dashboard for AO + workspace-scoped general instructions`
- Owner: `Avadhoot Divekar`
- Created: `2026-07-24`
- Last Updated: `2026-07-24`
- Status: `Done`

## Summary
- **Goal:** Give AO a browser-based dashboard (file/code browsing, run control, run
  statistics) and add support for general instructions that apply to every task regardless
  of how a run is invoked.
- **Scope In:** `ao ui` command; FastAPI backend + React/Vite frontend; directory & code
  browsing incl. hidden/binary files; new run from a typed prompt; resume / cancel / browse /
  delete runs; aggregate + per-run statistics; `ao run --prompt` / `--prompt-file`;
  workspace-scoped general instructions via config/env/CLI/workflow; roadmap file.
- **Scope Out (deferred, recorded on the roadmap):** UI-based dynamic workflow generation
  (explicitly noted by the user as nice-to-have, not now); authentication / login /
  configurable secrets (explicitly left open for now); in-browser file editing; live
  streaming updates.

## Requirements

### Dashboard — browsing
- **FR-B1:** Browse directories under the workspace, showing **all** entries including
  hidden dotfiles and binary files.
- **FR-B2:** View file contents; binary files are identified and not inlined.
- **FR-B3:** Requests must not escape the configured roots (traversal or symlink).
- **FR-B4:** Reads are bounded so a huge file cannot exhaust server memory.

### Dashboard — run control & stats
- **FR-R1:** Start a new run from a prompt typed into a text field in the browser.
- **FR-R2:** Resume and cancel existing runs.
- **FR-R3:** Browse and inspect old runs.
- **FR-R4:** Delete old runs.
- **FR-R5.1:** Workspace-wide statistics — how many runs, plus totals.
- **FR-R5.2:** Per-run statistics — task count, cost, wall time, actual (execution) time,
  tokens, and outputs.
- **FR-R6 (deferred):** UI-based dynamic workflow generation — roadmap only.

### General instructions
- **FR-GI1:** Instruction files declared **once per workspace** are applied to **every task
  of every run**, including when `ao run` is invoked with no related flag.
- **FR-GI2:** Declarable via `.ao/config.yaml`, `AO_GENERAL_INSTRUCTIONS`,
  `--general-instruction`, and a workflow's own `general_instructions`.

### Run prompt
- **FR-P1:** `ao run --prompt` / `--prompt-file` writes the text into the workflow's declared
  `prompt_path` before the run starts (the mechanism behind FR-R1).

### Non-functional
- **NFR-1:** Context hygiene preserved — the executor boundary passes **paths only**, never
  file contents.
- **NFR-2:** Core dependency set unchanged; web deps live behind an optional `[ui]` extra.
- **NFR-3:** No new persisted state for statistics — everything derived from `state.json`.
- **NFR-4:** No regressions; coverage maintained or improved.
- **NFR-5:** Unauthenticated by design this release → loopback bind default + explicit
  warning when binding elsewhere.

## Task List
- [x] `T-Gi4Mn8-general-instructions` — Workspace-scoped general instructions end to end
- [x] `T-Pr7Wt3-run-prompt` — `ao run --prompt` / `--prompt-file` + `prompt_path` in the spec
- [x] `T-Db2Hs5-dashboard-backend` — Service layer, file browser, run repository, supervisor
- [x] `T-Ap9Kf1-dashboard-api` — FastAPI adapter + `ao ui` command
- [x] `T-Fe6Rv4-dashboard-frontend` — React/Vite frontend built into the package
- [x] `T-Ts8Nc2-test-suites` — Unit / integration / e2e across Python and frontend
- [x] `T-Rm5Jd7-roadmap-and-docs` — `meta/ROADMAP.md`, HLD, ADR-0010, README, CI, Makefile

## Risks and Dependencies
- **Unauthenticated surface.** The dashboard can read files and spend money. Mitigated by
  loopback default + warning + allow-listed options + path guard; real fix is roadmap §3.1.
- **Run-id attribution.** The engine owns `run_id`, so the dashboard infers it by diffing the
  runs directory. Bounded by excluding claimed ids; a `--run-id` flag on `ao run` would
  remove the guesswork (roadmap).
- **Node build step.** The frontend requires node to rebuild. Mitigated by committing the
  built output so `pip install` needs no node; CI rebuilds to prove reproducibility.
- **Precedence exception.** General instructions deliberately break ADR-0003's precedence
  chain (union, not override). Documented in ADR-0010 D1.

## Links
- Design doc: [`docs-md/dashboard-and-general-instructions-hld.md`](../../../docs-md/dashboard-and-general-instructions-hld.md)
- ADR: [`docs-md/adr/ADR-0010-dashboard-architecture-and-general-instructions.md`](../../../docs-md/adr/ADR-0010-dashboard-architecture-and-general-instructions.md)
- Roadmap: [`meta/ROADMAP.md`](../../ROADMAP.md)
- Status rollup: [`STATUS.md`](STATUS.md)
