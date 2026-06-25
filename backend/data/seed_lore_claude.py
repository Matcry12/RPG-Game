"""Standalone lore seeder — uses Claude Code CLI (Haiku) for entity extraction.

Run from the backend directory:
    uv run python data/seed_lore_claude.py

This script is SUPPORT SCAFFOLDING and does not import the main app modules.
It writes LightRAG graph files into data/lightrag/<npc_id>/, which the main
app's retrieve_lore() reads at query-time (via Groq keyword extraction).

Why a separate script: LightRAG index-time entity/relationship extraction is
LLM-heavy and exhausts the Groq free tier for > a few entries. This script
uses `claude -p --model haiku` (Claude Code subscription, no API key needed)
as the seeding LLM only. The NPC persona voice at query-time is still Groq.
"""

import asyncio
import json
import sys
from pathlib import Path

import numpy as np
import yaml
from chromadb.utils.embedding_functions import DefaultEmbeddingFunction
from lightrag import LightRAG
from lightrag.utils import EmbeddingFunc

# ── paths (relative to this script's location = backend/data/) ──────────────
_DATA_DIR = Path(__file__).parent          # backend/data/
_BACKEND_DIR = _DATA_DIR.parent            # backend/
_REPO_ROOT = _BACKEND_DIR.parent           # RPG-Game/
_LOREBOOK = _REPO_ROOT / "shared" / "lore" / "lorebook.json"
_PERSONAS_DIR = _DATA_DIR / "personas"
_LIGHTRAG_DIR = _DATA_DIR / "lightrag"

# ── embedder — must match vector_store.py exactly (same dim/model) ──────────
_chroma_ef = DefaultEmbeddingFunction()


async def _embed(texts: list[str]) -> np.ndarray:
    return np.array(_chroma_ef(texts))


_ef = EmbeddingFunc(embedding_dim=384, max_token_size=8192, func=_embed)


# ── LLM via claude CLI subprocess ────────────────────────────────────────────
async def _claude_haiku_llm(
    prompt: str,
    system_prompt: str | None = None,
    history_messages: list = [],
    keyword_extraction: bool = False,
    **kwargs,
) -> str:
    """LightRAG-compatible llm_model_func backed by `claude -p --model haiku`."""
    cmd = ["claude", "-p", "--model", "haiku", "--output-format", "text"]

    parts = []
    if system_prompt:
        parts.append(system_prompt)
    for msg in history_messages:
        role = msg.get("role", "user").capitalize()
        parts.append(f"{role}: {msg.get('content', '')}")
    parts.append(prompt)
    stdin_text = "\n\n".join(parts)

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate(input=stdin_text.encode())
    if proc.returncode != 0:
        raise RuntimeError(
            f"claude CLI error (rc={proc.returncode}): {stderr.decode()[:300]}"
        )
    return stdout.decode().strip()


# ── persona parsing ───────────────────────────────────────────────────────────
def _persona_categories(npc_id: str) -> list[str]:
    """Return lore_categories from a persona file's YAML frontmatter."""
    path = _PERSONAS_DIR / f"{npc_id}.md"
    if not path.exists():
        return []
    content = path.read_text()
    if not content.startswith("---"):
        return []
    try:
        end = content.index("---", 3)
        fm = yaml.safe_load(content[3:end])
        return fm.get("lore_categories", [])
    except (ValueError, yaml.YAMLError):
        return []


# ── seeder ────────────────────────────────────────────────────────────────────
async def seed_npc(npc_id: str, entries: list[dict]) -> int:
    """Build/update the LightRAG graph for one NPC. Returns count inserted."""
    working_dir = str(_LIGHTRAG_DIR / npc_id)
    Path(working_dir).mkdir(parents=True, exist_ok=True)

    rag = LightRAG(
        working_dir=working_dir,
        embedding_func=_ef,
        llm_model_func=_claude_haiku_llm,
    )
    await rag.initialize_storages()

    texts = [e["text"] for e in entries]
    if texts:
        await rag.ainsert(texts)
    return len(texts)


async def main() -> None:
    if not _LOREBOOK.exists():
        print(f"ERROR: lorebook not found at {_LOREBOOK}", file=sys.stderr)
        sys.exit(1)

    lorebook: list[dict] = json.loads(_LOREBOOK.read_text())
    print(f"Loaded {len(lorebook)} lore entries from {_LOREBOOK}")

    persona_files = list(_PERSONAS_DIR.glob("*.md"))
    if not persona_files:
        print(f"No persona files found in {_PERSONAS_DIR}", file=sys.stderr)
        sys.exit(1)

    total = 0
    for persona_file in persona_files:
        npc_id = persona_file.stem
        categories = _persona_categories(npc_id)
        if not categories:
            print(f"  [{npc_id}] no lore_categories in frontmatter — skipping")
            continue

        filtered = [e for e in lorebook if e.get("category") in categories]
        print(f"  [{npc_id}] seeding {len(filtered)} entries (categories: {categories}) …", end=" ", flush=True)
        count = await seed_npc(npc_id, filtered)
        print(f"done ({count} inserted)")
        total += count

    print(f"\nSeeding complete — {total} entries across {len(persona_files)} NPC(s).")
    print(f"Graph files written to: {_LIGHTRAG_DIR}")


if __name__ == "__main__":
    asyncio.run(main())
