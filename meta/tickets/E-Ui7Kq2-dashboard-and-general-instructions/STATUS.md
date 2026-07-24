# STATUS

- ID: `E-Ui7Kq2-dashboard-and-general-instructions`
- Updated At: `2026-07-24`
- State: `Done`
- Owner: `Avadhoot Divekar`

## This update

All 7 tasks delivered on branch `ad/ui-dashboard` (branched from `main` @ `0d60580`).

**Shipped**
- `ao ui` — browser dashboard: file/code browsing (hidden + binary included), run control
  (start-from-prompt / resume / cancel / delete), workspace-wide and per-run statistics,
  per-task table, captured CLI log, and a workspace view of effective general instructions.
- **General instructions** — declared once per workspace (`.ao/config.yaml`,
  `AO_GENERAL_INSTRUCTIONS`, `--general-instruction`, or a workflow's own
  `general_instructions`), applied to every task of every run. All layers are **additive**,
  a deliberate documented exception to ADR-0003 (ADR-0010 D1).
- **`ao run --prompt` / `--prompt-file`** plus `WorkflowSpec.prompt_path` — the per-run input
  mechanism behind the dashboard's prompt box.
- `meta/ROADMAP.md`, HLD, ADR-0010, README section, CI frontend job, Makefile `ui*` targets.

**Two defects found and fixed by the new tests (not pre-existing):**
1. Exited child processes became zombies, so `os.kill(pid, 0)` reported them alive forever —
   cancel and liveness were both wrong. Fixed by retaining `Popen` and reaping via `poll()`.
2. The SPA catch-all swallowed unknown `/api/*` paths, returning HTML 200 instead of a JSON
   404. Fixed by excluding the API prefix from the fallback.

**Two pre-existing lint errors on `main`** (`tests/test_e2e_cli.py`: E501 + F841) were fixed
here — `make lint` was red on `main`, which would have blocked this branch's CI.

## Evidence

| Gate | Result |
|---|---|
| Full suite | **1528 passed**, 7 deselected (was 1267 on `main`) — no regressions |
| Dashboard tests | 216 passed (`tests/ui/`) |
| General instructions | 25 passed (`tests/test_general_instructions.py`) |
| Prompt + instruction e2e | 20 passed (`tests/test_e2e_cli_prompt_and_instructions.py`) |
| Frontend unit tests | 25 passed (`ui/`, vitest) |
| Coverage | **95%** (baseline on `main`: 94%) — improved |
| `ruff check .` | All checks passed |
| `ruff format --check .` | 209 files already formatted |
| `mypy src` | Clean for all new code; only the 4 pre-existing `_version.py` errors remain |
| `npx tsc -b --noEmit` | Clean |
| `npm audit` | 0 vulnerabilities |
| Frontend build | Succeeds → `src/agent_orchestrator/ui/static/` |
| E2E (live server) | Real uvicorn + real HTTP + real `ao run` child: prompt → run → stats → delete |

## Risks / Blockers

- **No authentication** — deliberate for this release per the requirement. Loopback-bind
  default + startup warning + allow-listed subprocess options + path guard are the whole
  mitigation. Top roadmap item (§3.1).
- **Cancel only covers dashboard-launched runs** — a run started from another terminal has no
  PID this dashboard owns. Roadmap §3.3.
- **Run-id attribution by directory diff** — concurrent launches in the same instant could
  in principle be mis-attributed. Roadmap §4.
- **`mypy .`** (whole-repo, as opposed to `mypy src`) is still red on a pre-existing
  duplicate-`conftest` collision under `benchmarks/`. Untouched; CI runs `mypy src`.

## Next actions
1. Authentication + configurable secrets (roadmap §3.1) — gates any non-localhost use.
2. UI-based dynamic workflow generation (explicitly deferred here; roadmap §3.3).
3. Live streaming updates in place of polling (roadmap §3.3).

## Comments

- By: `Claude` · Role: `developer` · Date: `2026-07-24` · Comment: Requirements 1 and 2 were
  initially conflated in planning; the user clarified that the UI prompt box is a per-run
  input (like a `--prompt` argument) while general instructions are a separate
  workspace-level mechanism. The implementation keeps them fully distinct — see ADR-0010 D3.
