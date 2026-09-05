# Suns Chan roadmap

## North star

Build a persistent AI companion whose behavior can change because she has a
history: she remembers events, pursues self-generated interests, evaluates
outcomes, and operates freely inside environments Surya intentionally provides.
The system will not claim that these mechanisms prove consciousness or a human
body. They are accountable engineering mechanisms for continuity and adaptation.

## Design rule

**Soft inner life; hard external boundary.**

Suns Chan's interpretation, tone, interests, mood, and optional goals are model
decisions informed by experience. Her external effect is capability-scoped,
audited, resource-bounded, and sandboxed. The policy does not tell her how to
feel or what to want; it defines where a proposed action may occur.

## Phase 0 — durable core (complete)

- [x] Append-only event ledger with structured metadata.
- [x] Selective recall boundary.
- [x] Persisted, experience-shaped state: curiosity, confidence, stress,
  energy, warmth, directness, rhythm, and growing interest affinities.
- [x] Editable personality/identity seed.
- [x] Autonomous wake entry point and self-proposed goal records.
- [x] Outcome and failure-to-learning audit path.
- [x] Sandbox capability descriptors; no ambient command execution.
- [x] Policy blocks destructive actions and keeps non-sandbox installs gated.

## Phase 1 — real conversation loop (complete)

**Goal:** attach exactly one LLM adapter without turning the adapter into the
system.

- [x] Strict JSON schema for model decisions (`src/suns_chan/schemas.py`:
  response, goals, interest tags, optional action, confidence, expected outcome).
- [x] Provider adapters: deterministic `EchoProvider` default plus an
  `OllamaProvider` HTTP backend (`src/suns_chan/providers/ollama.py`;
  unreachable/invalid server output raises, never fakes a reply).
- [x] Response validated before persisting; invalid output becomes an
  `invalid_decision` event, never an action (`AgentCore.turn`,
  `AgentRuntime.run_task` maps it to task failure).
- [x] Terminal chat (`python main.py --chat`) plus memory/audit inspector
  (`/recall`, `/audit`, `/state`, `/goals`) in `src/suns_chan/chat.py`.
- [x] Repeatable scenarios in `tests/test_scenarios.py`: correction,
  forgotten fact, failed experiment, changing interest, conflicting memories.
- [x] Runtime drives `AgentCore.turn()` per step, so multi-step tasks persist
  observations/decisions/verdicts instead of tracing only in memory.

**Exit test:** `test_exit_contradiction_recalls_source_and_updates_belief` —
a later recall demonstrably returns the contradicting source event and the
knowledge claim is superseded with source linkage.

## Phase 2 — learning and memory quality (complete)

**Goal:** improve retrieval and learning without deleting raw experience.

- [x] Hybrid search behind `EventLedger.recall()`: TF-IDF keyword + recency
  + importance metadata + optional semantic signal (`src/suns_chan/ranking.py`,
  `embeddings.py`; backend-agnostic `EmbeddingProvider`, local hashing default).
- [x] Structured `knowledge` view with source IDs, confidence, tags,
  `updated_at`, statuses active/uncertain/contradicted/superseded/rejected,
  `contradict()` and `explain()` (`src/suns_chan/knowledge.py`).
- [x] Budgeted consolidation plus an explicit, bounded `reflect()` job whose
  findings persist as `reflection` events for later consolidation
  (`src/suns_chan/reflection.py`, `consolidation.py`).
- [x] Evidence-accumulation learning: preferences adopted after >=3 sightings,
  corrections supersede immediately (`src/suns_chan/learning.py`).
- [x] Tests for stale fact supersession, false lesson rejection, source
  traceability (`tests/test_memory_quality.py`), plus Phase 2 scenarios A–G,
  recall latency, and the MEMORY ≠ AUTHORIZATION gate
  (`tests/test_phase2_scenarios.py`).

**Exit test:** `/sources <id>` (backed by `KnowledgeStore.explain()`) states
the current belief, supporting sources, contradictory evidence, and confidence;
UNCERTAIN/CONTRADICTED statuses mark exactly when evidence is weak or
contradicted.

## Phase 3 — full-freedom VM sandbox (complete; REPLACES the earlier container plan)

**Model:** the VM IS the sandbox. Full freedom inside (root, arbitrary
commands/packages/filesystem, even guest destruction); hard boundary outside
(host, credentials, other infra, network enforced at clone time).

- [x] `SandboxProvider` protocol + `MockSandboxProvider` (MOCK-labeled,
  arbitrary guest commands accepted, snapshot/restore, in-memory).
- [x] `ProxmoxSandboxProvider` (REAL: clone/configure/start, agent readiness,
  guest exec, snapshot retention, stop/destroy; env-only credentials, redacted).
- [x] Reproducible template (`VMTemplateSpec` + deterministic cloud-init;
  `docs/sandbox-vm-template.md`).
- [x] `SandboxController`: typed `ExperimentProposal` validation, lifecycle,
  time budgets, log summarization, artifact SHA-256, rollback/recreate
  recovery, audit trail, REAL-requires-approver gate.
- [x] Outcome → Event Ledger (experiment + outcome + failure-lesson events);
  failed experiments demonstrably influence later decisions.
- [x] Adversarial tests (escape, escalation, leakage, malformed proposals)
  plus deterministic file-create-and-inspect e2e with host unchanged.
- [x] Gated real-VM test (`tests/test_proxmox_real.py`; skips without
  `SANDBOX_MODE=proxmox` + credentials — never faked).

**Exit test:** `test_failed_experiment_influences_later_decision` — an
experiment fails safely, the host is unchanged, and the lesson reaches a
later decision through memory.

## Phase 4 — bounded autonomy (complete)

**Goal:** continuous self-directed operation over memory, curiosity, goals,
and the sandbox — without uncontrolled loops.

- [x] `AutonomyEngine`: explicit persisted state machine, bounded `run()`,
  cognitive (AgentCore) and sandbox (controller) executors.
- [x] Explained activity generation + weighted selection with novelty room.
- [x] Rich experience events + derived relationships on the ledger (no second DB).
- [x] Evidence-linked curiosity lifecycle feeding future activities.
- [x] Existing reflection/consolidation/learning reused per activity.
- [x] Crash-safe sessions (resume replans, never blindly re-executes),
  stuck detection (strategy change → pause), pause/resume/stop, `describe()`.
- [x] Memory demonstrably drives selection (supporting IDs recorded);
  failures preserved; VM destruction never erases experience.

**Exit test:** `test_failed_experiment_influences_later_decision` (Phase 3)
plus `test_memory_influences_selection` and
`test_stuck_triggers_pause_with_strategy_change` — a week-equivalent of
sandbox-only activity stays bounded, explained, and history-preserving.

Reconciliation audit (2026-09-04) independently verified every claim above
against code, tests, and runtime; six gaps were found and repaired:
`--autonomy` CLI entrypoint (engine was test-only), curiosity lifecycle
transitions, generator probe ops + artifact collection, goal evolution on
stuck, recency-discounted selection rotation, and a 6-cycle memory-influence
simulation (`test_multi_cycle_memory_shapes_later_activities`). An
infinite-loop flaw in `run()` (empty-candidate spin) was found by the audit
and fixed with waiting/no-progress guards.

## Phase 5 — optional embodiment and richer continuity (complete)

**Rule:** extension only — no second brain/memory/autonomy; ledger and
KnowledgeStore stay authoritative; mocks labeled MOCK, never REAL.

- [x] Read-only sensors (`sensors.py`): threshold rules + cooldowns, hub
  emits significant `sensor` events only; Proxmox VM liveness via existing
  provider (status bits out, credentials never cross).
- [x] Sensor-triggered wakes (`evaluate_wake` + ENVIRONMENT candidates in
  the existing generator; wake recorded, cooldown enforced, no LLM per reading).
- [x] Voice/avatar as optional adapters (`voice.py`, `avatar.py`); text mode
  and coreless operation proven by tests.
- [x] Graph retrieval (`graph.py`): derived nodes/edges with provenance,
  bidirectional traversal, contradictions preserved, bounded depth.
- [x] Affective rhythm (`affect.py`): evidence-informed updates on existing
  `AgentState` fields, attention weights nudging selection ±0.1, optional
  engine integration; AFFECT ≠ AUTHORIZATION tested.
- [x] Approval-gated comms (`comms.py`): proposal → PolicyGate → explicit
  approval → channel → audit; secrets scrubbed; unapproved never sends.
- [x] Continuity (`projects.py`, bounded context sections, extended
  `describe()`, `/environment /affect /graph /communications` commands).
- [x] Integration + 7-day-equivalent continuity simulation green.

**Exit proof:** `test_full_chain_observation_to_influence` (15-step chain)
and `test_seven_day_equivalent` (recall → wake → activity → reflection →
retrieval → changed behavior). Real Proxmox/voice/avatar providers remain
unconfigured; mocks stand in with honest labels.

## Phase 6 — self-improving learning (complete)

**Rule:** experience must measurably change future behavior; history,
provenance, and uncertainty are preserved; learning never authorizes.

- [x] Learned objects (pattern/skill/preference/strategy) in a shared-DB
  table with evidence counts, conditions, statuses, knowledge links.
- [x] Transparent confidence engine (formula + breakdown + explanation).
- [x] Deterministic miners over ledger evidence; contradiction as signal;
  explicit corrections cap at uncertain; justified stale decay.
- [x] Budgeted idempotent `consolidate_learning()` with promotion thresholds
  and demotion of contradicted claims; secrets scrubbed; ledger read-only.
- [x] Planning hook: bounded [0.7, 1.3] strategy multipliers in selection,
  wired through `AutonomyEngine` and `--autonomy`; exploration preserved.
- [x] `/learning /patterns /skills /preferences /strategies /why` inspectors.
- [x] Behavioral tests TEST 1–15, multi-cycle adaptation + long-run sims.

**Exit proof:** `test_adapt_then_readapt` (advantage → contradiction →
competitive replanning) and `test_many_experiences_shift_planning`
(patterns+skills+preferences+strategies → promotion → shifted selection
with raw history intact). See [docs/learning.md](docs/learning.md).

Experience-driven development (same phase, second charter): association
across four channels, emergent sub-skills with goal context, derived
self-model, interest weaken/disappear dynamics, affect snapshots in
experiences, reinterpretation chains, trigger-gated consolidation, and a
documented LLM/deterministic split — all proven by `test_development.py`
(B/E/G/H/K, self-model, affect) with history preserved throughout.

## Phase 7 — world knowledge and knowing Surya (complete)

**Rule:** one coherent architecture; no duplicate brains. World knowledge and
personal understanding are DERIVED views over the same event ledger; external
knowledge never becomes fact on its own and never grants authorization.

- [x] World/web/API knowledge pipeline (Phase 2/7): `ingest.py` acquire →
  evaluate (BELIEF-capped, corroboration/contradiction/source reliability) →
  provenanced knowledge; `sources.py` real `HttpSource` (allowlist/caps) plus
  honest `MockWebSource`/`MockSearchSource`/`MockApiSource`.
- [x] Provenance: `KnowledgeStore.provenance()` resolves a claim's source
  events (url/source_type/backend/retrieved_at); immutable events keep it
  through supersession/contradiction.
- [x] Knowledge graph (`graph.py`): is-a/related-to/confirms/contradicts/
  refines/tested edges with provenance, bidirectional bounded traversal,
  and experience→knowledge links (`record_experience(knowledge_ids=...)`).
- [x] Contradictions preserved (both sides explainable); temporal staleness
  via `refresh_temporal`; optional semantic knowledge recall via embedder.
- [x] Personal understanding (`understanding.py`): a derived "I know Surya"
  layer with distinct kinds — fact / observation / preference / inference /
  uncertainty (inference is confidence-capped and can never become fact) —
  revision with history preserved, provenance, and bounded `relevant()`
  retrieval. `mine_understanding` derives it from interaction deterministically.
- [x] Integration: understanding feeds bounded prompt context
  (`build_user_understanding_section`), planning (`generate_activities`
  surfaces weak understanding), autonomy (`AutonomyEngine` mines understanding
  each step), and chat (`/understanding <q>` + live mining).
- [x] Public discovery (`public.py`): `evaluate_identity` scores only
  contextual evidence (username/project/terms) — a bare name match is never
  certainty; `discover_public` records strong matches as `identity`, plausible
  as `inference`, and drops weak matches. Offline search degrades to events.
- [x] Tests: `test_understanding.py`, `test_public.py`, `test_ingest.py`,
  `test_phase7_e2e.py` (14-step loop), `test_phase7_longrun.py` (evolving
  understanding, no duplicate facts, bounded context, restart persistence).

**Exit proof:** `test_phase7_e2e.py::test_full_loop_changes_future_reasoning` —
Surya discusses a project, a knowledge gap appears, external knowledge is
acquired with provenance, an experiment links it back to experience, and
future reasoning demonstrably draws on the accumulated understanding (not just
a grown database). `test_phase7_longrun.py` shows changing interests revise
understanding without erasing history, and bounded retrieval never dumps the
profile. Full suite: 230 tests green.

## Phase 8 — capability acquisition (digital environment) (complete)

**Rule:** build the layer that lets Suns Chan acquire capabilities she needs;
do not rebuild cognition, memory, knowledge, or autonomy. Capabilities are
runtime objects, not a static plugin list. One registry, one lifecycle.

- [x] `capability.py` — `CapabilityRecord` + `CapabilityStore` (shared-DB
  table): kinds tool/executable/package/script/library/agent/service/webapp,
  lifecycle state machine `discovered→downloaded→installed→initialized→
  validated→available` with `failed`/`disabled`/`removed`, version tracking,
  usage/failure history, `reliability()`, token `match()`, dedup by
  (name, version). `detect_capability_gap()` (the planner's "can I already do
  this?") and `recommend_capability()` (reliability-weighted selection).
- [x] `browser.py` — `BrowserProvider` protocol + `MockBrowserProvider`
  (deterministic page graph: open/navigate/inspect/follow/download) and
  `HttpBrowserProvider` (REAL fetch over allowlisted domains + link
  extraction). Browser ≠ search; it is document interaction.
- [x] `acquisition.py` — `Downloader` (provenance + sha256 integrity; corrupt
  artifacts fail loudly), `DiscoverySource`/`MockDiscoverySource` +
  `BrowserDiscoverySource` (browser→candidate adapter), `evaluate_candidate`
  (honest uncertainty), and `CapabilityAcquirer` (gap→discover→evaluate→
  download→install in sandbox→validate→register→experience) — goal-driven,
  single-shot, never random.
- [x] Experience/learning: `record_experience(capability_id=...)` links
  acquisition/use to the existing experience + reflection loop; capability
  success/failure history feeds `recommend_capability`, so failed tools are
  deprioritized without hard-coded "never use".
- [x] Planning: `generate_activities(capabilities=...)` emits `ACQUIRE`
  candidates when a goal has a capability gap. Chat: `/capabilities
  /capability <n> /agents /acquisitions`.
- [x] Security unchanged: acquisition installs inside the sandbox only;
  KNOWLEDGE/REGISTRATION ≠ AUTHORIZATION (PolicyGate and host boundary intact).

**Exit proof:** `test_capability_e2e.py::test_browser_discovery_to_available_capability`
(browser hub → candidate → download → install → validate → register → use →
experience → no remaining gap) plus restart persistence, and
`test_capability_longrun.py` (reliable capability reused over flaky, repeated
failure becomes disabled, no duplicate registrations, no retry loops, no
capability explosion). Full suite: 260 tests green.

Real network/browser backends remain config-gated (`HttpBrowserProvider`,
`Downloader` default fetcher); mocks stand in with honest labels.

## Additional subsystem — main-server observability (complete)

An input layer, not a new phase: observe the main server without writing to it.

- [x] `telemetry.py` — read-only `TelemetrySource` (Scripted + REAL file tail),
  persisted cursors + bounded dedup fingerprints (`TelemetryState`), and a
  `TelemetryCollector` (normalize → redact → dedupe → importance filter →
  ledger `telemetry` events). Low-noise stays out of cognition.
- [x] Normalized events carry timestamp/source/category/event_type/severity/
  subject/action/provenance; secrets scrubbed (message + sensitive metadata).
- [x] Experience + understanding integration (`promote_telemetry`): significant
  events become ONE experience and `observation`-kind understanding
  (OBSERVATION ≠ FACT), idempotent and bounded.
- [x] `/telemetry` CLI, `telemetry_section` bounded context, `TelemetrySettings`
  (`SUNS_TELEMETRY_*`; disabled by default). Read-only by construction.

**Exit proof:** `test_telemetry_e2e.py` (login → config change → restart →
healthy → query without raw-log dump, plus real file-source incremental +
restart) and `test_telemetry_longrun.py` (duplicates deduped, noise filtered,
disconnect/reconnect, restart cursor, no unbounded growth). Full suite:
274 tests green.
