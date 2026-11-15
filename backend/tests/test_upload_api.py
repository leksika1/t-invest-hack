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
