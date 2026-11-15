"""Single registry for the fifteen fields: typed values, retrieval and ISS rules."""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Annotated, Literal
import re
import uuid

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, create_model, field_validator, model_validator

ISIN_RE = re.compile(r"^RU[A-Z0-9]{9}[0-9]$")
DISCLAIMER = "Факты из документов эмитента, не инвестиционная рекомендация"
Text = Annotated[str, Field(min_length=1, max_length=12000)]


def decimal_string(value):
    if not isinstance(value, str):
        raise ValueError("Десятичное значение должно быть строкой")
    try:
        number = Decimal(value.replace(',', '.').replace(' ', '').replace('\u00a0', ''))
        if not number.is_finite():
            raise ValueError("Число должно быть конечным")
        return '0' if number == 0 else format(number.normalize(), 'f')
    except InvalidOperation as exc:
        raise ValueError("Некорректное число") from exc


DecimalText = Annotated[str, BeforeValidator(decimal_string)]


class Value(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Money(Value):
    amount: DecimalText
    currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$")]


class DateValue(Value):
    date: date


class CouponType(Value):
    type: Literal['fixed', 'floating', 'variable', 'unknown']
    description: Text | None = None


class RatePeriod(Value):
    from_: int = Field(alias='from', ge=1)
    to: int = Field(ge=1)
    rate_percent: DecimalText | None = None
    amount: DecimalText | None = None

    @model_validator(mode='after')
    def ordered(self):
        if self.to < self.from_:
            raise ValueError('Конец периода раньше начала')
        return self


class Formula(Value):
    text: Text
    base: Text | None = None
    spread_pp: DecimalText | None = None
    cap_percent: DecimalText | None = None
    floor_percent: DecimalText | None = None
    observation_rule: Text | None = None
    rounding_rule: Text | None = None


class CouponRate(Value):
    periods: list[RatePeriod] = Field(default_factory=list, max_length=1000)
    formula: Formula | None = None


class Payment(Value):
    number: int = Field(ge=1)
    start: date | None = None
    end: date | None = None
    payment_date: date


class CouponSchedule(Value):
    frequency_per_year: int | None = Field(None, ge=1, le=366)
    period_days: int | None = Field(None, ge=1)
    payments: list[Payment] = Field(max_length=1000)


class Offer(Value):
    date: date
    type: Literal['put', 'call', 'unknown']
    price_percent: DecimalText | None = None


class Offers(Value):
    events: list[Offer] = Field(max_length=1000)


class AmortizationEvent(Value):
    date: date
    amount: DecimalText | None = None
    percent: DecimalText | None = None
    percent_base: Literal['initial_face_value', 'remaining_face_value', 'unknown']


class Amortization(Value):
    events: list[AmortizationEvent] = Field(max_length=1000)


class Covenant(Value):
    condition: Text
    test_date_rule: Text | None = None
    consequence: Text | None = None
    exceptions: Text | None = None


class Covenants(Value):
    items: list[Covenant] = Field(max_length=100)


class RedemptionGround(Value):
    trigger: Text
    deadline: Text | None = None
    exceptions: Text | None = None


class HolderRedemption(Value):
    grounds: list[RedemptionGround] = Field(max_length=100)


class CallCondition(Value):
    trigger: Text
    date_rule: Text | None = None
    price_rule: Text | None = None
    notice_rule: Text | None = None


class IssuerCall(Value):
    conditions: list[CallCondition] = Field(max_length=100)


class SecurityItem(Value):
    type: Literal['surety', 'third_party_offer', 'pledge', 'other']
    party: Text | None = None
    scope: Text
    expiry: Text | None = None
    exceptions: Text | None = None


class Security(Value):
    items: list[SecurityItem] = Field(max_length=100)


class CrossDefaultItem(Value):
    trigger: Text
    threshold: Money | None = None
    entities: Text | None = None
    grace_period: Text | None = None
    consequence: Text | None = None
    exceptions: Text | None = None


class CrossDefault(Value):
    items: list[CrossDefaultItem] = Field(max_length=100)


class Restriction(Value):
    action: Text
    restriction: Text
    exceptions: Text | None = None
    consequence: Text | None = None


class Restrictions(Value):
    items: list[Restriction] = Field(max_length=100)


class Procedure(Value):
    offer_date: date | None = None
    window_start: date | None = None
    window_end: date | None = None
    window_rule: Text | None = None
    via: Text | None = None
    submission: Text
    settlement_rule: Text | None = None


class OfferProcedure(Value):
    procedures: list[Procedure] = Field(max_length=100)


@dataclass(frozen=True)
class FieldSpec:
    code: str
    title: str
    value_type: type[Value]
    hints: str
    iss: bool = False
    comparison: str = 'none'


SPECS = [
    FieldSpec('face_value', 'Номинал и валюта', Money, 'номинальная стоимость валюта первоначальный номинал', True, 'decimal_currency'),
    FieldSpec('placement_date', 'Дата размещения', DateValue, 'дата начала размещения срок размещения', True, 'date'),
    FieldSpec('maturity_date', 'Дата погашения', DateValue, 'дата погашения окончательное погашение срок', True, 'date'),
    FieldSpec('coupon_type', 'Тип купона', CouponType, 'фиксированная плавающая переменная ставка определяется эмитентом', True, 'coupon_type'),
    FieldSpec('coupon_rate', 'Ставка или формула купона', CouponRate, 'размер купонного дохода ставка ключевая спред формула округление', True, 'coupon_periods'),
    FieldSpec('coupon_schedule', 'График купонов', CouponSchedule, 'купонный период даты выплаты график продолжительность', True, 'payments'),
    FieldSpec('offers', 'Оферты', Offers, 'оферта приобретение по требованию владельцев put call дата выкупа', True, 'offers'),
    FieldSpec('amortization', 'Амортизация', Amortization, 'амортизация частичное погашение первоначальной номинальной стоимости график', True, 'amortization'),
    FieldSpec('covenants', 'Ковенанты', Covenants, 'ковенант финансовые показатели обязуется соблюдать чистый долг EBITDA нарушение'),
    FieldSpec('holder_early_redemption', 'Погашение по требованию владельцев', HolderRedemption, 'досрочное погашение по требованию существенное нарушение срок предъявления'),
    FieldSpec('issuer_call_option', 'Call-опцион эмитента', IssuerCall, 'по усмотрению эмитента эмитент вправе call уведомление досрочное погашение'),
    FieldSpec('guarantee_security', 'Обеспечение', Security, 'обеспечение поручитель залог оферта третьего лица объем ответственности'),
    FieldSpec('cross_default', 'Кросс-дефолт', CrossDefault, 'кросс дефолт иные долговые обязательства неисполнение порог льготный период'),
    FieldSpec('issuer_restrictions', 'Ограничения эмитента', Restrictions, 'ограничения дивиденды отчуждение активов крупные сделки запрет залога реорганизация'),
    FieldSpec('offer_procedure', 'Порядок участия в оферте', OfferProcedure, 'порядок предъявления требований период через депозитарий акцепт оферты срок подачи'),
]
FIELDS = {spec.code: spec for spec in SPECS}


class Evidence(Value):
    quote: Text
    document_id: uuid.UUID
    page: int = Field(ge=1)
    clause: str | None = None
    value_path: str | None = None


class Extraction(Value):
    value: dict | None = None
    quote: Text | None = None
    document_id: uuid.UUID | None = None
    page: int | None = Field(None, ge=1)
    clause: str | None = None
    confidence: float | None = Field(None, ge=0, le=1, allow_inf_nan=False)
    not_found: bool
    ambiguous: bool = False
    evidence: list[Evidence] = Field(default_factory=list, max_length=100)

    @model_validator(mode='after')
    def coherent(self):
        if self.not_found and self.value is not None:
            raise ValueError('not_found несовместим с value')
        if not self.not_found and self.value is None:
            raise ValueError('Найденному полю нужно значение')
        return self


def extraction_model(code):
    return create_model(code + '_extraction', __base__=Extraction, value=(FIELDS[code].value_type | None, None))


def validate_value(code, value):
    return FIELDS[code].value_type.model_validate(value).model_dump(mode='json', by_alias=True)


class CardRequest(Value):
    isin: str

    @field_validator('isin')
    @classmethod
    def validate_isin(cls, value):
        value = value.strip().upper()
        if not ISIN_RE.fullmatch(value):
            raise ValueError('ISIN должен иметь вид RU + 9 букв/цифр + цифра')
        return value


class Review(Value):
    user_status: Literal['confirmed', 'corrected']
    user_value: dict | None = None

    @model_validator(mode='after')
    def coherent(self):
        if (self.user_status == 'corrected') != (self.user_value is not None):
            raise ValueError('Новое значение обязательно только для corrected')
        return self
