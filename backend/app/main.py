import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.state import router as state_router
from app.api.talk import router as talk_router
from app.api.world import router as world_router
from app.config import settings
from app.memory.sqlite_store import connect, init_db

# LangChain reads tracing config from os.environ, not from settings directly.
# Propagate here so values from .env are picked up.
if settings.langchain_tracing_v2:
    os.environ["LANGCHAIN_TRACING_V2"] = "true"
    os.environ["LANGCHAIN_API_KEY"] = settings.langchain_api_key
    os.environ["LANGCHAIN_PROJECT"] = settings.langchain_project


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure DB schema + demo seed exist before the first request.
    conn = connect(settings.db_path)
    try:
        init_db(conn)
    finally:
        conn.close()
    yield


app = FastAPI(title="NPC Agent Service", lifespan=lifespan)

app.include_router(talk_router)
app.include_router(state_router)
app.include_router(world_router)


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}
