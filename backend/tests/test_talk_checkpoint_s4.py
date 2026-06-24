"""S4 tests — conversation survives a restart + per-turn scratch never leaks (ADR-0009).

Offline: run a turn, drop AND rebuild the compiled graph (simulating a server restart, since
the checkpoint is on disk), then run another turn on the same thread and assert the first
turn's messages were restored into the agent prompt.
"""

from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient
from langchain_core.messages import AIMessage, HumanMessage

from app.main import app

from .conftest import make_scripted_chat, tool_turn

TURN1_MSG = "Remember this: my name is Bramble."
TURN2_MSG = "What did I just tell you?"
REPLY = "Understood."


@pytest.mark.asyncio
async def test_conversation_survives_restart(chroma):
    from app.graph import build

    sink: list = []
    # One scripted fake instance shared across both turns (script: reply, reply).
    llm = make_scripted_chat([REPLY, REPLY], sink)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        with patch("app.graph.nodes.get_agent_llm", return_value=llm):
            r1 = await client.post("/npc/shopkeeper/talk", json={"player_id": "p1", "message": TURN1_MSG})
            assert r1.status_code == 200
            assert r1.text.strip() == REPLY

            await build.reset_graph()  # simulate restart

            r2 = await client.post("/npc/shopkeeper/talk", json={"player_id": "p1", "message": TURN2_MSG})
            assert r2.status_code == 200
            assert r2.text.strip() == REPLY

    # Turn 2's prompt must contain Turn 1's restored conversation.
    assert len(sink) == 2
    turn2 = sink[1]
    humans = [m.content for m in turn2 if isinstance(m, HumanMessage)]
    ais = [m.content for m in turn2 if isinstance(m, AIMessage)]
    assert TURN1_MSG in humans, "Turn 1 player message not restored from checkpoint"
    assert any(REPLY in a for a in ais), "Turn 1 reply not restored from checkpoint"
    assert TURN2_MSG in humans

    graph = await build.get_graph()
    state = await graph.aget_state({"configurable": {"thread_id": "shopkeeper:p1"}})
    assert len(state.values["history"]) == 4  # Human/AI x2


@pytest.mark.asyncio
async def test_tool_scratch_does_not_leak_across_turns(chroma):
    """Turn 1 accepts a tool; Turn 2 (no tool) must NOT inherit turn 1's gate scratch."""
    from app.graph import build

    # Turn 1: tool call then reply. Turn 2: reply only.
    llm = make_scripted_chat([tool_turn("UpdateDisposition", {"delta": -5}), REPLY, REPLY])

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        with patch("app.graph.nodes.get_agent_llm", return_value=llm):
            r1 = await client.post("/npc/shopkeeper/talk", json={"player_id": "p2", "message": "you crook"})
            assert r1.status_code == 200
            r2 = await client.post("/npc/shopkeeper/talk", json={"player_id": "p2", "message": "hello"})
            assert r2.status_code == 200

    graph = await build.get_graph()
    state = await graph.aget_state({"configurable": {"thread_id": "shopkeeper:p2"}})
    assert state.values.get("gate_results", []) == [], "turn-1 gate scratch leaked into turn 2"
