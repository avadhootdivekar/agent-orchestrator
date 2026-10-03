# Usefulness signals — HLD + LLD (E-Us9Kd4, 2026-10-02)

Goal: go beyond pass/fail to tell whether workflows/models/efforts produce USEFUL work. Local-first: everything is derived from, or stored in, the workspace (`.orchestrator/runs/<id>/`); no network, no telemetry. Extends `usage-analytics.md` (read it first). Backward compatible: every new field defaults to unknown/None; old runs load unchanged.

Three signal families, one data model, one rollup (`ao report-usage`):

| Part | Signal | Source | Objective? |
|------|--------|--------|-----------|
| 1 | Verdicts (reviewer PASS/FAIL, overseer checkpoint/final-verify vs charter) | non-gating sidecar JSON written by agents | agent-judged |
| 2 | Diff survival (`ao report-survival`) | git only | objective, code work only |
| 3 | User feedback + implicit signals | `feedback.json`, state, git | subjective / derived |

## Part 1 — generic verdict capture
**Convention (no new gate).** `usage.verdict_path_for(task)` resolves the sidecar path, first match wins:
1. `TaskSpec.verdict_path` (new optional field; workspace-relative; explicit, works for any custom workflow; NOT a declared output so it can never fail a run).
2. A declared output whose basename is `verdict.json` or ends `-verdict.json` (the file itself is the verdict; the author already accepted it as a declared output — this is how overseer checkpoints `outputs/checkpoints/ck-NN/verdict.json` are picked up with zero template change).
3. Sibling table `{"review.md": "review-verdict.json", "verify.md": "verify-verdict.json"}` applied to a declared output (keeps routed-runner working; adds overseer final-verify).
Recorded at dispatch on `TaskRunState.verdict_path` (`review_verdict_path` stays populated for review tasks — back-compat). Read lazily at report time via `artifacts.read_control` (bounded); missing/malformed = "no verdict".

**Verdict kinds** (discriminated by content, `usage.parse_verdict`):
- `review` — `{"verdict":"PASS"|"FAIL","findings":{critical,major,minor},"must_fix":n}` → attributed to producers of the reviewer's inputs (unchanged).
- `checkpoint` — overseer `verdict.json` (`ao.overseer.verdict/v1`): `decision`, `criteria[].status` (met/unmet/deferred), `alignment[].status`. Run-level only.
- `final_verify` — new sidecar `outputs/final/verify-verdict.json`, `{"schema":"ao.overseer.final-verify/v1","asks":[{"id":"A1","verdict":"met"|"partial"|"not_met"}]}`; written by the `final-verify` agent per `40-final-verify.md`; optional, never a gate.
**Report**: `UsageReport.outcomes: list[RunOutcome]` (per run: last checkpoint decision, criteria met/unmet/deferred, alignment counts, final-verify asks met/partial/not_met) rendered as an "Outcome vs charter" section; `verdicts found x/y` stays and now covers all kinds.

## Part 2 — diff survival (`survival.py`, `ao report-survival`)
**What is measured.** Per task (where attributable) and per run: lines added by the task's change vs how many of those lines are still present at a reference (`--ref`, default each repo's current `HEAD`).
**Attribution** (per repo key; most reliable first):
1. *isolation* — `RunState.task_integration[task].squash_commits[repo]`: the task's own squash commit; patch = `sha^..sha`.
2. *serial* (no isolation) — new record: `RunState.git_repos` (repo_key→toplevel) + `RunState.git_start_heads` (written once at run start for every git repo) and `TaskRunState.end_heads` (repo_key→HEAD at settle). Task patch = `prev_head..end_head` where `prev_head` is the previous settled task's head (or the start head). Only when the task's [started_at, ended_at] window overlaps no other task that moved HEAD; otherwise the commits are `ambiguous` and counted at run level only.
3. *time-window* (old runs, nothing recorded) — commits on the workspace repo with commit time in [started_at, updated_at]; run level only, marked low-confidence.
**Survival metric** (deterministic, git-only; `git diff`/`cat-file`, no blame dependency since squash/rebase rewrite shas): for each file in the patch, the multiset of non-blank ADDED lines; survived = Σ min(added_count(line), count(line) in the file at ref). Renames between the task commit and ref are followed via `git diff -M --name-status commit ref`; deleted file ⇒ 0. Binary files and files over `MAX_FILE_LINES`, and commit/file counts over caps are skipped and reported (`truncated`).
**Flags.** `likely_worthless` = lines_added ≥ `MIN_LINES_FOR_FLAG` (10) and survival < `LOW_SURVIVAL_RATE` (0.10); `reverted` = a commit on ref's history whose message contains `This reverts commit <sha>` for one of the task's commits (also detects reverts of the squash). Run-level read-only tasks (no commits) are "n/a", never flagged.
**Output.** `ao report-survival [--workspace W] [--run-id R...] [--ref REF] [--json]` table (run/task, attribution, commits, files, lines added, survived, rate, flags) + `--json`. `usage.py` joins per-(agent,model,effort) `lines_added/lines_survived/survival_rate/survival_tasks` when survival is requested (`--with-survival`, dashboard toggle); git failures degrade to "survival: unavailable (reason)" and never break report-usage.
**Limits.** Content match, not true blame: a line identical to a pre-existing line can inflate survival; moved code across files looks like loss; formatters rewriting lines look like loss; measured at report time so it is ~100% right after a run and only becomes informative as later work lands (re-run it later). Net-per-patch: lines added then removed inside one range don't count at all. Non-code work (docs-only) is measured the same way but is a weak signal.

## Part 3 — local feedback (`feedback.py`)
**Storage** `<ws>/.orchestrator/runs/<run_id>/feedback.json`, atomic write (tmp+`os.replace`, under a lock for the read-modify-write):
`{"schema_version":1,"entries":[{"ts","scope":"run"|"task","task_id?","rating":"good"|"ok"|"bad","reasons":[wrong|incomplete|unnecessary|too-costly|needed-hand-fixing],"note?","source":"cli"|"dashboard"}]}`.
Bounds: note ≤ `MAX_NOTE_CHARS` (2000), ≤ `MAX_ENTRIES` (500; oldest dropped is NOT done — adding beyond the cap is rejected), reasons subset of the enum (deduped), task_id must exist in the run's state, run id matches `^[A-Za-z0-9._-]{1,128}$`. History is kept; the LATEST entry per (scope, task_id) is "effective". A task's effective rating = its task entry, else the run entry.
**CLI** `ao rate <run-id> good|ok|bad [--task ID] [--reason R]... [--note T]`, `ao rate <run-id> --show`. The CLI and the dashboard POST both call `feedback.add_feedback` — one validator, one writer.
**Implicit signals** (`implicit_signals.py`, computed on demand, nothing stored, only what data supports):
- `landed`: isolation integration head is an ancestor of the repo's HEAD → landed; else `not_landed`; None when unknown.
- `followup_commits`: commits after the run's last head, touching files the run changed, NOT recorded by any ao run in the workspace (so a human/other-tool edit) — approximates "needed hand fixing".
- `reverted_commits`: from survival.
- `run_status` (cancelled = killed) and `tripped_breakers` count — pause/kill as recorded.
Non-MVP (not derivable reliably today): re-run of a similar prompt (the prompt/hash is not recorded in `RunState`), explicit pause events (no event log), human-vs-agent authorship.
**Rollup join** (per group): effective ratings per task → `fb_good/ok/bad`, `fb_unnecessary`, `fb_rated_tasks`, coverage `runs_rated x/y`. Reviewer-error candidates: producer task with verdict PASS but effective rating bad → `false_pass_candidates`; verdict FAIL but rating good → `false_fail_candidates`; denominator `verdict_rated_pairs`. Flags only when sample ≥ `MIN_SAMPLE_FOR_FLAG` (5): "user-rated bad ≥50%", "reviewer disagrees with user ≥50%".

## Dashboard
Backend (`ui/service.py` + thin routes in `ui/app.py`, reuse `usage.py`/`feedback.py`/`survival.py`):
- `GET /api/usage?run_id=R&run_id=R2&survival=bool` → report JSON incl. computed rates.
- `GET /api/runs/{id}/feedback` → `{entries, effective}`; `POST /api/runs/{id}/feedback` body `{scope,task_id?,rating,reasons[],note?}` (source forced `dashboard`); 400 on validation, 404 unknown run/task.
- `GET /api/runs/{id}/signals?survival=bool` → implicit signals + per-task survival (on demand).
Security: existing `SecurityMiddleware` covers Host/Origin/JSON content-type for the POST; ids validated by the shared validators (no path traversal); body size bounds enforced by pydantic; the server never takes a path from the client. Frontend: new "Usage" tab (group table, run filter, survival toggle), RunDetail rating control + per-task thumbs/reason tags/note, implicit-signal/survival panel; built with the existing `ui/` Vite pipeline into `ui/static`.

## Early-gate review outcome (reviewer, 2026-10-02) — incorporated
- Serial attribution under `max_parallel>1`: also record `TaskRunState.start_heads` at dispatch; overlapping windows => ambiguous; uncommitted work => n/a.
- Run-start git record is separate from the isolation path, written once, never overwritten on resume.
- Squash sha may be rewritten/unreachable: prefer integration `head_from..head_to` range, fall back to sha; sum multiple squashes; low confidence when not an ancestor of ref. Time-window attribution is run-level only and never joins into groups.
- Metric ignores trivial lines (`MIN_LINE_CHARS`); all caps are named constants.
- Verdict rule 2 only counts files that parse to a known kind; `verdict_path` rejects `..`/absolute; overseer final-verify declared outputs stay exactly `[verify.md]` (pinned by a test).
- Reviewer-error candidates use EXPLICIT task-scope ratings only (a run-level bad must not mark every PASS a false-pass); flags say "disagrees", they are candidates, not an error rate. Last verdict per (producer, reviewer) counts.
- Feedback: cross-process flock + fsync + same-dir tmp; corrupt/unknown-schema files raise, never overwritten; run id rejects `.`/`..` + containment check.
- Dashboard: survival computed off the event loop with caps; `/api/usage` caps run_id count; notes rendered as text only.

## Limits (read before acting on any of it)
- Reviewer PASS/FAIL can be a false PASS or false FAIL — hence the reviewer-error measure, itself only a candidate list (the user may be wrong too).
- A correct PASS on a task that should never have existed is invisible to pass/fail; only the `unnecessary` reason and low survival hint at it.
- User feedback has response bias (people rate extremes, rate rarely) and is subjective; coverage is always shown ("rated x of y runs") and flags need ≥5 samples.
- Survival is a heuristic (see Part 2), implicit signals are circumstantial. None of this is an A/B test.

## Non-goals / later
LLM-judge or self-review scoring; reviewer calibration against graded `ao-bench` tasks; N-repeat/paired comparison in `ao-bench`; opt-in telemetry; prompt-similarity re-run detection (needs prompt hash recording); human-vs-agent commit authorship.

## Follow-up (2026-10-02)
**A — `record_git_heads` opt-out (FR-12).** One boolean, default ON, three-layer precedence CLI (`--record-git-heads/--no-record-git-heads` on `ao run`/`ao resume`) > env `AO_RECORD_GIT_HEADS` > `.ao/config.yaml` `record_git_heads`. When OFF the engine makes no head-recording git calls (`git_repos`/`git_start_heads`/`start_heads`/`end_heads` stay empty); isolation `landed_ranges` (free, reuses integration heads) is still recorded. `ao report-survival` falls back isolation ranges -> serial heads -> time-window and says explicitly that heads were intentionally not recorded. Threaded through the Orchestrator constructor/run settings, not a global.
**B — run prompt in RunState (FR-13).** **Implemented 2026-10-02:** `run_prompt.py` (capture), `cli._capture_run_prompt`, `Orchestrator(run_prompt=)`, `ui/runs.py` (`prompt`, `prompt_changed_since_start`, `prompt_preview`), `ui/src/components/PromptPanel.tsx`. The CLI layer (never the engine; NFR-1) reads the workflow's `prompt_path` at run start and hands the value to the Orchestrator, which stores `RunState.prompt: RunPrompt | None` = `{text (<= 64 KiB, truncated flag + original chars), source: cli-prompt|cli-prompt-file|workflow-file, path, sha256 of FULL text, captured_at}`. Resume preserves the recorded prompt; the API surfaces `prompt_changed_since_start` when the file's sha now differs. Dashboard: "Prompt" panel in run detail (text only, escaped, collapsible), one-line preview in the runs list (summaries carry the preview only). Old runs: `None` -> "No prompt recorded for this run". Also unlocks the previously Non-MVP re-run-of-similar-prompt signal (same workflow id + prompt sha within a window) — still not implemented here.
