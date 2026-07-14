# TASK: T-d3v7hn-artifact-store

## Metadata
- Task ID: `T-d3v7hn-artifact-store`
- Epic ID: `E-m2k9pa-orchestrator-mvp-a`
- Owner: `TODO`
- Created: `2026-06-16`
- Last Updated: `2026-06-16`
- Status: `Draft`
- Estimate: `< 1 day`

## Requirements Mapping
- Requirement IDs: FR-3, FR-6, NFR-2, NFR-4

## Description
Implement `artifacts.py`: `ArtifactStore` ABC + `LocalFsArtifactStore` resolving workspace-relative paths
to absolute, checking existence, and guarding against path traversal. (LLD §5.)

## Acceptance Criteria
1. `resolve(path)` joins against `workspace_root`, normalizes, and **rejects** any path escaping the root → `ArtifactPathError`.
2. `exists(path)` returns existence of the resolved path.
3. Existence checks back resume/idempotency (used by T-w8s5lf, T-h7k3qm) — store reads paths, never contents.
4. Unit tests: relative resolve, `..` traversal rejected, exists true/false, absolute-path policy defined.

## Risks
- Symlink escapes — normalize realpath and compare against root.

## Dependencies
- Upstream: T-r4t8wd (workspace_root from RepoSet). Downstream: T-w8s5lf, T-h7k3qm.

## Pseudocode / Algorithm
```text
resolve(p): full = normpath(join(root, p)); if not full.startswith(realpath(root)): raise ArtifactPathError
exists(p): os.path.exists(resolve(p))
```

## Schemas / Interface Notes
- Interface: `ArtifactStore.resolve/exists` (LLD §5).
- Artifacts: operates on paths only; content-agnostic (supports NFR-1).

## Handoff Boundary
- Upstream: workspace_root. Downstream: engine/run-state existence checks.

## Artifacts
- Docs/comments: this folder.
