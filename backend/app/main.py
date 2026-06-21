from fastapi import FastAPI

from app.api.talk import router as talk_router

app = FastAPI(title="NPC Agent Service")

app.include_router(talk_router)
