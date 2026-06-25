"""Talk endpoint — drives the LangGraph turn graph (S4).

The propose/dispose loop, episodic memory, and persona render now live as graph nodes
(``app/graph/``). This endpoint:
  1. Loads the persona, builds the per-turn input + a ``thread_id`` keyed on
     ``(npc_id, player_id)`` so the checkpointer resumes the right conversation.
  2. Serializes turns per ``thread_id`` (review HIGH-1) so two overlapping requests for the
     same player can't interleave their read-modify-write of the checkpointed history.
  3. Runs the compiled graph with ``astream_events`` and streams ONLY the persona node's
     tokens to the client (the prose-free tool-routing call is never streamed).
The episodic write and checkpoint persistence happen inside the graph as it runs.
"""

import asyncio
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.config import settings
from app.graph.build import get_graph

logger = logging.getLogger(__name__)

router = APIRouter()


class TalkRequest(BaseModel):
    player_id: str
    message: str
    location: str | None = None


# Per-thread_id locks so concurrent same-player turns don't interleave their checkpoint
# read-modify-write (review HIGH-1). setdefault is atomic in single-process asyncio.
# ponytail: unbounded map (one lock per distinct player) — fine for MVP, prune if needed.
_thread_locks: dict[str, asyncio.Lock] = {}


@router.post("/npc/{npc_id}/talk")
async def talk(npc_id: str, request: TalkRequest) -> StreamingResponse:
    persona_path: Path = settings.persona_dir / f"{npc_id}.md"
    if not persona_path.exists():
        raise HTTPException(status_code=404, detail=f"NPC persona '{npc_id}' not found")

    persona_text = persona_path.read_text(encoding="utf-8")

    graph = await get_graph()
    thread_id = f"{npc_id}:{request.player_id}"
    # recursion_limit is a hard backstop on the agent⇄tools loop (review HIGH-1); the agent
    # node already forces a reply at MAX_AGENT_TURNS, this just bounds any pathological case.
    config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 25}
    lock = _thread_locks.setdefault(thread_id, asyncio.Lock())

    # Per-turn input. `history` is intentionally NOT seeded — the agent node appends the
    # player turn + reply and the checkpointer merges them into the durable thread. The
    # per-turn scratch channels are reset inside the retrieve_context entry node, so the
    # endpoint only supplies the request fields.
    turn_input = {
        "npc_id": npc_id,
        "player_id": request.player_id,
        "message": request.message,
        "persona_text": persona_text,
    }

    async def token_stream():
        streamed_any = False
        async with lock:
            try:
                async for event in graph.astream_events(
                    turn_input, config, version="v2"
                ):
                    if event[
                        "event"
                    ] == "on_chat_model_stream" and "persona" in event.get("tags", []):
                        content = event["data"]["chunk"].content
                        if isinstance(content, str) and content:
                            streamed_any = True
                            yield content
            except Exception:
                # The turn graph guards each node, but if anything still escapes mid-stream
                # we log and end the stream cleanly rather than leak a traceback to the client.
                logger.exception(
                    "Turn graph failed mid-stream — NPC: %s  thread: %s",
                    npc_id,
                    thread_id,
                )
        # Never hand the client an empty 200 (review MEDIUM-1): if no persona token streamed
        # (empty/errored generation, or a forced reply that produced nothing), emit a fallback.
        if not streamed_any:
            yield "..."

    return StreamingResponse(token_stream(), media_type="text/plain")
