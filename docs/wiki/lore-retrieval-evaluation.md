# LightRAG Retrieval — Live Output Evaluation & Token Optimization

**Status:** For review (2026-06-30). Proposes optimizations; nothing implemented yet.
**Feeds:** ADR-0015 (mix mode + pre-extracted keywords). Supersedes the `claude -p`
rewrite approach in [lore-query-improvement.md](lore-query-improvement.md).

---

## Method

Ran the **actual** `retrieve_lore()` (mode=`naive`, the current production setting) against
the real seeded `shopkeeper` graph — **65 entities, 15 chunks** in
`data/lightrag/shopkeeper/`. No LLM/Groq call (naive uses local MiniLM embeddings only).
Three queries: a direct one, a precise-entity one, and a deliberately vague one.

---

## Raw output (verbatim, truncated)

### Q1: "what happened to the old mill?" — 4327 chars (~1100 tokens)
```
Document Chunks (Each entry has a reference_id refer to the `Reference Document List`...):
```json
{"reference_id": "", "content": "The old mill at the western edge of the Market District
has been derelict since the Great Fire fifteen years ago... The bandit camp led by Rook is
in the woods roughly half a mile east of the mill along the old cart track."}
{"reference_id": "", "content": "Fifteen years ago, the Great Fire destroyed half the
western market district... rumours of arson by a rival trading faction have never died."}
{"reference_id": "", "content": "A bandit camp has been established in the woods east of
the derelict old mill... led by a disgraced former city guard named Rook..."}
... (more chunks)
```

### Q2: "who is Rook?" — 804 chars (~200 tokens)
```
Document Chunks (...):
```json
{"reference_id": "", "content": "A bandit camp has been established in the woods east of
the derelict old mill. The camp is rumoured to number around twenty, led by a disgraced
former city guard named Rook whose real name is believed to be Edric Marne. Rook was
dismissed from the watch five years ago for extortion. The camp has been raiding merchant
wagons on the eastern road, which is connected to the missing Dwarven amber caravans."}
```
Reference Document List (...):
```

```      ← EMPTY
```

### Q3: "tell me about that guy near the mill" (vague) — 3957 chars
Returned the mill + Rook chunks correctly despite no entity name in the query.

---

## Evaluation

| Dimension | Verdict | Evidence |
|---|---|---|
| **Recall** | ✅ Good | All 3 queries found correct chunks; vague Q3 still surfaced Rook |
| **Precision (short Q)** | ✅ Good | Q2 returned exactly the one relevant chunk |
| **Precision (broad Q)** | ⚠️ Loose | Q1 pulled ~4 chunks / ~1100 tok for one question |
| **Token efficiency** | ❌ Bad | ~20–50% of payload is boilerplate (see below) |
| **Graph utilization** | ❌ Wasted | naive returns raw chunks only; 65 entities + relations unused |

**Three concrete waste sources:**
1. **Empty citation scaffold every turn.** `reference_id` is `""` on every chunk and the
   "Reference Document List" is empty — the preamble explaining it is pure overhead.
2. **No effective size cap.** Defaults `lore_top_k=10`, `chunk_top_k=20`,
   `max_total_tokens=30000` are far larger than NPC dialogue needs.
3. **Paid-for graph thrown away.** Ingest extracted 65 entities + relationships; naive
   mode ignores all of it.

**Measured boilerplate-strip savings** (extract `content` values → plain sentences):

| Query | raw chars | content-only | saved |
|---|---|---|---|
| old mill | 4327 | 3577 | 17% |
| who is Rook? | 804 | 404 | **50%** |
| vague mill | 3957 | 3277 | 17% |
| **total** | **9088** | **7258** | **20%** |

Content-only "who is Rook?" output is clean and complete — no quality loss.

---

## Decision (proposed — for review)

Ship in this order; first two are free and mode-independent:

### 1. Strip boilerplate → content-only  ✅ do first
~5-line regex in `retrieve_lore()`: pull `content` values, join as sentences.
**−20% tokens overall, −50% on short turns.** Zero quality loss, no API call.

### 2. Cap retrieval size  ✅ config only
- `lore_top_k` 10 → **4** (existing Settings field)
- `chunk_top_k` 20 → 6, `max_total_tokens` 30000 → **2000** (via QueryParam)

Caps the broad-query case (~1100 tok → ~400) with no recall loss for single-question turns.

### 3. naive → mix, staged behind `lore_query_mode`  ✅ implemented 2026-06-30 (default off)
Uses the 65-entity graph we already built (for "who is Rook?", returns his entity node +
relationships, not just the closest chunk). Cost +1 LLM call (~610 tok) with pre-extracted
keywords (per ADR-0015). Shipped behind `lore_query_mode` (default `naive`); a
history-aware app-side rewrite (`extract_lore_query`) resolves vague references before
retrieval. **Live-verified:** 2-turn "who is he?" → rewrite "Who is the bandit Rook?" → mix
returns the Rook chunk; naive on the raw message misses. Still **A/B naive vs mix in the
ablation before flipping the default.**

---

## End-to-end turn by route (2026-06-30, mix on)

Ran one message per route through the full graph (`graph.ainvoke`), isolated players,
`lore_query_mode=mix`. Confirms the cheaper routes skip lore entirely and the lore route
grounds the reply in the graph.

| Route | Message | grounded | lore_block | reply grounded in lore? |
|---|---|---|---|---|
| trivial | "hello there!" | None (not attempted) | 0 chars | n/a — short-persona greeting |
| full-no-lore | "I would like to trade something with you" | None | 0 chars | n/a — in-character trade reply, no lore fetch |
| full-with-lore | "what happened to the bandit Rook?" | **True** | **1039 chars** | ✅ Rook east of old mill, eastern-road raids, missing Dwarven amber caravans — all from lorebook |

Sample lore-route reply (grounded, no hallucination):
> "Rook, the bandit leader… His camp, set up east of the old mill, has been raiding wagons on
> the eastern road, and I've heard rumors he's behind the missing Dwarven amber caravans…"

**Takeaways:** routing gates cost correctly (trivial/no-lore make zero lore calls); the
lore turn passes the grounding gate and the reply stays inside what the lore confirms.

---

## Latency (2026-06-30, measured)

| Operation | Latency | Notes |
|---|---|---|
| naive cold (first call) | ~500 ms | one-time `get_lore_rag` init + storage load per NPC/process |
| naive warm | ~140–170 ms | embedding + vector chunk search |
| mix warm (retrieval only) | ~155–185 ms | entity graph + community + chunks |
| extraction LLM call | ~500–900 ms | the query rewrite (LLM round-trip, variable) |

**Mix retrieval is NOT slower than naive** (~170 ms both) — pre-populated keywords skip
LightRAG's extraction and `only_need_context` skips synthesis, so graph lookups are cheap.
All of mix's added latency is the one extraction call (~0.5–1 s), which runs serially before
the lore fetch (episodic also needs the rewrite). Per-turn cost: naive ≈150 ms vs mix ≈0.7–1.1 s.
Lore cache makes repeats ~0 ms.

---

## Naive vs Mix — measured comparison (2026-06-30)

Ran both modes against the real `shopkeeper` graph. Each vague case supplies 2 turns of
history; "hit" = the correct target entity appears in the returned lore. Mix made 1 real
extraction call per query (Kira→Groq).

| Case | Message | naive | mix | rewrite |
|---|---|---|---|---|
| direct entity | "who is Rook?" | ✅ HIT | ✅ HIT | (unchanged) |
| **vague pronoun** | "who is he?" | ❌ miss | ✅ HIT | "Who is the bandit Rook?" |
| vague noun | "what happened to that merchant?" | ✅ HIT | ✅ HIT | "What happened to Corvin Dale?" |
| cross-ref | "what about the maps he left?" | ✅ HIT | ✅ HIT | "...maps left by Corvin" |
| **pure pronoun** | "is he trustworthy?" | ❌ miss | ✅ HIT | "Is Captain Aldric Voss trustworthy?" |

**Result: naive 3/5, mix 5/5.**

**Honest nuance — where mix actually wins:** mix only beats naive on *contentless-reference*
queries ("who is he?", "is he trustworthy?") that carry **no topical noun** — the raw
embedding of such a message is meaningless and retrieves nothing relevant. When the message
keeps a content word ("merchant", "maps"), naive still hits because that noun embeds near the
right chunks. So the value of mix is concentrated on pure pronoun/anaphora turns — exactly the
cross-session resolution problem, but not a blanket win on every lore turn.

**Implication for the ablation:** measure the *frequency* of contentless-reference turns in
the red-team / persona sets. If they're common, mix's +610 tokens/turn earns its keep; if most
lore questions name their subject, naive-stripped may be the better cost/quality trade. Decide
the default from that frequency, not from the 5/5 headline.

---

### Rejected / superseded
- **`claude -p` Haiku rewrite** ([lore-query-improvement.md](lore-query-improvement.md)
  option 2): violates "no `claude -p` inside `app/`" production rule. Replaced by ADR-0015's
  single Groq extraction call that emits keywords + rewritten query together.
- **Separate semantic+BM25 hybrid:** dense half duplicates naive; BM25's proper-noun value
  already covered by the entity graph; outputs can't RRF-fuse (blob vs ranked list).
- **Multi-query expansion:** mix already multi-angles internally; episodic corpus too small
  to benefit. Revisit episodic-only if ablation shows recall misses.

---

## Related
- `docs/decisions/0015-lightrag-mix-mode-with-pre-extracted-keywords.md`
- `docs/decisions/0010-lightrag-lore-grounding.md`
- `backend/app/memory/vector_store.py` — `retrieve_lore()` (mode + pre-extracted keywords)
- `backend/app/serving/llm.py` — `extract_lore_query()` + `_LORE_EXTRACT_SYSTEM` prompt
- `backend/app/config.py` — `lore_top_k`, `lore_query_mode`, `lore_rewrite_history_window`
