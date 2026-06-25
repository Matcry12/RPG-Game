# 0010 — Use LightRAG (per-NPC graphs, mix mode) for lore grounding

- **Status:** Accepted
- **Date:** 2026-06-25
- **Relates to:** `../npc-agent-service/v2/plan.md` §S5; `../../MEMORY.md`; [ADR-0006](0006-episodic-memory-write-and-recall-policy.md)

## Context

S5 goal: NPC answers in-lore questions correctly; declines out-of-lore questions instead of
hallucinating. The spec originally chose traditional Chroma vector RAG as the lore store. Two
alternatives emerged: Chroma (document-centric) and agentic RAG (looped query planning).

Lore is relational — entities connect to places, factions, histories. Traditional RAG retrieves
isolated chunks and misses cross-entity relationships. Agentic RAG loops cost tokens at scale.

LightRAG (HKUDS, arXiv 2410.05779, version 1.5.4) builds a knowledge graph at index time using LLM
entity/relationship extraction, then at query time does dual-level retrieval: LLM keyword extraction
(low-level → entities, high-level → relations) → graph traversal + vector search. Verified against
the paper.

LightRAG has no Chroma-style per-document metadata filter — its graph is global per `working_dir`.
This commits us to per-NPC isolation via separate working directories.

## Decision

**1. LightRAG replaces Chroma for the lore store.** Chroma remains for `episodic` (temporal,
per-player, metadata-filtered) and later `beliefs`. Two stores chosen by data shape: graph-relational
(lore) vs temporal (episodes).

**2. Per-NPC `working_dir` at `data/lightrag/<npc_id>/`.** Each NPC's graph is seeded only from the
lore categories listed in its persona frontmatter (`lore_categories`). True structural isolation —
the shopkeeper's graph never contains military lore. Trade-off: shared "general" lore is re-extracted
per NPC (negligible for a small lorebook).

**3. `mode="mix"` + `only_need_context=True`.** Mix mode gives keyword extraction + graph traversal +
raw-text semantic search — maximum recall for grounding. `only_need_context=True` returns context
without LightRAG generating an answer; our Groq persona agent is the only voice.

**4. `conversation_history` (last 2 turns) passed into QueryParam** for pronoun/follow-up
disambiguation at keyword extraction time.

**5. Grounding signal = context length threshold (`lore_context_min_chars=100`).** Empty/thin context
→ `grounded=False` → inject decline instruction. Non-trivial context → inject as lore block with
"speak only to what this confirms." No second LLM verification call.

**6. Gated by `GROUNDING_GATE` feature flag** — disableable for the S8 ablation comparison.

**7. Lore is hand-authored** (human + Claude), not LLM-generated. The lorebook JSON lives in
`shared/lore/lorebook.json`.

## Alternatives considered

- **Chroma (original spec choice)** — rejected: isolated chunks miss cross-entity context; grounding
  accuracy depends on query → dense embedding mismatch.
- **Agentic RAG (multi-turn planning)** — rejected: each query planning loop costs extra Groq calls;
  linearity (single turn per player action) is hard to break at S5.
- **LightRAG global graph (single working_dir)** — rejected: NPC-specific lore categories require
  isolation; a single graph conflates categories and leaks data.

## Consequences

- **+** Dual-level retrieval (keyword extraction + graph traversal) captures cross-entity context.
- **+** Per-NPC graphs guarantee data isolation without semantic filtering overhead.
- **+** Mix mode provides maximum recall; `only_need_context=True` preserves the LLM voice.
- **+** Grounding signal is cheap (context length) — no extra LLM calls per turn.
- **−** Each grounded turn costs one Groq keyword-extraction call (LightRAG mix mode). Acceptable at
  demo scale; gated by the flag.
- **−** Index-time entity/relationship extraction requires Groq calls at `/world/seed` — one-time,
  idempotent.
- **−** Adding a new NPC requires: persona file with `lore_categories` frontmatter + re-running
  `/world/seed`.
- **−** `initialize_pipeline_status` does not exist in LightRAG 1.5.4; `auto_manage_storages_states=True`
  handles storage lifecycle automatically (differs from older versions).
- **Affected (now):** `backend/app/memory/` (new LightRAG wrapper), `backend/app/graph/` (lore
  retrieval node), `backend/app/serving/` (grounding gate), `backend/pyproject.toml` (lightrag).
- **Affected (at S8):** Ablation table (`GROUNDING_GATE` on/off) comparing lore grounding accuracy
  vs token cost.
