from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from app.config import settings
from app.serving.llm import get_llm

router = APIRouter()


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
    messages = [SystemMessage(content=persona_text), HumanMessage(content=request.message)]

    llm = get_llm()

    async def token_stream():
        async for chunk in llm.astream(messages):
            yield chunk.content

    return StreamingResponse(token_stream(), media_type="text/plain")
