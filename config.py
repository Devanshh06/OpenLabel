"""
OpenLabel — Configuration & Settings
Loads environment variables with validation via pydantic-settings.
"""

from functools import lru_cache
from pydantic_settings import BaseSettings
from typing import List
import os


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # ── Supabase ──────────────────────────────────────────
    supabase_url: str
    supabase_anon_key: str
    supabase_service_role_key: str

    # ── Google Gemini AI ──────────────────────────────────
    gemini_api_key: str

    # Member 3 optional Gemini model override (read by `ai_logic/` directly)
    gemini_model: str | None = None

    # ── Server ────────────────────────────────────────────
    app_host: str = "0.0.0.0"
    # Render dynamically assigns the external port via `PORT`.
    # If the app is started with `python main.py` (not `uvicorn ... --port $PORT`),
    # we still need to listen on the Render-provided `PORT` to avoid 502 Bad Gateway.
    app_port: int = int(os.getenv("PORT", "8000"))
    app_debug: bool = False

    # ── CORS ──────────────────────────────────────────────
    cors_origins: str = "*"

    @property
    def cors_origin_list(self) -> List[str]:
        """Parse comma-separated CORS origins into a list."""
        return [origin.strip() for origin in self.cors_origins.split(",")]

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False


@lru_cache()
def get_settings() -> Settings:
    """Cached singleton for app settings."""
    return Settings()
