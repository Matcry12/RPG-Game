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
**Watch for:** Any Groq/Llama tool-calling path where one call must both emit prose and call a tool, or runs at high temperature — both make `tool_use_failed` likely. Catch provider errors at the **API-call boundary**, not only at parse time. This will matter again for `GiveReward`/`StartQuest` (S2) and the local Gemma path (S10).

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

**S0 ✅ · S1 ✅ · S2 ✅ done → S3 next.** S2 (reward/quest gate + reject feedback) built + reviewed 2026-06-22, **live-verified 2026-06-23** on branch `slice/s2-reward-quest-gate` (not yet committed/merged — user handles git). Live round-trip vs real Groq: insult → `disposition -1` in SQLite; "give me the reward/legendary sword" → model proposes `GiveReward` → gate rejects (quest not found, since the NPC isn't told quest IDs until S3/S5) → NPC refuses **in character** → `/state` unchanged (`inventory:[]`, no quest change). The propose→dispose→refuse-in-voice→no-state-leak loop holds end-to-end. Gate generalized into one `validate(call, …)` dispatch (no duplication); added `GiveReward`/`StartQuest` with the *reject* path; rejection reason conditions the persona generate call → in-character refusal, no second tool round-trip ([ADR-0004](docs/decisions/0004-rejection-feedback-via-persona-prompt.md)). Code-reviewed APPROVE-WITH-NITS, all nits folded in: **atomic** `grant_reward` (one `with conn:` txn, claim-first PK guard → no double-grant), seeded runtime-reachable quests (`rat_cellar`=complete for the grant demo, `lost_locket`=not_started for StartQuest, `herb_delivery`=active for reject demo), `sqlite3.Error` added to talk.py degrade tuple, quest-state whitelist, StartQuest accept-ack. 34 tests pass. Next: **S3** — episodic write + recall (Chroma `episodic`), wire the `TODO(S3)` in talk.py — **stays linear** (orthogonal to orchestration). The current two-call flow is intentionally linear; the **agentic tool-loop is deferred to S4** (LangGraph), which lifts the single-tool/turn + no-re-decide limits ([ADR-0005](docs/decisions/0005-agentic-tool-loop-at-s4.md)). Build approach: vertical slices ([ADR-0002](docs/decisions/0002-vertical-slice-build-approach.md)).

## Project state snapshot

- Monorepo: `backend/` `game/` `shared/{contracts,lore}/` `docs/` created 2026-06-22 (ADR-0003).
- S0 shipped 2026-06-22: `backend/app/{main,config}.py`, `app/api/talk.py`, `app/serving/llm.py`, `data/personas/shopkeeper.md`, `tests/test_talk_s0.py`. Deps via `uv` (S0 subset only). `backend/.env` holds `GROQ_API_KEY` (gitignored).
- S1 shipped 2026-06-22: `app/memory/sqlite_store.py` (players/npcs/disposition tables), `app/tools/{schemas,gates}.py`, `app/api/state.py`, propose/dispose wired in `talk.py`, tool LLM at temp 0 + tool-routing prompt in `llm.py`. Tables seed demo `p1`/`shopkeeper`. `*.db` gitignored. Not yet committed/merged at time of writing — user handles git (branch `slice/s1-disposition-gate`).
- S2 shipped 2026-06-22 (branch `slice/s2-reward-quest-gate`, uncommitted): `quests`/`inventory`/`rewards_claimed` tables + helpers in `sqlite_store.py` (incl. atomic `grant_reward`); `GiveReward`/`StartQuest` in `schemas.py`; gate dispatch `validate()` in `gates.py`; reject feedback + `sqlite3.Error` degrade in `talk.py`; `/state` returns `active_quests`; flag renamed `disposition_tool_enabled`→`tools_enabled` (binds all 3 tools). New `tests/test_gate_reward.py`. ADR-0004 records the reject-feedback design.
- Harness set up: 2026-06-21 (CLAUDE.md, MEMORY.md, docs wiki, .claude/settings.json).
