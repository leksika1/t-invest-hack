"""Normalize observed ISS columns, never infer legal coupon/offer types."""
from datetime import date
from decimal import Decimal, InvalidOperation

from app.iss.client import rows


def number(value) -> str | None:
    try:
        n = Decimal(str(value).replace(",", "."))
        return ("0" if n == 0 else format(n.normalize(), "f")) if n.is_finite() else None
    except (InvalidOperation, ValueError):
        return None


def day(value) -> str | None:
    try:
        return date.fromisoformat(str(value)[:10]).isoformat()
    except ValueError:
        return None


def currency(value) -> str | None:
    return {"SUR": "RUB", "RUR": "RUB"}.get(value, value) or None


def normalize(raw: dict) -> dict:
    desc = {r["name"]: r["value"] for r in rows(raw.get("description", {}).get("description", {}))}
    bond = raw.get("bondization", {})
    coupons = sorted(rows(bond.get("coupons", {})), key=lambda r: r.get("coupondate") or "")
    amort = rows(bond.get("amortizations", {}))
    offers = rows(bond.get("offers", {}))
    emitter = next(iter(rows(raw.get("emitter", {}).get("emitter", {}))), {})
    fallback = next((r for r in rows(raw.get("search", {}).get("securities", {})) if r.get("isin") == desc.get("ISIN")), {})
    initials = {number(r.get("initialfacevalue")) for r in coupons + amort} - {None}
    # FACEVALUE in description is the remaining face value (confirmed on RU000A1048A9).
    initial = next(iter(initials)) if len(initials) == 1 else None
    warnings = []
    if initial is None:
        warnings.append("initial_face_value_unavailable")
    rates = [{"from": i, "to": i, "rate_percent": number(r.get("valueprc")), "amount": number(r.get("value"))}
             for i, r in enumerate(coupons, 1)]
    freq = number(desc.get("COUPONFREQUENCY"))
    fields = {
        "face_value": {"amount": initial, "currency": currency((coupons or amort or [{}])[0].get("faceunit") or desc.get("FACEUNIT"))} if initial else None,
        "placement_date": {"date": day(desc.get("ISSUEDATE"))} if day(desc.get("ISSUEDATE")) else None,
        "maturity_date": {"date": day(desc.get("MATDATE"))} if day(desc.get("MATDATE")) else None,
        "coupon_type": None,
        "coupon_rate": {"periods": rates, "formula": None} if rates else None,
        "coupon_schedule": {"frequency_per_year": int(Decimal(freq)) if freq and Decimal(freq) == int(Decimal(freq)) else None,
                            "period_days": None,
                            "payments": [{"number": i, "start": day(r.get("startdate")), "end": day(r.get("coupondate")), "payment_date": day(r.get("coupondate"))} for i, r in enumerate(coupons, 1)]} if coupons else None,
        "offers": {"events": [{"date": day(r.get("offerdate")), "type": "call" if "call" in str(r.get("offertype")).lower() else "put" if "put" in str(r.get("offertype")).lower() else "unknown",
                               "price_percent": number(r.get("price")), "raw_type": r.get("offertype"),
                               "window_start": day(r.get("offerdatestart")), "window_end": day(r.get("offerdateend"))} for r in offers]} if offers else None,
        "amortization": {"events": [{"date": day(r.get("amortdate")), "amount": number(r.get("value")), "percent": number(r.get("valueprc")),
                                     "percent_base": "initial_face_value" if initial and number(r.get("valueprc")) and number(r.get("value")) and Decimal(str(r["value"])) == Decimal(initial) * Decimal(str(r["valueprc"])) / 100 else "unknown"} for r in amort]} if amort else None,
    }
    warnings.append("coupon_type_not_explicit_in_iss")
    if any(e["type"] == "unknown" for e in (fields["offers"] or {}).get("events", [])):
        warnings.append("offer_type_requires_document")
    return {"header": {"issuer": emitter.get("TITLE") or fallback.get("emitent_title"), "inn": str(emitter.get("INN") or fallback.get("emitent_inn") or "") or None,
                       "ogrn": str(emitter.get("OGRN") or "") or None, "name": desc.get("NAME"), "short_name": desc.get("SHORTNAME"), "listing_level": desc.get("LISTLEVEL"), "registration_number": desc.get("REGNUMBER")},
            "fields": fields, "warnings": warnings}
