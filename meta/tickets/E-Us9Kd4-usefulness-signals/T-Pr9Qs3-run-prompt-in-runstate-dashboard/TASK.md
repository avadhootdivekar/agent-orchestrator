# TASK: T-Pr9Qs3-run-prompt-in-runstate-dashboard

## Metadata
- Task ID: `T-Pr9Qs3-run-prompt-in-runstate-dashboard`
- Epic ID: `E-Us9Kd4-usefulness-signals`
- Owner: dev-epic (delegated)
- Created: 2026-10-02
- Last Updated: 2026-10-02
- Status: `Done`
- Estimate: `< 3 days`

## Requirements Mapping
- `FR-13`

## Description
Change B: capture run prompt in RunState + dashboard Prompt panel/list preview. Follow-up change request from the coordinator (HEAD ea43032). Contract: docs-md/usage-signals-hld.md "Follow-up" section.

## Implementation notes
- `models.RunPrompt` + `RunState.prompt`; capture helper `run_prompt.py` (streaming bounded read, sha256/chars over the FULL file, text cut at `MAX_PROMPT_BYTES`=64 KiB on a UTF-8 boundary).
- CLI layer (`cli._capture_run_prompt`, after `_apply_prompt`) hands the Orchestrator a value via `run_prompt=`; engine only stores it on a NEW run. `ao resume` passes the loaded state, so the recorded prompt is never rewritten.
- Launch paths: dashboard/service/`ao new --run`/template start all reach `ao run` (subprocess or in-process `_invoke_run_in_process`); service/dashboard resume reaches `ao resume`. So `ao run` is the single capture point.
- Dashboard: detail API has `prompt` + `prompt_changed_since_start` (sha re-hash through the workspace-guarded store, in `ui/runs.py`); list/summary has `prompt_preview` only (80 chars). Hub only shows run counts (no run list) — nothing to change.
- Frontend: `PromptPanel.tsx` (text-only), list preview, `format.ts` helpers, static rebuilt.
- Not done: `ao status` hint (skipped: status.json fast path lacks the prompt, would add a state read/noise).
