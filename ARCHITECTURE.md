# ARCHITECTURE

Suns Chan Phase 0 foundation. Existing durable core (`memory.py`, `knowledge.py`,
`core.py`, `policy.py`, `broker.py`) is preserved; new modules draw boundaries
so later phases plug in without restructuring.

## Components

- `config.py` — `Settings` from file + `SUNS_*` env vars. No secrets in code.
- `observability.py` — stdlib logging setup, `log_event`, `TaskTrace`, `health_check`.
- `textutil.py` — shared pure primitives (`clamp`, `normalize_tags`, `jaccard`,
  `signature`) formerly duplicated across the derived stores. `memory.py`
  additionally exposes `require_event(connection, id)` for source validation.
- `storage.py` — `Database` sqlite wrapper. Repositories build on this, not raw SQL.
- `events.py` — `Event` + `EventBus` (`UserMessageReceived`, `TaskCreated/Started`,
  `ToolRequested/Executed/Failed`, `EnvironmentChanged`, `MemoryCreated`,
  `LearningDetected`, `AgentFinished`).
- `llm.py` — `LLMProvider` protocol, `LLMRequest/Response`, `EchoProvider`,
  `provider_from_settings()` factory. Ollama/llama.cpp/vLLM attach here later.
- `memory.py` (existing) — append-only `EventLedger`, TF-IDF `recall`.
- `knowledge.py` + `consolidation.py` (existing) — source-linked claims, budgeted jobs.
- `core.py` (existing) — `AgentCore.turn()`: observe→recall→decide→policy→store.
- `runtime.py` — `AgentRuntime.run_task()`: explicit 14-stage loop
  (PERCEIVE…RESPOND) with `max_steps` budget. Each step drives
  `AgentCore.turn()`, so observations/decisions/verdicts persist;
  registry-only tools, invalid decisions become task failures.
- `schemas.py` — strict decision JSON schema + `parse_decision_json()`.
  Shape checked here; authority stays with `PolicyGate`/registry.
- `llm.py` + `providers/ollama.py` — `LLMProvider` protocol, `EchoProvider`
  (deterministic default), `OllamaProvider` (HTTP, raises on unreachable or
  invalid output). Factory `provider_from_settings()`; selection via
  `SUNS_LLM_PROVIDER` / `SUNS_LLM_MODEL` / `SUNS_LLM_BASE_URL`.
- `chat.py` — terminal-chat boundary: prompt builders, `build_chat_decide()`
  adapter, inspectors (`/recall`, `/knowledge`, `/sources`, `/reflect`,
  `/consolidate`, `/audit`, `/state`, `/goals`).
- `embeddings.py` — `EmbeddingProvider` protocol, `HashingEmbedder`
  (deterministic local default), `OllamaEmbedder` (HTTP `/api/embed`).
- `ranking.py` — deterministic `rank()` over explicit 0..1 signals
  (keyword/semantic/recency/importance/confidence/source_quality) with
  per-signal contributions returned for inspection.
- `memory.py` — hybrid `recall()` (TF-IDF keyword + recency + importance +
  optional semantic) with `kinds`/`metadata_filter`, plus
  `recall_with_scores()` explanations. Raw events immutable.
- `knowledge.py` — derived view: `updated_at`, `tags`, statuses
  active/uncertain/contradicted/superseded/rejected, `contradict()`,
  `mark_uncertain()`, `explain()` (supporting vs contradicting sources).
- `reflection.py` — `reflect()` job: patterns, questions, preference/skill
  candidates, contradictions, lessons → persisted `reflection` events.
- `learning.py` — `LearningTracker`: preferences adopted after >=3
  sightings (else UNCERTAIN candidates); corrections supersede immediately.
- `context.py` — bounded FACT/BELIEF/UNCERTAIN/CONTRADICTED/PREFERENCE/SKILL
  stanzas for prompts; never dumps the whole store.

## Learning (Phase 6)

Extension on the ledger + KnowledgeStore; no duplicate memory/autonomy.

- `learned.py` — learned objects (shared-DB table), transparent confidence,
  deterministic miners, strategy bonuses, justified decay, `explain_learning`.
- `consolidation.py` — `consolidate_learning()` batch job: mine, promote at
  thresholds, demote contradicted claims; budgeted, idempotent, ledger
  read-only, secrets scrubbed.
- Planning: `select_activity(score_multipliers=...)` bounded [0.7, 1.3],
  fed by `AutonomyEngine` when a learned store is attached; `--autonomy`
  consolidates after each run. Inspect via `/learning /patterns /skills
  /preferences /strategies /why`. LEARNING ≠ AUTHORIZATION (import boundary
  tested). See [docs/learning.md](docs/learning.md).

## VM sandbox (Phase 3)

```text
AgentRuntime / LLM proposal (data only)
    ↓
SandboxController (validation, approval, lifecycle, audit, ledger hook)
    ↓
SandboxProvider protocol (create/start/stop/restart/status/snapshot/restore/execute/collect/destroy)
    ↓
MockSandboxProvider (MOCK, in-memory) | ProxmoxSandboxProvider (REAL, API2 + qemu-guest-agent)
    ↓
Dedicated sandbox VM — full guest freedom, no allowlists
```

- `vm_sandbox.py` — models (`VMSpec`, `VMResources`, `GuestResult`,
  `ArtifactRecord`), the provider protocol, and the mock backend.
- `proxmox_provider.py` — clone/configure/start, agent readiness wait,
  guest exec, snapshot retention, stop/destroy. Credentials from env only,
  redacted everywhere observable.
- `vm_template.py` — reproducible baseline (`VMTemplateSpec` +
  deterministic cloud-init user-data). See `docs/sandbox-vm-template.md`.
- `sandbox_controller.py` — `ExperimentProposal` schema validation,
  lifecycle (proposed→validated→prepared→running→observing→terminal),
  time budgets, log summarization, artifact hashing, rollback/recreate
  recovery, audit trail, and experiment+outcome+learning ledger events.
  REAL backend runs require an approval callback; the LLM never touches
  the provider (verified by import-boundary test).

## Autonomy (Phase 4)

```text
EXPERIENCE → MEMORY → REFLECTION → CURIOSITY → ACTIVITY → ACTION → OBSERVATION ↺
```

- `autonomy.py` — `AutonomyEngine`: bounded `run()` over a persisted state
  machine (idle→assessing→generating→selecting→preparing→executing→
  observing→recording→reflecting→updating→decide-next). Composes AgentCore,
  ledger, reflect/consolidate, curiosities, knowledge, and the sandbox
  controller; duplicates none of them.
- `activities.py` — pure generator (goals/curiosities/failures/reflections/
  questions/interests/uncertain knowledge → explained candidates) and
  weighted selector with novelty room and inspectable reasons.
- `curiosity.py` — evidence-linked lifecycle (new→active→investigating→
  answered/abandoned→reopened); transitions require event evidence.
- `experience.py` — rich `experience` ledger events (intent, hypothesis,
  actions, observations, lessons, links) + derived relationships
  (follow_up_of, confirms, contradicts, …) resolved live from the ledger.
- `session.py` — `autonomy_sessions` table: crash-safe bookmarks; resume
  records interruptions instead of blindly re-executing; stuck signatures
  (N identical) force strategy change then pause; `recent_keys` rotation
  keeps standing goals from starving curiosity; no-progress guard bounds
  every run even when steps yield no result.
- `tools.py` — `ToolSpec`, `Tool` protocol, `ToolRegistry`
  (Validate→Security→Approval→Execute→Audit). Built-ins: `echo`, `clock` (read-only).
- `policy.py` + `sandbox.py` + `broker.py` (existing) — sandbox-scoped allows,
  human approval outside sandbox, deny destructive.
- `environment.py` — `EnvState/EnvObservation/EnvEvent`, `EnvProvider`,
  `FakeEnvironment`. Env never writes to memory directly.
- `identity.py` + `state.py` (existing) — stable seed vs dynamic state.
- `api.py` — `ChatRequest/Response`, `TaskRequest`, `MemoryQuery`, `route_table()`.
  No framework yet.
- `main.py` — boot: load settings → logging → ledger/store → runtime → ready/
  check/chat; `--autonomy` builds the real autonomous environment (provider
  per `SANDBOX_MODE`, controller, curiosity/session stores, engine) and runs
  bounded sessions with per-step reports. REAL backend additionally requires
  `SANDBOX_APPROVE_REAL=1`.
- Curiosity lifecycle is engine-driven (new→active→investigating→
  answered/active); blocked goals evolve (close + refined follow-up with
  evidence link); sandbox probe ops translate mock-syntax→shell on REAL
  backends; no guest allowlists anywhere.

## Embodiment (Phase 5)

Extension only: ledger + KnowledgeStore stay authoritative; no second
brain/memory/autonomy; mocks labeled MOCK.

- `sensors.py` — read-only providers, threshold rules + cooldowns, hub emits
  significant `sensor` events; wake decisions with cooldown; Proxmox VM
  liveness reuses the Phase 3 provider (status bits only).
- `graph.py` — derived index (experiences/goals/curiosities/knowledge/
  topics), provenance-carrying edges, bidirectional bounded traversal.
- `affect.py` — evidence-informed updates on existing `AgentState` fields,
  attention weights → selection nudges (±0.1), expression notes.
- `voice.py` / `avatar.py` — provider-independent STT/TTS and presentation
  adapters with mocks; text mode always works.
- `comms.py` — proposal → PolicyGate → approval → channel → audit, with
  secret scrubbing and an explicit approval brief.
- `projects.py` — derived cross-session project views (no second PM system).
- `context.py` — bounded environment/affect/graph sections alongside
  knowledge stanzas; `describe()` extended with env/affect/graph/comms/
  voice/avatar fields.

```text
Interface/API
    ↓
AgentRuntime (core.turn per step)
    ↓
LLM / Memory(EventLedger+Knowledge) / Tools(registry) / Environment
    ↓
PolicyGate / Storage(Database) / Observability / Events
```

## World knowledge & knowing Surya (Phase 7)

Derived views only — no second brain, memory, or autonomy:

- `ingest.py` + `sources.py` — external docs → evaluated, BELIEF-capped,
  provenanced knowledge (web/API adapters; real `HttpSource` allowlisted;
  mocks labeled MOCK). `KnowledgeStore.provenance()` resolves source events.
- `understanding.py` — the "I know Surya" layer. `UnderstandingStore` keeps
  kinds distinct (fact/observation/preference/inference/uncertainty; inference
  confidence-capped so it can never become fact), with revision history and
  bounded `relevant()` retrieval. `mine_understanding` derives it from
  interaction. Context is injected via `build_user_understanding_section`
  (bounded stanzas, never a profile dump).
- `public.py` — public info discovery with `evaluate_identity` (contextual
  evidence only; weak matches stay uncertain) and `discover_public`.
- `graph.py` — experience↔knowledge edges (`confirms`/`contradicts`/`tested`)
  join the existing derived index; `record_experience(knowledge_ids=...)` is
  the reverse of a knowledge claim's `source_ids`.

KNOWLEDGE ≠ AUTHORIZATION and UNDERSTANDING ≠ AUTHORIZATION: neither path
reaches PolicyGate, tools, or infrastructure.

## Capability acquisition (Phase 8)

The layer that lets Suns Chan acquire the abilities she needs. Capabilities
are runtime objects with operational metadata, not a static plugin list.

- `capability.py` — `CapabilityStore` (shared-DB table + `capability_usage`):
  lifecycle state machine, version tracking, reliability (success/failure
  history), `match()`, `detect_capability_gap()`, `recommend_capability()`.
- `browser.py` — `BrowserProvider` protocol; `MockBrowserProvider`
  (deterministic page graph) and `HttpBrowserProvider` (REAL allowlisted
  document fetch + link extraction). Browser is interaction, not just search.
- `acquisition.py` — `Downloader` (provenance + integrity), discovery sources
  (`DiscoverySource`, `MockDiscoverySource`, `BrowserDiscoverySource`),
  `evaluate_candidate`, and `CapabilityAcquirer` (gap→discover→evaluate→
  download→install in sandbox→validate→register→experience).
- `experience.py` gains `capability_id` so acquisition/use feeds the existing
  experience→reflection→learning loop; capability reliability then shapes
  future selection (`recommend_capability`).

CAPABILITY REGISTRATION ≠ AUTHORIZATION: acquisition happens inside the VM;
the sandbox/host boundary, PolicyGate, and approval gates are unchanged.

```text
goal -> capability gap -> discovery (browser/repo/index) -> evaluate
     -> download (verify) -> install (VM) -> validate -> register
     -> use -> experience -> reflection -> reliability -> future planning
```

## Main-server observability (telemetry)

The main server is an OBSERVED environment, never a writable one. Observation
and action are separate capabilities.

- `telemetry.py` — `TelemetrySource` protocol + `ScriptedTelemetrySource`
  (deterministic tests) and `FileTelemetrySource` (REAL read-only log tail,
  JSON-lines or plain lines). `TelemetryState` (shared-DB) persists per-source
  byte/event cursors and bounded dedup fingerprints.
- `TelemetryCollector` — poll → normalize → redact (`scrub_secrets` + key-based
  field redaction) → dedupe (content fingerprint) → importance filter →
  ledger `telemetry` events. Only events at/above the configured importance
  threshold reach cognition; low-noise events are counted and skipped, never
  dumped into prompts.
- Integration — `promote_telemetry` groups significant events into ONE
  experience (`record_experience`) and mines `observation`-kind understanding
  (OBSERVATION ≠ FACT), both idempotent and bounded. `telemetry_section`
  provides bounded context; `/telemetry` inspects recent events.
- Config — `TelemetrySettings` (`SUNS_TELEMETRY_*` env; disabled by default).
  Sources: `scripted`, `file`; other types (journald/syslog) are
  provider-dependent and fail closed.

OBSERVATION ≠ AUTHORIZATION: nothing here executes commands, writes files,
restarts services, or touches credentials. Sandbox/VM isolation and PolicyGate
are unchanged.

## Dependency rules

- Memory never imports Agent. Tools never drive Agent. Personality never
  executes tools. LLM never runs shell. Environment never writes memory.
- Security (`PolicyGate` + registry checks) sits between every decision and
  every state-changing action.

## Data flow (one task step)

```text
task → env.observe → ledger.recall → decide → policy.evaluate
  → registry.execute (approved only) → bus.publish → trace.step → response
```

## Event flow

Runtime publishes to `EventBus`; future scheduler/dashboard/learner subscribe
to kinds without importing the runtime.

## Extension points (no restructure needed)

- LLM: implement `LLMProvider` (ollama/llamacpp/vllm), extend factory.
- Retrieval: swap TF-IDF inside `EventLedger.recall` boundary; add embeddings.
- Tools: implement `Tool`, `register()`; add shell/proxmox/docker each with
  policy + audit + tests. Host shell stays disabled by default.
- Environment: implement `EnvProvider` for homelab sensors.
- Learning: subscribe to `outcome`/`LearningDetected`, write `KnowledgeStore`.
- API: mount `route_table()` kinds onto a real server + WS stream.
- Storage: new repository classes take `Database`; swap backend later.
