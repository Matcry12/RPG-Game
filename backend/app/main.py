from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.state import router as state_router
from app.api.talk import router as talk_router
from app.config import settings
from app.memory.sqlite_store import connect, init_db


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
