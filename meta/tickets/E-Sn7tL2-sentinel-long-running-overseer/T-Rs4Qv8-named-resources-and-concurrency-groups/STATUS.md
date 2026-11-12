# STATUS: T-Rs4Qv8-named-resources-and-concurrency-groups
- 2026-11-12: Draft filed from the FinPlan throughput analysis (T-Fp9Hng E5/E6). Design only; nothing implemented.
  Decision recorded: resources+groups are one generic engine mechanism; lanes are not an engine type; no template changes.
- 2026-11-12: Reviewed against clone HEAD + run data. Added: command-scoped leases (task-level alone is too coarse), exported slot index, host scope + flock interop, AC9-AC10. Priority normal: FinPlan unblocked by fin_plan 30b3fa85.
