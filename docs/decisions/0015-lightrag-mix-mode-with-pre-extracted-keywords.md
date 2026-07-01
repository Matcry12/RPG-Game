# ADR-0015 — LightRAG mix mode with pre-extracted keywords and rewritten query

**Status:** Accepted — implemented behind `lore_query_mode` flag (default `naive`), 2026-06-30  
**Date:** 2026-06-30  
**Replaces:** `mode="naive"` in `vector_store.retrieve_lore()` when `lore_query_mode="mix"`

---

## Context

The NPC service uses LightRAG for lore retrieval on `full-with-lore` turns. Currently
`mode="naive"` is set, which does pure vector similarity over text chunks with no LLM
call inside LightRAG. This misses entity relationships and community-level knowledge in
the knowledge graph.

Before implementing, all claims below were verified against the **installed source**
(`lightrag-hku==1.5.4`, `.venv/lib/python3.13/site-packages/lightrag/`).

---

## Verified facts from source (with file:line citations)

### 1. Pre-populated keywords bypass the LLM extraction call

`operate.py:4022–4024`:
```python
if query_param.hl_keywords or query_param.ll_keywords:
    return query_param.hl_keywords, query_param.ll_keywords
```
If either list is non-empty, `get_keywords_from_query` returns immediately — the LLM
`extract_keywords_only` call (`operate.py:4027`) is **never reached**.

### 2. `only_need_context=True` skips the synthesis LLM call

`operate.py:3877–3880`:
```python
if query_param.only_need_context and not query_param.only_need_prompt:
    return QueryResult(content=context_result.context, raw_data=context_result.raw_data)
```
Already set in our code (`vector_store.py`). Confirmed: no final synthesis call.

### 3. mix mode runs local + global + naive in one pass, embeddings batched

`operate.py:4344–4396`: all three embedding inputs (`query`, `ll_keywords`,
`hl_keywords`) are batched into **one** embedding API call before any graph lookup.

`operate.py:4360–4361`:
```python
need_ll = mode in ("local", "hybrid", "mix") and bool(ll_keywords)
need_hl = mode in ("global", "hybrid", "mix") and bool(hl_keywords)
```

### 4. naive vector search uses the `query` string directly

`operate.py:4367–4369`:
```python
if query and (kg_chunk_pick_method == "VECTOR" or chunks_vdb):
    texts_to_embed.append(query)
    text_purposes.append("query")
```
→ If we pass a rewritten query to `rag.aquery()`, naive vector search uses the
rewritten string, not the original raw message.

### 5. Keyword extraction prompt size (actual prompt, `prompt.py:484–515`)

The `keywords_extraction` prompt template is ~350 words. With query injected (~20 tokens)
and the example block (~30 tokens):

| Component | Tokens (est.) |
|---|---|
| Prompt template | ~500 |
| Examples block | ~30 |
| Query injection | ~20 |
| **Total input** | **~550** |
| JSON output | ~60 |
| **Total per call** | **~610** |

### 6. LightRAG caches keyword extraction results

`operate.py:4185–4194`: results are cached by `(mode, query_text, language)` hash.
Repeated identical queries pay 0 LLM tokens on cache hit.

---

## Decision

Switch from `mode="naive"` (0 LLM calls, vector only) to **`mode="mix"` with
pre-populated `hl_keywords` + `ll_keywords` + `rewritten_query`**, reducing to
**1 LLM call** that we control rather than the 1 LightRAG would make.

### Flow (verified against source)

```
raw player message
      │
      ▼
[Our combined prompt — 1 LLM call, ~610 tokens]
  Extract in one shot:
  {
    "ll_keywords":     ["Corvin Dale", "East Mill", "Merchant Guild"],
    "hl_keywords":     ["missing merchant", "trade disruption"],
    "rewritten_query": "Corvin Dale disappearance east mill Ashenveil"
  }
      │                              │                         │
      │ rewritten_query              │ ll_keywords             │ hl_keywords
      ▼                              ▼                         ▼
  naive vector              entity graph              community cluster
  search on                 lookup (local)            lookup (global)
  clean query               no LLM                    no LLM
      │                              │                         │
      └──────────────────────────────┴─────────────────────────┘
                                     │
                          (embeddings batched — 1 embed call)
                                     │
                                     ▼
                          raw context string returned
                          (only_need_context=True — no synthesis)
                                     │
                                     ▼
                          grounding gate → inject into system prompt
```

### Token cost per `full-with-lore` turn

| Step | LLM calls | Tokens |
|---|---|---|
| Our combined extraction (1 call) | 1 | ~610 |
| LightRAG keyword extraction | 0 (bypassed via pre-populated fields) | 0 |
| LightRAG synthesis | 0 (`only_need_context=True`) | 0 |
| Graph + vector lookups | 0 (embed only) | 0 |
| NPC reply (agent node) | 1 | ~1000–2000 |
| **Turn total** | **2** | **~1610–2610** |

### vs naive (current)

| Mode | LLM calls/turn | Tokens/turn |
|---|---|---|
| naive (current) | 1 | ~1000–2000 |
| mix + pre-extracted | 2 | ~1610–2610 |

Cost increase: ~610 tokens per `full-with-lore` turn. At Groq 100k TPD free tier this
allows ~38–62 lore turns per day vs ~50–100 on naive — acceptable for dev/demo.

---

## Rewritten query also feeds episodic recall (Option A — reuse-when-free)

The combined extraction call produces a third field, `rewritten_query`, alongside the
keyword lists. This rewritten query has a second consumer: **scored episodic recall**.

Episodic events are stored as canonical summary sentences with proper entity names
(e.g. `"Player asked about the missing merchant Corvin Dale"`). A raw player utterance
(`"what happened to that guy near the mill?"`) embeds poorly against that sentence; the
rewritten query (`"Corvin Dale disappearance east mill"`) embeds much closer, directly
improving the **γ relevance** component of `score_memories()`
(`stream.py:41`, `relevance = 1 - cosine_distance`).

**Decision — Option A (reuse only when free):**

| Route | Extractor runs? | Episodic query (`retrieve_episodic_scored` `query=`) | Lore |
|---|---|---|---|
| `trivial` | no | (no retrieval) | none |
| `full-no-lore` | no | **raw `message`** + recent raw history — no rewrite, no extra call | none |
| `full-with-lore` | yes (for lore) | **`rewritten_query`** — reused, zero extra cost | mix + pre-extracted keywords |

Rationale: the rewrite is paid for on `full-with-lore` turns anyway, so episodic reuses
it for free. `full-no-lore` turns (the common route — `"I want to buy something"`) are
already direct utterances; they keep raw `message` rather than pay ~610 tokens for a
rewrite that would barely change them. This honours the cost-first rule: **no new LLM
call is added to any route.**

Rejected alternatives:
- **Option B** (rewrite on all full turns): ~610 extra tokens on every `full-no-lore`
  turn, roughly doubling their cost — not justified until ablation shows weak no-lore recall.
- **Option C** (augment episodic query with `ll_keywords` on lore turns): kept as the
  upgrade path if Option A's lore-turn recall proves insufficient; no extra call either.

Implementation: in `retrieve_context`, when `route == "full-with-lore"`, pass the
rewritten query to BOTH `retrieve_lore()` and `retrieve_episodic_scored(query=...)`
(currently raw `message` at `nodes.py:367`). On `full-no-lore`, leave `query=message`.

---

## Custom extraction prompt

We override `keywords_extraction` (injected via `PROMPTS` dict at `get_lore_rag` init)
to be RPG-aware and output `rewritten_query` as a third field. We parse all three fields
ourselves; only `ll_keywords` + `hl_keywords` are passed to `QueryParam`. The
`rewritten_query` is passed as the `query` string to `rag.aquery()` where it feeds the
naive vector search (`operate.py:4368`).

---

## What we do NOT do

- **No monkey-patching** of LightRAG internals
- **No synthesis call** — `only_need_context=True` already in place
- **No separate reranker pass** — LightRAG's `enable_rerank=False` to keep cost flat
- **No change to embedding function** — stays on `DefaultEmbeddingFunction` (MiniLM-384)

---

## Consequences

- Quality improvement: entity graph + community context for lore questions
- Cost increase: ~610 tokens per lore turn (acceptable on free tier for demo volume)
- Cache benefit: LightRAG's keyword cache (`operate.py:4185`) means repeated questions
  pay only the naive embed cost on second hit — our lore cache (`nodes.py:_lore_cache`)
  still catches exact message repeats before any LightRAG call
- Fragility: relies on `QueryParam.hl_keywords`/`ll_keywords` bypass staying in place
  (`operate.py:4023`); must re-verify on LightRAG version upgrade

---

## Implementation checklist (done 2026-06-30)

- [x] Add `lore_query_mode: str = "naive"` + `lore_rewrite_history_window: int = 4` to `Settings`
- [x] `extract_lore_query(message, history) -> LoreQuery | None` in `serving/llm.py` (reuses Kira→Groq failover via `.with_structured_output`); returns `(ll_keywords, hl_keywords, rewritten_query)`
- [x] `retrieve_lore()` accepts `mode` + `ll_keywords`/`hl_keywords`, pre-populates `QueryParam` to bypass LightRAG extraction
- [x] Extraction wired in `nodes.retrieve_context` (up front, since episodic also needs the rewrite); reuses `rewritten_query` for `retrieve_episodic_scored` on full-with-lore (Option A); raw `message` on full-no-lore/naive
- [x] **Prompt lives in OUR app code** (`_LORE_EXTRACT_SYSTEM`), not LightRAG's `PROMPTS` — pre-populated keywords make overriding LightRAG's internal `keywords_extraction` unnecessary
- [x] `enable_rerank=False` on `QueryParam` (no reranker configured)
- [x] Lore cache keyed on `(npc_id, hash((lore_query_str, mode)))` — rewritten query already encodes history, so contexts don't collide and naive keeps message-keyed hits
- [x] Prereq bug fixed: `serving/llm.py` imported removed `StartQuest` → `SetQuestState`
- [x] Tests: prereq build, mix wiring (mode+keywords+rewrite reach lore & episodic), naive skips extraction, fallback-to-naive on `None`, cache-key non-collision
- [x] Live proof: 2-turn "who is he?" → rewrite "Who is the bandit Rook?" → mix returns Rook chunk; naive on raw misses
