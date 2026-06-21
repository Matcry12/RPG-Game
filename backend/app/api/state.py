"""State API — read authoritative NPC/player state from SQLite."""

from fastapi import APIRouter

from app.config import settings
from app.memory.sqlite_store import connect, get_disposition, init_db

router = APIRouter()


@router.get("/npc/{npc_id}/state")
async def get_state(npc_id: str, player_id: str) -> dict:
    """Return the current disposition score for this (npc, player) pair."""
    conn = connect(settings.db_path)
    try:
        init_db(conn)
        score = get_disposition(conn, npc_id, player_id)
    finally:
        conn.close()

    return {"npc_id": npc_id, "player_id": player_id, "disposition": score}
