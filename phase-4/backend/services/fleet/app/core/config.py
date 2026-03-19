from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False)
    APP_VERSION: str = "1.0.0"
    ENVIRONMENT: str = "development"
    DATABASE_URL: str = "postgresql+asyncpg://nerve_app:nerve_app_secret@localhost:6432/nerve"
    REDIS_URL: str = "redis://:nerve_redis_secret@localhost:6379/0"
    ANTHROPIC_API_KEY: str = ""
    ANTHROPIC_MODEL: str = "claude-sonnet-4-20250514"
    ANTHROPIC_MAX_TOKENS: int = 2048
    AI_SIMILARITY_THRESHOLD: float = 0.75
    AI_MAX_CONTEXT_TOKENS: int = 4000
    PROMETHEUS_URL: str = "http://localhost:9090"
    JAEGER_URL: str = "http://localhost:16686"
    TEMPORAL_HOST: str = "localhost"
    TEMPORAL_PORT: int = 7233
    CATALOG_SERVICE_URL: str = "http://localhost:8001"
    ENFORCER_SERVICE_URL: str = "http://localhost:8002"
    ERROR_BUDGET_SERVICE_URL: str = "http://localhost:8005"
    MATURITY_SERVICE_URL: str = "http://localhost:8007"
    SECURITY_SERVICE_URL: str = "http://localhost:8008"
    CHAOS_SERVICE_URL: str = "http://localhost:8011"
    FLEET_SERVICE_URL: str = "http://localhost:8012"
    GATEWAY_URL: str = "http://localhost:8000"
    AWS_REGION: str = "us-east-1"
    AWS_ACCESS_KEY_ID: str = ""
    AWS_SECRET_ACCESS_KEY: str = ""
    S3_BUCKET_TECHDOCS: str = "nerve-techdocs"
    GITHUB_TOKEN: str = ""

@lru_cache
def get_settings() -> Settings:
    return Settings()
settings = get_settings()
