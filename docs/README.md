# Docs Wiki — NPC Agent Service

Research-and-reference wiki for the project. Two layers, on purpose:

- **`docs/wiki/`** — human-readable, git-tracked research pages (the durable knowledge base you
  read and review). Author these when you research a topic (LangGraph checkpointers, GBNF grammars,
  Generative Agents memory stream, vLLM serving, etc.).
- **OMC wiki (`.omc/wiki/`, git-ignored)** — the *queryable* keyword/tag index. Use the wiki skill
  tools (`wiki_ingest`, `wiki_query`, `wiki_list`, `wiki_lint`) for fast retrieval mid-session.
  When a finding matters long-term, promote it into a `docs/wiki/` page here.

## Index

| Page | Topic |
|------|-------|
| [npc-agent-service/v2/plan.md](./npc-agent-service/v2/plan.md) | Full build spec v2 (source of truth for scope) |
| [npc-agent-service/v1/draft_plan_v1.md](./npc-agent-service/v1/draft_plan_v1.md) | Original draft (kept for diff only) |
| [decisions/](./decisions/) | **Decisions log (ADRs)** — every decision recorded here, one file each |
| [wiki/00-research-index.md](./wiki/00-research-index.md) | Research topic tracker |
| [wiki/durable-agent-state.md](./wiki/durable-agent-state.md) | LangGraph threads + checkpointers |
| [wiki/layered-memory.md](./wiki/layered-memory.md) | SQLite + Chroma dual memory, memory stream |
| [wiki/propose-dispose-tools.md](./wiki/propose-dispose-tools.md) | Gated state-mutating tools |
| [wiki/serving-latency.md](./wiki/serving-latency.md) | Model serving, KV prefix cache, streaming |
| [wiki/eval-redteam.md](./wiki/eval-redteam.md) | LLM-as-judge eval + adversarial suite |

## How to research a topic

1. Check official docs first (LangGraph / FastAPI / ChromaDB / llama.cpp / vLLM).
2. Drop findings + citations into the relevant `docs/wiki/<page>.md` (create if missing).
3. `wiki_ingest` the same finding into the OMC wiki with tags so it's queryable later.
4. If it changes a project decision, also record it in `../MEMORY.md`.
