"""Единое описание полей карточки выпуска.

Эти модели — будущая JSON-схема для LLM-извлечения. Чтобы добавить поле,
достаточно добавить атрибут в IssVerifiableFields или RiskTextFields.
"""

import re
from enum import Enum

from pydantic import BaseModel, Field, field_validator

ISIN_RE = re.compile(r"^RU[A-Z0-9]{9}[0-9]$")


class FieldStatus(str, Enum):
    verified_iss = "verified_iss"
    mismatch_iss = "mismatch_iss"
    not_found = "not_found"
    needs_review = "needs_review"
    not_implemented = "not_implemented"


class Source(BaseModel):
    document: str | None = Field(None, description="Документ: решение о выпуске, проспект, сущфакт")
    url: str | None = Field(None, description="Ссылка на документ")
    page: str | None = Field(None, description="Страница или пункт документа")


class CardField(BaseModel):
    value: str | None = None
    quote: str | None = Field(None, description="Дословная цитата из документа")
    source: Source = Field(default_factory=Source)
    status: FieldStatus = FieldStatus.not_implemented


def _field(description: str):
    return Field(default_factory=CardField, description=description)


class IssVerifiableFields(BaseModel):
    """«Жёсткие» поля, сверяемые с MOEX ISS."""

    face_value: CardField = _field("Номинал")
    currency: CardField = _field("Валюта")
    placement_date: CardField = _field("Дата размещения")
    maturity_date: CardField = _field("Дата погашения")
    coupon_type: CardField = _field("Тип купона")
    coupon_rate: CardField = _field("Ставка или формула купона")
    coupon_schedule: CardField = _field("Частота и график купонов")
    offers: CardField = _field("Оферты: даты, тип put/call")
    amortization: CardField = _field("Амортизация: график")


class RiskTextFields(BaseModel):
    """Текстовые рисковые условия, извлекаемые из документов."""

    covenants: CardField = _field("Ковенанты")
    holder_early_redemption: CardField = _field("Основания досрочного погашения по требованию владельцев")
    issuer_call_option: CardField = _field("Call-опцион эмитента")
    guarantee_security: CardField = _field("Поручительство, оферта третьего лица, обеспечение")
    cross_default: CardField = _field("Кросс-дефолт")
    issuer_restrictions: CardField = _field("Ограничения для эмитента: сделки, распоряжение активами, дивиденды")
    offer_procedure: CardField = _field("Порядок участия в оферте: окно и способ подачи заявки")


class CardRequest(BaseModel):
    isin: str

    @field_validator("isin")
    @classmethod
    def validate_isin(cls, v: str) -> str:
        v = v.strip().upper()
        if not ISIN_RE.fullmatch(v):
            raise ValueError("ISIN должен иметь вид RU + 9 букв/цифр + цифра (12 символов)")
        return v


class BondCard(BaseModel):
    isin: str
    iss_fields: IssVerifiableFields = Field(default_factory=IssVerifiableFields)
    risk_fields: RiskTextFields = Field(default_factory=RiskTextFields)
    disclaimer: str = "Факты из эмиссионных документов, не инвестиционная рекомендация."
