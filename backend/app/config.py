from functools import lru_cache
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    jwt_secret: str = Field(min_length=32)
    cookie_secure: bool = True
    app_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)
    trusted_proxy_hops: int = Field(default=0, ge=0)
    # Optional header set by the front proxy with the browser's IP (e.g. x-vercel-forwarded-for).
    client_ip_header: str = ""
    gemini_api_key: str = ""
    gemini_model: str = ""

    @field_validator("app_origins", mode="before")
    @classmethod
    def _split_origins(cls, value):
        if isinstance(value, str):
            return [o.strip().rstrip("/") for o in value.split(",") if o.strip()]
        return value

    @field_validator("database_url")
    @classmethod
    def _psycopg_driver(cls, value: str) -> str:
        # Hosting providers hand out postgres:// or postgresql:// URLs.
        for prefix in ("postgres://", "postgresql://"):
            if value.startswith(prefix):
                return "postgresql+psycopg://" + value[len(prefix):]
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
