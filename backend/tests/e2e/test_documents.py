import time

import pymupdf


def wait_job(http, job_id):
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        response = http.get("/api/jobs/" + job_id)
        assert response.status_code == 200
        body = response.json()
        if body["status"] in ("succeeded", "failed"):
            assert body["status"] == "succeeded", body
            return body
        time.sleep(0.5)
    raise AssertionError("Job did not finish within 60 seconds")


def test_upload_parse_selection_no_external_network(http):
    document = pymupdf.open()
    page = document.new_page()
    page.insert_htmlbox(page.rect, "<p>9.5.1. Эмитент обязуется соблюдать ковенанты.</p>")
    data = document.tobytes(); document.close()
    url = "/api/issues/RU000A10AXF7/documents"
    response = http.post(url, files={"files": ("decision.pdf", data, "application/pdf")}, data={"document_types": "decision"})
    assert response.status_code == 202, response.text
    body = response.json()
    wait_job(http, body["job_id"])
    documents = http.get(url).json()
    item = next(d for d in documents["items"] if d["id"] == body["documents"][0]["id"])
    assert item["status"] == "ready" and item["pages"] == 1 and item["ocr_pages"] == 0
    assert item["origins"][0]["source"] == "upload"
    assert http.patch(url + "/" + item["id"], json={"enabled": False}).json()["enabled"] is False
    duplicate = http.post(url, files={"files": ("same.pdf", data, "application/pdf")}).json()
    assert duplicate["documents"][0]["id"] == item["id"]
    assert duplicate["documents"][0]["duplicate"]
    assert duplicate["documents"][0]["enabled"] is False
    assert http.get("/api/issues/RU000A10AXF7").status_code == 200


def test_reject_bad_document(http):
    response = http.post("/api/issues/RU000A10AXF7/documents", files={"files": ("bad.pdf", b"not pdf")})
    assert response.status_code == 415
