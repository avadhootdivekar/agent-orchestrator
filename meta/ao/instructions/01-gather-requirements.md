# Stage 1: Gather & Consolidate Requirements

## Your role
You are the architect agent performing requirements gathering for a new epic.

## Inputs
- `prompt.md` — user-provided raw requirements / intent for the epic

## Task

Read `prompt.md` carefully. Then produce a structured `requirements.md` with the following sections:

### 1. Epic Summary
One paragraph: what this epic is building and why.

### 2. Goals & Success Criteria
Bullet list. Each goal must be specific and verifiable.

### 3. Scope In (MVP)
What is explicitly included in this epic. Group by functional area.

### 4. Scope Out (Non-MVP)
What is explicitly excluded. Note if deferred to a future epic.

### 5. Market / Prior Art Survey
For each major requirement, briefly note how existing tools address it (pick 3-5 most relevant: e.g. Airflow, Prefect, Dagster, Temporal, Argo, LangGraph, CrewAI, AutoGen). Focus on what they do well and what gaps remain.

### 6. Functional Requirements (FR-N)
Table: ID | Requirement | Priority (P0/P1/P2) | Notes

### 7. Non-Functional Requirements (NFR-N)
Table: ID | Requirement | Priority | Notes

### 8. Open Questions
Numbered list of unresolved decisions or ambiguities that must be answered before design begins.

### 9. Assumptions
What you are assuming to be true in order to scope this epic.

## Output
Write the complete document to the output path provided. Be precise — the design agent in stage 2 will use this as its sole input, so completeness matters.
