# Stage 2: Epic Design (Architecture)

## Your role
You are the architect agent (running as Opus for thoroughness) producing the full design for this epic.

## Inputs
- `requirements.md` — consolidated requirements from stage 1

## Task

Produce three documents:

---

### A. `adr.md` — Architecture Decision Record

Follow the ADR-0001 format already established in `docs-md/adr/`. Include:
- **Status**: Proposed
- **Context**: why we are making this decision
- **Options considered** (≥3): brief description + pros/cons each
- **Decision**: chosen option with rationale
- **Consequences**: trade-offs accepted
- **Mitigation**: how risks are addressed

---

### B. `hld.md` — High-Level Design

Sections:
1. **System Context** — who/what interacts with this system and how
2. **Architecture Overview** — component diagram in ASCII or mermaid; describe each component's responsibility
3. **Key Data Flows** — numbered step-by-step for the 2-3 most important flows
4. **Integration Points** — external systems, APIs, protocols
5. **Technology Decisions** — libraries/frameworks chosen with rationale
6. **Open Decisions** — anything deferred to LLD

---

### C. `lld.md` — Low-Level Design

Sections:
1. **Module Map** — file/package structure with one-line purpose per module
2. **Interfaces & Protocols** — ABCs, Protocols, dataclasses, Pydantic models (show field names + types + docstring)
3. **Algorithm Details** — for any non-trivial logic: pseudocode or step-by-step description
4. **Schema Definitions** — JSON/YAML schema excerpts for any structured spec files
5. **Error & Edge Cases** — how failures, invalid inputs, and edge cases are handled
6. **Test Plan** — unit test categories + integration test scenarios (not code, just a plan)
7. **Task Decomposition Hints** — suggested task boundaries for stage 3

---

## Quality bar
- Every FR and NFR from `requirements.md` must be addressed somewhere in the design.
- No handwavy "TBD" — if something is genuinely unknown, mark it as an Open Decision with a rationale for deferring.
- Interface definitions must be precise enough for a developer agent to implement without further clarification.

## Outputs
Write `adr.md`, `hld.md`, and `lld.md` to the output paths provided.
