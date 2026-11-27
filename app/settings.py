from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    service_name: str = "moderation"
    environment: str = "local"
    log_level: str = "INFO"
    log_json: bool = True

    database_url: SecretStr
    database_pool_size: int = 5
    database_echo: bool = False
    rabbitmq_url: SecretStr
    outbox_poll_seconds: float = Field(default=1.0, gt=0)

    # events-service: права на секцию (#18102, кеш в Redis) и объект жалобы (#18120).
    events_url: str = "http://events:8000"
    redis_url: SecretStr | None = None

    @field_validator("database_url")
    @classmethod
    def _require_asyncpg(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().startswith("postgresql+asyncpg://"):
            raise ValueError("DATABASE_URL must start with postgresql+asyncpg://")
        return value
