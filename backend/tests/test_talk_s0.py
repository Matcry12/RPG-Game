"""S0 tests — no real Groq calls, LLM is monkeypatched."""
from typing import AsyncIterator
from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from langchain_core.messages import AIMessageChunk

from app.main import app

# ---------------------------------------------------------------------------
# Fake LLM helpers
# ---------------------------------------------------------------------------

FAKE_TOKENS = ["Ah, ", "welcome ", "traveller! ", "What ", "can ", "I ", "do ", "for ", "you?"]


async def _fake_astream(messages) -> AsyncIterator[AIMessageChunk]:
    """Yields fake chunks and records the messages it received."""
    _fake_astream.last_messages = messages  # type: ignore[attr-defined]
    for token in FAKE_TOKENS:
        yield AIMessageChunk(content=token)


_fake_astream.last_messages = []  # type: ignore[attr-defined]


def make_fake_llm():
    """Return a mock LLM whose .astream() is our async generator."""
    llm = MagicMock()
    llm.astream = _fake_astream
    return llm


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_llm_patch():
    """Patch get_llm in the talk router module."""
    with patch("app.api.talk.get_llm", return_value=make_fake_llm()) as mock:
        yield mock


@pytest.mark.asyncio
async def test_talk_streams_tokens(fake_llm_patch):
    """The endpoint concatenates all fake chunks and streams them back."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/npc/shopkeeper/talk",
            json={"player_id": "p1", "message": "hello", "location": "shop"},
        )

    assert response.status_code == 200
    assert response.text == "".join(FAKE_TOKENS)


@pytest.mark.asyncio
async def test_talk_sends_persona_as_system_message(fake_llm_patch):
    """The persona markdown must be the first (System) message passed to the LLM."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post(
            "/npc/shopkeeper/talk",
            json={"player_id": "p1", "message": "hello"},
        )

    messages = _fake_astream.last_messages
    assert len(messages) == 2

    from langchain_core.messages import HumanMessage, SystemMessage

    system_msg = messages[0]
    human_msg = messages[1]

    assert isinstance(system_msg, SystemMessage)
    assert isinstance(human_msg, HumanMessage)

    # The persona file exists, so system content must be non-empty and contain the NPC name
    assert "Mira" in system_msg.content or "shopkeeper" in system_msg.content.lower()
    assert human_msg.content == "hello"


@pytest.mark.asyncio
async def test_talk_missing_persona_returns_404():
    """A non-existent NPC id must yield a 404, no LLM call needed."""
    with patch("app.api.talk.get_llm", return_value=make_fake_llm()):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/npc/unknown_npc/talk",
                json={"player_id": "p1", "message": "hello"},
            )

    assert response.status_code == 404
