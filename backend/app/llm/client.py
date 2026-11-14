"""Тонкая обёртка над OpenAI-совместимым API.

Остальной код работает только через LLMClient, чтобы провайдера можно было сменить
в одном месте. Клиент создаётся лениво и только при заданном LLM_API_KEY.
"""

from openai import OpenAI

from app.config import Settings, get_settings


class LLMNotConfigured(RuntimeError):
    pass


class LLMClient:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._client: OpenAI | None = None

    @property
    def configured(self) -> bool:
        return bool(self._settings.llm_api_key)

    def _get_client(self) -> OpenAI:
        if not self.configured:
            raise LLMNotConfigured("LLM_API_KEY is not set")
        if self._client is None:
            self._client = OpenAI(
                api_key=self._settings.llm_api_key,
                base_url=self._settings.llm_base_url or None,
            )
        return self._client

    def complete_json(self, system: str, user: str) -> str:
        """Запрос к модели с ответом в формате JSON. Возвращает сырой текст ответа."""
        response = self._get_client().chat.completions.create(
            model=self._settings.llm_model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            response_format={"type": "json_object"},
            temperature=0,
        )
        return response.choices[0].message.content or ""
