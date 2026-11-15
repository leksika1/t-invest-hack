"""Exercise the real build loop offline; only persistence/retrieval are replaced."""
from contextlib import nullcontext
from types import SimpleNamespace
import uuid

import pytest

from app.cards import service
from app.cards.schema import SPECS
from app.cards.verification import project_iss
from app.config import Settings
from app.llm.gateway import GatewayError
from app.models import Card, CardField, Job, LLMCall, Page
from tests.fake_llm import FakeLLM


@pytest.fixture
def prepared_build(monkeypatch):
    document_id, card_id, job_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    text = ('9.5.1. Эмитент обязуется поддерживать отношение долга к EBITDA не более 3.\n'
            '9.5.2. Эмитент вправе досрочно погасить облигации по цене 100 процентов номинала.\n'
            '9.5.3. Владельцы предъявляют требования через депозитарий в течение 5 рабочих дней.\n'
            '9.5.4. Исполнение обеспечено поручительством ООО Пример в полном объёме.\n'
            '9.5.5. Номинальная стоимость составляет 1 000 рублей.')
    from app.documents.parser import chunks
    fragments, _ = chunks(text)
    context = [{'document_id': str(document_id), 'page': 1, **f} for f in fragments]
    iss = {
        'face_value': {'amount': '1000', 'currency': 'RUB'},
        'placement_date': {'date': '2025-01-01'}, 'maturity_date': {'date': '2028-01-01'},
        'coupon_type': {'type': 'fixed'},
        'coupon_rate': {'periods': [{'from': 1, 'to': 1, 'rate_percent': '10'}]},
        'coupon_schedule': {'payments': [{'number': 1, 'payment_date': '2025-07-01'}]},
        'offers': {'events': [{'date': '2026-01-01', 'type': 'put', 'price_percent': '100'}]},
        'amortization': {'events': [{'date': '2028-01-01', 'percent': '100', 'percent_base': 'initial_face_value'}]},
    }
    card = SimpleNamespace(id=card_id, issue_id=uuid.uuid4(), status='processing', journal={}, input_snapshot={
        'prepared': True, 'model': 'google/gemini-2.5-flash', 'embedding_model': None,
        'isin': 'RU000A10CS75', 'secid': 'RU000A10CS75', 'iss_fields': iss,
        'document_ids': [str(document_id)], 'documents': [
            {'id': str(document_id), 'filename': 'decision.pdf', 'pages': 1, 'ocr_pages': 0}],
    })
    job = SimpleNamespace(id=job_id, worker_id=None, payload={'card_id': str(card_id)})
    fields = {s.code: SimpleNamespace(id=uuid.uuid4(), reason='processing') for s in SPECS}

    class Rows(list):
        def all(self):
            return self

    class Session:
        def execute(self, query, parameters=None):
            pass  # advisory lock

        def get(self, model, identifier):
            if model is Card:
                return card
            if model is Job:
                return job
            assert model is CardField
            return next(f for f in fields.values() if f.id == identifier)

        def scalar(self, query):
            assert query.column_descriptions[0]['entity'] is CardField
            return fields[query.compile().params['code_1']]

        def scalars(self, query):
            entity = query.column_descriptions[0]['entity']
            if entity is Page:
                return Rows([SimpleNamespace(document_id=document_id, number=1, text=text)])
            assert entity is LLMCall
            return Rows()

    class Factory:
        def __call__(self):
            return nullcontext(Session())

        begin = __call__

    monkeypatch.setattr(service, 'retrieve', lambda *args: (context, []))
    monkeypatch.setattr('app.jobs.handlers.progress', lambda *args: None)
    return Factory(), job, card, fields, iss


@pytest.mark.parametrize('reason', [
    'llm_not_configured', 'llm_prices_not_configured', 'budget_exceeded', 'llm_timeout',
    'llm_http_error', 'llm_transport_error', 'invalid_json', 'llm_client_error', 'llm_proxy_error',
])
def test_build_gateway_failure_preserves_all_eight_iss_fields(prepared_build, monkeypatch, reason):
    factory, job, card, fields, iss = prepared_build
    configured = Settings(llm_api_key='' if reason == 'llm_not_configured' else 'fake', _env_file=None)
    if reason == 'llm_prices_not_configured':
        configured = Settings(llm_api_key='fake', llm_model='other/model',
                              llm_input_price_per_1m_usd='', llm_output_price_per_1m_usd='', _env_file=None)
        card.input_snapshot['model'] = configured.llm_model
    monkeypatch.setattr(service, 'get_settings', lambda: configured)
    calls = []

    class RefusingGateway:
        def __init__(self, **kwargs):
            pass

        def extract(self, code, *args):
            calls.append(code)
            raise GatewayError(reason, 'Тестовый отказ шлюза')

    service.build_card(factory, job, gateway_factory=RefusingGateway)
    assert card.status == 'ready'
    assert reason in card.journal['warnings']
    for spec in SPECS:
        field = fields[spec.code]
        assert field.quote is None and field.document_id is None
        if spec.iss:
            assert field.value == project_iss(spec.code, iss[spec.code])
            assert field.status == 'extracted' and not field.not_found
            assert field.source['kind'] == 'moex_iss'
            assert field.source['url'].endswith('/securities/RU000A10CS75.json')
        else:
            assert field.value is None and field.reason == reason
            assert field.status == ('not_found' if reason == 'llm_not_configured' else 'needs_review')
            assert field.not_found == (reason == 'llm_not_configured')
    if reason in ('llm_not_configured', 'llm_prices_not_configured'):
        assert not calls


def test_build_fake_llm_still_verifies_document_fields(prepared_build, monkeypatch):
    factory, job, card, fields, _ = prepared_build
    monkeypatch.setattr(service, 'get_settings', lambda: Settings(llm_api_key='fake', _env_file=None))
    service.build_card(factory, job, gateway_factory=FakeLLM)
    assert card.status == 'ready'
    assert fields['face_value'].status == 'verified_iss'
    assert fields['face_value'].source['kind'] == 'document'
    for code in ('covenants', 'issuer_call_option', 'offer_procedure', 'guarantee_security'):
        assert fields[code].status == 'extracted'
    assert fields['cross_default'].value is None
    assert fields['cross_default'].reason == 'quote_not_verified'


def test_real_gateway_proxy_error_reaches_build_fallback(prepared_build, monkeypatch):
    import httpx
    from app.llm.gateway import LLMGateway
    from tests.test_gateway import FakeTransport, MemoryLedger
    factory, job, card, fields, iss = prepared_build
    proxy = 'http://fake-user:fake-secret@proxy.internal:8888'
    configured = Settings(llm_api_key='fake', llm_proxy_url=proxy, llm_max_retries=0, _env_file=None)
    monkeypatch.setattr(service, 'get_settings', lambda: configured)
    transport = FakeTransport([httpx.ProxyError('Cannot connect: ' + proxy) for _ in SPECS])
    ledger = MemoryLedger()

    def gateway_factory(**kwargs):
        return LLMGateway(**kwargs, transport=transport, ledger=ledger)

    service.build_card(factory, job, gateway_factory=gateway_factory)
    assert card.status == 'ready' and 'llm_proxy_error' in card.journal['warnings']
    assert len(ledger.records) == 15
    assert all(record['error']['code'] == 'llm_proxy_error' for record in ledger.records)
    for spec in SPECS:
        if spec.iss:
            assert fields[spec.code].status == 'extracted'
            assert fields[spec.code].value == project_iss(spec.code, iss[spec.code])
            assert fields[spec.code].source['kind'] == 'moex_iss'
        else:
            assert fields[spec.code].status == 'needs_review'
            assert fields[spec.code].reason == 'llm_proxy_error'
    assert 'fake-secret' not in (str(ledger.records) + str(card.journal))
