"""Talk endpoint — propose/dispose loop wired end-to-end.

Flow (composable functions; S4 will lift these into LangGraph nodes):
  1. Open DB, read current disposition.
  2. Propose: ask the tool-bound LLM which tool (if any) to call.
     Uses a terse tool-routing system prompt (NOT the persona) at temperature 0
     so the model emits only a structured tool call, never mixed prose+tool output.
  3. Dispose: gate validates + persists (gate is the only writer; SQLite is truth).
  4. Generate: stream the in-character reply using the full persona system prompt,
     injecting a brief system note when the gate accepted or rejected an action.
"""

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import groq
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, ValidationError

from app.config import settings
from app.memory.sqlite_store import connect, get_disposition, init_db
from app.serving.llm import get_llm, get_tool_llm
from app.tools import gates
from app.tools.schemas import GiveReward, StartQuest, UpdateDisposition

logger = logging.getLogger(__name__)

router = APIRouter()

_TOOL_ROUTING_SYSTEM = (
    "You are the action-control module for an RPG NPC. "
    "Decide whether the player's latest message warrants a tool call. "
    "Available tools:\n"
    "  • UpdateDisposition(delta) — shift how the NPC feels about the player "
    "(negative = worse, positive = better, range -10 to 10).\n"
    "  • StartQuest(quest_id) — begin a quest the player hasn't started yet. "
    "Only call this when the player explicitly asks to start or take on a quest.\n"
    "  • GiveReward(quest_id, item_id, reason) — grant an item reward for a completed quest. "
    "Only call this when the player asks for or clearly expects their reward after finishing a quest.\n"
    "If no tool applies, do not call any tool. "
    "Do NOT write any dialogue or prose."
)

# Map tool name strings to their Pydantic schema classes.
_TOOL_SCHEMA_MAP: dict[str, type] = {
    "UpdateDisposition": UpdateDisposition,
    "GiveReward": GiveReward,
    "StartQuest": StartQuest,
}


class TalkRequest(BaseModel):
    player_id: str
    message: str
    location: str | None = None


@router.post("/npc/{npc_id}/talk")
async def talk(npc_id: str, request: TalkRequest) -> StreamingResponse:
    persona_path: Path = settings.persona_dir / f"{npc_id}.md"
    if not persona_path.exists():
        raise HTTPException(status_code=404, detail=f"NPC persona '{npc_id}' not found")

    persona_text = persona_path.read_text(encoding="utf-8")

    # ------------------------------------------------------------------
    # 1. Open DB and read current disposition for context.
    # ------------------------------------------------------------------
    conn = connect(settings.db_path)
    gate_result = None
    try:
        init_db(conn)
        current_score = get_disposition(conn, npc_id, request.player_id)

        # ------------------------------------------------------------------
        # 2 & 3. Propose / dispose — skipped entirely when flag is off.
        #
        # IMPORTANT: the tool-proposal call uses a SEPARATE, terse system prompt
        # (not the persona). This prevents the model from mixing roleplay prose
        # with a tool call in one message, which Groq rejects as tool_use_failed.
        # ------------------------------------------------------------------
        if settings.tools_enabled:
            tool_routing_messages = [
                SystemMessage(
                    content=(
                        f"{_TOOL_ROUTING_SYSTEM}\n"
                        f"(current disposition score: {current_score})"
                    )
                ),
                HumanMessage(content=request.message),
            ]
            tool_llm = get_tool_llm()
            try:
                proposal = await tool_llm.ainvoke(tool_routing_messages)
            except groq.BadRequestError as exc:
                # Groq rejected the generation (e.g. tool_use_failed 400).
                # A failed proposal is best-effort — degrade gracefully to "no tool"
                # rather than crashing the turn.
                logger.warning(
                    "Groq BadRequestError during tool proposal — treating as no tool call. "
                    "NPC: %s  Player: %s  Error: %s",
                    npc_id,
                    request.player_id,
                    exc,
                )
                proposal = None

            if proposal is not None and proposal.tool_calls:
                if len(proposal.tool_calls) > 1:
                    logger.warning(
                        "Model proposed %d tool calls; only the first will be processed. "
                        "Extra calls dropped: %s",
                        len(proposal.tool_calls),
                        [tc.get("name") for tc in proposal.tool_calls[1:]],
                    )
                raw_call = proposal.tool_calls[0]
                tool_name = raw_call.get("name", "")
                raw_args = raw_call["args"]
                schema_cls = _TOOL_SCHEMA_MAP.get(tool_name)
                try:
                    if schema_cls is None:
                        raise TypeError(f"Unknown tool name from model: {tool_name!r}")
                    call = schema_cls(**raw_args)
                    now = datetime.now(timezone.utc).isoformat()
                    gate_result = gates.validate(
                        call, npc_id, request.player_id, conn, now=now
                    )
                    # TODO(S3): write episodic event for this tool call
                except (ValidationError, KeyError, TypeError, ValueError, sqlite3.Error) as exc:
                    logger.warning(
                        "Malformed tool-call args from model — skipping gate (no SQLite write). "
                        "Args: %r  Error: %s",
                        raw_args,
                        exc,
                    )
    finally:
        conn.close()

    # ------------------------------------------------------------------
    # 4. Generate: stream the in-character reply (second LLM call).
    #    Uses the FULL persona system prompt — entirely separate from the
    #    tool-routing call above.
    #    When the gate rejected an action, inject a brief system note so
    #    the NPC explains in-character why the action can't happen.
    #    When the gate accepted an action, optionally note the outcome.
    # ------------------------------------------------------------------
    persona_messages = [
        SystemMessage(
            content=(
                f"{persona_text}\n\n"
                f"(current disposition toward this player: {current_score})"
            )
        ),
    ]

    if gate_result is not None and not gate_result.accepted:
        persona_messages.append(
            SystemMessage(
                content=(
                    f"The player attempted an action that was refused by the world rules: "
                    f"{gate_result.reason}. "
                    "Stay fully in character and explain, in your own voice, why you can't do "
                    "that right now. Do not break character or mention rules/systems."
                )
            )
        )
    elif gate_result is not None and gate_result.accepted and gate_result.granted_item:
        persona_messages.append(
            SystemMessage(
                content=(
                    f"You just granted the player: {gate_result.granted_item}. "
                    "Acknowledge this naturally in character."
                )
            )
        )
    elif gate_result is not None and gate_result.accepted and gate_result.quest_id and not gate_result.granted_item:
        persona_messages.append(
            SystemMessage(
                content=(
                    "You just agreed to start the quest with the player. "
                    "Acknowledge it naturally in character."
                )
            )
        )

    persona_messages.append(HumanMessage(content=request.message))

    llm = get_llm()

    async def token_stream():
        async for chunk in llm.astream(persona_messages):
            yield chunk.content

    return StreamingResponse(token_stream(), media_type="text/plain")
