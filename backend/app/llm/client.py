"""Transport only. All model requests are authorized/logged by LLMGateway."""
from urllib.parse import urlsplit

import httpx

from app.config import get_settings


class LLMClient:
    def __init__(self, settings=None, client=None):
        self.settings = settings or get_settings()
        self.client = client

    @property
    def configured(self):
        return bool(self.settings.llm_api_key)

    def request(self, purpose, payload):
        embedding = purpose == 'embedding'
        base = (self.settings.embedding_base_url or self.settings.llm_base_url) if embedding else self.settings.llm_base_url
        key = (self.settings.embedding_api_key or self.settings.llm_api_key) if embedding else self.settings.llm_api_key
        headers = {'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'}
        if urlsplit(base).hostname == 'openrouter.ai':
            headers['X-Title'] = 'Bond Card MVP'
            if self.settings.public_base_url:
                headers['HTTP-Referer'] = self.settings.public_base_url
        path = '/embeddings' if embedding else '/chat/completions'
        if self.client is None:
            with httpx.Client(timeout=self.settings.llm_timeout_seconds,
                              proxy=self.settings.llm_proxy_url or None, trust_env=False) as client:
                response = client.post(base.rstrip('/') + path, headers=headers, json=payload)
        else:
            response = self.client.post(base.rstrip('/') + path, headers=headers, json=payload, timeout=self.settings.llm_timeout_seconds)
        response.raise_for_status()
        return response.json()
