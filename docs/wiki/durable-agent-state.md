# Durable Agent State (LangGraph)

> Priority skill #1. Spec: `../npc-agent-service/v2/plan.md` §5.1. Status: **todo**.

## What we need

- Each NPC = a LangGraph **thread**, keyed by `npc_id` (+ optional `player_id` for per-player relationships).
- A **checkpointer** so conversation + working state survive process restarts (SQLite saver for MVP, Postgres later).
- Graph nodes: `retrieve_context` → `plan_response` → `propose_tools` → `grounding_gate` → `generate_reply` → `write_memory`.

## Open questions

- How does the SQLite checkpointer scale with many concurrent NPC threads?
- Thread-id scheme: `f"{npc_id}:{player_id}"` vs separate namespaces?
- When to migrate to Postgres checkpointer?

## Findings

_(record research here with citations: URL + date accessed)_
