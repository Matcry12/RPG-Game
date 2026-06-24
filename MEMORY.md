# Project Memory — NPC Agent Service

Durable memory that survives across sessions. Newest entries on top. Keep entries short; link out for detail.

> **Where things go:**
> - **Mistakes / errors / things to avoid → this file** (section below). *Every* error or wrong turn gets recorded here so we never repeat it.
> - **Decisions → `docs/decisions/` (ADRs)**, one file per decision. This file only tracks resolved §9 questions + project state.
> - Research findings → wiki (`docs/wiki/` + `wiki_ingest`). Ephemeral scratch → `.omc/notepad.md`.

---

## Mistakes & Lessons (never repeat)

**Rule:** the moment something goes wrong — a bug we caused, a wrong assumption, a failed command, a
dead-end approach, a misread of the spec — record it here *before moving on*. Each entry is a tripwire
for the future.

_Format each as:_
> ### YYYY-MM-DD — <what went wrong, one line>
> **What happened:** … **Root cause:** … **Fix / what to do instead:** … **Watch for:** <how to catch it early next time>

### 2026-06-22 — Groq `tool_use_failed` 400 when the tool-proposal prompt also asks for roleplay prose
**What happened:** First live `/talk` round-trip crashed with `groq.BadRequestError: tool_use_failed` (HTTP 400). Llama-3.3-70b on Groq emitted in-character prose AND a malformed inline tool call (`<function=UpdateDisposition({"delta": -2})</function>`) in a single message; Groq's server-side parser rejected the whole generation. The error is raised by `tool_llm.ainvoke()` (the API call itself), so the existing try/except around *argument parsing* never saw it → uncaught 500.
**Root cause:** (1) The propose call reused the full chatty NPC persona as its system prompt at temperature 0.7, so the model tried to roleplay and emit a tool call at once → malformed dual output. (2) The robustness guard sat at the wrong layer (arg parse, not the `ainvoke` API boundary).
**Fix / what to do instead:** Separate the two LLM jobs. The propose/tool-decision call uses a **terse tool-routing system prompt** ("decide the tool; write NO dialogue") at **temperature 0** for well-formed tool calls; the in-character prose stays in the separate generate call (temp 0.7, no tools). Wrap the propose `ainvoke` in `try/except groq.BadRequestError` — a failed proposal is best-effort and must never break the turn (degrade to "no tool", still reply).
**Watch for:** Any Groq/Llama tool-calling path where one call must both emit prose and call a tool. Catch provider errors at the **API-call boundary**, not only at parse time. This will matter again for `GiveReward`/`StartQuest` (S2) and the local Gemma path (S10).

**Addendum 2026-06-24 (controlled experiment — TRUE root cause found; supersedes the original "roleplay prose" framing):** Reproduced `tool_use_failed` on current `llama-3.3-70b-versatile` and isolated the real cause with ~80 live calls.
- **REAL ROOT CAUSE = argument *type* mismatch, not prose-mixing.** Llama-3.3-70b on Groq frequently serializes a numeric tool arg as a **string** (`delta: "-8"` instead of `-8`). Groq validates tool-arg **types server-side**; an `int`-only schema then 400s with `tool_use_failed` (`failed_generation: <function=update_disposition>{"delta": "-8"}</function>`, message: "expected integer, but got string"). Measured rate: **11/20 (55%)** with a unified persona+tool agent. This is the actual 2026-06-22 crash, finally reproduced.
- **THE FIX → declare numeric tool params as `int | str` and coerce in a `field_validator` (→ `anyOf[integer,string]` in the JSON schema Groq sees).** Re-ran 20×: **0/20 failures** (model sent the string every time; all coerced to int). Applied to `UpdateDisposition.delta` in `schemas.py` (ADR-0008). Apply the same to ANY future numeric tool param (and the local Gemma path, S10).
- **Correction to my earlier same-session note (now deleted):** I wrongly claimed "the 400 no longer reproduces / Groq softened the parser." That was a testing error — I had induced the tag *inside prose* (which Groq returns as harmless `content`, no 400), not a *real structured call with a bad-typed arg* (which 400s). The 400 is alive and frequent on type mismatch.
- **Secondary, still-true findings:** (a) a prompt that demands prose **and** a tool in the *same message* makes the model drop the tool (prose-only) ~32/35 — so keep tool-decision turns sequential/prose-free; (b) temperature is near-irrelevant to tool reliability once turns are separated. The S1/S4 prose-free `agent` node + `try/except BadRequestError` remain correct, but the **schema-coercion fix is the primary cure** for `tool_use_failed`; the try/except is now just defense-in-depth.

### 2026-06-21 — Project memory file is `MEMORY.md` (uppercase), and a bulk sed broke link targets
**What happened:** Wrote the memory file as `memory.md`; it persisted on disk as `MEMORY.md`. References and the SessionStart hook pointed at `memory.md`, which doesn't resolve on case-sensitive Linux. Fixing it with `sed 's/memory.md/MEMORY.md/g'` also rewrote the `layered-memory.md` wiki link to a dead `layered-MEMORY.md`.
**Root cause:** (1) The memory file is canonically `MEMORY.md` here; (2) the sed pattern was too broad — it matched `memory.md` inside the unrelated filename `layered-memory.md`.
**Fix / what to do instead:** The project memory file is **`MEMORY.md`** — always reference it uppercase. When doing a global replace on a substring that also appears inside other filenames, anchor the pattern (e.g. `\bmemory\.md\b` or include the leading `/`/space) and grep-verify link targets exist afterward.
**Watch for:** Markdown links to `*-memory.md` wiki pages (lowercase) vs the `MEMORY.md` index — they are different files; don't let a replace conflate them.

---

## Decisions

Decisions live in **`docs/decisions/`** (one ADR per decision). Do not log decision rationale here —
write an ADR. This section is intentionally just a pointer.

---

## Resolved open questions

Resolved in `docs/npc-agent-service/v2/plan.md` §10 (were §9 in v1).

| # | Question | Status | Answer |
|---|----------|--------|--------|
| 1 | GPU or CPU-only target? | **resolved** | GTX 1660 SUPER 6GB. Primary brain is Groq (remote, free) so local HW never gates the demo; local Gemma 3n `e2b` runs partial-offload as failover. vLLM = cloud path only. ([ADR-0001](docs/decisions/0001-groq-primary-brain-local-fallback.md)) |
| 2 | Which model? | **resolved** | Groq `llama-3.3-70b-versatile` primary; Ollama Gemma 3n `e2b` (→`e4b` if it fits) local failover, both via LangChain. ([ADR-0001](docs/decisions/0001-groq-primary-brain-local-fallback.md)) |
| 3 | Per-player relationships in MVP, or global first? | **resolved** | Per-player from day 1, keyed by `(npc_id, player_id)`. Single demo player. |
| 4 | Sync request/response, or async pre-generation OK? | **resolved** | Sync request/response with token streaming; reflection as background pass. |
| 5 | Lore authoring — hand-written or LLM-generated JSON? | **resolved** | LLM-generated structured JSON lorebook, hand-curated, embedded into Chroma. |

---

## Current phase

**S0 ✅ · S1 ✅ · S2 ✅ · S3 ✅ · S4 ✅ done (unified-agent) → S5 next.** S4 (LangGraph + SQLite checkpointer) built 2026-06-23, then **refactored to a unified agent 2026-06-24** on branch `slice/s4-langgraph-checkpointer` (not yet committed/merged — user handles git). Graph in `app/graph/{state,nodes,build}.py`: **`retrieve_context → agent(persona+tools) ⇄ tools(gate-backed) → write_memory`**. **ONE persona+tools ReAct agent** both proposes tools and writes the in-character reply ([ADR-0009](docs/decisions/0009-unified-agent-turn.md), **supersedes ADR-0007's prose-free split**). Cap (`MAX_AGENT_TURNS=3`) enforced INSIDE the agent — overflow turn drops tools → forced reply (no separate render node, no silent drop). Gate rejections fold back as `ToolMessage`s the agent re-reasons over → refusals explained in character (subsumes ADR-0004's note). `tool_use_failed` cured at the schema layer (`int|str` coercion, ADR-0008) — NOT by prompt separation. `AsyncSqliteSaver` (langgraph **1.2.6**) at `settings.checkpoint_path` (`data/checkpoints.db`, gitignored via `*.db`), `thread_id=f"{npc_id}:{player_id}"`; durable windowed `history` (add_messages, last 20 msgs into prompt) survives restart; per-turn scratch reset in entry node. `talk.py` slim endpoint, per-`thread_id` asyncio lock, `astream_events(version="v2")` forwarding ONLY persona-tagged tokens. Gate still the sole SQLite writer; Chroma recall-only. Tests use a custom scripted streaming+tool fake (`make_scripted_chat`) patched at `app.graph.nodes.get_agent_llm` (stock `GenericFakeChatModel` can't stream tool calls). **45 offline tests pass + LIVE-verified vs Groq** (insult → UpdateDisposition fired + in-character reply; reward-for-unstarted-quest → refused in character, no reward granted). **Re-reviewed** (separate `code-reviewer` pass): 0 CRITICAL, both hard rules upheld; fixes folded — **structural loop termination** (forced turn ignores echoed tool calls → always ends on a reply; `recursion_limit=25` backstop; regression test), empty-stream `"..."` fallback, guarded DB-open in tools node, removed dead `last_gate`. Residuals documented in ADR-0009 addendum (tool-turn prose-leak is by-instruction not structural). Next: **S5** — Chroma lore + grounding gate. Build approach: vertical slices ([ADR-0002](docs/decisions/0002-vertical-slice-build-approach.md)).

## Project state snapshot

- Monorepo: `backend/` `game/` `shared/{contracts,lore}/` `docs/` created 2026-06-22 (ADR-0003).
- S0 shipped 2026-06-22: `backend/app/{main,config}.py`, `app/api/talk.py`, `app/serving/llm.py`, `data/personas/shopkeeper.md`, `tests/test_talk_s0.py`. Deps via `uv` (S0 subset only). `backend/.env` holds `GROQ_API_KEY` (gitignored).
- S1 shipped 2026-06-22: `app/memory/sqlite_store.py` (players/npcs/disposition tables), `app/tools/{schemas,gates}.py`, `app/api/state.py`, propose/dispose wired in `talk.py`, tool LLM at temp 0 + tool-routing prompt in `llm.py`. Tables seed demo `p1`/`shopkeeper`. `*.db` gitignored. Not yet committed/merged at time of writing — user handles git (branch `slice/s1-disposition-gate`).
- S2 shipped 2026-06-22 (branch `slice/s2-reward-quest-gate`, uncommitted): `quests`/`inventory`/`rewards_claimed` tables + helpers in `sqlite_store.py` (incl. atomic `grant_reward`); `GiveReward`/`StartQuest` in `schemas.py`; gate dispatch `validate()` in `gates.py`; reject feedback + `sqlite3.Error` degrade in `talk.py`; `/state` returns `active_quests`; flag renamed `disposition_tool_enabled`→`tools_enabled` (binds all 3 tools). New `tests/test_gate_reward.py`. ADR-0004 records the reject-feedback design.
- S3 shipped 2026-06-23 (branch `slice/s3-episodic-memory`, uncommitted): `app/memory/vector_store.py` (`get_client` [lru_cached] / `get_episodic_collection` / `write_episodic` / `retrieve_episodic`); `chroma_path` in `config.py`; `chromadb>=0.5.0` dep; retrieve+write wiring in `talk.py`; `tests/test_vector_store.py` + `tests/test_talk_recall_s3.py`. `backend/data/chroma/` gitignored. ADR-0006 records the write/importance/recall policy.
- S4 shipped 2026-06-23 (branch `slice/s4-langgraph-checkpointer`, uncommitted): new `app/graph/{__init__,state,nodes,build}.py` (StateGraph + nodes + `get_graph`/`reset_graph` singleton over `AsyncSqliteSaver`); `checkpoint_path` in `config.py`; `langgraph`/`langgraph-checkpoint-sqlite`/`aiosqlite` deps; `talk.py` rewritten to drive the graph + stream persona-tagged tokens; `tests/conftest.py` (shared S4 harness) + new `tests/test_talk_checkpoint_s4.py`; s0/s1/s3 endpoint tests ported to `app.graph.nodes` seams. ADR-0007 records the loop-shape + checkpointer decision. langgraph installed is **1.2.6** (1.x — newer than training memory; API verified by introspection + smoke tests, not guessed).
- Harness set up: 2026-06-21 (CLAUDE.md, MEMORY.md, docs wiki, .claude/settings.json).
