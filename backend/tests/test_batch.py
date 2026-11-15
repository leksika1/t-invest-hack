import json
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select

from app.cards import service
from app.cards.batch import extract_batch
from app.cards.retriever import full_document_context
from app.cards.schema import SPECS, extraction_model
from app.config import Settings
from app.llm.gateway import LLMGateway
from app.models import LLMCall, Page
from tests.fake_llm import FakeLLM
from tests.test_gateway import MemoryLedger, FakeTransport, response, extract
from tests.test_llm_fallback import prepared_build


def batch_response(specs, broken=None):
    return response(json.dumps({s.code: {} if s.code == broken else {'not_found': True, 'value': None} for s in specs}))


def google_error(delay, *, daily=True, array=True, status=429):
    message = 'Generate content requests per day limit exceeded on Free Tier; AQ.secret-key' if daily else 'Too many requests'
    body = {'error': {'message': message, 'status': 'RESOURCE_EXHAUSTED', 'details': [
        {'violations': [{'quotaId': 'GenerateRequestsPerDayPerProjectPerModel-FreeTier' if daily else 'RequestsPerMinute', 'quotaValue': '20'}]},
        {'retryDelay': f'{delay}s'}]}}
    return httpx.HTTPStatusError('rejected', request=httpx.Request('POST', 'https://example.test'),
                                response=httpx.Response(status, json=[body] if array else body))


def gateway(responses, **options):
    ledger, transport, sleeps = MemoryLedger(), FakeTransport(responses), []
    settings = Settings(_env_file=None, llm_api_key='AQ.secret-key', **options)
    return LLMGateway(settings=settings, ledger=ledger, transport=transport, sleep=sleeps.append), ledger, transport, sleeps


def test_seven_risks_one_call_partial_repairs_only_broken_field():
    specs = [s for s in SPECS if not s.iss]
    g, ledger, transport, _ = gateway([batch_response(specs, 'cross_default'), response('{"not_found":true,"value":null}')])
    result = extract_batch(g, specs, [{'text': 'Текст документа', 'page': 1}])
    assert len(result) == 7 and all(r.not_found for r in result.values())
    assert len(transport.calls) == 2
    assert transport.calls[1][1]['response_format']['json_schema']['name'] == 'cross_default'
    assert len(ledger.records) == 2


def test_schema_rejection_then_json_object_does_not_repeat_valid_fields():
    from tests.test_gateway import http_error
    specs = [s for s in SPECS if not s.iss]
    g, _, transport, _ = gateway([http_error(400, 'schema too complex'), batch_response(specs)])
    result = extract_batch(g, specs, [{'text': 'Документ'}])
    assert len(result) == 7
    assert [p['response_format']['type'] for _, p in transport.calls] == ['json_schema', 'json_object']
    assert 'Верни JSON по этой схеме' in transport.calls[1][1]['messages'][-1]['content']


@pytest.mark.parametrize('array', [True, False])
def test_daily_quota_changes_model_preserves_sanitized_diagnostics(array):
    g, ledger, transport, sleeps = gateway([google_error(58000, array=array), response(usage={'prompt_tokens': 100, 'completion_tokens': 50})])
    assert extract(g).value.amount == '1000'
    assert [p['model'] for _, p in transport.calls] == ['gemini-3.5-flash', 'gemini-3.1-flash-lite']
    error = ledger.records[0]['error']
    assert error['status'] == 'RESOURCE_EXHAUSTED'
    assert error['quota_value'] == '20' and error['retry_after_seconds'] == 58000
    assert '20 запросов на модель' in error['message'] and '~17 ч' in error['message']
    assert 'secret-key' not in str(ledger.records)
    assert not sleeps
    assert ledger.records[1]['cost_source'] == 'google_estimate'
    assert ledger.records[1]['cost'] > 0


def test_short_rate_limit_retries_same_model():
    g, _, transport, sleeps = gateway([google_error(3, daily=False), response()])
    extract(g)
    assert [p['model'] for _, p in transport.calls] == ['gemini-3.5-flash'] * 2
    assert sleeps == [3]


def test_unavailable_model_falls_back_and_quota_does_not_fan_out():
    missing = httpx.HTTPStatusError('missing', request=httpx.Request('POST', 'https://example.test'),
        response=httpx.Response(404, json={'error': {'message': 'model gemini-3.5-flash not available'}}))
    specs = [s for s in SPECS if not s.iss]
    g, _, transport, _ = gateway([missing, batch_response(specs)])
    assert all(v.not_found for v in extract_batch(g, specs, [{'text': 'Документ'}]).values())
    assert transport.calls[-1][1]['model'] == 'gemini-3.1-flash-lite'
    g, _, transport, _ = gateway([google_error(58000), google_error(58000)])
    result = extract_batch(g, specs, [{'text': 'Документ'}])
    assert len(transport.calls) == 2 and all(v.code == 'llm_rate_limited' for v in result.values())


@pytest.mark.parametrize("fallback", [False, True])
def test_complete_build_two_calls_with_real_verification(prepared_build, monkeypatch, fallback):
    factory, job, card, fields, _ = prepared_build
    with factory() as session:
        page = session.scalars(select(Page))[0]
        session_type = type(session)
    context = full_document_context(card.input_snapshot['documents'], {(str(page.document_id), 1): page.text}, 150000)
    fake = FakeLLM()
    replies = []
    for iss in (True, False):
        values = {s.code: fake.extract(s.code, [{'role': 'system', 'content': ''}, {'role': 'user', 'content': json.dumps({'fragments': context})}], extraction_model(s.code)).model_dump(mode='json') for s in SPECS if s.iss == iss}
        replies.append(response(json.dumps(values), usage={'prompt_tokens': 100, 'completion_tokens': 50}))
    g, ledger, transport, _ = gateway(([google_error(58000)] if fallback else []) + replies, llm_model='google/gemini-2.5-flash', llm_fallback_models='gemini-3.1-flash-lite')
    monkeypatch.setattr(service, 'get_settings', lambda: g.settings)
    original = session_type.scalars
    def scalars(self, query):
        if query.column_descriptions[0]['entity'] is LLMCall:
            calls = [SimpleNamespace(model=transport.calls[i][1]['model'], purpose='extraction', cost_source=r['cost_source'], cost_usd=r['cost'], tokens_in=r['usage'].get('prompt_tokens'), tokens_out=r['usage'].get('completion_tokens'), error=r['error']) for i, r in enumerate(ledger.records)]
            return SimpleNamespace(all=lambda: calls)
        return original(self, query)
    monkeypatch.setattr(session_type, 'scalars', scalars)
    service.build_card(factory, job, gateway_factory=lambda **kwargs: g)
    assert len(transport.calls) == card.journal['generation_calls'] == (3 if fallback else 2)
    assert fields['face_value'].status == 'verified_iss'
    assert fields['covenants'].status == 'extracted'
    assert fields['cross_default'].reason == 'quote_not_verified'
    assert card.journal['tokens_in'] == 200 and card.journal['tokens_out'] == 100
    assert 'фактически может быть 0' in card.journal['cost_note']
    assert card.journal['models'] == (['gemini-3.1-flash-lite', 'google/gemini-2.5-flash'] if fallback else ['google/gemini-2.5-flash'])
    assert card.journal['response_models'] == (['gemini-3.1-flash-lite'] if fallback else ['google/gemini-2.5-flash'])
    if fallback:
        assert [p['model'] for _, p in transport.calls][1:] == ['gemini-3.1-flash-lite'] * 2


def test_provider_defaults_and_explicit_disable():
    assert Settings(_env_file=None, llm_api_key='AQ.fake').fallback_models == ['gemini-3.1-flash-lite']
    assert Settings(_env_file=None, llm_api_key='sk-or-fake').fallback_models == []
    assert Settings(_env_file=None, llm_api_key='AQ.fake', llm_fallback_models=',').fallback_models == []

def test_failed_repair_retains_valid_siblings():
    specs = [s for s in SPECS if not s.iss]
    g, _, transport, _ = gateway([batch_response(specs, specs[0].code), google_error(58000)], llm_fallback_models=',')
    result = extract_batch(g, specs, [{'text': 'Текст'}])
    assert result[specs[0].code].code == 'llm_rate_limited'
    assert all(result[s.code].not_found for s in specs[1:])
    assert len(transport.calls) == 2

def test_cost_source_fits_existing_database_column_and_exhaustion_is_cached():
    from app.models import LLMCall
    g, ledger, transport, _ = gateway([google_error(58000), google_error(58000)])
    specs = [s for s in SPECS if not s.iss]
    extract_batch(g, specs, [{'text': 'Документ'}])
    extract_batch(g, specs, [{'text': 'Документ'}])
    assert len(transport.calls) == 2
    assert all(len(r['cost_source']) <= LLMCall.__table__.c.cost_source.type.length for r in ledger.records)
