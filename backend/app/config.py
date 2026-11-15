from functools import lru_cache

from pydantic import Field
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

    files_dir: str = "/data/files"
    max_upload_bytes: int = Field(30 * 1024 * 1024, gt=0)
    max_upload_files: int = Field(5, ge=1, le=20)
    max_pdf_pages: int = Field(1000, ge=1)
    max_archive_bytes: int = Field(100 * 1024 * 1024, gt=0)
    max_archive_unpacked_bytes: int = Field(150 * 1024 * 1024, gt=0)
    max_archive_entries: int = Field(100, ge=1)
    max_archive_ratio: int = Field(100, ge=1)
    ocr_timeout_seconds: int = Field(60, ge=1)
    document_parse_timeout_seconds: int = Field(300, ge=1)
    ocr_dpi: int = Field(150, ge=72, le=300)
    max_page_pixels: int = Field(25_000_000, ge=1)
    collector_user_agent: str = "BondCardMVP/0.1 (+https://tinvest.leksika1.ru)"
    collector_delay_seconds: float = Field(1.0, ge=1.0)
    collector_timeout_seconds: float = Field(25, gt=0)
    collector_cache_seconds: int = Field(3600, ge=1)
    collector_max_documents: int = Field(20, ge=1, le=100)
    collector_max_pages: int = Field(12, ge=1, le=50)
    edisclosure_api_url: str = ""
    edisclosure_api_key: str = ""
    embedding_model: str = ""
    embedding_base_url: str = ""
    embedding_api_key: str = ""
    embedding_dim: int = Field(1024, ge=1, le=2000)
    worker_enabled: bool = True
    job_poll_seconds: float = Field(1, gt=0)
    job_lease_seconds: int = Field(180, ge=30)
    job_max_attempts: int = Field(3, ge=1)

    # Через запятую; нужно, только если фронтенд ходит в API с другого домена
    cors_origins: str = ""

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
