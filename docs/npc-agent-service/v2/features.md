# NPC Agent Service — Feature Reference

Every feature the service currently runs, how it works, real numbers, and where to find it in the code.

---

## Turn flow (planned — v2 optimized)

```
POST /npc/shopkeeper/talk
  {"player_id": "p1", "message": "..."}

  ┌─ semantic router (FastEmbed, ~2ms, zero LLM) ─────────────────────┐
  │  3-way classification on message embedding:                        │
  │    trivial      → "hi", "ok", "thanks", filler                    │
  │    full-no-lore → NPC interaction, quest, trade, attitude          │
  │    full-with-lore → world questions, lore topics, history queries  │
  └────────────────────────────────────────────────────────────────────┘
       │
       ├─ trivial
       │    short persona (no retrieval) → LLM → reply
       │
       └─ full-no-lore / full-with-lore
            │
            ├─ PARALLEL pre-fetch (fires immediately, before LLM call):
            │    scored episodic (recency+importance+relevance re-rank)
            │    beliefs from Chroma   (returned together as past context)
            │    lore via LightRAG naive  ← only if full-with-lore
            │
            ▼
         build prompt  ──────────────────────────────────────────────────
           [CACHED prefix]  persona (~430 tok) + current beliefs
                            (beliefs cached until reflection fires)
           [DYNAMIC]        episodic events + lore (if any)
                            compressed history summary + last 2 raw turns
                            current message
         ─────────────────────────────────────────────────────────────────
            ▼
         agent (1 LLM call)  ────── streams tokens to client
            │  action tools only (no retrieval tools):
            │    UpdateDisposition / StartQuest / GiveReward  (gate-validated)
            │  loops only if action tool proposed (rare); cap = 3
            ▼
         write_memory
            │  write episodic event, add importance to accumulator
            │  update lore cache (key: query_hash, TTL: session)
            │  if accumulator >= 20 → fire reflection (background):
            │      pull top-10 high-importance events → LLM → write belief
            │      invalidate cached prefix (beliefs changed)
            ▼
         reply streamed to client
```

**Key decisions:**

| Decision | Choice | Why |
|---|---|---|
| Router | FastEmbed 3-way (trivial/no-lore/lore) | Zero LLM call, ~2ms, free local model |
| Retrieval | Speculative pre-fetch, injected as context | Eliminates tool round trip — 1 LLM call instead of 2–3 |
| Tools | Action tools only (no retrieve tools) | Retrieval via pre-fetch is faster and cheaper |
| History | Rolling summary + last 2 raw turns | ~150 tok vs ~1000 tok for raw window of 10 |
| Beliefs | Cached prefix, invalidated on reflection | Stable across most turns → cache hits |
| Prompt order | Stable first (persona+beliefs), dynamic last | Maximises provider KV cache hit rate |
| Episodic | Scored stream only (S6) — raw path removed | Better recall, negligible extra cost (Python re-rank, no LLM) |
| Lore results | Cached per `(npc_id, query_hash)`, session TTL | Lore is static — same question costs zero on repeat |
| `MEMORY_STREAM` flag | Removed — always on | One less code path to maintain |

## Turn flow (current — v1)
      ▼
  write_memory               persists episodic event, updates importance sum
```

**Files:** `backend/app/graph/nodes.py`, `backend/app/graph/build.py`  
**Entry point:** `backend/app/api/talk.py` → `POST /npc/{npc_id}/talk`

---

## 1. NPC Persona

**What it does:** Gives the NPC a fixed identity — voice, quirks, knowledge, and goals. Injected as a `SystemMessage` at the top of every prompt.

**How it works:**
- Persona loaded from `backend/data/personas/{npc_id}.md` at each turn
- `_persona_system()` in `nodes.py:151` builds the system message, injecting current disposition score, any lore context, episodic memories, and beliefs into the prompt

**Example — Mira's persona file** (`data/personas/shopkeeper.md`):
```
You are Mira Thistlewick, a weathered but sharp-eyed curio merchant who runs
"The Amber Shelf" in the market district of Ashenveil. In your mid-fifties,
with ink-stained fingers, a dozen mismatched rings...
Quirks: calls customers "traveller" until she trusts them. Has a raven named Ledger.
```

**Real numbers:** No tunable knobs — persona is pure text. Persona file is ~400 tokens.

**Files:** `backend/data/personas/shopkeeper.md`, `nodes.py:151`

---

## 2. Conversation History (LangGraph checkpoint)

**What it does:** Mira remembers the last N turns of the conversation, even across server restarts.

**How it works:**
- LangGraph `AsyncSqliteSaver` writes a checkpoint after every turn to `data/checkpoints.db`
- `thread_id = f"{npc_id}:{player_id}"` — one thread per NPC/player pair
- `retrieve_context` loads the last `history_window` messages from the checkpoint and injects them into the prompt as raw `HumanMessage`/`AIMessage` pairs

**Example:**
```
Turn 1 — player: "I'm looking for a map of the eastern road"
Turn 3 — player: "How much for that map you mentioned?"
           Mira: "The one from our earlier conversation? Still 40 gold."
```

**Real numbers:**
- `history_window = 10` messages injected per turn (`config.py:33`)
- Stored in `data/checkpoints.db` (gitignored)

**Files:** `backend/app/graph/build.py`, `nodes.py:190`, `config.py:33`

---

## 3. Tool Calling — Propose / Dispose Loop

**What it does:** Mira can take real actions (change disposition, start quests, give rewards). The LLM *proposes* — deterministic code *validates* against SQLite before executing.

**The 3 tools:**

| Tool | When the LLM calls it | Gate checks |
|---|---|---|
| `UpdateDisposition` | player is rude / kind / helpful | always accepted; delta clamped to [−10, +10] |
| `StartQuest` | player asks to take a quest | quest must exist and be in `not_started` state |
| `GiveReward` | player claims a completed quest reward | quest must be `complete` AND reward not yet claimed |

**How it works:**
1. Agent LLM proposes a tool call (e.g. `UpdateDisposition(delta=-8)`)
2. `tools` node passes it to `gates.validate()` — a pure function, no LLM involved
3. Gate writes to SQLite (`disposition`, `quest_state`, `rewards_claimed` tables)
4. Result returns as a `ToolMessage` — agent re-reasons and writes in-character reply
5. If gate rejects: agent explains the rejection in Mira's voice ("I'm afraid that quest isn't available")

**Example — hostile player:**
```
player: "Your wares are garbage, you old crow."
→ agent proposes: UpdateDisposition(delta=-5)
→ gate: clamps to -5 (already in bounds), writes -5 to SQLite
→ new disposition score: 45 → 40
→ Mira: "A sharp tongue, traveller. Mind it doesn't cut you before you leave."
```

**Example — reward gate rejection:**
```
player: "Give me my reward for the rat-cellar quest."
→ agent proposes: GiveReward(quest_id="rat-cellar", item_id="silver-pick")
→ gate: quest state = "not_started" (player never took it)
→ gate rejects: "quest not complete (state='not_started')"
→ Mira: "I've no record of a rat-cellar commission in my ledgers."
```

**Real numbers:**
- Delta clamped to [−10, +10] (`gates.py:44`)
- `agent_max_turns = 3` — max tool loops per turn before forced reply (`config.py:32`)
- Disposition stored as integer 0–100 in `players` table

**Files:** `backend/app/tools/schemas.py`, `backend/app/tools/gates.py`, `nodes.py:398`

---

## 4. Episodic Memory

**What it does:** Mira remembers specific things that happened — player actions, tool executions, notable exchanges — and recalls the most relevant ones each turn.

**How it works:**
- After every turn, `write_memory` writes an event to ChromaDB (`episodic` collection): text summary, importance score, timestamp, player_id
- Next turn, `retrieve_context` queries ChromaDB with the current player message as the query, returns top-k most relevant past events
- Events injected into Mira's prompt as "What you remember about this player"

**Example:**
```
Turn 2 — player completes a quest → event written:
  text: "Player p1 completed quest 'lost-locket' and received reward 'silver-key'"
  importance: 7, timestamp: "2026-06-28T10:15:00Z"

Turn 8 — player: "Do you remember the locket I found?"
→ episodic recall surfaces the Turn 2 event
→ Mira: "Ah yes — the Vellara locket. You earned that key fair and square."
```

**Importance scores (heuristic, `nodes.py:88`):**
- Plain conversation turn: `0`
- `StartQuest` accepted: `5`
- `GiveReward` accepted: `7`
- `UpdateDisposition`: `min(10, abs(delta))` — a −9 insult scores 9

**Real numbers:**
- `episodic_recall_k = 3` — top-3 events returned per turn
- `episodic_candidate_factor = 4` — fetches 12 candidates from Chroma, re-ranks to top 3
- Collection: `episodic` in `data/chroma/`
- Flag: `EPISODIC_MEMORY=true` (default on)

**Files:** `backend/app/memory/vector_store.py`, `nodes.py:468`, `config.py:36-38`

---

## 5. Lore Grounding Gate (S5)

**What it does:** Mira answers in-world lore questions accurately without hallucinating. For out-of-scope questions (topics not in the lorebook), she declines gracefully rather than inventing facts.

**How it works:**
- Lorebook: 15 hand-authored entries across 5 categories (general, market, city_history, factions, locations) in `shared/lore/lorebook.json`
- At startup, lorebook is seeded into a LightRAG knowledge graph per NPC (`data/lightrag/shopkeeper/`)
- Each turn, `retrieve_lore()` runs a vector-only search (`mode="naive"`, no LLM calls) over the graph, returns top matching chunks
- If lore context ≥ 100 chars: injected into prompt as grounded facts (`grounded=True`)
- If lore context < 100 chars: prompt tells Mira she has no grounded knowledge on this topic — she should decline, not invent

**Example — in-scope:**
```
player: "What's happening with amber prices lately?"
→ lore retrieval finds: "Amber prices rose 30% after the eastern mine collapse (lore entry: market-prices)"
→ Mira: "The eastern mine collapse drove prices up sharply — I've had to adjust my margins accordingly."
```

**Example — out-of-scope:**
```
player: "Who rules the Northern Isles?"
→ lore retrieval: 0 relevant chunks found
→ grounded=False → prompt: "you have no grounded knowledge on this topic"
→ Mira: "My knowledge runs deep in Ashenveil, traveller, not across the northern seas."
  ✓ No invented rulers
```

**Real numbers:**
- 15 lore entries, 5 categories
- `lore_top_k = 10` chunks returned by LightRAG (`config.py:41`)
- `lore_context_min_chars = 100` to count as grounded (`config.py:27`)
- `lore_history_window = 6` prior messages passed to LightRAG for context
- LightRAG graph: 65 entity nodes, 100 relationship edges, 15 text chunks
- Flag: `GROUNDING_GATE=true` (default on)

**Files:** `backend/app/memory/vector_store.py:380-425`, `backend/app/api/world.py`, `shared/lore/lorebook.json`, `config.py:27`

---

## 6. Memory Stream Scoring (S6)

**What it does:** Instead of returning the 3 most *similar* episodic memories, return the 3 most *useful* ones — balancing how recent, important, and relevant each event is.

**How it works (Park et al. 2023 formula):**
```
score = α · recency + β · importance + γ · relevance

recency    = exp(-0.1 · hours_since_event)   → halves every ~7 hours
importance = stored_score / 10               → 0.0–1.0
relevance  = 1 - cosine_distance             → 0.0–1.0 (from Chroma)
```

Chroma fetches `k × 4 = 12` candidates by embedding similarity, then `score_memories()` re-ranks them, and the top 3 are injected into the prompt.

**Example — why this matters:**
```
Without stream scoring: top-3 by embedding similarity
  → returns 3 "hello" greetings because they're textually close to current message

With stream scoring (alpha=0.35, beta=0.35, gamma=0.30):
  → a high-importance event from 2 hours ago (reward granted, score=7) 
     scores: 0.35·0.87 + 0.35·0.70 + 0.30·0.40 = 0.67
  → a low-importance greeting from 30 min ago (score=0)
     scores: 0.35·0.95 + 0.35·0.00 + 0.30·0.60 = 0.51
  → reward event wins even though it's less textually similar
```

**Real numbers:**
- `stream_alpha = 0.35` (recency weight)
- `stream_beta = 0.35` (importance weight)  
- `stream_gamma = 0.30` (relevance weight)
- `stream_decay = 0.1` per hour → half-life ≈ 7 hours
- Flag: `MEMORY_STREAM=false` (default off, enable for better recall)

**Files:** `backend/app/memory/stream.py`, `vector_store.py:retrieve_episodic_scored()`, `config.py:43-47`

---

## 7. Reflection Pass (S7)

**What it does:** After enough significant events accumulate, Mira runs a background reflection — synthesising recent memories into durable beliefs about the player. These beliefs persist across sessions and colour future responses.

**How it works:**
1. Every turn, the importance score of the event is added to a running `importance_sum` in SQLite
2. When `importance_sum ≥ 20`, a reflection fires:
   - Fetches the last 10 episodic events with `importance ≥ 5`
   - Calls the LLM with a reflection prompt: "What does Mira now believe about this player?"
   - LLM returns 2–3 belief sentences
   - Beliefs written to ChromaDB `beliefs` collection, `importance_sum` reset to 0
3. Next turn, `retrieve_context` pulls the most relevant belief and injects it into the prompt

**Example:**
```
Events accumulate (importance_sum: 0 → 5 → 12 → 19 → 26 ≥ 20 → reflection fires):
  - "Player insulted Mira (−5 disposition)" importance=5
  - "Player insulted Mira again (−7)" importance=7  
  - "Player started quest after all" importance=5
  - "Player returned quest reward" importance=7
  → total: 24, fires reflection

Reflection LLM output:
  "This player is volatile but ultimately honourable — rude at first,
   yet followed through on their commitment. Watch them, but don't close the door."

Next turn injection:
  [Mira's beliefs] This player is volatile but ultimately honourable...
  → Mira's tone shifts: guarded but not hostile
```

**Real numbers:**
- `reflection_threshold = 20` importance points before reflection fires (`config.py:51`)
- `reflection_min_importance = 5` — only events ≥ 5 go into the reflection prompt
- `reflection_event_limit = 10` max events passed to the reflection LLM
- Importance scores: plain turn=0, StartQuest=5, GiveReward=7, UpdateDisposition=min(10, |delta|)
- Flag: `REFLECTION=false` (default off)

**Files:** `nodes.py:103` (`_run_reflection`), `nodes.py:88` (`_tool_importance`), `vector_store.py:retrieve_for_reflection()`, `config.py:49-55`

---

## 8. LLM Providers — Kira + Groq Failover

**What it does:** Two free-tier LLMs in automatic failover. If the primary hits a rate limit or errors, the fallback takes over transparently — no client sees the switch.

**How it works:**
- LangChain `.with_fallbacks([groq])` wraps the Kira LLM
- Any exception (429 rate limit, 5xx, timeout) triggers the fallback
- `LLM_PRIMARY` env var controls which is primary

**Provider limits (free tier):**
| Provider | Model | Limit |
|---|---|---|
| Kira AI | kira-mini-1.0 | 4 RPM |
| Groq | llama-3.3-70b-versatile | 100k tokens/day |

**Example:**
```
Turn 15 — Kira returns 429 "4 requests/minute exceeded"
→ LangChain automatically retries with Groq
→ client sees no error, Mira replies normally
→ server log: WARNING Groq error in agent — forcing a tool-free reply
```

**Config:**
```env
LLM_PRIMARY=kira    # default — Kira first, Groq fallback
LLM_PRIMARY=groq    # Groq first, Kira fallback
```

**Files:** `backend/app/serving/llm.py`, `backend/app/config.py:11-21`, `backend/.env.example`

---

## 9. Ablation Eval Harness (S8)

**What it does:** Quantifies how much each feature layer adds. Runs 18 labelled cases × 5 configs through the live server, scores with an LLM judge (Haiku), prints a comparison table.

**5 configs (cumulative layers):**
```
baseline    GROUNDING_GATE=false  EPISODIC_MEMORY=false  MEMORY_STREAM=false  REFLECTION=false
+gate       GROUNDING_GATE=true   ...
+episodic   + EPISODIC_MEMORY=true
+stream     + MEMORY_STREAM=true
+reflection + REFLECTION=true
```

**18 test cases:**
- P-1…P-5: persona consistency
- L-1…L-6: lore accuracy (3 in-scope, 3 out-of-scope hallucination traps)
- T-1…T-5: tool accuracy (UpdateDisposition, GiveReward gate, StartQuest, reward rejection)
- M-1: episodic recall (player mentioned something last turn, does Mira remember?)
- M-2: reflection under hostility (3 hostile setup turns, then check Mira's attitude)

**Run 2 results (2026-06-28):**
```
Config           Persona  Lore acc  Tool acc    Memory   Overall
baseline           3.0/3     1.2/3     2.0/3     1.0/3     2.0/3
+gate              2.6/3     2.5/3     1.2/3     2.0/3     2.4/3
+episodic          2.6/3     2.5/3     2.0/3     2.0/3     2.5/3
+stream            2.0/3     2.5/3     1.5/3     2.0/3     2.3/3
+reflection        3.0/3     2.4/3     1.6/3     3.0/3     2.5/3
```

**Commands:**
```bash
cd backend
uv run python eval/run_ablation.py --smoke 2      # pre-flight: 10 calls
uv run python eval/run_ablation.py                # full run: 90 calls (~30 min)
uv run python eval/run_ablation.py --score-only   # re-score cached replies, no new NPC calls
uv run python eval/run_ablation.py --replace      # wipe cache, start fresh
```

**Files:** `backend/eval/run_ablation.py`, `backend/eval/dataset/ablation_cases.json`, `backend/eval/judge.py` (Groq), `backend/eval/judge_local.py` (Haiku, gitignored), `backend/eval/ablation_results.json` (gitignored)

---

## Feature flags summary

All flags live in `backend/app/config.py` and can be set in `.env`:

| Flag | Default | Effect |
|---|---|---|
| `GROUNDING_GATE` | `true` | Lore retrieval + hallucination guard |
| `EPISODIC_MEMORY` | `true` | Write + recall episodic events |
| `MEMORY_STREAM` | `false` | Re-rank episodic recall by recency/importance/relevance |
| `REFLECTION` | `false` | Background belief synthesis after threshold importance |
| `TOOLS_ENABLED` | `true` | Bind tools to agent LLM |
| `LLM_PRIMARY` | `kira` | `kira` or `groq` — which provider goes first |

---

## Known issues (from S8 eval)

1. **T-3/T-4 tool accuracy** — `StartQuest` and `GiveReward` always rejected in ablation. Root cause: seed DB copy for player `p1` doesn't have the prerequisite quest rows. Fix: update seed script.
2. **Persona dilution in +stream** — lore + memory context bloat flattens Mira's voice (2.0/3 vs 3.0/3). Fix: anchor persona at top of prompt, add reminder at bottom.
3. **`'...'` on rate limit** — when both Kira and Groq hit limits simultaneously, reply is empty. Fix: Ollama Gemma local fallback (S10).
