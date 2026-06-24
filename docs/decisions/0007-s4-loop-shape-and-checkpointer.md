# 0007 — S4 loop shape: prose-free tool-loop + separate persona render, SqliteSaver checkpointer

- **Status:** Superseded by [ADR-0009](0009-unified-agent-turn.md) (loop-shape only — the checkpointer, streaming, durable-state, and review-addendum decisions here still stand)
- **Date:** 2026-06-23
- **Relates to:** `../npc-agent-service/v2/implementation.md` §S4; [ADR-0005](0005-agentic-tool-loop-at-s4.md) (this resolves its three open items); [ADR-0004](0004-rejection-feedback-via-persona-prompt.md); `../../MEMORY.md` (Mistakes & Lessons — Groq `tool_use_failed`)

## Context

[ADR-0005](0005-agentic-tool-loop-at-s4.md) committed us to building the agentic tool-loop **at S4**, when
LangGraph + the SQLite checkpointer land, but explicitly **deferred three design choices to "the S4
deep-dive"**:

1. **Loop shape** — a single unified agent that both calls tools *and* writes the prose reply, vs. an
   agent-loop-for-tools followed by a *separate* persona render call.
2. **Max-iterations cap** per turn (each loop iteration is another Groq free-tier call).
3. **How rejection feedback ([ADR-0004](0004-rejection-feedback-via-persona-prompt.md)) folds into the loop.**

The hard constraint shaping all three is the recorded Groq bug (`MEMORY.md`, 2026-06-22): Llama-3.3-70b on
Groq returns `tool_use_failed` (400) when a *single* message both roleplays and emits a tool call. The
S0–S2 mitigation — a prose-free temp-0 tool-decision call kept separate from the temp-0.7 persona render —
must survive S4.

## Decision

**1. Loop shape — prose-free tool-loop + separate persona render.** The graph is:

```
retrieve_context → agent ⇄ tools(gate-backed) → generate → write_memory
                     └────── (loop) ──────┘
```

- `agent` — temp-0, tools bound, **terse tool-routing system prompt** (the existing `_TOOL_ROUTING_SYSTEM`,
  not the persona). Emits structured tool calls only, never prose. This is the `tool_use_failed` mitigation,
  unchanged.
- `tools` — the **gate-backed** node: `gates.validate` runs on *every* proposed call (SQLite stays the only
  writer of truth), and each result is appended as a `ToolMessage` the agent re-reasons over.
- Conditional edge: if the agent emitted tool calls → `tools` → back to `agent`; else → `generate`.
- `generate` — temp-0.7, **no tools bound**, full persona system prompt; streams the in-character reply.

We **reject the unified single-agent shape**: one model both calling tools and writing prose is exactly the
condition that triggers `tool_use_failed` on this provider. Keeping the split costs one extra LLM call per
turn but preserves the only mitigation we have.

**2. Max-iterations cap = 3.** The `agent ⇄ tools` cycle runs at most 3 agent turns per request, then forces
the path to `generate` regardless. Bounds Groq free-tier spend and guarantees termination if the model loops.
The MVP rarely needs more than one tool; 3 leaves room for react-to-rejection (try A → rejected → try B).

**3. Rejection feedback folds in as a `ToolMessage`.** A gate rejection is fed back into the loop as the
tool result the agent re-reasons over (it may now try a different action — the capability S4 unlocks). The
**final** gate result of the turn still conditions the persona render via the system note from
[ADR-0004](0004-rejection-feedback-via-persona-prompt.md), so the NPC explains the outcome in character.

**Durable state & checkpointer.** Graph state carries `history: Annotated[list, add_messages]` holding **only**
the Human turn and the final persona-AI turn — the real conversation. The tool-loop's working messages
(routing system prompt, tool-call/`ToolMessage` pairs) are **per-turn scratch**, deliberately kept *out* of
the persisted conversation so they never pollute later turns or re-feed prose into a tool call. The
checkpointer is `AsyncSqliteSaver` (async app) at `settings.checkpoint_path`, with
`thread_id = f"{npc_id}:{player_id}"` — so killing and restarting the server and reconnecting as the same
player resumes the conversation (the S4 headline). **Chroma episodic recall (S3) is unchanged and orthogonal**
— it remains fuzzy recall layered into the persona prompt; the checkpointer is the exact conversational thread.

**Streaming preserved.** The endpoint drives the graph with `astream_events(version="v2")` and forwards only
the persona node's token chunks (the persona LLM is tagged so its `on_chat_model_stream` events are
distinguishable from the agent node's). `write_memory` runs as the node *after* `generate`, so the episodic
write-after-stream behavior (ADR-0006) is preserved inside the graph.

## Alternatives considered

- **Unified single agent (one model calls tools *and* writes prose)** — rejected: directly re-triggers the
  `tool_use_failed` 400 on Groq/Llama. The whole prose/tool split exists to avoid this.
- **No iteration cap / cap = 1** — rejected: no cap risks runaway Groq spend and non-termination; cap = 1
  would forfeit the react-to-rejection capability that is the point of the loop.
- **Persist the full message list (tool calls + ToolMessages) as conversation history** — rejected: it
  pollutes later turns and risks feeding prior prose back into a tool-decision call. Persist only the
  Human/persona conversation; keep the loop scratch per-turn.

## Consequences

- **+** Multi-tool turns and react-to-rejection are now possible; the S2 single-tool limitation is lifted.
- **+** Conversation survives a server restart (checkpointed per `(npc_id, player_id)`) — the resume headline.
- **+** Safety boundary untouched: the same `gates.validate` runs inside the tools node; SQLite stays truth.
- **+** `tool_use_failed` mitigation preserved (tool turns stay prose-free, temp 0).
- **−** One extra LLM call per turn vs. a (hypothetical) unified agent, and up to 3 agent calls on a
  multi-tool turn — a deliberate cost for provider-safety and bounded by the cap.
- **−** `talk.py` is restructured to invoke a compiled graph; tests that patched `app.api.talk.get_tool_llm`
  / `get_llm` move to patch the graph nodes' seams. The public `/talk` contract is unchanged.
- **Affected:** `backend/app/graph/state.py` (new), `backend/app/graph/build.py` + nodes (new),
  `backend/app/api/talk.py` (invokes the graph), `backend/app/config.py` (`checkpoint_path`),
  `backend/pyproject.toml` (`langgraph`, `langgraph-checkpoint-sqlite`), `backend/tests/` (graph + restart tests).

## Review addendum (2026-06-23)

Independent code review (separate `code-reviewer` pass) confirmed all three hard rules hold and found
0 CRITICAL / 0 security issues. Fixes applied in this slice:

- **Unbounded history (HIGH):** `generate` now windows the conversation to the last
  `HISTORY_WINDOW_MESSAGES` (=20) messages so a long-lived thread can't blow Groq's context/TPM. The full
  thread stays checkpointed — only the prompt is trimmed.
- **Concurrent same-thread interleaving (HIGH):** `talk.py` serializes turns with a per-`thread_id`
  `asyncio.Lock`, so two overlapping requests for the same player can't interleave their read-modify-write
  of `history`. *Residual:* single-process scope only — a multi-worker deploy would need a shared lock.
- **Scratch-reset contract (HIGH):** the per-turn scratch (`loop_messages`/`agent_turns`/`gate_results`/
  `last_gate`/`reply`) is now reset inside the `retrieve_context` entry node, so any caller of the graph
  starts clean (no leak from the checkpoint). Guarded by `test_tool_scratch_does_not_leak_across_turns`.
- **`retrieve_context` unguarded (MEDIUM):** now degrades a SQLite/Chroma failure to neutral defaults
  instead of 500-ing the turn — closing the Rule-3 asymmetry. The stream loop in `talk.py` is also wrapped.
- **SQLite lock contention (MEDIUM):** `sqlite_store.connect` now enables WAL + a 5s busy timeout.
- **Minor (MEDIUM/LOW):** safer chunk accumulation (no `str()` coercion into history); unique fallback
  `tool_call_id` on id-less multi-tool calls.

Documented (not fixed — accepted for the MVP):

- **Tool-round budget:** the cap is `MAX_AGENT_TURNS` *agent calls*; since the turn must end on a persona
  render, the effective *tool-execution* budget is `MAX_AGENT_TURNS - 1` (=2 rounds — enough for
  try-A → rejected → try-B → render). Comment clarified in `nodes.py`.
- **Intra-turn stale score (LOW):** within a multi-round turn the agent reasons on the turn-start
  disposition; an accepted mid-turn `UpdateDisposition` isn't reflected until the next turn.
- **`_tool_event_sentence` infers event type from populated fields (LOW):** correct for current
  `GateResult` shapes; an explicit `event_type` would be more robust if the gate grows.
