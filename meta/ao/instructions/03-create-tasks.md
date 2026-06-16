# Stage 3: Task Decomposition

## Your role
You are the architect agent breaking the epic design into implementable tasks.

## Inputs
- `requirements.md` — from stage 1
- `adr.md`, `hld.md`, `lld.md` — from stage 2

## Task

Produce two artifacts:

---

### A. `tasks.json` — Structured task list

This file will drive stage 4 (implementation). Format:

```json
{
  "epic_id": "E-XXXXXX-slug",
  "epic_title": "...",
  "tasks": [
    {
      "id": "T-XXXXXX-slug",
      "title": "Short task title",
      "description": "What must be done. Reference the LLD section(s) this implements.",
      "lld_sections": ["Module Map §N", "Interfaces §N"],
      "estimated_days": 1,
      "depends_on": ["T-XXXXXX-other-task"],
      "inputs": [
        "path/to/input/artifact.md"
      ],
      "outputs": [
        "path/to/output/artifact.py",
        "tests/test_something.py"
      ],
      "acceptance_criteria": [
        "Criterion 1 — specific, testable.",
        "Criterion 2 — specific, testable."
      ],
      "fr_nfr_coverage": ["FR-1", "NFR-2"]
    }
  ]
}
```

Rules:
- Each task ≤ 3 days estimated effort.
- Task IDs use the format `T-<6-alnum>-<slug>` (match CLAUDE.md conventions).
- `depends_on` must reference tasks by ID — no forward references (depended-on tasks come first in the array).
- `inputs`/`outputs` are file paths relative to the workspace root.
- `acceptance_criteria` must be machine-checkable by a reviewer agent — no vague statements.
- Every FR and NFR must appear in at least one task's `fr_nfr_coverage`.

---

### B. Create the epic ticket

The `epic_id` in `tasks.json` uses the standard ticket format `E-XXXXXX-slug` (uppercase `E-` prefix, matching `ad/tickets/` conventions). This is distinct from the lowercase workflow directory under `meta/ao/epics/`.

Create or update the following files using the templates in `ad/tickets/_templates/`:
- `ad/tickets/{epic_id}/EPIC.md` — with task list pre-populated from tasks.json
- `ad/tickets/{epic_id}/STATUS.md` — initial status: `In Progress`

For each task, also create:
- `ad/tickets/{epic_id}/{task_id}/TASK.md`
- `ad/tickets/{epic_id}/{task_id}/STATUS.md`

---

## Output
Write `tasks.json` to the output path provided. Create ticket files in `ad/tickets/` as described above.
