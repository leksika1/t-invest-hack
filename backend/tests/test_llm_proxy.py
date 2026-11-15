from types import SimpleNamespace
import json

from fastapi.testclient import TestClient
import httpx
from pydantic import ValidationError
import pytest

from app import main
from app.config import Settings
from app.llm.client import LLMClient
from app.llm.gateway import GatewayError, LLMGateway
from tests.test_gateway import FakeTransport, MemoryLedger, extract, http_error, response, settings


PROXY = 'http://proxy-user:proxy-password@proxy.internal:8888'


@pytest.mark.parametrize('scheme', ['', 'http', 'https', 'socks5', 'socks5h'])
@pytest.mark.parametrize('purpose', ['extraction', 'embedding'])
def test_model_client_routes_only_explicit_proxy(monkeypatch, scheme, purpose):
    proxy = f'{scheme}://proxy-user:proxy-password@proxy.internal:8888' if scheme else ''
    monkeypatch.setenv('HTTP_PROXY', 'http://unwanted.invalid:9999')
    monkeypatch.setenv('HTTPS_PROXY', 'http://unwanted.invalid:9999')
    monkeypatch.setenv('ALL_PROXY', 'socks5://unwanted.invalid:9999')
    monkeypatch.setenv('NO_PROXY', '*')
    constructed, requests = [], []
    real_client = httpx.Client

    def reply(request):
        requests.append(request)
        return httpx.Response(200, json={'ok': True})

    def create_client(**kwargs):
        constructed.append(dict(kwargs))
        # Replace only the network, retaining the actual client lifecycle.
        kwargs.pop('proxy', None)
        return real_client(transport=httpx.MockTransport(reply), **kwargs)

    monkeypatch.setattr('app.llm.client.httpx.Client', create_client)
    configured = Settings(llm_api_key='generation-key', llm_proxy_url=proxy,
                          embedding_base_url='https://embedding.example/v1', embedding_api_key='embedding-key', _env_file=None)
    assert LLMClient(configured).request(purpose, {'model': 'test'}) == {'ok': True}
    assert constructed == [{'timeout': configured.llm_timeout_seconds, 'proxy': proxy or None, 'trust_env': False}]
    expected = ('https://embedding.example/v1/embeddings', 'embedding-key') if purpose == 'embedding' else (
        configured.llm_base_url + '/chat/completions', 'generation-key')
    assert str(requests[0].url) == expected[0]
    assert requests[0].headers['Authorization'] == 'Bearer ' + expected[1]
    assert 'proxy-authorization' not in requests[0].headers


@pytest.mark.parametrize('scheme', ['http', 'https', 'socks5', 'socks5h'])
def test_installed_httpx_can_construct_each_proxy_transport(scheme):
    # No sockets: also verifies that the SOCKS optional dependency is installed.
    with httpx.Client(proxy=f'{scheme}://user:pass@proxy.internal:8888', trust_env=False):
        pass


@pytest.mark.parametrize('value', ['', '   ', PROXY, 'socks5h://user:password@proxy.internal:1080'])
def test_proxy_env_and_health_never_expose_endpoint(monkeypatch, value):
    monkeypatch.setenv('LLM_PROXY_URL', value)
    configured = Settings(_env_file=None)
    monkeypatch.setattr(main, 'settings', configured)
    monkeypatch.setattr(main, 'check_db', lambda: True)
    reply = TestClient(main.app).get('/api/health')
    body = reply.json()
    assert body['llm_proxy'] == ('configured' if value.strip() else 'none')
    assert body['llm_proxy_scheme'] == (value.split(':')[0] if value.strip() else None)
    assert all(secret not in reply.text for secret in ('proxy-user', 'proxy-password', 'proxy.internal', 'user:password'))
    assert 'llm_proxy_url' not in configured.model_dump()
    assert 'proxy-password' not in repr(configured)


@pytest.mark.parametrize('value', ['ftp://user:secret@proxy.internal', 'http://',
                                 'http://user:secret@proxy.internal:99999', 'http://proxy.internal/path',
                                 'http://proxy.internal?token=secret'])
def test_invalid_proxy_configuration_has_safe_error(value):
    with pytest.raises(ValidationError) as error:
        Settings(llm_proxy_url=value, _env_file=None)
    assert 'LLM_PROXY_URL' in str(error.value)
    assert 'secret' not in str(error.value) and 'proxy.internal' not in str(error.value)


def test_admin_stats_exposes_only_proxy_state_and_scheme(monkeypatch):
    from app.admin import router
    from app.issues.router import database
    configured = Settings(admin_token='test-admin', llm_proxy_url=PROXY, _env_file=None)
    monkeypatch.setattr(router, 'get_settings', lambda: configured)
    rows = SimpleNamespace(all=lambda: [])
    session = SimpleNamespace(scalars=lambda query: rows, execute=lambda query: rows)
    main.app.dependency_overrides[database] = lambda: session
    try:
        reply = TestClient(main.app).get('/api/admin/stats', headers={'X-Admin-Token': 'test-admin'})
    finally:
        del main.app.dependency_overrides[database]
    assert reply.status_code == 200
    assert reply.json()['llm_proxy'] == 'configured' and reply.json()['llm_proxy_scheme'] == 'http'
    assert all(secret not in reply.text for secret in ('proxy-user', 'proxy-password', 'proxy.internal'))


def test_iss_ignores_model_and_environment_proxies(monkeypatch):
    from app.iss import client
    monkeypatch.setenv('HTTPS_PROXY', PROXY)
    monkeypatch.setenv('HTTP_PROXY', PROXY)
    monkeypatch.setattr(client, 'get_settings', lambda: Settings(llm_proxy_url=PROXY, _env_file=None))
    calls = []

    def get(url, **kwargs):
        calls.append((url, kwargs))
        return httpx.Response(200, json={'ok': True}, request=httpx.Request('GET', url))

    monkeypatch.setattr(client.httpx, 'get', get)
    assert client._get('/securities.json') == {'ok': True}
    assert calls[0][1]['trust_env'] is False
    assert 'proxy' not in calls[0][1]


def test_collector_still_uses_direct_pinned_https(monkeypatch, tmp_path):
    from app.documents import http
    monkeypatch.setenv('HTTPS_PROXY', PROXY)
    monkeypatch.setenv('HTTP_PROXY', PROXY)
    calls = []

    class Connection:
        def __init__(self, host, timeout):
            calls.append((host, timeout))

        def request(self, *args, **kwargs):
            assert self._create_connection is http.public_connection
            assert 'proxy-authorization' not in {k.lower() for k in kwargs['headers']}

        def getresponse(self):
            return SimpleNamespace(status=200, getheader=lambda *args: '0', read=lambda limit: b'{}', getheaders=lambda: [])

        def close(self):
            pass

    monkeypatch.setattr(http.http.client, 'HTTPSConnection', Connection)
    monkeypatch.setattr(http.time, 'sleep', lambda _: None)
    configured = Settings(llm_proxy_url=PROXY, files_dir=str(tmp_path), _env_file=None)
    source = http.SourceHTTP(configured, {'disclosure.skrin.ru'})
    assert source._raw('GET', 'https://disclosure.skrin.ru/disclosure/').status == 200
    assert calls == [('disclosure.skrin.ru', configured.collector_timeout_seconds)]


@pytest.mark.parametrize('failure', [httpx.ProxyError, httpx.ConnectError, httpx.ConnectTimeout])
@pytest.mark.parametrize('purpose', ['extraction', 'embedding'])
def test_proxy_failures_are_sanitized_and_budgeted(failure, purpose, caplog):
    configured = settings(llm_proxy_url=PROXY, llm_max_retries=0, embedding_model='test-embedding')
    ledger = MemoryLedger()
    transport = FakeTransport([failure('Cannot connect to ' + PROXY)])
    gateway = LLMGateway(settings=configured, ledger=ledger, transport=transport)
    with pytest.raises(GatewayError) as error:
        gateway.embed(['text']) if purpose == 'embedding' else extract(gateway)
    assert error.value.code == 'llm_proxy_error'
    assert str(error.value) == 'Прокси LLM недоступен'
    assert ledger.records[0]['error'] == {'code': 'llm_proxy_error', 'message': 'Прокси LLM недоступен'}
    assert ledger.records[0]['cost'] is None and ledger.records[0]['reserve'] > 0
    assert 'proxy-password' not in (json.dumps(ledger.records, default=str) + caplog.text + str(error.value))


def test_proxy_error_retry_can_recover():
    ledger = MemoryLedger()
    transport = FakeTransport([httpx.ProxyError(PROXY), response()])
    waits = []
    result = extract(LLMGateway(settings=settings(llm_proxy_url=PROXY), ledger=ledger,
                                transport=transport, sleep=waits.append))
    assert result.value.amount == '1000' and waits == [1]
    assert len(ledger.records) == 2
    assert ledger.records[0]['error']['code'] == 'llm_proxy_error'


@pytest.mark.parametrize('proxy,expected', [('', 'llm_transport_error'), (PROXY, 'llm_proxy_error')])
def test_direct_connection_errors_keep_their_reason(proxy, expected):
    with pytest.raises(GatewayError) as error:
        extract(LLMGateway(settings=settings(llm_proxy_url=proxy, llm_max_retries=0), ledger=MemoryLedger(),
                           transport=FakeTransport([httpx.ConnectError('refused')])))
    assert error.value.code == expected


@pytest.mark.parametrize('failure,expected', [
    (httpx.ReadTimeout('provider timeout'), 'llm_timeout'),
    (http_error(403, PROXY), 'llm_access_denied'),
    (http_error(407, PROXY), 'llm_proxy_error'),
])
def test_proxy_does_not_hide_provider_failures(failure, expected):
    ledger = MemoryLedger()
    with pytest.raises(GatewayError) as error:
        extract(LLMGateway(settings=settings(llm_proxy_url=PROXY, llm_max_retries=0), ledger=ledger,
                           transport=FakeTransport([failure])))
    assert error.value.code == expected
    assert 'proxy-password' not in (str(error.value) + str(ledger.records))


def test_missing_mihomo_dns_is_a_proxy_error(monkeypatch):
    import socket
    resolved = []

    def missing(host, port, *args, **kwargs):
        resolved.append((host, port))
        assert host == 'tinvest-llm-proxy'
        raise socket.gaierror(socket.EAI_NONAME, 'Name or service not known')

    monkeypatch.setattr(socket, 'getaddrinfo', missing)
    ledger = MemoryLedger()
    # Real HTTPX/LLMClient maps DNS failure to ConnectError, then gateway sanitizes it.
    gateway = LLMGateway(settings=settings(llm_proxy_url='http://tinvest-llm-proxy:7890', llm_max_retries=0), ledger=ledger)
    with pytest.raises(GatewayError) as error:
        extract(gateway)
    assert error.value.code == 'llm_proxy_error'
    assert resolved == [('tinvest-llm-proxy', 7890)]
    assert ledger.records[0]['error']['message'] == 'Прокси LLM недоступен'
