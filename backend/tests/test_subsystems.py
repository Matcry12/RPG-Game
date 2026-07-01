"""Comprehensive subsystem tests — no LLM calls.

Covers every tool/store that is NOT already exercised by:
  test_gate_disposition.py   → validate_update_disposition
  test_gate_reward.py        → SetQuestState / GiveReward gates + validate() dispatcher
  test_stream_s6.py          → score_memories() + retrieve_episodic_scored()
  test_s9_routing.py         → classify_turn + retrieve_context route-awareness

What we add here:
  1. SQLite store   — direct CRUD (disposition, quests, inventory, importance accumulator)
  2. Tool schemas   — UpdateDisposition string→int coercion; bad input rejection
  3. Beliefs store  — write_belief, retrieve_beliefs
  4. retrieve_for_reflection — importance filter + newest-first ordering
  5. LightRAG       — retrieve_lore returns '' on error (always mocked — no process needed)
  6. Lore cache     — hit/miss inside _fetch_lore avoids duplicate retrieve_lore calls
  7. Beliefs cache  — population on first call, invalidation after write_memory
  8. Prompt builder — _persona_system output shape
"""

import sqlite3
from unittest.mock import AsyncMock, patch

import chromadb
import pytest
from chromadb import EmbeddingFunction, Embeddings

from app.memory.sqlite_store import (
    add_inventory,
    add_to_importance_sum,
    apply_disposition_delta,
    get_active_quests,
    get_disposition,
    get_importance_sum,
    get_inventory,
    get_quest_state,
    grant_reward,
    init_db,
    is_reward_claimed,
    reset_importance_sum,
    set_quest_state,
)
from app.memory.vector_store import (
    get_beliefs_collection,
    get_episodic_collection,
    retrieve_beliefs,
    retrieve_for_reflection,
    write_belief,
    write_episodic,
)
from app.tools.schemas import GiveReward, SetQuestState, UpdateDisposition

NOW = "2026-01-01T00:00:00+00:00"
NPC = "shopkeeper"
PLAYER = "p1"


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    init_db(c)
    yield c
    c.close()


class _ConstEF(EmbeddingFunction):
    """All docs get the same unit embedding — isolates correctness from similarity."""

    def __call__(self, input: list[str]) -> Embeddings:
        return [[1.0] * 384 for _ in input]


@pytest.fixture
def beliefs_col(tmp_path):
    client = chromadb.PersistentClient(path=str(tmp_path / "beliefs_chroma"))
    return get_beliefs_collection(client, embedding_function=_ConstEF())


@pytest.fixture
def episodic_col(tmp_path):
    client = chromadb.PersistentClient(path=str(tmp_path / "episodic_chroma"))
    return get_episodic_collection(client, embedding_function=_ConstEF())


# ---------------------------------------------------------------------------
# 1. SQLite store — disposition
# ---------------------------------------------------------------------------


def test_get_disposition_default_zero(conn):
    assert get_disposition(conn, NPC, PLAYER) == 0


def test_apply_disposition_delta_from_zero(conn):
    new = apply_disposition_delta(conn, NPC, PLAYER, 5, NOW)
    assert new == 5
    assert get_disposition(conn, NPC, PLAYER) == 5


def test_apply_disposition_delta_accumulates(conn):
    apply_disposition_delta(conn, NPC, PLAYER, 3, NOW)
    new = apply_disposition_delta(conn, NPC, PLAYER, -1, NOW)
    assert new == 2


def test_apply_disposition_negative(conn):
    new = apply_disposition_delta(conn, NPC, PLAYER, -7, NOW)
    assert new == -7


# ---------------------------------------------------------------------------
# 2. SQLite store — quests
# ---------------------------------------------------------------------------


def test_get_quest_state_seeded(conn):
    # init_db seeds herb_delivery=active, rat_cellar=complete, lost_locket=not_started
    assert get_quest_state(conn, "herb_delivery", PLAYER) == "active"
    assert get_quest_state(conn, "rat_cellar", PLAYER) == "complete"
    assert get_quest_state(conn, "lost_locket", PLAYER) == "not_started"


def test_get_quest_state_missing(conn):
    assert get_quest_state(conn, "nonexistent", PLAYER) is None


def test_set_quest_state_transition(conn):
    set_quest_state(conn, "herb_delivery", PLAYER, "complete")
    assert get_quest_state(conn, "herb_delivery", PLAYER) == "complete"


def test_set_quest_state_invalid_raises(conn):
    with pytest.raises(ValueError):
        set_quest_state(conn, "herb_delivery", PLAYER, "broken_state")


def test_get_active_quests(conn):
    active = get_active_quests(conn, PLAYER)
    assert "herb_delivery" in active
    assert "rat_cellar" not in active


# ---------------------------------------------------------------------------
# 3. SQLite store — inventory
# ---------------------------------------------------------------------------


def test_get_inventory_empty(conn):
    assert get_inventory(conn, PLAYER) == []


def test_add_inventory_and_get(conn):
    add_inventory(conn, PLAYER, "healing_potion", 2)
    inv = get_inventory(conn, PLAYER)
    assert len(inv) == 1
    assert inv[0]["item_id"] == "healing_potion"
    assert inv[0]["qty"] == 2


def test_add_inventory_accumulates(conn):
    add_inventory(conn, PLAYER, "gold_coin", 10)
    add_inventory(conn, PLAYER, "gold_coin", 5)
    inv = {row["item_id"]: row["qty"] for row in get_inventory(conn, PLAYER)}
    assert inv["gold_coin"] == 15


# ---------------------------------------------------------------------------
# 4. SQLite store — reward claims
# ---------------------------------------------------------------------------


def test_is_reward_claimed_initially_false(conn):
    assert not is_reward_claimed(conn, PLAYER, "rat_cellar")


def test_grant_reward_marks_claimed_and_adds_inventory(conn):
    grant_reward(conn, PLAYER, "silver_ring", "rat_cellar", NOW)
    assert is_reward_claimed(conn, PLAYER, "rat_cellar")
    inv = {row["item_id"]: row["qty"] for row in get_inventory(conn, PLAYER)}
    assert inv["silver_ring"] == 1


def test_grant_reward_idempotent_raises_on_double_claim(conn):
    grant_reward(conn, PLAYER, "silver_ring", "rat_cellar", NOW)
    with pytest.raises(Exception):
        grant_reward(conn, PLAYER, "silver_ring", "rat_cellar", NOW)


# ---------------------------------------------------------------------------
# 5. SQLite store — importance accumulator
# ---------------------------------------------------------------------------


def test_importance_sum_starts_at_zero(conn):
    assert get_importance_sum(conn, NPC, PLAYER) == 0


def test_add_to_importance_sum(conn):
    total = add_to_importance_sum(conn, NPC, PLAYER, 7)
    assert total == 7


def test_importance_sum_accumulates(conn):
    add_to_importance_sum(conn, NPC, PLAYER, 5)
    add_to_importance_sum(conn, NPC, PLAYER, 3)
    assert get_importance_sum(conn, NPC, PLAYER) == 8


def test_reset_importance_sum(conn):
    add_to_importance_sum(conn, NPC, PLAYER, 15)
    reset_importance_sum(conn, NPC, PLAYER)
    assert get_importance_sum(conn, NPC, PLAYER) == 0


# ---------------------------------------------------------------------------
# 6. Tool schemas — UpdateDisposition coercion
# ---------------------------------------------------------------------------


def test_schema_update_disposition_int():
    call = UpdateDisposition(delta=5)
    assert call.delta == 5


def test_schema_update_disposition_string_coerces():
    call = UpdateDisposition(delta="-8")
    assert call.delta == -8
    assert isinstance(call.delta, int)


def test_schema_update_disposition_bad_string_raises():
    with pytest.raises(Exception):
        UpdateDisposition(delta="not_a_number")


def test_schema_give_reward_fields():
    r = GiveReward(quest_id="rat_cellar", item_id="silver_ring", reason="job well done")
    assert r.quest_id == "rat_cellar"
    assert r.item_id == "silver_ring"


def test_schema_set_quest_state_fields():
    q = SetQuestState(quest_id="lost_locket", state="active")
    assert q.quest_id == "lost_locket"
    assert q.state == "active"


# ---------------------------------------------------------------------------
# 7. Beliefs store
# ---------------------------------------------------------------------------


def test_retrieve_beliefs_empty(beliefs_col):
    result = retrieve_beliefs(beliefs_col, npc_id=NPC, player_id=PLAYER)
    assert result == []


def test_write_and_retrieve_beliefs(beliefs_col):
    write_belief(beliefs_col, npc_id=NPC, player_id=PLAYER, text="Player is trustworthy.", timestamp=NOW)
    result = retrieve_beliefs(beliefs_col, npc_id=NPC, player_id=PLAYER)
    assert len(result) == 1
    assert result[0]["text"] == "Player is trustworthy."
    assert result[0]["timestamp"] == NOW


def test_retrieve_beliefs_newest_first(beliefs_col):
    t1 = "2026-01-01T10:00:00+00:00"
    t2 = "2026-01-01T12:00:00+00:00"
    write_belief(beliefs_col, npc_id=NPC, player_id=PLAYER, text="Older belief.", timestamp=t1)
    write_belief(beliefs_col, npc_id=NPC, player_id=PLAYER, text="Newer belief.", timestamp=t2)
    result = retrieve_beliefs(beliefs_col, npc_id=NPC, player_id=PLAYER)
    assert result[0]["text"] == "Newer belief."


def test_beliefs_isolated_by_player(beliefs_col):
    write_belief(beliefs_col, npc_id=NPC, player_id="p1", text="Belief for p1.", timestamp=NOW)
    write_belief(beliefs_col, npc_id=NPC, player_id="p2", text="Belief for p2.", timestamp=NOW)
    p1_beliefs = retrieve_beliefs(beliefs_col, npc_id=NPC, player_id="p1")
    assert all("p1" not in b["text"] or "p1" in b["text"] for b in p1_beliefs)
    assert len(p1_beliefs) == 1
    assert p1_beliefs[0]["text"] == "Belief for p1."


# ---------------------------------------------------------------------------
# 8. retrieve_for_reflection — importance filter + ordering
# ---------------------------------------------------------------------------


def test_retrieve_for_reflection_empty(episodic_col):
    result = retrieve_for_reflection(episodic_col, npc_id=NPC, player_id=PLAYER, min_importance=5)
    assert result == []


def test_retrieve_for_reflection_filters_by_importance(episodic_col):
    t1 = "2026-01-01T10:00:00+00:00"
    t2 = "2026-01-01T11:00:00+00:00"
    write_episodic(episodic_col, npc_id=NPC, player_id=PLAYER, text="Low importance event.", timestamp=t1, importance=3)
    write_episodic(episodic_col, npc_id=NPC, player_id=PLAYER, text="High importance event.", timestamp=t2, importance=8)
    result = retrieve_for_reflection(episodic_col, npc_id=NPC, player_id=PLAYER, min_importance=5)
    assert len(result) == 1
    assert result[0]["text"] == "High importance event."


def test_retrieve_for_reflection_newest_first(episodic_col):
    t1 = "2026-01-01T09:00:00+00:00"
    t2 = "2026-01-01T11:00:00+00:00"
    write_episodic(episodic_col, npc_id=NPC, player_id=PLAYER, text="Old salient event.", timestamp=t1, importance=7)
    write_episodic(episodic_col, npc_id=NPC, player_id=PLAYER, text="New salient event.", timestamp=t2, importance=9)
    result = retrieve_for_reflection(episodic_col, npc_id=NPC, player_id=PLAYER, min_importance=5)
    assert result[0]["text"] == "New salient event."


def test_retrieve_for_reflection_respects_limit(episodic_col):
    for i in range(5):
        write_episodic(
            episodic_col, npc_id=NPC, player_id=PLAYER,
            text=f"Event {i}.", timestamp=f"2026-01-01T{10+i:02d}:00:00+00:00", importance=8
        )
    result = retrieve_for_reflection(episodic_col, npc_id=NPC, player_id=PLAYER, min_importance=5, limit=3)
    assert len(result) == 3


# ---------------------------------------------------------------------------
# 8b. _strip_lore_context — boilerplate removal
# ---------------------------------------------------------------------------


def test_strip_lore_context_extracts_content():
    from app.memory.vector_store import _strip_lore_context

    raw = (
        'Document Chunks (Each entry has a reference_id ...):\n```json\n'
        '{"reference_id": "", "content": "The old mill is derelict."}\n'
        '{"reference_id": "", "content": "Rook leads the bandit camp."}\n'
        '```\nReference Document List (...):\n```\n\n```'
    )
    result = _strip_lore_context(raw)
    assert result == "The old mill is derelict.\nRook leads the bandit camp."
    assert "reference_id" not in result
    assert "Document Chunks" not in result


def test_strip_lore_context_unescapes_json():
    from app.memory.vector_store import _strip_lore_context

    raw = '{"content": "He said \\"hello\\" to the guard."}'
    assert _strip_lore_context(raw) == 'He said "hello" to the guard.'


def test_strip_lore_context_fallback_when_no_content():
    """No content blocks → return raw unchanged (never silently drop context)."""
    from app.memory.vector_store import _strip_lore_context

    raw = "some plain text with no json content blocks"
    assert _strip_lore_context(raw) == raw


# ---------------------------------------------------------------------------
# 9. LightRAG — retrieve_lore returns '' on error
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retrieve_lore_returns_empty_string_on_exception():
    """retrieve_lore must return '' on any error — LightRAG failure must not crash a turn."""
    from app.memory.vector_store import retrieve_lore

    with patch("app.memory.vector_store.get_lore_rag", side_effect=RuntimeError("rag unavailable")):
        result = await retrieve_lore(
            NPC, "what happened here?",
            history=[],
            lightrag_path="/tmp/fake",
            groq_api_key="fake",
            groq_model="fake",
        )
    assert result == ""


@pytest.mark.asyncio
async def test_retrieve_lore_returns_string_on_success():
    """retrieve_lore passes through the rag.aquery string result."""
    from app.memory.vector_store import retrieve_lore

    mock_rag = AsyncMock()
    mock_rag.aquery = AsyncMock(return_value="The kingdom fell during the Sundering.")

    with patch("app.memory.vector_store.get_lore_rag", return_value=mock_rag):
        result = await retrieve_lore(
            NPC, "what is the kingdom?",
            history=[],
            lightrag_path="/tmp/fake",
            groq_api_key="fake",
            groq_model="fake",
        )
    assert "kingdom" in result


# ---------------------------------------------------------------------------
# 10. Lore cache — second call skips retrieve_lore
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lore_cache_hit_skips_retrieve_lore():
    """Same (npc_id, message) must not call retrieve_lore twice."""
    import app.graph.nodes as nodes

    # Clear the cache before testing
    nodes._lore_cache.clear()

    mock_lore = AsyncMock(return_value="Lore about the ancient war.")
    state = {
        "npc_id": NPC,
        "player_id": PLAYER,
        "message": "tell me about the ancient war",
        "route": "full-with-lore",
        "persona_text": "# Mira\n\nYou are a merchant.\n",
        "history": [],
    }

    with (
        patch("app.graph.nodes.retrieve_lore", mock_lore),
        patch("app.graph.nodes.get_client"),
        patch("app.graph.nodes.get_episodic_collection"),
        patch("app.graph.nodes.get_beliefs_collection"),
        patch("app.graph.nodes.retrieve_episodic_scored", return_value=[]),
        patch("app.graph.nodes.retrieve_beliefs", return_value=[]),
        patch("app.config.settings.grounding_gate", True),
        patch("app.config.settings.lore_context_min_chars", 5),
        patch("app.config.settings.reflection", False),
        patch("app.config.settings.episodic_memory", False),
    ):
        await nodes.retrieve_context(state)
        await nodes.retrieve_context(state)  # second call — same message

    assert mock_lore.call_count == 1  # cache hit on second call


@pytest.mark.asyncio
async def test_lore_cache_miss_on_different_message():
    """Different messages must each call retrieve_lore independently."""
    import app.graph.nodes as nodes

    nodes._lore_cache.clear()

    mock_lore = AsyncMock(return_value="Some lore.")
    base_state = {
        "npc_id": NPC,
        "player_id": PLAYER,
        "route": "full-with-lore",
        "persona_text": "# Mira\n\nYou are a merchant.\n",
        "history": [],
    }

    with (
        patch("app.graph.nodes.retrieve_lore", mock_lore),
        patch("app.graph.nodes.get_client"),
        patch("app.graph.nodes.get_episodic_collection"),
        patch("app.graph.nodes.get_beliefs_collection"),
        patch("app.graph.nodes.retrieve_episodic_scored", return_value=[]),
        patch("app.graph.nodes.retrieve_beliefs", return_value=[]),
        patch("app.config.settings.grounding_gate", True),
        patch("app.config.settings.lore_context_min_chars", 5),
        patch("app.config.settings.reflection", False),
        patch("app.config.settings.episodic_memory", False),
    ):
        await nodes.retrieve_context({**base_state, "message": "what is the kingdom?"})
        await nodes.retrieve_context({**base_state, "message": "tell me about the war"})

    assert mock_lore.call_count == 2


# ---------------------------------------------------------------------------
# 11. Beliefs cache — population + invalidation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_beliefs_cache_populated_on_first_call():
    import app.graph.nodes as nodes

    nodes._beliefs_cache.clear()

    mock_beliefs = [{"text": "Player is generous.", "timestamp": NOW}]
    state = {
        "npc_id": NPC,
        "player_id": PLAYER,
        "message": "hello there",
        "route": "full-no-lore",
        "persona_text": "# Mira\n\nYou are a merchant.\n",
        "history": [],
    }

    with (
        patch("app.graph.nodes.get_client"),
        patch("app.graph.nodes.get_episodic_collection"),
        patch("app.graph.nodes.get_beliefs_collection"),
        patch("app.graph.nodes.retrieve_episodic_scored", return_value=[]),
        patch("app.graph.nodes.retrieve_beliefs", return_value=mock_beliefs),
        patch("app.config.settings.reflection", True),
        patch("app.config.settings.episodic_memory", False),
    ):
        await nodes.retrieve_context(state)

    cache_key = (NPC, PLAYER)
    assert cache_key in nodes._beliefs_cache
    assert "generous" in nodes._beliefs_cache[cache_key]


def test_beliefs_cache_invalidated_after_pop():
    """_beliefs_cache.pop removes the key — simulates write_memory invalidation."""
    import app.graph.nodes as nodes

    nodes._beliefs_cache.clear()
    nodes._beliefs_cache[(NPC, PLAYER)] = "cached belief block"

    nodes._beliefs_cache.pop((NPC, PLAYER), None)

    assert (NPC, PLAYER) not in nodes._beliefs_cache


# ---------------------------------------------------------------------------
# 12. Prompt builder — _persona_system output shape
# ---------------------------------------------------------------------------


_PERSONA_TEXT = "---\nfoo: bar\n---\n\n# Mira\n\nYou are a merchant.\n\n## Voice\nWarm.\n"


def _make_state(route="full-no-lore", lore_block="", memory_block="", persona_text=_PERSONA_TEXT):
    return {
        "npc_id": NPC,
        "player_id": PLAYER,
        "message": "hello",
        "route": route,
        "persona_text": persona_text,
        "lore_block": lore_block,
        "memory_block": memory_block,
        "current_score": 0,
        "grounded": None,
        "history": [],
    }


def test_persona_system_trivial_uses_short_persona():
    from app.graph.nodes import _persona_system

    content = _persona_system(_make_state(route="trivial")).content
    assert "## Voice" not in content
    assert "foo: bar" not in content
    assert "Mira" in content


def test_persona_system_full_uses_full_persona():
    from app.graph.nodes import _persona_system

    content = _persona_system(_make_state(route="full-no-lore")).content
    assert "## Voice" in content


def test_persona_system_injects_lore_block():
    from app.graph.nodes import _persona_system

    lore = "The kingdom fell during the Sundering War."
    content = _persona_system(_make_state(route="full-with-lore", lore_block=lore)).content
    assert "Sundering War" in content


def test_persona_system_injects_memory_block():
    from app.graph.nodes import _persona_system

    memory = "Player helped you recover a stolen cart."
    content = _persona_system(_make_state(memory_block=memory)).content
    assert "stolen cart" in content


# ---------------------------------------------------------------------------
# 13. Prereq — agent LLM builds with the renamed SetQuestState tool
# ---------------------------------------------------------------------------


def test_get_agent_llm_builds_with_tools():
    """Regression: serving/llm.py must import SetQuestState (not the removed StartQuest)."""
    from app.serving.llm import get_agent_llm

    with patch("app.config.settings.tools_enabled", True):
        llm = get_agent_llm(with_tools=True)  # raised ImportError before the rename fix
    assert llm is not None


# ---------------------------------------------------------------------------
# 14. Mix mode wiring (ADR-0015) — query rewrite feeds lore + episodic
# ---------------------------------------------------------------------------


def _mix_state(message="who is he?", history=None):
    return {
        "npc_id": NPC,
        "player_id": PLAYER,
        "message": message,
        "route": "full-with-lore",
        "persona_text": "# Mira\n\nYou are a merchant.\n",
        "history": history or [],
    }


@pytest.mark.asyncio
async def test_mix_mode_passes_rewrite_and_keywords_to_lore():
    """mix: extract_lore_query result must reach retrieve_lore (mode/keywords/query) and episodic."""
    import app.graph.nodes as nodes
    from app.serving.llm import LoreQuery

    nodes._lore_cache.clear()
    mock_lore = AsyncMock(return_value="Rook leads the bandit camp.")
    mock_extract = AsyncMock(return_value=LoreQuery(
        ll_keywords=["Rook"], hl_keywords=["bandits"], rewritten_query="who is Rook the bandit leader"
    ))

    with patch("app.graph.nodes.extract_lore_query", mock_extract), \
         patch("app.graph.nodes.retrieve_lore", mock_lore), \
         patch("app.graph.nodes.get_client"), \
         patch("app.graph.nodes.get_episodic_collection"), \
         patch("app.graph.nodes.retrieve_episodic_scored", return_value=[]) as mock_epi, \
         patch("app.config.settings.grounding_gate", True), \
         patch("app.config.settings.lore_context_min_chars", 5), \
         patch("app.config.settings.reflection", False), \
         patch("app.config.settings.episodic_memory", True), \
         patch("app.config.settings.lore_query_mode", "mix"):
        await nodes.retrieve_context(_mix_state())

    mock_extract.assert_awaited_once()
    lkw = mock_lore.call_args.kwargs
    assert lkw["mode"] == "mix"
    assert lkw["ll_keywords"] == ["Rook"]
    assert lkw["hl_keywords"] == ["bandits"]
    assert mock_lore.call_args.args[1] == "who is Rook the bandit leader"  # rewritten query
    # episodic recall reused the rewritten query (Option A)
    assert mock_epi.call_args.kwargs["query"] == "who is Rook the bandit leader"


@pytest.mark.asyncio
async def test_mix_mode_falls_back_to_naive_on_extract_failure():
    """When extraction returns None, retrieve_lore must get mode=naive + raw message."""
    import app.graph.nodes as nodes

    nodes._lore_cache.clear()
    mock_lore = AsyncMock(return_value="some lore")
    mock_extract = AsyncMock(return_value=None)

    with patch("app.graph.nodes.extract_lore_query", mock_extract), \
         patch("app.graph.nodes.retrieve_lore", mock_lore), \
         patch("app.graph.nodes.get_client"), \
         patch("app.graph.nodes.get_episodic_collection"), \
         patch("app.graph.nodes.retrieve_episodic_scored", return_value=[]) as mock_epi, \
         patch("app.config.settings.grounding_gate", True), \
         patch("app.config.settings.lore_context_min_chars", 5), \
         patch("app.config.settings.reflection", False), \
         patch("app.config.settings.episodic_memory", True), \
         patch("app.config.settings.lore_query_mode", "mix"):
        await nodes.retrieve_context(_mix_state(message="tell me about the war"))

    _, lkw = mock_lore.call_args
    assert lkw["mode"] == "naive"
    assert lkw["ll_keywords"] is None
    assert mock_lore.call_args.args[1] == "tell me about the war"
    assert mock_epi.call_args.kwargs["query"] == "tell me about the war"


@pytest.mark.asyncio
async def test_naive_default_skips_extraction():
    """Default lore_query_mode=naive must NOT call extract_lore_query; episodic uses raw message."""
    import app.graph.nodes as nodes

    nodes._lore_cache.clear()
    mock_lore = AsyncMock(return_value="some lore")
    mock_extract = AsyncMock(return_value=None)

    with patch("app.graph.nodes.extract_lore_query", mock_extract), \
         patch("app.graph.nodes.retrieve_lore", mock_lore), \
         patch("app.graph.nodes.get_client"), \
         patch("app.graph.nodes.get_episodic_collection"), \
         patch("app.graph.nodes.retrieve_episodic_scored", return_value=[]) as mock_epi, \
         patch("app.config.settings.grounding_gate", True), \
         patch("app.config.settings.lore_context_min_chars", 5), \
         patch("app.config.settings.reflection", False), \
         patch("app.config.settings.episodic_memory", True), \
         patch("app.config.settings.lore_query_mode", "naive"):
        await nodes.retrieve_context(_mix_state(message="hello there merchant"))

    mock_extract.assert_not_called()
    assert mock_lore.call_args.kwargs["mode"] == "naive"
    assert mock_epi.call_args.kwargs["query"] == "hello there merchant"


@pytest.mark.asyncio
async def test_mix_cache_key_distinguishes_different_rewrites():
    """Same raw message, different rewrites (from different context) must NOT collide in cache."""
    import app.graph.nodes as nodes
    from app.serving.llm import LoreQuery

    nodes._lore_cache.clear()
    mock_lore = AsyncMock(return_value="lore")
    # Each call returns a different rewritten query (simulating history-dependent resolution).
    mock_extract = AsyncMock(side_effect=[
        LoreQuery(ll_keywords=["Rook"], hl_keywords=[], rewritten_query="who is Rook"),
        LoreQuery(ll_keywords=["Corvin"], hl_keywords=[], rewritten_query="who is Corvin Dale"),
    ])

    with patch("app.graph.nodes.extract_lore_query", mock_extract), \
         patch("app.graph.nodes.retrieve_lore", mock_lore), \
         patch("app.graph.nodes.get_client"), \
         patch("app.graph.nodes.get_episodic_collection"), \
         patch("app.graph.nodes.retrieve_episodic_scored", return_value=[]), \
         patch("app.config.settings.grounding_gate", True), \
         patch("app.config.settings.lore_context_min_chars", 5), \
         patch("app.config.settings.reflection", False), \
         patch("app.config.settings.episodic_memory", False), \
         patch("app.config.settings.lore_query_mode", "mix"):
        await nodes.retrieve_context(_mix_state(message="who is he?"))
        await nodes.retrieve_context(_mix_state(message="who is he?"))

    # Different rewrites → two distinct cache keys → retrieve_lore called twice (no stale hit).
    assert mock_lore.call_count == 2
