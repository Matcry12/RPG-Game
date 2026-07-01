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

import asyncio
import logging
import re
import sqlite3
from datetime import datetime, timezone
from functools import lru_cache

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
    retrieve_episodic_scored,
    retrieve_for_reflection,
    retrieve_lore,
    write_belief,
    write_episodic,
)
from app.serving.llm import extract_lore_query, get_agent_llm
from app.tools import gates
from app.tools.schemas import GiveReward, SetQuestState, UpdateDisposition

from .state import TurnState

logger = logging.getLogger(__name__)

# Max agent LLM calls that may carry tools per turn (ADR-0007/0009): bounds Groq spend and
# guarantees termination. On the overflow turn the agent is called WITHOUT tools, so it must
# produce a reply — the loop always ends on prose, never on a dropped tool call.
# Map tool name strings to their Pydantic schema classes (for the gate).
_TOOL_SCHEMA_MAP: dict[str, type] = {
    "UpdateDisposition": UpdateDisposition,
    "GiveReward": GiveReward,
    "SetQuestState": SetQuestState,
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
    # Trivial turns (greetings, acks) use a short persona — name + intro only, ~50 tok vs ~430.
    persona = (
        _extract_short_persona(state["persona_text"])
        if state.get("route") == "trivial"
        else state["persona_text"]
    )
    lore_block = state.get("lore_block", "")
    lore_part = ""
    if lore_block:
        lore_part = (
            f"\n\n{lore_block}\n\nSpeak only to what the lore above confirms. "
            "Do not invent names, places, events, or history beyond what is stated."
        )
    elif settings.grounding_gate and state.get("grounded") is False:
        lore_part = (
            "\n\nYou have no lore records on this topic. If the traveller asks about "
            "world facts you do not know, say so in your own voice — never invent "
            "names, places, or history."
        )
    return SystemMessage(
        content=(
            f"{persona}{state.get('memory_block', '')}{lore_part}{guidance}\n\n"
            f"(current disposition toward this player: {state['current_score']})"
        )
    )


# ---------------------------------------------------------------------------
# Session-scoped caches (cleared on server restart = session TTL)
# ---------------------------------------------------------------------------

_lore_cache: dict[tuple[str, int], str] = {}    # (npc_id, hash(message)) → lore text
_beliefs_cache: dict[tuple[str, str], str] = {}  # (npc_id, player_id) → formatted belief block

# ---------------------------------------------------------------------------
# Semantic router
# ---------------------------------------------------------------------------

# Heuristic fallback — used when settings.semantic_routing is False (default for tests).
_LORE_DOMAIN = frozenset({
    "kingdom", "empire", "war", "battle", "history", "legend", "myth",
    "dragon", "king", "queen", "ruler", "lord", "guild", "order",
    "ancient", "origin", "prophecy", "artifact", "magic", "curse",
    "city", "town", "village", "forest", "mountain", "dungeon", "bandit",
    "tavern", "inn", "castle", "temple", "shrine",
})


_ROUTE_UTTERANCES: dict[str, list[str]] = {
    "trivial": [
        "hi", "hello", "hey", "good morning", "good evening",
        "goodbye", "bye", "see you", "farewell",
        "thanks", "thank you", "ok", "okay", "sure", "alright", "got it",
    ],
    "full-no-lore": [
        "how are you doing", "what can you sell me", "show me your wares",
        "I want to buy something", "what do you have for sale",
        "help me with something", "I need your assistance",
        "can you help me", "what do you do here",
    ],
    "full-with-lore": [
        "tell me about the kingdom", "who is the king", "what happened here",
        "history of this city", "what do you know about the bandits",
        "tell me about the missing merchant", "who rules this land",
        "what is the legend of", "what war happened", "explain the faction",
        "where is the dungeon", "tell me about the artifact",
    ],
}


@lru_cache(maxsize=1)
def _build_route_centroids():
    """Lazy-load fastembed model and pre-embed route utterances (downloads model once)."""
    import numpy as np
    from fastembed import TextEmbedding

    model = TextEmbedding("BAAI/bge-small-en-v1.5")
    centroids = {}
    for route, utterances in _ROUTE_UTTERANCES.items():
        embeds = list(model.embed(utterances))
        centroids[route] = np.mean(embeds, axis=0)
    return model, centroids


def _classify_embedding(message: str) -> str:
    import numpy as np

    model, centroids = _build_route_centroids()
    q = list(model.embed([message]))[0]
    best = max(
        centroids.items(),
        key=lambda kv: np.dot(q, kv[1]) / (np.linalg.norm(q) * np.linalg.norm(kv[1]) + 1e-9),
    )
    return best[0]


def _classify_heuristic(message: str) -> str:
    words = message.lower().split()
    if "?" in message or any(w in _LORE_DOMAIN for w in words):
        return "full-with-lore"
    if len(words) <= 4:
        return "trivial"
    return "full-no-lore"


def _extract_short_persona(full_text: str) -> str:
    """Name + intro paragraph only — strips YAML frontmatter and ## sections."""
    text = re.sub(r"^---\n.*?\n---\n\n?", "", full_text, flags=re.DOTALL).strip()
    return re.split(r"\n##", text, maxsplit=1)[0].strip()


def classify_turn(state: TurnState) -> dict:
    """Route the turn: trivial skips retrieval; lore-route triggers lore pre-fetch."""
    msg = state["message"]
    route = _classify_embedding(msg) if settings.semantic_routing else _classify_heuristic(msg)
    return {"route": route}


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------


async def retrieve_context(state: TurnState) -> dict:
    """Fetch context for this turn. Route-aware: trivial turns skip all retrieval.

    For full routes, lore (async HTTP) is kicked off as a background task immediately so
    its network latency overlaps with the fast synchronous ChromaDB reads (episodic + beliefs).
    Scored episodic (S6 stream) is always used; memory_stream flag is removed.
    """
    npc_id, player_id, message = state["npc_id"], state["player_id"], state["message"]
    route = state.get("route", "full-with-lore")

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

    # Trivial turns (greetings, acks) skip all retrieval — just disposition is enough.
    if route == "trivial":
        return {
            "current_score": current_score,
            "recalled": [],
            "memory_block": "",
            "lore_block": "",
            "grounded": None,
            "loop_messages": [],
            "agent_turns": 0,
            "gate_results": [],
            "reply": "",
        }

    client = get_client(settings.chroma_path)

    lore_history = [
        {"role": "user" if isinstance(m, HumanMessage) else "assistant", "content": m.content}
        for m in list(state.get("history", []))[-settings.lore_history_window:]
        if isinstance(m, (HumanMessage, AIMessage))
    ]

    # Mix mode (ADR-0015): one history-aware LLM call rewrites the query + extracts keywords,
    # resolving vague references ("who is he?") before retrieval. The rewritten query feeds
    # both LightRAG and episodic recall (Option A). Falls back to naive + raw message on failure.
    lore_query_str = message
    episodic_query = message
    lore_mode = "naive"
    ll_kw: list[str] | None = None
    hl_kw: list[str] | None = None
    if route == "full-with-lore" and settings.grounding_gate and settings.lore_query_mode == "mix":
        lq = await extract_lore_query(message, lore_history[-settings.lore_rewrite_history_window:])
        if lq:
            lore_query_str = episodic_query = lq.rewritten_query
            lore_mode = "mix"
            ll_kw, hl_kw = lq.ll_keywords or None, lq.hl_keywords or None
            logger.info("Mix rewrite (%s): %r -> %r", npc_id, message[:40], lq.rewritten_query[:60])

    # Cache on the actual query+mode used: the rewritten query already encodes the history
    # resolution, so different contexts ("who is he?") never collide and naive keeps message-keyed hits.
    _lore_key = (npc_id, hash((lore_query_str, lore_mode)))

    async def _fetch_lore() -> str:
        if _lore_key in _lore_cache:
            logger.info("Lore cache hit for (%s, %r)", npc_id, lore_query_str[:40])
            return _lore_cache[_lore_key]
        try:
            result = await retrieve_lore(
                npc_id, lore_query_str,
                history=lore_history,
                lightrag_path=settings.lightrag_path,
                groq_api_key=settings.groq_api_key,
                groq_model=settings.groq_model,
                mode=lore_mode,
                ll_keywords=ll_kw,
                hl_keywords=hl_kw,
            )
            _lore_cache[_lore_key] = result
            return result
        except Exception as exc:
            logger.warning("Lore retrieval failed (degrading to ungrounded) — (%s,%s): %s", npc_id, player_id, exc)
            return ""

    lore_task = asyncio.create_task(_fetch_lore()) if route == "full-with-lore" and settings.grounding_gate else None
    if lore_task:
        await asyncio.sleep(0)  # yield so lore's first HTTP request is sent before sync work starts

    # Sync ChromaDB reads (fast, ~5 ms) while lore HTTP is in flight.
    recalled: list[dict] = []
    if settings.episodic_memory:
        try:
            episodic = get_episodic_collection(client)
            recalled = retrieve_episodic_scored(
                episodic, npc_id=npc_id, player_id=player_id, query=episodic_query, k=settings.episodic_recall_k
            )
            if recalled:
                logger.info("S3 recall for (%s,%s): %r", npc_id, player_id, [r["text"] for r in recalled])
        except Exception as exc:
            logger.warning("Episodic recall failed (degrading to no recall) — (%s,%s): %s", npc_id, player_id, exc)

    belief_block = ""
    if settings.reflection:
        _belief_key = (npc_id, player_id)
        if _belief_key in _beliefs_cache:
            belief_block = _beliefs_cache[_belief_key]
        else:
            try:
                beliefs_col = get_beliefs_collection(client)
                beliefs = retrieve_beliefs(beliefs_col, npc_id=npc_id, player_id=player_id)
                if beliefs:
                    belief_block = "\n\nYour current beliefs about this player:\n" + "\n".join(f"- {b['text']}" for b in beliefs)
                _beliefs_cache[_belief_key] = belief_block
            except Exception as exc:
                logger.warning("Beliefs recall failed (non-fatal) — (%s,%s): %s", npc_id, player_id, exc)

    lore_ctx = await lore_task if lore_task else ""

    # Build prompt blocks.
    memory_block = ""
    if recalled:
        lines = "\n".join(f"- {r['text']}" for r in recalled)
        memory_block = f"\n\nThings you remember from past encounters with this player:\n{lines}"
    memory_block += belief_block

    lore_block = ""
    grounded = None  # None = lore not attempted (full-no-lore); False = attempted, not found
    if route == "full-with-lore" and settings.grounding_gate:
        grounded = len(lore_ctx) >= settings.lore_context_min_chars
        if grounded:
            lore_block = f"\n\nRelevant lore for this conversation:\n{lore_ctx}"
            logger.info("S5 lore retrieved for (%s,%s): %d chars", npc_id, player_id, len(lore_ctx))
        else:
            logger.info("S5 lore: no grounded context for (%s,%s)", npc_id, player_id)

    return {
        "current_score": current_score,
        "recalled": recalled,
        "memory_block": memory_block,
        "lore_block": lore_block,
        "grounded": grounded,
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
                    _beliefs_cache.pop((npc_id, player_id), None)
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
