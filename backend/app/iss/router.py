import re

from fastapi import APIRouter, HTTPException

from app.iss.client import IssError, get_description

router = APIRouter(prefix="/api/iss", tags=["iss"])

SECID_RE = re.compile(r"^[A-Za-z0-9_-]{1,36}$")


@router.get("/{secid}")
def iss_security(secid: str) -> dict:
    if not SECID_RE.fullmatch(secid):
        raise HTTPException(status_code=422, detail="Некорректный secid")
    try:
        description = get_description(secid)
    except IssError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    if not description:
        raise HTTPException(status_code=404, detail="Выпуск не найден в ISS")
    return {"secid": secid, "description": description}
