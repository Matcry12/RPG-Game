from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_BACKEND_DIR = Path(__file__).parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    groq_api_key: str = ""
    groq_model: str = "llama-3.3-70b-versatile"
    # llama-3.3-70b-versatile 
    # llama-3.1-8b-instant

    # LangSmith tracing — set to see full prompts/responses in smith.langchain.com
    langchain_tracing_v2: bool = False
    langchain_api_key: str = ""
    langchain_project: str = "rpg-npc"
    persona_dir: Path = _BACKEND_DIR / "data" / "personas"
    db_path: Path = _BACKEND_DIR / "data" / "npc.db"
    chroma_path: Path = _BACKEND_DIR / "data" / "chroma"
    checkpoint_path: Path = _BACKEND_DIR / "data" / "checkpoints.db"
    tools_enabled: bool = True
    lightrag_path: Path = _BACKEND_DIR / "data" / "lightrag"
    grounding_gate: bool = True
    lore_context_min_chars: int = 100
    memory_stream: bool = False

    # Agent loop
    agent_max_turns: int = 3
    history_window: int = 10             # messages from checkpoint injected into prompt
    lore_history_window: int = 6         # messages passed to LightRAG for context

    # Retrieval
    episodic_recall_k: int = 3           # top-k episodic events per turn
    episodic_candidate_factor: int = 4   # fetch k*factor candidates before re-ranking
    reflection_event_limit: int = 10     # max events passed to the reflection prompt
    lore_top_k: int = 10                 # top-k nodes LightRAG returns

    # Memory stream scoring weights (S6, Park et al. 2023) — must sum to 1
    stream_alpha: float = 0.35           # recency weight
    stream_beta: float = 0.35            # importance weight
    stream_gamma: float = 0.30           # relevance weight
    stream_decay: float = 0.1            # per-hour exponential decay (half-life ≈ 7 h)

    # S7 reflection — importance knobs
    reflection: bool = False
    reflection_threshold: int = 20       # accumulated importance before a reflection fires
    reflection_min_importance: int = 5   # min importance to include in reflection prompt
    importance_plain_turn: int = 0       # plain turns carry no significance
    importance_start_quest: int = 5
    importance_give_reward: int = 7
    importance_max: int = 10             # cap for UpdateDisposition = min(max, abs(delta))


settings = Settings()
