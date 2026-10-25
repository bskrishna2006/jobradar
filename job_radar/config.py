"""Configuration and environment variables for JobRadar."""

from __future__ import annotations

import os
from typing import List, Optional
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Telegram Bot
    telegram_bot_token: str = Field(default="", alias="TELEGRAM_BOT_TOKEN")
    telegram_webhook_secret: str = Field(default="", alias="TELEGRAM_WEBHOOK_SECRET")
    allowed_chat_ids_raw: str = Field(default="", alias="ALLOWED_CHAT_IDS")
    owner_chat_id: Optional[int] = Field(default=None, alias="OWNER_CHAT_ID")

    # MongoDB
    mongodb_uri: str = Field(default="mongodb://localhost:27017", alias="MONGODB_URI")
    mongodb_db_name: str = Field(default="job_radar", alias="MONGODB_DB_NAME")

    # LLM Settings
    llm_provider: str = Field(default="gemini", alias="LLM_PROVIDER")
    gemini_api_key: str = Field(default="", alias="GEMINI_API_KEY")
    gemini_model: str = Field(default="gemini-2.5-flash-lite", alias="GEMINI_MODEL")
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")

    # Embedding Settings
    embedding_provider: str = Field(default="local", alias="EMBEDDING_PROVIDER")

    # Job Sources
    freehire_base_url: str = Field(default="https://freehire.me/api/v1", alias="FREEHIRE_BASE_URL")
    greenhouse_companies_raw: str = Field(
        default="cloudflare,stripe,gitlab,automattic", alias="GREENHOUSE_COMPANIES"
    )
    lever_companies_raw: str = Field(default="postman,netflix,spotify", alias="LEVER_COMPANIES")
    ashby_companies_raw: str = Field(default="retool,linear,ramp", alias="ASHBY_COMPANIES")

    # GitHub Actions Dispatch
    github_dispatch_token: str = Field(default="", alias="GITHUB_DISPATCH_TOKEN")
    github_repo: str = Field(default="", alias="GITHUB_REPO")

    # Operation Mode
    dry_run: bool = Field(default=False, alias="DRY_RUN")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    # Rate Limiting (when ALLOWED_CHAT_IDS is empty)
    max_resume_uploads_per_day: int = 5
    max_jobs_calls_per_day: int = 5

    @field_validator("owner_chat_id", mode="before")
    @classmethod
    def parse_owner_chat_id(cls, v: Any) -> Optional[int]:
        if v is None:
            return None
        if isinstance(v, int):
            return v
        s = str(v).strip()
        if "#" in s:
            s = s.split("#")[0].strip()
        if not s or not (s.isdigit() or (s.startswith("-") and s[1:].isdigit())):
            return None
        return int(s)

    @field_validator("allowed_chat_ids_raw", mode="before")
    @classmethod
    def parse_allowed_raw(cls, v: Any) -> str:
        if v is None:
            return ""
        s = str(v).strip()
        if "#" in s:
            s = s.split("#")[0].strip()
        return s

    @property
    def allowed_chat_ids(self) -> List[int]:
        if not self.allowed_chat_ids_raw.strip():
            return []
        ids = []
        for item in self.allowed_chat_ids_raw.split(","):
            cleaned = item.strip()
            if cleaned and (cleaned.isdigit() or (cleaned.startswith("-") and cleaned[1:].isdigit())):
                ids.append(int(cleaned))
        return ids

    @property
    def greenhouse_companies(self) -> List[str]:
        return [c.strip() for c in self.greenhouse_companies_raw.split(",") if c.strip()]

    @property
    def lever_companies(self) -> List[str]:
        return [c.strip() for c in self.lever_companies_raw.split(",") if c.strip()]

    @property
    def ashby_companies(self) -> List[str]:
        return [c.strip() for c in self.ashby_companies_raw.split(",") if c.strip()]

    def validate_secrets(self, require_telegram: bool = True, require_mongo: bool = True, require_llm: bool = True) -> None:
        """Fail loudly at startup if a required secret is missing."""
        missing = []
        if require_telegram and not self.telegram_bot_token:
            missing.append("TELEGRAM_BOT_TOKEN")
        if require_mongo and not self.mongodb_uri:
            missing.append("MONGODB_URI")
        if require_llm:
            provider = self.llm_provider.lower()
            if provider == "gemini" and not self.gemini_api_key:
                missing.append("GEMINI_API_KEY (required for LLM_PROVIDER=gemini)")
            elif provider == "openai" and not self.openai_api_key:
                missing.append("OPENAI_API_KEY (required for LLM_PROVIDER=openai)")
            elif provider == "anthropic" and not self.anthropic_api_key:
                missing.append("ANTHROPIC_API_KEY (required for LLM_PROVIDER=anthropic)")

        if missing:
            raise ValueError(
                f"Missing required environment variables for JobRadar: {', '.join(missing)}. "
                "Please check your .env file or deployment secrets."
            )


# Singleton settings instance
settings = Settings()
