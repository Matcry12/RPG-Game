"""Pydantic schemas for NPC tool calls.

These are the shapes the LLM may *propose*; the gate layer decides whether to *dispose* them.
"""

from pydantic import BaseModel, Field, field_validator


class UpdateDisposition(BaseModel):
    """Adjust how the NPC feels about the player; positive = warmer, negative = colder."""

    # `int | str`, NOT plain `int`: Groq/Llama frequently serializes the number as a
    # STRING ("-8"), and Groq validates tool-arg *types* server-side — an int-only schema
    # 400s (`tool_use_failed`) ~55% of the time (measured 2026-06-24). Declaring int|str
    # makes the JSON schema `anyOf[integer,string]` so Groq accepts the string; the
    # validator coerces it back to int here, dropping that failure rate to 0.
    delta: int | str = Field(
        description=(
            "How much to shift disposition toward (+) or away from (-) the player. "
            "Bounded to [-10, 10] by the gate regardless of the value proposed here."
        )
    )

    @field_validator("delta")
    @classmethod
    def _coerce_delta(cls, v: int | str) -> int:
        return int(v)  # "-8" -> -8 ; real ints pass through. Bad strings raise -> gate skipped.


class GiveReward(BaseModel):
    """NPC grants an item reward for a completed quest."""

    quest_id: str = Field(description="ID of the completed quest the reward is for.")
    item_id: str = Field(description="ID of the item to grant the player.")
    reason: str = Field(description="Brief in-world reason the NPC is giving this reward.")


class SetQuestState(BaseModel):
    """Start or abandon a quest for the player."""

    quest_id: str = Field(description="ID of the quest to update.")
    state: str = Field(description="Target state: 'active' to start the quest, 'abandoned' to stop it.")
