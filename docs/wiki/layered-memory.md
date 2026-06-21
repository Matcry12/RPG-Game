# Layered Memory (SQLite + ChromaDB)

> Priority skill #2. Spec: `../npc-agent-service/v2/plan.md` §5.2. Status: **todo**.

## Architecture

- **SQLite (authoritative, structured):** `players`, `npcs`, `disposition`, `quests`, `inventory`, `flags`.
- **ChromaDB (fuzzy, vector):** `lore` collection (static world facts, seeded once) + `episodic`
  collection (per `(npc_id, player_id)` events with timestamp + importance).
- **Retrieval per turn** = relevant lore (semantic) + relevant episodic (scored) + authoritative state from SQLite.

## Memory stream — CORE (Generative Agents, Park et al. 2023)

- Score each episodic memory by `recency × importance × relevance`; retrieve top-k.
- Periodic **reflection**: compress recent episodics into higher-level beliefs, store back as new higher-importance memories.

## Open questions

- How to compute/assign `importance` at write time (LLM-scored vs heuristic)?
- Reflection cadence — every N events, or time-based?
- Embedding model for Chroma (reuse Rabbook's? latency impact?).

## Findings

_(record research here with citations)_

## References

- Park et al. 2023, "Generative Agents: Interactive Simulacra of Human Behavior" — _(add link)_
