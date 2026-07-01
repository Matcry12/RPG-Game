from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_BACKEND_DIR = Path(__file__).parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # LLM provider order — set LLM_PRIMARY to "kira" or "groq"
    # Primary is tried first; the other is the automatic fallback on rate limits/errors.
    llm_primary: str = "groq"

    # Kira AI (OpenAI-compatible)
    kira_api_key: str = ""
    kira_model: str = "kira-mini-1.0"
    kira_base_url: str = "https://kiraai.vn/api/v1"

    # Groq
    groq_api_key: str = ""
    groq_model: str = "llama-3.3-70b-versatile"

    # LangSmith tracing — set to see full prompts/responses in smith.langchain.com
    langchain_tracing_v2: bool = False
    langchain_api_key: str = ""
    langchain_project: str = "rpg-npc"
    persona_dir: Path = _BACKEND_DIR / "data" / "personas"
    db_path: Path = _BACKEND_DIR / "data" / "npc.db"
    chroma_path: Path = _BACKEND_DIR / "data" / "chroma"
    checkpoint_path: Path = _BACKEND_DIR / "data" / "checkpoints.db"
    tools_enabled: bool = True
    semantic_routing: bool = False        # set True to use fastembed embeddings; False uses heuristic (default for tests)
    lightrag_path: Path = _BACKEND_DIR / "data" / "lightrag"
    grounding_gate: bool = True
    lore_context_min_chars: int = 100
    # Agent loop
    agent_max_turns: int = 3
    history_window: int = 2              # raw turns injected into prompt (scored episodic covers older context)
    lore_history_window: int = 6         # messages passed to LightRAG for context

    # Retrieval
    episodic_memory: bool = True         # set false to disable episodic write+recall entirely (baseline ablation)
    episodic_recall_k: int = 3           # top-k episodic events per turn
    episodic_candidate_factor: int = 4   # fetch k*factor candidates before re-ranking
    reflection_event_limit: int = 10     # max events passed to the reflection prompt
    lore_top_k: int = 4                  # top-k nodes LightRAG returns (capped for NPC dialogue)
    lore_chunk_top_k: int = 6            # max chunks LightRAG returns per query
    lore_max_total_tokens: int = 2000    # hard ceiling on lore context size injected into prompt
    lore_query_mode: str = "naive"       # "naive" (raw query, 0 extra LLM) | "mix" (history-aware rewrite + graph)
    lore_rewrite_history_window: int = 4 # recent turns fed to the mix-mode query-rewrite prompt

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
