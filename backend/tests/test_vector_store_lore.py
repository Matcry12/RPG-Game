"""Unit tests for LightRAG lore store (S5) — offline, no Groq."""

import numpy as np
import pytest

import app.memory.vector_store as vs_mod


@pytest.fixture(autouse=True)
def clear_lore_state():
    """Isolate module-level dicts between tests."""
    vs_mod._lore_rags.clear()
    vs_mod._lore_locks.clear()
    yield
    vs_mod._lore_rags.clear()
    vs_mod._lore_locks.clear()


@pytest.fixture
def fake_llm(monkeypatch):
    """Monkeypatch openai_complete_if_cache so no real Groq calls are made."""
    async def _stub(*args, **kwargs):
        return "stub"

    monkeypatch.setattr("lightrag.llm.openai.openai_complete_if_cache", _stub)


@pytest.mark.asyncio
async def test_seed_lore_inserts_without_error(tmp_path, fake_llm):
    entries = [
        {"id": "1", "text": "Ashenveil is a city of traders.", "category": "general"},
        {"id": "2", "text": "The market district is known for rare goods.", "category": "market"},
    ]
    count = await vs_mod.seed_lore(
        "shopkeeper", entries, lightrag_path=tmp_path, groq_api_key="fake", groq_model="stub"
    )
    assert count == 2


@pytest.mark.asyncio
async def test_retrieve_lore_returns_string(tmp_path, fake_llm):
    entries = [{"id": "1", "text": "Ashenveil is a city of traders.", "category": "general"}]
    await vs_mod.seed_lore(
        "shopkeeper", entries, lightrag_path=tmp_path, groq_api_key="fake", groq_model="stub"
    )
    result = await vs_mod.retrieve_lore(
        "shopkeeper",
        "Tell me about Ashenveil",
        history=[],
        lightrag_path=tmp_path,
        groq_api_key="fake",
        groq_model="stub",
    )
    assert isinstance(result, str)


@pytest.mark.asyncio
async def test_per_npc_isolation(tmp_path, fake_llm):
    """Two NPCs get separate LightRAG instances (different working_dirs)."""
    await vs_mod.seed_lore(
        "shopkeeper",
        [{"id": "1", "text": "Mira runs The Amber Shelf.", "category": "market"}],
        lightrag_path=tmp_path,
        groq_api_key="fake",
        groq_model="stub",
    )
    await vs_mod.seed_lore(
        "innkeeper",
        [{"id": "2", "text": "The inn is called The Rusty Flagon.", "category": "locations"}],
        lightrag_path=tmp_path,
        groq_api_key="fake",
        groq_model="stub",
    )

    assert "shopkeeper" in vs_mod._lore_rags
    assert "innkeeper" in vs_mod._lore_rags
    assert vs_mod._lore_rags["shopkeeper"] is not vs_mod._lore_rags["innkeeper"]

    shopkeeper_dir = str(tmp_path / "shopkeeper")
    innkeeper_dir = str(tmp_path / "innkeeper")
    assert vs_mod._lore_rags["shopkeeper"].working_dir == shopkeeper_dir
    assert vs_mod._lore_rags["innkeeper"].working_dir == innkeeper_dir
