# 0009 — Unify the turn into a single persona+tools ReAct agent (supersedes ADR-0007's split)

- **Status:** Accepted
- **Date:** 2026-06-24
- **Supersedes:** [ADR-0007](0007-s4-loop-shape-and-checkpointer.md) (loop-shape portion; the checkpointer/streaming/state decisions in 0007 still stand)
- **Relates to:** [ADR-0004](0004-rejection-feedback-via-persona-prompt.md), [ADR-0008](0008-tolerant-numeric-tool-arg-schemas.md); `backend/app/graph/`; `../../MEMORY.md`

## Context

ADR-0007 chose a **two-prompt split** for S4: a terse, prose-free `agent` node (tool decisions)
feeding a separate `generate` node (persona render). That was a conservative response to the
`tool_use_failed` scare — keep prose and tools in different prompts so a tool turn can never mix them.

Two later findings (2026-06-24, ~100 live Groq calls — see `MEMORY.md`) changed the calculus:

1. **The real cause of `tool_use_failed` was an argument *type* mismatch** (Llama sends `delta: "-8"`;
   Groq validates types server-side), now fixed structurally at the schema layer ([ADR-0008](0008-tolerant-numeric-tool-arg-schemas.md)) — independent of prompt shape.
2. **A single persona-flavored prompt that says "call the tool, then reply" calls tools reliably.**
   Measured 20/20 (after the schema fix) including in-character replies; the failures we'd attributed
   to "prose + tools" actually came from prompts that demanded *same-message* mixing, not from one prompt.

So the split's main benefit (structural prose/tool separation) is no longer load-bearing: the type-400
is cured elsewhere, and sequential ("tool turn, then reply turn") behavior is achievable in one prompt.

## Decision

Replace the two-prompt split with a **single unified `agent` node** — one persona prompt with tools
bound — looping with the gate-backed `tools` node:

```
START → retrieve_context → agent(persona + tools) ⇄ tools(gate) → write_memory → END
```

- `agent` `astream`s (persona-tagged). If the streamed turn carries tool calls it loops through the
  gate; otherwise its content **is** the in-character reply (streamed to the client).
- **The iteration cap lives inside `agent`:** once `agent_turns >= MAX_AGENT_TURNS` it is invoked
  **without tools**, forcing a final reply — the loop always terminates on prose, never on a dropped
  tool call (no separate `generate`/`finalize` node).
- **Gate rejections fold in as `ToolMessage`s** the agent re-reasons over, so a refusal is explained
  in character on the next turn. This **subsumes ADR-0004's separate persona note** (removed).

Everything else from ADR-0007 is retained: `AsyncSqliteSaver` checkpointer keyed `(npc_id, player_id)`,
durable `history` (windowed into the prompt), per-turn scratch reset in the entry node, `astream_events`
streaming filtered by the `persona` tag, and the gate as the sole writer of SQLite truth.

**The prose-free-tool-turn guarantee is now by *instruction* (the prompt says "call the tool, no
dialogue that turn"), not by a separate prompt** — a deliberate trade of a structural guard for
simplicity, justified by the measured reliability and the schema-level `tool_use_failed` cure.

## Alternatives considered

- **Keep the ADR-0007 split** — rejected: its structural guarantee is no longer needed (type-400 fixed
  at the schema; one prompt calls tools reliably), and it carries an extra prompt + render node.
- **Unify into ONE LLM *call* (tool + reply in a single generation)** — rejected: breaks the gate-ordering
  invariant (the reply would be written before the gate validates) and can't stream cleanly. The ReAct
  *loop* keeps the gate strictly between a tool call and the reply.
- **LangGraph prebuilt `create_react_agent` / `ToolNode`** — rejected: `ToolNode` auto-executes tools,
  bypassing our gate. We keep a custom gate-backed tools node.

## Consequences

- **+** Simpler graph: one agent prompt, no separate render node, no `route_after_retrieve`.
- **+** Replies are more coherent (written with full tool-result context) and rejections are handled
  naturally via tool feedback rather than a bolted-on note.
- **+** Verified live: insult → `UpdateDisposition` fired + in-character reply; reward-for-unstarted-quest
  → refused in character, no reward granted (gate held). 44 offline tests green.
- **−** The "no prose on a tool turn" property is now enforced by prompt instruction, not structurally —
  a different persona could in principle narrate during a tool turn (low risk; `astream` filtering only
  forwards non-empty content, and our reliability batch showed none). Mitigated by explicit guidance.
- **−** Tool-decision turns now run at persona temperature (0.7) rather than 0; the schema coercion
  (ADR-0008) keeps them well-formed. Re-measure if a new numeric tool is added.
- **Affected:** `backend/app/serving/llm.py` (`get_agent_llm`), `backend/app/graph/nodes.py`
  (unified `agent`, removed `generate`/`route_after_retrieve`), `backend/app/graph/build.py` (topology),
  `backend/tests/` (scripted streaming+tool fake).

## Review addendum (2026-06-24)

Independent re-review (separate `code-reviewer` pass): 0 CRITICAL, both hard rules upheld. Fixes folded:

- **Termination no longer depends on the model (was HIGH).** The cap previously only unbound tools; if the
  model echoed a tool call on the forced turn, `route_after_agent` could loop. Now the forced turn is
  **structurally a reply turn** — the agent ignores any echoed tool calls and appends a tool-call-free
  AIMessage, so the graph always routes to `write_memory`. A `recursion_limit=25` backstop was added in
  `talk.py`. Regression test: `test_agent_that_always_calls_tools_terminates`.
- **No silent empty 200 (MEDIUM).** If zero persona tokens stream (empty/forced-empty generation), the
  endpoint emits a `"..."` fallback.
- **DB-open in the tools node guarded (MEDIUM).** A `connect`/`init_db` failure now rejects every proposed
  call with a ToolMessage so the agent still replies in character, instead of aborting the turn silently.
- **Removed dead `last_gate` state (LOW)** — unused after ADR-0004's note was subsumed by ToolMessage feedback.
- **Noted the unbounded per-`thread_id` lock map (LOW)** as an MVP follow-up.

Accepted residual risks (documented, not fixed): tool-decision turns *could* stream prose tokens if a
persona narrates mid-tool-turn (the gate-ordering invariant holds for persisted state, not the byte stream),
and the BadRequestError fallback could duplicate tokens in that same case — both bounded by the prose-free
instruction and unobserved in the live reliability batch. 45 tests pass, ruff clean.
