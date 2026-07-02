# Ticket Workspace (`ad/tickets`)

This directory is the project-local ticket tracker (Jira-equivalent) for epic/task execution.

## ID format (mandatory)

- Epic: `E-<RANDOM>-<slug>`
- Task: `T-<RANDOM>-<slug>`
- `<RANDOM>` must be fixed-length alphanumeric, exactly 6 chars (`[A-Za-z0-9]{6}`).
- `<slug>` must be lowercase kebab-case.

Examples:
- `E-h3k9zb-dag-scheduler`
- `T-ay7ofv-cycle-detection`

## Directory layout

```text
ad/tickets/
  E-h3k9zb-dag-scheduler/
    EPIC.md
    STATUS.md
    T-ay7ofv-cycle-detection/
      TASK.md
      STATUS.md
      HANDOFF.md
```

## Rules

1. All epic documents must be under `ad/tickets/<EpicID>/`.
2. All task documents must be under `ad/tickets/<EpicID>/<TaskID>/`.
3. Progress updates should go to `STATUS.md` in each epic/task folder.
4. Place large generated outputs in root `output/` and link them from ticket docs.
5. Keep ticket docs markdown-first and human-readable.

## Collaboration and comment protocol

6. When working on a ticket, always consider the end-to-end flow and impacts across architecture, spec schema (JSON/YAML), the DAG/dependency model, artifacts/IO, triggers/scheduling, tests, deployment, and runtime safety.
7. Ticket comments from users and agents must include clear attribution and timestamp.
   - Required format:
     - `By: <actor name or agent name>`
     - `Role: <user|developer|tester|architect|reviewer|manager|other>`
     - `Date: YYYY-MM-DD`
     - `Comment: <message>`
8. Any ticket status update must stay synchronized across relevant files:
   - Task-level: `TASK.md`, `STATUS.md`, and `HANDOFF.md` (if present)
   - Epic-level: `EPIC.md` and `STATUS.md` rollup summaries
9. When closing/opening review items, ensure wording and counts match in all touched docs (for example: "Critical items", "Warning items", and summary sections).
