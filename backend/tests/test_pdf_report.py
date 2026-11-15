from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace
import uuid

from fastapi.testclient import TestClient
import pymupdf
import pytest

from app.cards.report import render_pdf, rosette_points
from app.cards.schema import DISCLAIMER, SPECS


@pytest.fixture
def report_card():
    def field(spec):
        return {'code': spec.code, 'value': None, 'iss_value': None, 'status': 'needs_review',
                'reason': 'llm_not_configured', 'quote': None, 'source': {}, 'evidence': [], 'user_status': None}
    card = {'isin': 'RU000A10CS75', 'version': 7, 'status': 'ready',
            'header': {'name': 'Аэрофлот П02-БО-02', 'issuer': 'ПАО «Аэрофлот — российские авиалинии»',
                       'inn': '7712040126', 'registration_number': '4B02-02-00010-A-002P'},
            'iss_fields': {spec.code: field(spec) for spec in SPECS if spec.iss},
            'risk_fields': {spec.code: field(spec) for spec in SPECS if not spec.iss},
            'documents': [{'id': 'doc', 'filename': 'Условия выпуска.pdf', 'status': 'ready', 'enabled': True,
                           'pages': 12, 'ocr_pages': 0, 'origins': [{'download_url': 'https://example.org/terms.pdf'}]}],
            'journal': {'documents_used': 1, 'models': ['test-model'], 'cost_usd': None,
                        'known_cost_usd': '0.01', 'unknown_cost_calls': 1, 'duration_ms': 1500}}
    card['risk_fields']['issuer_call_option'].update(value={'conditions': [{'trigger': 'По усмотрению эмитента'}]},
        status='extracted', reason=None, quote='Эмитент вправе досрочно погасить облигации по своему усмотрению.',
        document_id='doc', page=12, clause='6.4')
    card['iss_fields']['coupon_rate'].update(value=None, iss_value={'periods': [{'from': i, 'to': i, 'amount': '12.5'} for i in range(1, 61)]}, source={'kind': 'moex_iss'}, reason='iss_not_confirmed')
    card['iss_fields']['coupon_schedule'].update(value={'payments': []},
        iss_value={'payments': [{'number': i, 'payment_date': f'2027-{i:02}-13'} for i in range(1, 13)]}, status='verified_iss')
    return card


def test_pdf_cyrillic_embedded_fonts_coupons_provenance_and_a4(report_card):
    data = render_pdf(report_card, datetime(2026, 11, 15, tzinfo=timezone.utc))
    assert data.startswith(b'%PDF-')
    with pymupdf.open(stream=data, filetype='pdf') as doc:
        content = '\n'.join(page.get_text() for page in doc)
        for expected in [report_card['isin'], 'Аэрофлот', DISCLAIMER, 'Условия выпуска.pdf', 'стр. 12, п. 6.4',
                         'Известно выплат: 12', 'Источник графика — MOEX ISS', 'неизвестна', 'test-model']:
            assert expected in content
        assert any('terms.pdf#page=12' in link.get('uri', '') for page in doc for link in page.get_links())
        fonts = {font[3] for page in doc for font in page.get_fonts()}
        assert any('Golos' in font for font in fonts) and any('PTSerif' in font for font in fonts)
        assert 'известных периодов: 60' in content
        assert 'Из ISS, в документах не' in content
        for page in doc:
            assert round(page.rect.width) == 595 and round(page.rect.height) == 842
            assert f'Страница {page.number + 1}' in page.get_text()
            assert all(0 <= word[0] < word[2] <= page.rect.width + 1 and 0 <= word[1] < word[3] <= page.rect.height + 1 for word in page.get_text('words'))


def test_pdf_long_quotes_escape_markup_and_keep_human_correction_separate(report_card):
    field = report_card['risk_fields']['issuer_call_option']
    field.update(quote=('Условие <b>эмитента</b> & владельца. ' * 350) + 'Конец длинной цитаты.',
                 user_status='corrected', user_value={'conditions': [{'trigger': 'Правка пользователя'}]})
    data = render_pdf(report_card)
    with pymupdf.open(stream=data, filetype='pdf') as doc:
        content = '\n'.join(page.get_text() for page in doc)
        assert len(doc) > 3
        assert '<b>эмитента</b> & владельца.' in content
        assert 'Конец длинной цитаты.' in ' '.join(content.split())
        assert 'Правка пользователя' in content and 'По усмотрению эмитента' in content


def test_rosette_matches_frontend_seed():
    points = rosette_points('RU000A10CS75', 0)
    assert len(points) == 1441
    assert points[0] == (197.11, 98.13)
    assert points == rosette_points('RU000A10CS75', 0)
    assert points != rosette_points('RU000A10ATW2', 0)


def test_pdf_endpoint_ready_and_not_ready(monkeypatch, report_card):
    from app.main import app
    from app.cards import router
    identifier = uuid.uuid4()
    card = SimpleNamespace(status='ready', version=7)
    monkeypatch.setattr(router, 'required', lambda *args: card)
    monkeypatch.setattr(router, 'card_json', lambda *args: deepcopy(report_card))
    app.dependency_overrides[router.database] = lambda: None
    try:
        client = TestClient(app)
        response = client.get(f'/api/cards/{identifier}/export.pdf')
        assert response.status_code == 200
        assert response.headers['content-type'] == 'application/pdf'
        assert 'card-RU000A10CS75-v7.pdf' in response.headers['content-disposition']
        with pymupdf.open(stream=response.content, filetype='pdf') as pdf:
            assert 'Аэрофлот' in pdf[0].get_text()
        card.status = 'processing'
        assert client.get(f'/api/cards/{identifier}/export.pdf').status_code == 409
    finally:
        app.dependency_overrides.clear()
