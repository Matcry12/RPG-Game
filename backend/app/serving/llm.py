from langchain_groq import ChatGroq

from app.config import settings


def _base_groq(temperature: float) -> ChatGroq:
    """Shared factory: build a ChatGroq with the configured model and given temperature."""
    return ChatGroq(
        model=settings.groq_model,
        temperature=temperature,
        api_key=settings.groq_api_key or None,
    )


def get_llm() -> ChatGroq:
    """Return a ChatGroq instance for prose generation (temp 0.7).

    Called per-request so it stays monkeypatchable in tests.
    """
    return _base_groq(temperature=0.7)


def get_tool_llm():
    """Return an LLM for tool-decision calls (temp 0, deterministic tool calls).

    Using temperature=0 here is critical: at high temperature the model may emit
    in-character prose AND a tool call simultaneously, which Groq rejects as
    ``tool_use_failed`` (400). Temperature 0 keeps the response structured.

    When ``settings.tools_enabled`` is False the plain low-temp LLM is
    returned with no tools bound, so the model cannot propose any tool at all.
    """
    if not settings.tools_enabled:
        return _base_groq(temperature=0)

    from app.tools.schemas import GiveReward, StartQuest, UpdateDisposition  # local import avoids circular refs

    return _base_groq(temperature=0).bind_tools([UpdateDisposition, GiveReward, StartQuest])
