# Suns Chan — Persistent Agent Core

This is a deliberately small foundation for a persistent AI agent. It is not a
claim of consciousness, nor a bundle of unrelated "cognitive" subsystems.
Its job is to make every interaction inspectable:

```text
observe -> recall -> decide -> policy check -> act -> observe outcome -> store
```

The project retains raw experiences in a local SQLite event ledger and retrieves
only relevant memory for an agent turn. A later consolidation step can produce
knowledge records without rewriting or deleting the original experience.

## What is included now (Phases 0–4, all tested)

- append-only event storage with timestamps and structured metadata;
- hybrid retrieval (keyword + recency + importance + optional embeddings);
- source-linked knowledge with supersession, contradiction, and provenance;
- budgeted reflection/consolidation and evidence-accumulated learning;
- an explicit approval policy for actions;
- an editable personality seed plus experience-shaped mood/interest state;
- typed autonomous wakes, self-proposed goals, and first-class curiosities;
- a strict JSON decision schema; invalid model output becomes an event, never an action;
- terminal chat (`python main.py --chat`) with memory/audit inspectors;
- a full-freedom VM sandbox behind `SandboxController` (mock backend by
  default; Proxmox backend gated and credential-isolated), with snapshots,
  artifact hashing, and outcome-to-memory integration;
- an `AutonomyEngine` running bounded self-directed sessions
  (`python main.py --autonomy`): curiosity/goal/failure-driven activities,
  persisted state machine, crash recovery, stuck detection, pause/resume;
- Phase 5 embodiment: read-only homelab sensors with threshold wakes,
  optional graph retrieval with provenance, evidence-informed affective
  rhythm, provider-independent voice/avatar adapters (text mode by default),
  approval-gated external communication with secret scrubbing, and
  cross-session project continuity views;
- Phase 6 learning: evidence-backed patterns/skills/preferences/strategies
  with transparent confidence, budgeted consolidation into knowledge, and
  bounded planning influence — past experience measurably changes future
  behavior without ever granting authorization;
- Phase 7 world knowledge + knowing Surya: web/API knowledge pipeline with
  provenance, a derived personal-understanding layer (fact/observation/
  preference/inference/uncertainty kept distinct; inference never becomes
  fact), and public-info discovery with careful identity uncertainty;
- Phase 8 capability acquisition: a dynamic `CapabilityStore` (lifecycle,
  versioning, usage/failure history), a browser abstraction (mock + real
  allowlisted fetch), a provenance-carrying downloader with integrity checks,
  and a goal-driven acquirer (gap → discover → evaluate → download → install
  in the sandbox VM → validate → register → experience);
- a model-agnostic `AgentCore` that accepts a decision function rather than
  hard-coding personality or action rules.

There is intentionally **no ambient host execution**. Commands run only
inside the sandbox VM through the controller; the host boundary (VM
isolation, credential isolation, network attachment, approvals for real
infrastructure) is documented in [SECURITY.md](SECURITY.md).

The next staged build plan is in [ROADMAP.md](ROADMAP.md). The one editable
starting personality for Suns Chan is [config/identity_seed.json](config/identity_seed.json).
It is an identity seed, not a prison: subsequent state, interests, goals and
knowledge are recorded as evidence-backed history rather than hard-coded traits.

## Quick start

Requires Python 3.11 or later. There are no third-party runtime dependencies.

```powershell
cd "C:\Users\SURYA\Documents\Codex\2026-09-04\referenced-chatgpt-conversation-this-is-an-2\outputs\suns-chan-autonomous"
$env:PYTHONPATH="src"; python -m unittest discover -s tests -v
python main.py --check
python main.py --chat
python main.py --autonomy --session main --activities 3
```

## Current boundary

`AgentCore` does not call an LLM by itself. Supply an adapter that translates
the `TurnContext` into a model call and returns a `Decision`. That separation
means the persistence and safety behavior stay testable even if the model,
provider, or prompt changes.

The initial policy allows read-only operations. Outside the sandbox, write,
command, network, message and install actions require a human approval
callback, and destructive actions are denied by default. Inside the
dedicated sandbox VM, the guest has full freedom (the VM is the containment);
real Proxmox runs additionally require explicit per-invocation approval.

## Imported reference source

The four public repositories discussed in the referenced conversation have
been cloned as separate snapshots in `third_party/`. Their source is **not**
linked into this package and is not presented as Suns Chan code. See
[THIRD_PARTY.md](THIRD_PARTY.md) for origins, pinned commits, licenses, and the
specific architectural ideas considered.

## Research map

The initial, reviewed repository map is in
[research/REPOSITORY_CATALOG.md](research/REPOSITORY_CATALOG.md), while the
non-negotiable build layers and order are in
[research/FOUNDATIONS.md](research/FOUNDATIONS.md). To refresh broad GitHub
discovery without pretending an internet search is exhaustive, run:

```powershell
python tools/discover_github_repos.py --output research/github-candidates.json
```

The generated JSON is intentionally ignored by Git: it is a dated discovery
artifact, not a reproducible dependency lockfile.

## Next implementation steps (Phase 5 candidates)

- Optional embodiment: voice/avatar interfaces, dashboard bindings onto the
  autonomy `describe()`/`pause`/`resume` control points.
- Graph retrieval for entities and relationships.
- Approval-gated external communication.
- Real Proxmox integration runs once infrastructure is available
  (`tests/test_proxmox_real.py` currently skips honestly without it).
