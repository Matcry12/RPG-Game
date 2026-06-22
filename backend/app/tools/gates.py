"""Gate layer — the safety boundary between LLM proposals and SQLite truth.

The gate is a pure function over (proposed_call, ids, conn, now) → GateResult.
It never trusts the model: clamping and precondition checks here are non-negotiable.

The public entry point is validate(call, npc_id, player_id, conn, *, now) which dispatches
by call type.  Per-tool validators are kept as module-level functions for direct testing.
"""

import sqlite3

from pydantic import BaseModel

from app.memory.sqlite_store import (
    apply_disposition_delta,
    get_quest_state,
    grant_reward,
    is_reward_claimed,
    set_quest_state,
)
from app.tools.schemas import GiveReward, StartQuest, UpdateDisposition

# The model may propose any integer; we enforce this range unconditionally.
DISPOSITION_CLAMP: tuple[int, int] = (-10, 10)


class GateResult(BaseModel):
    accepted: bool
    reason: str
    clamped_delta: int | None = None
    new_score: int | None = None
    granted_item: str | None = None
    quest_id: str | None = None


def validate_update_disposition(
    call: UpdateDisposition,
    npc_id: str,
    player_id: str,
    conn: sqlite3.Connection,
    *,
    now: str,
) -> GateResult:
    """Clamp delta, persist the change, and return a GateResult.

    This is the line that enforces "the LLM never owns truth": an absurd -999 becomes -10.
    The gate always accepts UpdateDisposition (the reject path is introduced in S2 for tools
    that have preconditions, e.g. GiveReward requiring a completed quest).
    """
    lo, hi = DISPOSITION_CLAMP
    clamped = max(lo, min(hi, call.delta))
    was_clamped = clamped != call.delta

    new_score = apply_disposition_delta(conn, npc_id, player_id, clamped, now)

    reason = "applied (clamped)" if was_clamped else "applied"
    return GateResult(
        accepted=True,
        reason=reason,
        clamped_delta=clamped,
        new_score=new_score,
    )


def validate_start_quest(
    call: StartQuest,
    npc_id: str,
    player_id: str,
    conn: sqlite3.Connection,
    *,
    now: str,
) -> GateResult:
    """Accept only if the quest exists and is in 'not_started' state."""
    state = get_quest_state(conn, call.quest_id, player_id)
    if state != "not_started":
        current = f"state={state!r}" if state is not None else "quest not found"
        return GateResult(
            accepted=False,
            reason=f"quest cannot be started ({current})",
            quest_id=call.quest_id,
        )
    set_quest_state(conn, call.quest_id, player_id, "active")
    return GateResult(accepted=True, reason="quest started", quest_id=call.quest_id)


def validate_give_reward(
    call: GiveReward,
    npc_id: str,
    player_id: str,
    conn: sqlite3.Connection,
    *,
    now: str,
) -> GateResult:
    """Accept only when quest is complete and reward hasn't been claimed yet."""
    state = get_quest_state(conn, call.quest_id, player_id)
    if state != "complete":
        current = f"state={state!r}" if state is not None else "quest not found"
        return GateResult(
            accepted=False,
            reason=f"quest not complete ({current})",
            quest_id=call.quest_id,
        )
    if is_reward_claimed(conn, player_id, call.quest_id):
        return GateResult(
            accepted=False,
            reason="reward already claimed",
            quest_id=call.quest_id,
        )
    grant_reward(conn, player_id, call.item_id, call.quest_id, now)
    return GateResult(
        accepted=True,
        reason="reward granted",
        granted_item=call.item_id,
        quest_id=call.quest_id,
    )


def validate(
    call: UpdateDisposition | StartQuest | GiveReward,
    npc_id: str,
    player_id: str,
    conn: sqlite3.Connection,
    *,
    now: str,
) -> GateResult:
    """Dispatch to the correct per-tool validator.

    This is the single public entry point callers should use.
    Raises TypeError for unknown call types.
    """
    if isinstance(call, UpdateDisposition):
        return validate_update_disposition(call, npc_id, player_id, conn, now=now)
    if isinstance(call, StartQuest):
        return validate_start_quest(call, npc_id, player_id, conn, now=now)
    if isinstance(call, GiveReward):
        return validate_give_reward(call, npc_id, player_id, conn, now=now)
    raise TypeError(f"Unknown tool call type: {type(call)!r}")
