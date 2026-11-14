"""Минимальный клиент MOEX ISS."""

import httpx

from app.config import get_settings


class IssError(RuntimeError):
    pass


def _get(path: str) -> dict:
    s = get_settings()
    try:
        resp = httpx.get(f"{s.iss_base_url}{path}", params={"iss.meta": "off"}, timeout=s.iss_timeout)
        resp.raise_for_status()
        return resp.json()
    except (httpx.HTTPError, ValueError) as e:
        raise IssError(f"MOEX ISS недоступен: {e}") from e


def get_description(secid: str) -> dict[str, str]:
    """Базовые параметры выпуска из блока description: {name: value}."""
    block = _get(f"/securities/{secid}.json").get("description", {})
    cols = block.get("columns", [])
    try:
        i_name, i_value = cols.index("name"), cols.index("value")
    except ValueError as e:
        raise IssError("Неожиданный формат ответа ISS") from e
    return {row[i_name]: row[i_value] for row in block.get("data", [])}
