# backend — NPC Agent Service

Python backend powering stateful, tool-using LLM NPCs. FastAPI + LangGraph + SQLite (authoritative
state) + ChromaDB (fuzzy memory). Brain: Groq free tier → local Gemma 3n failover via LangChain.

Design docs live in `../docs/npc-agent-service/v2/` (plan, implementation tickets, diagrams).
Build order: vertical slices S0–S11 (see `implementation.md`).

Layout (created as slices land):

```
backend/
├── app/
│   ├── api/        # FastAPI routers: talk, state, world
│   ├── graph/      # LangGraph nodes + graph build
│   ├── memory/     # sqlite_store.py, vector_store.py, stream.py
│   ├── tools/      # schemas.py (Pydantic), gates.py (propose/dispose)
│   ├── serving/    # llm.py (ChatGroq+ChatOllama), tool_parse.py
│   ├── config.py   # feature flags
│   └── main.py     # app entry
├── eval/           # judge.py, run_ablation.py, run_redteam.py, dataset/
├── tests/          # mirror app/ layout
└── pyproject.toml
```
