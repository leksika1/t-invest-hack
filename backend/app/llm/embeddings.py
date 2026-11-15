"""Stage 2: no paid calls until the budgeted gateway exists."""
from typing import Protocol


class EmbeddingGateway(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]] | None: ...


class DisabledEmbeddingGateway:
    def embed(self, texts: list[str]) -> None:
        return None
