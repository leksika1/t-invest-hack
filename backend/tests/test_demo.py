import json
from pathlib import Path
from types import SimpleNamespace
import uuid

from fastapi.testclient import TestClient
import pytest

from app.cards.batch import extract_batch
from app.cards.schema import SPECS
from app.cards.retriever import full_document_context
from app.cards.verification import verify
from app.config import Settings
from app.llm.demo import DemoGateway, DEMO_NOTICE
from tests.test_llm_fallback import prepared_build


def recorded(isin):
    root = Path(__file__).parents[1] / 'eval'
    gold = json.loads((root / 'gold/demo' / f'{isin}.json').read_text(encoding='utf-8'))
    documents, pages = [], {}
    for doc in gold['documents']:
        identifier = str(uuid.uuid4())
        rows = json.loads((root / doc['pages_file']).read_text(encoding='utf-8'))['pages']
        documents.append({'id': identifier, 'sha256': doc['sha256'], 'filename': doc['filename'], 'pages': len(rows)})
        pages.update({(identifier, row['number']): row['text'] for row in rows})
    context = full_document_context(documents, pages, 150000)
    return gold, documents, pages, context


@pytest.mark.parametrize('isin', ['RU000A10CS75', 'RU000A10ATW2'])
def test_recorded_values_pass_real_quote_verification(isin):
    gold, docs, pages, context = recorded(isin)
    gateway = DemoGateway(isin=isin, documents=docs)
    assert gateway.engine == 'demo_recorded'
    for group in (True, False):
        specs = [s for s in SPECS if s.iss == group]
        answers = extract_batch(gateway, specs, context)
        for spec in specs:
            answer = answers[spec.code]
            result = verify(spec.code, answer, pages, context, Settings(_env_file=None))
            if answer.not_found:
                assert result['status'] == 'not_found'
            else:
                assert result['status'] == 'extracted', (isin, spec.code, result['reason'])
                assert str(answer.document_id) in {d['id'] for d in docs}


def test_default_is_demo_and_live_transport_is_blocked(monkeypatch):
    from app.llm.gateway import LLMGateway, GatewayError
    from app.cards.schema import extraction_model
    from app.llm.client import LLMClient
    monkeypatch.delenv('LLM_MODE', raising=False)
    settings = Settings(_env_file=None, llm_api_key='sk-ignored', embedding_model='ignored')
    assert settings.llm_mode == 'demo' and settings.llm_configuration_error is None
    class Forbidden:
        def request(self, *args):
            pytest.fail('Demo attempted a network request')
    gateway = LLMGateway(settings=settings, transport=Forbidden())
    assert gateway.embed(['text']) is None
    with pytest.raises(GatewayError):
        gateway.extract('face_value', [], extraction_model('face_value'))
    with pytest.raises(RuntimeError):
        LLMClient(settings=settings, client=Forbidden()).request('extraction', {})


def test_changed_hash_and_other_isin_use_literal_heuristics():
    _, docs, _, _ = recorded('RU000A10CS75')
    docs[0]['sha256'] = '0' * 64
    for isin in ('RU000A10CS75', 'RU000A999999'):
        gateway = DemoGateway(isin=isin, documents=docs)
        assert gateway.engine == 'demo_heuristic'
        identifier = str(uuid.uuid4())
        text = '9.5.1. Эмитент обязуется соблюдать ковенант: долг к EBITDA не более 3.'
        context = [{'document_id': identifier, 'page': 1, 'clause': '9.5.1', 'text': text}]
        answers = extract_batch(gateway, [s for s in SPECS if not s.iss], context)
        result = verify('covenants', answers['covenants'], {(identifier, 1): text}, context, Settings(_env_file=None))
        assert result['status'] == 'extracted' and result['quote'] in text
        assert answers['cross_default'].not_found


def test_demo_build_zero_cost_health_and_exports(prepared_build, monkeypatch):
    from app.cards import service
    from app.cards.export import render_markdown
    from app.cards.report import render_pdf
    import pymupdf
    settings = Settings(_env_file=None, llm_api_key='sk-ignored', embedding_model='ignored')
    factory, job, card, fields, _ = prepared_build
    card.input_snapshot.update(model='demo', llm_mode='demo', embedding_model=None)
    monkeypatch.setattr(service, 'get_settings', lambda: settings)
    def forbidden(**kwargs):
        pytest.fail('Demo must ignore the live gateway factory')
    service.build_card(factory, job, gateway_factory=forbidden)
    assert card.status == 'ready'
    assert card.journal['models'] == ['demo'] and card.journal['cost_usd'] == '0'
    assert card.journal['generation_calls'] == 0 and 'llm_demo_mode' in card.journal['warnings']
    assert all(field.source['engine'] == 'demo_heuristic' for field in fields.values())
    for field in fields.values():
        field.user_status = field.user_value = field.reviewed_at = None
    assert 'Демо-режим' in service.field_json(fields['cross_default'])['reason_message']
    from app import main
    monkeypatch.setattr(main, 'settings', settings)
    monkeypatch.setattr(main, 'check_db', lambda: True)
    health = TestClient(main.app).get('/api/health').json()
    assert health['llm'] == 'demo' and health['llm_mode'] == 'demo'
    data = {'isin': 'RU000A10CS75', 'version': 1, 'status': 'ready', 'header': {}, 'documents': [],
            'iss_fields': {s.code: service.field_json(fields[s.code]) for s in SPECS if s.iss},
            'risk_fields': {s.code: service.field_json(fields[s.code]) for s in SPECS if not s.iss}, 'journal': card.journal}
    assert DEMO_NOTICE in render_markdown(data)
    with pymupdf.open(stream=render_pdf(data), filetype='pdf') as pdf:
        assert 'Демо-режим: ответы модели имитированы' in pdf[0].get_text()

def test_quote_anchors_keep_dates_and_clause_references_exact():
    from app.cards.verification import anchors_supported
    assert anchors_supported('2025-02-28', 'Дата: «28» февраля 2025 года.')
    assert not anchors_supported('2025-02-27', 'Дата: «28» февраля 2025 года.')
    assert anchors_supported('п. 9.5.2', 'В соответствии с п. 9.5.2 Программы.')
    assert not anchors_supported('п. 9.5.3', 'В соответствии с п. 9.5.2 Программы.')
    assert not anchors_supported('п. 9.5.2', 'В соответствии с п. 9.5.20 Программы.')
