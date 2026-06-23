"""Unit tests for vector_store — fuzzy episodic memory.

Uses a deterministic injected embedding function (hash-based) so tests need
NO network or model download.  Uses chromadb.EphemeralClient() for an in-memory
store that is discarded after each test.
"""

import hashlib

import chromadb
import pytest
from chromadb import EmbeddingFunction, Embeddings

from app.memory.vector_store import (
    get_episodic_collection,
    retrieve_episodic,
    write_episodic,
)

# ---------------------------------------------------------------------------
# Deterministic embedding function — no network, no model
# ---------------------------------------------------------------------------

_DIM = 32  # small fixed width; enough for Chroma to operate


class _HashEmbed(EmbeddingFunction):
    """Hash each document/query string into a fixed-width float vector."""

    def __init__(self):
        pass

    def name(self) -> str:
        return "hash-embed"

    def get_config(self):
        return {}

    def __call__(self, input: list[str]) -> Embeddings:  # noqa: A002
        result: Embeddings = []
        for text in input:
            digest = hashlib.sha256(text.encode()).digest()
            # Repeat digest bytes until we have _DIM values, then normalise to [0,1].
            extended = (digest * (_DIM // len(digest) + 1))[:_DIM]
            result.append([b / 255.0 for b in extended])
        return result


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def collection(tmp_path):
    """Fresh PersistentClient Chroma collection with the hash embedder, torn down after each test.

    Uses tmp_path so each test gets a completely isolated on-disk store — EphemeralClient
    shares an in-memory singleton across tests in the same process, which causes cross-test
    contamination.
    """
    client = chromadb.PersistentClient(path=str(tmp_path / "chroma"))
    col = get_episodic_collection(client, embedding_function=_HashEmbed())
    yield col


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_write_then_retrieve_returns_text(collection):
    """Writing one doc and querying with a similar string must return that doc's text."""
    write_episodic(
        collection,
        npc_id="shopkeeper",
        player_id="p1",
        text="The player asked about the missing locket.",
        timestamp="2026-01-01T00:00:00+00:00",
        importance=3,
    )

    results = retrieve_episodic(
        collection,
        npc_id="shopkeeper",
        player_id="p1",
        query="missing locket",
        k=1,
    )

    assert len(results) == 1
    assert results[0]["text"] == "The player asked about the missing locket."
    assert results[0]["importance"] == 3
    assert results[0]["timestamp"] == "2026-01-01T00:00:00+00:00"


def test_isolation_by_npc_and_player(collection):
    """Docs for (npcA, p1) must not appear when querying (npcB, p1) or (npcA, p2)."""
    write_episodic(
        collection,
        npc_id="npcA",
        player_id="p1",
        text="npcA-p1 memory",
        timestamp="2026-01-01T00:00:00+00:00",
        importance=3,
    )
    write_episodic(
        collection,
        npc_id="npcB",
        player_id="p1",
        text="npcB-p1 memory",
        timestamp="2026-01-01T00:00:00+00:00",
        importance=3,
    )
    write_episodic(
        collection,
        npc_id="npcA",
        player_id="p2",
        text="npcA-p2 memory",
        timestamp="2026-01-01T00:00:00+00:00",
        importance=3,
    )

    results = retrieve_episodic(
        collection,
        npc_id="npcA",
        player_id="p1",
        query="memory",
        k=10,
    )

    texts = [r["text"] for r in results]
    assert "npcA-p1 memory" in texts
    assert "npcB-p1 memory" not in texts
    assert "npcA-p2 memory" not in texts


def test_empty_collection_returns_empty_list(collection):
    """retrieve_episodic on an empty collection must return [] without raising."""
    results = retrieve_episodic(
        collection,
        npc_id="shopkeeper",
        player_id="p1",
        query="anything",
        k=3,
    )
    assert results == []


def test_retrieve_filters_when_only_other_pairs_exist(collection):
    """If docs exist but none match the requested (npc, player), return []."""
    write_episodic(
        collection,
        npc_id="npcB",
        player_id="p1",
        text="irrelevant memory",
        timestamp="2026-01-01T00:00:00+00:00",
        importance=3,
    )

    results = retrieve_episodic(
        collection,
        npc_id="npcA",
        player_id="p1",
        query="irrelevant memory",
        k=3,
    )
    assert results == []
