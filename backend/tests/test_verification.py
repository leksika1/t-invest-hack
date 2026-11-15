import uuid

import pytest
from pydantic import ValidationError

from app.cards.schema import FIELDS, extraction_model, validate_value
from app.cards.verification import compare_iss, degraded_field, quote_matches, verify
from app.cards.retriever import bound_context
from app.config import Settings

DOC = str(uuid.uuid4())
QUOTE = 'Номинальная стоимость каждой облигации составляет 1 000 рублей.'


def found(code='face_value', **kwargs):
    return extraction_model(code).model_validate({'value': {'amount': '1000', 'currency': 'RUB'}, 'quote': QUOTE,
        'document_id': DOC, 'page': 1, 'clause': '9.5.1', 'confidence': .99, 'not_found': False, **kwargs})


def check(extracted, iss=None):
    return verify('face_value', extracted, {(DOC, 1): QUOTE}, [{'document_id': DOC, 'page': 1, 'clause': '9.5.1', 'text': QUOTE}], Settings(_env_file=None), iss)


def test_schema_15_typed_values_and_decimal_strings():
    assert len(FIELDS) == 15 and sum(s.iss for s in FIELDS.values()) == 8
    assert validate_value('face_value', {'amount': '1 000,00', 'currency': 'RUB'})['amount'] == '1000'
    with pytest.raises(ValidationError):
        validate_value('face_value', {'amount': 'NaN', 'currency': 'RUB'})
    with pytest.raises(ValidationError):
        validate_value('maturity_date', {'date': '2027-02-30'})


def test_confirmed_quote_and_normalized_iss_match_mismatch():
    assert check(found(), {'amount': '1000.00', 'currency': 'RUB'})['status'] == 'verified_iss'
    result = check(found(), {'amount': '2000', 'currency': 'RUB'})
    assert result['status'] == 'mismatch_iss'
    assert result['value']['amount'] == '1000' and result['iss_value']['amount'] == '2000'


@pytest.mark.parametrize('kwargs', [{'quote': 'Несуществующая цитата'}, {'page': 2}, {'document_id': str(uuid.uuid4())}, {'clause': '99.9'}])
def test_hallucinated_source_or_quote_is_hidden(kwargs):
    result = check(found(**kwargs))
    assert result['status'] == 'needs_review' and result['reason'] == 'quote_not_verified'
    assert result['value'] is None and result['quote'] is None


def test_value_not_supported_and_low_confidence():
    assert check(found(value={'amount': '9999', 'currency': 'RUB'}))['value'] is None
    result = check(found(confidence=.1))
    assert result['status'] == 'needs_review' and result['reason'] == 'low_confidence'
    assert check(found(ambiguous=True))['reason'] == 'conflicting_sources'


def test_quote_normalization_and_fuzzy_does_not_change_numbers_or_negations():
    assert quote_matches('Эмитент не выплачивает дивиденды, кроме исключений.', 'ЭМИТЕНТ НЕ выплачивает дивиденды, кроме исключений.', .95)
    assert quote_matches('Ёмкость «фонда» составляет 100 рублей.', 'Емкость "фонда" составляет 100 рублей.', .95)
    text = 'Эмитент обязан выплатить владельцу 1000 рублей в течение установленного срока.'
    assert not quote_matches(text.replace('1000', '1001'), text, .9)
    assert not quote_matches(text.replace('обязан', 'не обязан'), text, .9)


def test_coupon_graphs_and_partial_data():
    iss = {'periods': [{'from': 1, 'to': 1, 'rate_percent': '18', 'amount': '45'}, {'from': 2, 'to': 2, 'rate_percent': '18', 'amount': '45'}], 'formula': None}
    value = {'periods': [{'from': 1, 'to': 2, 'rate_percent': '18', 'amount': None}], 'formula': None}
    assert compare_iss('coupon_rate', value, iss) == 'verified_iss'
    value['periods'][0]['to'] = 1
    assert compare_iss('coupon_rate', value, iss) == 'verified_iss'
    assert compare_iss('coupon_type', {'type': 'fixed'}, None) == 'extracted'


def test_degraded_iss_has_no_pdf_quote_or_verified_status():
    result = degraded_field('face_value', {'amount': '1000', 'currency': 'RUB'}, 'https://iss.moex.com/iss/test')
    assert result['status'] == 'extracted' and result['quote'] is None and result['source']['kind'] == 'moex_iss'
    assert degraded_field('covenants', None, '')['reason'] == 'llm_not_configured'


def test_context_byte_token_bound():
    context = [{'document_id': DOC, 'page': 1, 'clause': None, 'text': 'Русский текст ' * 1000} for _ in range(20)]
    result = bound_context(context, top_k=3, max_tokens=1000)
    assert 0 < len(result) <= 3
    assert sum(len(r['text'].encode('utf-8')) + 64 for r in result) < 1000
