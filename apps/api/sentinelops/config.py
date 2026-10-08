from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    database_url: str = "sqlite:///./sentinelops.db"
    demo_mode: bool = False
    jwt_secret: str = ""
    jwt_public_key: str = ""
    jwt_algorithm: str = "HS256"
    jwt_issuer: str = "sentinelops"
    jwt_audience: str = "sentinelops-api"
    access_token_minutes: int = Field(default=60, ge=1, le=480)
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    llm_mode: str = "fixture"
    openai_api_key: str = ""
    openai_model: str = "gpt-4.1-mini"
    live_tools_enabled: bool = False
    enable_pgvector: bool = False
    otel_console_export: bool = False
    eval_report_path: str = "../../evals/results/latest.json"
    worker_poll_seconds: float = Field(default=2, ge=0.1, le=60)
    job_lease_seconds: int = Field(default=120, ge=120, le=3600)

    def verification_key(self) -> str:
        if self.jwt_algorithm == "HS256":
            if len(self.jwt_secret) < 32:
                raise ValueError("JWT_SECRET must contain at least 32 characters")
            return self.jwt_secret
        if self.jwt_algorithm == "RS256" and self.jwt_public_key:
            return self.jwt_public_key.replace("\\n", "\n")
        raise ValueError("Configure HS256 JWT_SECRET or RS256 JWT_PUBLIC_KEY")


@lru_cache
def get_settings() -> Settings:
    return Settings()
