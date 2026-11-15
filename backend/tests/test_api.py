import pytest
from fastapi.testclient import TestClient

from app import main

client = TestClient(main.app)


def test_history_includes_snapshot_title_and_field_status_counts():
    from types import SimpleNamespace
    from datetime import datetime, timezone
    import uuid
    from app.issues.router import database
    identifier = uuid.uuid4()
    timestamp = datetime.now(timezone.utc)
    card = SimpleNamespace(id=identifier, issue_id=uuid.uuid4(), input_snapshot={'header': {'name': 'Выпуск из снимка'}}, version=2, status='ready', created_at=timestamp, updated_at=timestamp)
    session = SimpleNamespace(scalar=lambda query: 1, scalars=lambda query: [card], get=lambda *args: SimpleNamespace(isin='RU000A10CS75'), execute=lambda query: [(identifier, 'extracted', 7), (identifier, 'needs_review', 8)])
    main.app.dependency_overrides[database] = lambda: session
    try:
        result = client.get('/api/cards').json()
    finally:
        del main.app.dependency_overrides[database]
    assert result['total'] == 1
    assert result['items'][0]['header']['name'] == 'Выпуск из снимка'
    assert result['items'][0]['field_statuses'] == {'extracted': 7, 'needs_review': 8}


def test_health(monkeypatch):
    monkeypatch.setattr(main, "check_db", lambda: True)
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"status", "db", "llm", "llm_mode", "version", "llm_proxy", "llm_proxy_scheme"}
    assert body["status"] == "ok"
    assert body["db"] == "ok"


def test_health_db_down(monkeypatch):
    monkeypatch.setattr(main, "check_db", lambda: False)
    body = client.get("/api/health").json()
    assert body["status"] == "degraded"
    assert body["db"] == "error"


@pytest.mark.parametrize('key,model,prices,expected', [
    ('', 'google/gemini-2.5-flash', ('', ''), 'not_configured'),
    ('fake', 'google/gemini-2.5-flash', ('', ''), 'configured'),
    ('fake', 'other/model', ('', ''), 'not_configured'),
    ('fake', 'other/model', ('1', ''), 'not_configured'),
    ('fake', 'other/model', ('0', '0'), 'configured'),
])
def test_health_requires_key_and_both_model_prices(monkeypatch, key, model, prices, expected):
    from app.config import Settings
    monkeypatch.setattr(main, 'check_db', lambda: True)
    monkeypatch.setattr(main, 'settings', Settings(llm_api_key=key, llm_model=model,
                        llm_input_price_per_1m_usd=prices[0], llm_output_price_per_1m_usd=prices[1], _env_file=None))
    body = client.get('/api/health').json()
    assert set(body) == {'status', 'db', 'llm', 'llm_mode', 'version', 'llm_proxy', 'llm_proxy_scheme'}
    assert body['llm'] == expected


@pytest.mark.parametrize("isin", ["RU000A10AXF7", "ru000a10axf7", " RU000A0JX0J2 "])
def test_card_valid_isin(isin):
    from app.cards.schema import CardRequest, FIELDS
    assert CardRequest(isin=isin).isin == isin.strip().upper()
    assert len(FIELDS) == 15


@pytest.mark.parametrize(
    "isin", ["", "US0378331005", "RU000A10AXF", "RU000A10AXFX", "RU000A10AXF77", "RU00-A10AXF7"]
)
def test_card_invalid_isin(isin):
    r = client.post("/api/cards", json={"isin": isin})
    assert r.status_code == 422


def test_iss_unavailable_returns_502(monkeypatch):
    from app.iss import router as iss_router
    from app.iss.client import IssError

    def boom(secid):
        raise IssError("MOEX ISS недоступен: timeout")

    monkeypatch.setattr(iss_router, "get_description", boom)
    r = client.get("/api/iss/SU26238RMFS4")
    assert r.status_code == 502
