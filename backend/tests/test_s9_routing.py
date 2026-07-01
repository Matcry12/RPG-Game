"""S9 routing tests — classify_turn, _extract_short_persona, retrieve_context route-awareness.

All tests are non-LLM. They verify:
  1. classify_turn heuristic routes messages correctly.
  2. _extract_short_persona produces a short version of the persona.
  3. retrieve_context skips ChromaDB entirely on trivial route.
  4. retrieve_context skips lore on full-no-lore route.
  5. retrieve_context starts a lore task on full-with-lore route.
"""

from unittest.mock import AsyncMock, patch

import pytest

from app.graph.nodes import _classify_heuristic, _extract_short_persona
from app.graph.state import TurnState

# ---------------------------------------------------------------------------
# 1. Heuristic router
# ---------------------------------------------------------------------------

TRIVIAL_CASES = [
    "hi",
    "hello",
    "thanks",
    "ok",
    "bye",
    "good morning",       # 2 words, no ?
    "see you later",      # 3 words, no ?
]

FULL_WITH_LORE_CASES = [
    "What happened to Corvin Dale?",          # has ?
    "who is the king?",                        # has ?
    "tell me about the kingdom",               # "kingdom" in _LORE_DOMAIN
    "I heard there was a war here",            # "war" in _LORE_DOMAIN
    "do you know any legends?",                # has ?
    "what is that ancient artifact?",          # has ? + "ancient"
]

FULL_NO_LORE_CASES = [
    "I want to buy something from you",
    "show me what you have",
    "can you help me with a trade",
    "I need supplies for my journey",
]


@pytest.mark.parametrize("msg", TRIVIAL_CASES)
def test_heuristic_trivial(msg):
    assert _classify_heuristic(msg) == "trivial"


@pytest.mark.parametrize("msg", FULL_WITH_LORE_CASES)
def test_heuristic_full_with_lore(msg):
    assert _classify_heuristic(msg) == "full-with-lore"


@pytest.mark.parametrize("msg", FULL_NO_LORE_CASES)
def test_heuristic_full_no_lore(msg):
    assert _classify_heuristic(msg) == "full-no-lore"


def test_heuristic_five_words_no_question_is_not_trivial():
    assert _classify_heuristic("I would like some help") == "full-no-lore"


def test_heuristic_four_words_with_question_is_not_trivial():
    assert _classify_heuristic("who are you?") == "full-with-lore"


# Hard cases — lore domain wins over trivial word count
def test_heuristic_single_lore_word_no_question():
    # Previously a bug: short lore words were classified trivial before the domain check
    assert _classify_heuristic("kingdom") == "full-with-lore"


def test_heuristic_two_lore_words_no_question():
    assert _classify_heuristic("ancient curse") == "full-with-lore"


def test_heuristic_short_lore_phrase_no_question():
    assert _classify_heuristic("the dragon") == "full-with-lore"


def test_heuristic_lore_word_in_long_statement():
    # Lore domain word inside a longer statement with no ?
    assert _classify_heuristic("I heard the bandits raided a village last night") == "full-with-lore"


def test_heuristic_mixed_case_lore_word():
    # Case-insensitive match via .lower()
    assert _classify_heuristic("Tell me about the KINGDOM") == "full-with-lore"


def test_heuristic_lore_word_negation():
    # "legend" (singular) IS in _LORE_DOMAIN; conservative: negation still fetches lore context
    assert _classify_heuristic("I don't believe the legend") == "full-with-lore"


def test_heuristic_plural_lore_word_is_known_gap():
    # "legends" (plural) is NOT in _LORE_DOMAIN — no stemming → routes to full-no-lore
    # ponytail: add stemming or expand domain when RAG router lands in S10
    assert _classify_heuristic("I don't care about legends") == "full-no-lore"


def test_heuristic_social_question_is_false_positive():
    # Known limitation: social ? routes to full-with-lore (wastes a lore fetch, not a correctness bug)
    assert _classify_heuristic("are you okay?") == "full-with-lore"


def test_heuristic_four_word_farewell_is_trivial():
    # 4 words, no ?, no lore word → trivial
    assert _classify_heuristic("good luck out there") == "trivial"


def test_heuristic_exclamation_is_trivial():
    # "!" is not "?" — one-word exclamation stays trivial
    assert _classify_heuristic("hello!") == "trivial"


def test_heuristic_lore_word_with_punctuation_attached():
    # "kingdom." — .split() keeps punctuation attached, .lower() doesn't strip it
    # "kingdom." is NOT in _LORE_DOMAIN → routes to trivial (known limitation of split-based check)
    result = _classify_heuristic("the kingdom.")
    # 2 words, no ?, "kingdom." not in domain → trivial (documents current behavior, not desired)
    assert result == "trivial"  # ponytail: known gap, strip punctuation when RAG router lands in S10


# ---------------------------------------------------------------------------
# 2. Short persona extraction
# ---------------------------------------------------------------------------

_SAMPLE_PERSONA = """\
---
lore_categories:
  - general
  - market
---

# Mira Thistlewick — Curio Merchant

You are Mira Thistlewick, a weathered merchant.

## Voice and manner
Speak in a warm tone.

## What you sell
Rare maps and trinkets.
"""


def test_short_persona_strips_frontmatter():
    result = _extract_short_persona(_SAMPLE_PERSONA)
    assert "---" not in result
    assert "lore_categories" not in result


def test_short_persona_stops_at_first_section():
    result = _extract_short_persona(_SAMPLE_PERSONA)
    assert "## Voice" not in result
    assert "## What you sell" not in result


def test_short_persona_keeps_name_and_intro():
    result = _extract_short_persona(_SAMPLE_PERSONA)
    assert "Mira Thistlewick" in result
    assert "weathered merchant" in result


def test_short_persona_shorter_than_full():
    result = _extract_short_persona(_SAMPLE_PERSONA)
    assert len(result) < len(_SAMPLE_PERSONA) / 2


def test_short_persona_no_frontmatter_passthrough():
    text = "# Name\n\nIntro.\n\n## Section\n\ndetail"
    result = _extract_short_persona(text)
    assert "## Section" not in result
    assert "Intro" in result


# ---------------------------------------------------------------------------
# 3. retrieve_context route-awareness (no LLM, ChromaDB mocked)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_trivial_route_skips_chroma(chroma: object):
    """Trivial route must not touch ChromaDB at all."""
    from app.graph.nodes import retrieve_context

    state: TurnState = {
        "npc_id": "shopkeeper",
        "player_id": "p1",
        "message": "hi",
        "route": "trivial",
        "persona_text": _SAMPLE_PERSONA,
        "history": [],
    }

    with patch("app.graph.nodes.get_client") as mock_get_client:
        result = await retrieve_context(state)
        mock_get_client.assert_not_called()

    assert result["memory_block"] == ""
    assert result["lore_block"] == ""
    assert result["grounded"] is None
    assert result["recalled"] == []


@pytest.mark.asyncio
async def test_full_no_lore_skips_lore_fetch(chroma: object):
    """full-no-lore route must not call retrieve_lore."""
    from app.graph.nodes import retrieve_context

    state: TurnState = {
        "npc_id": "shopkeeper",
        "player_id": "p1",
        "message": "I want to buy something",
        "route": "full-no-lore",
        "persona_text": _SAMPLE_PERSONA,
        "history": [],
    }

    with patch("app.graph.nodes.retrieve_lore") as mock_lore:
        await retrieve_context(state)
        mock_lore.assert_not_called()


@pytest.mark.asyncio
async def test_full_with_lore_calls_lore_fetch(chroma: object):
    """full-with-lore route must call retrieve_lore when grounding_gate is on."""
    from app.graph.nodes import retrieve_context

    state: TurnState = {
        "npc_id": "shopkeeper",
        "player_id": "p1",
        "message": "What happened to the missing merchant?",
        "route": "full-with-lore",
        "persona_text": _SAMPLE_PERSONA,
        "history": [],
    }

    mock_lore = AsyncMock(return_value="Some lore context here for grounding purposes.")
    with (
        patch("app.graph.nodes.retrieve_lore", mock_lore),
        patch("app.config.settings.grounding_gate", True),
        patch("app.config.settings.lore_context_min_chars", 10),
    ):
        result = await retrieve_context(state)
        mock_lore.assert_called_once()

    assert result["grounded"] is True
    assert "lore context" in result["lore_block"]


@pytest.mark.asyncio
async def test_full_with_lore_grounded_false_on_empty(chroma: object):
    """When retrieve_lore returns empty, grounded must be False (not None)."""
    from app.graph.nodes import retrieve_context

    state: TurnState = {
        "npc_id": "shopkeeper",
        "player_id": "p1",
        "message": "tell me about the ancient war",
        "route": "full-with-lore",
        "persona_text": _SAMPLE_PERSONA,
        "history": [],
    }

    with (
        patch("app.graph.nodes.retrieve_lore", AsyncMock(return_value="")),
        patch("app.config.settings.grounding_gate", True),
    ):
        result = await retrieve_context(state)

    assert result["grounded"] is False
    assert result["lore_block"] == ""
