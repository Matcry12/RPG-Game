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


class GiveReward(BaseModel):
    """NPC grants an item reward for a completed quest."""

    quest_id: str = Field(description="ID of the completed quest the reward is for.")
    item_id: str = Field(description="ID of the item to grant the player.")
    reason: str = Field(description="Brief in-world reason the NPC is giving this reward.")


class StartQuest(BaseModel):
    """Begin a quest the player hasn't started."""

    quest_id: str = Field(description="ID of the quest to begin for the player.")
