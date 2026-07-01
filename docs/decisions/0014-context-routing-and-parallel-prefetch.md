# ADR-0014 — Context routing and parallel lore pre-fetch

**Status:** Accepted  
**Date:** 2026-06-29

## Context

Every turn previously ran the same retrieval pipeline in sequence:
disposition → episodic → beliefs → lore. Lore retrieval (LightRAG → Groq HTTP)
takes ~500–1000 ms and dominates turn latency, but many turns don't need it at
all (greetings, acks, short follow-ups). Episodic and beliefs were also blocked
behind lore even though they're fast synchronous ChromaDB calls.

Additionally, `memory_stream: bool` created two code paths for episodic retrieval.
The scored path (S6) is strictly better; the flag was dead weight.

## Decision

1. **3-way heuristic router (`classify_turn` node)** runs before `retrieve_context`:
   - `trivial` (≤ 4 words, no `?`) → skip all retrieval, straight to agent.
   - `full-no-lore` → episodic + beliefs only, skip lore.
   - `full-with-lore` (message contains a lore-domain noun) → full retrieval.

2. **Parallel lore pre-fetch**: for `full-with-lore`, lore is started as an
   `asyncio.create_task` immediately. `await asyncio.sleep(0)` yields the event
   loop so lore's first HTTP request is in flight before the synchronous
   ChromaDB reads run. Total latency ≈ max(episodic+beliefs, lore) instead of
   their sum.

3. **`memory_stream` flag removed**: `retrieve_episodic_scored` (S6 scoring) is
   always used. The unscored `retrieve_episodic` path is gone.

4. **`history_window` default 10 → 2**: scored episodic events cover older
   context; raw history window is for the last two turns only.

5. **`_persona_system` lore logic**: uses `lore_block` presence rather than the
   `grounded` bool. `grounded=None` means lore was intentionally skipped
   (trivial or full-no-lore); no "no records" disclaimer is added in that case.

## Router implementation note

The router is a keyword heuristic (`_LORE_DOMAIN` frozenset). It is accurate
enough for the RPG domain and adds zero dependencies. `semantic-router` +
`fastembed` (local free model, ~2 ms) can replace it when ML-accuracy routing
is needed — the interface is the same `{"route": str}` dict.

## Consequences

- Trivial turns skip all vector DB reads → faster response, fewer tokens.
- Lore latency no longer blocks episodic/beliefs for full turns.
- Single episodic code path is simpler and always uses the better scorer.
- Ablation table's `+stream` condition (env `MEMORY_STREAM`) becomes a no-op
  (field removed from Settings); historical results are unaffected.
