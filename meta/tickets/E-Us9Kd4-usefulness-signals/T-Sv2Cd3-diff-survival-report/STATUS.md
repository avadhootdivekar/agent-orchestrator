# STATUS

- ID: `T-Sv2Cd3-diff-survival-report`
- Updated At: 2026-10-02
- State: Done
- Owner: dev-epic

## This update
- By: dev-epic | Role: manager | Date: 2026-10-02 | Comment: ticket opened, work delegated.
- By: developer | Role: developer | Date: 2026-10-02 | Comment: Implemented `survival.py`, `ao report-survival`, git-head recording (models/engine/cli narrow edits), 35 unit + 10 e2e tests. Gates: see below.

## Deviations from the HLD (applied per reviewer early-gate message)
- Added `TaskRunState.start_heads` (first dispatch) so serial windows are bounded; the patch base is `start_heads` (else the previous settled head). Overlap with another HEAD-moving task => `ambiguous` (run level only). Uncommitted work => zero commits => `n/a`.
- Added `TaskIntegrationState.landed_ranges` (repo_key -> [[head_from, head_to], ...], appended at the integration-settle site in `engine.py` where `head_from`/`head_to` are computed). Isolation attribution prefers these durable ranges (retries summed) over `squash_commits` (last squash only, may be rewritten/unreachable). Confidence is `low` and the `likely_worthless` flag suppressed when the patch head is not an ancestor of the ref.
- Metric ignores trivial added lines (stripped length < `MIN_LINE_CHARS`=3 or no alphanumerics) — HLD said "non-blank".
- `time-window` rows are run level only, `confidence=low`, never flagged; consumers (usage join) should filter on `attribution`/`confidence`.
- `git_repos` / `git_start_heads` are recorded only when `run()` is called without a prior RunState (a resume never overwrites; a run resumed from a pre-feature state therefore has no start head and falls back to time-window).
- Git calls use `GitRepo._run(check=False)` (private but the single choke point) since `GitRepo` has no `diff`/`cat-file`/`log` porcelain for these; `git.py` untouched. Root-commit parent uses the sha1 empty-tree constant (sha256 repos unsupported for that edge).
- Revert detection matches `This reverts commit <sha>` against the task's commits; a squash later rebased to a new sha is not detected (HLD limitation).

## Known limits / risks
- Content-match heuristic (see HLD Limits). One `git cat-file` per surviving file per unit; bounded by `MAX_FILES`/`MAX_COMMITS`.
- Recording is one `rev-parse` per repo at each dispatch and settle, failure-tolerant (debug log only).
