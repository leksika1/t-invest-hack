import json
from pathlib import Path
import uuid

import pytest
from pydantic import ValidationError

from app.cards import prompts, service
from app.cards.retriever import bound_context, context_token_bound, full_document_context, retrieval_weight
from app.cards.schema import FIELDS, SPECS, extraction_model
from app.cards.verification import compare_iss, verify
from app.config import Settings
from app.llm.gateway import GatewayError, LLMGateway
from tests.fake_llm import FakeLLM
from tests.test_gateway import FakeTransport, MemoryLedger, response
from tests.test_llm_fallback import prepared_build  # noqa: F401 (shared offline build fixture)

RECORDED = json.loads((Path(__file__).parent/'fixtures/llm/quality_raw.json').read_text(encoding='utf-8'))
DOC = str(uuid.uuid4())


@pytest.mark.parametrize('record', RECORDED, ids=lambda r: r['isin'] + '-' + r['code'])
def test_recorded_raw_responses_validate_and_empty_not_found_becomes_null(record):
    result = extraction_model(record['code']).model_validate_json(record['raw'])
    if json.loads(record['raw'])['not_found']:
        assert result.value is None
    if not record['original_valid']:
        assert result.not_found and result.value is None


@pytest.mark.parametrize('value', [{}, [], '', ' ', {'items': []}, {'items': [{'condition': None}]}, {'items': None}])
def test_semantically_empty_not_found_values(value):
    model = extraction_model('covenants').model_validate({'not_found': True, 'value': value})
    assert model.value is None


@pytest.mark.parametrize('value', [{'items': [{'condition': 'Долг не выше 3'}]}, {'items': [0]}, {'items': [False]}])
def test_nonempty_not_found_is_still_an_error(value):
    with pytest.raises(ValidationError):
        extraction_model('covenants').model_validate({'not_found': True, 'value': value})


def test_full_context_is_complete_ordered_labeled_and_threshold_inclusive():
    other = str(uuid.uuid4())
    docs = [{'id': DOC, 'filename': 'Решение.pdf', 'pages': 2}]
    pages = {(DOC, 2): '9.2. Эмитент вправе погасить выпуск.', (DOC, 1): 'Титул.\n9.1. Указывается возможность погашения.',
             (other, 1): 'Чужой выключенный документ'}
    context = full_document_context(docs, pages, 150000)
    assert {r['page'] for r in context} == {1, 2} and all(r['document_id'] == DOC for r in context)
    assert context[-1]['previous_clause'] == '9.1' and '9.2.' in context[-1]['text']
    assert '[документ ' + DOC + ': Решение.pdf, стр. 2' in context[-1]['label']
    assert 'Указывается' in ' '.join(r['text'] for r in context)  # retained in the full document
    size = context_token_bound(context)
    assert full_document_context(docs, pages, size) == context
    assert full_document_context(docs, pages, size - 1) is None
    assert full_document_context(docs, pages, 0) is None
    assert full_document_context(docs, {(DOC, 1): 'only one page'}, 150000) is None


@pytest.mark.parametrize('isin', ['RU000A10ATW2', 'RU000A10CS75'])
def test_saved_demo_pdf_sets_fit_default_full_context(isin):
    # Only corpus coverage is asserted; the draft legal annotations are not an oracle.
    root = Path(__file__).parents[1] / 'eval'
    gold = json.loads((root / 'gold/demo' / (isin + '.json')).read_text(encoding='utf-8'))
    documents, pages = [], {}
    for document in gold['documents']:
        rows = json.loads((root / document['pages_file']).read_text(encoding='utf-8'))['pages']
        documents.append({'id': document['document_id'], 'filename': document['filename'], 'pages': len(rows)})
        pages.update({(document['document_id'], row['number']): row['text'] for row in rows})
    result = full_document_context(documents, pages, Settings(_env_file=None).extraction_full_context_max_tokens)
    assert result is not None and len(result) == len(pages) == 22
    assert all(record['text'] == pages[(record['document_id'], record['page'])] for record in result)


@pytest.mark.parametrize('limit,mode,expected_retrievals', [(150000, 'full_document', 0), (0, 'retrieval', 2), (10, 'retrieval', 2)])
def test_build_shares_full_documents_between_two_batches(prepared_build, monkeypatch, limit, mode, expected_retrievals):
    factory, job, card, fields, _ = prepared_build
    monkeypatch.setattr(service, 'get_settings', lambda: Settings(llm_api_key='fake', extraction_full_context_max_tokens=limit, _env_file=None))
    old_retrieve, queries, inputs = service.retrieve, [], {}
    def retrieve(*args):
        queries.append(args[-1])
        return old_retrieve(*args)
    class RecordingLLM(FakeLLM):
        def extract(self, code, messages, output_model, *, batch=False):
            inputs[code] = json.loads(messages[1]['content'])['fragments']
            return super().extract(code, messages, output_model, batch=batch)
    monkeypatch.setattr(service, 'retrieve', retrieve)
    service.build_card(factory, job, gateway_factory=RecordingLLM)
    assert len(queries) == expected_retrievals
    assert card.journal['risk_context_mode'] == mode
    if mode == 'full_document':
        assert all('label' in record for spec in SPECS if not spec.iss for record in inputs[spec.code])
        assert not any(not FIELDS[code].iss for code in queries)
    assert fields['face_value'].status == 'verified_iss'
    assert fields['covenants'].status == 'extracted'


def test_boilerplate_is_downweighted_but_actual_conditions_survive():
    template = '5.6. Указывается возможность досрочного погашения.'
    actual = '5.6.1. Эмитент обязуется выплатить владельцу номинальную стоимость.'
    assert retrieval_weight(template) < retrieval_weight(actual)
    assert retrieval_weight('Для облигаций с ипотечным покрытием указываются особенности.') < 1
    assert retrieval_weight('В случае если эмитент нарушает обязательство, владелец вправе требовать погашения.') == 1
    assert retrieval_weight('Прием оферт потенциальных приобретателей с предложением заключить предварительный договор.', 'offer_procedure') < 1
    assert bound_context([{'document_id': DOC, 'page': 1, 'clause': None, 'text': template}], 1, 1000)[0]['text'] == template


def test_each_risk_prompt_has_definition_pitfalls_and_common_document_prefix():
    context = [{'document_id': DOC, 'page': 1, 'clause': None, 'text': 'Текст'}]
    for spec in SPECS:
        if not spec.iss:
            payload = json.loads(prompts.messages(spec, context)[1]['content'])
            assert spec.definition and spec.pitfalls
            assert payload['definition'] == spec.definition and payload['pitfalls'] == spec.pitfalls
            assert list(payload)[0] == 'fragments'
    assert 'НЕ процедура размещения' in FIELDS['offer_procedure'].pitfalls
    assert 'самого эмитента' in FIELDS['issuer_restrictions'].definition
    assert '17.1' in FIELDS['holder_early_redemption'].definition


def document_result(code='face_value', value=None, quote='Номинал 1000 рублей.', **overrides):
    data = {'not_found': False, 'value': value if value is not None or overrides.get('not_found') else {'amount': '1000', 'currency': 'RUB'}, 'quote': quote,
            'document_id': DOC, 'page': 1, 'clause': None, 'confidence': .99, **overrides}
    return extraction_model(code).model_validate(data)


def check(code, extracted, iss, quote=None):
    quote = quote or extracted.quote or 'Текст документа'
    return verify(code, extracted, {(DOC, 1): quote}, [{'document_id': DOC, 'page': 1, 'clause': None, 'text': quote}],
                  Settings(_env_file=None), iss, iss_url='https://iss.example/securities/test.json')


@pytest.mark.parametrize('change', [{'not_found': True, 'value': None}, {'quote': 'Выдуманная цитата'},
                                 {'value': {'amount': '9999', 'currency': 'RUB'}}, {'confidence': .1}])
def test_iss_remains_visible_when_document_does_not_confirm(change):
    extracted = document_result(**change)
    result = check('face_value', extracted, {'amount': '1000', 'currency': 'RUB'}, 'Номинал 1000 рублей.')
    assert result['status'] == 'extracted' and result['source']['kind'] == 'moex_iss'
    assert result['reason'] == 'iss_not_confirmed' and result['value']['amount'] == '1000'
    assert result['quote'] is None and not result['not_found']


def test_real_document_mismatch_and_ambiguity_are_distinct():
    result = check('face_value', document_result(), {'amount': '2000', 'currency': 'RUB'})
    assert result['status'] == 'mismatch_iss' and result['value']['amount'] == '1000' and result['iss_value']['amount'] == '2000'
    result = check('face_value', document_result(ambiguous=True), {'amount': '1000', 'currency': 'RUB'})
    assert result['status'] == 'needs_review' and result['reason'] == 'conflicting_sources'


def test_first_coupon_can_be_compared_without_full_graph():
    value = {'periods': [{'from': 1, 'to': 1, 'rate_percent': '18'}]}
    iss = {'periods': [{'from': 1, 'to': 2, 'rate_percent': '18'}]}
    quote = 'Ставка первого купона 18 процентов.'
    result = check('coupon_rate', document_result('coupon_rate', value, quote), iss)
    assert result['status'] == 'verified_iss' and result['reason'] == 'iss_partial_confirmation'
    iss['periods'][0]['rate_percent'] = '19'
    assert check('coupon_rate', document_result('coupon_rate', value, quote), iss)['status'] == 'mismatch_iss'


def test_full_page_verification_keeps_clause_boundaries():
    text = '9.1. Номинал 1000 рублей.\n9.2. Эмитент вправе погасить выпуск.'
    context = full_document_context([{'id': DOC, 'filename': 'a.pdf', 'pages': 1}], {(DOC, 1): text}, 150000)
    extracted = document_result(quote='Номинал 1000 рублей.', clause='9.1')
    assert verify('face_value', extracted, {(DOC, 1): text}, context, Settings(_env_file=None))['status'] == 'extracted'
    extracted = document_result(quote='Номинал 1000 рублей.', clause='9.2')
    assert verify('face_value', extracted, {(DOC, 1): text}, context, Settings(_env_file=None))['reason'] == 'quote_not_verified'


def test_no_retrieval_context_still_shows_available_iss(prepared_build, monkeypatch):
    factory, job, card, fields, _ = prepared_build
    monkeypatch.setattr(service, 'get_settings', lambda: Settings(llm_api_key='fake', extraction_full_context_max_tokens=0, _env_file=None))
    monkeypatch.setattr(service, 'retrieve', lambda *args: ([], []))
    service.build_card(factory, job, gateway_factory=FakeLLM)
    assert fields['face_value'].status == 'extracted' and fields['face_value'].source['kind'] == 'moex_iss'
    assert fields['face_value'].reason == 'iss_not_confirmed'


def test_empty_positive_risk_is_not_a_confirmed_condition():
    extracted = extraction_model('holder_early_redemption').model_validate({'not_found': False, 'value': {'grounds': []}})
    result = verify('holder_early_redemption', extracted, {}, [], Settings(_env_file=None))
    assert result['status'] == 'needs_review' and result['value'] is None and result['reason'] == 'empty_extraction'


def test_single_amortization_event_with_different_date_is_mismatch():
    value = {'events': [{'date': '2028-01-01', 'amount': '1000', 'percent_base': 'initial_face_value'}]}
    iss = {'events': [{'date': '2028-02-01', 'amount': '1000', 'percent_base': 'initial_face_value'}]}
    assert compare_iss('amortization', value, iss) == 'mismatch_iss'


def test_coupon_frequency_period_and_count_can_confirm_partial_schedule():
    value = {'payments': [], 'period_days': 30, 'frequency_per_year': 12, 'total_payments': 2}
    iss = {'payments': [{'number': 1, 'start': '2026-01-01', 'end': '2026-01-31', 'payment_date': '2026-01-31'},
                        {'number': 2, 'start': '2026-01-31', 'end': '2026-03-02', 'payment_date': '2026-03-02'}], 'frequency_per_year': 12}
    quote = 'Купонный период 30 дней, 12 выплат в год, всего 2 купона.'
    result = check('coupon_schedule', document_result('coupon_schedule', value, quote), iss)
    assert result['status'] == 'verified_iss' and result['reason'] == 'iss_partial_confirmation'
    value['total_payments'] = 3
    assert compare_iss('coupon_schedule', value, iss) == 'mismatch_iss'


def test_invalid_json_summary_contains_only_paths_and_codes():
    data = json.loads(response()['choices'][0]['message']['content'])
    data.update(confidence=2, quote='SECRET_DOCUMENT', sk_SECRET_DOCUMENT='secret')
    ledger = MemoryLedger()
    gateway = LLMGateway(settings=Settings(llm_api_key='fake', _env_file=None), ledger=ledger,
                         transport=FakeTransport([response(json.dumps(data)), response(json.dumps(data))]))
    with pytest.raises(GatewayError):
        gateway.extract('face_value', [{'role': 'user', 'content': 'Текст'}], extraction_model('face_value'))
    detail = ledger.records[0]['error']['validation_error']
    assert 'confidence: less_than_equal' in detail and len(detail) <= 500
    assert 'SECRET_DOCUMENT' not in detail and 'secret' not in detail


def test_default_budget_supports_ten_near_threshold_cards_without_cache():
    configured = Settings(llm_api_key='fake', _env_file=None)
    context = full_document_context([{'id': DOC, 'filename': 'decision.pdf', 'pages': 1}], {(DOC, 1): 'x' * 149000}, 150000)
    assert context is not None
    ledger = MemoryLedger()
    gateway = LLMGateway(settings=configured, ledger=ledger, transport=FakeTransport([response('{"not_found":true,"value":null}') for _ in SPECS]))
    for spec in SPECS:
        records = bound_context(context, configured.retrieval_top_k, configured.retrieval_max_tokens) if spec.iss else context
        gateway.extract(spec.code, prompts.messages(spec, records), extraction_model(spec.code))
    assert len(ledger.records) == 15
    assert 10 * sum(record['reserve'] for record in ledger.records) <= configured.llm_daily_budget_usd
