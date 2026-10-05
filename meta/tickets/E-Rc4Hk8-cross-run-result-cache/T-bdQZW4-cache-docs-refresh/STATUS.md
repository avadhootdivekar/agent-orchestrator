# STATUS

- ID: `T-bdQZW4-cache-docs-refresh`
- Updated At: `2026-10-05`
- State: `Draft`
- Owner: `developer` (Dev B), `architect` sign-off

## This update
- Rev 3 ticket: depends on G2 and T-nPMuz4; adds the example workflow, the release-note line and
  the flip-point sentence; deferred features dropped. Estimate unchanged (8 h).

## Evidence
- None yet (not started). Design evidence: HLD Rev 3 and ADR-0019 Rev 3.

## Risks / Blockers
- Depends on G2 PASS and T-nPMuz4.

## Next actions
1. Start after G2 PASS and T-nPMuz4.
2. Work through HLD §25 steps 1–10 with grep-verification, then get architect sign-off.

## Comments
- By: architect · Role: architect · Date: 2026-10-04 · Comment: Status initialized (Draft).
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2 re-plan after the Phase-4
  consultation.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 3 re-plan after the
  early-gate review and the manager's scope decisions (HLD §23.5). State stays `Draft`
  (last); this file, `TASK.md`, `HANDOFF.md` (when present) and the epic `STATUS.md` rollup
  agree.
- By: developer · Role: developer · Date: 2026-10-05 · Comment: G1b carry-over for the docs refresh: (1) list sec S-3 (git `filter.<x>.clean` executed by the guard-3 probe; precondition: an in-.git write by a task agent) as a named residual in HLD 7.7 and the authoring guide, recommending --no-cache for untrusted repos; (2) document the agent-writable cache dir and the on-mode banner clause (sec S-5); (3) HLD 8.7.5 allow-list includes cache.cli (rev N-4); (4) residual rows for skip-worktree / assume-unchanged and partial-attempt baselines (sec N-7, N-9) and the orphaned restore temp files that directory hashing ignores (sec S-1). See `T-fXWbqg-cache-review-gates/STATUS.md` (G1b remediation).
By: developer · Role: developer · Date: 2026-10-05 · Comment: G2 carry-over for the docs refresh (state stays Draft). (1) ACCEPTED RESIDUAL, rev G2-S4 / G1a SEC-11: "`ANTHROPIC_BASE_URL`, `CLAUDE_CODE_USE_BEDROCK`, `CLAUDE_CODE_USE_VERTEX` and the provider region are not in the key's env allowlist, so a hit can be served across a backend or endpoint switch with an unchanged model alias; same model name, operator-controlled, non-secret; changing it would change GV-1. Mitigation: pass `--no-cache` (or run `ao cache clear`) when switching provider or endpoint." List it in HLD 7.7 and the authoring guide. (2) ACCEPTED RESIDUAL, sec G2-S2 / G1b S-3 (same family as G1a SEC-03): "`git status` in the guard-3 probe still executes a `filter.<x>.clean` command named in the agent-writable git config (re-confirmed live, git 2.39.5; `core.fsmonitor` is closed). Precondition: cache on AND the task opted in AND a writer of the git config / `.gitattributes` that cannot already run code (a tool-restricted agent without Bash); an agent with Bash already has the same power, and the same primitive pre-exists elsewhere in the code base. Mitigation: use `--no-cache` for untrusted repositories." List it in HLD 7.7 and the authoring guide with that precondition. (3) rev G2-S3: correct the HLD 24.2 merge notes: add the unlisted file `tests/ui/test_run_graph_endpoint.py` (+12/-3, adds `result_cache` to two exact key-set assertions; merge guidance: "take both sides; the key set must contain every sibling's key"); `cli.py` `status` also changes `except (json.JSONDecodeError, KeyError)` to `except (ValueError, KeyError)` and imports `cache.cli` eagerly at module level; `runstate.py` is +12/-1 (not "about 6"; it also retypes `snapshot`); `ui/files.py` was +9 before G2 and is now a few lines more (casefolded deny-list, NUL guard); the new module-level imports of `cache.constants` in `project_config.py`, `bench/subjects.py` and `ui/files.py`; the HLD 8.7.5 / 18.1 E-2 allow-list must include `cache.cli`. (4) HLD text owed by the G2 fixes: D8 (no restore-temp exemption in directory hashing; ADR-0019 D8 addendum) and D29 / SEC-15 (extended sensitive lists; ADR-0019 D29 addendum; HLD 7.7 M-14 and the constants snippet near line 1043); G0 protocol Step 9 (shadow mode stores output copies; `ao cache clear --yes` cleanup) (sec G2-N4); retention statement (outputs persist in `.orchestrator/cache` until `rm/clear/prune`; shadow also stores them); the sweep residual after `clear` (sec G2-N7); the HLD 18 baseline sentence is stale (rev G2-N8). See `T-fXWbqg-cache-review-gates/STATUS.md` (G2 remediation).
