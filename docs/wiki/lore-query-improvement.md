# Lore Query Improvement — Cross-Session Entity Resolution

**Status:** ⚠️ Partly superseded (2026-06-30). The **problem** (cross-session entity
resolution) still stands, but the **agreed solution below — `claude -p` Haiku rewrite
(option 2) — is rejected**: it violates the "no `claude -p` inside `app/`" production rule.
Replaced by ADR-0015, which emits keywords + a rewritten query from a single in-app **Groq**
extraction call. See [lore-retrieval-evaluation.md](lore-retrieval-evaluation.md). Read the
edge-case analysis below for context, not the implementation.
**Planned slice:** S6 (memory improvements).

---

## The Problem

LightRAG receives the raw player message as a search query. When the message is vague or uses pronouns, keyword extraction misses the right graph nodes:

- `"Any news about that missing person?"` → extracts `["missing person"]` → misses `CORVIN_DALE` node
- `"What about the maps he left?"` → extracts `["maps"]` → may miss the Corvin Dale → maps edge

This is especially bad **cross-session**: the player said "Corvin Dale" two sessions ago; today's message is `"Back to those maps we discussed"` with no entity name at all.

---

## Approaches Explored

### 1. Entity carry-forward in state (keyword matching)
Maintain `mentioned_entities` in TurnState. Scan player message + Mira's reply for known lore entities (string match). Append matching entities to the LightRAG query.

**Edge cases that break it:**
| Case | Example | Breaks because |
|---|---|---|
| Ambiguous pronoun | "What did he do?" (two male entities in scope) | Can't resolve "he" without LLM |
| Negative context | "Forget about Corvin Dale" | Mention adds him to list, biases next query |
| Generic keyword FP | "Do you have a map?" → triggers Corvin Dale | "map" too generic |
| Cross-session cold start | List cleared between sessions | Entity not in list yet, no hints available |
| Entity from Mira's reply | Mira mentions Corvin Dale unprompted → added | Player didn't ask, biases next query |

Mitigations (reduce but don't eliminate the edge cases):
- Per-entity keyword sets with 2+ keyword match requirement
- Alias table for common references ("the captain" → Aldric Voss)
- Relevance filter: only append entities whose keyword set matches the current message
- Sliding window by recency (last 3 turns only)

**Verdict:** Works for the common case (focused dialogue, one topic per turn). Fails on topic shifts, negative context, and cross-session cold start. Tunable heuristics, zero cost.

### 2. Haiku query rewriting (separate LLM call — agreed solution)
Before calling LightRAG, call Haiku via `claude -p` (zero API cost, uses Claude Code subscription):

```
Given this conversation:
User: "I'm looking for Corvin Dale"
Mira: "He disappeared six weeks ago..."
User: "Any news about that missing person?"

Rewrite the last message as an explicit search query. Output only the query.
→ "news about Corvin Dale's disappearance"
```

Send the rewritten query to LightRAG instead of the raw player message.

**Pros:**
- Solves ALL edge cases — Haiku has full context, handles pronouns, negation, topic shifts
- Zero API cost (claude CLI subprocess, already have `lore_llm.py` infrastructure)
- Persona agent stays clean and focused
- Failure isolated — if Haiku fails, fall back to raw message

**Cons:**
- One extra subprocess call per turn (~1–2s latency)
- Only needed on grounded turns (when `grounding_gate=True` and lore is queried)

### 3. Combined answer + entity extraction (rejected)
Ask Groq to output reply AND structured entities in one call. Rejected because:
- Parsing fragility (model may not follow format)
- Couples persona voice with memory concerns
- Against "LLM never owns truth" principle

---

## Agreed Implementation (when revisited)

Add a `rewrite_query` step inside `retrieve_context` in `backend/app/graph/nodes.py`, between the episodic recall step and the LightRAG call:

```python
# After episodic recall, before retrieve_lore:
if settings.grounding_gate:
    raw_query = message
    if lore_history:  # only rewrite if there's context to use
        rewritten = await rewrite_lore_query(message, lore_history)  # Haiku via claude CLI
    else:
        rewritten = raw_query
    lore_ctx = await retrieve_lore(npc_id, rewritten, history=lore_history, ...)
```

`rewrite_lore_query` lives in `backend/app/serving/lore_llm.py` alongside `claude_haiku_llm`. It spawns `claude -p --model haiku` with a terse rewriting prompt and the last 4 conversation turns.

Gate the rewrite call behind a feature flag (e.g. `lore_query_rewrite: bool = False` in config) so it can be ablated in S8 eval.

---

## Related files
- `backend/app/serving/lore_llm.py` — claude CLI subprocess wrapper (already exists)
- `backend/app/graph/nodes.py` — `retrieve_context` node where the call goes
- `backend/app/memory/vector_store.py` — `retrieve_lore` signature
- `docs/decisions/0010-lightrag-lore-grounding.md` — ADR for the LightRAG retrieval design
