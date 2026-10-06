# G1a security review: E-Rc4Hk8 cross-run result cache (core set)

- Reviewer: dev-security (Gate G1a, security pass). Date: 2026-10-05.
- Scope: `git diff 0b980e3..HEAD -- src tests specs` at HEAD `a359fb9`. Code under `src/agent_orchestrator/cache/`
  (`safeio`, `types`, `store`, `restore`, `hashing`, `fingerprint`, `keys`, `repo_state`, `eligibility`, `settings`,
  `constants`) plus the `models.py`, `project_config.py`, `cli.py` and `workflow.schema.json` deltas.
- Baseline: HLD `docs-md/cross-run-result-cache-hld.md` section 7.7 (M-1..M-16), 8.2 (safeio), 8.4 (store), 8.5 (restore); ADR-0019.
- Threat model used: the HLD's own. The cache directory is agent-writable (same uid). Deliberate same-uid poisoning,
  an active TOCTOU race and user-level context are accepted residuals and are NOT reported as findings.
- No source or test file was modified. All adversarial scripts live in the scratchpad (`adv1_parse.py` .. `adv11_m3.py`),
  none in the repo tree.

## Verdict: **FAIL** (1 MUST-FIX)

PASS requires 0 MUST-FIX. SEC-01 breaks the M-7 mitigation ("bounded inline maintenance") with a demonstrated
~200x work amplification on the orchestrator main thread. It is a small, local fix. Everything else is SHOULD-FIX or NIT.
Counts: MUST-FIX 1, SHOULD-FIX 8, NIT 12.

Summary of posture: the hostile-data and link-following core is solid. Every adversarial case I tried against M-1..M-6, M-8, M-9,
M-13 held: traversal, symlinked root / `.orchestrator` / shard / `blobs` dir / blob file, FIFO, setuid mode, corrupt / truncated / extended
blobs, hardlinked destination, a 20 000-mutation parser fuzz, secrets in summaries, `verify` deleting nothing, `clear` / `prune` never
following a link. The weak spots are (1) the inline-maintenance bound, (2) the git subprocess environment, (3) capture not re-validating
its parent chain, and (4) case sensitivity of the sensitive-path list.

## Findings

| id | severity | file:line | issue | fix / verification |
|----|----------|-----------|-------|--------------------|
| SEC-01 | **MUST-FIX** | `cache/store.py:488-518` (`_referenced_blobs`), `:735-755` (`_scan_sizes`), `:757-773` (`maybe_enforce_limits`), `constants.py:65-66` | **D19 inline bound does not cover what the prune actually reads.** `_scan_sizes` counts only regular files directly inside `entries/v1/<shard>/` and skips every directory and symlink. But the prune that `maybe_enforce_limits` triggers (`:773` -> `prune` -> `:685` `_referenced_blobs`) reads, in full (up to 1 MiB each, regex-scanned), EVERY regular file under `entries/**` at any depth: foreign version trees (`entries/v9/...`) and sub-directories nested inside a v1 shard. A planted tree is therefore never counted, never deferred, and read inline on the engine's store path. The trigger is only "approx total > max_bytes", which a sparse blob (counted by `st_size`) achieves. Measured: 3000 sparse 1 MiB files under `entries/v9/aa` -> `maybe_enforce_limits` took **30.46 s** (10 ms/file, linear, 0.00 s of which is the bound check); 300 files nested under `entries/v1/aa/deep/er` -> **3.08 s** and `deferred=False`. 100 000 such files is ~17 min with a few seconds of attacker effort (inodes only; sparse files cost no disk). It runs on the main scheduler thread, so no task timeout or cancel applies and every parallel task stalls. Contradicts M-7 ("bounded inline maintenance") and the `constants.py:66` comment ("the mark phase reads every entry file"). Also organic after a version rollback (foreign `v2` tree). | Make the inline bound match the read set. Either (a) in `_scan_sizes` walk `entries/**` with the same walker as `_referenced_blobs`, counting every file AND directory entry against `INLINE_PRUNE_MAX_ENTRIES` / `..._ENTRY_FILE_BYTES` and defer on excess; or (b) give `_referenced_blobs` a read budget (files + bytes) and have inline callers return `PruneReport(deferred=True)` when it is exhausted (the explicit `ao cache prune` stays unbounded). Add a test (ADV-7 style): 1000 sparse files under `entries/v9` and under `entries/v1/aa/x`, assert `deferred=True` and wall time < 1 s. Verification: re-run `adv5_dos.py` (case C) and `adv10_nested.py`. |
| SEC-02 | SHOULD-FIX | `store.py:735-755`, `:520-548`, `:550-560` | The blob walk in `_scan_sizes` (and `_scan_blobs`, `_tmp_files` inside prune) has no count or time bound; only entries are bounded. Measured 100 000 blob files = 0.20 s warm (lstat only, so linear and cheap), but a cold cache or millions of planted inodes turns it into a long inline stall. `DirEntry.stat` at `:746` / `:754` is also not guarded against `FileNotFoundError` when another process prunes concurrently, so a parallel `ao cache prune` makes the inline check raise (cache disabled for that run by the M-16 boundary). Anomalies are not bounded either. | Add `INLINE_PRUNE_MAX_BLOBS` (and count symlinks/dirs) to the deferral condition; wrap the two `stat` calls in `except FileNotFoundError: continue`. |
| SEC-03 | SHOULD-FIX | `cache/repo_state.py:99`, `:157-175` (`WorktreeProbe.snapshot` -> `GitRepo.status_porcelain`) | **Orchestrator executes git config from an agent-writable repo.** `git status` runs `core.fsmonitor` from the in-workspace `.git/config`. `GitRepo._run` neutralises `core.hooksPath` only (`isolation/git.py:544-545`). Demonstrated (`adv6_git.py` B): with `core.fsmonitor = "touch <marker>; echo"` in the workspace `.git/config`, `WorktreeProbe.snapshot()` created the marker file; `rev-parse` (HEAD read) did not. Path: an agent in a tool-restricted task (ADR-0005) that can Write/Edit `.git/config` gets command execution in the orchestrator process after it finishes, outside the Claude tool policy; the probe runs at every store. This is the same execution-sink class as M-14, reached with no cache replay. Pre-existing in the isolation package, but the cache adds it to the default non-isolated path. The outer-repo case is clean: with a hostile `core.fsmonitor` in an ancestor repo, nothing ran (workspace-root bound, U-G7, `adv6_git.py` A). | Pass `core.fsmonitor=false` (and ideally `core.untrackedCache=false`) for every cache git read, via the `env` that `_GitReaders._repo` already builds (git's `GIT_CONFIG_COUNT` / `GIT_CONFIG_KEY_n` / `GIT_CONFIG_VALUE_n` have command-line precedence over repo config; verify empirically, I could not run it here because the harness blocks that env), or add the `-c` to `isolation.git.SAFETY_ARGS`. Add the other known sinks to the HLD 7.7 residual table (`filter.<x>.clean/smudge` for tracked files with attributes) and recommend `--no-cache` for untrusted repos. |
| SEC-04 | SHOULD-FIX | `repo_state.py:99` | `env = {**os.environ, ...}` inherits `GIT_DIR`, `GIT_WORK_TREE`, `GIT_INDEX_FILE`, `GIT_COMMON_DIR`, `GIT_OBJECT_DIRECTORY`, `GIT_CEILING_DIRECTORIES`. Demonstrated (`adv6_git.py` C): with `GIT_DIR` pointing at another repo, `RepoHeadReader.read` returned that repo's HEAD (`7d986a4192`) instead of the workspace's (`4e8ba29bc1`). Realistic when `ao run` is launched from a git hook or a tool that exports `GIT_DIR`. The key then embeds a wrong, constant HEAD, so real HEAD movement never changes the key: stale hits (key poisoning by environment). | Drop all `GIT_*` location variables from the env copy (keep `GIT_OPTIONAL_LOCKS`), and set `GIT_CEILING_DIRECTORIES` to the workspace parent as a second guard. Test: set `GIT_DIR` in the test env and assert the reader still returns the workspace HEAD. |
| SEC-05 | SHOULD-FIX | `cache/restore.py:97-109` (`capture_outputs`) | Capture opens `output_abs[rel]` with `O_NOFOLLOW` on the final component only, and does not re-validate the parent chain the way restore does (`restore.py:154`). `output_abs` is the KEY-TIME path. Demonstrated (`adv3_restore.py`): after `ws/out` is swapped for a symlink to an outside directory, `capture_outputs({"out/id_rsa": ...})` returned a record and wrote the outside file's bytes (`PRIVATE-KEY-MATERIAL`) into a cache blob, which a later hit restores into the declared output. The HLD (M-5) only promises final-component refusal for capture, and the engine's settle-time `resolve` check may stop the task first (G1b to confirm), so this is defence in depth, but it is a cheap read-outside-workspace hole and an exfil primitive. | Before opening, assert `os.path.realpath(path) == path` and `safeio.check_dir_chain(workspace_root, dirname(path))` (needs the workspace root passed in), mapping failure to `StoreSkip(REASON_OUTPUT_NOT_REGULAR)`. Add the swapped-parent test next to ADV-5. |
| SEC-06 | SHOULD-FIX | `safeio.py:207-215`, `constants.py:119-124` | Sensitive-path matching is exact-case (D29). On a case-insensitive filesystem (macOS default, Windows) `.GIT/hooks/pre-commit`, `.CLAUDE/settings.json`, `Claude.md` resolve to the protected targets but pass both the key-time and the restore-time checks. Demonstrated on Linux (where those names are inert): `.GIT/hooks/pre-commit` -> RESTORED, `Claude.md` -> RESTORED. Impact is limited because the destination comes from the spec and the agent can already write it, so M-14 is defence in depth, but it is not met on those platforms (the repo has Windows branches in safeio, so cross-platform is in scope). D29's rationale ("`docs/claude.md` is not sensitive") is a Linux-only argument. | Compare `casefold()`-ed components and basenames (accept the extra false refusals), or probe the workspace filesystem once and casefold only when it is case-insensitive. Update D29 and add a test with `.GIT` / `Claude.md`. |
| SEC-07 | SHOULD-FIX | `restore.py:177-179` | `os.chmod(tmp, ...)` runs by PATH in phase 2, after the staging directory has been unprotected for the whole of phase 1. A same-uid actor that swaps `tmp` for a symlink in that window makes the chmod follow it (CWE-59; the mask limits the damage to `<= 0o755`). It is also the only place a symlink can still be followed in restore. Related: a failure during the commit loop leaves a PARTIAL restore (demonstrated: `a.md` replaced, `b.md` kept old, `RestoreMiss(restore_failed)`), and parent directories created by `ensure_dir_chain` stay behind. The partial case is documented as non-storable (module docstring), so the actual risk is a mixed workspace until the agent reruns. | Apply the mode with `os.fchmod(fh.fileno(), ...)` on the open fd in phase 1 (the file is then final and phase 2 is renames only). Optionally keep the replaced files as `.bak` temp names and roll back on failure, or state in HLD 8.5 that partial commit is accepted. |
| SEC-08 | SHOULD-FIX | `store.py:386-388` (`put_blob` dedupe), `:417-424` (`delete_blob`), `restore.py:173-174` | Dedupe decides on `os.path.lexists(dst)` only, without type or size. A planted directory at `blobs/xx/<sha>` makes every future store of that content return `BlobRef(new=False)` and write an entry pointing at an unrestorable blob. Demonstrated (`adv2_store_restore.py`): `put_blob` returned `new=False`, `has_blob` False, and the self-heal `delete_blob` raised `IsADirectoryError`, so the poison never clears and every run pays a failed restore. A wrong-content regular blob does self-heal (restore deletes it, `adv11_m3.py`). | In the dedupe branch require `S_ISREG(lstat)` and `st_size == size`; otherwise `os.replace(tmp, dst)` over it (replace of a regular file is safe) or refuse with `store_error`. Catch `IsADirectoryError` in `delete_blob` and surface `CacheUnsafePathError` (never evict). |
| SEC-09 | SHOULD-FIX | `store.py:404-405`, `restore.py:173-174` | `read_blob` maps ANY `OSError` (EMFILE, EIO, EACCES, ENOMEM) to `CacheIntegrityError(blob_corrupt)`. The restore then returns `RestoreMiss(evict=True, blob=sha)` and the coordinator deletes the entry AND the blob. Under `max_parallel` with a low fd limit, a transient error destroys good cached data (destructive maintenance on non-evidence). | Only `NotRegularFileError` / hash mismatch mean corruption. Map other `OSError`s to a non-evicting miss (`store_error`, `evict=False`). |
| SEC-10 | NIT | `fingerprint.py:111-116` | `claude --version`: argv is fixed, `shell=False`, 10 s timeout (good), but no `stdin=DEVNULL`, `capture_output` is unbounded, and the child inherits the full environment (API keys) and the orchestrator cwd. The memo identity `(realpath, mtime_ns, size)` can be spoofed with `utime` by a same-uid writer (residual). The binary is the one that would run at dispatch anyway, so no new trust. | Add `stdin=subprocess.DEVNULL`, read at most `MAX_CLI_VERSION_CHARS` bytes, pass a minimal env. |
| SEC-11 | NIT | `fingerprint.py:36-46` | Closed env allowlist omits provider and endpoint selectors. Demonstrated: `ANTHROPIC_BASE_URL` and `CLAUDE_CODE_USE_BEDROCK` do not change the key, so a result produced through one endpoint or provider is replayed under another. Non-secret variables, so safe to add. (A-9 accepts the closed list.) | Add `ANTHROPIC_BASE_URL`, `CLAUDE_CODE_USE_BEDROCK`, `CLAUDE_CODE_USE_VERTEX` (names only; never a key). |
| SEC-12 | NIT | `tests/cache/test_ast_guard.py:26-66` | The guard flags `pickle` / `eval` / `exec` / `shell=True` / bare `open` / `os.open` only. It misses `Path.open/read_*`, `io.open`, `os.system`, `os.popen`, `shell=<expr>`, `compile`, `__import__`, `importlib`, `yaml.load`. Nothing uses them today (grep clean); M-9 holds, but the guard is easy to evade. | Extend the banned-call set; keep the negative self-test. |
| SEC-13 | NIT | `types.py:147-156`, `:158-205` | Pydantic lax mode: `"size":"6"`, `"size":true`, `"mode":420.0` are accepted (demonstrated). Harmless today (the blob hash check rejects a wrong size) but weakens "strict models". | `ConfigDict(strict=True)` on the entry models (keep `AwareDatetime` parsing explicit). |
| SEC-14 | NIT | `safeio.py:223-225` | `strip_control_chars` removes C0/C1 and DEL, but leaves Unicode bidi overrides (U+202E) and zero-width characters, so a hostile entry string can still visually spoof `ao cache ls`. | Also strip `​-‏‪-‮⁦-⁩`. |
| SEC-15 | NIT | `constants.py:119-124` | Sensitive list is exactly D29 but misses other exec sinks: `.gitlab-ci.yml`, `.circleci/`, `.githooks/`, `.pre-commit-config.yaml`, `.vscode/tasks.json`, `.cursor/`, `Jenkinsfile`, `.gitmodules`. All were RESTORED in `adv3_restore.py`. | Extend the sets (ADR-0019 addendum). |
| SEC-16 | NIT | `restore.py:178` | A stored mode `0o000` is restored as `0o000`: the owner cannot read the file, so later input hashing fails with `input_unreadable`. Same uid only. | `mode | 0o600` before the mask. |
| SEC-17 | NIT | `store.py:803-816` (`_count_tree`) | When `blobs/` is a symlink, `clear()` renames the link into trash (outside data untouched, `shutil.rmtree` unlinks it) but `_count_tree` scans through the link, so `ClearReport` counts and sizes outside files (demonstrated: `removed_blobs=1` for a foreign directory). Reporting only. | `is_symlink()` check on the top-level before scanning. |
| SEC-18 | NIT | `store.py:118-121`, `:339-346` | A forged future `created_at` never expires (negative age, by design) and a forged mtime pins an entry against LRU. Same-uid poisoning is out of scope, but immortal entries survive `prune --ttl`. | Treat `created_at > now + 1 day` as corrupt in `parse` / prune. |
| SEC-19 | NIT | `restore.py:158-163` | Staging temp files `.ao-result-cache-*.tmp` live in the workspace next to the destination; a SIGKILL during restore leaves them, and `prune` only sweeps `cache/tmp/`. | Sweep by prefix in an `ao cache prune` step, or stage under the cache `tmp/` when on the same filesystem. |
| SEC-20 | NIT | `safeio.py:175-204` | Root check requires owner and no group/other WRITE but accepts a `0o755` root (M-10 says `0o700`), and `.orchestrator` ownership and mode are unchecked. Blobs and entries are `0o600` and subdirs `0o700`, so content is not exposed; the listing is. `store._entry_path` raises plain `ValueError` (not a `CacheError`) on a bad key. | Tighten `0o755` roots we own as well; wrap the `ValueError` or document it in the ABC. |
| SEC-21 | NIT | `store.py:339-346`, `:386-387` | `touch_entry` and the dedupe `utime` act on the inode, so a hardlinked entry or blob changes the mtime of an outside file. No content effect. | Accept; or require `st_nlink == 1` in the root check. |

## M-item evidence (G1a: M-1..M-9, M-13, M-14)

| M | Verdict | Code evidence | Adversarial / test evidence |
|---|---------|---------------|-----------------------------|
| M-1 traversal on restore | **Verified** | `restore.py:142-143` manifest used as a SET compared with `expected`; destinations only from `expected[rel]` (`:149`); defence in depth `safeio._components_below` raises `UnsafePathError` on `..` (`safeio.py:105-106`), mapped to `restore_failed` (`restore.py:183`). | `adv3_restore.py`: manifest `../escape.md` -> `RestoreMiss manifest_mismatch`, no file; absolute `/etc/passwd` -> mismatch; even with `expected` itself `../escape.md` -> `restore_failed: UnsafePathError`, nothing written outside. |
| M-2 path splicing | **Verified** | `store.py:208-218` `fullmatch` before any join; `types.py:144` hex pattern; `types.py:241-242` `entry.key != expected_key`. | `adv9_misc.py`: 7 hostile keys (`../../etc/passwd`, trailing `\n`, uppercase, 63/65 chars, Arabic-Indic digits, `/../x`) -> `ValueError` before any path; entry with trailing-newline key or sha, uppercase, non-ASCII digits -> `corrupt_entry`. |
| M-3 corrupt blob | **Verified** | `restore.py:170-176` size and sha verified while staging; failure -> `RestoreMiss(blob_corrupt, evict=True, blob=sha)`; `finally` removes staged files (`:185`); `store.read_blob` bound `:408-412`. | `adv11_m3.py`: flipped byte, truncated, extended (5 KB over), empty blob -> all `blob_corrupt evict=True`, destination absent, no leftover tmp. SEC-09 is the over-eager side. |
| M-4 link following in the cache dir | **Verified** | `safeio.py:110-135` `lstat` walk; `:175-204` root checks; `store.py:226-231` `_checks` before EVERY op incl. `delete_entry` `:348`, `touch_entry` `:339`, `delete_blob` `:417`; `O_NOFOLLOW` opens `safeio.py:28-30`; `iter_entries` / scans report symlinks (`store.py:449-453`, `:526-535`). | `adv2_store_restore.py`: symlinked shard, `blobs/`, root, `.orchestrator`, blob file, FIFO blob -> `CacheUnsafePathError` / `CacheLayoutError` / `blob_corrupt`; the outside directory never changed; FIFO read did not block. `adv4_maint.py`: prune and clear with symlinked shard / `blobs` left outside data intact. Tests U-ST15 (12 passed). |
| M-5 link following at inputs / outputs | **Verified** (SEC-05 caveat on capture) | `keys.py` uses `artifact_store.resolve` (`fingerprint.py:guarded_resolve`); `hashing.py:107-108` symlink in an input dir -> `input_not_regular`; `restore.py:154` `realpath(dest) != dest`. | `adv8_keys.py`: input symlink to `/etc/passwd` -> `path_rejected`, dir containing symlink -> `input_not_regular`, FIFO input -> `input_not_regular` without hanging. `adv3_restore.py`: dest symlink and parent symlink -> `restore_failed`, victim untouched; hardlinked destination replaced, outside inode unchanged. |
| M-6 hang on special file | **Verified** | `safeio.py:28` `O_NONBLOCK`, `:52-68` `S_ISREG` after `fstat`; every cache open goes through it (AST guard). | FIFO blob read returned `blob_corrupt` in < 3 s; FIFO input uncacheable; test thread-joins in `test_store_core.py`. |
| M-7 resource exhaustion | **NOT verified: FAIL, see SEC-01 (and SEC-02)** | Verified parts: `hashing.py:48-52,67` charge before reading; `safeio.py:71-90` stat then read bounded at `size + 1`; `store.py:373-376` and `:408-412` bounded streams; `types.py` list / string caps; `MAX_ENTRY_FILE_BYTES` 1 MiB. | Entry-count bound works (100 000 real files -> `deferred=True` in 0.01 s). Bound bypass: `adv5_dos.py` case C (30.46 s) and `adv10_nested.py` (3.08 s, `deferred=False`). |
| M-8 dangerous mode bits | **Verified** | `constants.py:44` masks; `types.py:156` `mode <= 0o777`; capture `restore.py:118` masks `STORED_MODE_MASK`; restore `:178` masks `0o755`. | Corpus `mode_setuid_4777.json` -> `corrupt_entry`; `model_construct` bypass with `0o4777` still restored as `0o755` (`adv3_restore.py`). SEC-07 (path chmod) and SEC-16 (mode 000) are minor. |
| M-9 unsafe deserialization | **Verified** | grep over `cache/`: no `pickle` / `marshal` / `shelve` / `yaml` / `eval` / `exec`; only `json.loads`; one `subprocess.run([real, "--version"])` (list argv, `shell` unset) plus the `GitRepo` runner. | `test_ast_guard.py` 18 passed; guard gaps are SEC-12. |
| M-13 hostile-data crash | **Verified** | `types.py:225-243` total parser (`except Exception` -> `CacheIntegrityError`); bounded models; `store.py:241-244` layout parse also catches `RecursionError`. | `adv1_parse.py`: 17 hostile inputs (100 000-deep arrays and objects, 100 000-digit int, NaN / Infinity, negative size, setuid mode, naive datetime, NULs, invalid UTF-8, 5 MB string, lone surrogate) all `corrupt_entry`, each < 6 ms; 20 000 random byte mutations: 0 escapes. Corpus tests: 30 passed. Lax coercion: SEC-13. |
| M-14 execution sink via restore | **Verified on case-sensitive filesystems; SEC-06 / SEC-15 gaps** | Key build `keys.py:122-123`; restore `restore.py:150-151`; `safeio.py:207-215`; also an output symlinked into `.git/hooks` is refused after resolve. | `adv3_restore.py` / `adv8_keys.py`: `.git/hooks/*`, `.claude/*`, `CLAUDE.md`, `docs/CLAUDE.md`, `.github/*`, `.orchestrator/runs/*` refused at both stages; `hk/pre-commit` (symlink to `.git/hooks`) -> `sensitive_output`. |

## Other checklist areas

- **Destructive maintenance / `verify` deletes nothing: verified.** `store.py:819-863` only reads; `adv4_maint.py` snapshot (type, mode, mtime, size, sha, link target of every file) of a deliberately messy cache (corrupt and mismatched entries, expired entry, orphan, corrupt and missing blobs, symlinks, junk, stale tmp, trash dir, foreign `v9`) was byte-identical before and after `verify()` (`changed cache tree? False`, outside dir unchanged); the same for `stats()` and `prune(dry_run=True)`. Real `prune` removed only invalid entries, one orphan, one stale tmp, one trash dir, left symlinks, the foreign tree and the outside directory alone.
- **CacheUnsafePathError never evicted: verified.** `_checks` precedes every unlink (`store.py:226-231`); `restore.py` catches only `(OSError, SafeIOError)` (`:183`) so the store's `CacheUnsafePathError` propagates (documented, test `test_unsafe_path_from_the_store_propagates_unchanged_and_cleans_up`); a prune over a symlinked shard removed nothing and left the outside entry in place. A final-component symlink is `corrupt_entry` and eviction unlinks only the link (covered by `test_final_component_symlink_entry_is_corrupt_and_eviction_unlinks_only_the_link`). SEC-08's `IsADirectoryError` is the one unsafe-ish case that escapes as a raw `OSError`.
- **Repository detection stops at the workspace root: verified** (`repo_state.py:find_git_toplevel`, U-G7). `adv6_git.py` A: workspace nested inside a repo with a hostile `core.fsmonitor`: `toplevel=None`, heads `{}`, snapshot empty, marker NOT created.
- **Key poisoning / collision / completeness: verified** except SEC-04 / SEC-11. `adv8_keys.py`: extra_args, model, effort, max_turns, working_dir, disallowed_tools, ANTHROPIC_MODEL, HEAD, include_repo_heads, input content, `CLAUDE.md`, `.claude/skills/*`, a prior output's content each change the key; the key is invariant to the absolute workspace path. Canonical JSON (`types.py:50-54`, sorted keys, ASCII, `allow_nan=False`) is injective; duplicate or `./a` outputs and traversal inputs are uncacheable.
- **Secrets leakage into entries: clean.** `adv8_keys.py`: an agent with a secret in `extra_args` and `ANTHROPIC_API_KEY` in the environment produced a summary / entry without the secret. The entry carries only paths, model / effort / max_turns, sha256 display digests, repo heads and 12-char component digests (`keys.py:67-93`); the fingerprint env allowlist contains no secret names. Residual (documented): sha256 of low-entropy input files in `key_summary.digests`.
- **Subprocess argv / env:** both subprocess sites use list argv, no shell. `git` goes through `GitRepo` (no network verbs, alias guard, empty hooks dir, `LC_ALL=C`, 10 s timeout, `GIT_OPTIONAL_LOCKS=0`); see SEC-03 / SEC-04 for the config and environment gaps. `claude --version`: SEC-10.
- **Supply chain:** the core set adds no dependency (`pyproject.toml` / lockfile untouched in the diff; pydantic was already present). The new `StrictBool` / `AwareDatetime` use is current API. CI wiring (pip-audit, gitleaks, Semgrep) is outside this task's diff.
- **Trigger authn/authz:** no trigger, control API or network surface in the core set (the `ao cache` CLI is a stub, `cache/cli.py` 19 lines). N/A for G1a; G1b/G2.

## Commands and results

All run from `/usr/avadhoot/mounted/agent-orchestrator/.claude/worktrees/agent-a18ce2c08e42a3a5a` with `.venv/bin/python`.

| command | result |
|---------|--------|
| `.venv/bin/python -m pytest -q -p no:cacheprovider tests/cache` | **870 passed in 8.39 s** |
| `... tests/cache/test_ast_guard.py` (AST guard, M-9) | 18 passed |
| `... tests/cache/test_keys_golden.py` (GV-1) | 2 passed |
| `... tests/cache/test_store_core.py -k "unsafe or symlink or ST15"` | 12 passed |
| `... tests/cache/test_repo_state.py` (incl. U-G7) | 43 passed |
| `... tests/cache/test_eligibility.py` (incl. U-E27) | 57 passed |
| `... tests/cache/test_settings.py` (incl. U-S4) | 47 passed |
| `... tests/cache/test_types.py -k corpus` (hostile corpus) | 30 passed |
| `... tests/cache/test_store_maintenance.py test_store_race.py test_restore.py test_safeio.py test_hashing.py` | 235 passed |

Scratchpad adversarial scripts (`/tmp/claude-1000/-usr-avadhoot-mounted-agent-orchestrator/c3bbc271-ec14-4ba6-9c94-0ca585be9938/scratchpad/`):

| script | what it shows |
|--------|---------------|
| `adv1_parse.py` | 17 hostile parses + 20 000-mutation fuzz: 0 escapes of anything but `CacheIntegrityError` |
| `adv2_store_restore.py` | M-4 symlink / FIFO / dir-at-blob-path cases (SEC-08) |
| `adv3_restore.py` | M-1, M-5, M-8, M-14, hardlink, partial commit (SEC-07), capture through swapped parent (SEC-05) |
| `adv4_maint.py` | `verify` / `stats` / dry-run byte-identical; prune and clear never follow links; `ClearReport` miscount (SEC-17) |
| `adv5_dos.py` (N=100000, M=3000) | symlink and real-file bounds; blob walk 0.20 s; **mark-phase 30.46 s** (SEC-01) |
| `adv10_nested.py` | 300 nested sparse files: 3.08 s, `deferred=False` (SEC-01) |
| `adv6_git.py`, `adv7_filter.py` | nested-workspace clean; fsmonitor executed by `status` (SEC-03); `GIT_DIR` redirect (SEC-04); `filter.clean` not executed in my probe |
| `adv8_keys.py` | key completeness (SEC-11), hostile paths, FIFO input, secrets absent from entries |
| `adv9_misc.py` | key / sha validation (M-2), control-char stripping (SEC-14), case-sensitive matcher (SEC-06) |
| `adv11_m3.py` | four blob-corruption shapes all `blob_corrupt evict=True` |

## CI and process (core set)

- The G1a re-verify for SEC-01 should be an automated test (ADV-7 extension: planted `entries/v9` and nested v1 trees must defer), not only a manual rerun.
- Add to CI for the package: `bandit -r src/agent_orchestrator/cache` (would also catch the `subprocess` and `os.chmod` patterns), `pip-audit` (already requested by CLAUDE.md), and `gitleaks` on the PR diff; `ruff` with `S` rules for the cache package.
- Extend the AST guard (SEC-12) so M-9 does not depend on reviewers noticing a new import.
- Record SEC-03 / SEC-04 outcomes in HLD 7.7 (either fixed or an explicit residual row "git config exec sinks and inherited `GIT_*` environment"), and amend D29 for case-insensitive filesystems (SEC-06).

## Follow-ups

| item | owner | when |
|------|-------|------|
| SEC-01 fix + ADV-7 extension, then G1a re-verify | T-HjxNQ0 owner (store maintenance) | before G1b starts (the coordinator will call `maybe_enforce_limits`) |
| SEC-03 / SEC-04 git environment hardening | T-uoYW6b / repo_state owner | before G1b (probe is used by the coordinator) |
| SEC-05 capture re-validation | T-u3jG8F owner (restore) | with SEC-07; G1b to confirm the engine's settle-time output check order |
| SEC-06 / SEC-15 sensitive-path set and case folding (ADR-0019 addendum) | architect + T-uoYW6b owner | G2 at the latest |
| SEC-08 / SEC-09 store self-heal and error mapping | T-U7ckfd owner | G1b |
| NITs SEC-10 .. SEC-21 | batch into one hygiene ticket | G2 |

## Re-verification (delta, after remediation `763375f` code / `1e65fc3` docs)

- Reviewer: dev-security, 2026-10-05. Scope: `git diff a359fb9..HEAD -- src/agent_orchestrator/cache tests/cache`. No source or test file modified.
- Method: original adversarial scripts re-run unchanged from the scratchpad, plus one new script (`rv_worst.py`) for the bound's worst case.

### Verdict: **PASS** (0 open MUST-FIX)

SEC-01 is fixed and M-7 now holds. SEC-02..09 are fixed (one documented residual each for SEC-03 and SEC-07). The deferred items are
hygiene or accepted-residual; none is a MUST-FIX in disguise. One new NIT (RV-1) below.

### SEC-01 / M-7: re-run of the original reproductions

| scenario (script) | before | after |
|---|---|---|
| 3000 sparse 1 MiB files under `entries/v9/aa`, over `max_bytes` (`adv5_dos.py` C) | 30.46 s, inline prune ran | 0.00 s, `deferred=True` |
| 300 sparse 1 MiB files nested under a v1 shard (`adv10_nested.py`) | 3.08 s, `deferred=False` | 0.00 s, `deferred=True` |
| 100 000 symlinks / real files in a v1 shard, 100 000 blob files (`adv5_dos.py` A, B) | bounded | still bounded: 0.04 / 0.01 / 0.11 s, `deferred=True` |
| just under every bound: 60 x 1 MiB random files + 4 000 empty files under `entries/v9`, 30 000 blobs, over `max_bytes` (`rv_worst.py` W1, run while the 11-minute suite loaded the box) | n/a | 0.97 s, `deferred=False`: the worst case that is still allowed to run inline |

Code evidence: `store.py:158-200` `_Budget` (items / files / bytes / blobs), `_inline_budget` reads the constants at call time;
`_walk_entry_files` (`:591`) charges every directory entry and every regular file of ANY version and depth BEFORE reading it;
`_referenced_blobs`, `_scan_blobs`, `_tmp_files`, `_trash_dirs`, `_iter_entries` each take a fresh per-phase budget (`:781` `_prune(new_budget=...)`);
`_scan_sizes` (`:886`) uses the same walker, so the trigger and the prune now cover the same tree; `_BudgetExceeded` is raised before any deletion
(all read phases precede the delete block), so a deferral never leaves a partial prune; `maybe_enforce_limits` (`:907`) turns it into
`PruneReport(deferred=True)` and clears `_approx_total` so the next store re-checks with a cheap lstat-only scan. Explicit `ao cache prune` keeps
`_Budget()` (unbounded) by design. The bound is count/byte based, not wall-clock: the allowed maximum is
100 000 directory entries, 5 000 files / 64 MiB read per phase, 50 000 blobs, which measured about 1 s. Acceptable; a wall-clock deadline would
be a nice extra, not a requirement.

### Spot checks (SEC-02..09), each by the original script or a direct read

| id | result | evidence |
|---|---|---|
| SEC-02 / S-2 | Fixed | blob and walk bounds above (`INLINE_PRUNE_MAX_BLOBS` 50 000, `INLINE_PRUNE_MAX_WALK_ITEMS` 100 000); every `stat` in `_scan_blobs`, `_scan_sizes`, `_walk_entry_files`, `_tmp_files` tolerates `FileNotFoundError` |
| SEC-03 | Fixed, residual documented | `adv6_git.py` B: `core.fsmonitor` marker NOT created by `WorktreeProbe.snapshot`; `core.fsmonitor=false` + `core.untrackedCache=false` injected via `GIT_CONFIG_COUNT` (`repo_state.py:98-112`). Residual: `filter.<x>.clean` / `diff.<x>.textconv` through in-repo `.gitattributes`; my original probe did not execute a filter either |
| SEC-04 | Fixed | `adv6_git.py` C: with `GIT_DIR` exported, `RepoHeadReader` returned the workspace HEAD (`3fbd539fa6`), not the other repo's; `GIT_SCRUBBED_ENV_VARS` + `GIT_CONFIG_KEY_/VALUE_` prefix scrub |
| SEC-05 | Fixed | `adv3_restore.py`: capture through a swapped parent symlink -> `StoreSkip(output_not_regular_file)`, no outside bytes stored (`restore.py:154`) |
| SEC-06 | Fixed | `adv3_restore.py`: `.GIT/hooks/pre-commit` and `Claude.md` -> `refused:sensitive_output` (was RESTORED) |
| SEC-07 | Fixed, residual documented | mode set by `fchmod` on the open fd after hash verification (`restore.py`, phase 1); hard-link backups + `_rollback`: the forced 2nd-rename failure now leaves `a.md: OLD-A, b.md: OLD-B` (was a mixed workspace). Residual: no hard links -> that one file stays replaced (documented). Leftover parent dirs from `ensure_dir_chain` unchanged (harmless) |
| SEC-08 | Fixed | `adv2_store_restore.py`: a planted directory at `blobs/xx/<sha>` is renamed into `trash-*` (no recursion inline) and the verified blob installed, `new=True`, `has_blob` True; `has_blob` requires `S_ISREG`; `_dedupe_onto_existing` requires regular + exact size; `delete_blob` on a directory is `CacheUnsafePathError`, not a raw `IsADirectoryError` |
| SEC-09 | Fixed | `read_blob` maps only `NotRegularFileError` to `blob_corrupt`; other `OSError` -> `CacheError(store_error)`; restore maps it to a non-evicting `RestoreMiss(store_error)` (`CacheUnsafePathError` still propagates, D33) |

### Deferred items: justification

All deferred items (SEC-10..21, S-8/SEC-15 sensitive-name list, S-9 attribute-call AST guard, SEC-11 key allowlist, SEC-13 strict models,
SEC-14 CLI display, SEC-16, SEC-17, SEC-18, SEC-19) need either an architect decision (D29 list, GV-1 golden vector change), are same-uid
poisoning already accepted in the threat model, are display/report-only, or are code with no demonstrated exploit path on a current caller
(restore destinations come from the spec; the cache is not yet wired to the engine). None gives an unauthenticated or cross-trust-boundary
execution, read-outside-workspace, or unbounded-work path. Confirmed still demonstrable but accepted: `.gitlab-ci.yml`, `.githooks/*`,
`Jenkinsfile`, `.circleci/*`, `.vscode/tasks.json` etc. restore (SEC-15) and mode `0o000` restores unreadable (SEC-16). Recommendation: the
SEC-15 list decision should land before G2/release, since the cache then restores spec-declared outputs into CI-config paths.

### New finding

| id | severity | file:line | issue | fix |
|----|----------|-----------|-------|-----|
| RV-1 | NIT | `store.py:849-850` (`_prune` trash removal via `_rmtree_checked`) | The deletion of stale `trash-*` directories is not budgeted. A planted `trash-x` with 200 000 empty files made the inline prune run 1.23 s (`rv_worst.py` W2); a 3 000-deep chain was fine on Python 3.12. Cost is about 1x the planted inode count (no amplification), and the files are same-uid attacker-created, so this is not a SEC-01-class issue. | Optionally skip inline trash removal beyond N directories and leave it to `ao cache prune`; or count `os.scandir` of each trash dir against the item budget. |

### Commands and results (worktree `.../agent-a18ce2c08e42a3a5a`, `.venv/bin/python`)

| command | result |
|---|---|
| `pytest -q -p no:cacheprovider tests/cache tests/test_spawn_provenance.py tests/test_nfr2_regression_gate.py` | **945 passed** in 10.25 s |
| `ruff check src tests` | All checks passed |
| `mypy src tests/cache` | 4 errors, all in `src/agent_orchestrator/_version.py:24-27` (`None` assigned to `str`/`bool`); the file is untouched by this epic (`git diff 0b980e3..HEAD` empty), so pre-existing and unrelated to the cache; no errors in `cache/` or `tests/cache` |
| `pytest -q -p no:cacheprovider` (full suite) | **6081 passed, 10 skipped, 0 failed**, 1 warning, 691 s (prior 6027 / 10) |

Residual follow-ups: SEC-15 list decision (architect) before G2; keep ADV-7 style planted-tree tests (present: `test_store_hardening.py`) in CI; record SEC-03
filter/textconv residual in HLD 7.7; mypy `_version.py` errors belong to a separate hygiene fix.
