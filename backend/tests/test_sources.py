from pathlib import Path
import socket

import pytest

from app.config import Settings
from app.documents.http import Response, SourceHTTP, public_connection, validate_url
from app.documents.sources import EDisclosureSource, Identity, PrimeSource, SkrinSource, assisted, matches_issue, publication_date
from app.errors import ProcessingError

FIXTURES = Path(__file__).parent / "fixtures" / "sources"


class RecordedHTTP:
    def __init__(self, mapping):
        self.mapping = mapping
        self.calls = []

    def request(self, url, *args):
        self.calls.append(url)
        name = self.mapping[url]
        return Response(url, 200, {"content-type": "text/html; charset=utf-8"}, (FIXTURES / name).read_bytes())


def test_prime_real_issuer_embeds_document_tables():
    url = "https://disclosure.1prime.ru/Portal/Default.aspx?emId=7826705374"
    http = RecordedHTTP({url: "prime_balt.html"})
    result = PrimeSource(Settings(_env_file=None), http).discover(Identity("RU000A10ATW2", "7826705374", registration_number="4B02-15-36442-R-001P"))
    assert result.status == "succeeded" and len(result.documents) == 2
    assert any("4cb600a4761e4316866f903828009967" in c.download_url for c in result.documents)
    assert result.documents[0].published_at == "2025-02-05"
    assert http.calls == [url]


def test_prime_rejects_wrong_issuer():
    http = RecordedHTTP({"https://disclosure.1prime.ru/Portal/Default.aspx?emId=1234567890": "prime_balt.html"})
    assert PrimeSource(Settings(_env_file=None), http).discover(Identity("RU000A10ATW2", "1234567890")).status == "empty"


def test_prime_name_search_posts_actual_aspnet_fields():
    calls = []
    class FormHTTP:
        def request(self, url, method="GET", body=None, headers=None):
            calls.append((url, method, body))
            name = "prime_lsr.html" if "emId=" in url else "prime_search.html" if method == "POST" else "prime_catalog.html"
            return Response(url, 200, {"content-type": "text/html; charset=utf-8"}, (FIXTURES/name).read_bytes())
    result = PrimeSource(Settings(_env_file=None), FormHTTP()).discover(Identity("RU000A10ATW2", name='ПАО "Группа ЛСР"'))
    assert result.status == "empty"  # this issuer only publishes messages at PRIME
    assert calls[1][1] == "POST"
    assert b"ctl00%24Main%24SearchName=" in calls[1][2]


def test_skrin_real_document_rows_exclude_other_issues():
    text = (FIXTURES / "skrin_aeroflot_bonds.html").read_text(encoding="utf-8")
    identity = Identity("RU000A10CS75", "7712040126", registration_number="4B02-02-00010-A-002P", issue_name="Аэрофлот П02-БО-02")
    documents = SkrinSource.parse_documents(text, "https://disclosure.skrin.ru/disclosure/7712040126/6", identity)
    assert len(documents) == 2
    assert all("П02-БО-02" in c.download_url for c in documents)
    assert all(c.published_at == "2025-09-18" for c in documents)
    assert not SkrinSource.parse_documents(text, "https://disclosure.skrin.ru/", Identity("RU000A10ATW2"))


def test_skrin_profile_and_bounded_pagination():
    base = "https://disclosure.skrin.ru/disclosure/7712040126"
    http = RecordedHTTP({base: "skrin_aeroflot.html", base + "/6": "skrin_aeroflot_bonds.html"})
    result = SkrinSource(Settings(collector_max_pages=1, _env_file=None), http).discover(
        Identity("RU000A10CS75", "7712040126", registration_number="4B02-02-00010-A-002P"))
    assert result.status == "succeeded" and len(result.documents) == 2
    assert result.message  # pagination cap is disclosed


def test_official_access_stub_never_uses_network():
    identity = Identity("RU000A10ATW2", "7826705374")
    assert EDisclosureSource(Settings(_env_file=None)).discover(identity).status == "official_access_required"
    assert EDisclosureSource(Settings(edisclosure_api_url="https://official.invalid", edisclosure_api_key="fake", _env_file=None)).discover(identity).status == "adapter_not_implemented"
    assert assisted(identity)["query"] == "7826705374"
    assert assisted(identity)["mode"] == "manual_query"


@pytest.mark.parametrize("url", ["http://disclosure.skrin.ru/file.pdf", "https://127.0.0.1/a", "https://disclosure.skrin.ru@localhost/a", "https://disclosure.skrin.ru:8443/a", "https://evil.test/a"])
def test_source_url_allowlist(url):
    with pytest.raises(ProcessingError):
        validate_url(url, {"disclosure.skrin.ru"})


def test_dns_rebinding_private_address_is_blocked(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))])
    with pytest.raises(ProcessingError) as error:
        public_connection(("disclosure.skrin.ru", 443))
    assert error.value.code == "unsafe_address"


def test_robots_denial_404_missing_and_redirect(tmp_path, monkeypatch):
    client = SourceHTTP(Settings(files_dir=str(tmp_path), _env_file=None), {"disclosure.skrin.ru"})
    def deny(method, url, *args):
        return Response(url, 200, {}, b"User-agent: *\nDisallow: /files")
    monkeypatch.setattr(client, "_cached", deny)
    with pytest.raises(ProcessingError) as error:
        client.request("https://disclosure.skrin.ru/files/a.pdf")
    assert error.value.code == "robots_denied"
    def redirect(method, url, *args):
        if url.endswith("robots.txt"):
            return Response(url, 404, {}, b"")
        return Response(url, 302, {"location": "http://169.254.169.254/"}, b"")
    monkeypatch.setattr(client, "_cached", redirect)
    with pytest.raises(ProcessingError) as error:
        client.request("https://disclosure.skrin.ru/a")
    assert error.value.code == "unsafe_url"


def test_cache_and_retries_without_network(tmp_path, monkeypatch):
    client = SourceHTTP(Settings(files_dir=str(tmp_path), _env_file=None), {"disclosure.skrin.ru"})
    calls = []
    monkeypatch.setattr("app.documents.http.time.sleep", lambda n: None)
    def raw(method, url, *args):
        calls.append(url)
        return Response(url, 503 if len(calls) < 3 else 200, {}, b"PDF")
    monkeypatch.setattr(client, "_raw", raw)
    assert client._cached("GET", "https://disclosure.skrin.ru/a").content == b"PDF"
    assert client._cached("GET", "https://disclosure.skrin.ru/a").content == b"PDF"
    assert len(calls) == 3


def test_403_and_unknown_robots_close_source(tmp_path, monkeypatch):
    client = SourceHTTP(Settings(files_dir=str(tmp_path), _env_file=None), {"disclosure.skrin.ru"})
    monkeypatch.setattr(client, "_cached", lambda *a: Response(a[1], 403, {}, b""))
    with pytest.raises(ProcessingError) as error:
        client.request("https://disclosure.skrin.ru/a")
    assert error.value.code == "robots_unavailable"


def test_publication_dates():
    assert publication_date("18 сентября 2025") == "2025-09-18"
    assert publication_date("bad") is None


def test_transport_timeout_is_source_error(tmp_path, monkeypatch):
    class TimedOutConnection:
        def __init__(self, *args, **kwargs): pass
        def request(self, *args, **kwargs): raise TimeoutError("socket timeout")
        def close(self): pass
    monkeypatch.setattr("app.documents.http.http.client.HTTPSConnection", TimedOutConnection)
    monkeypatch.setattr("app.documents.http.time.sleep", lambda seconds: None)
    client = SourceHTTP(Settings(files_dir=str(tmp_path), _env_file=None), {"disclosure.skrin.ru"})
    with pytest.raises(ProcessingError) as error:
        client._raw("GET", "https://disclosure.skrin.ru/a")
    assert error.value.code == "source_unavailable"
