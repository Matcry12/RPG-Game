"""Gate layer — the safety boundary between LLM proposals and SQLite truth.

The gate is a pure function over (proposed_call, ids, conn, now) → GateResult.
It never trusts the model: clamping here is non-negotiable.

S2 will generalise this into a dispatch validate(call, db) → GateResult with per-tool
validators; for S1 we have exactly one tool so no dispatch is needed yet.
"""

import sqlite3

from pydantic import BaseModel

from app.memory.sqlite_store import apply_disposition_delta
from app.tools.schemas import UpdateDisposition

# The model may propose any integer; we enforce this range unconditionally.
DISPOSITION_CLAMP: tuple[int, int] = (-10, 10)


class GateResult(BaseModel):
    accepted: bool
    reason: str
    clamped_delta: int | None = None
    new_score: int | None = None


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
