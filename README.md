# RPG-Game

A role-playing game with **stateful, tool-using LLM NPCs** — characters that remember the player
across sessions, answer grounded in world lore, and take real, validated in-game actions.

The headline idea: **the LLM never owns truth.** Anything that must be correct (does the player hold
the key? is the quest done? what's the disposition score?) lives in SQLite and is checked by
deterministic code. The model *proposes* actions; code *disposes* (validates) them against ground
truth.

## Repository layout (monorepo)

| Folder | What's in it |
|--------|--------------|
| [`backend/`](backend/) | **NPC Agent Service** — Python: FastAPI + LangGraph + SQLite (authoritative) + ChromaDB (fuzzy memory). The brain, the gate, the memory stream. |
| [`game/`](game/) | **Godot client** — renders streamed NPC dialogue, sends player utterances over HTTP/WS. |
| [`shared/`](shared/) | **Contracts + lore** both sides depend on. `contracts/` is the `/talk` schema (single source of truth); `lore/` is the curated lorebook JSON. |
| [`docs/`](docs/) | Design docs spanning the whole system — the build plan, ADRs, and flow diagrams. |

> Decision records explaining the monorepo split, the brain choice, and the build approach live in
> [`docs/decisions/`](docs/decisions/) (ADR-0003, ADR-0001, ADR-0002).

## How it fits together

```
[ Godot client ] --HTTP/WS JSON (shared/contracts)--> [ FastAPI ] --> NPC Agent Service (backend/)
```

Per turn, the backend runs a LangGraph pipeline:
`retrieve_context → plan_response → propose_tools → grounding_gate → generate_reply → write_memory`.
The **propose/dispose loop** is the spine — every state-mutating tool call is validated against SQLite
before it's allowed, and every accepted call is written to episodic memory.

## Brain

Groq free tier (`llama-3.3-70b-versatile`) primary → local Ollama Gemma 3n failover, unified through
LangChain `.with_fallbacks()`. $0 to run; the gate never trusts either model. See
[ADR-0001](docs/decisions/0001-groq-primary-brain-local-fallback.md).

## Status

Pre-code. Building in **vertical slices** S0–S11 (each one end-to-end and demo-able) — see
[`docs/npc-agent-service/v2/implementation.md`](docs/npc-agent-service/v2/implementation.md).
Next: **S0** — FastAPI + `ChatGroq` streaming a persona reply.

## Getting started

The backend isn't scaffolded yet (lands with S0). Once it is:

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e .
cp .env.example .env        # add your GROQ_API_KEY
uvicorn app.main:app --reload
```

See [`backend/README.md`](backend/README.md) for the full backend layout and
[`docs/npc-agent-service/README.md`](docs/npc-agent-service/README.md) for the design-doc index.
