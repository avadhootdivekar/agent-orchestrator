# External benchmarks via Harbor (`benchmarks/harbor/`)

Run third-party agentic-coding benchmarks (Terminal-Bench 2.1, DeepSWE, SWE-Marathon, SWE-bench
Pro and the rest of the [Harbor registry](https://hub.harborframework.com/datasets)) on this host,
comparing **plain Claude Code** with **`ao` (overseer-runner) driving the same Claude Code**, on
the same model, with the same net input. Harbor runs each task in its own Docker container and
grades it with the task's own verifier, so scores are comparable to public leaderboards
(same tasks and verifiers; our harness, not theirs).

Ticket: `meta/tickets/E-Bn7Dsc-discriminating-benchmark-suites/T-Hb3Adp-bench-harbor-external/`.

| File | What |
|---|---|
| `setup.sh` | One-time host install + config (Harbor CLI, credentials file, work root); `--check`, `--smoke` |
| `run.sh` | Run one arm (`cc` or `ao`) on one model over a dataset / task list |
| `summarize.py` | Per-job table (solved, mean reward, $, $/solved, minutes, infra errors) + per-task / pairwise comparison |
| `ao_harbor/agent.py` | Harbor agents: `CcAgent` (plain arm) and `AoAgent` (ao arm), both with the parity layer |
| `parity/env` | Agent env applied to every arm |
| `parity/claude/` | User-level Claude config copied into every arm's fresh `CLAUDE_CONFIG_DIR` |
| `../subjects/ao-overseer-harbor.json` | The ao arm's subject: overseer-runner + its wrapper prompt (`ao-overseer/prompt-harbor.md`) |

Product-side seams (in `src/agent_orchestrator/bench/harbor_exec.py`, all `ao-bench` commands):
`exec-subject` (run an ao_workflow subject on an existing repo dir; what the ao arm runs inside
the container), `harbor-prompt` (the plain arm's prompt template), `harbor-parity` (compare what
each arm's sessions loaded).

## 1. Setup (once per host)

```bash
bash benchmarks/harbor/setup.sh          # installs harbor==0.25.0 (uv tool), creates config + dirs
bash benchmarks/harbor/setup.sh --check  # READY?
bash benchmarks/harbor/setup.sh --smoke  # optional: no-LLM oracle run of hello-world
```

Credentials (`run.sh --auth`):
- `host` (default when the file below holds no credential): the host's own Claude login. Only its
  current **access token** goes to the containers (`CLAUDE_CODE_OAUTH_TOKEN` in a per-job 0600 env
  file under `$AO_HARBOR_ROOT/secrets/`, deleted when the job ends), never the refresh token, so
  a container can never rotate it and log the host out. The token lasts ~8 h; `run.sh` refuses
  to start when it has under `--min-token-hours` (default 3) left. Any `claude` command on the
  host refreshes it.
- `file`: `~/.config/ao-harbor/env` with `CLAUDE_CODE_OAUTH_TOKEN=...` (`claude setup-token`,
  long-lived) or `ANTHROPIC_API_KEY=...`.

Needs Docker (user in the `docker` group), `uv`, `git`, `claude`. Work root `/var/tmp/ao-harbor`
(`AO_HARBOR_ROOT`): `jobs/` (results), `wheels/` (ao wheels per commit), `prompts/`. It is outside
any checkout so no `CLAUDE.md`/`.claude/` sits above a job. Setup never touches the live `ao`
install or `ao.service`.

## 2. Running

```bash
H=benchmarks/harbor
# identical task list for every arm (bare names get the dataset's org prefix)
printf '%s\n' fix-git cobol-modernization >/var/tmp/ao-harbor/pilot.txt
$H/run.sh --arm cc --model sonnet --dataset terminal-bench/terminal-bench-2-1 --tasks-file /var/tmp/ao-harbor/pilot.txt -n 2
$H/run.sh --arm ao --model sonnet --dataset terminal-bench/terminal-bench-2-1 --tasks-file /var/tmp/ao-harbor/pilot.txt -n 2
python3 $H/summarize.py /var/tmp/ao-harbor/jobs/<cc-job> /var/tmp/ao-harbor/jobs/<ao-job>
uv run ao-bench harbor-parity /var/tmp/ao-harbor/jobs/<cc-job> /var/tmp/ao-harbor/jobs/<ao-job>
harbor view /var/tmp/ao-harbor/jobs        # browse trajectories
```

- `--model sonnet|opus|haiku|<id>`; `--effort` (default `high`) and `--budget-usd` (default 20, per
  benchmark task) are applied identically to both arms;
  `-n` concurrency (default 2); `--attempts K`; `--n-tasks N`; `--include GLOB`; `--dry-run`
  prints the harbor command; `--install-only` installs the agent in each container and stops
  (no LLM spend).
- `--subject` picks the ao subject; pass the same one to both arms (the plain arm uses its
  wrapper prompt too).
- The ao arm builds a wheel from this checkout (`wheels/<sha>/`); a dirty `src/` gets a fresh,
  dirty-stamped build. Benchmark from a clean, committed tree.
- `-- <args>` passes extra flags to `harbor run` (e.g. `--agent-timeout-multiplier 2`).

### Datasets checked on this host (2026-11-11)

| Dataset | Tasks | Per task | Notes |
|---|---|---|---|
| `terminal-bench/terminal-bench-2-1` | 89 | 1-4 CPU, 2-8 GB, 15-60 min (one 200 min) | Images on Docker Hub; light. Top public scores ~80-90%: near saturation |
| `datacurve/deep-swe-1-1` | 113 | 2 CPU, 8 GB, 90 min | ~5x larger patches than SWE-bench Pro, 5 languages; best candidate for spread |
| `abundant/swe-marathon` | 20 | 4-8 CPU, 16-64 GB, 4-10 h | 5 tasks need a GPU (skip here: T400 4 GB). 15 CPU tasks; 16-32 GB each limits concurrency to ~2-3 on 62 GB |

Docker images are pulled or built per task (DeepSWE images are large); keep 60+ GB free under the
Docker data-root. Harbor removes each trial's environment after the trial (`--delete`, the
default; the smoke runs left no `hb__*` images behind); check `docker system df` between datasets.

## 3. Input parity: what is identical, what differs by design

The comparison must not come down to one arm getting better instructions or settings. Rule: **if
something should help one arm, it goes into the shared parity layer so it helps both.**

| Identical in both arms | How |
|---|---|
| Task container, task files, verifier, wall-clock agent timeout | Harbor, same task list |
| Claude Code version | `--ak version=<host claude version>` (both install the same pinned CLI) |
| Model | `-m`; in ao also `allowed_models` and `default_unit_model` pinned to it (overseer cannot pick another model per unit) |
| Thinking effort | `--effort` (default `high`): `claude --effort` / `AO_THINKING_EFFORT` on every ao agent, plus `CLAUDE_CODE_EFFORT_LEVEL` env |
| Task prompt | byte-identical: the subject's wrapper (`ao-overseer/prompt-harbor.md`) + the verbatim instruction (`compose_prompt`; the plain arm renders `ao-bench harbor-prompt`'s template) |
| User-level config (CLAUDE.md, skills, agents, commands, settings) | `parity/claude/` copied into each trial's fresh `CLAUDE_CONFIG_DIR` (empty by default: stock Claude Code). No host `~/.claude`, no memories |
| Auto memory | off (`CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`); otherwise ao's later sessions would read earlier sessions' memory |
| claude.ai MCP connectors | off (`ENABLE_CLAUDEAI_MCP_SERVERS=false`) |
| Permission mode, autocompact, output style, thinking display, max output tokens | Claude Code defaults in both (`bypassPermissions`; neither arm sets the others) |
| Web tools | enabled in both (Claude Code default) |
| Spend cap per benchmark task | `run.sh --budget-usd` (default 20): the plain arm's session gets `claude --max-budget-usd`, the whole ao run (all its sessions) `run_budget_usd`. ao's per-unit `task_budget_usd` (subject, 8) is internal pacing |
| Wall-clock limit | the dataset's own agent timeout, both arms (leaderboard-comparable); ao's overhead counts against it |
| Credentials | same env file |

| Different by design (the thing under test) | |
|---|---|
| Sessions | plain: one `claude -p` session. ao: many sessions (intake, waves of units, checkpoints, final verify, closeout) |
| ao's orchestration instructions | overseer contract, stage instructions, role prompt templates (`agents.json`): ao's method |
| Per-session turn caps | plain: none. ao: per-unit turn budgets (`medium` 30 / `high` 60) from the template |
| Git | ao's `init_git` commits the repo and adds a local bare `origin` (outside the repo) for its branch-off stage |


Verify parity on every pilot before reading scores: `ao-bench harbor-parity <cc-job> <ao-job>`
compares every session's init event (tools, skills, agents, slash commands, MCP servers,
plugins, permission mode, output style, CLI version, auto memory); exit 3 on a mismatch.

## 4. Reading results

- `summarize.py`: rates use attempted trials. An agent timeout counts as an attempt (reward 0);
  every other trial exception (image build, verifier timeout, API/auth error) is infra and
  excluded, listed in the last column. Read those trials before trusting a comparison.
- Pairwise lines give, over tasks both arms attempted, `both / A only / B only / neither`. Only the
  discordant counts separate two arms; with fewer than ~10 of them no claim is safe. Add tasks or
  attempts, not conclusions.
- Costs: `cost_usd` is what Claude Code reports (with a subscription token: the list-price
  equivalent, not a bill). The ao arm's cost is ao's own run total (all sessions).

## 5. For developers

- `CcAgent` subclasses Harbor's `claude-code` and uploads `parity/claude/`. `AoAgent` subclasses
  `CcAgent`, keeps its Claude Code install and auth, then installs `uv`, the ao wheel (Python 3.12
  via uv) and the subject's own files (nothing else from the checkout), links the tool venv's
  Python as `ao-python` (the overseer hooks' `python_bin`) and runs `ao-bench exec-subject` in the
  task's working dir. Results: `agent/ao-usage.json` -> Harbor's cost/token fields;
  `agent/ao-orchestrator.tgz` keeps the ao run state and transcripts.
- Harbor is pinned (`HARBOR_VERSION` in `setup.sh`). The adapter uses Harbor internals
  (`ClaudeCode` methods), so re-run `--install-only` and a one-task pilot after a Harbor upgrade.
- Tests: `uv run pytest -q tests/bench/test_harbor_exec.py` (fake tier, no Docker). Live checks:
  `setup.sh --smoke`, `run.sh --install-only`, then a 1-2 task pilot per arm.

## 6. Rules for Claude sessions

1. Real money: confirm the budget with the owner before any run beyond a 1-2 task pilot; set
   `--n-tasks`/`--tasks-file` explicitly; never start a full dataset unasked.
2. Same `--tasks-file`, `--model`, `--effort` and `--subject` for both arms of a comparison, and
   run `harbor-parity` on the pilot before reading scores.
3. Anything that should help ao's agents (CLAUDE.md, skills, agent files) goes into
   `parity/claude/`, never into the ao subject alone.
4. Never run against this workspace's live `workflows/**/runs/`, never `install.sh`; the ao arm
   uses a wheel built from the checkout, not the live `ao`.
5. Record results and spend in the ticket and the KB (see the `bench-campaign-report` skill).
