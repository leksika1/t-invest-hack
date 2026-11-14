from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_version: str = "0.1.0"
    database_url: str = "postgresql+psycopg://bonds:bonds@db:5432/bonds"

    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""

    iss_base_url: str = "https://iss.moex.com/iss"
    iss_timeout: float = 10.0

    # Через запятую; нужно, только если фронтенд ходит в API с другого домена
    cors_origins: str = ""

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
