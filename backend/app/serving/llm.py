from langchain_groq import ChatGroq

from app.config import settings


def _base_groq(temperature: float) -> ChatGroq:
    """Shared factory: build a ChatGroq with the configured model and given temperature."""
    return ChatGroq(
        model=settings.groq_model,
        temperature=temperature,
        api_key=settings.groq_api_key or None,
    )


def get_agent_llm(*, with_tools: bool = True):
    """Return the unified agent LLM (persona temp 0.7), tools bound when appropriate.

    S4 (ADR-0009) uses ONE agent for both tool decisions and the in-character reply, in a
    ReAct loop. Tools are bound only when ``with_tools`` AND ``settings.tools_enabled``;
    the loop's overflow turn calls with ``with_tools=False`` to force a final reply.

    Numeric tool args are declared ``int | str`` and coerced (ADR-0008) so Groq's
    server-side type validation does not 400 (``tool_use_failed``) on a stringified number.
    """
    base = _base_groq(temperature=0.7)
    if with_tools and settings.tools_enabled:
        from app.tools.schemas import GiveReward, StartQuest, UpdateDisposition  # avoid circular import

        return base.bind_tools([UpdateDisposition, GiveReward, StartQuest])
    return base
