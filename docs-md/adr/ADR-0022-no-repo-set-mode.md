# ADR-0022 — No-repo-set (workspace-only) mode

- **Status:** Proposed (2026-10-06); becomes Accepted when the implementation units land.
- **Date:** 2026-10-06
- **Deciders:** Avadhoot Divekar (owner, ask A2 of overseer run `o-969ifr-requirement`); architect (agent)
- **Design:** [`no-repo-set-mode-hld.md`](../no-repo-set-mode-hld.md) (decisions D1–D8, use-site
  inventory §4, plan §8)
- **Related:** ADR-0003, ADR-0013, ADR-0019

## Context

`WorkflowSpec.repo_set` and a reposets file are mandatory, though the reposet conflates the
workspace root with git repo paths. Users with workspace-only workflows (no git repo) must invent
a reposet.

## Decision

1. `repo_set` is optional; absent/`null`/`""` ⇒ no-repo-set mode, `ctx.repo_paths = {}`. Reposets
   are required only when the workflow names a `repo_set`.
2. Workspace root in this mode: `--workspace` > `AO_WORKSPACE_ROOT` > project-config
   `workspace_root` > directory containing `.ao/` > cwd (with a NOTE). With a `repo_set`
   the existing env > `reposet.workspace_root` rule is unchanged.
3. Git-dependent features degrade rather than fail: isolation falls back to `none` (existing
   `no_git_repos` path) with a validate-time warning; survival and the result cache operate on the
   empty repo set.
4. No new path-safety mechanism — `LocalFsArtifactStore` containment already bounds everything to
   the workspace.
5. Agents stay required. Built-in templates make `repo_set` optional.

## Alternatives rejected

- **Synthetic implicit reposet** (auto-create a one-entry reposet for the workspace): invents
  repo ids/paths, would trigger git probing and isolation on a non-repo, and breaks
  `canonical_spec_json` stability.
- **Adopt the sole configured reposet when `repo_set` is omitted:** behaviour keyed to an
  unrelated file; surprising.
- **Fatal error when isolation is requested without repos:** CLI `--isolation` is applied after
  validation, so it cannot be enforced reliably; a warning + runtime degrade matches existing behaviour.

## Consequences

Backward compatible for every workflow with a `repo_set` (HLD §7). Additive `state.json` change
(`repo_set: null`). One deliberate delta: omitting the template `repo_set` param is no longer an
error. Templates with git-only stages stay git-only.
