"""Unit tests for SetQuestState and GiveReward gates, plus the validate() dispatcher.

Pure functions, no LLM, in-memory SQLite only.
The gate is the safety boundary that ensures the LLM never owns truth.
"""

import sqlite3

import pytest

from app.memory.sqlite_store import (
    get_inventory,
    get_quest_state,
    grant_reward,
    init_db,
    is_reward_claimed,
    set_quest_state,
)
from app.tools.gates import (
    validate,
    validate_give_reward,
    validate_set_quest_state,
)
from app.tools.schemas import GiveReward, SetQuestState, UpdateDisposition

NOW = "2026-01-01T00:00:00+00:00"
NPC = "shopkeeper"
PLAYER = "p1"
QUEST = "herb_delivery"
ITEM = "silver_coin"


@pytest.fixture
def conn():
    """In-memory SQLite connection, schema initialised, torn down after each test."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    init_db(c)
    yield c
    c.close()


# ---------------------------------------------------------------------------
# SetQuestState — start (not_started → active)
# ---------------------------------------------------------------------------

def test_set_quest_active_from_not_started(conn):
    set_quest_state(conn, QUEST, PLAYER, "not_started")
    result = validate_set_quest_state(SetQuestState(quest_id=QUEST, state="active"), NPC, PLAYER, conn, now=NOW)
    assert result.accepted is True
    assert result.quest_id == QUEST
    assert get_quest_state(conn, QUEST, PLAYER) == "active"


def test_set_quest_active_already_active_rejected(conn):
    # seed: herb_delivery is already 'active'
    result = validate_set_quest_state(SetQuestState(quest_id=QUEST, state="active"), NPC, PLAYER, conn, now=NOW)
    assert result.accepted is False
    assert "active" in result.reason
    assert get_quest_state(conn, QUEST, PLAYER) == "active"


def test_set_quest_active_from_complete_rejected(conn):
    set_quest_state(conn, QUEST, PLAYER, "complete")
    result = validate_set_quest_state(SetQuestState(quest_id=QUEST, state="active"), NPC, PLAYER, conn, now=NOW)
    assert result.accepted is False


def test_set_quest_active_missing_quest_rejected(conn):
    result = validate_set_quest_state(SetQuestState(quest_id="no_such_quest", state="active"), NPC, PLAYER, conn, now=NOW)
    assert result.accepted is False


# ---------------------------------------------------------------------------
# SetQuestState — abandon (active → abandoned)
# ---------------------------------------------------------------------------

def test_set_quest_abandoned_from_active(conn):
    # seed: herb_delivery is 'active'
    result = validate_set_quest_state(SetQuestState(quest_id=QUEST, state="abandoned"), NPC, PLAYER, conn, now=NOW)
    assert result.accepted is True
    assert get_quest_state(conn, QUEST, PLAYER) == "abandoned"


def test_set_quest_abandoned_from_not_started_rejected(conn):
    set_quest_state(conn, QUEST, PLAYER, "not_started")
    result = validate_set_quest_state(SetQuestState(quest_id=QUEST, state="abandoned"), NPC, PLAYER, conn, now=NOW)
    assert result.accepted is False


def test_set_quest_invalid_state_rejected(conn):
    result = validate_set_quest_state(SetQuestState(quest_id=QUEST, state="complete"), NPC, PLAYER, conn, now=NOW)
    assert result.accepted is False
    assert "invalid target state" in result.reason


# ---------------------------------------------------------------------------
# GiveReward
# ---------------------------------------------------------------------------

def test_give_reward_quest_not_complete_rejected(conn):
    """GiveReward when quest is active (not complete) → rejected, no inventory row, not claimed."""
    # herb_delivery is seeded as 'active'
    result = validate_give_reward(
        GiveReward(quest_id=QUEST, item_id=ITEM, reason="good work"),
        NPC, PLAYER, conn, now=NOW,
    )

    assert result.accepted is False
    assert "not complete" in result.reason
    assert get_inventory(conn, PLAYER) == []
    assert not is_reward_claimed(conn, PLAYER, QUEST)


def test_give_reward_quest_complete_accepted(conn):
    """GiveReward when quest complete and unclaimed → accepted, inventory +1, claim recorded."""
    set_quest_state(conn, QUEST, PLAYER, "complete")

    result = validate_give_reward(
        GiveReward(quest_id=QUEST, item_id=ITEM, reason="well done"),
        NPC, PLAYER, conn, now=NOW,
    )

    assert result.accepted is True
    assert result.granted_item == ITEM
    assert result.quest_id == QUEST
    inventory = get_inventory(conn, PLAYER)
    assert len(inventory) == 1
    assert inventory[0]["item_id"] == ITEM
    assert inventory[0]["qty"] == 1
    assert is_reward_claimed(conn, PLAYER, QUEST)


def test_give_reward_already_claimed_rejected(conn):
    """GiveReward second attempt after successful claim → rejected, inventory NOT incremented."""
    set_quest_state(conn, QUEST, PLAYER, "complete")

    # First claim succeeds
    first = validate_give_reward(
        GiveReward(quest_id=QUEST, item_id=ITEM, reason="first"),
        NPC, PLAYER, conn, now=NOW,
    )
    assert first.accepted is True

    # Second attempt is rejected
    second = validate_give_reward(
        GiveReward(quest_id=QUEST, item_id=ITEM, reason="again"),
        NPC, PLAYER, conn, now=NOW,
    )

    assert second.accepted is False
    assert "already claimed" in second.reason
    # Inventory must not have been incremented
    inventory = get_inventory(conn, PLAYER)
    assert inventory[0]["qty"] == 1


def test_give_reward_quest_not_found_rejected(conn):
    """GiveReward for a quest that has no row → rejected."""
    result = validate_give_reward(
        GiveReward(quest_id="no_such_quest", item_id=ITEM, reason="test"),
        NPC, PLAYER, conn, now=NOW,
    )

    assert result.accepted is False
    assert "not complete" in result.reason


# ---------------------------------------------------------------------------
# validate() dispatcher
# ---------------------------------------------------------------------------

def test_dispatcher_routes_update_disposition(conn):
    call = UpdateDisposition(delta=5)
    result = validate(call, NPC, PLAYER, conn, now=NOW)
    assert result.accepted is True
    assert result.new_score == 5


def test_dispatcher_routes_set_quest_state(conn):
    set_quest_state(conn, QUEST, PLAYER, "not_started")
    call = SetQuestState(quest_id=QUEST, state="active")
    result = validate(call, NPC, PLAYER, conn, now=NOW)
    assert result.accepted is True
    assert get_quest_state(conn, QUEST, PLAYER) == "active"


def test_dispatcher_routes_give_reward(conn):
    set_quest_state(conn, QUEST, PLAYER, "complete")
    call = GiveReward(quest_id=QUEST, item_id=ITEM, reason="dispatch test")
    result = validate(call, NPC, PLAYER, conn, now=NOW)
    assert result.accepted is True
    assert result.granted_item == ITEM


def test_dispatcher_raises_for_unknown_type(conn):
    with pytest.raises(TypeError, match="Unknown tool call type"):
        validate("not_a_call", NPC, PLAYER, conn, now=NOW)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Atomic grant_reward
# ---------------------------------------------------------------------------

def test_grant_reward_records_claim_and_increments_inventory(conn):
    """grant_reward in one transaction: claim recorded AND inventory +1."""
    grant_reward(conn, PLAYER, ITEM, QUEST, NOW)

    assert is_reward_claimed(conn, PLAYER, QUEST)
    inventory = get_inventory(conn, PLAYER)
    assert len(inventory) == 1
    assert inventory[0]["item_id"] == ITEM
    assert inventory[0]["qty"] == 1


def test_grant_reward_second_call_rejected_inventory_unchanged(conn):
    """Second grant_reward on same quest raises (duplicate claim PK) and inventory stays at 1."""
    import sqlite3 as _sqlite3

    grant_reward(conn, PLAYER, ITEM, QUEST, NOW)

    with pytest.raises(_sqlite3.IntegrityError):
        grant_reward(conn, PLAYER, ITEM, QUEST, NOW)

    # Inventory must still be exactly 1 — the rolled-back second attempt must not have added.
    inventory = get_inventory(conn, PLAYER)
    assert inventory[0]["qty"] == 1


def test_validate_give_reward_atomic_path_accept_then_reject(conn):
    """validate_give_reward accept followed by second attempt → rejected, qty still 1."""
    set_quest_state(conn, QUEST, PLAYER, "complete")

    first = validate_give_reward(
        GiveReward(quest_id=QUEST, item_id=ITEM, reason="atomic"),
        NPC, PLAYER, conn, now=NOW,
    )
    assert first.accepted is True
    assert get_inventory(conn, PLAYER)[0]["qty"] == 1

    second = validate_give_reward(
        GiveReward(quest_id=QUEST, item_id=ITEM, reason="again"),
        NPC, PLAYER, conn, now=NOW,
    )
    assert second.accepted is False
    assert "already claimed" in second.reason
    assert get_inventory(conn, PLAYER)[0]["qty"] == 1
