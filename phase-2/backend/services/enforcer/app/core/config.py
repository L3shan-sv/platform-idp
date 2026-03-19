from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False)
    APP_VERSION: str = "1.0.0"
    ENVIRONMENT: str = "development"
    DATABASE_URL: str = "postgresql+asyncpg://nerve_app:nerve_app_secret@localhost:6432/nerve"
    REDIS_URL: str = "redis://:nerve_redis_secret@localhost:6379/0"
    OPA_URL: str = "http://localhost:8181"
    CATALOG_SERVICE_URL: str = "http://localhost:8001"
    VAULT_URL: str = "http://localhost:8200"
    VAULT_TOKEN: str = "nerve-vault-dev-token"
    VAULT_MOUNT: str = "secret"
    GITHUB_TOKEN: str = ""
    GITHUB_ORG: str = ""
    TERRAFORM_CLOUD_TOKEN: str = ""
    TEMPLATE_REPO_PATH: str = "/templates"

@lru_cache
def get_settings() -> Settings:
    return Settings()

settings = get_settings()
