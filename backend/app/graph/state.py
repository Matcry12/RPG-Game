"""Typed LangGraph state for one ``/talk`` turn (S4).

``history`` is the **durable** conversation thread — Human turns + the final persona
reply — merged via ``add_messages`` and persisted by the SQLite checkpointer. This is
what survives a server restart (the S4 headline): reconnecting as the same
``(npc_id, player_id)`` thread resumes with full conversational context.

Every other field is **per-turn scratch**: it is passed fresh in the graph input each
turn and overwritten (no reducer), so the prose-free tool-loop never leaks across turns
or feeds prior prose back into a tool-decision call (ADR-0007).
"""

from typing import Annotated, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


class TurnState(TypedDict, total=False):
    # --- Durable, checkpointed conversation thread (Human + final persona AI). ---
    history: Annotated[list[BaseMessage], add_messages]

    # --- Per-turn inputs (overwritten each turn from the request). ---
    npc_id: str
    player_id: str
    message: str
    persona_text: str

    # --- Per-turn derived context (retrieve_context node). ---
    current_score: int
    recalled: list[dict]
    memory_block: str
    lore_block: str
    grounded: bool

    # --- Tool-loop scratch (prose-free; overwritten, NOT reduced). ---
    # Working messages for the agent⇄tools cycle: the routing turn is rebuilt each
    # agent call, so we persist only the (AIMessage tool-call, ToolMessage) pairs here.
    loop_messages: list[BaseMessage]
    agent_turns: int
    # Every accepted gate result this turn (GateResult.model_dump()), for episodic writes.
    gate_results: list[dict]

    # --- Final reply text (set by the agent's reply turn), for the episodic write. ---
    reply: str
