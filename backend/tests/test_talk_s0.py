"""S0 tests — endpoint streams a persona reply. No real Groq calls.

Unified agent (ADR-0009): the reply streams out of the single ``agent`` node, patched at
``app.graph.nodes.get_agent_llm``. With ``tools_enabled`` off the agent has no tools bound,
so the turn is a pure persona render.
"""

from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient
from langchain_core.messages import HumanMessage, SystemMessage

from app.main import app

from .conftest import make_scripted_chat

FAKE_TEXT = "Ah, welcome traveller! What can I do for you?"


@pytest.fixture
def no_tools(monkeypatch):
    """S0 predates tools — render persona only."""
    monkeypatch.setattr("app.config.settings.tools_enabled", False)


@pytest.mark.asyncio
async def test_talk_streams_tokens(no_tools, chroma):
    """The endpoint streams the agent's reply tokens back, concatenated."""
    with patch("app.graph.nodes.get_agent_llm", return_value=make_scripted_chat([FAKE_TEXT])):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/npc/shopkeeper/talk",
                json={"player_id": "p1", "message": "hello", "location": "shop"},
            )

    assert response.status_code == 200
    # streamed word-by-word with a trailing space per word
    assert response.text.strip() == FAKE_TEXT


@pytest.mark.asyncio
async def test_talk_sends_persona_as_system_message(no_tools, chroma):
    """The persona markdown must be the first (System) message; the player msg is the Human."""
    sink: list = []
    with patch("app.graph.nodes.get_agent_llm", return_value=make_scripted_chat([FAKE_TEXT], sink)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post(
                "/npc/shopkeeper/talk",
                json={"player_id": "p1", "message": "hello"},
            )

    assert sink, "agent LLM was never called"
    messages = sink[0]
    # First turn, tools off: [SystemMessage(persona), HumanMessage(player)].
    assert len(messages) == 2
    system_msg, human_msg = messages[0], messages[1]
    assert isinstance(system_msg, SystemMessage)
    assert isinstance(human_msg, HumanMessage)
    assert "Mira" in system_msg.content or "shopkeeper" in system_msg.content.lower()
    assert human_msg.content == "hello"


@pytest.mark.asyncio
async def test_talk_missing_persona_returns_404():
    """A non-existent NPC id yields 404 before any graph/LLM work."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/npc/unknown_npc/talk",
            json={"player_id": "p1", "message": "hello"},
        )

    assert response.status_code == 404
