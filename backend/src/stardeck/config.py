from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration, read from environment variables prefixed with STARDECK_."""

    model_config = SettingsConfigDict(env_prefix="STARDECK_", env_file=".env", extra="ignore")

    # Any OpenAI-compatible endpoint: Ollama, llama.cpp server, vLLM, LM Studio.
    llm_base_url: str = "http://localhost:11434/v1"
    llm_model: str = "qwen3.5:9b"
    llm_api_key: str = "not-needed"
    llm_timeout_s: float = 180.0
    llm_temperature: float = 0.1
    llm_max_tokens: int = 4096
    # Reasoning ("thinking") models can spend thousands of tokens before answering. Stardeck's
    # calls are narrow and schema-constrained, so "off" is faster and, per the evals, as accurate.
    # "off" sends the switches used by LM Studio, vLLM, llama.cpp and Ollama; "server" sends
    # nothing (for strict OpenAI-compatible APIs that reject unknown fields).
    llm_thinking: Literal["off", "on", "server"] = "off"

    # Everything Stardeck writes: metadata DB, encryption key, uploaded files, demo DB.
    data_dir: Path = Path("data")
    # Defaults to SQLite inside data_dir; any SQLAlchemy URL works (e.g. Postgres).
    database_url: str | None = None
    # Encrypts stored connection passwords. Generated into data_dir if not provided.
    secret_key: str | None = None
    # Add the built-in "Retail demo" source on first start.
    seed_demo: bool = True

    # Maps: OpenStreetMap tiles in the browser; place names -> coordinates via Nominatim (cached).
    # Only place names and map views leave the network. Set geocoder to "off" to disable lookups,
    # or point the URLs at self-hosted servers.
    map_tiles_url: str = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
    map_attribution: str = "© OpenStreetMap contributors"
    geocoder: Literal["nominatim", "off"] = "nominatim"
    geocoder_url: str = "https://nominatim.openstreetmap.org"

    query_timeout_s: int = 30
    max_upload_mb: int = 200

    cors_origins: list[str] = ["http://localhost:5173"]

    # Optional shared password. When set, every API call needs a signed-in session; useful when
    # Stardeck is reachable from other machines. Leave unset for single-user local use.
    password: str | None = None
    session_hours: int = 24 * 14

    @property
    def metadata_url(self) -> str:
        return self.database_url or f"sqlite:///{(self.data_dir / 'stardeck.db').resolve()}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
