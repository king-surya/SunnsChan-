# Learning architecture (Phase 6)

Raw experience stays in the Event Ledger, untouched. Learning derives
**learned objects** (patterns, skills, preferences, strategies) in the
`learned_objects` table of the same SQLite file, then promotes mature ones
into the existing KnowledgeStore. Nothing here authorizes anything.

## Evidence model

Every learned object carries: kind, subject, predicate, conditions,
supporting/contradicting event ids + counts, confidence, status
(candidate → established, with uncertain / contradicted / superseded),
linked knowledge id, created/updated/last-observed timestamps.

## Confidence (transparent)

`0.5 + 0.4·(S−C)/(S+C+4)` plus diversity (+0.02/day, cap 0.1) plus recency
(+0.03), clamped [0.05, 0.95]. A lone sighting scores ≈0.58 and stays a
candidate (support < 3 never establishes). Statuses: contradicted when
contradictions outnumber support; uncertain below 0.4 or ≥50% contradiction
ratio. `compute_confidence()` returns the full breakdown; `explain_learning()`
renders what/confidence/evidence/conditions/provenance.

## Miners (deterministic, read-only over the ledger)

- patterns: Jaccard clusters per (signature, outcome); cross-observations
  contradict same-signature rivals; subjects scrubbed of secrets.
- skills: activity_started category × activity_finished result pairs.
- preferences: voluntary selections support; stuck-avoidance contradicts
  (category resolved via the matching start event).
- strategies: experience outcomes grouped by goal context + activity
  category (temporal join to the preceding start event).

## Consolidation

`consolidate_learning(ledger, knowledge, learned, budget)` mines, then
promotes established objects (confidence ≥ 0.6) into knowledge and demotes
contradicted ones to uncertain. Budgeted, idempotent per evidence id,
ledger never written, per-stage errors collected not raised.

## Planning hook

`strategy_bonus_for()` maps context+category to a [0.7, 1.3] multiplier:
established 1.2, contradicted 0.7, else 1.0 (exploration intact).
`select_activity(score_multipliers=...)` applies and annotates them;
`AutonomyEngine` passes them when a learned store is attached, and
`python main.py --autonomy` runs consolidation after each session.

## Inspection

`/learning /patterns /skills /preferences /strategies` list objects;
`/why <id>` explains one. No secrets enter subjects or claims (scrubbed).

## Rules

LEARNING ≠ AUTHORIZATION. Learning never touches PolicyGate, the sandbox
controller, or comms approval; the controller module imports no learning
code (import-boundary test). Corrections cap at uncertain until fresh
evidence arrives. Stale established objects decay with explicit reasons.
No source-code self-modification, no reward hacking (ledger outcomes rule).

## Experience-driven development

Beyond counters, the architecture supports emergence:

- **Association** (`graph.associate()`): semantic + graph + session +
  project channels, relevance-ordered, each naming its channel.
- **Sub-skills**: attempts feed parent (`skill:category`) and emergent child
  (`skill:category:slug`) objects; conditions carry goal context.
- **Self-model** (`self_model.build_self_model()`): strengths, weaknesses,
  enjoys, returns-to, approaches, recent learning, curiosities, limitations —
  derived fresh on every call, revisable by construction.
- **Interests**: grow through lived turns, weaken/disappear via
  `tend_interests()` without recent behavioral support (history untouched).
- **Affect in experience**: `record_experience(affect=…)` snapshots the
  current state fields; the engine attaches them when affect is enabled.
- **Reinterpretation**: supersede/contradict chains preserve both sides;
  `knowledge.history()` keeps the full revision trail traversable.
- **Consolidation triggers**: `consolidation_due()` asks whether enough
  uncovered evidence (or a contradiction) exists — no cron robot; the CLI
  checks before consolidating.
- **LLM split**: deterministic code owns storage/retrieval/provenance/
  boundaries; interpretation and significance judgments flow through
  reflection findings and the engine's injected decision provider.
