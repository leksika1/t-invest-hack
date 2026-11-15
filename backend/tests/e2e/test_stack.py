def test_health_db_ok(http):
    r = http.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["db"] == "ok"


def test_card_valid_isin(http):
    r = http.post("/api/cards", json={"isin": "RU000A10AXF7"})
    assert r.status_code == 200
    body = r.json()
    assert body["isin"] == "RU000A10AXF7"
    fields = list(body["iss_fields"].values()) + list(body["risk_fields"].values())
    assert fields
    assert all(f["status"] == "not_found" for f in fields)


def test_card_invalid_isin(http):
    r = http.post("/api/cards", json={"isin": "XX123"})
    assert r.status_code == 422


def test_frontend_index(http):
    r = http.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert '<div id="root">' in r.text
