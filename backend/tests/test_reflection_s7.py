"""S7 reflection tests — importance scoring, accumulator, beliefs collection."""

import sqlite3
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import chromadb
import pytest

from app.memory import sqlite_store as store
from app.memory.vector_store import (
    get_beliefs_collection,
    get_episodic_collection,
    retrieve_beliefs,
    retrieve_for_reflection,
    write_belief,
    write_episodic,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mem_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    store.init_db(conn)
    return conn


def _chroma(tmp_path):
    return chromadb.PersistentClient(path=str(tmp_path / "chroma"))


TS = "2026-01-01T00:00:00+00:00"


# ---------------------------------------------------------------------------
# Importance scoring via _tool_importance
# ---------------------------------------------------------------------------


def test_tool_importance_disposition_uses_abs_delta():
    from app.graph.nodes import _tool_importance

    gate = {"clamped_delta": -8, "new_score": -8}
    assert _tool_importance(gate) == 8


def test_tool_importance_disposition_capped_at_max():
    from app.graph.nodes import _tool_importance
    from app.config import settings

    gate = {"clamped_delta": 10, "new_score": 10}
    assert _tool_importance(gate) == settings.importance_max


def test_tool_importance_give_reward():
    from app.graph.nodes import _tool_importance
    from app.config import settings

    gate = {"granted_item": "silver_coin", "quest_id": "herb_delivery"}
    assert _tool_importance(gate) == settings.importance_give_reward


def test_tool_importance_start_quest():
    from app.graph.nodes import _tool_importance
    from app.config import settings

    gate = {"quest_id": "lost_locket"}
    assert _tool_importance(gate) == settings.importance_start_quest


def test_tool_importance_plain_turn_zero():
    """Plain turns carry no importance — model already signalled significance via tool calls."""
    from app.config import settings

    assert settings.importance_plain_turn == 0


# ---------------------------------------------------------------------------
# SQLite accumulator
# ---------------------------------------------------------------------------


def test_accumulator_starts_at_zero():
    conn = _mem_conn()
    assert store.get_importance_sum(conn, "shopkeeper", "p1") == 0


def test_accumulator_add_returns_new_total():
    conn = _mem_conn()
    total = store.add_to_importance_sum(conn, "shopkeeper", "p1", 8)
    assert total == 8


def test_accumulator_add_is_cumulative():
    conn = _mem_conn()
    store.add_to_importance_sum(conn, "shopkeeper", "p1", 8)
    total = store.add_to_importance_sum(conn, "shopkeeper", "p1", 7)
    assert total == 15


def test_accumulator_reset():
    conn = _mem_conn()
    store.add_to_importance_sum(conn, "shopkeeper", "p1", 20)
    store.reset_importance_sum(conn, "shopkeeper", "p1")
    assert store.get_importance_sum(conn, "shopkeeper", "p1") == 0


def test_accumulator_isolated_by_player(tmp_path):
    conn = _mem_conn()
    store.add_to_importance_sum(conn, "shopkeeper", "p1", 10)
    assert store.get_importance_sum(conn, "shopkeeper", "p2") == 0


# ---------------------------------------------------------------------------
# Beliefs collection
# ---------------------------------------------------------------------------


def test_write_belief_retrievable(tmp_path):
    client = _chroma(tmp_path)
    col = get_beliefs_collection(client)
    write_belief(col, npc_id="shopkeeper", player_id="p1", text="I trust this traveller.", timestamp=TS)
    results = retrieve_beliefs(col, npc_id="shopkeeper", player_id="p1")
    assert len(results) == 1
    assert "trust" in results[0]["text"]


def test_beliefs_isolated_by_player(tmp_path):
    client = _chroma(tmp_path)
    col = get_beliefs_collection(client)
    write_belief(col, npc_id="shopkeeper", player_id="p1", text="p1 belief", timestamp=TS)
    assert retrieve_beliefs(col, npc_id="shopkeeper", player_id="p2") == []


def test_beliefs_empty_collection(tmp_path):
    client = _chroma(tmp_path)
    col = get_beliefs_collection(client)
    assert retrieve_beliefs(col, npc_id="shopkeeper", player_id="p1") == []


# ---------------------------------------------------------------------------
# retrieve_for_reflection — metadata filter, no embedding query
# ---------------------------------------------------------------------------


def test_retrieve_for_reflection_returns_high_importance_only(tmp_path):
    client = _chroma(tmp_path)
    col = get_episodic_collection(client)
    write_episodic(col, npc_id="npc1", player_id="p1", text="low", timestamp=TS, importance=0)
    write_episodic(col, npc_id="npc1", player_id="p1", text="high", timestamp=TS, importance=8)
    results = retrieve_for_reflection(col, npc_id="npc1", player_id="p1", min_importance=5)
    assert len(results) == 1
    assert results[0]["text"] == "high"


def test_retrieve_for_reflection_isolated_by_player(tmp_path):
    client = _chroma(tmp_path)
    col = get_episodic_collection(client)
    write_episodic(col, npc_id="npc1", player_id="p2", text="other", timestamp=TS, importance=9)
    results = retrieve_for_reflection(col, npc_id="npc1", player_id="p1", min_importance=5)
    assert results == []


def test_retrieve_for_reflection_empty_collection(tmp_path):
    client = _chroma(tmp_path)
    col = get_episodic_collection(client)
    assert retrieve_for_reflection(col, npc_id="npc1", player_id="p1", min_importance=5) == []


# ---------------------------------------------------------------------------
# write_memory importance routing (unit — no Chroma/SQLite I/O)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_write_memory_plain_turn_zero_importance(tmp_path):
    """Plain turn must not increment the accumulator."""
    conn = _mem_conn()
    state = {
        "npc_id": "shopkeeper",
        "player_id": "p1",
        "message": "Hello",
        "reply": "Good day!",
        "gate_results": [],
        "persona_text": "You are Mira.",
    }

    with (
        patch("app.graph.nodes.get_client"),
        patch("app.graph.nodes.get_episodic_collection"),
        patch("app.graph.nodes.write_episodic"),
        patch("app.graph.nodes.settings") as mock_settings,
        patch("app.graph.nodes.connect", return_value=conn),
        patch("app.graph.nodes.init_db"),
        patch("app.graph.nodes.add_to_importance_sum") as mock_add,
    ):
        mock_settings.reflection = True
        mock_settings.importance_plain_turn = 0
        mock_settings.chroma_path = str(tmp_path)

        from app.graph.nodes import write_memory
        await write_memory(state)

    mock_add.assert_not_called()
