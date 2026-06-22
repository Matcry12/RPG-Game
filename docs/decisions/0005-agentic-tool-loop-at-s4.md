# 0005 — Adopt an agentic tool-calling loop at S4 (replacing the linear two-call flow)

- **Status:** Accepted
- **Date:** 2026-06-22
- **Relates to:** `../npc-agent-service/v2/plan.md` §5.1, §8 (S4); `../npc-agent-service/v2/implementation.md` §S4; [ADR-0004](0004-rejection-feedback-via-persona-prompt.md); `../../MEMORY.md` (Mistakes & Lessons — Groq `tool_use_failed`)

## Context

The per-turn flow built in S0–S2 is **linear**: one prose-free temp-0 *router* call decides at most one
tool → the deterministic **gate** (`gates.validate`) validates it against SQLite → a separate temp-0.7
*persona* call streams the reply. It is reviewed and working (S2: APPROVE-WITH-NITS, 34 tests), but it is
architecturally **split-brained**:

- the router decides an action **blind to the gate's verdict** (it can't see whether the call will be accepted);
- only the **first** proposed tool call is processed — extras are logged and dropped (no multi-tool turns);
- a rejection can only **color the prose** (ADR-0004) — the NPC cannot react by *trying a different action*.

This was the right MVP for S0–S2 for two reasons: (1) it dodges a real Groq bug — `tool_use_failed` 400
when a single message both roleplays **and** emits a tool call (recorded in `MEMORY.md`); the prose-free
router + separate persona render is the mitigation. (2) Slice order: the agent-framework layer (LangGraph
+ SQLite checkpointer) is scheduled for **S4** and is not yet a dependency (`backend/pyproject.toml` has
only `langchain-groq` + `langchain-core`; `app/graph/` is empty).

Linear-vs-agentic is an **orchestration** choice, not a safety one: the gate is a pure function over
`(call, npc_id, player_id, conn, *, now) → GateResult` and disposes every proposed call regardless of how
the turn is orchestrated. "The LLM never owns truth" holds either way, and the gate slots into an S4
tools-node unchanged.

## Decision

At **S4**, when LangGraph and the SQLite checkpointer land, **replace** the fixed
`propose → gate → generate` path with an **agentic tool-calling loop**: an `agent` node that proposes tool
calls, cycling with a **gate-backed tools node** (`gates.validate` runs on every call, the result is fed
back), iterating until the model emits no further tool calls — then render the in-character reply. This
lifts the single-tool-per-turn limit and lets the NPC react to a rejection with a different action.

The loop **must preserve** the `tool_use_failed` mitigation — tool-decision turns stay prose-free.

S2 and S3 remain linear; S3 (episodic memory) is orthogonal (it adds retrieve/write *around* the turn),
so building it on the linear flow is fine and is not wasted work.

### Open — to resolve in the S4 deep-dive

- **Loop shape:** single unified agent that also writes the final reply, vs. agent-loop-for-tools + a
  separate persona render call (keeps the prose/tool split). Trade-off is coherence vs. `tool_use_failed` risk.
- **Max-iterations cap** and Groq free-tier budget per turn (each iteration is another call).
- **How rejection feedback (ADR-0004) folds into the loop** — as a tool message the agent re-reasons over,
  rather than a one-shot prose note.

## Alternatives considered

- **Hand-roll a lightweight loop now (before LangGraph)** — rejected: the loop logic would be written
  once now and rewritten into LangGraph at S4 — a double build that violates the reuse-first rule.
- **Keep the linear two-call flow as the end state** — rejected: it's the wrong target for a project whose
  signal is "Agent Engineer who builds tool-using agents"; it caps turns at one action and can't react to
  gate rejections.

## Consequences

- **+** One implementation of the loop, in its final LangGraph form, at the slice where the framework lands.
- **+** Multi-tool turns and react-to-rejection become possible; removes the S2 single-tool limitation.
- **+** Safety boundary is untouched — the same gate runs inside the tools node.
- **−** Defers richer orchestration until S4; S2/S3 ship with the known single-tool limitation (documented).
- **Affected (at S4):** `backend/app/graph/` (new StateGraph + nodes), `backend/app/api/talk.py`
  (invokes the compiled graph instead of the inline two-call flow), `backend/app/serving/llm.py`,
  `backend/pyproject.toml` (`langgraph`, `langgraph-checkpoint-sqlite`).
