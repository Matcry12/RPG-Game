"""Shared test harness for the unified-agent S4 graph (ADR-0009).

One ``agent`` node now both proposes tool calls and streams the in-character reply, so a
fake must be able to do BOTH across turns: stream prose AND carry tool calls. The stock
``GenericFakeChatModel`` can't (no tool_calls; can't stream an empty-content turn), so
``make_scripted_chat`` is a real ``BaseChatModel`` that scripts a sequence of turns —
each either a reply string (streamed) or a list of tool-call dicts (streamed as a tool_call
chunk with empty prose). It also records the messages it received for prompt assertions.
"""

import json

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult


def tool_turn(name: str, args: dict, call_id: str = "c1") -> list[dict]:
    """A scripted tool-call turn: one tool call."""
    return [{"name": name, "args": args, "id": call_id}]


def make_scripted_chat(turns, sink: list | None = None):
    """A real streaming chat model that plays one scripted turn per call.

    Each turn is either:
      - a ``str`` -> streamed word-by-word as the reply content, or
      - a ``list[dict]`` of tool calls ({'name','args','id'}) -> one tool_call chunk, no prose.
    The last turn repeats if the agent loops further. ``sink`` (a list) captures each call's
    input messages for assertions. Real ``BaseChatModel`` -> ``astream_events`` emits the
    ``on_chat_model_stream`` events the endpoint forwards.
    """
    state = {"i": 0}
    script = list(turns)

    class _Scripted(BaseChatModel):
        @property
        def _llm_type(self) -> str:
            return "scripted-fake"

        def _next(self, messages):
            if sink is not None:
                sink.append(list(messages))
            turn = script[min(state["i"], len(script) - 1)]
            state["i"] += 1
            return turn

        def _stream(self, messages, stop=None, run_manager=None, **kwargs):
            turn = self._next(messages)
            if isinstance(turn, str):
                for word in turn.split(" "):
                    yield ChatGenerationChunk(message=AIMessageChunk(content=word + " "))
            else:
                tcs = [
                    {"name": c["name"], "args": json.dumps(c["args"]), "id": c.get("id", f"c{i}"), "index": i}
                    for i, c in enumerate(turn)
                ]
                yield ChatGenerationChunk(message=AIMessageChunk(content="", tool_call_chunks=tcs))

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            chunks = list(self._stream(messages, stop=stop, run_manager=run_manager, **kwargs))
            msg = chunks[0].message
            for c in chunks[1:]:
                msg = msg + c.message
            ai = AIMessage(content=msg.content, tool_calls=msg.tool_calls)
            return ChatResult(generations=[ChatGeneration(message=ai)])

    return _Scripted()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
async def graph_env(tmp_path, monkeypatch):
    """Isolate each test: temp SQLite/Chroma/checkpoint paths + a fresh graph singleton."""
    from app.graph import build

    monkeypatch.setattr("app.config.settings.db_path", tmp_path / "npc.db")
    monkeypatch.setattr("app.config.settings.chroma_path", tmp_path / "chroma")
    monkeypatch.setattr("app.config.settings.checkpoint_path", tmp_path / "ckpt.db")
    monkeypatch.setattr("app.config.settings.tools_enabled", True)

    await build.reset_graph()
    yield
    await build.reset_graph()


@pytest.fixture
def chroma():
    """Patch the episodic store seams to an offline MagicMock collection (empty by default)."""
    from unittest.mock import MagicMock, patch

    collection = MagicMock()
    collection.count.return_value = 0
    collection.query.return_value = {"documents": [[]], "metadatas": [[]], "distances": [[]]}

    with (
        patch("app.graph.nodes.get_client", return_value=MagicMock()),
        patch("app.graph.nodes.get_episodic_collection", return_value=collection),
    ):
        yield collection
