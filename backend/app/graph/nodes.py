"""LangGraph nodes for the ``/talk`` turn (S4, unified-agent shape — ADR-0009).

The turn is a single-prompt ReAct agent looping with a gate-backed tools node:

    retrieve_context → agent(persona + tools) ⇄ tools(gate) → write_memory

- ``agent`` uses ONE persona prompt with tools bound. It ``astream``s (persona-tagged) so
  the endpoint forwards its reply tokens. If the streamed turn carries tool calls it loops
  through the gate; otherwise its content IS the in-character reply. The iteration cap lives
  inside the node: once ``agent_turns >= MAX_AGENT_TURNS`` the agent is invoked WITHOUT tools,
  forcing a final reply (no silent drop, no separate render node).
- ``tools`` is the gate: ``gates.validate`` runs on every proposed call (SQLite stays the only
  writer of truth); each verdict returns as a ToolMessage the agent re-reasons over — so a
  rejection is explained in character on the next turn (this replaces ADR-0004's separate note).
- ``write_memory`` persists episodic events after the reply (ADR-0006), exception-guarded.

The prose-free tool turn is now enforced by *instruction* (the persona prompt says "call the
tool, no dialogue that turn"), not by a separate terse prompt; ADR-0009 records that trade-off.
The ``tool_use_failed`` type-validation 400 is cured at the schema layer (``int | str`` coercion,
ADR-0008); the ``try/except`` here is defense-in-depth.
"""

import logging
import sqlite3
from datetime import datetime, timezone

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from pydantic import ValidationError

from app.config import settings
from app.memory.sqlite_store import (
    add_to_importance_sum,
    connect,
    get_disposition,
    init_db,
    reset_importance_sum,
)
from app.memory.vector_store import (
    get_beliefs_collection,
    get_client,
    get_episodic_collection,
    retrieve_beliefs,
    retrieve_episodic,
    retrieve_episodic_scored,
    retrieve_for_reflection,
    retrieve_lore,
    write_belief,
    write_episodic,
)
from app.serving.llm import get_agent_llm
from app.tools import gates
from app.tools.schemas import GiveReward, StartQuest, UpdateDisposition

from .state import TurnState

logger = logging.getLogger(__name__)

# Max agent LLM calls that may carry tools per turn (ADR-0007/0009): bounds Groq spend and
# guarantees termination. On the overflow turn the agent is called WITHOUT tools, so it must
# produce a reply — the loop always ends on prose, never on a dropped tool call.
# Map tool name strings to their Pydantic schema classes (for the gate).
_TOOL_SCHEMA_MAP: dict[str, type] = {
    "UpdateDisposition": UpdateDisposition,
    "GiveReward": GiveReward,
    "StartQuest": StartQuest,
}

# ---------------------------------------------------------------------------
# Episodic helpers (provisional; real salience scoring lands in S6 — ADR-0006).
# ---------------------------------------------------------------------------


def _tool_event_sentence(gate: dict) -> str:
    """Derive a concrete episodic sentence from an accepted gate result dict."""
    if gate.get("granted_item"):
        return (
            f"You gave the player {gate['granted_item']} as a reward "
            f"for the {gate.get('quest_id')} quest."
        )
    if gate.get("quest_id"):
        return f"You agreed to start the {gate['quest_id']} quest with the player."
    return (
        f"Your opinion of the player shifted by {gate.get('clamped_delta')} "
        f"(now {gate.get('new_score')})."
    )


def _tool_importance(gate: dict) -> int:
    """Map an accepted gate result to its importance score (S7 design — ADR-0013).

    UpdateDisposition: abs(delta) reflects the actual relationship shift.
    GiveReward/StartQuest: fixed values from config so they're tunable in one place.
    """
    if gate.get("clamped_delta") is not None:
        return min(settings.importance_max, abs(gate["clamped_delta"]))
    if gate.get("granted_item"):
        return settings.importance_give_reward
    if gate.get("quest_id"):
        return settings.importance_start_quest
    return 0


async def _run_reflection(
    npc_id: str,
    player_id: str,
    episodic,
    chroma_client,
    persona_text: str,
) -> None:
    """Pull high-importance events, ask the LLM for a single belief, write it to beliefs."""
    events = retrieve_for_reflection(
        episodic,
        npc_id=npc_id,
        player_id=player_id,
        min_importance=settings.reflection_min_importance,
        limit=settings.reflection_event_limit,
    )
    if not events:
        return

    event_lines = "\n".join(f"- {e['text']}" for e in events)
    llm = get_agent_llm(with_tools=False)
    result = await llm.ainvoke(
        [
            SystemMessage(
                content=(
                    f"{persona_text}\n\nYou are reflecting privately on your recent "
                    "interactions with the traveller. Based only on what actually happened, "
                    "form a single clear conclusion about them."
                )
            ),
            HumanMessage(
                content=(
                    f"Recent events:\n{event_lines}\n\n"
                    "Despite any contradictions, what is your SINGLE strongest feeling or "
                    "conclusion about this player? One sentence, in your voice, first person."
                )
            ),
        ]
    )
    belief_text = result.content.strip() if isinstance(result.content, str) else ""
    if not belief_text:
        return

    ts = datetime.now(timezone.utc).isoformat()
    beliefs = get_beliefs_collection(chroma_client)
    write_belief(beliefs, npc_id=npc_id, player_id=player_id, text=belief_text, timestamp=ts)
    logger.info("S7 reflection written for (%s,%s): %r", npc_id, player_id, belief_text)


def _persona_system(state: TurnState) -> SystemMessage:
    """Build the single persona+tools system prompt for the agent."""
    guidance = (
        (
            "\n\nYou can take real actions through tools (adjust how you feel about the player, start a "
            "quest, grant a reward). When the player's words or actions warrant one, CALL THE TOOL and "
            "write no dialogue in that turn. After you see the tool result, reply fully in character — "
            "and if an action was refused, explain in your own voice why you can't do it, without "
            "breaking character or mentioning rules/systems. Never write a tool call inside your dialogue."
        )
        if settings.tools_enabled
        else ""
    )
    lore_part = ""
    if settings.grounding_gate:
        if state.get("grounded"):
            lore_part = (
                f"\n\n{state.get('lore_block', '')}\n\nSpeak only to what the lore above confirms. "
                "Do not invent names, places, events, or history beyond what is stated."
            )
        else:
            lore_part = (
                "\n\nYou have no lore records on this topic. If the traveller asks about "
                "world facts you do not know, say so in your own voice — never invent "
                "names, places, or history."
            )
    return SystemMessage(
        content=(
            f"{state['persona_text']}{state.get('memory_block', '')}{lore_part}{guidance}\n\n"
            f"(current disposition toward this player: {state['current_score']})"
        )
    )


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------


async def retrieve_context(state: TurnState) -> dict:
    """Entry node: reset per-turn scratch, read disposition (SQLite) + recall events (Chroma).

    Resetting scratch here (not only in the endpoint) keeps any caller safe (review HIGH-3).
    Both stores are best-effort: a failure degrades to a neutral default rather than 500-ing
    the turn before a token streams (review MEDIUM-3).
    """
    npc_id, player_id, message = state["npc_id"], state["player_id"], state["message"]

    current_score = 0
    try:
        conn = connect(settings.db_path)
        try:
            init_db(conn)
            current_score = get_disposition(conn, npc_id, player_id)
        finally:
            conn.close()
    except sqlite3.Error as exc:
        logger.warning(
            "Disposition read failed (degrading to 0) — (%s,%s): %s",
            npc_id,
            player_id,
            exc,
        )

    recalled: list[dict] = []
    client = get_client(settings.chroma_path)
    if settings.episodic_memory:
        try:
            episodic = get_episodic_collection(client)
            if settings.memory_stream:
                recalled = retrieve_episodic_scored(
                    episodic, npc_id=npc_id, player_id=player_id, query=message, k=settings.episodic_recall_k
                )
            else:
                recalled = retrieve_episodic(
                    episodic, npc_id=npc_id, player_id=player_id, query=message, k=settings.episodic_recall_k
                )
        except Exception as exc:  # recall is best-effort context, never fatal
            logger.warning(
                "Episodic recall failed (degrading to no recall) — (%s,%s): %s",
                npc_id,
                player_id,
                exc,
            )

    if recalled:
        logger.info(
            "S3 recall for (%s,%s): %r",
            npc_id,
            player_id,
            [r["text"] for r in recalled],
        )

    memory_block = ""
    if recalled:
        lines = "\n".join(f"- {r['text']}" for r in recalled)
        memory_block = (
            f"\n\nThings you remember from past encounters with this player:\n{lines}"
        )

    if settings.reflection:
        try:
            beliefs_col = get_beliefs_collection(client)
            beliefs = retrieve_beliefs(beliefs_col, npc_id=npc_id, player_id=player_id)
            if beliefs:
                belief_lines = "\n".join(f"- {b['text']}" for b in beliefs)
                memory_block += f"\n\nYour current beliefs about this player:\n{belief_lines}"
        except Exception as exc:
            logger.warning(
                "Beliefs recall failed (non-fatal) — (%s,%s): %s", npc_id, player_id, exc
            )

    lore_block = ""
    grounded = False
    if settings.grounding_gate:
        lore_history: list[dict] = []
        for msg in list(state.get("history", []))[-settings.lore_history_window:]:
            if isinstance(msg, HumanMessage):
                lore_history.append({"role": "user", "content": msg.content})
            elif isinstance(msg, AIMessage):
                lore_history.append({"role": "assistant", "content": msg.content})

        try:
            lore_ctx = await retrieve_lore(
                npc_id,
                message,
                history=lore_history,
                lightrag_path=settings.lightrag_path,
                groq_api_key=settings.groq_api_key,
                groq_model=settings.groq_model,
            )
            grounded = len(lore_ctx) >= settings.lore_context_min_chars
            if grounded:
                lore_block = f"\n\nRelevant lore for this conversation:\n{lore_ctx}"
                logger.info(
                    "S5 lore retrieved for (%s,%s): %d chars",
                    npc_id,
                    player_id,
                    len(lore_ctx),
                )
            else:
                logger.info(
                    "S5 lore: no grounded context for (%s,%s)", npc_id, player_id
                )
        except Exception as exc:
            logger.warning(
                "Lore retrieval failed (degrading to ungrounded) — (%s,%s): %s",
                npc_id,
                player_id,
                exc,
            )

    return {
        "current_score": current_score,
        "recalled": recalled,
        "memory_block": memory_block,
        "lore_block": lore_block,
        "grounded": grounded,
        # Reset per-turn scratch (overwrite, no reducer).
        "loop_messages": [],
        "agent_turns": 0,
        "gate_results": [],
        "reply": "",
    }


async def _astream_agent(llm, messages):
    """Stream the agent LLM and aggregate chunks into one AIMessage-like result."""
    final = None
    async for chunk in llm.astream(messages):
        final = chunk if final is None else final + chunk
    return final


async def agent(state: TurnState) -> dict:
    """Unified persona+tools agent. Either proposes tool calls (→ gate) or writes the reply.

    Streams via ``astream`` (persona-tagged) so the endpoint forwards reply tokens. Tool-turn
    generations carry no prose, so nothing streams on those turns. On the overflow turn
    (cap reached) tools are dropped, forcing a final reply.
    """
    turns = state.get("agent_turns", 0)
    force_reply = turns >= settings.agent_max_turns

    history = list(state.get("history", []))[-settings.history_window:]
    loop = list(state.get("loop_messages", []))
    messages = [
        _persona_system(state),
        *history,
        HumanMessage(content=state["message"]),
        *loop,
    ]

    llm = get_agent_llm(with_tools=not force_reply).with_config(tags=["persona"])
    final = None
    try:
        final = await _astream_agent(llm, messages)
    except Exception as exc:
        # Catches BadRequestError (tool_use_failed), RateLimitError, and any other provider
        # error. A tool-decision turn streams no prose, so nothing reached the client yet.
        # Try once more without tools; if that also fails final stays None → '...' fallback.
        logger.warning(
            "Groq error in agent — forcing a tool-free reply. NPC: %s  Error: %s",
            state["npc_id"],
            exc,
        )
        try:
            fallback = get_agent_llm(with_tools=False).with_config(tags=["persona"])
            final = await _astream_agent(fallback, messages)
        except Exception as exc2:
            logger.warning("Tool-free fallback also failed — NPC: %s  Error: %s", state["npc_id"], exc2)

    content = final.content if (final and isinstance(final.content, str)) else ""
    tool_calls = list(getattr(final, "tool_calls", None) or [])

    out: dict = {"agent_turns": turns + 1}
    if tool_calls and not force_reply:
        # Tool-decision turn → loop through the gate.
        out["loop_messages"] = [
            *loop,
            AIMessage(content=content, tool_calls=tool_calls),
        ]
    else:
        # Reply turn. On the forced (cap) turn we IGNORE any tool calls the now-unbound model
        # might still echo, so the appended AIMessage carries no tool_calls and the graph ALWAYS
        # terminates on a reply (review HIGH-1) — termination no longer depends on the model.
        if not content.strip():
            logger.warning(
                "Agent produced an empty reply — NPC: %s (endpoint emits a fallback).",
                state["npc_id"],
            )
        out["loop_messages"] = [*loop, AIMessage(content=content)]
        out["reply"] = content
        out["history"] = [
            HumanMessage(content=state["message"]),
            AIMessage(content=content),
        ]
    return out


def route_after_agent(state: TurnState) -> str:
    """Loop to the gate when the agent proposed tool calls; otherwise the reply is done."""
    loop = state.get("loop_messages", [])
    last = loop[-1] if loop else None
    return "tools" if getattr(last, "tool_calls", None) else "write_memory"


async def tools(state: TurnState) -> dict:
    """Gate-backed tools node: dispose every proposed call against SQLite truth.

    Each verdict is appended as a ToolMessage the agent re-reasons over (so a rejection is
    explained in character next turn). Accepted results are collected for the episodic write.
    """
    loop = list(state.get("loop_messages", []))
    last = loop[-1]
    npc_id, player_id = state["npc_id"], state["player_id"]

    new_msgs: list[ToolMessage] = []
    gate_results = list(state.get("gate_results", []))

    # Guard the connection open itself (review MEDIUM-2): if the DB is unavailable, reject every
    # proposed call with a ToolMessage so the agent still produces a graceful in-character reply.
    try:
        conn = connect(settings.db_path)
        init_db(conn)
    except sqlite3.Error as exc:
        logger.warning(
            "DB unavailable in tools node — rejecting all proposed calls. Error: %s",
            exc,
        )
        rejects = [
            ToolMessage(
                content="rejected: backend unavailable",
                tool_call_id=(tc.get("id") or f"{tc.get('name', 'tool')}_{i}"),
            )
            for i, tc in enumerate(last.tool_calls)
        ]
        return {"loop_messages": [*loop, *rejects], "gate_results": gate_results}

    try:
        for i, tc in enumerate(last.tool_calls):
            name = tc.get("name", "")
            raw_args = tc.get("args", {})
            tc_id = tc.get("id") or f"{name}_{i}"
            schema_cls = _TOOL_SCHEMA_MAP.get(name)
            try:
                if schema_cls is None:
                    raise TypeError(f"Unknown tool name from model: {name!r}")
                call = schema_cls(**raw_args)
                now = datetime.now(timezone.utc).isoformat()
                result = gates.validate(call, npc_id, player_id, conn, now=now)
                if result.accepted:
                    gate_results.append(result.model_dump())
                content = (
                    f"{'accepted' if result.accepted else 'rejected'}: {result.reason}"
                )
            except (
                ValidationError,
                KeyError,
                TypeError,
                ValueError,
                sqlite3.Error,
            ) as exc:
                logger.warning(
                    "Malformed tool-call args from model — skipping gate (no SQLite write). "
                    "Args: %r  Error: %s",
                    raw_args,
                    exc,
                )
                content = f"rejected: malformed tool call ({exc})"
            new_msgs.append(ToolMessage(content=content, tool_call_id=tc_id))
    finally:
        conn.close()

    return {"loop_messages": [*loop, *new_msgs], "gate_results": gate_results}


async def write_memory(state: TurnState) -> dict:
    """Persist episodic events after the reply. A failure here must never break the turn."""
    npc_id, player_id = state["npc_id"], state["player_id"]
    reply = state.get("reply", "")
    message = state["message"]

    client = get_client(settings.chroma_path)
    episodic = get_episodic_collection(client)
    turn_importance = 0
    try:
        ts = datetime.now(timezone.utc).isoformat()

        if settings.episodic_memory and reply.strip():
            turn_text = f'The player said: "{message}". You replied: "{reply}".'
            write_episodic(
                episodic,
                npc_id=npc_id,
                player_id=player_id,
                text=turn_text,
                timestamp=ts,
                importance=settings.importance_plain_turn,
            )
            turn_importance += settings.importance_plain_turn

        for gr in state.get("gate_results", []):
            imp = _tool_importance(gr)
            write_episodic(
                episodic,
                npc_id=npc_id,
                player_id=player_id,
                text=_tool_event_sentence(gr),
                timestamp=ts,
                importance=imp,
            )
            turn_importance += imp
    except Exception as exc:
        logger.warning(
            "Episodic write failed (non-fatal) — NPC: %s  Player: %s  Error: %s",
            npc_id,
            player_id,
            exc,
        )

    if settings.reflection and turn_importance > 0:
        try:
            conn = connect(settings.db_path)
            try:
                init_db(conn)
                new_total = add_to_importance_sum(conn, npc_id, player_id, turn_importance)
                if new_total >= settings.reflection_threshold:
                    reset_importance_sum(conn, npc_id, player_id)
                    await _run_reflection(
                        npc_id,
                        player_id,
                        episodic,
                        client,
                        state.get("persona_text", ""),
                    )
            finally:
                conn.close()
        except Exception as exc:
            logger.warning(
                "Reflection trigger failed (non-fatal) — NPC: %s  Player: %s  Error: %s",
                npc_id,
                player_id,
                exc,
            )

    return {}
