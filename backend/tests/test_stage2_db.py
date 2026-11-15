from datetime import datetime, timedelta, timezone
import uuid

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select, text

from app.config import Settings, get_settings
from app.indexing.search import search
from app.issues.service import ensure_issue, new_job, put_document
from app.jobs.worker import claim, finish
from app.models import Chunk, Document, DocumentOrigin, Job, Page

pytestmark = pytest.mark.db


def test_fts_russian_inflections_and_issue_isolation(db_factory):
    with db_factory.begin() as session:
        first, second = ensure_issue(session, "RU000A10ATW2"), ensure_issue(session, "RU000A10CS75")
        for issue in (first, second):
            doc = Document(issue_id=issue.id, type="decision", filename="a.pdf", sha256="a"*64, path="/fake", status="ready", enabled=True, pages=1, ocr_pages=0)
            session.add(doc);session.flush()
            page = Page(document_id=doc.id, number=1, text="Обязательства эмитента и ковенанты", ocr=False)
            session.add(page);session.flush()
            session.add(Chunk(document_id=doc.id, page_id=page.id, text=page.text, clause="9.5.1"))
        session.flush()
        found = search(session, first.id, "обязательство ковенант")
        assert len(found) == 1
        assert not search(session, first.id, "ковенант", document_ids=[])
        session.execute(text("SELECT '[1,2,3]'::vector <=> '[1,2,3]'::vector"))


def test_worker_claim_skip_locked_lease_and_owner(db_factory):
    settings = Settings(_env_file=None)
    with db_factory.begin() as session:
        a = new_job(session, "ingest", {"document_ids": []})
        b = new_job(session, "ingest", {"document_ids": []})
        aid, bid = a.id, b.id
    with db_factory.begin() as session:
        session.scalar(select(Job).where(Job.id == aid).with_for_update())
        picked = claim(db_factory, "worker-b", settings)
        assert picked.id == bid
    picked = claim(db_factory, "worker-a", settings)
    assert picked.id == aid
    finish(db_factory, aid, "wrong-owner", {"wrong": True})
    with db_factory.begin() as session:
        a = session.get(Job, aid)
        assert a.status == "running"
        a.lease_until = datetime.now(timezone.utc) - timedelta(seconds=1)
    recovered = claim(db_factory, "worker-c", settings)
    assert recovered.id == aid and recovered.attempts == 2
    finish(db_factory, aid, "worker-c", {"ok": True})
    with db_factory() as session:
        assert session.get(Job, aid).status == "succeeded"


def test_upload_dedupe_origins_selection_and_job_api(db_factory, tmp_path, monkeypatch, pdf_bytes):
    from app.main import app
    from app.issues import router
    monkeypatch.setattr(router, "get_sessionmaker", lambda: db_factory)
    monkeypatch.setenv("FILES_DIR", str(tmp_path));get_settings.cache_clear()
    client = TestClient(app)
    url = "/api/issues/RU000A10ATW2/documents"
    def upload():
        return client.post(url, files=[("files", ("decision.pdf", pdf_bytes, "application/pdf"))], data={"document_types": "decision"})
    first = upload();assert first.status_code == 202, first.text
    did = first.json()["documents"][0]["id"]
    assert client.get("/api/jobs/" + first.json()["job_id"]).json()["status"] == "queued"
    assert client.patch(url + "/" + did, json={"enabled": False}).status_code == 200
    second = upload();assert second.json()["documents"][0]["duplicate"]
    assert not second.json()["documents"][0]["enabled"]
    listing = client.get(url).json()
    assert listing["total"] == 1 and listing["items"][0]["origins"][0]["source"] == "upload"
    assert client.get("/api/issues/RU000A10ATW2").json()["iss"]["status"] == "unavailable"
    assert client.patch(url + "/" + did, json={"enabled": "false"}).status_code == 422
    assert client.post(url, files=[("files", ("bad.pdf", b"bad", "application/pdf"))]).status_code == 415
    one=client.post("/api/issues/RU000A10ATW2/collect");two=client.post("/api/issues/RU000A10ATW2/collect")
    assert one.json()["job_id"] == two.json()["job_id"]


def test_batch_validation_and_ingest_replay(db_factory, tmp_path, monkeypatch, pdf_bytes):
    from app.main import app
    from app.issues import router
    from app.jobs.handlers import ingest, parse_document
    monkeypatch.setattr(router, "get_sessionmaker", lambda: db_factory)
    monkeypatch.setenv("FILES_DIR", str(tmp_path));get_settings.cache_clear()
    client = TestClient(app)
    url="/api/issues/RU000A10ATW2/documents"
    response=client.post(url, files=[("files", ("ok.pdf", pdf_bytes)), ("files", ("bad.pdf", b"bad"))])
    assert response.status_code == 415
    with db_factory() as s: assert not s.scalars(select(Document)).all()
    response=client.post(url,files=[("files",("ok.pdf",pdf_bytes))]);data=response.json()
    did=uuid.UUID(data["documents"][0]["id"]);jid=uuid.UUID(data["job_id"])
    settings=get_settings()
    class BrokenEmbeddingGateway:
        def embed(self, texts):
            raise TimeoutError("embedding endpoint unavailable")
    outcome=parse_document(db_factory,did,settings,gateway=BrokenEmbeddingGateway())
    assert outcome["warning"]=="embeddings_failed_fts_only"
    ingest(db_factory,jid,{"document_ids":[str(did)]},settings)
    ingest(db_factory,jid,{"document_ids":[str(did)]},settings)
    with db_factory() as s:
        assert s.get(Document,did).status=="ready"
        assert len(s.scalars(select(Page)).all())==1
        assert len(s.scalars(select(Chunk)).all())==2


def test_migration_repeat_and_indexes(db_factory):
    from alembic import command
    from alembic.config import Config
    command.upgrade(Config("alembic.ini"), "head")
    command.upgrade(Config("alembic.ini"), "head")
    with db_factory() as session:
        names=set(session.scalars(text("SELECT indexname FROM pg_indexes WHERE tablename='chunks'")))
        assert {"ix_chunks_fts","ix_chunks_embedding"} <= names
        assert session.scalar(text("SELECT extversion FROM pg_extension WHERE extname='vector'"))
