# ADR-0013 — S7 Reflection Pass: Importance Accumulator → Belief Formation

**Status:** Accepted  
**Date:** 2026-06-26  
**Relates to:** ADR-0006 (episodic write policy), ADR-0011 (memory stream scoring)

---

## Context

After S6, episodic recall ranks memories by recency + importance + relevance. But all retrieved
memories are raw events ("your opinion shifted by -8"). The NPC has no mechanism to form a
persistent *conclusion* about the player across turns — no equivalent of "despite individual
moments, I've decided this person is trustworthy."

The goal of S7 is a **reflection pass**: when enough significant events accumulate, the NPC
privately synthesizes them into a single belief that persists and informs future turns.

---

## Decision

### Importance scoring (final)

Only tool-fired events carry importance. Plain turns are always 0 — if something mattered, the
model already signalled it by calling a tool.

| Event | Importance |
|-------|-----------|
| Plain turn | 0 |
| `StartQuest` | `settings.importance_start_quest` (default 5) |
| `GiveReward` | `settings.importance_give_reward` (default 7) |
| `UpdateDisposition(delta)` | `min(settings.importance_max, abs(delta))` |

All values live in `config.py` as named fields so they can be tuned without grep.

### Reflection trigger

A per-`(npc_id, player_id)` importance accumulator lives in SQLite
(`importance_accumulator` table). Each accepted tool call adds its importance score to the
accumulator. When the total reaches `settings.reflection_threshold` (default 20), the reflection
fires and the accumulator resets to 0.

Threshold of 20 ≈ 2–3 major disposition swings OR 4 quest completions — enough to form a real
pattern, not a snapshot.

### Retrieval for reflection

At reflection time there is no natural query, so `collection.query()` (embedding-based) is the
wrong tool. Instead: `collection.get()` with `where importance >= reflection_min_importance`
metadata filter, sorted by recency in Python, top 10. This returns only meaningful events
(tool results), skipping plain-turn noise entirely.

### Reflection prompt (Option B — forced single conclusion)

> "Despite any contradictions, what is your SINGLE strongest feeling or conclusion about this
> player? One sentence, in your voice, first person."

Option B forces a conclusion rather than a wishy-washy summary. The NPC commits to a belief.

### Beliefs collection

Reflection output is written to a separate `beliefs` Chroma collection (importance 9). Beliefs
are retrieved in `retrieve_context` alongside episodic recall when `REFLECTION=True`, and added
to the memory block as "Your current beliefs about this player."

### Feature flag

`settings.reflection = False` (default). When off, the accumulator never touches SQLite and
beliefs are never retrieved — S6 behaviour is fully preserved. The flag is the ablation switch
for S8.

---

## Alternatives considered

- **Option A (summary-style prompt):** "Summarize your feelings" — produces vague output. Rejected
  in favour of Option B's forced conclusion.
- **LLM importance rating at write time:** Would require an extra LLM call per turn (Groq cost)
  or a `claude -p` call (violates the `app/` architecture boundary). Deferred to S8 as an offline
  enrichment script.
- **Embedding-based reflection retrieval with a fake query:** "notable events with this player"
  as the query string. Rejected — manufactured queries introduce hallucinated relevance scores.
  Metadata filtering is the correct tool when relevance is not the goal.

---

## Consequences

- Reflection fires as a background step inside `write_memory` — adds one Groq call on threshold
  events only, not every turn.
- All tunable parameters (`reflection_threshold`, `reflection_min_importance`, importance values)
  are in `config.py` — one place to tune for S8.
- `beliefs` collection is a new Chroma collection; compatible with existing `episodic` collection.
- Tests cover: importance mapping, accumulator CRUD, beliefs write/retrieve, reflection retrieval
  metadata filter, and plain-turn zero-importance guard.
