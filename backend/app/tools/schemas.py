"""Pydantic schemas for NPC tool calls.

These are the shapes the LLM may *propose*; the gate layer decides whether to *dispose* them.
"""

from pydantic import BaseModel, Field


class UpdateDisposition(BaseModel):
    """Adjust how the NPC feels about the player; positive = warmer, negative = colder."""

    delta: int = Field(
        description=(
            "How much to shift disposition toward (+) or away from (-) the player. "
            "Bounded to [-10, 10] by the gate regardless of the value proposed here."
        )
    )
