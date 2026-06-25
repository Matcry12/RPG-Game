"""POST /world/seed — seed the LightRAG lore graph for each NPC persona."""

import json
from pathlib import Path

import yaml
from fastapi import APIRouter, HTTPException

from app.config import settings

router = APIRouter()

_LOREBOOK_PATH = (
    Path(__file__).parent.parent.parent.parent / "shared" / "lore" / "lorebook.json"
)


@router.post("/world/seed")
async def seed_world():
    from app.memory.vector_store import seed_lore

    if not _LOREBOOK_PATH.exists():
        raise HTTPException(
            status_code=500, detail=f"Lorebook not found: {_LOREBOOK_PATH}"
        )
    with open(_LOREBOOK_PATH) as f:
        lorebook = json.load(f)

    results = {}
    for persona_file in settings.persona_dir.glob("*.md"):
        npc_id = persona_file.stem
        content = persona_file.read_text()
        categories = []
        if content.startswith("---"):
            end = content.index("---", 3)
            categories = yaml.safe_load(content[3:end]).get("lore_categories", [])
        if not categories:
            continue
        filtered = [e for e in lorebook if e.get("category") in categories]
        count = await seed_lore(
            npc_id,
            filtered,
            lightrag_path=settings.lightrag_path,
            groq_api_key=settings.groq_api_key,
            groq_model=settings.groq_model,
        )
        results[npc_id] = {"seeded": count, "categories": categories}

    return {"status": "ok", "npcs": results}
