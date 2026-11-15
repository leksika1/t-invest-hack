"""Минимальный клиент MOEX ISS."""

import httpx

from app.config import get_settings


class IssError(RuntimeError):
    pass


def _get(path: str, params: dict | None = None) -> dict:
    s = get_settings()
    try:
        resp = httpx.get(f"{s.iss_base_url}{path}", params={"iss.meta": "off", **(params or {})},
                         timeout=s.iss_timeout, trust_env=False)
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


def rows(block: dict) -> list[dict]:
    columns = block.get("columns", [])
    data = block.get("data", [])
    if not isinstance(columns, list) or not isinstance(data, list) or any(len(r) != len(columns) for r in data):
        raise IssError("Неожиданный табличный формат ISS")
    return [dict(zip(columns, r)) for r in data]


def get_bondization(secid: str) -> dict:
    path = f"/securities/{secid}/bondization.json"
    result = _get(path, {"limit": "unlimited"})
    # Some ISS endpoints ignore unlimited; finish each block using its cursor.
    for name in ("coupons", "offers", "amortizations"):
        cursor = rows(result.get(name + ".cursor", {}))
        if not cursor:
            continue
        total = int(cursor[0].get("TOTAL", 0))
        block = result.get(name, {})
        while len(block.get("data", [])) < total:
            start = len(block.get("data", []))
            extra = _get(path, {"iss.only": name, "limit": 100, "start": start}).get(name, {})
            if (not extra.get("data") or extra.get("columns") != block.get("columns")
                    or all(row in block["data"] for row in extra["data"])):
                raise IssError(f"Неполный график ISS: {name}")
            block["data"].extend(extra["data"])
    return result


def get_issue(isin: str) -> dict:
    from app.iss.normalize import normalize

    description = _get(f"/securities/{isin}.json")
    desc = {r["name"]: r["value"] for r in rows(description.get("description", {}))}
    if not desc or desc.get("ISIN") != isin:
        raise IssError("Выпуск не найден в ISS")
    secid = desc.get("SECID") or isin
    warnings = []
    emitter = {}
    if desc.get("EMITTER_ID"):
        try:
            emitter = _get(f"/emitters/{desc['EMITTER_ID']}.json")
        except IssError:
            warnings.append("emitter_endpoint_unavailable")
    search = {}
    if not any(r.get("INN") for r in rows(emitter.get("emitter", {}))):
        try:
            search = _get("/securities.json", {"q": isin, "securities.columns": "secid,isin,emitent_title,emitent_inn"})
        except IssError:
            warnings.append("issuer_search_unavailable")
    bondization = get_bondization(secid)
    raw = {"description": description, "bondization": bondization, "emitter": emitter, "search": search}
    result = normalize(raw)
    result["warnings"].extend(warnings)
    return {"secid": secid, "raw": raw, **result}
