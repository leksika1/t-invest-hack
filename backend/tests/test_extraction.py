import uuid

from fastapi import HTTPException
from fastapi.testclient import TestClient
import pymupdf
import pytest

from app.cards import prompts
from app.cards.export import render_markdown
from app.cards.schema import SPECS, extraction_model
from app.cards.verification import result_template, verify
from app.config import Settings, get_settings
from app.documents.parser import chunks
from tests.fake_llm import FakeLLM


def test_generated_pdf_fake_llm_and_quote_guard_without_database():
    document_id = str(uuid.uuid4())
    doc = pymupdf.open(); page = doc.new_page()
    page.insert_htmlbox(page.rect, '''<p>9.5.1. Эмитент обязуется поддерживать отношение долга к EBITDA не более 3.</p>
    <p>9.5.2. Эмитент вправе досрочно погасить облигации по цене 100 процентов номинала.</p>
    <p>9.5.3. Владельцы предъявляют требования через депозитарий в течение 5 рабочих дней.</p>
    <p>9.5.4. Исполнение обеспечено поручительством ООО Пример в полном объёме.</p>
    <p>9.5.5. Номинальная стоимость составляет 1 000 рублей.</p>''')
    # Exercise the PDF text layer, not a prewritten string fixture.
    with pymupdf.open(stream=doc.tobytes(), filetype='pdf') as reopened:
        text = reopened[0].get_text('text', sort=True)
    doc.close()
    fragments, _ = chunks(text)
    context = [{'document_id': document_id, 'page': 1, **f} for f in fragments]
    results = {}
    for spec in SPECS:
        extracted = FakeLLM().extract(spec.code, prompts.messages(spec, context), extraction_model(spec.code))
        results[spec.code] = verify(spec.code, extracted, {(document_id, 1): text}, context, Settings(_env_file=None),
                                  {'amount': '1000', 'currency': 'RUB'} if spec.code == 'face_value' else None)
    assert results['face_value']['status'] == 'verified_iss'
    for code in ('covenants', 'issuer_call_option', 'offer_procedure', 'guarantee_security'):
        assert results[code]['status'] == 'extracted', results[code]
    assert results['cross_default']['status'] == 'needs_review' and results['cross_default']['value'] is None


def test_export_escapes_untrusted_content_and_preserves_review():
    fields = {s.code: {**result_template(s.code), 'user_status': None, 'user_value': None} for s in SPECS}
    fields['face_value'].update(value={'amount': '1000', 'currency': 'RUB'}, iss_value={'amount': '2000', 'currency': 'RUB'},
                               status='mismatch_iss', user_status='corrected', user_value={'amount': '1500', 'currency': 'RUB'})
    card = {'isin': 'RU000A10ATW2', 'version': 1, 'status': 'ready', 'header': {},
            'iss_fields': {s.code: fields[s.code] for s in SPECS if s.iss}, 'risk_fields': {s.code: fields[s.code] for s in SPECS if not s.iss},
            'documents': [{'filename': '<script>[x](javascript:alert)</script>', 'sha256': 'a'*64, 'origins': []}], 'journal': {}}
    text = render_markdown(card)
    assert '<script>' not in text and '&lt;script&gt;' in text
    assert all(value in text for value in ('1000', '1500', '2000', 'mismatch_iss', 'corrected'))


def test_admin_token_is_closed_and_constant_time_check(monkeypatch):
    from app.admin.router import authorize
    monkeypatch.setenv('ADMIN_TOKEN', 'test-secret');get_settings.cache_clear()
    try:
        authorize('test-secret')
        with pytest.raises(HTTPException) as error:
            authorize('wrong')
        assert error.value.status_code == 401
        monkeypatch.setenv('ADMIN_TOKEN', '');get_settings.cache_clear()
        with pytest.raises(HTTPException) as error:
            authorize('anything')
        assert error.value.status_code == 503
    finally:
        get_settings.cache_clear()


def test_rate_limit_http_retry_after(monkeypatch):
    from app.main import app
    monkeypatch.setattr('app.rate_limit.consume', lambda *a: 7)
    response = TestClient(app).post('/api/cards', json={'isin': 'RU000A10ATW2'})
    assert response.status_code == 429 and response.headers['Retry-After'] == '7'
