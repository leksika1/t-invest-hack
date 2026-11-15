"""Strict annotation and export contracts; application value types stay authoritative."""
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.cards.schema import CardRequest, FIELDS, validate_value

RISK_ARRAYS = {
    'covenants': 'items', 'holder_early_redemption': 'grounds', 'issuer_call_option': 'conditions',
    'guarantee_security': 'items', 'cross_default': 'items', 'issuer_restrictions': 'items',
    'offer_procedure': 'procedures',
}
ISS_CODES = tuple(code for code, spec in FIELDS.items() if spec.iss)
RISK_CODES = tuple(RISK_ARRAYS)
STATUSES = Literal['verified_iss', 'mismatch_iss', 'extracted', 'not_found', 'needs_review']


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)


class Citation(Strict):
    quote: str = Field(min_length=1, max_length=12000)
    document_id: str = Field(min_length=1)
    page: int = Field(ge=1)
    clause: str | None = None
    value_path: str | None = None


class GoldDocument(Strict):
    document_id: str
    filename: str
    path: str
    sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    enabled: bool = True
    type: str = 'decision'
    origins: list[dict] = Field(default_factory=list)
    pages_file: str | None = None
    pages_sha256: str | None = Field(None, pattern=r'^[a-f0-9]{64}$')

    @model_validator(mode='after')
    def paired_sidecar(self):
        if bool(self.pages_file) != bool(self.pages_sha256):
            raise ValueError('pages_file и pages_sha256 указываются вместе')
        return self


class FrozenPage(Strict):
    number: int = Field(ge=1, strict=True)
    text: str
    ocr: bool = Field(strict=True)
    error: str | None = None


class FrozenPages(Strict):
    pdf_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    pages: list[FrozenPage] = Field(min_length=1, max_length=1000)


class Atom(Strict):
    """One expected array element, with pre-approved paraphrases/slot constraints."""
    index: int = Field(ge=0)
    condition_id: str = Field(min_length=1)
    category: Literal['covenants', 'call', 'early_redemption', 'put', 'other'] = 'other'
    alternatives: list[dict] = Field(default_factory=list)
    # Leaf path -> required AND groups; each group contains approved OR phrases.
    constraints: dict[str, list[list[str]]] = Field(default_factory=dict)


class GoldField(Strict):
    value: dict | None = None
    not_found: bool = False
    unscorable: bool = False
    reason: str | None = None
    alternatives: list[dict] = Field(default_factory=list)
    quote: str | None = None
    document_id: str | None = None
    page: int | None = Field(None, ge=1)
    clause: str | None = None
    evidence: list[Citation] = Field(default_factory=list)
    iss_value: dict | None = None
    expected_status: STATUSES | None = None
    atoms: list[Atom] = Field(default_factory=list)

    @model_validator(mode='after')
    def coherent(self):
        if self.unscorable:
            if not self.reason:
                raise ValueError('unscorable требует причину')
        elif self.not_found:
            if self.value is not None or self.alternatives or self.atoms:
                raise ValueError('not_found несовместим со значением или атомарными условиями')
            if not self.reason:
                raise ValueError('not_found требует причину отсутствия в данном корпусе')
            if self.expected_status not in (None, 'not_found'):
                raise ValueError('not_found требует ожидаемый статус not_found')
        elif self.value is None or not self.quote or not self.document_id or not self.page or not self.expected_status:
            raise ValueError('Оцениваемое значение требует value, status, quote, document_id и page')
        if self.quote and (not self.document_id or not self.page):
            raise ValueError('Цитата требует документ и физическую страницу')
        if self.value is not None and self.expected_status == 'not_found':
            raise ValueError('Значение несовместимо со статусом not_found')
        return self


class Review(Strict):
    annotators: list[str] = Field(default_factory=list)
    resolved: bool = False
    adjudicator: str | None = None
    disagreements: list[str] = Field(default_factory=list)
    resolution: str | None = None
    verified_by: str = 'codex'
    needs_human_check: bool = True


class GoldIssue(Strict):
    schema_version: Literal['1'] = '1'
    isin: str
    kind: Literal['synthetic', 'real']
    as_of: date
    issuer_group: str
    documents: list[GoldDocument]
    iss_fixture: str
    fields: dict[str, GoldField]
    review: Review
    todo: list[str] = Field(default_factory=list)
    collection: dict = Field(default_factory=dict)

    @model_validator(mode='after')
    def validate_contract(self):
        self.isin = CardRequest(isin=self.isin).isin
        if set(self.fields) != set(FIELDS):
            raise ValueError('Gold должен содержать ровно 15 известных кодов полей')
        ids = [d.document_id for d in self.documents]
        hashes = [d.sha256 for d in self.documents]
        if len(set(ids)) != len(ids) or len(set(hashes)) != len(hashes):
            raise ValueError('Документы gold должны иметь уникальные ID и sha256')
        for code, field in self.fields.items():
            for value in [field.value, *field.alternatives]:
                if value is not None:
                    validate_value(code, value)
            if code in RISK_ARRAYS:
                if field.iss_value is not None or field.expected_status in ('verified_iss', 'mismatch_iss'):
                    raise ValueError(f'{code}: рисковое поле не имеет ISS-сверки')
                array_key = RISK_ARRAYS[code]
                if any(len(v[array_key]) != len((field.value or {}).get(array_key, [])) for v in field.alternatives):
                    raise ValueError(f'{code}: альтернативы должны сохранять число атомарных условий')
            citations = [c.model_dump() for c in field.evidence]
            if field.quote:
                citations.append({'document_id': field.document_id, 'page': field.page})
            if any(c['document_id'] not in ids for c in citations):
                raise ValueError(f'{code}: неизвестный документ цитаты')
            array = RISK_ARRAYS.get(code, 'events' if code == 'offers' else None)
            if field.atoms:
                items = (field.value or {}).get(array, []) if array else []
                if sorted(a.index for a in field.atoms) != list(range(len(items))):
                    raise ValueError(f'{code}: atoms должны покрывать все элементы ровно один раз')
                for atom in field.atoms:
                    for alternative in atom.alternatives:
                        validate_value(code, {array: [alternative]})
                    for path, groups in atom.constraints.items():
                        current = items[atom.index]
                        try:
                            for key in path.split('.'):
                                current = current[key]
                        except (KeyError, TypeError) as exc:
                            raise ValueError(f'{code}: неизвестный путь правила {path}') from exc
                        if not isinstance(current, str) or not groups or any(not group or any(not phrase.strip() for phrase in group) for group in groups):
                            raise ValueError(f'{code}: пустое/нестроковое правило сопоставления')
            elif code in RISK_ARRAYS and field.value and not field.unscorable:
                if not field.value.get(array):
                    raise ValueError(f'{code}: пустой массив не доказывает not_found')
                raise ValueError(f'{code}: каждое рисковое условие требует atom')
        if self.review.resolved and self.review.disagreements and (not self.review.adjudicator or not self.review.resolution):
            raise ValueError('Разногласия требуют арбитра и решения')
        return self

    @property
    def approved(self):
        return (not self.review.needs_human_check and self.review.resolved
                and len(set(self.review.annotators)) >= 2 and not self.todo)


class ManifestEntry(Strict):
    isin: str
    gold: str
    split: Literal['dev', 'test']
    issuer_group: str
    card_id: str | None = None


class Manifest(Strict):
    schema_version: Literal['1'] = '1'
    name: str
    seed: int
    frozen: bool
    settings: dict
    issues: list[ManifestEntry]

    @model_validator(mode='after')
    def no_leakage(self):
        if not self.issues or len({e.isin for e in self.issues}) != len(self.issues):
            raise ValueError('Manifest пуст или содержит повторный ISIN')
        groups = {}
        for entry in self.issues:
            CardRequest(isin=entry.isin)
            if groups.setdefault(entry.issuer_group, entry.split) != entry.split:
                raise ValueError('Один эмитент одновременно в dev и test')
        return self
