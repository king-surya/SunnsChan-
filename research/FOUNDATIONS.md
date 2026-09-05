# Foundations for Suns Chan

Suns Chan is an experiment in **persistent, adaptive agent behavior**. It is
not a claim that an LLM is alive or conscious. The engineering target is much
more concrete: it should retain an inspectable history, form revisable
knowledge, use relevant experience in future decisions, and act only within
explicit boundaries.

## The required foundation map

| Foundation | Minimum responsibility | MVP | Later, only when the preceding layer works |
| --- | --- | --- | --- |
| 1. Event ledger | Append raw observations, decisions, actions, results, timestamps, provenance and user/agent scope. Raw records are immutable. | SQLite and JSON metadata | encrypted-at-rest storage; event signatures |
| 2. Memory lifecycle | Create derived memories from events; track source episodes, confidence, salience, supersession and retention. | recent context plus keyword recall | semantic vectors, graph links, decay, consolidation |
| 3. Context assembly | Select a bounded, explainable set of recent and recalled memories for one decision. | top relevant events with token budget | hybrid retrieval, reranking, graph traversal |
| 4. Model boundary | Convert `TurnContext` to one model request and validate structured output. The provider is replaceable. | one chat-only adapter | local-model routing, retries, cost limits, multimodal inputs |
| 5. Decision protocol | Make goals, uncertainty, proposed action and expected outcome explicit. | response plus optional typed action | planning, prediction/outcome comparison, reflection |
| 6. Tool boundary | Every tool has a typed input/output schema, idempotency expectation, timeout and audit event. | no write tools; one read-only sensor later | isolated runners, capability-scoped credentials |
| 7. Safety and human authority | Classify risk before tool execution; capture approval and deny by default when unknown. | read-only allowed; write/network/command require approval; destructive denied | per-tool permissions, staged autonomy, sandboxing |
| 8. Persistent state / identity | Keep stable, editable statements of purpose, constraints, relationships and current commitments—each traceable to evidence or explicit user choice. | a versioned identity file plus active-goal list | belief revision and confidence calibration |
| 9. Autonomous scheduling | Wake only for named triggers or schedules; every wake has a budget and a stop condition. | disabled until interactive loop is trustworthy | sensor-triggered wakes, maintenance/reflection jobs |
| 10. Observability and evaluation | Explain what was recalled, why an action was blocked, and whether an outcome matched the prediction. | event/audit viewer and regression tests | scenario suite, long-horizon memory evaluation, drift alarms |
| 11. Privacy and data governance | Define ownership, retention, export, deletion, backups and secret isolation before ingesting personal logs. | local path separation, no telemetry | encryption, redaction, user-scoped consent records |
| 12. Runtime operations | Make start/stop, migrations, backup, recovery and version upgrades boring and reproducible. | local Python environment and database backup | service manager, health checks, restore drills |

## Build order

```text
event ledger -> recall/context -> chat-only model adapter -> evaluation
     -> one read-only sensor -> approval-gated tool -> reflection/consolidation
     -> scheduled autonomy -> optional graph/affect/voice/avatar
```

This order is deliberate. A graph, voice, avatar, emotional model, or background
loop cannot repair missing event provenance, bad retrieval, or unsafe tools.

## Non-negotiable data invariants

1. A derived fact, summary, skill, or self-description links back to its source
   event(s), or is labeled as an explicit user-provided assertion.
2. Memory decay means **do not surface by default**, not silently destroy the
   raw experience.
3. A model suggestion is not execution authority. Tool execution happens only
   after policy evaluation.
4. Every autonomous wake has a task budget, a maximum tool budget, and a
   recorded reason for waking.
5. The agent's statement about its own history is a queryable claim with
   evidence, not privileged truth.

## What to borrow, rather than merge

- From **PaulusAI**: explicit action safety gates and a provider boundary.
- From **Crescent Grove**: continuity-first memory lifecycle and the separation
  between raw history and memory views.
- From **Persistent AI Memory**: portable local storage and multi-client scope.
- From **emotion_memory_graph**: relationship/affect retrieval as an optional
  later experiment, not the first database.
- From **MemOS, Memora, Mem0, Graphiti, and agentic-memory**: competing memory
  lifecycle and retrieval designs to benchmark behind one `recall()` interface.

The core must own one event schema and one policy boundary. Everything else is
replaceable infrastructure.
