"""S1 integration test — propose/dispose loop end-to-end, no live LLM.

Mocks:
  - get_tool_llm() → fake whose .ainvoke() returns a response with one UpdateDisposition tool_call
  - get_llm()      → fake whose .astream() yields reply tokens

Uses a temp-file SQLite DB (monkeypatched via settings.db_path) so it never touches npc.db.
"""

import sqlite3
import tempfile
from pathlib import Path
from typing import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from langchain_core.messages import AIMessageChunk

from app.main import app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_tool_response(delta: int):
    """Return a fake AIMessage with one UpdateDisposition tool_call."""
    msg = MagicMock()
    msg.tool_calls = [
        {
            "name": "UpdateDisposition",
            "args": {"delta": delta},
            "id": "call_fake_001",
        }
    ]
    return msg


REPLY_TOKENS = ["Very ", "well,", " traveller."]


async def _fake_astream(messages) -> AsyncIterator[AIMessageChunk]:
    for token in REPLY_TOKENS:
        yield AIMessageChunk(content=token)


def _make_stream_llm():
    llm = MagicMock()
    llm.astream = _fake_astream
    return llm


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """Point settings.db_path at a fresh temp file; return its Path."""
    db_file = tmp_path / "test_npc.db"
    monkeypatch.setattr("app.config.settings.db_path", db_file)
    # Also patch it in every module that imported settings before the monkeypatch
    monkeypatch.setattr("app.api.talk.settings", _patched_settings(db_file))
    monkeypatch.setattr("app.api.state.settings", _patched_settings(db_file))
    monkeypatch.setattr("app.main.settings", _patched_settings(db_file))
    return db_file


def _patched_settings(db_file: Path, *, disposition_tool_enabled: bool = True):
    """Return a copy of settings with db_path (and optional flag) overridden."""
    from app.config import settings as real_settings

    class _S:
        groq_api_key = real_settings.groq_api_key
        groq_model = real_settings.groq_model
        persona_dir = real_settings.persona_dir
        db_path = db_file
        disposition_tool_enabled = True  # default; callers may override on the instance

    s = _S()
    s.disposition_tool_enabled = disposition_tool_enabled
    return s


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_talk_updates_disposition_and_state_reflects_it(tmp_db):
    """POST /talk (delta=-5) → disposition becomes -5 → GET /state returns -5."""
    fake_tool_llm = MagicMock()
    fake_tool_llm.ainvoke = AsyncMock(return_value=_make_tool_response(delta=-5))

    with (
        patch("app.api.talk.get_tool_llm", return_value=fake_tool_llm),
        patch("app.api.talk.get_llm", return_value=_make_stream_llm()),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            talk_resp = await client.post(
                "/npc/shopkeeper/talk",
                json={"player_id": "p1", "message": "you swindling crook", "location": "shop"},
            )
            assert talk_resp.status_code == 200
            assert talk_resp.text == "".join(REPLY_TOKENS)

            state_resp = await client.get("/npc/shopkeeper/state?player_id=p1")
            assert state_resp.status_code == 200

    data = state_resp.json()
    assert data["npc_id"] == "shopkeeper"
    assert data["player_id"] == "p1"
    assert data["disposition"] == -5


@pytest.mark.asyncio
async def test_talk_no_tool_call_leaves_disposition_at_zero(tmp_db):
    """If the LLM proposes no tool call, disposition stays at 0."""
    no_tool_msg = MagicMock()
    no_tool_msg.tool_calls = []

    fake_tool_llm = MagicMock()
    fake_tool_llm.ainvoke = AsyncMock(return_value=no_tool_msg)

    with (
        patch("app.api.talk.get_tool_llm", return_value=fake_tool_llm),
        patch("app.api.talk.get_llm", return_value=_make_stream_llm()),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post(
                "/npc/shopkeeper/talk",
                json={"player_id": "p1", "message": "hello"},
            )
            state_resp = await client.get("/npc/shopkeeper/state?player_id=p1")

    assert state_resp.json()["disposition"] == 0


@pytest.mark.asyncio
async def test_talk_malformed_tool_call_returns_200_and_leaves_disposition_unchanged(tmp_db):
    """Malformed tool-call args must not 500 — turn completes and SQLite is untouched."""
    malformed_msg = MagicMock()
    malformed_msg.tool_calls = [
        {
            "name": "UpdateDisposition",
            "args": {"wrong_field": "garbage"},
            "id": "call_fake_bad",
        }
    ]

    fake_tool_llm = MagicMock()
    fake_tool_llm.ainvoke = AsyncMock(return_value=malformed_msg)

    with (
        patch("app.api.talk.get_tool_llm", return_value=fake_tool_llm),
        patch("app.api.talk.get_llm", return_value=_make_stream_llm()),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            talk_resp = await client.post(
                "/npc/shopkeeper/talk",
                json={"player_id": "p1", "message": "do your worst"},
            )
            # (a) must not 500 — the turn still streams the reply
            assert talk_resp.status_code == 200
            assert talk_resp.text == "".join(REPLY_TOKENS)

            state_resp = await client.get("/npc/shopkeeper/state?player_id=p1")

    # (b) disposition must be untouched — no SQLite write on parse failure
    assert state_resp.json()["disposition"] == 0


@pytest.mark.asyncio
async def test_groq_bad_request_error_returns_200_and_leaves_disposition_unchanged(tmp_db):
    """Part B regression: groq.BadRequestError at ainvoke boundary must NOT 500.

    Simulates Groq rejecting the tool-proposal call (e.g. tool_use_failed 400).
    The turn must complete normally with the reply streamed and disposition untouched.
    """
    from unittest.mock import MagicMock

    import groq

    fake_response = MagicMock()  # groq.BadRequestError needs an httpx.Response-like object
    bad_request_error = groq.BadRequestError(
        message="tool_use_failed",
        response=fake_response,
        body={"error": {"code": "tool_use_failed"}},
    )

    fake_tool_llm = MagicMock()
    fake_tool_llm.ainvoke = AsyncMock(side_effect=bad_request_error)

    with (
        patch("app.api.talk.get_tool_llm", return_value=fake_tool_llm),
        patch("app.api.talk.get_llm", return_value=_make_stream_llm()),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            talk_resp = await client.post(
                "/npc/shopkeeper/talk",
                json={"player_id": "p1", "message": "you thieving wretch"},
            )
            # (a) must return 200 and stream the reply — no 500
            assert talk_resp.status_code == 200
            assert talk_resp.text == "".join(REPLY_TOKENS)

            state_resp = await client.get("/npc/shopkeeper/state?player_id=p1")

    # (b) disposition must be unchanged — failed proposal treated as "no tool"
    assert state_resp.json()["disposition"] == 0


@pytest.mark.asyncio
async def test_propose_uses_tool_routing_prompt_not_persona(tmp_db):
    """Part A: the tool-proposal call must receive the terse routing prompt, not the persona text.

    Locks in the separation between the tool-routing system message and the prose system message.
    """
    captured_propose_messages = []

    async def _capture_ainvoke(messages):
        captured_propose_messages.extend(messages)
        # Return a no-tool response so the rest of the turn completes normally
        msg = MagicMock()
        msg.tool_calls = []
        return msg

    fake_tool_llm = MagicMock()
    fake_tool_llm.ainvoke = _capture_ainvoke

    with (
        patch("app.api.talk.get_tool_llm", return_value=fake_tool_llm),
        patch("app.api.talk.get_llm", return_value=_make_stream_llm()),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post(
                "/npc/shopkeeper/talk",
                json={"player_id": "p1", "message": "greetings"},
            )

    # The first message in the propose call must be the routing system prompt
    assert captured_propose_messages, "ainvoke was never called with messages"
    propose_system = captured_propose_messages[0].content
    # Must contain routing instructions
    assert "disposition-control" in propose_system
    assert "Do NOT write any dialogue" in propose_system
    # Must NOT contain persona text (shopkeeper persona contains "Mira" or "Thistlewick")
    assert "Mira" not in propose_system
    assert "Thistlewick" not in propose_system


@pytest.mark.asyncio
async def test_disposition_tool_disabled_flag_skips_propose(tmp_db, monkeypatch):
    """When disposition_tool_enabled=False, the propose path is skipped entirely.

    Assertions:
      (a) /talk returns 200 and streams the reply normally.
      (b) disposition in SQLite is unchanged (still 0) — no tool ran.
      (c) get_tool_llm is never called (propose path was skipped).
    """
    # Build a settings object with the flag off and the same temp DB.
    disabled_settings = _patched_settings(tmp_db, disposition_tool_enabled=False)

    # Patch settings in all modules that imported it before this test.
    monkeypatch.setattr("app.api.talk.settings", disabled_settings)
    monkeypatch.setattr("app.api.state.settings", disabled_settings)
    monkeypatch.setattr("app.main.settings", disabled_settings)
    monkeypatch.setattr("app.config.settings", disabled_settings)

    with (
        patch("app.api.talk.get_tool_llm") as mock_get_tool_llm,
        patch("app.api.talk.get_llm", return_value=_make_stream_llm()),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            talk_resp = await client.post(
                "/npc/shopkeeper/talk",
                json={"player_id": "p1", "message": "hello stranger"},
            )
            # (a) endpoint must still return 200 and stream the reply
            assert talk_resp.status_code == 200
            assert talk_resp.text == "".join(REPLY_TOKENS)

            state_resp = await client.get("/npc/shopkeeper/state?player_id=p1")

    # (b) disposition must be untouched — no tool call ran
    assert state_resp.json()["disposition"] == 0

    # (c) get_tool_llm must not have been called — propose block was skipped
    mock_get_tool_llm.assert_not_called()
