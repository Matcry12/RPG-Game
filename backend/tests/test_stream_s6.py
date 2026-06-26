"""S6 — memory stream scoring tests.

Two levels:
  1. Unit: score_memories() pure function — high-importance old memory beats recent trivia.
  2. Integration: retrieve_episodic_scored() through a real in-memory Chroma collection.

Math check (OLD=6h, distance=0.1 both, ALPHA=0.35, BETA=0.35, GAMMA=0.30, DECAY=0.1/h):
  salient  (imp=9, 6h):  0.35*exp(-0.6) + 0.35*0.9 + 0.30*0.9 ≈ 0.192+0.315+0.270 = 0.777
  trivial  (imp=2, 5min): 0.35*exp(-0.008) + 0.35*0.2 + 0.30*0.9 ≈ 0.347+0.070+0.270 = 0.687
"""

from datetime import datetime, timedelta, timezone

import chromadb
import pytest
from chromadb import EmbeddingFunction, Embeddings

from app.memory.stream import ALPHA, BETA, GAMMA, score_memories
from app.memory.vector_store import (
    get_episodic_collection,
    retrieve_episodic_scored,
    write_episodic,
)

NOW = datetime(2026, 6, 25, 12, 0, 0, tzinfo=timezone.utc)
OLD = (NOW - timedelta(hours=6)).isoformat()  # 6 hours ago — salient but not ancient
NEW = (NOW - timedelta(minutes=5)).isoformat()  # 5 minutes ago — recent but trivial


# ---------------------------------------------------------------------------
# Unit: score_memories (pure function, no Chroma)
# ---------------------------------------------------------------------------


def test_high_importance_old_beats_low_importance_recent():
    """Salient 6h-old memory (importance=9) outscores trivial 5-min-old (importance=2)
    at equal relevance — importance weight compensates for recency gap."""
    candidates = [
        {"text": "trivial", "importance": 2, "timestamp": NEW, "distance": 0.1},
        {"text": "salient", "importance": 9, "timestamp": OLD, "distance": 0.1},
    ]
    ranked = score_memories(candidates, NOW)
    assert ranked[0]["text"] == "salient"


def test_score_field_in_range():
    c = [{"text": "x", "importance": 5, "timestamp": NEW, "distance": 0.5}]
    assert 0.0 <= score_memories(c, NOW)[0]["score"] <= 1.0


def test_perfect_memory_scores_one():
    """A memory that is instant, max-importance, and perfectly relevant scores 1.0."""
    candidates = [
        {
            "text": "perfect",
            "importance": 10,
            "timestamp": NOW.isoformat(),
            "distance": 0.0,
        }
    ]
    assert (
        abs(score_memories(candidates, NOW)[0]["score"] - (ALPHA + BETA + GAMMA)) < 0.01
    )


def test_malformed_timestamp_degrades_gracefully():
    candidates = [{"text": "x", "importance": 5, "timestamp": "bad", "distance": 0.5}]
    assert len(score_memories(candidates, NOW)) == 1  # doesn't raise


def test_empty_input():
    assert score_memories([], NOW) == []


# ---------------------------------------------------------------------------
# Integration: retrieve_episodic_scored via in-memory Chroma
# ---------------------------------------------------------------------------


class _ConstEF(EmbeddingFunction):
    """All docs get the same unit embedding — isolates scoring from similarity."""

    def name(self) -> str:
        return "const-ef"

    def get_config(self):
        return {}

    def __call__(self, input: list[str]) -> Embeddings:  # noqa: A002
        return [[1.0] * 384 for _ in input]


@pytest.fixture
def col(tmp_path):
    # EphemeralClient is a singleton — use PersistentClient with tmp_path for isolation.
    client = chromadb.PersistentClient(path=str(tmp_path / "chroma"))
    return get_episodic_collection(client, embedding_function=_ConstEF())


def test_scored_retrieval_ranks_by_stream_score(col):
    """With identical embeddings (same relevance=1.0), importance+recency decide ranking.
    salient (imp=9, 6h) must beat trivial (imp=2, 5min)."""
    write_episodic(
        col,
        npc_id="npc1",
        player_id="p1",
        text="You chatted about the weather.",
        timestamp=NEW,
        importance=2,
    )
    write_episodic(
        col,
        npc_id="npc1",
        player_id="p1",
        text="You helped the player slay the bandit lord.",
        timestamp=OLD,
        importance=9,
    )
    # different player — must not appear in results
    write_episodic(
        col,
        npc_id="npc1",
        player_id="other",
        text="other player event",
        timestamp=NEW,
        importance=9,
    )

    results = retrieve_episodic_scored(
        col, npc_id="npc1", player_id="p1", query="bandits", k=2, now=NOW
    )
    assert len(results) == 2
    assert results[0]["text"] == "You helped the player slay the bandit lord."


def test_scored_retrieval_returns_score_field(col):
    write_episodic(
        col, npc_id="npc1", player_id="p1", text="event", timestamp=NEW, importance=5
    )
    results = retrieve_episodic_scored(
        col, npc_id="npc1", player_id="p1", query="event", k=1, now=NOW
    )
    assert results and "score" in results[0]


def test_scored_retrieval_empty_collection(col):
    assert (
        retrieve_episodic_scored(
            col, npc_id="npc1", player_id="p1", query="x", k=3, now=NOW
        )
        == []
    )


def test_scored_retrieval_isolates_by_player(col):
    write_episodic(
        col, npc_id="npc1", player_id="other", text="other", timestamp=NEW, importance=9
    )
    assert (
        retrieve_episodic_scored(
            col, npc_id="npc1", player_id="p1", query="x", k=3, now=NOW
        )
        == []
    )
