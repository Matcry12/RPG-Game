# ADR-0011 — Memory stream: weighted retrieval (S6)

**Date:** 2026-06-25  
**Status:** Accepted  
**Relates to:** plan §5.2, §8 S6; ADR-0006 (episodic write policy)

---

## Context

S3 introduced plain ChromaDB cosine-similarity recall for episodic memories: top-k by
embedding distance only. This ignores two signals the Park et al. 2023 Generative Agents
paper identifies as essential for human-like memory:

- **Importance** — how salient was the event when it happened (stored at write time as 1–10)?
- **Recency** — recent events decay in relevance over time via exponential decay.

With pure cosine recall, a trivial recent message ("chatted about the weather") can rank above
a high-importance older event ("helped slay the bandit lord") simply because recent turns use
language similar to the current query. This hurts persona consistency and is the wrong default
for an "agent engineer" portfolio.

The `MEMORY_STREAM` feature flag (off by default) must gate this behaviour so the ablation
table in S8 can compare with/without scored retrieval.

---

## Decision

At S6, replace plain cosine recall with a **three-component weighted score** when
`settings.memory_stream = True`:

```
score = α·recency + β·importance + γ·relevance
```

| Component | Formula | Notes |
|-----------|---------|-------|
| `recency` | `exp(-0.1 · hours_since_event)` | half-life ≈ 7 h; events older than ~48 h decay below 0.01 |
| `importance` | `stored_score / 10` → [0, 1] | written at turn time (ADR-0006) |
| `relevance` | `1 - min(1, cosine_distance)` | Chroma returns cosine distance ∈ [0, 2] |

Default weights: `α = β = 0.35`, `γ = 0.30` (paper-faithful equal-ish weighting; tunable).

**Implementation:**
- `backend/app/memory/stream.py` — `score_memories(candidates, now)` pure function.
- `backend/app/memory/vector_store.py` — `retrieve_episodic_scored()`: fetches `k×4`
  candidates from Chroma (wider net), re-ranks via `score_memories`, returns top-k.
- `backend/app/graph/nodes.py` — `retrieve_context` branches on `settings.memory_stream`.
- `backend/app/config.py` — `memory_stream: bool = False` (off → S3 plain recall; ablation switch).

**Verified (unit):** salient 6h-old memory (importance=9) outscores trivial 5-min-old
(importance=2) at equal relevance. Math: `0.35·exp(-0.6)+0.35·0.9+0.30·0.9 = 0.777` vs
`0.35·exp(-0.008)+0.35·0.2+0.30·0.9 = 0.687`.

---

## Alternatives considered

- **Keep plain cosine recall** — rejected; fails the S8 ablation story and the portfolio goal.
- **Normalize scores across candidates before summing** (paper-exact) — more complex, requires
  two passes; deferred until S8 if scores look miscalibrated in practice.
- **Store `last_access` and update on recall** — adds a Chroma write on every read; skipped for
  MVP (timestamp at write time is a reasonable proxy within a single demo session).

---

## Consequences

- `memory_stream = False` (default) → S3 behaviour, no change to existing tests.
- `memory_stream = True` → scored recall; weight constants in `stream.py` are the tuning knob
  for S8 ablation experiments.
- S7 (reflection / beliefs) reuses `write_episodic` with high importance scores (≥8) —
  the stream scorer will naturally surface beliefs above low-importance chat turns.
- The `importance` score written by `write_memory` (currently `5 if len > 80 else 3`) is a
  provisional heuristic; real salience scoring (LLM-rated 1–10) can replace it in S6+ without
  changing the scoring formula.
