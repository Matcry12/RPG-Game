# shared — contracts + lore

Assets both `backend/` and `game/` depend on. Single source of truth, so the two sides cannot drift.

- **`contracts/`** — the `/npc/{id}/talk` request/response (and WS event) schema. The seam between
  game and backend. Owned here; neither side redefines it.
- **`lore/`** — the LLM-generated, hand-curated lorebook JSON. The backend embeds it into Chroma;
  the game may surface parts of it (journal, codex).
