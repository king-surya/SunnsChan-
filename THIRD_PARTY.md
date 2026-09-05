# Third-party source inventory

These repositories were explicitly named in the referenced conversation. They
were downloaded on 2026-09-04 as independent, shallow Git clones and retain
their own `.git` metadata and `LICENSE` files. All four advertise the MIT
License. Review each repository's notices and current upstream state before
redistributing, modifying, or copying code.

| Folder | Upstream | Snapshot commit | License | Kept as reference for |
| --- | --- | --- | --- | --- |
| `third_party/paulusai` | https://github.com/Pavel6625/PaulusAI | `42092087fa8134e7bbec722dde40d71378a48f5a` | MIT | tool approval boundaries, durable facts, consolidation and provider boundary |
| `third_party/crescent-grove` | https://github.com/Canon-73/crescent-grove | `71cb116ff70f039706e7de81bae6625355ad4625` | MIT | event continuity, episodic lifecycle and autonomous-loop design |
| `third_party/emotion-memory-graph` | https://github.com/MValeResearch/emotion_memory_graph | `6c7c1928f366a536fb682cb5e9c0d97c702e1074` | MIT | graph/relationship retrieval as a future optional backend |
| `third_party/persistent-ai-memory` | https://github.com/savantskie/persistent-ai-memory | `028a09265da950adcc9450df9fec9e311f6db9ee` | MIT | portable SQLite memory operations and multi-client boundaries |

## Deliberate non-merges

No code was copied from these projects into `src/suns_chan`. The core is an
original, minimal integration point. Specifically, it does not inherit:

- provider-specific chat/UI integrations;
- Telegram, MCP, OpenWebUI, or Neo4j dependencies;
- any execution sandbox or tool implementation;
- an emotion/personality simulation; or
- each project's incompatible database schema.

Those are useful reference designs, but merging them wholesale would create
conflicting lifecycles, dependencies, data ownership, and security assumptions.
