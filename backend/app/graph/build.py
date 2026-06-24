"""Assemble and cache the compiled ``/talk`` StateGraph (S4).

The graph wires the S0–S3 functions as nodes (ADR-0005) with a persistent SQLite
checkpointer (``AsyncSqliteSaver``) so conversation state survives a server restart —
the S4 headline. The compiled graph + its checkpointer connection are built once and
cached for the app lifetime; ``reset_graph`` exists so tests can rebuild against a
temp checkpoint DB.
"""

import asyncio

import aiosqlite
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph

from app.config import settings

from .nodes import (
    agent,
    retrieve_context,
    route_after_agent,
    tools,
    write_memory,
)
from .state import TurnState


def build_graph(checkpointer):
    """Compile the turn StateGraph with the given checkpointer.

    Topology (ADR-0009 — unified agent):
        START → retrieve_context → agent
        agent → {tools | write_memory}   (loop via gate; cap enforced inside agent)
        tools → agent
        write_memory → END

    The single ``agent`` node both proposes tool calls and writes the in-character reply;
    there is no separate render node. The iteration cap lives inside ``agent`` (it drops
    tools on the overflow turn), so the loop always terminates on a reply.
    """
    builder = StateGraph(TurnState)
    builder.add_node("retrieve_context", retrieve_context)
    builder.add_node("agent", agent)
    builder.add_node("tools", tools)
    builder.add_node("write_memory", write_memory)

    builder.add_edge(START, "retrieve_context")
    builder.add_edge("retrieve_context", "agent")
    builder.add_conditional_edges(
        "agent",
        route_after_agent,
        {"tools": "tools", "write_memory": "write_memory"},
    )
    builder.add_edge("tools", "agent")
    builder.add_edge("write_memory", END)

    return builder.compile(checkpointer=checkpointer)


# --- Lifetime-cached compiled graph + its checkpointer connection. ---
_graph = None
_conn: aiosqlite.Connection | None = None
_lock = asyncio.Lock()


async def get_graph():
    """Return the compiled graph, building it (and its checkpointer) once.

    The ``AsyncSqliteSaver`` is backed by a file-based aiosqlite connection that lives
    for the whole app lifetime (not a short ``async with`` block), so the checkpoint DB
    persists across requests and restarts.
    """
    global _graph, _conn
    if _graph is None:
        async with _lock:
            if _graph is None:
                _conn = await aiosqlite.connect(str(settings.checkpoint_path))
                saver = AsyncSqliteSaver(_conn)
                await saver.setup()
                _graph = build_graph(saver)
    return _graph


async def reset_graph() -> None:
    """Drop the cached graph and close its checkpointer connection (test seam)."""
    global _graph, _conn
    _graph = None
    if _conn is not None:
        await _conn.close()
        _conn = None
