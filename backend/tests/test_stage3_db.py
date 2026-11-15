from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path

from fastapi.testclient import TestClient
import pymupdf
import pytest
from sqlalchemy import func, select

from app.cards.service import build_card, card_json, create_card
from app.config import Settings, get_settings
from app.iss.normalize import normalize
from app.llm.gateway import BudgetLedger, GatewayError
from app.models import Card, CardField, Issue, Job, LLMCall, RateLimitEvent
from app.rate_limit import consume
from tests.fake_llm import FakeLLM

pytestmark = pytest.mark.db


@pytest.fixture
def card_setup(db_factory, tmp_path, monkeypatch):
    from app.issues import router
    from app.documents.sources import MoexIssSource
    raw = json.loads((Path(__file__).parent/'fixtures/iss/RU000A1048A9.json').read_text(encoding='utf-8'))
    monkeypatch.setattr(MoexIssSource, 'resolve', lambda self, isin: {'secid': isin, 'raw': raw, **normalize(raw)})
    monkeypatch.setattr(router, 'get_sessionmaker', lambda: db_factory)
    monkeypatch.setenv('FILES_DIR', str(tmp_path))
    monkeypatch.setenv('LLM_API_KEY', 'fake')
    monkeypatch.setenv('ADMIN_TOKEN', 'test-admin')
    get_settings.cache_clear()
    document = pymupdf.open(); page = document.new_page()
    page.insert_htmlbox(page.rect, '''<p>9.5.1. Эмитент обязуется поддерживать отношение долга к EBITDA не более 3.</p>
      <p>9.5.2. Эмитент вправе досрочно погасить облигации по цене 100 процентов номинала.</p>
      <p>9.5.3. Владельцы предъявляют требования через депозитарий в течение 5 рабочих дней.</p>
      <p>9.5.4. Исполнение обеспечено поручительством ООО Пример в полном объёме.</p>
      <p>9.5.5. Номинальная стоимость составляет 1 000 рублей.</p>''')
    data = document.tobytes(); document.close()
    from app.main import app
    client = TestClient(app)
    uploaded = client.post('/api/issues/RU000A1048A9/documents', files={'files': ('decision.pdf', data, 'application/pdf')})
    assert uploaded.status_code == 202, uploaded.text
    with db_factory.begin() as session:
        card, job = create_card(session, 'RU000A1048A9')
        card_id, job_id = card.id, job.id
    with db_factory() as session:
        job = session.get(Job, job_id)
    return client, card_id, job, uploaded.json()['documents'][0]['id']


def test_fake_llm_pipeline_review_export_and_snapshot(db_factory, card_setup):
    client, card_id, job, document_id = card_setup
    # Selection after POST affects future versions, not this frozen input.
    assert client.patch('/api/issues/RU000A1048A9/documents/'+document_id, json={'enabled': False}).status_code == 200
    build_card(db_factory, job, gateway_factory=FakeLLM)
    response = client.get('/api/cards/'+str(card_id)); assert response.status_code == 200
    card = response.json()
    assert card['status'] == 'ready' and len(card['iss_fields']) == 8 and len(card['risk_fields']) == 7
    assert card['iss_fields']['face_value']['status'] == 'verified_iss'
    for code in ('covenants', 'issuer_call_option', 'offer_procedure', 'guarantee_security'):
        assert card['risk_fields'][code]['status'] == 'extracted', card['risk_fields'][code]
    hallucination = card['risk_fields']['cross_default']
    assert hallucination['value'] is None and hallucination['reason'] == 'quote_not_verified'
    base='/api/cards/'+str(card_id)
    original=card['iss_fields']['face_value']
    corrected=client.patch(base+'/fields/face_value',json={'user_status':'corrected','user_value':{'amount':'1100','currency':'RUB'}})
    assert corrected.status_code==200 and corrected.json()['value']==original['value']
    assert corrected.json()['user_value']['amount']=='1100'
    assert client.patch(base+'/fields/face_value',json={'user_status':'corrected','user_value':{'bad':'value'}}).status_code==422
    assert client.patch(base+'/fields/cross_default',json={'user_status':'confirmed'}).status_code==409
    export=client.get(base+'/export.md')
    assert export.status_code==200 and '1100' in export.text and 'quote_not_verified' not in export.text
    assert 'Несуществующая цитата' not in export.text
    assert client.get('/api/cards?isin=RU000A1048A9').json()['total']==1
    assert card['documents'][0]['origins'][0]['source']=='upload'
    assert card['journal']['pages']==1


def test_no_key_uses_iss_and_no_model_calls(db_factory, card_setup, monkeypatch):
    client, card_id, job, _=card_setup
    monkeypatch.setenv('LLM_API_KEY','');get_settings.cache_clear()
    build_card(db_factory, job)
    card=client.get('/api/cards/'+str(card_id)).json()
    assert card['iss_fields']['face_value']['status']=='extracted'
    assert card['iss_fields']['face_value']['source']['kind']=='moex_iss'
    assert card['iss_fields']['face_value']['quote'] is None
    assert all(f['reason']=='llm_not_configured' for f in card['risk_fields'].values())
    with db_factory() as session:
        assert session.scalar(select(func.count()).select_from(LLMCall))==0


def test_budget_parallel_reservations_and_utc(db_factory):
    ledger=BudgetLedger(db_factory)
    settings=Settings(llm_daily_budget_usd='1',_env_file=None)
    with db_factory.begin() as session:
        session.add(LLMCall(purpose='extraction',model='old',duration_ms=0,state='succeeded',cost_usd='999',reserved_usd=0,
                            created_at=datetime.now(timezone.utc)-timedelta(days=2)))
    def reserve():
        try:
            return ledger.reserve(Decimal('.6'),settings,'extraction','fake',None,None,1)
        except GatewayError:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        identifiers=list(pool.map(lambda _: reserve(), range(2)))
    assert sum(identifier is not None for identifier in identifiers)==1
    identifier=next(i for i in identifiers if i is not None)
    ledger.complete(identifier,{'prompt_tokens':10,'cost':.2},Decimal('.2'),'provider',12,None,'fake-id')
    assert reserve() is not None


def test_admin_auth_stats_logs(db_factory, card_setup):
    client, _, job, _=card_setup
    assert client.get('/api/admin/stats').status_code==401
    assert client.get('/api/admin/stats',headers={'X-Admin-Token':'wrong'}).status_code==401
    ledger=BudgetLedger(db_factory);settings=Settings(llm_daily_budget_usd='1',_env_file=None)
    identifier=ledger.reserve(Decimal('.1'),settings,'extraction','fake',job.id,None,1)
    ledger.complete(identifier,{},None,'unknown',40,{'code':'llm_timeout','message':'timeout'})
    success=ledger.reserve(Decimal('.1'),settings,'extraction','fake',job.id,None,1)
    ledger.complete(success,{'cost':.02},Decimal('.02'),'provider',12,None)
    headers={'X-Admin-Token':'test-admin'}
    stats=client.get('/api/admin/stats',headers=headers)
    assert stats.status_code==200 and stats.json()['errors']==1 and stats.json()['unknown_cost_calls']==1
    calls=client.get('/api/admin/llm-calls?error=true',headers=headers)
    assert calls.json()['total']==1 and calls.json()['items'][0]['state']=='unknown'
    assert 'fake-key' not in calls.text


def test_shared_rate_limiter(db_factory):
    assert consume(db_factory,'client','cards',1,60) is None
    assert consume(db_factory,'client','cards',1,60) > 0
    assert consume(db_factory,'other-client','cards',1,60) is None


def test_gateway_attempts_are_persisted(db_factory):
    from app.llm.gateway import LLMGateway
    from app.cards.schema import extraction_model
    from tests.test_gateway import FakeTransport, http_error, response, settings
    gateway = LLMGateway(factory=db_factory, settings=settings(), transport=FakeTransport([http_error(503), response()]), sleep=lambda seconds: None)
    gateway.extract('face_value', [{'role':'user','content':'Номинал'}], extraction_model('face_value'))
    with db_factory() as session:
        calls=session.scalars(select(LLMCall).order_by(LLMCall.created_at)).all()
        assert len(calls)==2
        assert any(c.error and c.state=='unknown' and c.reserved_usd > 0 for c in calls)
        assert any(c.cost_source=='provider' and c.tokens_in==100 and c.state=='succeeded' for c in calls)


def test_vector_retrieval_is_scoped_to_snapshot(db_factory):
    from app.cards.retriever import retrieve
    from app.issues.service import ensure_issue
    from app.models import Document, Page, Chunk
    from uuid import uuid4
    vector=[1.0]+[0.0]*1023
    with db_factory.begin() as session:
        issue=ensure_issue(session,'RU000A10ATW2'); other=ensure_issue(session,'RU000A10CS75')
        identifiers=[]
        for owner in [issue,other]:
            doc=Document(issue_id=owner.id,filename='a.pdf',type='decision',sha256='b'*64,path='/fake',enabled=False,status='ready',pages=1,ocr_pages=0)
            session.add(doc);session.flush();identifiers.append(doc.id)
            page=Page(document_id=doc.id,number=1,text='Реквизиты компании',ocr=False)
            session.add(page);session.flush()
            session.add(Chunk(document_id=doc.id,page_id=page.id,text=page.text,embedding=vector,embedding_model='demo-vector'))
        issue_id=issue.id
    class QueryEmbedding:
        def embed(self, texts):return [vector]
    context,warnings=retrieve(db_factory,issue_id,[identifiers[0]],'совершенно другой поиск',Settings(embedding_model='demo-vector',_env_file=None),QueryEmbedding())
    assert context and not warnings
    assert {record['document_id'] for record in context}=={str(identifiers[0])}


def test_retrieval_fallback_preserves_page_order(db_factory):
    from app.cards.retriever import retrieve
    from app.issues.service import ensure_issue
    from app.models import Document, Page, Chunk
    from uuid import UUID
    with db_factory.begin() as session:
        issue = ensure_issue(session, 'RU000A10ATW2')
        doc = Document(issue_id=issue.id, filename='ordered.pdf', type='decision', sha256='c'*64,
                       path='/fake', enabled=True, status='ready', pages=2, ocr_pages=0)
        session.add(doc); session.flush()
        for number, identifier in [(1, UUID(int=2)), (2, UUID(int=1))]:
            page = Page(document_id=doc.id, number=number, text='Реквизиты компании', ocr=False)
            session.add(page); session.flush()
            session.add(Chunk(id=identifier, document_id=doc.id, page_id=page.id, text=page.text))
        issue_id, document_id = issue.id, doc.id
    context, warnings = retrieve(db_factory, issue_id, [document_id], 'ковенанты',
                                 Settings(embedding_model='', _env_file=None), None)
    assert not warnings
    assert [fragment['page'] for fragment in context] == [1, 2]
