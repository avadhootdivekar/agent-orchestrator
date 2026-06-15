---
name: repo-intel
description: Use the `ri` (repo-intel) CLI as a repo intelligence layer for exploration, code search, context retrieval, and ticket/design/ADR management. Reach for this BEFORE manual grep/find sweeps or large read passes — `ri search` returns overlay-annotated, token-budgeted hits, `ri context` assembles a budgeted context bundle for a question, and `ri run` executes independent commands in parallel from one JSON spec. Use when searching the codebase, gathering context for a question, locating which files/tickets own a symbol, running several build/test/lint commands at once, or creating tickets/designs/ADRs in the meta/ overlay.
---

# Skill: Repo Intelligence (`ri`) layer

`ri` (alias of `repo-intel`) is a portable knowledge-overlay CLI installed at `~/.local/bin/ri`. It maintains a `meta/` overlay (tickets, designs, ADRs, agent instructions) and provides overlay-aware **search**, **context retrieval**, **indexing**, and **parallel command execution**. Use it to do your job — exploration, retrieval, context — with fewer tokens and fewer round-trips than manual `grep`/`find`/`Read` sweeps.

`ri --help` lists subcommands; `ri <cmd> --help` documents each. The overlay lives at `meta/`. Heavy artefacts (index, caches) live under `$XDG_CACHE_HOME/repo-intel/<repo-id>/` and are not committed.

> **Self-improvement (do this):** If you find this skill doc is missing or inaccurate about `ri` behavior, update this file. If you notice a bug, gap, or genuinely useful `ri` feature/improvement while working, report it to the user in your output when appropriate (skip if the user asked for output in a specific format).

---

## ⚠️ This repo — current state & footguns

Re-check with `ri doctor` / `ri index status` if behavior differs from this note.

- **Overlay may not be initialized.** This repo's working tickets/learnings live under `ad/` (e.g. `ad/tickets/`, `ad/learnings.md`), not the `meta/` overlay. If `ri` reports the overlay is missing and you genuinely want overlay features, run `ri init` — but day-to-day ticket work here uses `ad/`, so prefer that unless told otherwise.
- **`ri search` is the reliable workhorse** — it uses `rg` (ripgrep) / `sg` (ast-grep) and searches all files regardless of index state. Best for precise symbol/string lookup.
- **`ri context` / `ri index` need `bm25s` + a built index.** If unavailable, fall back to `ri search`. Build/refresh with `ri index build` and check freshness with `ri index status`.
- **Index footgun — generated/minified files.** Keep generated, vendored, or minified files (e.g. `*.min.js`, build output, `coverage/`, `reports/`) out of the index `include:` globs — duplicate ctags can crash `ri index build` with a `UNIQUE constraint failed: corpus.doc_id` error.
- **DO NOT run `ri sync-agents` in this repo.** `sync-agents` regenerates `CLAUDE.md` (and `.github/copilot-instructions.md`) as thin shims that forward to `AGENTS.md`. In this repo the **reverse** is true by design: `CLAUDE.md` and `.claude/` are hand-authored and authoritative, and `AGENTS.md` / Copilot / Cursor files are thin pointers *to* them. Running `sync-agents` would **overwrite** the authored `CLAUDE.md`. A `ri doctor` `shims: stale` report is expected and is NOT a reason to run sync.

---

## Quick decision guide

| You want to… | Use |
|---|---|
| Find where a symbol / string / pattern lives | `ri search "QUERY"` (text) or `--mode struct` (AST) |
| Know which ticket/ADR/design owns a hit | `ri search` (provenance is annotated inline) |
| Run 2+ independent build/test/lint commands | `ri run --stdin` with a JSON spec |
| Assemble budgeted context for a question | `ri context "..."` (needs bm25s + built index) |
| Check overlay/tooling health | `ri doctor` |
| Create a ticket/design/ADR in the overlay | `ri ticket new` / `ri design new` / `ri adr new` |

---

## `ri search` — overlay-aware code search (primary tool here)

Searches code and annotates each hit with the owning `meta/` artefacts (ticket/ADR/design/owner). `--mode text` uses ripgrep; `--mode struct` uses ast-grep (needs `--lang`).

```bash
ri search "QUERY"                       # text search, all files
ri search "QUERY" -g "src/**/*.py"      # scope to a glob (repeatable)
ri search "QUERY" -l                      # files-only (cheap: just paths)
ri search "QUERY" -c                      # count per file
ri search "QUERY" -C 3                     # 3 context lines around each hit
ri search "QUERY" -b 1500                  # cap output to ~1500 tokens
ri search "QUERY" -f json                  # JSON bundle (for parsing)
ri search "PATTERN" --mode struct --lang python   # structural / AST match
```

| Flag | Default | Meaning |
|---|---|---|
| `--mode text\|struct` | `text` | ripgrep vs ast-grep |
| `-g GLOB` | all | path glob filter (repeatable) |
| `--case smart\|sensitive\|insensitive` | `smart` | case matching |
| `-i` | — | shorthand for `--case insensitive` |
| `-C N` | `0` | context lines above & below |
| `-l` | — | files-only (no line text) |
| `-c` | — | count only |
| `--lang LANG` | — | language hint; **required** for `--mode struct` |
| `-n N` | unlimited | cap returned hits (post-engine) |
| `-b N` | unlimited | token budget — truncate hits to ~N tokens |
| `-f json\|text` | `text` | output format |

**Token discipline:** start with `-l` or `-c` to scope, then re-run with `-C` / `-b` only on the files you care about. Pass `-b` to keep large searches from blowing the context window.

**Exit codes:** `0` ok · `1` bad query / missing `--lang` for struct · `2` overlay missing (`ri init`) · `4` engine binary absent (see stderr install hint).

---

## `ri run` — parallel command execution (DAG-aware)

Run two-or-more independent commands in one call instead of sequential shell round-trips. Pipe a JSON spec via `--stdin` and parse the single JSON response.

```bash
echo '{"commands":[
  {"id":"tests","cmd":"pytest -q","detail":"summary"},
  {"id":"lint","cmd":"ruff check .","detail":"summary"},
  {"id":"types","cmd":"mypy .","detail":"summary"}
]}' | ri run --stdin --cwd /usr/avadhoot/mounted/agent-orchestrator
```

| Field | Required | Default | Meaning |
|---|---|---|---|
| `id` | yes | — | unique id in this spec |
| `cmd` | yes | — | shell command (run via `sh -c`) |
| `detail` | no | `full` | `status` \| `summary` \| `full` |
| `depends_on` | no | `[]` | ids that must succeed first (skipped if a dep fails) |

**Detail levels:** `status` = exit_code/status/duration only · `summary` = + first ~2000 chars of combined stdout+stderr · `full` = + complete stdout & stderr (separate fields). Use `status`/`summary` to keep token cost low.

**Output:** `{ "summary": {total,succeeded,failed,skipped,duration_s}, "results": [{id,status,exit_code,stdout,stderr,duration_s, reason?}] }`. `status` ∈ `success|failed|skipped`.

**Exit codes:** `0` all ran (some may skip) · `1` ≥1 failed · `2` invalid spec (bad JSON, cycle, unknown/duplicate dep).

Use `--cwd` so every command runs from the repo root regardless of where `ri` is invoked.

---

## `ri context` — token-budgeted context bundle

Turns a natural-language question into a ranked, budget-capped bundle of code + overlay snippets (needs bm25s + a built index):

```bash
ri context "how does the scheduler resolve the DAG?"   # prompt-formatted output
ri context "..." -f json          # structured bundle
ri context "..." -b 8000          # token budget (0 = unbounded)
ri context "..." -k 50            # BM25 candidates retrieved before ranking
ri context "..." --no-build       # don't auto-build a stale index; exit 3 instead
```

Auto-builds a stale index unless `--no-build`. **Exit codes:** `0` ok · `1` no usable terms · `2` overlay missing · `3` stale index with `--no-build` · `4` backend (bm25s) absent.

---

## `ri index` — build / inspect the index

```bash
ri index status                 # freshness, file/symbol/corpus counts, STALE|FRESH
ri index build                  # incremental refresh (needs bm25s)
ri index build --rebuild        # ignore cache, full rebuild
ri index build --branch NAME    # build for a specific branch
```

`ri index status` is a safe, cheap health check even without bm25s (reports `state: STALE` and zero counts when unbuilt).

---

## `ri doctor` — overlay & tooling health

```bash
ri doctor
```

Checks `meta/` presence, AGENTS.md, ticket IDs/front-matter, design/ADR numbering, search engines (`rg`/`sg`), and index backends (`bm25s`/tree-sitter/ctags). Run it first when `ri` behaves unexpectedly. Note: a `shims: stale` FAIL is **expected** here and must not be "fixed" with `sync-agents` (see footguns).

---

## Overlay management (`meta/`) — use only when working in the overlay

| Command | Effect |
|---|---|
| `ri ticket new <title> [--type TYPE] [--epic E-XXXXXX]` | new ticket in `meta/tickets/` |
| `ri ticket list [--status S] [--type T] [--owner O] [--include-epics]` | list overlay tickets |
| `ri ticket update <id> ...` | update ticket fields |
| `ri design new <title>` | numbered design in `meta/designs/` |
| `ri adr new <title>` | numbered ADR in `meta/designs/adr/` |
| `ri init [--force]` | bootstrap overlay |
| `ri sync-agents` | regenerate agent shims from `AGENTS.md` — **do not run in this repo** |

Overlay conventions: ticket IDs are Crockford-Base32 (`E-`/`T-`/`S-XXXXXX-slug`); designs/ADRs are sequentially numbered and never renumbered after publication; lifecycle `backlog → ready → in_progress → in_review → done|cancelled`. This repo's day-to-day tickets live in `ad/tickets/`, separate from this overlay.
