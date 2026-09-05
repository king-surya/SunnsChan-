# Repository discovery catalog

**Scan date:** 2026-09-04  
**Purpose:** source map for Suns Chan research, not a dependency list or a
quality/security endorsement. Repository descriptions are author claims and
must be verified by reading source, tests, license and active maintenance.

GitHub search is open-ended: the initial exact queries returned **1,160**
repositories for `"persistent memory" "AI agent"`, **683** for `"AI companion"
memory`, and **2,634** for `"agent memory" language:Python`. The selected set
below prioritizes repositories that expose an architecture, an implementation,
or a benchmark relevant to Suns Chan. The repeatable scanner in
[`tools/discover_github_repos.py`](../tools/discover_github_repos.py) produces a
full candidate JSON list from configurable queries and pages.

## Tier A — inspect before implementing the next memory layer

| Repository | Focus | Why it matters | License reported by GitHub |
| --- | --- | --- | --- |
| [Crescent Grove](https://github.com/Canon-73/crescent-grove) | local companion, episodic decay, autonomous loop | closest end-to-end continuity reference | MIT |
| [PaulusAI](https://github.com/Pavel6625/PaulusAI) | multi-store memory and guarded tools | good safety/tool boundary study | MIT |
| [MemOS](https://github.com/MemTensor/MemOS) | self-evolving memory OS and skill reuse | compare memory lifecycle and reuse claims | Apache-2.0 |
| [Memora](https://github.com/microsoft/Memora) | ingestion, abstraction and retrieval policies | useful production-oriented lifecycle reference | inspect upstream |
| [A-MEM](https://github.com/agiresearch/A-mem) | agentic memory architecture | research implementation to evaluate separately | MIT |
| [agentic-memory](https://github.com/agentclash/agentic-memory) | semantic, episodic and procedural stores | clean multi-store taxonomy reference | inspect upstream |
| [OpenViking](https://github.com/volcengine/OpenViking) | context/memory/RAG/skill database | broad competing infrastructure design | AGPL-3.0 |
| [Hindsight](https://github.com/vectorize-io/hindsight) | learning agent memory | retrieval and feedback-loop reference | MIT |
| [Graphiti](https://github.com/getzep/graphiti) | temporally-aware knowledge graph | optional temporal relationship backend | inspect upstream |
| [Mem0](https://github.com/mem0ai/mem0) | memory extraction and retrieval layer | integration baseline, not a full agent identity | Apache-2.0 |
| [mcp-memory-service](https://github.com/doobidoo/mcp-memory-service) | MCP memory and consolidation | useful protocol/server boundary study | Apache-2.0 |
| [neo4j agent-memory](https://github.com/neo4j-labs/agent-memory) | graph-native context/memory | later graph experiment | Apache-2.0 |

## Tier B — architecture patterns and validation material

| Repository | Focus | Use for Suns Chan |
| --- | --- | --- |
| [Agent Memory Techniques](https://github.com/NirDiamant/Agent_Memory_Techniques) | runnable memory patterns | compare technique trade-offs before adding a store |
| [Learn Agent Memory](https://github.com/hardness1020/learn-agent-memory) | raw events as ground truth, staged implementation | design review/checklist material |
| [Awesome AI Memory](https://github.com/IAAR-Shanghai/Awesome-AI-Memory) | papers, systems and benchmarks | discovery index, not runtime dependency |
| [Awesome Agent Memory](https://github.com/TeleAI-UAGI/Awesome-Agent-Memory) | curated systems/benchmarks | discovery index and evaluation leads |
| [agent-memory-kit](https://github.com/joelbrilliant/agent-memory-kit) | evidence-gated local memory, FTS5 | minimal local-first counterweight to vector/graph complexity |
| [Engram](https://github.com/blakestone-x/engram) | tiered memory, decay, consolidation | retention policy and source-linking study |
| [persistent-ai-memory](https://github.com/savantskie/persistent-ai-memory) | SQLite persistent memory and MCP | portable memory operations reference |
| [emotion_memory_graph](https://github.com/MValeResearch/emotion_memory_graph) | affect-linked Neo4j memory graph | optional affect/relationship retrieval reference |
| [agentmemory](https://github.com/jayzeng/agentmemory) | plain-text persistent agent memory | simple artifact/log layout reference |

## Tier C — life-like/companion product studies

| Repository | Focus | Caveat |
| --- | --- | --- |
| [ai-companion-pi](https://github.com/sonopdx/ai-companion-pi) | scheduled wakes, journaling, dashboard, requests | cloud/provider and messaging assumptions are not Suns Chan defaults |
| [OpenAlma](https://github.com/mekineer-com/OpenAlma) | local companion memory | inspect code maturity and scope before use |
| [Mimir's Memory Hub](https://github.com/Kronic90/Mimirs-Memory-Hub) | companion memory that fades/strengthens | check persistence guarantees in source |
| [companion-app](https://github.com/a16z-infra/companion-app) | lightweight hosted companion stack | useful UI/product reference, not core cognition |
| [AionsHome](https://github.com/death34018-hue/AionsHome) | self-hosted companion, voice, vision, smart home | high-risk integrations must remain outside MVP |
| [Noema](https://github.com/HappyFox001/Noema) | voice, emotion, tools, plugins | AGPL-3.0 reported; do not copy into a permissive core casually |

## Excluded until justified

- Repositories with missing/unclear licenses: study ideas, do not import code.
- AGPL/GPL systems: do not mix into Suns Chan without consciously accepting
  their licensing obligations.
- Projects that make only “self-aware”, “soul”, or benchmark claims without a
  clear event schema, tests, or reproducible execution path.
- Full UI, avatar, voice, social-network, or smart-home stacks before the
  core's event/retrieval/policy tests pass.

## Import rule

For every candidate that moves from catalog to `third_party/`, record:

1. upstream URL and immutable commit;
2. license plus notices;
3. exact files/functions being studied;
4. the one capability it would add;
5. why existing Suns Chan interfaces cannot cover it; and
6. a test proving the integration does not weaken event provenance or action
   safety.
