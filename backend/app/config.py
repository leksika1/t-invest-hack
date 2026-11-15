from functools import lru_cache
from decimal import Decimal
from urllib.parse import urlsplit

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# OpenRouter, USD / 1M tokens. Проверить актуальность на openrouter.ai/models.
# Defaults apply only to this exact model; other models require explicit prices.
DEFAULT_LLM_MODEL = 'google/gemini-2.5-flash'
DEFAULT_LLM_PRICES = {
    DEFAULT_LLM_MODEL: {
        'llm_input_price_per_1m_usd': Decimal('0.30'),
        'llm_output_price_per_1m_usd': Decimal('2.50'),
    },
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", hide_input_in_errors=True)

    app_version: str = "0.1.0"
    database_url: str = "postgresql+psycopg://bonds:bonds@db:5432/bonds"

    llm_base_url: str = "https://openrouter.ai/api/v1"
    llm_api_key: str = ""
    # Credentials may be present: exclude the URL from repr/serialization.
    llm_proxy_url: str = Field('', repr=False, exclude=True)
    llm_model: str = DEFAULT_LLM_MODEL
    public_base_url: str = ""
    llm_daily_budget_usd: Decimal = Field(Decimal("10"), ge=0)
    llm_input_price_per_1m_usd: Decimal | None = Field(None, ge=0)
    llm_output_price_per_1m_usd: Decimal | None = Field(None, ge=0)
    embedding_input_price_per_1m_usd: Decimal | None = Field(None, ge=0)
    llm_timeout_seconds: float = Field(45, gt=0)
    llm_max_retries: int = Field(2, ge=0, le=5)
    llm_max_output_tokens: int = Field(4096, ge=64, le=16384)
    retrieval_top_k: int = Field(8, ge=1, le=30)
    retrieval_max_tokens: int = Field(10000, ge=256, le=32000)
    extraction_full_context_max_tokens: int = Field(150000, ge=0, le=500000)
    quote_fuzzy_threshold: float = Field(0.95, ge=0.9, le=1)
    extraction_min_confidence: float = Field(0.8, ge=0, le=1)
    admin_token: str = ""
    rate_limit_window_seconds: int = Field(60, ge=1)
    rate_limit_cards: int = Field(5, ge=1)
    rate_limit_uploads: int = Field(10, ge=1)
    rate_limit_collect: int = Field(5, ge=1)

    @field_validator('llm_proxy_url')
    @classmethod
    def valid_llm_proxy(cls, value):
        value = value.strip()
        if not value:
            return ''
        try:
            parsed = urlsplit(value)
            valid = (parsed.scheme in ('http', 'https', 'socks5', 'socks5h') and parsed.hostname
                     and parsed.path in ('', '/') and not parsed.query and not parsed.fragment
                     and not any(char.isspace() for char in value)
                     and (parsed.port is None or 1 <= parsed.port <= 65535))
        except ValueError:
            valid = False
        if not valid:
            raise ValueError('LLM_PROXY_URL: нужен http/https/socks5/socks5h URL с хостом, без пути, query и fragment') from None
        return value

    @field_validator('llm_input_price_per_1m_usd', 'llm_output_price_per_1m_usd', 'embedding_input_price_per_1m_usd', mode='before')
    @classmethod
    def blank_price(cls, value, info):
        if isinstance(value, str) and not value.strip():
            value = None
        if value is None:
            return DEFAULT_LLM_PRICES.get(info.data.get('llm_model'), {}).get(info.field_name)
        return value

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
    def llm_proxy_info(self) -> dict:
        # Never return host, userinfo or the original URL in public diagnostics.
        return {'llm_proxy': 'configured' if self.llm_proxy_url else 'none',
                'llm_proxy_scheme': urlsplit(self.llm_proxy_url).scheme if self.llm_proxy_url else None}

    @property
    def llm_configuration_error(self) -> str | None:
        if not self.llm_api_key.strip():
            return 'llm_not_configured'
        if self.llm_input_price_per_1m_usd is None or self.llm_output_price_per_1m_usd is None:
            return 'llm_prices_not_configured'
        return None

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
