import pytest
from fastapi.testclient import TestClient

from app import main

client = TestClient(main.app)


def test_health(monkeypatch):
    monkeypatch.setattr(main, "check_db", lambda: True)
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"status", "db", "llm", "version"}
    assert body["status"] == "ok"
    assert body["db"] == "ok"


def test_health_db_down(monkeypatch):
    monkeypatch.setattr(main, "check_db", lambda: False)
    body = client.get("/api/health").json()
    assert body["status"] == "degraded"
    assert body["db"] == "error"


@pytest.mark.parametrize("isin", ["RU000A10AXF7", "ru000a10axf7", " RU000A0JX0J2 "])
def test_card_valid_isin(isin):
    r = client.post("/api/cards", json={"isin": isin})
    assert r.status_code == 200
    body = r.json()
    assert body["isin"] == isin.strip().upper()
    fields = list(body["iss_fields"].values()) + list(body["risk_fields"].values())
    assert len(fields) == 16
    assert all(f["status"] == "not_found" and f["reason"] == "extraction_pending_stage3" for f in fields)


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
