# RPG-Game — NPC Agent Service

Backend service powering **stateful, tool-using LLM NPCs** for an RPG: NPCs that remember the
player across sessions, answer grounded in world lore, and take real actions via validated tool calls.

Full spec: `docs/npc-agent-service/v2/plan.md`. Research notes: `docs/wiki/` (and the queryable OMC wiki — see below).

---

## The one rule that overrides everything

**The LLM never owns truth.** Anything that must be correct (does the player have the key? is the
quest complete? what is the disposition score?) lives in **SQLite** and is checked by deterministic
code — never trusted from the model. The LLM *proposes* actions; code *disposes* (validates) them.

---

## Tech stack (committed)

| Concern | Choice |
|---|---|
| API | FastAPI (async, streaming) |
| Orchestration | LangGraph + SQLite checkpointer (durable per-NPC/per-player state) |
| **NPC brain (primary)** | **Groq free tier** (`llama-3.3-70b-versatile`) via LangChain `ChatGroq` |
| **NPC brain (failover)** | **Ollama Gemma 3n** (`gemma3n:e2b`) local via `ChatOllama` + `.with_fallbacks()` |
| Tool I/O | Pydantic (validated schemas; provider tool-calling on Groq, structured JSON output on local Gemma) |
| Fuzzy memory | ChromaDB (lore + episodic + beliefs collections) |
| Authoritative state | SQLite (disposition, quests, inventory, flags, rewards_claimed) |
| Eval | LLM-as-judge (human-calibrated, ~20 cases) — ablation table + red-team |
| Config | Feature flags (`MEMORY_STREAM`, `GROUNDING_GATE`, `REFLECTION` in `config.py`) |

**Hardware note:** GTX 1660 SUPER 6GB. vLLM continuous batching is infeasible here (no bf16, KV cache won't fit) — documented as cloud-deploy path only. Primary brain is remote (Groq), so local hardware never gates the demo; local Gemma 3n runs partial-offload as failover when Groq is rate-limited.

Language: **Python**. Prefer `async` throughout. Use Pydantic models for all tool I/O and API bodies.

---

## Architecture spine

```
[ Godot client ] --HTTP/WS JSON--> [ FastAPI ] --> NPC Agent Service (backend/)
```

LangGraph nodes: `retrieve_context` → `plan_response` → `propose_tools` → `grounding_gate` → `generate_reply` → `write_memory`.

The **propose/dispose loop** is the headline of the whole service:
1. LLM emits a tool call (provider tool-calling on Groq, or structured JSON on local Gemma).
2. A deterministic **gate** validates it against SQLite ground truth.
3. Accepted → execute + write an `episodic` memory event. Rejected → feed the reason back to the LLM.

---

## Conventions for working in this repo

- **Build order:** ship Phase 1 (MVP) end-to-end before touching Phase 2. A working MVP beats a half-built stretch goal.
- **Suggested layout** lives in `docs/npc-agent-service/v2/plan.md` §9 (`backend/app/graph`, `backend/app/memory`, `backend/app/tools`, `backend/app/serving`, `backend/eval/`).
- **Tools stay small:** 3–4 gated tools for MVP (`GiveReward`, `StartQuest`, `UpdateDisposition`).
- **Every accepted tool call** is also written to episodic memory as an event.
- **Bound mutating deltas** (e.g. clamp disposition delta to [-10, 10]).
- **Stream tokens** from `/npc/{id}/talk`; the typewriter UI hides latency.
- **Evals are the portfolio centerpiece** — report before/after metrics tables (persona consistency, lore grounding / hallucination rate, tool-call accuracy, adversarial hold rate).

## Workflow expectations

- Consult official docs before implementing against LangGraph / FastAPI / ChromaDB / llama.cpp APIs — don't guess SDK shapes.
- Verify before claiming done: run the relevant test/eval and show output.
- Capture research findings in the wiki (`docs/wiki/` + `wiki_ingest`).
- Open questions are resolved in `docs/npc-agent-service/v2/plan.md` §10 — see `MEMORY.md` for the answers.

## Planning — vertical slices, not horizontal layers

When planning any work, **always slice vertically.** A plan is a sequence of thin, end-to-end
increments — each one cuts through every layer it needs (API → graph → tools/gate → SQLite/Chroma →
reply) and leaves the system **working and demonstrable** at the end. Never plan horizontally (don't
do "build the whole SQLite layer", then "build the whole tool layer", then "wire it up" — that
produces nothing runnable until the very end).

- **Each slice ships one observable behavior** a human can implement, run, and verify on its own.
  Example slices: *"NPC echoes a persona-flavored reply over `/talk`"* → *"one gated `UpdateDisposition`
  tool: LLM proposes, gate validates against SQLite, disposition changes"* → *"that event is written to
  episodic memory and recalled next turn."*
- **Order by value + risk:** do the thinnest slice that proves the spine first (the propose/dispose
  loop), then widen. This matches the spec's phase plan (`docs/npc-agent-service/v2/plan.md` §8 — each
  phase is independently shippable).
- **Make slices human-sized:** each should be a small, self-contained PR-worth of work with a clear
  "done = X works" check, not a sprawling cross-cutting change.
- When you present a plan, state it as an ordered list of vertical slices, each with its end-to-end
  outcome and how to verify it.

## Two non-negotiable logging rules

1. **Every decision → an ADR in `docs/decisions/`.** One file per decision, written when the
   decision is made (copy `docs/decisions/0000-template.md`). What we chose and *why*. Don't
   silently re-decide later — supersede with a new ADR. Tick resolved §10 questions in `MEMORY.md`.
2. **Every error or mistake → `MEMORY.md` (Mistakes & Lessons).** A bug we caused, a wrong
   assumption, a failed command, a dead-end approach, a misread spec — record it *before moving on*,
   with root cause and what to do instead. Check that section before repeating a class of work so we
   don't re-make the same mistake.

## Two memory systems — what goes where

This project is touched by **two** memory stores. Don't duplicate between them — route by scope:

| | **Built-in Claude memory** | **Project `MEMORY.md`** |
|---|---|---|
| Path | `~/.claude/projects/<this-project>/memory/` (outside the repo) | repo root (in the repo) |
| Loaded | **Automatically** — index every session, facts via `<system-reminder>` | Only when read (SessionStart hook surfaces phase + latest mistake) |
| Ships with repo? | No (machine-local, personal) | Yes (git-tracked, visible to collaborators/CI) |
| Shape | one atomic fact per file + `MEMORY.md` index | one running document |

**Routing rule:**
- **Cross-session facts about the user or how to work** (preferences, standing guardrails like
  *check-git-before-coding*) → **built-in memory** (atomic, auto-loaded).
- **Project decisions, mistakes, resolved questions, current phase** that should ship with the code →
  **`MEMORY.md`** (this repo). Decisions still get a full ADR in `docs/decisions/`; `MEMORY.md` keeps
  the Mistakes & Lessons log + state snapshot.

If a fact is "about this codebase and should travel in the repo," it's `MEMORY.md`. If it's "about me
/ how Claude should behave across all my work," it's built-in memory.

## Building a feature — reuse first, don't duplicate

Before writing new code for a feature, **search for existing code that already does it or nearly does
it**, and extend/reuse that instead of adding a parallel implementation.

- **No duplicate tools.** Don't create several tools/functions that do the same thing. If two pieces
  of work share behavior, factor out **one reusable, parameterized** helper/tool and call it from
  both. The MVP tool set is deliberately small (`GiveReward`, `StartQuest`, `UpdateDisposition`) —
  add a tool only when no existing one can be generalized to cover the case.
- **Find before you build.** Grep/Glob (or `wiki_query`) for an existing schema, gate, store method,
  or model adapter before introducing a new one. Prefer extending a Pydantic model, a gate function,
  or a memory-store method over copy-pasting a variant.
- **One responsibility, one home.** SQLite access lives in the SQLite store, vector access in the
  vector store, tool validation in the gate layer — don't scatter duplicate logic across nodes.
- When you *do* consolidate or replace a duplicate, note it (ADR if it's a real decision; otherwise a
  short line) so the reuse path is discoverable next time.

## File placement — every file has a home (sort by category)

**No loose files.** Before creating any file, decide its **category** and put it in the matching
folder below. Don't drop files at the repo root or in an arbitrary directory. If a file fits no
category here, add a new row to this table first (so the convention stays the source of truth).

| Category | Folder | What goes there |
|----------|--------|-----------------|
| API routes | `backend/app/api/` | FastAPI routers: `talk`, `state`, `world` |
| Graph | `backend/app/graph/` | LangGraph nodes + graph build |
| Memory stores | `backend/app/memory/` | `sqlite_store.py` (authoritative), `vector_store.py` (Chroma), `stream.py` |
| Tools | `backend/app/tools/` | `schemas.py` (Pydantic models), `gates.py` (propose/dispose validation) |
| Serving | `backend/app/serving/` | `llm.py` (ChatGroq primary + ChatOllama fallback, `bind_tools`/`with_fallbacks`), `tool_parse.py` (native/structured tool-call → Pydantic) |
| App entry | `backend/app/` | `main.py` only |
| Eval | `backend/eval/` | `judge.py`, `run_ablation.py`, `run_redteam.py`, `dataset/` (incl. red-team) |
| Seed data | `backend/data/` | runtime fixtures + the embedded lorebook (`gen_lorebook.py`). Source lore JSON lives in `shared/lore/` |
| Tests | `backend/tests/` | unit/integration tests (mirror `backend/app/` layout) |
| Spec | `docs/` | the build spec + `README.md` index |
| Decisions (ADRs) | `docs/decisions/` | one ADR per decision |
| Research / wiki | `docs/wiki/` | one page per research topic |
| Project memory | repo root | `MEMORY.md` only |
| Harness / config | `.claude/` | `settings.json`, `hooks/` |
| Game client | `game/` | Godot client: scenes, scripts |
| Shared contract | `shared/contracts/` | the `/talk` request/response schema — single source of truth |
| Shared lore | `shared/lore/` | LLM-gen lorebook JSON |

Rules:
- **Match an existing folder before inventing one.** Place a new store method in `backend/app/memory/`,
  a new gate in `backend/app/tools/gates.py`, etc. — consistent with the reuse-first rule above.
- **Root stays minimal:** only `CLAUDE.md`, `MEMORY.md`, `README.md`, and the top-level component
  directories (`backend/`, `game/`, `shared/`, `docs/`) live at the repo root. No packaging files
  at root — `pyproject.toml` lives under `backend/`.
- This mirrors the repo structure in `docs/npc-agent-service/v2/plan.md` §9 — keep the two in sync.

## Model routing (agent management)

**Opus (or any top-tier model) plans; it does not write code.** Reserve Opus for planning,
architecture, design, review/critique, and the hardest reasoning. Delegate the actual coding —
implementation, edits, refactors, mechanical/bulk work — to **cheaper models** (Sonnet for standard
implementation; Haiku for lookups, mechanical edits, and per-item work).

- Route code/implementation to `oh-my-claudecode:executor` (Sonnet) or equivalent — **not** the main Opus loop doing it inline.
- Use `oh-my-claudecode:planner` / `architect` / `critic` (Opus) for plans, architecture, and reviews.
- Keep only conclusions in the Opus context; let subagents hold the file-level detail.
- Keep authoring and review in separate passes (writer creates; `code-reviewer`/`verifier` approves) — never self-approve in the same context.

## Resolved decisions (from v2 §10 — no longer open)

These were open questions in v1 §9; all are resolved in `docs/npc-agent-service/v2/plan.md` §10. See `MEMORY.md` for the full table.

1. **GPU/CPU target** → GTX 1660 SUPER 6GB. Primary brain is Groq (remote); local Gemma 3n partial-offload as failover. vLLM = cloud path only.
2. **Which brain/model** → Groq free tier (`llama-3.3-70b-versatile`) as primary; local Ollama Gemma 3n (`gemma3n:e2b`) as failover via `.with_fallbacks()`. See ADR-0001.
3. **Per-player vs global** → Per-player from day 1, keyed by `(npc_id, player_id)`. Single demo player for MVP.
4. **Sync vs async** → Sync request/response with token streaming; reflection runs as a background pass.
5. **Lore authoring** → LLM-generated structured JSON lorebook, hand-curated, then embedded into Chroma.
