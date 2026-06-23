# NPC Agent Service — Implementation Plan (Vertical Slices)

> Buildable companion to `plan.md`. Each slice is a thin, end-to-end increment: it cuts through every layer it needs and leaves the system **running and demoable**. Build in order; do not start a slice until the previous one's **Done =** check passes. Anything unspecified here we **fix while building** (see §Open items).
>
> **Brain:** Groq free tier (`llama-3.3-70b-versatile`) primary → Ollama Gemma 3n (`gemma3n:e2b`) local failover, both via LangChain (`.with_fallbacks()` + `.bind_tools()`). The gate never trusts either.

---

## Prerequisites (one-time, before S0)

- **Python 3.11+**, `uv` or `venv`.
- **Deps:** `fastapi uvicorn[standard] langgraph langchain-core langchain-groq langchain-ollama langgraph-checkpoint-sqlite chromadb pydantic pydantic-settings pytest httpx`.
- **Groq key:** create at console.groq.com → `export GROQ_API_KEY=...` (read via `pydantic-settings`, never hardcode).
- **Ollama (for the S10 failover, install now so it's ready):** `ollama pull gemma3n:e2b` (try `gemma3n:e4b` too; keep whichever fits 6GB).
- **Repo skeleton:** create the `backend/` tree from `plan.md` §9. Every file lands in its category folder (CLAUDE.md file-placement rule).

**Verify prereqs:** `python -c "from langchain_groq import ChatGroq; print('ok')"` and `ollama list | grep gemma3n`.

---

## Slice tickets

Each ticket: **Goal · Layers cut · Files · Key decisions · Done = (acceptance) · Verify**.

### S0 — NPC echoes a persona reply over `/talk`
- **Goal:** prove the spine of the request path with a real LLM.
- **Layers:** FastAPI route → `ChatGroq` → streamed reply.
- **Files:** `backend/app/main.py`, `backend/app/api/talk.py`, `backend/app/serving/llm.py`, `backend/app/config.py`, `backend/data/personas/shopkeeper.md`.
- **Key decisions:** `llm.py` exposes `get_llm()` returning `ChatGroq(model=settings.groq_model, temperature=0.7)`. `/npc/{id}/talk` streams via FastAPI `StreamingResponse` over `llm.astream(messages)`. Persona loaded from a markdown file as the system prompt. No memory, no tools yet.
- **Done =** `curl -N` the endpoint with a message → in-character tokens stream back from Groq.
- **Verify:** `curl -N -X POST localhost:8000/npc/shopkeeper/talk -d '{"player_id":"p1","message":"hello","location":"shop"}'`

### S1 — One gated tool changes real state: `UpdateDisposition` ⭐ spine
- **Goal:** the propose/dispose loop, end-to-end, against ground truth.
- **Layers:** `/talk` → propose tool → **gate** → SQLite write → `/state` read.
- **Files:** `backend/app/memory/sqlite_store.py`, `backend/app/tools/schemas.py`, `backend/app/tools/gates.py`, `backend/app/api/state.py`, update `backend/app/serving/llm.py` (`bind_tools`), update `talk.py`.
- **Key decisions:**
  - SQLite tables: `players`, `npcs`, `disposition(npc_id, player_id, score, updated_at)`. Single demo player seeded.
  - `schemas.py`: `class UpdateDisposition(BaseModel): delta: int`.
  - `gates.py`: `validate_update_disposition()` **clamps `delta` to [-10,10]** (never trust the model), persists, returns an accept/reject result. Gate is a pure function over (proposed_call, db) → `GateResult`.
  - `llm.bind_tools([UpdateDisposition])`; parse `response.tool_calls[0]` → Pydantic.
  - `/state` returns `{disposition}`.
- **Done =** an insult drops disposition in SQLite; an absurd delta (e.g. -999) is clamped to -10; `/state` reflects the new score.
- **Verify:** `pytest backend/tests/test_gate_disposition.py` (clamp + persist) **and** a manual talk→state round-trip.

### S2 — A rejection becomes in-character dialogue: `GiveReward` + `StartQuest`
- **Goal:** widen the tool set and add the **rejection feedback loop** — without duplicating the gate.
- **Layers:** same spine; +quest/inventory tables; reject-reason → regenerate.
- **Files:** extend `backend/app/tools/schemas.py`, `backend/app/tools/gates.py`, `backend/app/memory/sqlite_store.py`; update `talk.py` graph step.
- **Key decisions:**
  - Tables: `quests(id, player_id, state)`, `inventory(player_id, item_id, qty)`, `rewards_claimed(player_id, quest_id)`.
  - **Generalize the gate** from S1 into one dispatch (`validate(call, db) -> GateResult`) with per-tool validators — no copy-paste (CLAUDE.md reuse rule).
  - `GiveReward(quest_id, item_id, reason)`: require quest `complete` AND not in `rewards_claimed`; else `GateResult.reject("quest not complete")`.
  - On reject: feed the reason back to the LLM as a tool/assistant message → it regenerates an in-character refusal. On accept: mutate SQLite, insert `rewards_claimed`.
- **Done =** asking for the reward early → gate rejects → NPC refuses in character, SQLite unchanged; completing the quest → reward granted exactly once (second attempt rejected by `rewards_claimed`).
- **Verify:** `pytest backend/tests/test_gate_reward.py` (idempotency + precondition) + manual.
- **Known limitation (resolved in S4):** the turn is linear — one tool call per turn (extras dropped), and a rejection only colors the prose; the NPC can't re-decide and try a different action. The agentic tool-loop in S4 lifts this ([ADR-0005](../../decisions/0005-agentic-tool-loop-at-s4.md)).

### S3 — The NPC remembers what just happened: episodic write + recall
- **Status: ✅ done (2026-06-23).** Linear flow preserved. Chroma `episodic` collection; retrieve-before-generate + write-after-stream wired into `talk.py`; 39 tests pass. Importance is a **provisional heuristic** and recall is **plain similarity** (importance-weighted ranking deferred to S6) — see [ADR-0006](../../decisions/0006-episodic-memory-write-and-recall-policy.md).
- **Goal:** first memory layer — events persist and re-enter context.
- **Layers:** write_memory → Chroma `episodic` → retrieve_context.
- **Files:** `backend/app/memory/vector_store.py`, new graph nodes `write_memory` / `retrieve_context`, wire into `talk.py`.
- **Key decisions:**
  - Chroma `episodic` collection; doc = `{text, npc_id, player_id, timestamp, importance}`. Every accepted tool call + salient turn written. `importance` = a quick LLM 1–10 rating (cheap call) or heuristic for now (fix-while-build).
  - `retrieve_context`: semantic top-k over `episodic` filtered by `(npc_id, player_id)`, prepended to the prompt. Plain similarity for now (scoring comes in S6).
- **Done =** do something memorable, then **next turn** the NPC references it unprompted ("last time you helped with the bandits").
- **Verify:** two-turn manual script; assert the recalled text appears in the turn-2 prompt (log it).

### S4 — Memory survives a restart: LangGraph checkpointer ⭐
- **Goal:** durable agent state — the resume headline.
- **Layers:** wrap the turn flow as a LangGraph `StateGraph` + SQLite checkpointer.
- **Files:** `backend/app/graph/build.py` (assemble nodes), `backend/app/graph/state.py` (typed graph state), update `talk.py` to invoke the compiled graph with `thread_id=(npc_id, player_id)`.
- **Key decisions:**
  - Reuse S1–S3 functions as graph **nodes** (`retrieve_context → plan → propose_tools → gate → generate → write_memory`). No logic rewrite — just composition.
  - `SqliteSaver` checkpointer; `thread_id` keyed per `(npc_id, player_id)`.
  - **Replace the linear two-call flow with an agentic tool-loop** ([ADR-0005](../../decisions/0005-agentic-tool-loop-at-s4.md)): an `agent` node cycles with a **gate-backed tools node** (`gates.validate` on every call) until no tool calls remain, then renders the reply — enabling multi-tool turns + react-to-rejection. Must preserve the `tool_use_failed` mitigation (tool-decision turns stay prose-free). Loop *shape* is an S4 deep-dive (see ADR-0005 open items).
- **Done =** kill the server mid-conversation, restart, reconnect as the same player → NPC continues with full conversational context (checkpoint restored).
- **Verify:** start convo → `kill` uvicorn → restart → next message shows continuity; checkpoint row exists in the saver DB.

### S5 — Grounded in lore, refuses to invent: Chroma lore + grounding gate
- **Goal:** lore RAG + hallucination control.
- **Layers:** lorebook pipeline → Chroma `lore` → retrieve_context → grounding_gate on the answer.
- **Files:** `backend/data/gen_lorebook.py`, `backend/data/lorebook.json`, `backend/app/api/world.py` (`POST /world/seed`), extend `backend/app/memory/vector_store.py` + the gate.
- **Key decisions:**
  - `gen_lorebook.py`: LLM generates structured JSON lore, hand-curated, committed. `/world/seed` embeds it once into Chroma `lore`.
  - `retrieve_context` also pulls top-k lore. **Grounding gate** (flag `GROUNDING_GATE`): if the drafted answer makes a world claim with no supporting lore hit above a threshold, block/soften it.
- **Done =** in-lore question → correct grounded answer; out-of-lore question → NPC declines instead of inventing.
- **Verify:** 2 eval cases (1 grounded, 1 trap); assert no fabricated fact on the trap.

### S6 — Salient old memories beat recent trivia: the memory stream ⭐ centerpiece
- **Goal:** Generative-Agents scored retrieval.
- **Layers:** deepen S3 recall into `α·recency + β·importance + γ·relevance`.
- **Files:** `backend/app/memory/stream.py`, swap retrieval in `retrieve_context` (flag `MEMORY_STREAM`).
- **Key decisions:** `recency` = exp decay on `last_access`; `importance` = stored 1–10; `relevance` = cosine to query embedding. Normalize each to [0,1], weighted sum, top-k. Update `last_access` on retrieval.
- **Done =** a high-importance event from many turns ago outranks recent small-talk when relevant — show the scored list (text + 3 sub-scores).
- **Verify:** unit test on the scorer with crafted memories; assert ordering.

### S7 — The NPC forms a belief about you: reflection ⭐ money-shot
- **Goal:** importance-accumulation reflection → beliefs.
- **Layers:** accumulator → reflection pass → Chroma `beliefs` → back into retrieval.
- **Files:** extend `backend/app/memory/stream.py` (reflection), `beliefs` collection in `backend/app/memory/vector_store.py`, accumulator in graph state, flag `REFLECTION`.
- **Key decisions:** maintain running Σimportance since last reflection; when ≥ `REFLECTION_THRESHOLD`, pull recent salient memories → LLM derives 1–3 higher-level beliefs ("the player is untrustworthy") → store in `beliefs` (high importance) → reset accumulator. Beliefs join the memory-stream retrieval pool.
- **Done =** across two sessions of betrayals, the NPC independently concludes distrust and acts on it (colder tone, refuses favors).
- **Verify:** scripted multi-turn betrayal sequence; assert a `beliefs` doc is created and influences the next reply.

### S8 — Prove every pillar earns its place: ablation harness ⭐ highest signal
- **Goal:** the portfolio centerpiece table.
- **Layers:** feature flags → calibrated LLM-as-judge → table.
- **Files:** `backend/eval/dataset/`, `backend/eval/judge.py`, `backend/eval/run_ablation.py`.
- **Key decisions:** ~20 hand-labeled calibration cases for the judge. `run_ablation.py` runs the dataset under each flag combo (baseline → +gate → +episodic → +stream → +reflection), scores persona / grounding / tool-accuracy / ungrounded-actions, emits a markdown table.
- **Done =** one command produces the §7.1 ablation table.
- **Verify:** `python backend/eval/run_ablation.py` → table written to `README`/artifacts; judge agreement with labels reported.

### S9 — Safety is structural: red-team suite
- **Goal:** the gate-hold-rate security story.
- **Layers:** attack dataset → full system → metrics.
- **Files:** `backend/eval/dataset/redteam/`, `backend/eval/run_redteam.py`.
- **Key decisions:** attacks = instruction-override, system-prompt-exfil, fake-authorization, lore prompt-injection. Metrics: **persona-hold %** and the headline **gate-hold %** (LLM was manipulated into *proposing* a forbidden tool call, but the gate rejected it). Hard assert: ungrounded actions executed = **0**.
- **Done =** report shows jailbreaks that reach a forbidden `GiveReward` proposal, all blocked by the gate.
- **Verify:** `python backend/eval/run_redteam.py` → persona-hold % + gate-hold % + 0 executed violations.

### S10 — Resilient $0 serving: local Gemma failover + benchmark
- **Goal:** automatic multi-provider failover + honest comparison. *(Pull forward if Groq daily limits block dev.)*
- **Layers:** add `ChatOllama` fallback to the existing LLM object; per-turn brain tag.
- **Files:** extend `backend/app/serving/llm.py`, `backend/app/serving/tool_parse.py`, add a `brain` field to logs/`/state` debug.
- **Key decisions:** `get_llm()` returns `primary.with_fallbacks([ollama_gemma]).bind_tools(TOOLS)`. Local tool-calls via Ollama `format=json`/JSON-schema → same Pydantic parse in `tool_parse.py`. Log `brain=groq|local`.
- **Done =** force a Groq `429` (unset key / exhaust quota) mid-conversation → turn served by local Gemma, no client-visible break; eval harness runs on both → TTFT/tok-s + quality comparison row + measured failover rate.
- **Verify:** integration test that monkeypatches Groq to raise → asserts local path answers and `brain==local`.

### S11 — Stretch (each its own thin slice)
Foil NPC (generalization) · WebSocket `/talk` · two-tier routing (`llama-3.1-8b-instant` for one-liners, 70B for quests) · Postgres checkpointer. Pick by value; each ships with its own Done-check.

---

## Dependency order

```
S0 → S1 → S2 → S3 → S4 → S5 → S6 → S7 → S8 → S9   (S10 anytime after S1; S11 optional)
                                    ▲
        portfolio checkpoints: S1, S4, S7, S8
```
S6 requires S3 (episodic store). S7 requires S6 (scored stream). S8/S9 require the flags wired in S1/S5/S6/S7. S10 only needs S1's tool binding.

## Testing convention
`backend/tests/` mirrors `backend/app/`. Gate logic gets **unit tests first** (pure functions, no LLM) — the gate is the safety boundary, so it's the most-tested code. LLM-touching paths get thin integration tests with 1–2 cases; the real LLM evaluation lives in `backend/eval/` (S8/S9).

## Open items — fix while building
- **Exact importance scoring** (LLM-rated vs heuristic) — start heuristic in S3, upgrade in S6 if the judge shows it matters.
- **Groq model pin** — `llama-3.3-70b-versatile` assumed; confirm it's still the best free tool-caller at build time, else swap (the LangChain seam makes this one line).
- **Local Gemma tool reliability** — if `gemma3n:e2b` structured-output is too flaky for tool calls, fall back to `e4b`, or use Ollama JSON-schema mode / a stricter retry. Decide with a quick S10 spike.
- **Memory-stream weights** (α/β/γ) and `REFLECTION_THRESHOLD` — tune empirically in S6/S7.
- **Grounding-gate threshold** — calibrate against the S5 trap cases.
- **Free-tier budgeting** — if dev burns the 1000 req/day, pull S10 forward and/or cache aggressively.
