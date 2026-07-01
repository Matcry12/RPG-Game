"""S3 recall test — retrieved episodic memories are injected into the persona prompt.

Unified agent (ADR-0009): recall happens in ``retrieve_context`` and is folded into the
single agent prompt. We configure the ``chroma`` fixture to return one known memory and
assert it lands in the system message the agent receives.
"""

from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient
from langchain_core.messages import SystemMessage

from app.main import app

from .conftest import make_scripted_chat

REPLY = "Aye, I remember."


@pytest.mark.asyncio
async def test_recalled_memory_is_injected_into_persona_prompt(chroma):
    """Episodic recall from Chroma must appear in the SystemMessage sent to the agent."""
    KNOWN_MEMORY = "You agreed to start the lost_locket quest with the player."

    chroma.count.return_value = 1
    chroma.query.return_value = {
        "documents": [[KNOWN_MEMORY]],
        "metadatas": [[{"importance": 8, "timestamp": "t0"}]],
        "distances": [[0.1]],
    }

    sink: list = []
    with patch("app.graph.nodes.get_agent_llm", return_value=make_scripted_chat([REPLY], sink)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/npc/shopkeeper/talk",
                json={"player_id": "p1", "message": "do you remember our deal?"},
            )
            assert resp.status_code == 200

    assert sink, "agent was never called"
    system = next((m for m in sink[0] if isinstance(m, SystemMessage)), None)
    assert system is not None
    assert "Things you remember" in system.content
    assert "You agreed to start the lost_locket quest" in system.content
