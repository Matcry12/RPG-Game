from langchain_groq import ChatGroq
from langchain_openai import ChatOpenAI

from app.config import settings


def get_agent_llm(*, with_tools: bool = True):
    """Return the unified agent LLM with Kira → Groq automatic failover.

    Kira (kira-mini-1.0) is tried first. On any error (rate limit, quota,
    outage) LangChain's .with_fallbacks() transparently retries with Groq
    (llama-3.3-70b-versatile). S4 (ADR-0009): one agent for both tool
    decisions and in-character reply.
    """
    kira = ChatOpenAI(
        model=settings.kira_model,
        temperature=0.7,
        api_key=settings.kira_api_key or "no-key",
        base_url=settings.kira_base_url,
    )
    groq = ChatGroq(
        model=settings.groq_model,
        temperature=0.7,
        api_key=settings.groq_api_key or None,
    )

    primary, fallback = (kira, groq) if settings.llm_primary == "kira" else (groq, kira)

    if with_tools and settings.tools_enabled:
        from app.tools.schemas import GiveReward, StartQuest, UpdateDisposition  # avoid circular

        tools = [UpdateDisposition, GiveReward, StartQuest]
        return primary.bind_tools(tools).with_fallbacks([fallback.bind_tools(tools)])

    return primary.with_fallbacks([fallback])
