def test_health_db_ok(http):
    r = http.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["db"] == "ok"


def test_card_valid_isin(http):
    import pymupdf
    from tests.e2e.test_documents import wait_job
    pdf = pymupdf.open(); page = pdf.new_page()
    page.insert_htmlbox(page.rect, '<p>9.5.1. Условия выпуска и ковенанты.</p>')
    content = pdf.tobytes(); pdf.close()
    uploaded = http.post('/api/issues/RU000A10ATW2/documents', files={'files': ('card-source.pdf', content, 'application/pdf')})
    assert uploaded.status_code == 202, uploaded.text
    wait_job(http, uploaded.json()['job_id'])
    r = http.post("/api/cards", json={"isin": "RU000A10ATW2"})
    assert r.status_code == 202, r.text
    wait_job(http, r.json()['job_id'])
    ready = http.get('/api/cards/' + r.json()['card_id'])
    assert ready.status_code == 200
    body = ready.json()
    assert body["isin"] == "RU000A10ATW2" and body['status'] == 'ready'
    fields = list(body["iss_fields"].values()) + list(body["risk_fields"].values())
    assert len(fields) == 15
    assert body['iss_fields']['face_value']['status'] == 'extracted'
    assert body['iss_fields']['face_value']['source']['kind'] == 'moex_iss'
    assert all(f['status'] == 'not_found' and f['reason'] == 'llm_not_configured' for f in body['risk_fields'].values())
    card_url = '/api/cards/' + body['id']
    reviewed = http.patch(card_url + '/fields/face_value', json={'user_status': 'confirmed'})
    assert reviewed.status_code == 200 and reviewed.json()['user_status'] == 'confirmed'
    assert http.get(card_url + '/export.md').status_code == 200
    report = http.get(card_url + '/export.pdf')
    assert report.status_code == 200
    assert report.headers['content-type'] == 'application/pdf'
    with pymupdf.open(stream=report.content, filetype='pdf') as pdf:
        assert body['isin'] in pdf[0].get_text()
    assert http.get('/api/cards?isin=RU000A10ATW2').json()['total'] >= 1


def test_card_invalid_isin(http):
    r = http.post("/api/cards", json={"isin": "XX123"})
    assert r.status_code == 422


def test_frontend_index(http):
    r = http.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert '<div id="root">' in r.text
