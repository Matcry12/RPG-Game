"""State API — read authoritative NPC/player state from SQLite."""

from fastapi import APIRouter

from app.config import settings
from app.memory.sqlite_store import (
    connect,
    get_active_quests,
    get_disposition,
    get_inventory,
    init_db,
)

router = APIRouter()


@router.get("/npc/{npc_id}/state")
async def get_state(npc_id: str, player_id: str) -> dict:
    """Return current disposition, active quests, and inventory for this (npc, player) pair."""
    conn = connect(settings.db_path)
    try:
        init_db(conn)
        score = get_disposition(conn, npc_id, player_id)
        active_quests = get_active_quests(conn, player_id)
        inventory = get_inventory(conn, player_id)
    finally:
        conn.close()

    return {
        "npc_id": npc_id,
        "player_id": player_id,
        "disposition": score,
        "active_quests": active_quests,
        "inventory": inventory,
    }
