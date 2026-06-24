"""S1 integration tests — propose/dispose loop end-to-end, no live LLM.

Unified agent (ADR-0009): the single ``agent`` node proposes tools and writes the reply.
Seam is ``app.graph.nodes.get_agent_llm``; the scripted fake plays a tool-call turn then a
reply turn. The propose/dispose safety contract is unchanged: the gate is the only writer of
SQLite truth, and a malformed/refused proposal never 500s.
"""

import groq
import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app

from .conftest import make_scripted_chat, tool_turn

REPLY = "How dare you, traveller."


@pytest.mark.asyncio
async def test_talk_updates_disposition_and_state_reflects_it(chroma):
    """POST /talk: agent calls UpdateDisposition(-5) → gate persists -5 → /state returns -5."""
    llm = make_scripted_chat([tool_turn("UpdateDisposition", {"delta": -5}), REPLY])

    with patch_agent(llm):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            talk_resp = await client.post(
                "/npc/shopkeeper/talk",
                json={"player_id": "p1", "message": "you swindling crook", "location": "shop"},
            )
            assert talk_resp.status_code == 200
            assert talk_resp.text.strip() == REPLY

            state_resp = await client.get("/npc/shopkeeper/state?player_id=p1")
            assert state_resp.status_code == 200

    data = state_resp.json()
    assert data["disposition"] == -5


@pytest.mark.asyncio
async def test_talk_no_tool_call_leaves_disposition_at_zero(chroma):
    """If the agent proposes no tool call, disposition stays at 0."""
    with patch_agent(make_scripted_chat([REPLY])):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/npc/shopkeeper/talk", json={"player_id": "p1", "message": "hello"})
            state_resp = await client.get("/npc/shopkeeper/state?player_id=p1")

    assert state_resp.json()["disposition"] == 0


@pytest.mark.asyncio
async def test_talk_malformed_tool_call_returns_200_and_leaves_disposition_unchanged(chroma):
    """Malformed tool-call args must not 500 — gate skipped, agent still replies, SQLite untouched."""
    llm = make_scripted_chat([tool_turn("UpdateDisposition", {"wrong_field": "garbage"}), REPLY])

    with patch_agent(llm):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            talk_resp = await client.post(
                "/npc/shopkeeper/talk",
                json={"player_id": "p1", "message": "do your worst"},
            )
            assert talk_resp.status_code == 200
            assert talk_resp.text.strip() == REPLY
            state_resp = await client.get("/npc/shopkeeper/state?player_id=p1")

    assert state_resp.json()["disposition"] == 0


@pytest.mark.asyncio
async def test_groq_bad_request_error_returns_200_and_leaves_disposition_unchanged(chroma):
    """A groq.BadRequestError on the tools-bound turn must degrade to a tool-free reply, not 500."""
    bad_request = groq.BadRequestError(
        message="tool_use_failed", response=__import__("unittest.mock", fromlist=["MagicMock"]).MagicMock(),
        body={"error": {"code": "tool_use_failed"}},
    )
    reply_fake = make_scripted_chat([REPLY])

    def factory(*, with_tools=True):
        return _RaisingChat(bad_request) if with_tools else reply_fake

    with patch("app.graph.nodes.get_agent_llm", side_effect=factory):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            talk_resp = await client.post(
                "/npc/shopkeeper/talk",
                json={"player_id": "p1", "message": "you thieving wretch"},
            )
            assert talk_resp.status_code == 200
            assert talk_resp.text.strip() == REPLY
            state_resp = await client.get("/npc/shopkeeper/state?player_id=p1")

    assert state_resp.json()["disposition"] == 0


@pytest.mark.asyncio
async def test_agent_that_always_calls_tools_terminates(chroma):
    """Review HIGH-1: even if the model emits a tool call on EVERY turn, the loop must end.

    The cap forces a tool-free reply turn at MAX_AGENT_TURNS=3, so exactly 3 gate rounds run
    (delta -1 each → -3) and the turn terminates (no recursion runaway). With no prose ever
    produced, the endpoint emits the '...' fallback.
    """
    from app.graph.nodes import MAX_AGENT_TURNS

    # A single tool turn that repeats forever (make_scripted_chat repeats the last turn).
    llm = make_scripted_chat([tool_turn("UpdateDisposition", {"delta": -1})])

    with patch_agent(llm):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            talk_resp = await client.post(
                "/npc/shopkeeper/talk",
                json={"player_id": "p1", "message": "grrr"},
            )
            assert talk_resp.status_code == 200
            assert talk_resp.text == "..."  # no prose ever produced → fallback
            state_resp = await client.get("/npc/shopkeeper/state?player_id=p1")

    # Exactly MAX_AGENT_TURNS gate rounds ran, each delta -1.
    assert state_resp.json()["disposition"] == -MAX_AGENT_TURNS


@pytest.mark.asyncio
async def test_agent_prompt_is_persona_plus_tool_guidance(chroma):
    """Unified agent: the single prompt must carry the persona AND the tool guidance.

    (Replaces the old 'routing prompt is not persona' test — unification is the point now.)
    """
    sink: list = []
    with patch_agent(make_scripted_chat([REPLY], sink)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/npc/shopkeeper/talk", json={"player_id": "p1", "message": "greetings"})

    assert sink, "agent was never called"
    system = sink[0][0].content
    assert "Mira" in system or "shopkeeper" in system.lower()  # persona present
    assert "CALL THE TOOL" in system  # tool guidance present


@pytest.mark.asyncio
async def test_tools_disabled_skips_tools_and_guidance(chroma, monkeypatch):
    """tools_enabled=False: no tool guidance in the prompt, agent just replies, disposition 0."""
    monkeypatch.setattr("app.config.settings.tools_enabled", False)
    sink: list = []

    with patch_agent(make_scripted_chat([REPLY], sink)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            talk_resp = await client.post(
                "/npc/shopkeeper/talk",
                json={"player_id": "p1", "message": "hello stranger"},
            )
            assert talk_resp.status_code == 200
            assert talk_resp.text.strip() == REPLY
            state_resp = await client.get("/npc/shopkeeper/state?player_id=p1")

    assert state_resp.json()["disposition"] == 0
    assert "CALL THE TOOL" not in sink[0][0].content  # guidance omitted when tools off


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

from unittest.mock import patch  # noqa: E402

from langchain_core.language_models.chat_models import BaseChatModel  # noqa: E402


def patch_agent(llm):
    return patch("app.graph.nodes.get_agent_llm", return_value=llm)


class _RaisingChat(BaseChatModel):
    """A fake whose astream raises the given exception (to simulate Groq 400 on the tool turn)."""

    exc: object = None

    def __init__(self, exc):
        super().__init__()
        object.__setattr__(self, "exc", exc)

    @property
    def _llm_type(self) -> str:
        return "raising-fake"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        raise self.exc

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
        raise self.exc
        yield  # pragma: no cover  (makes this an async generator)
