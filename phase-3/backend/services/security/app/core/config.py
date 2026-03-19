from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False)
    APP_VERSION: str = "1.0.0"
    ENVIRONMENT: str = "development"
    DATABASE_URL: str = "postgresql+asyncpg://nerve_app:nerve_app_secret@localhost:6432/nerve"
    REDIS_URL: str = "redis://:nerve_redis_secret@localhost:6379/0"
    NEO4J_URI: str = "bolt://localhost:7687"
    NEO4J_USER: str = "neo4j"
    NEO4J_PASSWORD: str = "nerve_neo4j_secret"
    NEO4J_DATABASE: str = "neo4j"
    NEO4J_MAX_CONNECTION_POOL_SIZE: int = 50
    CACHE_TTL_BLAST_RADIUS: int = 60
    PROMETHEUS_URL: str = "http://localhost:9090"
    JAEGER_URL: str = "http://localhost:16686"
    SLACK_WEBHOOK_URL: str = ""
    AWS_REGION: str = "us-east-1"
    AWS_ACCESS_KEY_ID: str = ""
    AWS_SECRET_ACCESS_KEY: str = ""
    COST_ANOMALY_STD_DEVS: float = 2.0
    CATALOG_SERVICE_URL: str = "http://localhost:8001"

@lru_cache
def get_settings() -> Settings:
    return Settings()
settings = get_settings()
