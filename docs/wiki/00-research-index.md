# Research Index

Tracks the research threads tied to the 5 priority skills in `../npc-agent-service/v2/plan.md` §1.
Status: `todo` · `researching` · `decided`. Promote queryable findings via `wiki_ingest`.

| Page | Priority skill | Status | Key open question |
|------|----------------|--------|-------------------|
| [durable-agent-state](./durable-agent-state.md) | 1. Durable agent state | todo | SQLite vs Postgres checkpointer; thread keying by npc_id + player_id |
| [layered-memory](./layered-memory.md) | 2. Layered memory | todo | recency×importance×relevance scoring; reflection cadence |
| [propose-dispose-tools](./propose-dispose-tools.md) | 3. Gated tools | todo | GBNF grammar generation from Pydantic; gate rejection feedback loop |
| [serving-latency](./serving-latency.md) | 4. Serving & latency | todo | GPU vs CPU; KV prefix cache mechanics; latency budget |
| [eval-redteam](./eval-redteam.md) | 5. Eval & red-team | todo | judge calibration set size; attack taxonomy |

## Conventions

- One page per priority skill. Sub-topics become `## sections`, not new files, until a page gets large.
- Every claim that came from a source gets a citation (URL + date accessed).
- When a finding resolves a `docs/npc-agent-service/v2/plan.md §10` open question, update `../../MEMORY.md`.
