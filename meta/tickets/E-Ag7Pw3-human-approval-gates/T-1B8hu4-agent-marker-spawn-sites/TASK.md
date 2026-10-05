# TASK: T-1B8hu4-agent-marker-spawn-sites

## Metadata
- Task ID: `T-1B8hu4-agent-marker-spawn-sites`
- Epic ID: `E-Ag7Pw3-human-approval-gates`
- Owner: developer
- Created: 2026-10-04
- Last Updated: 2026-10-05
- Status: Draft
- Estimate: 1.5 days (rev 1: 1 d; Gate 1 added `GitRepo.version`/`probe`, the "marker last" rule with an
  overlay test per site, and the regenerate-resolver test)

## Requirements Mapping
- Requirement IDs: FR-13, NFR-1, NFR-7
- Design: HLD §9.11 (decision table: 8 edit sites covering 10 spawn points), §7.4 TM-2, §26 rows 7, 22–26
  (incl. 25b); Gate 1 S-10, suggestion S-03

## Description
Export `AO_IN_AGENT=1` into the environment of every agent-influenced child process, always as the
**last** assignment to the child environment (after any `ctx.env`, `env_overlay`, `isolation.env` or
`extra_env` merge, so an overlay carrying `AO_IN_AGENT: "0"` cannot clear it), and never into the engine's
own `os.environ`.

1. New `src/agent_orchestrator/approvals/marker.py` (imports only `os`/`collections.abc`):
   `AO_IN_AGENT_ENV`, `AO_IN_AGENT_VALUE`, `agent_child_env(base=None) -> dict[str, str]`,
   `with_marker(env) -> env`, `in_agent_context(environ=os.environ) -> bool` ("" and "0" mean absent).
2. Mark the 8 edit sites of HLD §9.11 rows 1–8: `executors/claude_cli.py` Popen
   (`env=agent_child_env(ctx.env)`), `hooks.py::_run_hook_inner` env dict, `isolation/integrator.py::
   Integrator._base_env` (covers verify and the regenerate resolver), `isolation/git.py::GitRepo._run`
   (marker set **after** `env.update(extra_env)`, not via `_FORCED_ENV`), `isolation/git.py::GitRepo.version`
   and `GitRepo.probe` (`env=agent_child_env()` at the three runner calls, Gate 1 S-10),
   `spec.py::_git_rev_parse` env, `bench/subjects.py` child env, `bench/graders.py` child env.
3. Do **not** mark rows 9–12 (`bench` fixed-argv tooling, `ui/processes.py`, `service/supervisor.py`,
   `service/systemd.py`, `_version.py`); record the decision in code comments only where it adds clarity.

Files — new: `approvals/marker.py`, `tests/approvals/test_marker_spawn_sites.py`. Shared: the 8 edit sites
above (1–3 lines each + import). **Exclusive files during stage B** (HLD §22.3): these files; `spec.py` only
after `T-AGO2L6` has merged.

## Acceptance Criteria
1. For each of the 10 spawn points a test spies on the subprocess call (monkeypatched `subprocess.Popen`/
   `run` or the module's injectable runner) and asserts `env["AO_IN_AGENT"] == "1"` in the child env,
   including `test_git_version_and_probe_marked` and `test_regenerate_resolver_child_marked`.
2. `test_overlay_cannot_clear_marker[<site>]`: wherever a site accepts an overlay (`ctx.env`, hook env,
   integrator env, `extra_env`), an overlay containing `AO_IN_AGENT: "0"` still yields `"1"` in the child.
3. `ClaudeCliExecutor`: the child env still contains every parent variable and the `ctx.env` overlay (the
   existing `tests/isolation/test_security_guards.py::TestPlantedSecretPassThrough` passes unedited).
4. After an `Orchestrator.run()` of a fixture workflow that dispatches a `claude_cli`-shaped spy, a hook and
   a git call, `"AO_IN_AGENT" not in os.environ` (`test_engine_process_env_unchanged`).
5. `ui/processes.py` launches (spy) carry no `AO_IN_AGENT` added by `ao` (inherited only if the dashboard
   itself had it).
6. `in_agent_context`: `{}`, `{"AO_IN_AGENT": ""}`, `{"AO_IN_AGENT": "0"}` → False; `"1"`, `"true"` → True.
7. No pre-existing test edited; ruff/mypy clean on changed modules; targeted tests green (full suite at the
   stage-B checkpoint).
8. A `reviewer`-agent review is recorded in STATUS.md.

## Risks
- Changing `claude_cli` from `env=None` to an explicit env: behaviour is identical except the marker
  (inherited env == `os.environ`); AC3 guards the security-relevant variables.
- `GitRepo.version`/`probe` get an explicit env for the first time; only the marker is added (no
  `_FORCED_ENV` change of behaviour).

## Dependencies
- `T-AGO2L6` complete and merged (the `approvals/` package skeleton; `spec.py` is shared).

## Pseudocode / Algorithm
```text
def agent_child_env(base=None): env = dict(os.environ); env.update(base or {}); env[AO_IN_AGENT_ENV] = "1"; return env
def with_marker(env): env[AO_IN_AGENT_ENV] = AO_IN_AGENT_VALUE; return env      # always the LAST assignment
```

## Schemas / Interface Notes
- Env var: `AO_IN_AGENT=1` (HLD §9.11).

## Verification
```
WT=/usr/avadhoot/mounted/agent-orchestrator/.claude/worktrees/agent-a513b4d78ac3b20bb
PY=/usr/avadhoot/mounted/agent-orchestrator/.venv/bin/python
cd $WT && $PY -m pytest -o pythonpath=src -p no:cacheprovider -q tests/approvals/test_marker_spawn_sites.py tests/isolation tests/test_executor.py tests/test_hooks.py
cd $WT && $PY -m ruff check src/agent_orchestrator tests/approvals && $PY -m mypy src/agent_orchestrator/approvals/marker.py src/agent_orchestrator/executors/claude_cli.py src/agent_orchestrator/hooks.py src/agent_orchestrator/isolation/integrator.py src/agent_orchestrator/isolation/git.py src/agent_orchestrator/spec.py
```

## Handoff Boundary
- Upstream: HLD §9.11.
- Downstream: `T-nmL0HP` (CLI refusal uses `in_agent_context`), `T-l43hCg` (dashboard refusal),
  `T-pdLR96` (adversarial TM-2).

## Artifacts
- Code as listed; evidence in STATUS.md.
