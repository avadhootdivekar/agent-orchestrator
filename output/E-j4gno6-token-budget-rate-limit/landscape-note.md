# Landscape note — token/cost budgeting & rate limiting

> Epic `E-j4gno6-token-budget-rate-limit` · architect · 2026-06-18 · brief validation of the chosen approach.

## How adjacent systems handle this

| System | Total budget | Rate limit | Exhaustion handling | Accounting |
|--------|--------------|-----------|---------------------|------------|
| **Airflow** | No native token/cost budget; `pools` cap concurrent **slots**, not tokens. | Pool slots / `max_active_tasks`; no token-rate. | Tasks queue when pool full (implicitly "wait"); no token reset math. | N/A (not token-aware). |
| **Prefect** | `concurrency limits` + `global concurrency`; no token cost budget. | Global/tag concurrency, rate limiting via `rate_limit()` (slots/time). | Blocks/sleeps until a slot frees (occupy-then-release). | Slot-based, not token-based. |
| **Temporal** | No token budget; activity/workflow timeouts + rate limiting per task queue. | Worker/task-queue rate limits (RPS). | Backpressure + retry policy with backoff; durable resume is first-class. | Request-based, not token. |
| **Argo Workflows** | `resource` (CPU/mem) quotas via k8s; no token concept. | k8s rate/quota objects. | Pod pending until quota; resubmit/resume. | Resource-based. |
| **LiteLLM** | **Yes** — per-key/user `max_budget` (USD) + `tpm`/`rpm` limits. | **tpm / rpm** per key/model. | Returns 429 / `budget exceeded`; caller decides; tracks reset windows. | **Token + cost**, from provider `usage`. |
| **LangChain** | `get_openai_callback()` / `UsageMetadataCallbackHandler` track tokens+cost; no enforcement. | None native (callbacks observe, don't gate). | None — observation only. | Token from provider response usage. |

## Takeaways validating our design
- General DAG orchestrators (Airflow/Prefect/Temporal/Argo) model **concurrency/resource** limits,
  not **token** budgets — so token budgeting is a genuine gap we fill for an *agent* orchestrator.
- The closest prior art (LiteLLM) confirms our key choices: **two limits** (total budget + tpm rate),
  **provider `usage`** as the source of actuals, and **429 + reset-time** driving backoff. We add a
  **hybrid pre-run estimate gate** (LiteLLM enforces post-hoc/at-call), which lets us *admit/block a
  task before spending* — useful when a single agent task is large and expensive.
- LangChain shows the "observe-only" failure mode (tokens tracked but never enforced); our
  `BudgetManager` deliberately *enforces*, not just measures.
- Prefect/Temporal validate the **wait-on-exhaustion** pattern (block until a slot/quota frees) and
  **durable resume** — our injected-sleeper wait + RunState-persisted counters mirror this, kept
  deterministic for tests.

## Differentiation / anti-bloat positioning
- **Match LiteLLM** in two-limit (total + rate) + provider-429-aware enforcement.
- **Beat LangChain** by enforcing (gate/stop/wait), not just observing.
- **Beat generic DAG tools** by being token-aware at all (they only do slots/resources).
- **Avoid** LiteLLM's proxy/server complexity, multi-tenant key DBs, and USD price tables — we stay
  **per-run, token-only, in-process**, configured by one small spec block. USD cost + multi-run/global
  quotas are explicitly Scope Out to prevent feature creep.
