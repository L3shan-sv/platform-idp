from functools import lru_cache
from typing import List
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False)

    APP_VERSION: str = "1.0.0"
    ENVIRONMENT: str = "development"
    DEBUG: bool = False

    # Database — always connect through PgBouncer (port 6432)
    DATABASE_URL: str = "postgresql+asyncpg://nerve_app:nerve_app_secret@localhost:6432/nerve"
    # Alembic migrations only — direct PostgreSQL (port 5432), bypasses PgBouncer
    DATABASE_URL_MIGRATIONS: str = "postgresql+psycopg2://nerve:nerve_dev_secret@localhost:5432/nerve"

    REDIS_URL: str = "redis://:nerve_redis_secret@localhost:6379/0"

    NEO4J_URI: str = "bolt://localhost:7687"
    NEO4J_USER: str = "neo4j"
    NEO4J_PASSWORD: str = "nerve_neo4j_secret"

    VAULT_URL: str = "http://localhost:8200"
    VAULT_TOKEN: str = "nerve-vault-dev-token"

    TEMPORAL_HOST: str = "localhost"
    TEMPORAL_PORT: int = 7233

    OPA_URL: str = "http://localhost:8181"

    JWT_SECRET_KEY: str = "nerve-jwt-dev-secret-change-in-production"
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    JWT_REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    CORS_ORIGINS: List[str] = ["http://localhost:5173", "http://localhost:3000"]

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def parse_cors(cls, v):
        if isinstance(v, str):
            return [o.strip() for o in v.split(",")]
        return v

    RATE_LIMIT_DEFAULT: str = "100/minute"
    RATE_LIMIT_DEPLOY: str = "10/minute"

    OTEL_EXPORTER_OTLP_ENDPOINT: str = "http://localhost:4317"

    ANTHROPIC_API_KEY: str = ""
    ANTHROPIC_MODEL: str = "claude-sonnet-4-20250514"

    GITHUB_TOKEN: str = ""
    GITHUB_ORG: str = ""
    GITHUB_RATE_LIMIT_BUFFER: int = 100

    NERVE_INTERNAL_TOKEN: str = "nerve-internal-dev-token"

    # Service-to-service URLs
    CATALOG_SERVICE_URL: str = "http://localhost:8001"
    ENFORCER_SERVICE_URL: str = "http://localhost:8002"
    PIPELINE_SERVICE_URL: str = "http://localhost:8003"
    BLAST_RADIUS_SERVICE_URL: str = "http://localhost:8004"
    ERROR_BUDGET_SERVICE_URL: str = "http://localhost:8005"
    COST_SERVICE_URL: str = "http://localhost:8006"
    MATURITY_SERVICE_URL: str = "http://localhost:8007"
    SECURITY_SERVICE_URL: str = "http://localhost:8008"

    # Cache TTLs (seconds)
    CACHE_TTL_CATALOG: int = 30
    CACHE_TTL_BLAST_RADIUS: int = 60
    CACHE_TTL_DORA: int = 60
    CACHE_TTL_COST: int = 300
    CACHE_TTL_MATURITY: int = 30


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
