"""S3 recall test — verify that retrieved episodic memories are injected into the persona prompt.

The headline of S3 is "next turn the NPC references a past event unprompted."
This test asserts the recalled memory sentence actually lands in the SystemMessage
that gets passed to the generate-reply LLM, fully offline (no Groq, no real Chroma,
no model download).
"""

from typing import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from langchain_core.messages import AIMessageChunk, SystemMessage

from app.main import app


# ---------------------------------------------------------------------------
# Helpers (mirrors test_talk_state_s1.py style)
# ---------------------------------------------------------------------------

REPLY_TOKENS = ["Aye,", " I", " remember."]


async def _fake_astream(messages) -> AsyncIterator[AIMessageChunk]:
    # Capture the messages list in the outer scope via the closure set up per-test.
    yield AIMessageChunk(content="".join(REPLY_TOKENS))


def _make_no_tool_response():
    msg = MagicMock()
    msg.tool_calls = []
    return msg


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """Point settings.db_path at a fresh temp file."""
    from app.config import settings as real_settings

    db_file = tmp_path / "test_npc.db"

    class _S:
        groq_api_key = real_settings.groq_api_key
        groq_model = real_settings.groq_model
        persona_dir = real_settings.persona_dir
        db_path = db_file
        chroma_path = real_settings.chroma_path
        tools_enabled = True

    s = _S()
    monkeypatch.setattr("app.config.settings", s)
    monkeypatch.setattr("app.api.talk.settings", s)
    monkeypatch.setattr("app.api.state.settings", s)
    monkeypatch.setattr("app.main.settings", s)
    return db_file


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_recalled_memory_is_injected_into_persona_prompt(tmp_db):
    """Episodic recall from Chroma must appear in the SystemMessage sent to the generate LLM.

    Setup:
    - Patch get_episodic_collection to return a fake collection whose count()=1 and
      query() returns one known memory sentence.
    - Capture messages passed to llm.astream() to assert the persona SystemMessage
      contains both the memory sentence and the "Things you remember" header.
    - Mock get_tool_llm() to return no tool call (propose step is a no-op here).
    """
    KNOWN_MEMORY = "You agreed to start the lost_locket quest with the player."

    # Fake Chroma collection: non-empty so retrieve_episodic proceeds past the count guard,
    # and query() returns our known memory sentence.
    fake_collection = MagicMock()
    fake_collection.count.return_value = 1
    fake_collection.query.return_value = {
        "documents": [[KNOWN_MEMORY]],
        "metadatas": [[{"importance": 8, "timestamp": "t0"}]],
    }
    fake_client = MagicMock()

    # Capture the messages list passed to llm.astream()
    captured_persona_messages: list = []

    async def _capturing_astream(messages) -> AsyncIterator[AIMessageChunk]:
        captured_persona_messages.extend(messages)
        for token in REPLY_TOKENS:
            yield AIMessageChunk(content=token)

    fake_llm = MagicMock()
    fake_llm.astream = _capturing_astream

    fake_tool_llm = MagicMock()
    fake_tool_llm.ainvoke = AsyncMock(return_value=_make_no_tool_response())

    with (
        patch("app.api.talk.get_client", return_value=fake_client),
        patch("app.api.talk.get_episodic_collection", return_value=fake_collection),
        patch("app.api.talk.get_llm", return_value=fake_llm),
        patch("app.api.talk.get_tool_llm", return_value=fake_tool_llm),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/npc/shopkeeper/talk",
                json={"player_id": "p1", "message": "do you remember our deal?"},
            )
            assert resp.status_code == 200

    # The first message in the generate call must be the persona SystemMessage.
    assert captured_persona_messages, "llm.astream was never called with messages"
    persona_system = next(
        (m for m in captured_persona_messages if isinstance(m, SystemMessage)),
        None,
    )
    assert persona_system is not None, "No SystemMessage found in persona messages"

    content = persona_system.content
    assert "Things you remember" in content, (
        f"Expected 'Things you remember' header in persona system message, got:\n{content}"
    )
    assert "You agreed to start the lost_locket quest" in content, (
        f"Expected known memory sentence in persona system message, got:\n{content}"
    )
