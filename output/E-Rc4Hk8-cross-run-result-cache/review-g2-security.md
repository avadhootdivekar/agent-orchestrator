# G2 security delta review: E-Rc4Hk8 cross-run result cache

- Reviewer: dev-security (Gate G2, security delta pass). Date: 2026-10-05.
- Scope: `git diff 6ba90ba..HEAD` at HEAD `370e9d8`, plus the G1b remediation commit `6ba90ba` itself (not reviewed before).
  Code: `cache/cli.py`, `cache/cli_ops.py`, `cache/restore_sweep.py`, `cache/safeio.py` (`open_dir_fd`, `strip_control_chars`,
  `is_restore_tmp_name`), `cache/hashing.py`, `cache/report.py`, `cli.py` (status echo, banner), `ui/files.py`, `ui/runs.py`,
  `ui/src/components/RunDetail.tsx`, `ui/src/types.ts`, the committed bundle, `docs-md/result-cache-g0-protocol.md`,
  `.github/workflows/ci.yml`, plus the deferred items of G1a and G1b.
- Baseline: HLD `docs-md/cross-run-result-cache-hld.md` 7.7 (M-1..M-16), 8.9, 8.10; ADR-0019 + addenda; G1a/G1b security reports
  (including the G1a re-verification section) and the T-fXWbqg STATUS carry-overs.
- Threat model: the HLD's. The cache dir, `state.json`, `status.json`, `.git` and the workspace are agent-writable (same uid).
  Deliberate same-uid forgery and active TOCTOU races are accepted residuals, reported only where the PoC adds information.
- No source or test file was modified. All adversarial scripts are in the scratchpad
  (`/tmp/claude-1000/-usr-avadhoot-mounted-agent-orchestrator/c3bbc271-ec14-4ba6-9c94-0ca585be9938/scratchpad/g2s_*.py`).

## Verdict: **PASS** (0 MUST-FIX)

Counts: MUST-FIX 0, SHOULD-FIX 3, NIT 9.

Posture: the new surface held under hostile input. `ao cache` handles hostile prefixes, planted links, hostile entry strings, a lone
surrogate and hostile flags without a crash, an escape sequence, a write outside the store, or a deletion through a link; `verify` and
`prune --dry-run` leave the whole tree byte-identical (names, modes, sizes, mtimes, inodes). The restore-leftover sweep never followed a
link, never touched a directory or FIFO, never left the workspace, and kept the sole copy of a replaced file. The dashboard refuses every
path, `..`, symlink, absolute and encoding variant of `.orchestrator/cache`, including through the HTML preview's asset references; the
rendering is text-only and the committed bundle matches the source. The three SHOULD-FIX items are all cheap and worth doing before the
merge, but none is exploitable beyond the already-accepted "same-uid writer can influence an opted-in task's output" residual.

Top items: G2-S1 restore-tmp name exemption lets any file hide from a directory-input key (demonstrated poisoned hit; severity assessed
below), G2-S2 git `filter.<x>.clean` (G1b S-3, re-confirmed live), G2-S3 SEC-15 CI-config sensitive-path list.

## Findings

| id | severity | file:line | issue | fix / verification |
|----|----------|-----------|-------|--------------------|
| G2-S1 | SHOULD-FIX (assessed: not MUST-FIX, see "Restore-tmp exemption" below) | `cache/hashing.py:107-109` (skip), `cache/safeio.py:240-249` (`is_restore_tmp_name`), `cache/restore.py:59-61` (stale comment: "Nothing sweeps them") | **A file named `.ao-result-cache-<anything>.tmp` or `...tmp.bak` inside a declared input DIRECTORY is not in the key (any depth, regular files only).** Anything in the workspace can create one, no cache-dir write needed. PoC `g2s_tmpname.py`, real `ao run --cache`, fake executor, task `inputs: ["indir"]`, `cache: true`: run 1 with `indir/.ao-result-cache-1-2-aaaa.tmp` present (content "PROMPT INJECTION...") stores an entry; run 2 with the file removed and `out/` deleted is a **hit** (`hits=1`) on the entry computed while the hidden file was visible to the task. Controls: `.hidden-note.txt` and `.ao-result-cache-1-2-aaaa.txt` both change the key (miss, new entry); `.tmp.bak` is also hidden. Consequence: an indirect prompt injection planted by one agent (task B) in an input dir of a cacheable task C is baked into C's stored output under the CLEAN key, so clean runs keep replaying it for `ttl_days` (default 30). Without the exemption the tainted result would sit under a tainted key that a clean run never matches. The same blindness works in reverse (an input changed only through such a file never invalidates). It contradicts the code's own rule at `hashing.py:12-14` ("causes false misses, never false hits"). | Remove the skip (hash these files like any other): delete `hashing.py:107-109` and fix the module docstring (lines 10-14) and the `restore.py:59-61` comment. A crash leftover then causes at worst a false MISS, which is safe, and `ao cache prune` now sweeps the leftovers (`restore_sweep`), so G1b's reason for the exemption is gone. Keep `is_restore_tmp_name` for the sweep. Tests to flip: `tests/cache/test_hashing.py` "orphans ignored" -> "orphans hashed" and `test_g1b_remediation.py::TestRestoreTmpNamePredicate` callers in hashing. GV-1 is unaffected (the golden workspace has no such name). Alternative if false misses are unwanted: raise `UncacheableError` (new reason `input_restore_leftover`, detail "run `ao cache prune`") when one is found. Re-verify with `g2s_tmpname.py` (expect `CLEAN-RUN-HIT-ON-TAINTED-ENTRY=False` for all four names). |
| G2-S2 | SHOULD-FIX (G1b S-3, re-judged: not MUST-FIX) | `cache/repo_state.py:178-209` (`WorktreeProbe.snapshot`), `:98-112` (`git_read_env`) | **`git status` in the guard-3 probe still executes `filter.<x>.clean` named in the agent-writable `.git/config`.** Re-confirmed at HEAD with git 2.39.5 (`g2s_filter.py`): `.gitattributes: *.txt filter=pwn` + `[filter "pwn"] clean = sh -c "id > MARKER; cat"` in `.git/config`, then `touch tracked.txt` (stat-dirty, content unchanged): `WorktreeProbe().snapshot(...)` created MARKER with the orchestrator's uid. `core.fsmonitor` stays closed (marker not created). Why not MUST-FIX: (1) preconditions: cache on AND task opted in AND a writer of `.git/config` that is NOT already able to run code (a tool-restricted agent, Write/Edit without Bash; an agent with Bash already runs arbitrary code as the same uid); (2) the same primitive pre-exists in this code base outside the cache (`cli.py:2712` `git.status_porcelain(..., untracked=True)`, `isolation/resolvers.py:357,412`, `survival.py` git diff; `isolation.git.SAFETY_ARGS` neutralises hooks only); (3) default off. It is still a tool-policy bypass for restricted agents, so it must be a NAMED residual in the docs. | Minimal code fix (about 10 lines, optional before merge): in `WorktreeProbe.snapshot`, before `status_porcelain`, run `git config --local --get-regexp '^filter\.'` through the same hardened env (reading config executes nothing); if any line matches, raise `UncacheableError(REASON_REPO_WORKTREE_PROBE_FAILED, "git_filter_configured")` (the coordinator already maps that to "not storable", never "ineligible"). Cost: repos with `git lfs install --local` become not-storable, a safe false negative. Required regardless: T-bdQZW4 lists it in HLD 7.7 and the authoring guide with its precondition and "use `--no-cache` for untrusted repos" (same family as the G1a SEC-03 residual). |
| G2-S3 | SHOULD-FIX (SEC-15, re-judged: not MUST-FIX) | `cache/constants.py:152-157` (`SENSITIVE_PATH_COMPONENTS`, `SENSITIVE_BASENAMES`) | The M-14 refusal list misses other exec sinks. Destinations come only from the spec's declared outputs (restore never chooses a path), so the cache adds no capability a normal task run does not already have; the only added power is replaying an old or forged entry into a path the spec author declared. Defence in depth, which G1a asked to land "before G2/release". Still restorable today: `.gitlab-ci.yml`, `.githooks/*`, `Jenkinsfile`, `.circleci/*`, `.vscode/tasks.json`, `.cursor/*`, `.pre-commit-config.yaml`, `.gitmodules`, `.gitattributes`. | Exact minimal fix, additive and key-neutral for every non-sensitive path: `SENSITIVE_PATH_COMPONENTS` add `.githooks`, `.circleci`, `.vscode`, `.devcontainer`, `.cursor`; `SENSITIVE_BASENAMES` add `.gitlab-ci.yml`, `Jenkinsfile`, `.pre-commit-config.yaml`, `.gitmodules`, `.gitattributes`, `.travis.yml`, `azure-pipelines.yml`, `bitbucket-pipelines.yml` (matching is already case-folded, `safeio.py:208-224`). Add one parametrised case to `tests/cache/test_safeio.py` (sensitive) and `test_restore.py` (refused). Needs a one-line ADR-0019 D29 addendum. `restore_sweep.safe_parent_dir` and key build pick the list up automatically. |
| G2-N1 | NIT | `cache/safeio.py:37-41` (`_CONTROL_CHARS_RE`) | `strip_control_chars` (SEC-14 "fixed") still lets through U+061C ARABIC LETTER MARK (a bidi formatting control), U+00AD soft hyphen and the Unicode tag characters U+E0000-U+E007F (invisible "ASCII smuggling" text, also invisible to an LLM the operator pastes the output into). `g2s_cli.py`: a planted entry with those in `source.task_id` printed them verbatim in `ao cache ls` and `show` text mode (JSON mode escapes them). ESC, BEL, C1, U+202E and zero-width characters were all removed. | Add `؜­᠎\U000e0000-\U000e007f` to the class. |
| G2-N2 | NIT | `ui/files.py:205-209` | (a) The deny-list compares resolved paths case-sensitively. On a case-insensitive filesystem (macOS, Windows) `.Orchestrator/Cache/blobs/..` would resolve to the real directory yet not match. Linux (the supported platform: CI is ubuntu, `install.sh` has no macOS branch) returns 404 for those names (verified). (b) A NUL byte in `path` makes `Path.resolve()` raise `ValueError` -> HTTP 500 (pre-existing, no data returned). Hard links of a blob into the workspace are not path-blockable (same uid can copy it anyway; accepted). | (a) Compare `casefold()`ed parts of `resolved.relative_to(root_path)` against `CACHE_DIR_PARTS` (mirror of SEC-06). (b) Catch `ValueError` and raise `PathNotAllowedError`. |
| G2-N3 | NIT | `.github/workflows/ci.yml:25-32` and the whole file | The new cache step is sound (static shell loop, no interpolation of untrusted data, `-e` semantics so a failing module aborts), but this branch's workflow has none of the supply-chain gates CLAUDE.md asks for: no `pip-audit`, no SAST (Bandit or Semgrep), no secret scan (gitleaks), no top-level `permissions: contents: read`, actions pinned by tag (`@v4`/`@v5`) not SHA, no `timeout-minutes`. The per-module 90 % gate lists `keys store restore coordinator` only; `cli_ops`, `restore_sweep` and `safeio` (the new destructive and path-safety code) are covered by the 85 % package gate only. A sibling branch (T-2wE08U, dashboard auth) adds `permissions:` and a `pip-audit` job to the same file: check the merge keeps them. | At merge: keep the sibling's `permissions:`/`pip-audit`; add `cli_ops restore_sweep safeio` to the `for m in ...` list; add `gitleaks` and `bandit -r src -ll` (or Semgrep) as separate non-blocking-then-blocking jobs. Poor-man's SAST evidence now: `ruff check --select S src/agent_orchestrator/cache` reports only `S101` (`report.py:164` `assert`, harmless). |
| G2-N4 | NIT | `docs-md/result-cache-g0-protocol.md` Steps 3-5, 7 | The commands are safe to copy-paste: no `rm`, `sudo`, `curl | sh`, `eval`; no step prints an environment variable, token or prompt (only counts, dates, reason names); Step 1 installs only the beta flavour (stable untouched); Step 4 correctly says "never delete real outputs". Gaps: (1) shadow mode STORES copies of every successful output under `.orchestrator/cache` (0700, self-gitignored) and the protocol has no cleanup step or retention note (M-10); (2) `$RUNIDS` is intentionally unquoted: it is safe only because the `grep -E` is anchored and the character set is `[0-9TZ-]`, but `WF_ID` is spliced unescaped into the regex (operator-set, so low); (3) Step 6 `json.loads` of agent-writable `run.log` lines would `KeyError` on a hostile line (crash only, no execution; printed counters use `repr`, so escapes cannot reach the terminal). | Add "Step 9: when done, `$AO cache clear --yes --workspace "$WS"` (or leave the entries; they are the measurement's only residue) and note that shadow keeps output copies"; quote `WF_ID` with `grep -E -- "^$(printf '%s' "$WF_ID" | sed 's/[][\.*^$]/\\&/g')-..."` or just document "WF_ID must be alphanumeric/dash". |
| G2-N5 | NIT | `cache/restore_sweep.py:138-146` | Accepted-class TOCTOU: `check_dir_chain` walks the chain, then `open_dir_fd` opens with `O_NOFOLLOW` on the FINAL component only; an intermediate component swapped for a symlink between the two calls is followed, and the sweep then unlinks `.ao-result-cache-*.tmp[.bak]` names (only those names, regular files, owned by us, older than the grace) in the target. Needs an active same-uid racer and the blast radius is files with a staging name (HLD "active TOCTOU race" residual). | Optional: after `open_dir_fd`, re-`lstat` each chain component or compare `os.fstat(fd)` with `os.stat(directory)` (dev/ino). |
| G2-N6 | NIT | `cache/cli_ops.py:157-167` (`_limits`) | `ao cache <cmd> --workspace W` takes `max_bytes`/`ttl_days` from the config discovered from the CWD, not from W's `.ao/config.yaml` (`g2s_cwdcfg.py`: CWD config `max_bytes: 1, ttl_days: 1` -> `ao cache prune -w mine` removed all 3 entries of `mine`). So running the destructive `prune` from inside an untrusted checkout lets that checkout's config drive eviction (cache is regenerable; `--dry-run` mitigates; consistent with how other `ao` commands discover config). | Resolve the project config from the resolved workspace (or print the effective limits in the prune summary and mention `--dry-run`). |
| G2-N7 | NIT | `cache/restore_sweep.py` module docstring, `ao cache clear` | Residual (documented in the module): the sweep visits only the output dirs named by VALID entries still in the store. After `ao cache clear` (or eviction) a crash leftover in an output dir is never swept and keeps a plaintext copy of a cached output in the workspace. | Note it in the docs refresh (T-bdQZW4); or have `clear --sweep` take the dirs before clearing. |
| G2-N8 | NIT | `models.py:1234-1236` (G1b N-4), `ui/runs.py` payload | `ResultCacheRecord.outcome/mode/mode_source` still have no `max_length`; `task_view` copies them into the run-detail payload. The frontend renders none of them (only `hit` and `source_run_id`), so no sink; other `RunState` strings (`route`, `not_taken_reason`) are equally unbounded (payload-size DoS from an agent-writable `state.json`, pre-existing class). `reason_detail` still carries absolute host paths (G1b N-5) and is now also in the dashboard payload (the dashboard already shows `workspace_root`). | `Field(max_length=RESULT_CACHE_MAX_REASON)` on the three fields; use `posix_rel` in `reason_detail`. |
| G2-N9 | NIT | `cache/restore.py:59-61` | Stale comment (see G2-S1): says nothing sweeps the staging leftovers; `ao cache prune` now does. | Reword when G2-S1 is fixed. |

## Restore-tmp exemption: severity assessment (G1b remediation, `6ba90ba`)

Question: can an agent exploit the `.ao-result-cache-*.tmp` name exemption to smuggle an input past the key and produce a poisoned hit?
**Yes, demonstrated end to end** (`g2s_tmpname.py`, table row G2-S1): the file is invisible to the key, so a result computed while it was
present is served to a later run where it is absent, and a change confined to such a file never invalidates an entry.

Severity: **SHOULD-FIX, not MUST-FIX.**

- Who: any same-uid writer of a declared input directory (an earlier task's agent, a prompt-injected agent). That is the actor already
  in the accepted "deliberate poisoning by a same-uid writer" residual (HLD 7.7), who can equally rewrite an entry JSON and its blob.
- Reach: only a task that is double-opted-in (operator `--cache`/`AO_CACHE=1` AND `cache: true` on the task), whose inputs include a
  DIRECTORY, in `on` mode (shadow never serves a hit). Hooks, approval and verdict tasks stay ineligible, so the blast radius is "the
  declared outputs of an opted-in task" (S-5 of G1b), bounded by `ttl_days`.
- Delta vs the accepted residual: lower skill (no cache layout knowledge, no cache-dir write; a plain file write by an agent that has
  only Write/Edit) and it works through a legitimate channel, the task's own context, so it is a persistence primitive for indirect
  prompt injection (the injected task's output is stored under the clean key). That regresses the property the code comment itself
  states, so fixing it before merge is recommended; the fix is a deletion plus test flips and carries no key-schema or GV-1 change.

## Delta checks

### `ao cache` CLI (`cache/cli.py`, `cache/cli_ops.py`, `cache/restore_sweep.py`)

| check | result | evidence |
|---|---|---|
| Hostile key/prefix (`""`, `..`, `../../etc/passwd`, `abcd\n`, `ABCD`, 3 hex, 65 hex, Arabic-Indic digits, `%s%s`, `a/b`, ESC and bidi suffixes) | all exit 2 `malformed key prefix`, message cleaned, no path built | `g2s_cli.py` section 1; `KEY_PREFIX_RE.fullmatch` (`constants.py:48`, `cli_ops.py:371`); `abcd\n` rejected because `fullmatch` |
| Ambiguous and unique prefix | ambiguous: exit 1, up to 10 candidates, `removed: []`, nothing deleted; unique 5-hex `rm` removes exactly one entry | `g2s_cli.py` |
| Path handling in prefix resolution | symlinked shard dir: `UnsafePathError`, exit 1, outside file intact; entry leaf symlink is not a match (`is_file(follow_symlinks=False)`), outside file intact; listing bounded by `CLI_MAX_DIR_ITEMS`; a missing cache creates NOTHING for any of 8 commands | `cli_ops.py:368-410`; `g2s_cli.py` sections 0 and 4 |
| Destructive commands | `clear` without `--yes` and no TTY: exit 1, nothing deleted; `clear --json` never prompts, one doc; with a symlinked `blobs/` both `clear` and `prune` left the outside tree intact; `rm`/`prune` bound flags: negative, huge, non-numeric -> exit 2 | `g2s_cli.py` sections 1 and 5; `cli_ops.py:513-515,605-616` |
| `prune`/`rm`/`clear` go through the store API only | yes; the only other deletion is `restore_sweep` (below); no `shutil`/`os.remove` in `cli_ops.py` | code read |
| Restore-leftover sweep (CWE-59/22) | stale `.tmp` removed; fresh (inside grace) kept; `--dry-run` deletes nothing; symlink named like a tmp: link and its target untouched; directory and FIFO with the name untouched; symlinked output dir and symlinked parent skipped; alias symlink inside the workspace skipped; hostile entry paths (`../`, absolute, `a/../..`, `.git/`, `.claude/`, `CLAUDE.md`, `//`, `\\`, `./`, NUL) never yield a directory; hard link to a precious file: only the name goes, content survives; a `.bak` that is the only name of its inode is kept (`kept_backups`); a `.bak` whose sibling was just removed is conservatively kept | `g2s_sweep.py` (10 OK; the one FAIL was my own test expectation: the `.bak` hard-linked to the removed `.tmp` became nlink 1 and was conservatively KEPT, which is the intended fail-safe) |
| `verify` deletes nothing | tree snapshot (names, mode, size, mtime_ns, inode) identical before/after on a store with a corrupt blob, a symlinked blob, a planted trash dir and an old tmp; exit 1 on the corruption; `store.verify` code has no delete call | `g2s_cli.py` section 3; `store.py:977-1023`; `prune --dry-run` also identical |
| Output sanitisation (M-15) | ESC, BEL, `\n` (cannot forge a line), C1, U+202E, zero-width removed in `ls`, `show`, `stats`, `verify`, error messages; JSON mode `ensure_ascii` escapes everything; a lone surrogate in an entry is rejected as `corrupt_entry` and no command crashes. Survivors: G2-N1 | `g2s_cli.py` section 2, `g2s_surrogate.py` |
| Exit codes | 0 ok, 1 not found/ambiguous/store problem/refusal/verify corruption, 2 bad arguments; `--json` prints exactly one document also on exit 1 | `g2s_cli.py`; matches HLD 8.9 |
| Lazy import of `cache.cli` | `cli.py` imports only `typer` + `constants`; pinned by the AST test (green) | `cache/cli.py:11-14` |

### Dashboard (`ui/files.py`, `ui/runs.py`, `RunDetail.tsx`, bundle)

| check | result | evidence |
|---|---|---|
| Deny-list vs variants | 403 for: `.orchestrator/cache`, trailing `/`, `blobs/..`, `entries/..`, `./`, `runs/../cache`, `x/../`, absolute inside the root, absolute with `..`, `link_to_blob`, `link_to_cache`, relative symlink to the blobs dir, symlink to `.orchestrator`, double slashes, leading space, `%2f` literal, double-encoded, backslash form (404, no such file). All three endpoints (`/files`, `/files/content`, `/files/html`) | `g2s_dash.py` (TestClient, 26 variants x 3 endpoints, 0 leaks except the hard link below) |
| Compare happens after `resolve()` | yes (`files.py:205-209`), so `..` and symlinks into the store are refused; a cache root swapped for a symlink is resolved too | code + `g2s_dash.py` |
| HTML preview asset references | `<img src=".orchestrator/cache/blobs/..">`, `<a href=symlink>` and an SVG `<image href=symlink>` are dropped; no secret and no base64 of it in the preview; the preview reuses `FileBrowser.resolve` (`htmlpreview.py:325`) | `g2s_dash.py` |
| Run endpoints | `runs/{id}` with `..%2Fcache`, `%2e%2e%2fcache`, `..` -> 404; run-id resolution is confined to `.orchestrator/runs` (`runs.py:258-259`) | `g2s_dash.py` |
| Not covered by a path rule | a hard link of a blob into the workspace (same-uid; equal to a copy), case variants on a case-insensitive FS, NUL -> 500 (G2-N2) | `g2s_dash.py` |
| `ui/runs.py` payload | built from validated `RunState` through `report.current_records` (current records only); lazy import of `cache.report` only when `state.result_cache` is non-empty; every string is untrusted text for the frontend; no new route, no mutation endpoint | `runs.py:343-348,379,405` |
| `RunDetail.tsx` rendering | text only: `source_run_id` goes into a plain `title` attribute, all other values are numbers formatted by `formatCost` or fixed strings; no `dangerouslySetInnerHTML`/`innerHTML` was added (`git diff` of `RunDetail.tsx`/`types.ts`); bundle counts of `dangerouslySetInnerHTML` (14) and `innerHTML` (11) are identical before and after the bundle commit | diff read; `grep -c` on `index-B2cn_9HF.js` vs `4e61e68^` |
| Bundle matches source | the new strings (`served from run`, `served from cache`, `would-hit(s) (shadow)`, `avoidable (est.)`, `saved (est.)`, `hit(s)`) occur in `index-B2cn_9HF.js` and 0 times in the previous bundle; the minified `No`/`Po` components are the `ResultCacheTag`/`ResultCacheTile` logic verbatim (`title: t.source_run_id ? ... : ...`); `index.html` references `index-B2cn_9HF.js` and `index-CQm5Jase.css`, both present; bundle is a separate commit (`4e61e68`, three files) | `grep`, `ls static/assets`, `git show --stat` |

### G0 protocol doc

Exists and states in its first paragraph that G0 was not executed here. Commands: safe (see G2-N4); no credential-leaking step; the only
state-changing steps are the operator-consented real runs under `AO_CACHE=shadow` and the beta-flavour install. `bash install.sh
--flavor beta --force` in the troubleshooting table only reinstalls the beta flavour. The guard test executes the documented bash blocks
verbatim against a fixture workspace (green).

### CI step

See G2-N3. The step is correct and not injectable; the gaps are the missing repository-level gates, not this step.

## M-item evidence

| M | Verdict | Evidence |
|---|---|---|
| **M-10** secret retention and exposure | **VERIFIED end to end** (both halves), docs half owed to T-bdQZW4 | Store: after a real `ao run --cache` (`g2s_m10.py`): cache root `0o700`; `entries/`, `entries/v1/..`, `blobs/..`, `tmp/` all `0o700`; entry, blob, `layout.json`, `.gitignore`, `CACHEDIR.TAG` all `0o600`; `.gitignore` is `*`; the entry JSON has keys `created_at key key_schema key_summary outputs schema source usage` only, does NOT contain the prompt text (`SECRET-PROMPT-TEXT`), no env names, `argv` appears only as a component name with a 12-hex digest; `status.json` carries counts and the key. `ao cache clear --yes` removed the entry and the blob, no `trash-*` left. Dashboard: 403 for every variant (table above), including HTML-preview asset refs. Orphaned plaintext outside the root: the prune sweep removes stale leftovers (SEC-19 / G1b S-1 now closed, residual G2-N7). "Retention is documented": HLD 7.7 and ADR only; the user-facing documentation (authoring guide) is T-bdQZW4 and must state that outputs persist in `.orchestrator/cache` after deletion until `ao cache rm/clear/prune`, that shadow mode also stores them, and the G2-S2 / SEC-03 residuals. |
| M-15 terminal escape injection | VERIFIED (NIT G2-N1) | above |
| M-2 path splicing (CLI argument) | VERIFIED | `fullmatch` before any path, shard list only after the prefix check, `delete_entry` re-validates the key (`store.py:274-279`) |
| M-4 link following (CLI paths) | VERIFIED | symlinked shard, entry leaf, `blobs/` link: refused or not followed; outside data intact |
| M-7 bounded work (CLI) | VERIFIED | `ls` uses `heapq.nsmallest` (O(limit)), `--limit` capped at 1 000 000, prefix listing capped at `CLI_MAX_DIR_ITEMS`, sweep capped at `RESTORE_SWEEP_MAX_DIRS` (10 000) dirs x 100 000 names; explicit commands are unbounded by design (G1a) |
| M-9 unsafe deserialization | VERIFIED for the delta | `cli_ops.py` uses `json.dumps` only; `restore_sweep.py` no file opens; AST guard green; `ruff --select S` clean except `S101` |
| M-12 approval bypass | UNCHANGED, still open for the ordering check | E-Ag7Pw3 code is not in this tree (`grep -ni 'approval\|human_gate' engine.py models.py` empty); seam comment at `engine.py:1219`; re-verify when the approvals code lands (G1b N-10), including a gate recognised only through persisted state |
| M-14 execution sink via restore | VERIFIED with the SEC-15 gap | G2-S3 |
| M-11 / M-16 / M-1 / M-3 / M-5 / M-6 / M-8 / M-13 | Unchanged by this delta (no edits to `store.py`, `restore.py`, `keys.py`, `coordinator.py`, `types.py` since G1b) | `git diff 6ba90ba..HEAD --stat`; the G1a/G1b verdicts stand. M-11 note: G2-S1 weakens "inputs in the key" for directory inputs until fixed |

## Deferred items re-judged (every gate)

None is MUST-FIX for merge. `store.py`, `restore.py`, `keys.py`, `coordinator.py`, `types.py`, `fingerprint.py` are unchanged since G1b, so a
judgement that depends only on that code is unchanged.

| item | status now | verdict | minimal fix if taken |
|---|---|---|---|
| G1b S-3 `filter.<x>.clean` | re-confirmed live (G2-S2) | SHOULD-FIX, not MUST-FIX; docs must name it | probe `git config --local --get-regexp '^filter\.'` -> not storable |
| SEC-15 / S-8 CI-config names | still restorable (G2-S3) | SHOULD-FIX | extend the two frozensets |
| G1b S-1 restore-tmp (the exemption) | exemption introduces G2-S1; the sweep half is good | SHOULD-FIX | delete the hashing skip |
| SEC-10 `claude --version` stdin/env/output | unchanged; the binary runs at dispatch anyway; stdout is memoized and clipped to `MAX_CLI_VERSION_CHARS` after an unbounded `capture_output` read, 10 s timeout | NIT | `stdin=subprocess.DEVNULL` |
| SEC-11 provider env names not in the key | unchanged; operator-controlled, non-secret | NIT (staleness only); needs a GV-1 decision | add `ANTHROPIC_BASE_URL`, `CLAUDE_CODE_USE_BEDROCK`, `CLAUDE_CODE_USE_VERTEX` and bump the golden vector deliberately |
| SEC-12 / S-9 AST guard evasions | unchanged (`Path.open`, `io.open`, `os.system` not flagged); the new modules comply | NIT | extend the banned-call set |
| SEC-13 lax pydantic (`"size":"6"` accepted) | unchanged; blob size/hash check makes it harmless | NIT | `strict=True` on entry models |
| SEC-14 bidi stripping | fixed except U+061C, soft hyphen, tag characters (G2-N1) | NIT | extend the regex |
| SEC-16 mode `0o000` restores unreadable | unchanged; same uid | NIT | `mode | 0o600` |
| SEC-17 `ClearReport` counts through a symlinked `blobs/` | unchanged; reporting only (outside tree verified intact by `clear`) | NIT | `is_symlink` check before counting |
| SEC-18 forged future `created_at` immortal | unchanged; accepted same-uid residual | NIT | treat `created_at > now + 1 day` as corrupt |
| SEC-19 staging leftovers after SIGKILL | closed by `restore_sweep` (+ `ao cache prune`); residual G2-N7 | closed | n/a |
| SEC-20 root `0o755` accepted, plain `ValueError` on a bad key | unchanged; contents stay `0o600`/`0o700` | NIT | tighten to `& 0o077` |
| SEC-21 hard-linked mtime | accepted | NIT | n/a |
| G1a S-6 inline thrash, RV-1 unbudgeted trash removal | unchanged; self-limiting | NIT | n/a |
| G1b N-1..N-10 | N-4, N-5 -> G2-N8; N-7/N-9 (guard-3 blind spots) docs only; N-2, N-3, N-6, N-8 unchanged NITs; N-10 open (M-12 above) | NIT | n/a |
| G1b S-2, S-5 (status block totality, banner note) | VERIFIED: `format_summary_line` returns None for 14 hostile blocks (bool, str, NaN, inf, negative, > 1e15, non-dict, missing key) and formats only validated numbers; the echo strips control chars; `ao status` catches `ValueError`; banner carries the trust note in `on` mode only | closed | n/a |

## G1b remediation (`6ba90ba`) spot checks

- Status block parser total: `report.py` `_summary_number`/`format_summary_line` (`bool` excluded, `int`-only for counts, 0..10^15, NaN/inf
  fail the range test, only numbers formatted); `cli.py` echo strips control chars; `status` except widened to `ValueError`. Tests
  `TestSummaryLineIsTotal`, `TestAoStatusWithHostileBlock` green.
- Restore-tmp exemption: see G2-S1 (the one real finding of the remediation).
- DRY helpers (`clip_text`, `try_resolve`) are behaviour-preserving; no security effect.

## Commands and results

Worktree `/usr/avadhoot/mounted/agent-orchestrator/.claude/worktrees/agent-a18ce2c08e42a3a5a`, `.venv/bin/python`.

| command | result |
|---|---|
| `.venv/bin/python -m pytest -q -p no:cacheprovider tests/cache tests/ui` | **2041 passed, 2 skipped, 0 failed** (36 s). The 2 skips are `tests/ui/test_e2e_graph.py:295,538` (`playwright` not installed); none in `tests/cache` |
| `.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_e2e_cli_result_cache.py tests/test_e2e_cli_result_cache_admin.py` | 193 passed |
| `.venv/bin/ruff check src/agent_orchestrator/cache src/agent_orchestrator/ui` | All checks passed |
| `.venv/bin/ruff check --select S src/agent_orchestrator/cache` | 1 finding, `S101` assert at `report.py:164` (not a vulnerability) |
| `bandit`, `pip-audit`, `gitleaks` | not installed here; no dependency, lockfile or `package.json` change in the diff (`git diff --stat` on `pyproject.toml`, `uv.lock`, `ui/package*.json` empty) |
| `git status --short` after all scripts | only the other reviewer's untracked `review-g2.md`; nothing of mine in the repo |

Adversarial scripts (scratchpad), one line each:

| script | result |
|---|---|
| `g2s_cli.py` | 8 commands on a fresh workspace create nothing; 14 hostile prefixes exit 2; ambiguous/unique `rm`; flag bounds; `clear` refusal; hostile entry output sanitisation (survivors G2-N1); `verify` and `prune --dry-run` tree-identical; symlinked shard/leaf/`blobs/` safe |
| `g2s_sweep.py` | restore-leftover sweep safety matrix: all safe (one self-inflicted expectation error, noted) |
| `g2s_tmpname.py` | real `ao run --cache`: `.tmp` and `.tmp.bak` names hide an input from the key -> clean run hits the tainted entry (G2-S1); other hidden names change the key |
| `g2s_m10.py` | modes, `.gitignore`, summary-only entry, no prompt text, `clear` removes copies |
| `g2s_dash.py` | 26 path variants x 3 endpoints + HTML/SVG preview + run-id traversal: no leak except the un-blockable hard link; NUL -> 500 (pre-existing) |
| `g2s_filter.py` | `filter.<x>.clean` still executed by the guard-3 probe (git 2.39.5); `core.fsmonitor` closed |
| `g2s_cwdcfg.py` | `ao cache prune -w` uses the CWD's config limits (G2-N6) |
| `g2s_surrogate.py` | lone surrogate in an entry: `corrupt_entry`, no crash in any command |

## Pre-submit checklist

- [x] Engagement criteria: explicit request (Gate G2 security delta pass).
- [x] Untrusted spec/payload execution: no `eval`/`exec`/shell interpolation in the delta (`cli_ops.py`, `restore_sweep.py` are `os`/`json`/`typer` only; the G0 doc runs fixed commands). Finding G2-S2 (git filter, pre-existing class).
- [x] Sandboxing/isolation: sweep is bounded (dirs, names), `ls` O(limit); no fan-out, no process spawning in the delta.
- [x] Artifact/path safety: clean for the CLI, sweep and dashboard (variants tested); G2-S3 (sensitive list), G2-N5 (accepted TOCTOU class), G2-N2 (case variants, other platforms).
- [x] Secrets handling: M-10 verified (modes, summary-only entry, no prompt/env in entries, `clear` removes copies); G2-N4 (G0 doc retention note), G2-N7.
- [x] Trigger authn/authz: N/A for the delta; the cache CLI is local and the dashboard gained no route and no mutation endpoint (the dashboard-auth epic is a sibling branch).
- [x] Input validation: hostile args, entries, surrogate, NUL, size bounds; G2-S1 (a name-based exemption in key hashing).
- [x] Rate limiting/DoS: caps verified (`--limit`, listing, sweep); explicit admin commands are unbounded by design.
- [x] Supply chain: no dependency change; no `bandit`/`pip-audit` available locally; G2-N3 lists the missing gates in this branch's workflow.
- [x] CI/CD merge gates: new step reviewed (sound); G2-N3.
- [x] Every finding has a location and a demonstrated path (G2-N5, G2-N7 and the macOS half of G2-N2 are by code reading and say so).

## CI and process follow-ups

1. At merge: keep `permissions:` and the `pip-audit` job from T-2wE08U in `ci.yml`; add `cli_ops restore_sweep safeio` to the per-module coverage loop; add `gitleaks` and Bandit or Semgrep.
2. Add the three regression tests with the fixes: hashing hides nothing (G2-S1), sensitive names (G2-S3), invisible characters (G2-N1).
3. T-bdQZW4 (docs refresh) must: name the G2-S2 / SEC-03 residuals with preconditions and recommend `--no-cache` for untrusted repos; state retention (outputs persist in `.orchestrator/cache` until `rm/clear/prune`; shadow stores too); keep the banner trust note wording; note the ADR-0019 D29 addendum.
4. G1b N-10 / M-12: re-verify approval ordering when E-Ag7Pw3 lands (not testable in this tree).

## Follow-ups (owners)

| item | owner | when |
|---|---|---|
| G2-S1 delete the hashing exemption, flip 2 tests, fix 2 comments | T-8tr1H4 (hashing) / T-u3jG8F owner | before merge (recommended) |
| G2-S3 extend the sensitive lists + D29 addendum | T-uoYW6b owner + architect | before merge (recommended) |
| G2-S2 probe `filter.*` config check (optional) and docs residual | T-uoYW6b owner (code), T-bdQZW4 (docs) | docs required; code optional |
| G2-N1..N9 | one hygiene ticket | after merge |
