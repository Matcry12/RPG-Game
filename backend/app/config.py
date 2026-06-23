from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_BACKEND_DIR = Path(__file__).parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    groq_api_key: str = ""
    groq_model: str = "llama-3.3-70b-versatile"
    persona_dir: Path = _BACKEND_DIR / "data" / "personas"
    db_path: Path = _BACKEND_DIR / "data" / "npc.db"
    chroma_path: Path = _BACKEND_DIR / "data" / "chroma"
    tools_enabled: bool = True


settings = Settings()
