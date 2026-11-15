from decimal import Decimal
import json
import uuid

import httpx
import pytest

from app.cards.schema import extraction_model
from app.config import Settings
from app.llm.client import LLMClient
from app.llm.gateway import GatewayError, LLMGateway, usage_cost, utc_bounds


class MemoryLedger:
    def __init__(self):
        self.records = []

    def reserve(self, amount, settings, purpose, model, job_id, field_id, attempt):
        used = sum((r['cost'] if r['cost'] is not None else r['reserve']) for r in self.records)
        if settings.llm_daily_budget_usd <= 0 or used + amount > settings.llm_daily_budget_usd:
            raise GatewayError('budget_exceeded', 'Бюджет исчерпан')
        self.records.append({'reserve': amount, 'cost': None, 'purpose': purpose, 'attempt': attempt})
        return len(self.records) - 1

    def complete(self, identifier, usage, cost, cost_source, duration_ms, error, response_id=None):
        self.records[identifier].update(usage=usage, cost=cost, cost_source=cost_source, error=error)


class FakeTransport:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    def request(self, purpose, payload):
        self.calls.append((purpose, payload))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def settings(**kwargs):
    return Settings(llm_api_key='fake', llm_input_price_per_1m_usd='1', llm_output_price_per_1m_usd='1',
                    embedding_input_price_per_1m_usd='1', llm_max_output_tokens=256,
                    **kwargs, _env_file=None)


def response(content=None, usage=None):
    data = content if content is not None else json.dumps({'value': {'amount': '1000', 'currency': 'RUB'}, 'quote': 'Номинал 1000 рублей',
         'document_id': str(uuid.uuid4()), 'page': 1, 'clause': None, 'confidence': .99, 'not_found': False, 'evidence': []})
    return {'id': 'fake-response', 'choices': [{'message': {'content': data}}],
            'usage': usage if usage is not None else {'prompt_tokens': 100, 'completion_tokens': 50, 'cost': .0002}}


def extract(gateway):
    return gateway.extract('face_value', [{'role': 'user', 'content': 'Фрагменты'}], extraction_model('face_value'))


def http_error(status, text='error'):
    return httpx.HTTPStatusError('provider error', request=httpx.Request('POST', 'https://example.test'),
                                response=httpx.Response(status, text=text))


def test_provider_cost_and_fallback_including_zero():
    assert usage_cost({'cost': 0}, Decimal(5), Decimal(5)) == (Decimal(0), 'provider')
    assert usage_cost({'prompt_tokens': 100, 'completion_tokens': 50}, Decimal(1), Decimal(1)) == (Decimal('.00015'), 'config')
    assert usage_cost({}, Decimal(1), Decimal(1)) == (None, 'unknown')
    assert usage_cost({'cost': 'NaN'}, None, None) == (None, 'unknown')


def test_budget_blocks_before_transport():
    ledger, transport = MemoryLedger(), FakeTransport([response()])
    gateway = LLMGateway(settings=settings(llm_daily_budget_usd='0.000001'), ledger=ledger, transport=transport)
    with pytest.raises(GatewayError) as error:
        extract(gateway)
    assert error.value.code == 'budget_exceeded' and not transport.calls and not ledger.records


def test_log_success_and_validated_value():
    ledger = MemoryLedger()
    result = extract(LLMGateway(settings=settings(), ledger=ledger, transport=FakeTransport([response()])))
    assert result.value.amount == '1000'
    assert ledger.records[0]['cost'] == Decimal('.0002')
    assert ledger.records[0]['error'] is None


def test_json_schema_fallback_and_invalid_json_one_repair():
    ledger = MemoryLedger()
    transport = FakeTransport([http_error(400, 'json_schema unsupported'), response('{bad'), response()])
    result = extract(LLMGateway(settings=settings(), ledger=ledger, transport=transport))
    assert result.value.currency == 'RUB'
    assert [c[1]['response_format']['type'] for c in transport.calls] == ['json_schema', 'json_object', 'json_object']
    assert ledger.records[1]['error']['code'] == 'invalid_json'
    assert ledger.records[1]['cost'] is not None  # malformed JSON is still billed


def test_invalid_json_twice_is_error_not_infinite_retry():
    ledger = MemoryLedger(); transport = FakeTransport([response('{}'), response('{}')])
    with pytest.raises(GatewayError) as error:
        extract(LLMGateway(settings=settings(), ledger=ledger, transport=transport))
    assert error.value.code == 'invalid_json' and len(transport.calls) == 2


@pytest.mark.parametrize('failure', [http_error(429), http_error(503), httpx.ReadTimeout('timeout')])
def test_retry_and_error_logs(failure):
    ledger = MemoryLedger(); sleeps = []
    transport = FakeTransport([failure, response()])
    extract(LLMGateway(settings=settings(), ledger=ledger, transport=transport, sleep=sleeps.append))
    assert len(ledger.records) == 2 and ledger.records[0]['error'] and sleeps == [1]
    assert ledger.records[0]['cost'] is None  # retain reservation on uncertain failure


def test_embedding_gateway_and_dimension_validation():
    ledger = MemoryLedger()
    transport = FakeTransport([{'data': [{'index': 0, 'embedding': [1., 0., 0.]}], 'usage': {'prompt_tokens': 10, 'cost': 0}}])
    gateway = LLMGateway(settings=settings(embedding_model='fake-embedding', embedding_dim=3), transport=transport, ledger=ledger)
    assert gateway.embed(['текст']) == [[1., 0., 0.]]
    assert ledger.records[0]['purpose'] == 'embedding' and ledger.records[0]['cost'] == 0


def test_transport_attribution_without_real_call():
    requests = []
    def answer(request):
        requests.append(request)
        return httpx.Response(200, json=response(), request=request)
    client = httpx.Client(transport=httpx.MockTransport(answer))
    LLMClient(settings(public_base_url='https://tinvest.leksika1.ru'), client).request('extraction', {'model': 'fake'})
    assert requests[0].headers['HTTP-Referer'] == 'https://tinvest.leksika1.ru'
    assert requests[0].headers['X-Title'] == 'Bond Card MVP'
    assert requests[0].url.path == '/api/v1/chat/completions'


def test_no_key_and_no_embeddings_are_offline():
    gateway = LLMGateway(settings=Settings(_env_file=None), ledger=MemoryLedger(), transport=FakeTransport([]))
    assert gateway.embed(['text']) is None
    with pytest.raises(GatewayError) as error:
        extract(gateway)
    assert error.value.code == 'llm_not_configured'


def test_malformed_provider_envelope_is_logged_and_repaired():
    ledger=MemoryLedger();transport=FakeTransport([[],response()])
    extract(LLMGateway(settings=settings(),ledger=ledger,transport=transport))
    assert ledger.records[0]['error']['code']=='invalid_json'
    assert len(transport.calls)==2


def test_retry_after_and_blank_prices():
    failure=http_error(429); failure.response.headers['retry-after']='3'
    sleeps=[]
    extract(LLMGateway(settings=settings(),ledger=MemoryLedger(),transport=FakeTransport([failure,response()]),sleep=sleeps.append))
    assert sleeps==[3]
    assert Settings(llm_input_price_per_1m_usd='',_env_file=None).llm_input_price_per_1m_usd is None
