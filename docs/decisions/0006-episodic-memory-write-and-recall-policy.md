# 0006 — Episodic memory: what gets written, provisional importance, similarity-only recall

- **Status:** Accepted
- **Date:** 2026-06-23
- **Relates to:** `../npc-agent-service/v2/implementation.md` §S3, §S6; `../../MEMORY.md`; [ADR-0005](0005-agentic-tool-loop-at-s4.md)

## Context

S3 adds the first memory layer: a ChromaDB `episodic` collection so events persist and re-enter
context on later turns (the "the NPC remembers what just happened" behavior). The slice raised three
design choices that are not dictated by the spec and are worth recording — two of them deliberately
**provisional** (superseded by S6's memory stream).

The overriding rule still holds: **the LLM never owns truth.** Chroma is *fuzzy recall only* — it never
gates an action and never overrides SQLite. Disposition, quests, inventory, and reward-claims remain
authoritative in SQLite; the episodic store is a hint layer prepended to the persona prompt.

## Decision

**1. What gets written (per turn).** After the reply finishes streaming, `talk.py` writes:
- **The conversational turn** — `'The player said: "…". You replied: "…".'` — only when the reply is
  non-empty (an empty/errored stream writes no turn, to avoid recall noise).
- **An accepted tool-call event** — a concrete sentence derived from the `GateResult`
  (`_tool_event_sentence`: reward granted / quest started / disposition shifted) — written **whenever the
  gate accepted**, independent of the reply, so a real state change is always remembered. Rejected calls
  are **not** written (they changed no truth).

Both are written *after* streaming, in the response generator, wrapped in a `try/except` that logs and
swallows failures: **a memory-write error must never break a response that already streamed.** The
Chroma client is opened before the request body and intentionally outlives the SQLite `conn.close()`.

**2. Importance is a provisional heuristic, not an LLM call.** `_importance()` returns 3 (or 5 for long
messages); accepted tool events are hard-coded to 8. No extra LLM call is spent on salience in S3
(cost/latency). This is explicitly a placeholder — **real salience scoring lands in S6** (the memory
stream: recency × importance × relevance).

**3. Recall is plain vector similarity, filtered by `(npc_id, player_id)`.** `retrieve_episodic` returns
top-k by embedding similarity, with a Chroma `$and` where-filter on `npc_id` **and** `player_id` so
memories never bleed across NPCs or players. The stored `importance` is **not** used in ranking yet —
also deferred to S6. Default embedder is Chroma's bundled MiniLM (CPU, downloaded once on first use);
tests inject a deterministic hash embedder so the suite stays offline.

S3 **stays linear** ([ADR-0005](0005-agentic-tool-loop-at-s4.md)): retrieve-before-generate and
write-after-stream are plain function calls around the existing two-call flow, not graph nodes. At S4
they become the `retrieve_context` / `write_memory` nodes with no logic change.

## Alternatives considered

- **LLM-rated importance now** — rejected: an extra Groq call per turn for a number S6 will replace
  anyway. Heuristic is good enough to demonstrate recall.
- **Write the rejected tool call too** — rejected: a rejection changes no truth and would pollute recall
  with non-events; the rejection already surfaces in-character via the prose note (ADR-0004).
- **Importance-weighted recall in S3** — rejected: that *is* S6 (the memory stream is the centerpiece
  slice). Doing a half-version now would be rework.

## Consequences

- **+** Events persist and re-enter context; the two-turn recall behavior works end-to-end.
- **+** Safety boundary untouched — Chroma never gates; SQLite stays the only truth.
- **+** No added per-turn LLM cost for memory in S3.
- **−** Recall ignores `importance` until S6, so a salient old event can be out-ranked by recent trivia —
  exactly the gap S6 (memory stream) exists to close; `importance` is already stored, ready for it.
- **Affected (now):** `backend/app/memory/vector_store.py` (new), `backend/app/api/talk.py`
  (retrieve + write wiring), `backend/app/config.py` (`chroma_path`), `backend/pyproject.toml` (`chromadb`).
- **Affected (at S6):** `retrieve_episodic` ranking + the importance scorer.
