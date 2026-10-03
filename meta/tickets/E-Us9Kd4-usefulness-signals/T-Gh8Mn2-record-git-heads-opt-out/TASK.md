# TASK: T-Gh8Mn2-record-git-heads-opt-out

## Metadata
- Task ID: `T-Gh8Mn2-record-git-heads-opt-out`
- Epic ID: `E-Us9Kd4-usefulness-signals`
- Owner: dev-epic (delegated)
- Created: 2026-10-02
- Last Updated: 2026-10-02
- Status: `Done`
- Estimate: `< 3 days`

## Requirements Mapping
- `FR-12`

## Description
Change A: opt-out setting (config/env/CLI) for git-head recording. Follow-up change request from the coordinator (HEAD ea43032). Contract: docs-md/usage-signals-hld.md "Follow-up" section.

## Comments
- By: developer | Role: developer | Date: 2026-10-02 | Comment: Implemented and tested; see STATUS.md. Resume honours the current setting for newly started tasks and never clears recorded heads; RunState.record_git_heads reflects the setting at run start only.
