"""S5 lore grounding integration tests.

Patches ``retrieve_lore`` at the nodes seam so no real LightRAG/Groq calls are made.
Mirrors the pattern of test_talk_recall_s3.py.
"""

from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient
from langchain_core.messages import SystemMessage

from app.main import app

from .conftest import make_scripted_chat

REPLY = "Indeed, Corvin Dale was last seen at The Amber Shelf."


@pytest.mark.asyncio
async def test_lore_context_injected_when_grounded(chroma):
    """When retrieve_lore returns rich context the system prompt contains it."""
    sink: list = []
    with (
        patch("app.graph.nodes.retrieve_lore", return_value="Corvin Dale was last seen at The Amber Shelf."),
        patch("app.config.settings.grounding_gate", True),
        patch("app.config.settings.lore_context_min_chars", 10),
        patch("app.graph.nodes.get_agent_llm", return_value=make_scripted_chat([REPLY], sink)),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/npc/shopkeeper/talk",
                json={"player_id": "p1", "message": "What happened to Corvin Dale?"},
            )
            assert resp.status_code == 200

    assert sink, "agent was never called"
    system = next((m for m in sink[0] if isinstance(m, SystemMessage)), None)
    assert system is not None
    assert "Relevant lore" in system.content
    assert "Corvin Dale" in system.content


@pytest.mark.asyncio
async def test_decline_instruction_injected_when_ungrounded(chroma):
    """When retrieve_lore returns empty string the decline instruction is injected."""
    sink: list = []
    with (
        patch("app.graph.nodes.retrieve_lore", return_value=""),
        patch("app.config.settings.grounding_gate", True),
        patch("app.config.settings.lore_context_min_chars", 10),
        patch("app.graph.nodes.get_agent_llm", return_value=make_scripted_chat([REPLY], sink)),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/npc/shopkeeper/talk",
                json={"player_id": "p1", "message": "Tell me about the dragon wars."},
            )
            assert resp.status_code == 200

    assert sink, "agent was never called"
    system = next((m for m in sink[0] if isinstance(m, SystemMessage)), None)
    assert system is not None
    assert "never invent" in system.content or "do not know" in system.content
