"""Fuzzy episodic memory via ChromaDB — recall only, never truth.

This module is for approximate, best-effort recall of past events.
It NEVER gates any action and NEVER supersedes SQLite.
The LLM never owns truth; this is a hint layer only.

All public functions accept an explicit Chroma collection so they are
unit-testable without a real PersistentClient (mirrors sqlite_store.py style).
"""

import asyncio
import functools
import uuid
from datetime import datetime, timezone
from pathlib import Path

import chromadb
import numpy as np
from chromadb import ClientAPI, Collection
from chromadb.utils.embedding_functions import DefaultEmbeddingFunction
from lightrag import LightRAG, QueryParam
from lightrag.llm.openai import openai_complete_if_cache
from lightrag.utils import EmbeddingFunc

from app.config import settings
from app.memory.stream import score_memories


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


def get_episodic_collection(
    client: ClientAPI, *, embedding_function=None
) -> Collection:
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
            where={
                "$and": [{"npc_id": {"$eq": npc_id}}, {"player_id": {"$eq": player_id}}]
            },
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


def retrieve_episodic_scored(
    collection: Collection,
    *,
    npc_id: str,
    player_id: str,
    query: str,
    k: int = 3,
    now: datetime | None = None,
) -> list[dict]:
    """Return top-k episodic events ranked by α·recency + β·importance + γ·relevance.

    Fetches up to k*4 candidates from Chroma (cosine similarity), then re-ranks
    using score_memories(). Falls back to [] on any error (same contract as retrieve_episodic).
    """
    if now is None:
        now = datetime.now(timezone.utc)

    try:
        count = collection.count()
    except Exception:
        return []

    if count == 0:
        return []

    fetch = max(k, min(k * settings.episodic_candidate_factor, count))
    try:
        results = collection.query(
            query_texts=[query],
            n_results=fetch,
            where={
                "$and": [{"npc_id": {"$eq": npc_id}}, {"player_id": {"$eq": player_id}}]
            },
            include=["documents", "metadatas", "distances"],
        )
    except Exception:
        return []

    docs = results.get("documents", [[]])[0]
    metas = results.get("metadatas", [[]])[0]
    dists = results.get("distances", [[]])[0]

    if not docs:
        return []

    candidates = [
        {
            "text": doc,
            "importance": meta.get("importance"),
            "timestamp": meta.get("timestamp"),
            "distance": dist,
        }
        for doc, meta, dist in zip(docs, metas, dists)
    ]

    ranked = score_memories(candidates, now)
    return [
        {
            "text": c["text"],
            "importance": c["importance"],
            "timestamp": c["timestamp"],
            "score": c["score"],
        }
        for c in ranked[:k]
    ]


# ---------------------------------------------------------------------------
# Beliefs collection (S7 reflection output)
# ---------------------------------------------------------------------------


def get_beliefs_collection(client: ClientAPI, *, embedding_function=None) -> Collection:
    """Return (or create) the 'beliefs' collection — stores NPC reflection conclusions."""
    kwargs: dict = {"name": "beliefs"}
    if embedding_function is not None:
        kwargs["embedding_function"] = embedding_function
    return client.get_or_create_collection(**kwargs)


def write_belief(
    collection: Collection,
    *,
    npc_id: str,
    player_id: str,
    text: str,
    timestamp: str,
) -> None:
    """Append one belief document (a reflection conclusion) to the collection."""
    collection.add(
        documents=[text],
        metadatas=[
            {
                "npc_id": npc_id,
                "player_id": player_id,
                "timestamp": timestamp,
                "importance": 9,
            }
        ],
        ids=[uuid.uuid4().hex],
    )


def retrieve_for_reflection(
    collection: Collection,
    *,
    npc_id: str,
    player_id: str,
    min_importance: int,
    limit: int = 10,
) -> list[dict]:
    """Fetch up to `limit` high-importance episodic events sorted by recency.

    Uses metadata filtering (no embedding query) — there is no natural query at
    reflection time. Returns [] gracefully on any error.
    """
    try:
        count = collection.count()
    except Exception:
        return []
    if count == 0:
        return []
    try:
        results = collection.get(
            where={
                "$and": [
                    {"npc_id": {"$eq": npc_id}},
                    {"player_id": {"$eq": player_id}},
                    {"importance": {"$gte": min_importance}},
                ]
            },
            include=["documents", "metadatas"],
        )
    except Exception:
        return []
    docs = results.get("documents", [])
    metas = results.get("metadatas", [])
    if not docs:
        return []
    pairs = sorted(
        zip(docs, metas),
        key=lambda x: x[1].get("timestamp", ""),
        reverse=True,
    )
    return [
        {"text": d, "importance": m.get("importance"), "timestamp": m.get("timestamp")}
        for d, m in pairs[:limit]
    ]


def retrieve_beliefs(
    collection: Collection,
    *,
    npc_id: str,
    player_id: str,
) -> list[dict]:
    """Fetch all beliefs for this (npc, player) pair, newest first."""
    try:
        count = collection.count()
    except Exception:
        return []
    if count == 0:
        return []
    try:
        results = collection.get(
            where={
                "$and": [
                    {"npc_id": {"$eq": npc_id}},
                    {"player_id": {"$eq": player_id}},
                ]
            },
            include=["documents", "metadatas"],
        )
    except Exception:
        return []
    docs = results.get("documents", [])
    metas = results.get("metadatas", [])
    if not docs:
        return []
    pairs = sorted(
        zip(docs, metas),
        key=lambda x: x[1].get("timestamp", ""),
        reverse=True,
    )
    return [{"text": d, "timestamp": m.get("timestamp")} for d, m in pairs]


# ---------------------------------------------------------------------------
# LightRAG lore store (per-NPC knowledge graph, S5)
# ---------------------------------------------------------------------------

_chroma_ef = DefaultEmbeddingFunction()


async def _embed(texts: list[str]) -> np.ndarray:
    return np.array(_chroma_ef(texts))


_ef = EmbeddingFunc(embedding_dim=384, max_token_size=8192, func=_embed)

_lore_rags: dict[str, LightRAG] = {}
_lore_locks: dict[str, asyncio.Lock] = {}


async def get_lore_rag(
    npc_id: str, lightrag_path, groq_api_key: str, groq_model: str
) -> LightRAG:
    """Return (or create) the LightRAG instance for this NPC."""
    if npc_id not in _lore_rags:
        if npc_id not in _lore_locks:
            _lore_locks[npc_id] = asyncio.Lock()
        async with _lore_locks[npc_id]:
            if npc_id not in _lore_rags:

                async def _llm(
                    prompt,
                    system_prompt=None,
                    history_messages=[],
                    keyword_extraction=False,
                    **kwargs,
                ):
                    return await openai_complete_if_cache(
                        groq_model,
                        prompt,
                        system_prompt=system_prompt,
                        history_messages=history_messages,
                        api_key=groq_api_key,
                        base_url="https://api.groq.com/openai/v1",
                        **kwargs,
                    )

                working_dir = str(Path(lightrag_path) / npc_id)
                Path(working_dir).mkdir(parents=True, exist_ok=True)
                rag = LightRAG(
                    working_dir=working_dir,
                    embedding_func=_ef,
                    llm_model_func=_llm,
                )
                await rag.initialize_storages()
                _lore_rags[npc_id] = rag
    return _lore_rags[npc_id]


async def seed_lore(
    npc_id: str, entries: list[dict], lightrag_path, groq_api_key: str, groq_model: str
) -> int:
    """Insert lore entries into the NPC's graph. Returns count inserted. Idempotent."""
    rag = await get_lore_rag(npc_id, lightrag_path, groq_api_key, groq_model)
    texts = [e["text"] for e in entries]
    if texts:
        await rag.ainsert(texts)
    return len(texts)


async def retrieve_lore(
    npc_id: str,
    query: str,
    *,
    history: list[dict],
    lightrag_path,
    groq_api_key: str,
    groq_model: str,
) -> str:
    """Retrieve lore context for this NPC. Returns '' on miss/error (best-effort)."""
    try:
        rag = await get_lore_rag(npc_id, lightrag_path, groq_api_key, groq_model)
        result = await rag.aquery(
            query,
            param=QueryParam(
                mode="naive",
                only_need_context=True,
                conversation_history=history,
                top_k=settings.lore_top_k,
            ),
        )
        return result if isinstance(result, str) else ""
    except Exception:
        return ""
