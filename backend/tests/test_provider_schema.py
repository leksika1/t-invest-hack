from contextlib import nullcontext
from decimal import Decimal
import json
from types import SimpleNamespace
import uuid

import httpx
from pydantic import BaseModel, Field
import pytest

from app.cards.schema import SPECS, extraction_model
from app.llm.gateway import BudgetLedger, GatewayError, LLMGateway
from tests.test_gateway import FakeTransport, MemoryLedger, extract, http_error, response, settings


GEMINI_MESSAGE = ('The specified schema produces a constraint that has too many states for serving. '
                  'Typical causes are long array length limits or complex value matchers.')


def provider_failure(status=400, message='Provider returned error', raw_message=GEMINI_MESSAGE, usage=None, **extra):
    body = {'error': {'message': message, 'metadata': {
        'provider_name': 'Google AI Studio',
        'raw': json.dumps({'error': {'message': raw_message, 'status': 'INVALID_ARGUMENT', **extra}}),
    }}}
    if usage is not None:
        body['usage'] = usage
    return httpx.HTTPStatusError('provider failure', request=httpx.Request('POST', 'https://example.test'),
                                response=httpx.Response(status, json=body))


def schema_nodes(node):
    yield node
    for value in node.get('properties', {}).values():
        yield from schema_nodes(value)
    if isinstance(node.get('items'), dict):
        yield from schema_nodes(node['items'])


@pytest.mark.parametrize('code', [spec.code for spec in SPECS])
def test_provider_schema_is_small_flat_and_nullable_without_mutating_model(code):
    from app.llm.gateway import provider_schema
    model = extraction_model(code)
    original = model.model_json_schema()
    schema = provider_schema(model)
    forbidden = {'$defs', '$ref', 'anyOf', 'format', 'pattern', 'minimum', 'maximum', 'exclusiveMinimum',
                 'exclusiveMaximum', 'minLength', 'maxLength', 'minItems', 'maxItems', 'title', 'default'}
    for node in schema_nodes(schema):
        assert not forbidden.intersection(node)
        if 'properties' in node:
            assert node['required'] == list(node['properties'])
            assert node['additionalProperties'] is False
    assert schema['properties']['value']['type'] == ['object', 'null']
    assert schema['properties']['quote']['type'] == ['string', 'null']
    assert len(json.dumps(schema)) < len(json.dumps(original))
    assert model.model_json_schema() == original


def test_schema_keeps_short_descriptions_and_property_names():
    from typing import Literal
    from app.llm.gateway import provider_schema

    class Example(BaseModel):
        title: str = Field(description='short hint')
        default: str = Field(description='x' * 1000)
        kind: Literal['a', 'b'] | None = None

    schema = provider_schema(Example)
    assert set(schema['properties']) == {'title', 'default', 'kind'}
    assert schema['properties']['title']['description'] == 'short hint'
    assert len(schema['properties']['default'].get('description', '')) <= 160
    assert schema['properties']['kind']['type'] == ['string', 'null']
    assert schema['properties']['kind']['enum'] == ['a', 'b', None]


def test_gemini_states_error_retries_once_as_json_object():
    ledger, transport = MemoryLedger(), FakeTransport([provider_failure(), response()])
    result = extract(LLMGateway(settings=settings(), ledger=ledger, transport=transport))
    assert result.value.amount == '1000'
    assert [call[1]['response_format']['type'] for call in transport.calls] == ['json_schema', 'json_object']
    prompt = transport.calls[1][1]['messages'][-1]['content']
    assert '"properties"' in prompt and '"anyOf"' not in prompt
    first = ledger.records[0]
    assert first['cost'] == 0 and first['cost_source'] == 'request_rejected'
    assert first['error']['code'] == 'llm_schema_rejected'
    assert first['error']['http_status'] == 400
    assert first['error']['provider_name'] == 'Google AI Studio'
    assert first['error']['provider_status'] == 'INVALID_ARGUMENT'
    assert 'too many states' in first['error']['provider_message']


@pytest.mark.parametrize('hint', ['json_schema unsupported', 'response_format invalid', 'too many states',
                                'INVALID_ARGUMENT: schema invalid', 'structured output unsupported'])
def test_schema_failure_markers_allow_only_one_format_fallback(hint):
    ledger, transport = MemoryLedger(), FakeTransport([http_error(400, hint), http_error(400, hint)])
    with pytest.raises(GatewayError) as error:
        extract(LLMGateway(settings=settings(), ledger=ledger, transport=transport))
    assert error.value.code == 'llm_schema_rejected'
    assert str(error.value) == 'Модель отклонила схему ответа'
    assert len(transport.calls) == 2
    assert all(record['cost'] == 0 and record['cost_source'] == 'request_rejected' for record in ledger.records)


@pytest.mark.parametrize('status,code,message', [
    (400, 'llm_request_rejected', 'Модель отклонила запрос'),
    (401, 'llm_auth_error', 'Неверный ключ LLM'),
    (402, 'llm_insufficient_credits', 'Нет кредитов на OpenRouter'),
    (403, 'llm_access_denied', 'Провайдер LLM запретил доступ'),
    (404, 'llm_model_not_found', 'Модель или endpoint LLM не найдены'),
    (413, 'llm_request_too_large', 'Превышен размер запроса LLM'),
    (422, 'llm_request_rejected', 'Модель отклонила запрос'),
    (429, 'llm_rate_limited', 'Превышен лимит запросов LLM'),
])
def test_rejected_requests_release_budget_and_have_russian_field_reasons(status, code, message):
    from app.cards.export import REASONS
    ledger, transport = MemoryLedger(), FakeTransport([http_error(status)])
    with pytest.raises(GatewayError) as error:
        extract(LLMGateway(settings=settings(llm_max_retries=0), ledger=ledger, transport=transport))
    assert error.value.code == code and str(error.value) == message
    assert REASONS[code] == message
    assert ledger.records[0]['cost'] == 0 and ledger.records[0]['cost_source'] == 'request_rejected'


def test_rejections_do_not_exhaust_budget_after_many_calls():
    ledger = MemoryLedger()
    for _ in range(80):
        transport = FakeTransport([http_error(400)])
        with pytest.raises(GatewayError) as error:
            extract(LLMGateway(settings=settings(llm_daily_budget_usd='.01'), ledger=ledger, transport=transport))
        assert error.value.code == 'llm_request_rejected'
    assert sum(record['cost'] for record in ledger.records) == 0


@pytest.mark.parametrize('usage,expected_cost,expected_source', [
    ({'cost': '.25', 'prompt_tokens': 10}, Decimal('.25'), 'provider'),
    ({'prompt_tokens': 100, 'completion_tokens': 50}, Decimal('.00015'), 'config'),
    ({'cost': None}, None, 'unknown'),
])
def test_error_with_usage_keeps_actual_or_unknown_cost(usage, expected_cost, expected_source):
    ledger = MemoryLedger()
    with pytest.raises(GatewayError):
        extract(LLMGateway(settings=settings(), ledger=ledger,
                           transport=FakeTransport([provider_failure(401, usage=usage)])))
    assert ledger.records[0]['cost'] == expected_cost
    assert ledger.records[0]['cost_source'] == expected_source


@pytest.mark.parametrize('failure', [http_error(503), httpx.ReadTimeout('timeout')])
def test_ambiguous_failure_keeps_reservation(failure):
    ledger = MemoryLedger()
    with pytest.raises(GatewayError):
        extract(LLMGateway(settings=settings(llm_max_retries=0), ledger=ledger, transport=FakeTransport([failure])))
    assert ledger.records[0]['cost'] is None and ledger.records[0]['cost_source'] == 'unknown'


def test_provider_summary_redacts_secrets_and_ignores_raw_request():
    ledger = MemoryLedger()
    failure = provider_failure(401, message='Invalid key sk-or-v1-FAKE_SECRET Bearer FAKE_BEARER',
                               raw_message='Bad api_key="FAKE_CONFIGURED_KEY" token=FAKE_TOKEN ' + 'x' * 1200,
                               request={'messages': [{'content': 'SECRET_DOCUMENT'}]})
    configured = settings()
    configured.llm_api_key = 'FAKE_CONFIGURED_KEY'
    with pytest.raises(GatewayError):
        extract(LLMGateway(settings=configured, ledger=ledger, transport=FakeTransport([failure])))
    error = ledger.records[0]['error']
    dump = json.dumps(error)
    assert all(secret not in dump for secret in ('FAKE_SECRET', 'FAKE_BEARER', 'FAKE_CONFIGURED_KEY', 'FAKE_TOKEN', 'SECRET_DOCUMENT'))
    assert 'request' not in error and 'raw' not in error
    assert len(error['provider_message']) <= 500
    assert error['provider_name'] == 'Google AI Studio' and error['provider_status'] == 'INVALID_ARGUMENT'


def test_provider_message_echoing_document_is_not_persisted():
    from app.cards.schema import extraction_model
    secret = 'Эмитент обязуется поддерживать секретный коэффициент долга 17.'
    messages = [{'role': 'user', 'content': json.dumps({'fragments': [{'text': secret}]}, ensure_ascii=False)}]
    ledger = MemoryLedger()
    with pytest.raises(GatewayError):
        LLMGateway(settings=settings(), ledger=ledger, transport=FakeTransport([
            provider_failure(401, message='Rejected: ' + secret[:49], raw_message='Invalid input: ' + secret),
        ])).extract('face_value', messages, extraction_model('face_value'))
    assert 'Эмитент' not in json.dumps(ledger.records, ensure_ascii=False, default=str)


@pytest.mark.parametrize('mutation', ['page', 'currency', 'confidence', 'extra', 'long_quote'])
def test_full_pydantic_checks_still_reject_invalid_response(mutation):
    data = json.loads(response()['choices'][0]['message']['content'])
    if mutation == 'page': data['page'] = 0
    elif mutation == 'currency': data['value']['currency'] = 'rubles'
    elif mutation == 'confidence': data['confidence'] = 2
    elif mutation == 'extra': data['value']['unexpected'] = 'bad'
    else: data['quote'] = 'x' * 12001
    ledger, transport = MemoryLedger(), FakeTransport([response(json.dumps(data)), response(json.dumps(data))])
    with pytest.raises(GatewayError) as error:
        extract(LLMGateway(settings=settings(), ledger=ledger, transport=transport))
    assert error.value.code == 'invalid_json' and len(transport.calls) == 2
    assert all(record['cost_source'] == 'provider' for record in ledger.records)


def test_real_ledger_clears_rejected_reservation_and_counts_remaining_unknown():
    rows = {}

    class Session:
        def execute(self, *args): pass
        def scalars(self, query): return SimpleNamespace(all=lambda: list(rows.values()))
        def get(self, model, identifier): return rows[identifier]
        def add(self, entry):
            entry.id = uuid.uuid4()
            rows[entry.id] = entry
        def flush(self): pass

    ledger = BudgetLedger(SimpleNamespace(begin=lambda: nullcontext(Session())))
    configured = settings(llm_daily_budget_usd='1')
    identifier = ledger.reserve(Decimal('.8'), configured, 'extraction', 'fake', None, None, 1)
    ledger.complete(identifier, {}, Decimal(0), 'request_rejected', 1, {'code': 'llm_request_rejected'})
    assert rows[identifier].state == 'failed' and rows[identifier].reserved_usd == 0
    unknown = ledger.reserve(Decimal('.8'), configured, 'extraction', 'fake', None, None, 1)
    ledger.complete(unknown, {}, None, 'unknown', 1, {'code': 'llm_timeout'})
    assert rows[unknown].state == 'unknown' and rows[unknown].reserved_usd == Decimal('.8')
    with pytest.raises(GatewayError) as error:
        ledger.reserve(Decimal('.3'), configured, 'extraction', 'fake', None, None, 1)
    assert error.value.code == 'budget_exceeded'
