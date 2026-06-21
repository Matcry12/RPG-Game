"""Talk endpoint — propose/dispose loop wired end-to-end.

Flow (composable functions; S4 will lift these into LangGraph nodes):
  1. Open DB, read current disposition.
  2. Propose: ask the tool-bound LLM whether it wants to call UpdateDisposition.
     Uses a terse tool-routing system prompt (NOT the persona) at temperature 0
     so the model emits only a structured tool call, never mixed prose+tool output.
  3. Dispose: gate clamps + persists (gate is the only writer; SQLite is truth).
  4. Generate: stream the in-character reply using the full persona system prompt.
"""

import logging
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
from app.tools.gates import validate_update_disposition
from app.tools.schemas import UpdateDisposition

logger = logging.getLogger(__name__)

router = APIRouter()

_TOOL_ROUTING_SYSTEM = (
    "You are the disposition-control module for an RPG NPC. "
    "Decide whether the player's latest message should change how the NPC feels about them. "
    "If yes, call UpdateDisposition with an integer delta "
    "(negative = worse, positive = better, range roughly -10 to 10). "
    "If no change is warranted, do not call any tool. "
    "Do NOT write any dialogue or prose."
)


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
        if settings.disposition_tool_enabled:
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
                        "Model proposed %d tool calls; only the first will be processed (S1 handles one UpdateDisposition). "
                        "Extra calls dropped: %s",
                        len(proposal.tool_calls),
                        [tc.get("name") for tc in proposal.tool_calls[1:]],
                    )
                raw_args = proposal.tool_calls[0]["args"]
                try:
                    call = UpdateDisposition(**raw_args)
                    now = datetime.now(timezone.utc).isoformat()
                    _gate_result = validate_update_disposition(
                        call, npc_id, request.player_id, conn, now=now
                    )
                    # TODO(S3): write episodic event for this tool call
                except (ValidationError, KeyError, TypeError, ValueError) as exc:
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
    # ------------------------------------------------------------------
    persona_messages = [
        SystemMessage(
            content=(
                f"{persona_text}\n\n"
                f"(current disposition toward this player: {current_score})"
            )
        ),
        HumanMessage(content=request.message),
    ]
    llm = get_llm()

    async def token_stream():
        async for chunk in llm.astream(persona_messages):
            yield chunk.content

    return StreamingResponse(token_stream(), media_type="text/plain")
