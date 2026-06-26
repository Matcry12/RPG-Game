# ADR-0012 — Keep episodic memory (ChromaDB) for portfolio/ablation; plain history is nearly as good for short demos

**Date:** 2026-06-25  
**Status:** Accepted  
**Relates to:** plan §5.2, §8 S6; ADR-0006, ADR-0011

---

## Context

After S6 shipped, we discussed whether ChromaDB episodic memory is actually useful or just
overhead. The question: given that LangGraph already persists full conversation history via
`AsyncSqliteSaver` and we inject the last 10 turns into every prompt, what does episodic
recall actually add?

---

## Current flow (what we have)

Every turn, `write_memory` writes two things to Chroma:
1. **The turn itself** — `'Player said: "...". You replied: "...".'` — importance 3 or 5.
2. **Accepted tool events** — e.g. `"Opinion shifted by -3 (now 45)."` — importance 8.

At the start of the next turn, `retrieve_context` pulls the top-3 by stream score
(`α·recency + β·importance + γ·relevance`, ADR-0011) and injects them as `memory_block`
into the system prompt.

LangGraph's `AsyncSqliteSaver` persists the **full** conversation history across sessions,
but `nodes.py` only injects `history[-10:]` into the prompt (bounded context window).

---

## The honest quality assessment

**Plain history (last N turns) beats episodic recall for short demos because:**
- Verbatim context is always more accurate than retrieved text summaries.
- No retrieval errors, no wrong memory surfaced.
- Zero extra complexity: no Chroma, no `write_memory`, no `retrieve_episodic_scored`.
- LightRAG already covers the "world knowledge" side well.

**Episodic recall only meaningfully improves quality when:**
1. A single session exceeds the history window (> 10 turns) — early events fall off the prompt.
2. The player returns in a new session — checkpoint has full history but only last 10 are injected.

For a short single-session demo (5–10 turns), episodic adds almost nothing.
You could raise `HISTORY_WINDOW_MESSAGES` to 20–30 and get cross-session recall "for free"
from the LangGraph checkpoint at the cost of a larger prompt — no Chroma needed.

---

## Why we keep it anyway

**1. Portfolio centerpiece.** The build plan (§1) explicitly names "layered, durable,
per-player memory" as the top-priority differentiator for the "Applied AI / Agent Engineer"
target role. Removing ChromaDB episodic leaves only Groq + LightRAG + gate — solid, but
missing the memory depth story.

**2. S8 ablation table requires it.** One of the ablation rows is "with/without episodic
memory." Without it we can't run that comparison or make the measurable claim that episodic
recall improves quality. The table is the portfolio's proof-of-work.

**3. S7 (reflection) builds on it.** Reflection writes high-importance beliefs to the same
Chroma store. Removing episodic would also kill S7, the "money-shot" demo
("the NPC independently concluded you're untrustworthy").

---

## Decision

Keep ChromaDB episodic memory. Accept that it adds minimal quality benefit for short demos
and its real value is cross-session recall + the S8 ablation story.

**If the ablation table in S8 shows near-zero delta** (episodic on vs off), that is itself
a valid finding to report — "the gate and lore grounding drive quality; episodic adds recall
depth but not measurable single-session quality." That is an honest ML engineer result.

**If we ever cut scope** (ship before S7/S8), episodic is the first thing to remove:
delete `write_memory` writes, `retrieve_episodic*` calls in `retrieve_context`, and the
ChromaDB dependency. The rest of the system is unaffected.
