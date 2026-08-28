# STATUS

- ID: `T-Tw4fpl-finplan-wiring`
- Updated At: `2026-07-24`
- State: `Done`
- Owner: `claude`

## This update
- By: claude · Role: developer · Date: 2026-07-24
- Comment: Implemented per TASK.md / HLD §2.9, all changes in the sibling
  `ao-runner-finplan` workspace repo (nothing touched under `agent-orchestrator/` besides
  these ticket docs, per the task boundary):
  - `workflows/epic-runner/template/` — `template.yaml` (name `epic-runner`, `instance_dir:
    workflows/epic-runner/runs/{id}` UNCHANGED, `type` param only, `repo_set`/budgets
    hardcoded as workspace policy per the task's explicit instruction, no `assets:` since
    instructions already live in place), `workflow.json.tmpl`, `prompt.md.tmpl`,
    `breakdown-contract.md.tmpl` — all four derived from and cross-checked against the
    concurrently-shipped `builtin/routed-runner` template and the original
    `new-epic-run.sh` generator.
  - `.ao/config.yaml` — added a `templates:` entry registering the template dir, with a
    short comment (older `ao` ignores the key per HLD §2.1).
  - `new-epic-run.sh` — slimmed 729 → 295 lines: kept all UX (arg parsing, epic-id/slug
    resolution, `--resume`/`--extend-*` passthrough, `--run`/`--auto`/`--validate-only`
    confirm flows, `prompt_is_template` guard), replaced the embedded scaffolding
    Python + mkdirs + prompt heredoc with a delegation to `ao new epic-runner "$EPIC_ID"
    [--param type=...] [--prompt-file ...]`, added a `require_template_support` probe
    (mirrors `require_routing_support`'s stale-ao message) run before scaffolding.
  - `README.md` — updated the prerequisite note (template support alongside routing),
    added a "Template registration & direct `ao new` use" section (direct `ao new
    epic-runner ...` usage, `ao templates` listing, dashboard "New run → From template"
    flow), updated the Layout section (`template/` dir, `.ao/config.yaml`'s `templates:`
    key).

## Deviations from the literal task text (flagged explicitly)
1. **`.ao/config.yaml` path**: the task text suggested `templates:\n  - workflows/epic-
   runner/template`, but `project_config.py`'s `load_project_config` anchors relative
   `templates:` entries to **the config file's own directory** (`.ao/`), matching how
   this same file's pre-existing `agents: ../specs/agents.json` / `reposets: ../specs/
   reposets.json` entries already use a `../` prefix. The literal suggested path would
   have resolved to the non-existent `.ao/workflows/epic-runner/template`. Used
   `../workflows/epic-runner/template` instead — confirmed correct empirically (`ao
   templates` discovers `epic-runner [workspace]`; without the `../` fix it discovers
   nothing). Documented inline in `.ao/config.yaml`'s new comment.
2. **`workflow.json.tmpl` "name" field**: kept literally as `"routed-runner: {{ id }}"`
   (not renamed to "epic-runner: ...") to stay byte-equivalent to what the ORIGINAL
   `new-epic-run.sh` script actually wrote (`f"routed-runner: {epic_id}"` — apparently a
   pre-existing naming artifact in the original script, predating this generalization
   epic). Flagging in case the mismatched literal name was actually meant to be fixed
   here; happy to rename if that's preferred — would then no longer be a zero-delta
   scaffolder swap.

## Evidence (all commands run with `AO_BIN=/usr/avadhoot/mounted/agent-orchestrator/.venv/bin/ao`, from `ao-runner-finplan/`)
- `ao templates` → lists both `epic-runner [workspace]` (this task) and `routed-runner
  [builtin]` (T-Tb3rtr) side by side (different `name:`s, neither shadows the other).
- `./workflows/epic-runner/new-epic-run.sh tmpl-smoke-test --validate-only` → scaffolded
  `e-ow3mgp-tmpl-smoke-test`, `ao validate` → `OK: all specs valid`.
- **workflow.json diff**: replayed the exact pre-edit `new-epic-run.sh` Python generator
  (captured verbatim before editing) for the same id, parsed both JSON outputs, and
  diffed as data (raw text differs only in `json.dump(indent=2)`'s multi-line-array
  style vs. the hand-authored compact-array style inherited from `builtin/routed-runner`
  — cosmetic, not semantic). Result: **the only structural delta is the added top-level
  `prompt_path` field** — same task ids/agents/instruction paths/`depends_on`/timeouts,
  same 5 routes, same all 11 circuit breakers (thresholds and `mode: recommend`
  identical, `repo_set: "fin-plan"`, `task-budget-cap: 75.0`, `run-budget-cap: 1500.0`).
- **breakdown-contract.md**: rendered `breakdown-contract.md.tmpl` for the same id and
  diffed as text against the original script's f-string contract — **exact byte match**.
- **prompt.md**: rendered `prompt.md.tmpl` and diffed as text against the original
  script's heredoc — **exact byte match** (title `# {{ id }}`, FinPlan context section
  intact, `<describe the feature or change>` placeholder intact for
  `prompt_is_template()`).
- **Idempotent re-scaffold**: edited `prompt.md` (simulating a user edit), re-ran the
  script on the same id — output showed `skipped (1, already present):
  .../prompt.md`, md5 unchanged before/after, edit intact; `workflow.json`/`breakdown-
  contract.md` regenerated (both in `created`).
- **`--type bug`**: wrote `outputs/forced-type.txt` containing `bug`, `route -> forced:
  bug`. Re-scaffolding the same id **without** `--type` deleted `forced-type.txt`
  (correctly un-pins a stale forced route, matching the original script + HLD §2.2's
  documented `when:` delete-on-unset semantics) and printed `route -> auto`.
- **`require_template_support` probe**: pointed `AO_BIN` at a fake `ao` whose `templates`
  subcommand exits nonzero → script failed loudly with the install.sh --yes remedy text,
  exit code 1, before attempting to scaffold.
- Cleanup: `rm -rf workflows/epic-runner/runs/e-ow3mgp-tmpl-smoke-test` — confirmed the
  pre-existing 14 real run directories are untouched; no other throwaway artifacts left
  (temp files under `/tmp` only).
- `bash -n workflows/epic-runner/new-epic-run.sh` → syntax OK.
- No tokens spent; no real run started.
- `pytest`/`ruff`/`mypy` in `agent-orchestrator`: N/A — no code changed in that repo
  (ticket docs only, per the task's explicit boundary). `ao-runner-finplan` has no
  Python/JS test suite at the workspace-runner level (bash + config only); the CLI/bash
  smoke tests above are the test evidence for this task, matching how the pre-existing
  `new-epic-run.sh` itself was validated.

## Risks / Blockers
- None outstanding. The two flagged deviations above are judgment calls made to keep
  the deliverable actually working / byte-equivalent; neither changes behavior for
  existing runs (layout is unchanged, `.ao/config.yaml`'s `agents:`/`reposets:` anchoring
  convention is followed exactly).
- Global `ao` snapshot staleness (epic-level risk, restated here): this workspace's
  scripts now depend on `ao new`/`ao templates` existing, which is a NEWER surface than
  routing/breakers — `install.sh --yes` must be re-run after `E-Tpl3x9` merges to `main`,
  same as the pre-existing routing-support staleness risk. `require_template_support`
  guards against silently mis-scaffolding on a stale binary.

## Next actions
1. None for this task. Downstream: T-Te5rev's cross-cutting e2e review can reference
   this task's evidence for the finplan-side half of the epic.
