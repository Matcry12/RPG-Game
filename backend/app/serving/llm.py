import logging

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_groq import ChatGroq
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from app.config import settings

logger = logging.getLogger(__name__)


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
        from app.tools.schemas import GiveReward, SetQuestState, UpdateDisposition  # avoid circular

        tools = [UpdateDisposition, GiveReward, SetQuestState]
        return primary.bind_tools(tools).with_fallbacks([fallback.bind_tools(tools)])

    return primary.with_fallbacks([fallback])


# ---------------------------------------------------------------------------
# Lore query rewrite + keyword extraction (mix mode — ADR-0015)
# ---------------------------------------------------------------------------


class LoreQuery(BaseModel):
    """Structured output for the history-aware lore query rewrite.

    Pre-populated keywords bypass LightRAG's own extraction (operate.py:4023); the rewritten
    query feeds both LightRAG's naive vector branch and episodic recall.
    """

    ll_keywords: list[str] = Field(
        default_factory=list,
        description="Low-level keywords: specific named entities (characters, locations, items, factions, quests).",
    )
    hl_keywords: list[str] = Field(
        default_factory=list,
        description="High-level keywords: broad themes (history, politics, war, trade, magic, rumours).",
    )
    rewritten_query: str = Field(
        description="The player's message rewritten as a self-contained lore query, with pronouns/vague references resolved using recent history.",
    )


_LORE_EXTRACT_SYSTEM = (
    "You analyse a player's message to an RPG NPC and prepare it for lore retrieval.\n"
    "Using the recent conversation history, resolve pronouns and vague references "
    "(e.g. 'he', 'that guy', 'those maps') to the explicit entity names they refer to.\n"
    "Rules:\n"
    "- Keep the player's original entity names; do NOT invent entities, places, or facts "
    "not present in the message or history.\n"
    "- ll_keywords: specific names — characters, locations, items, factions, quests.\n"
    "- hl_keywords: broad themes — history, politics, war, trade, magic, rumours.\n"
    "- rewritten_query: one self-contained search query with references resolved. "
    "If nothing needs resolving, restate the message plainly.\n"
    "If the message is too vague to resolve, leave keywords empty and set "
    "rewritten_query to the original message."
)


def _format_history(history: list[dict]) -> str:
    """Render recent turns as 'role: content' lines for the extraction prompt."""
    return "\n".join(f"{h.get('role', 'user')}: {h.get('content', '')}" for h in history)


async def extract_lore_query(message: str, history: list[dict]) -> LoreQuery | None:
    """Rewrite the message + extract keywords using recent history. Returns None on any error.

    Caller falls back to naive retrieval on raw message when this returns None — a failed
    extraction must never break a turn.
    """
    kira = ChatOpenAI(
        model=settings.kira_model,
        temperature=0.0,
        api_key=settings.kira_api_key or "no-key",
        base_url=settings.kira_base_url,
    )
    groq = ChatGroq(
        model=settings.groq_model,
        temperature=0.0,
        api_key=settings.groq_api_key or None,
    )
    primary, fallback = (kira, groq) if settings.llm_primary == "kira" else (groq, kira)
    llm = primary.with_structured_output(LoreQuery).with_fallbacks(
        [fallback.with_structured_output(LoreQuery)]
    )

    hist = _format_history(history)
    user = (f"Recent conversation:\n{hist}\n\n" if hist else "") + f"Player message: {message}"

    try:
        result = await llm.ainvoke(
            [SystemMessage(content=_LORE_EXTRACT_SYSTEM), HumanMessage(content=user)]
        )
        if isinstance(result, LoreQuery) and result.rewritten_query.strip():
            return result
        return None
    except Exception as exc:
        logger.warning("Lore query extraction failed (falling back to naive): %s", exc)
        return None
