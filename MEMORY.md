# Project Memory — NPC Agent Service

Durable memory that survives across sessions. Newest entries on top. Keep entries short; link out for detail.

> **Where things go:**
> - **Mistakes / errors / things to avoid → this file** (section below). *Every* error or wrong turn gets recorded here so we never repeat it.
> - **Decisions → `docs/decisions/` (ADRs)**, one file per decision. This file only tracks resolved §9 questions + project state.
> - Research findings → wiki (`docs/wiki/` + `wiki_ingest`). Ephemeral scratch → `.omc/notepad.md`.

---

## Mistakes & Lessons (never repeat)

**Rule:** the moment something goes wrong — a bug we caused, a wrong assumption, a failed command, a
dead-end approach, a misread of the spec — record it here *before moving on*. Each entry is a tripwire
for the future.

_Format each as:_
> ### YYYY-MM-DD — <what went wrong, one line>
> **What happened:** … **Root cause:** … **Fix / what to do instead:** … **Watch for:** <how to catch it early next time>

### 2026-06-21 — Project memory file is `MEMORY.md` (uppercase), and a bulk sed broke link targets
**What happened:** Wrote the memory file as `memory.md`; it persisted on disk as `MEMORY.md`. References and the SessionStart hook pointed at `memory.md`, which doesn't resolve on case-sensitive Linux. Fixing it with `sed 's/memory.md/MEMORY.md/g'` also rewrote the `layered-memory.md` wiki link to a dead `layered-MEMORY.md`.
**Root cause:** (1) The memory file is canonically `MEMORY.md` here; (2) the sed pattern was too broad — it matched `memory.md` inside the unrelated filename `layered-memory.md`.
**Fix / what to do instead:** The project memory file is **`MEMORY.md`** — always reference it uppercase. When doing a global replace on a substring that also appears inside other filenames, anchor the pattern (e.g. `\bmemory\.md\b` or include the leading `/`/space) and grep-verify link targets exist afterward.
**Watch for:** Markdown links to `*-memory.md` wiki pages (lowercase) vs the `MEMORY.md` index — they are different files; don't let a replace conflate them.

---

## Decisions

Decisions live in **`docs/decisions/`** (one ADR per decision). Do not log decision rationale here —
write an ADR. This section is intentionally just a pointer.

---

## Resolved open questions

Resolved in `docs/npc-agent-service/v2/plan.md` §10 (were §9 in v1).

| # | Question | Status | Answer |
|---|----------|--------|--------|
| 1 | GPU or CPU-only target? | **resolved** | GTX 1660 SUPER 6GB. Primary brain is Groq (remote, free) so local HW never gates the demo; local Gemma 3n `e2b` runs partial-offload as failover. vLLM = cloud path only. ([ADR-0001](docs/decisions/0001-groq-primary-brain-local-fallback.md)) |
| 2 | Which model? | **resolved** | Groq `llama-3.3-70b-versatile` primary; Ollama Gemma 3n `e2b` (→`e4b` if it fits) local failover, both via LangChain. ([ADR-0001](docs/decisions/0001-groq-primary-brain-local-fallback.md)) |
| 3 | Per-player relationships in MVP, or global first? | **resolved** | Per-player from day 1, keyed by `(npc_id, player_id)`. Single demo player. |
| 4 | Sync request/response, or async pre-generation OK? | **resolved** | Sync request/response with token streaming; reflection as background pass. |
| 5 | Lore authoring — hand-written or LLM-generated JSON? | **resolved** | LLM-generated structured JSON lorebook, hand-curated, embedded into Chroma. |

---

## Current phase

**Slices S0 → S1.** First task: stand up FastAPI + LangChain `ChatGroq` streaming a persona reply (S0), then the Pydantic tool schemas and propose/dispose gate (`UpdateDisposition`) against a minimal SQLite schema (the spine — `docs/npc-agent-service/v2/plan.md` §11, tickets in `docs/npc-agent-service/v2/implementation.md`). Build approach: vertical slices ([ADR-0002](docs/decisions/0002-vertical-slice-build-approach.md)).

## Project state snapshot

- Monorepo: `backend/` `game/` `shared/{contracts,lore}/` `docs/` created 2026-06-22 (ADR-0003); no code yet — `backend/app/` not created until S0.
- Harness set up: 2026-06-21 (CLAUDE.md, MEMORY.md, docs wiki, .claude/settings.json).
