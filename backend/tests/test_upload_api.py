from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.middleware import UploadLimitMiddleware


def test_upload_body_limit_and_replay():
    app = FastAPI()
    app.add_middleware(UploadLimitMiddleware, max_bytes=100)
    @app.post("/api/issues/TEST/documents")
    async def body(request: Request):
        return {"length": len(await request.body())}
    client = TestClient(app)
    url = "/api/issues/TEST/documents"
    assert client.post(url, content=b"a" * 100).json() == {"length": 100}
    assert client.post(url, content=b"a" * 101).status_code == 413
    assert client.post(url, content=iter([b"a" * 60, b"b" * 60])).status_code == 413


def test_bad_pdf_without_db_or_iss():
    from app.main import app
    client = TestClient(app)
    response = client.post("/api/issues/RU000A10ATW2/documents", files={"files": ("bad.pdf", b"not pdf")})
    assert response.status_code == 415
    assert response.json()["detail"]["code"] == "not_pdf"


def zip_upload(entries):
    import io
    import zipfile
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries:
            archive.writestr(name, content)
    return output.getvalue()


def test_upload_zip_pdf_only_origin_and_document_type(monkeypatch, pdf_bytes):
    from types import SimpleNamespace
    import uuid
    from app.main import app
    from app.issues import router
    persisted = []
    issue = SimpleNamespace(id=uuid.uuid4(), isin='RU000A10CS75')
    monkeypatch.setattr(router, 'ensure_issue', lambda *args: issue)
    def put(session, issue_id, content, filename, kind, settings, origin):
        persisted.append((content, filename, kind, origin))
        return SimpleNamespace(id=uuid.uuid4(), filename=filename, status='pending'), False
    monkeypatch.setattr(router, 'put_document', put)
    monkeypatch.setattr(router, 'document_json', lambda session, document: {'filename': document.filename})
    jobs = []
    def new_job(session, kind, payload):
        jobs.append(payload)
        return SimpleNamespace(id=uuid.uuid4())
    monkeypatch.setattr(router, 'new_job', new_job)
    session = SimpleNamespace(commit=lambda: None)
    app.dependency_overrides[router.database] = lambda: session
    try:
        response = TestClient(app).post('/api/issues/RU000A10CS75/documents',
            files=[('files', ('edisclosure.zip', zip_upload([('docs/Условия.pdf', pdf_bytes), ('readme.txt', b'ignore')]), 'application/zip'))],
            data={'document_types': 'decision'})
        assert response.status_code == 202, response.text
        assert len(persisted) == 1 and len(jobs[0]['document_ids']) == 1
        content, name, kind, origin = persisted[0]
        assert content == pdf_bytes and name == 'Условия.pdf' and kind == 'decision'
        assert origin['source'] == 'upload'
        assert origin['archive'] == {'url': None, 'filename': 'edisclosure.zip', 'entry_name': 'docs/Условия.pdf'}
    finally:
        app.dependency_overrides.clear()


def test_upload_batch_rejects_unsafe_zip_before_any_mutation(monkeypatch, pdf_bytes):
    from app.main import app
    from app.issues import router
    def forbidden(*args):
        raise AssertionError('No mutation before the whole batch is valid')
    monkeypatch.setattr(router, 'ensure_issue', forbidden)
    app.dependency_overrides[router.database] = lambda: None
    try:
        for name, payload in [('../escape.pdf', pdf_bytes), ('nested.zip', b'PKbad'),
                              ('bad.pdf', b'not a pdf'), ('readme.txt', b'no PDFs')]:
            response = TestClient(app).post('/api/issues/RU000A10CS75/documents', files=[
                ('files', ('valid.pdf', pdf_bytes)),
                ('files', ('invalid.zip', zip_upload([(name, payload)])))])
            assert response.status_code in (400, 413, 415, 422), response.text
        response = TestClient(app).post('/api/issues/RU000A10CS75/documents', files={'files': ('broken.zip', b'not zip')})
        assert response.status_code == 415
    finally:
        app.dependency_overrides.clear()


def test_upload_zip_batch_limits(monkeypatch, pdf_bytes):
    from app.config import Settings
    from app.main import app
    from app.issues import router
    settings = Settings(_env_file=None, max_archive_entries=1)
    monkeypatch.setattr(router, 'get_settings', lambda: settings)
    app.dependency_overrides[router.database] = lambda: None
    try:
        response = TestClient(app).post('/api/issues/RU000A10CS75/documents', files=[
            ('files', ('first.zip', zip_upload([('one.pdf', pdf_bytes)]))),
            ('files', ('second.zip', zip_upload([('two.pdf', pdf_bytes)])))])
        assert response.status_code == 413
        assert response.json()['detail']['code'] == 'archive_limits'
    finally:
        app.dependency_overrides.clear()
