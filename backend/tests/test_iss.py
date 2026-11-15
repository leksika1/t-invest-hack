import json
from pathlib import Path

import pytest

from app.iss import client
from app.iss.normalize import normalize

FIXTURES = Path(__file__).parent / "fixtures" / "iss"


@pytest.mark.parametrize("isin", ["RU000A1048A9", "RU000A107UU5", "RU000A10C8F3"])
def test_actual_iss_columns_and_normalization(isin):
    raw = json.loads((FIXTURES / (isin + ".json")).read_text(encoding="utf-8"))
    result = normalize(raw)
    assert result["header"]["inn"] == "6685151087"
    assert result["fields"]["face_value"] == {"amount": "1000", "currency": "RUB"}
    assert len(result["fields"]) == 8
    assert result["fields"]["coupon_type"] is None
    assert result["fields"]["coupon_schedule"]["payments"][0]["number"] == 1
    if isin == "RU000A1048A9":
        assert len(result["fields"]["amortization"]["events"]) == 4
        assert result["fields"]["amortization"]["events"][0]["percent_base"] == "initial_face_value"
        assert result["fields"]["offers"]["events"][0]["type"] == "unknown"


def test_emitter_fallback_and_missing_values():
    raw = json.loads((FIXTURES / "RU000A1048A9.json").read_text(encoding="utf-8"))
    raw["emitter"] = {}
    assert normalize(raw)["header"]["inn"] == "6685151087"
    result = normalize({})
    assert result["fields"]["face_value"] is None
    assert result["fields"]["offers"] is None


def test_unlimited_ignored_uses_cursor(monkeypatch):
    calls = []
    def get(path, params):
        calls.append(params)
        if params.get("start") == 1:
            return {"coupons": {"columns": ["value"], "data": [[2]]}}
        return {"coupons": {"columns": ["value"], "data": [[1]]}, "coupons.cursor": {"columns": ["TOTAL"], "data": [[2]]}}
    monkeypatch.setattr(client, "_get", get)
    assert client.get_bondization("TEST")["coupons"]["data"] == [[1], [2]]
    assert calls[-1]["start"] == 1


def test_incomplete_pagination_is_error(monkeypatch):
    monkeypatch.setattr(client, "_get", lambda *a, **kw: {"coupons": {"columns": ["value"], "data": []}, "coupons.cursor": {"columns": ["TOTAL"], "data": [[2]]}})
    with pytest.raises(client.IssError, match="Неполный"):
        client.get_bondization("TEST")


def test_full_iss_flow_on_recorded_json(monkeypatch):
    raw = json.loads((FIXTURES / "RU000A1048A9.json").read_text(encoding="utf-8"))
    def get(path, params=None):
        if "bondization" in path:
            return raw["bondization"]
        return raw["emitter"] if "emitters" in path else raw["description"]
    monkeypatch.setattr(client, "_get", get)
    assert client.get_issue("RU000A1048A9")["header"]["inn"] == "6685151087"


def test_equal_initial_values_with_different_decimal_scale():
    raw = json.loads((FIXTURES / "RU000A1048A9.json").read_text(encoding="utf-8"))
    block = raw["bondization"]["amortizations"]
    block["data"][0][block["columns"].index("initialfacevalue")] = "1000.00"
    assert normalize(raw)["fields"]["face_value"]["amount"] == "1000"


def test_pagination_ignoring_start_is_not_silent_success(monkeypatch):
    monkeypatch.setattr(client, "_get", lambda *a, **kw: {"coupons": {"columns": ["value"], "data": [[1]]}, "coupons.cursor": {"columns": ["TOTAL"], "data": [[2]]}})
    with pytest.raises(client.IssError, match="Неполный"):
        client.get_bondization("TEST")
