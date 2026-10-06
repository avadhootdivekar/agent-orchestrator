# Testing a new `ao` build without touching the prod tool

Prod `ao` is a non-editable `uv tool` snapshot (`~/.local/bin/ao` -> `~/.local/share/uv/tools/agent-orchestrator/`).
Never run `install.sh` / `uv tool install` / restart `ao.service` to test. Baseline of prod (path, version,
tool-bin sha256, service PID) is in [`beta-test-baseline.txt`](beta-test-baseline.txt); diff against it afterwards.

## Method A — `uv run` from the clone (quickest)
```bash
cd <clone> && uv run ao --version     # uses the clone's gitignored .venv, editable
```
Run it from the clone, so use absolute paths / `--workflow` etc. for a workspace elsewhere, or
`uv run --project <clone> ao ...` from the workspace directory.

## Method B — temp venv with editable install (verified; preferred for workspaces)
```bash
T=$(mktemp -d /tmp/ao-beta.XXXX)
uv venv $T/venv && uv pip install --python $T/venv/bin/python -e <clone>   # add '[ui]' for the dashboard
$T/venv/bin/ao --version        # shows the clone's commit (e.g. ff8eaef.dirty), not prod's
mkdir $T/ws && cd $T/ws && $T/venv/bin/ao init
# put reposets.json + agents.json (copy from the runner's specs/) in $T/ws, then:
$T/venv/bin/ao new routed-runner smoke --param repo_set=ao --validate-only
$T/venv/bin/ao validate --workflow workflows/routed-runner/runs/*/workflow.json \
    --reposets reposets.json --agents agents.json     # -> "OK: all specs valid"
```
`ao new --validate-only` needs `--reposets`/`AO_REPOSETS` or `reposets` in `.ao/config.yaml`.
"Inferred edge ... not declared in depends_on" lines are expected warnings.
Clean up with `rm -rf $T`.

## Method C — separate tool dir (only if a `uv tool` layout is needed)
`UV_TOOL_DIR=$T/tools UV_TOOL_BIN_DIR=$T/bin uv tool install <clone>` — isolated, but still a `uv tool install`
that the workspace rule forbids; use A/B instead. Not exercised.

## Caveats
- Don't launch the dashboard/daemon from a beta: ports 8768/8770 belong to prod's `ao service`.
- Verify prod unchanged: `which ao; ao --version; uv tool list; sha256sum ~/.local/share/uv/tools/agent-orchestrator/bin/ao`.
