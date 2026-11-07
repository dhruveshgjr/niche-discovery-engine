"""Application settings, loaded from environment variables and .env."""
from __future__ import annotations

from pathlib import Path

import httpx
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent

ALL_SOURCES = ["hacker_news", "reddit", "job_boards", "yc_rfs", "x", "facebook"]

DEFAULT_SEEDS = (
    "data extraction,missing API,manual bottleneck,web scraping,"
    "spreadsheet automation,copy paste data,SaaS integration,no API"
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- LM Studio (OpenAI-compatible local server) ---
    lm_studio_base_url: str = "http://localhost:1234/v1"
    lm_studio_api_key: str = "lm-studio"
    primary_model: str = "Qwen 27B Flash"
    small_model: str = "Qwen MoE 35B Flash"
    llm_timeout_seconds: float = 180.0
    llm_max_retries: int = 3
    primary_max_input_chars: int = 24_000
    small_max_input_chars: int = 16_000

    # --- Storage ---
    database_url: str = ""

    # --- Pipeline ---
    items_per_source: int = 50
    max_posts_to_extract: int = 250
    query_count: int = 20
    seed_keywords: str = DEFAULT_SEEDS
    enabled_sources: str = ",".join(ALL_SOURCES)
    demo_mode: bool | None = None

    # --- Politeness / HTTP ---
    request_delay_seconds: float = 1.0
    yc_delay_seconds: float = 5.0
    http_timeout_seconds: float = 25.0

    # --- Advanced scraper credentials (session cookies, no proxies) ---
    x_auth_token: str = ""
    x_ct0: str = ""
    x_graphql_query_id: str = ""
    facebook_cookie: str = ""
    facebook_doc_id: str = ""

    log_level: str = "INFO"

    @property
    def seed_keyword_list(self) -> list[str]:
        return [s.strip() for s in self.seed_keywords.split(",") if s.strip()]

    @property
    def enabled_source_list(self) -> list[str]:
        return [s.strip() for s in self.enabled_sources.split(",") if s.strip()]

    @property
    def resolved_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite:///{PROJECT_ROOT / 'data' / 'niche_discovery.db'}"


def resolve_demo_mode(settings: Settings) -> bool:
    """Explicit DEMO_MODE wins; otherwise demo iff LM Studio is unreachable/empty."""
    if settings.demo_mode is not None:
        return settings.demo_mode
    try:
        resp = httpx.get(f"{settings.lm_studio_base_url}/models", timeout=2.0, trust_env=False)
        if resp.status_code != 200:
            return True
        data = resp.json()
        if not isinstance(data, dict):
            return True
        return not data.get("data")
    except (httpx.HTTPError, ValueError):
        return True