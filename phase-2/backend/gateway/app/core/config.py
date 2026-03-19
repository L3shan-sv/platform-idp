from functools import lru_cache
from typing import List
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False)
    APP_VERSION: str = "1.0.0"
    ENVIRONMENT: str = "development"
    DATABASE_URL: str = "postgresql+asyncpg://nerve_app:nerve_app_secret@localhost:6432/nerve"
    REDIS_URL: str = "redis://:nerve_redis_secret@localhost:6379/0"
    OPA_URL: str = "http://localhost:8181"
    JWT_SECRET_KEY: str = "nerve-jwt-dev-secret-change-in-production"
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    CORS_ORIGINS: List[str] = ["http://localhost:5173","http://localhost:3000"]
    OTEL_EXPORTER_OTLP_ENDPOINT: str = "http://localhost:4317"
    TEMPORAL_HOST: str = "localhost"
    TEMPORAL_PORT: int = 7233
    NERVE_INTERNAL_TOKEN: str = "nerve-internal-dev-token"
    CATALOG_SERVICE_URL: str = "http://localhost:8001"
    ENFORCER_SERVICE_URL: str = "http://localhost:8002"
    PIPELINE_SERVICE_URL: str = "http://localhost:8003"

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def parse_cors(cls, v):
        return [o.strip() for o in v.split(",")] if isinstance(v, str) else v

    @property
    def temporal_address(self) -> str:
        return f"{self.TEMPORAL_HOST}:{self.TEMPORAL_PORT}"

@lru_cache
def get_settings() -> Settings:
    return Settings()
settings = get_settings()
