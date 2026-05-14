"""
Pydantic application settings from .env (Mongo, JWT, email, etc.).

Named ``app.settings`` so it does not clash with the ``app.config`` package directory.
Stripe variables are read only in ``app.config.stripe`` via ``os.environ``, not here.
"""

from functools import lru_cache
from typing import Optional

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # MongoDB Settings
    MONGODB_URL: str
    MONGODB_DB_NAME: str

    # JWT Settings
    JWT_SECRET_KEY: str
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24  # 24 hours

    # Email Settings (sender addresses: EMAIL_FROM_HELLO / EMAIL_FROM_ALERTS / EMAIL_FROM_SUPPORT in env)
    POSTMARK_SERVER_API_TOKEN: str
    API_BASE_URL: Optional[str] = None

    # Azure Storage Settings
    AZURE_STORAGE_CONNECTION_STRING: Optional[str] = None
    AZURE_CONTAINER_NAME: Optional[str] = None

    # CORS Settings
    CORS_ORIGINS: str = "*"
    CORS_METHODS: str = "GET,POST,PUT,DELETE,OPTIONS"
    CORS_HEADERS: str = "*"

    # Image Processing Settings
    IMG_MAX_EDGE: int = 1024
    CACHE_DIR: str = "cache"

    class Config:
        env_file = ".env"
        case_sensitive = True


@lru_cache()
def get_settings() -> Settings:
    return Settings()
