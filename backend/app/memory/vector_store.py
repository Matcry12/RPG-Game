"""Fuzzy episodic memory via ChromaDB — recall only, never truth.

This module is for approximate, best-effort recall of past events.
It NEVER gates any action and NEVER supersedes SQLite.
The LLM never owns truth; this is a hint layer only.

All public functions accept an explicit Chroma collection so they are
unit-testable without a real PersistentClient (mirrors sqlite_store.py style).
"""

import functools
import uuid
from pathlib import Path

import chromadb
from chromadb import ClientAPI, Collection


# ---------------------------------------------------------------------------
# Client + collection factory
# ---------------------------------------------------------------------------

@functools.lru_cache(maxsize=None)
def get_client(path: Path | str) -> ClientAPI:
    """Return a ChromaDB PersistentClient rooted at `path`.

    Chroma is safe to reuse across requests; caching avoids per-request init
    and sqlite file-lock churn on the underlying Chroma metadata store.
    """
    return chromadb.PersistentClient(path=str(path))


def get_episodic_collection(client: ClientAPI, *, embedding_function=None) -> Collection:
    """Return (or create) the 'episodic' collection.

    `embedding_function=None` → Chroma's default MiniLM embedder.
    Pass a fast deterministic function in tests to avoid network/model downloads.
    """
    kwargs: dict = {"name": "episodic"}
    if embedding_function is not None:
        kwargs["embedding_function"] = embedding_function
    return client.get_or_create_collection(**kwargs)


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------

def write_episodic(
    collection: Collection,
    *,
    npc_id: str,
    player_id: str,
    text: str,
    timestamp: str,
    importance: int,
) -> None:
    """Append one episodic event document to the collection."""
    collection.add(
        documents=[text],
        metadatas=[
            {
                "npc_id": npc_id,
                "player_id": player_id,
                "timestamp": timestamp,
                "importance": importance,
            }
        ],
        ids=[uuid.uuid4().hex],
    )


# ---------------------------------------------------------------------------
# Retrieve
# ---------------------------------------------------------------------------

def retrieve_episodic(
    collection: Collection,
    *,
    npc_id: str,
    player_id: str,
    query: str,
    k: int = 3,
) -> list[dict]:
    """Return up to k episodic events most similar to `query` for this (npc, player) pair.

    Filters by both npc_id and player_id so memories never bleed across NPCs or players.
    Returns [] gracefully when the collection has no matching documents.
    """
    # Guard: if the collection is entirely empty, query() raises in some Chroma versions.
    # The per-pair empty case (documents exist but none match this npc/player) is handled
    # by the try/except around query() below, which returns [].
    # The $and where-filter in query() does the real per-(npc,player) isolation.
    try:
        count = collection.count()
    except Exception:
        return []

    if count == 0:
        return []

    try:
        results = collection.query(
            query_texts=[query],
            n_results=k,
            where={"$and": [{"npc_id": {"$eq": npc_id}}, {"player_id": {"$eq": player_id}}]},
        )
    except Exception:
        # Chroma raises if n_results > number of matching docs in some versions;
        # an empty result set is fine — just return [].
        return []

    docs = results.get("documents", [[]])[0]
    metas = results.get("metadatas", [[]])[0]

    if not docs:
        return []

    return [
        {
            "text": doc,
            "importance": meta.get("importance"),
            "timestamp": meta.get("timestamp"),
        }
        for doc, meta in zip(docs, metas)
    ]
