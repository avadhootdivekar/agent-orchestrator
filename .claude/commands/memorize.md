# /memorize — Session learning capture

Capture genuinely new learnings and memories from the current conversation session.

---

## Decision gate (run first — skip if nothing qualifies)

Scan the current session for things that were **non-obvious** and **reusable beyond this task**:
- Pitfalls, bugs, or surprises that weren't documented
- Behavioral/schema/architectural constraints uncovered through trial
- Workflow patterns that saved or cost significant time
- Correctness invariants that could trip up a future agent

If nothing qualifies: report **"No new learnings to capture."** and stop. Do NOT add noise.

---

## Step 1: Read existing entries (dedup)

Read `ad/learnings.md` and `meta/instructions/memories/README.md`.  
Skip anything already covered there.

---

## Step 2: Append to `ad/learnings.md`

For each new learning, append one block in this exact format:

```
---
Learning-ID: LRN-YYYYMMDD-<short-slug>
Learning: <one clear actionable sentence>
Context: <one sentence — why/when observed>
By: <name or "agent">
Role: agent
Date: YYYY-MM-DD
---
```

Rules:
- Learning + Context combined ≤ 50 words
- Slug: kebab-case, lowercase, max 5 words
- Use today's date (check `date +%Y%m%d` if unsure)

---

## Step 3: Update `meta/instructions/memories/README.md` (conditional)

Only for **durable repo-scoped facts** every agent should know long-term — not task context.  
Qualifying examples: non-obvious architectural invariants, API footguns, test anti-patterns with known regressions.

Append after the existing content using this format:

```markdown
---
name: <kebab-slug>
description: <one-line summary>
type: decision | constraint | pitfall | convention
---

<Fact>. **Why**: <reason>. **Apply**: <when>.
```

---

## Step 4: Update `ad/learning-compact.md` (conditional)

Only if **3 or more new learnings** were added. Add one bullet per new learning, ≤ 15 words, actionable insight only. Preserve all existing bullets.

---

## Output

Report exactly three lines:
- Learnings added: N (to ad/learnings.md)
- Memories added: N (to meta/instructions/memories/README.md)
- Compact updated: yes / no
