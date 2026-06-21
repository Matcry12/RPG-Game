from langchain_groq import ChatGroq

from app.config import settings


def get_llm() -> ChatGroq:
    """Return a ChatGroq instance. Called per-request so it stays monkeypatchable in tests."""
    return ChatGroq(
        model=settings.groq_model,
        temperature=0.7,
        api_key=settings.groq_api_key or None,
    )
