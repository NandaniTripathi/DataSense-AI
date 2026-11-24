"""Central application settings, loaded from environment variables / .env."""
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    app_name: str = "DataSense AI"
    groq_api_key: str = Field(default="", alias="GROQ_API_KEY")
    # Must support tool calling. See https://console.groq.com/docs/tool-use/overview
    groq_model: str = Field(default="openai/gpt-oss-120b", alias="GROQ_MODEL")
    # "groq" for the real thing; "mock" is only used by automated tests.
    llm_provider: str = Field(default="groq", alias="LLM_PROVIDER")

    cors_origins: str = Field(
        default="http://localhost:5173,http://127.0.0.1:5173", alias="CORS_ORIGINS"
    )
    max_upload_mb: int = Field(default=25, alias="MAX_UPLOAD_MB")
    chat_rate_limit_per_min: int = Field(default=20, alias="CHAT_RATE_LIMIT_PER_MIN")

    data_dir: Path = Field(default=BACKEND_DIR / "data", alias="DATA_DIR")

    # Query-engine safety limits
    query_timeout_seconds: float = 10.0
    query_max_rows: int = 5000
    llm_preview_rows: int = 40
    max_agent_steps: int = 6
    history_turns: int = 6

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def datasets_dir(self) -> Path:
        return self.data_dir / "datasets"

    @property
    def metadata_db_url(self) -> str:
        return f"sqlite:///{(self.data_dir / 'datasense.db').as_posix()}"

    @property
    def llm_configured(self) -> bool:
        return self.llm_provider == "mock" or bool(self.groq_api_key.strip())


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.datasets_dir.mkdir(parents=True, exist_ok=True)
    return s
