"""Polite cached source HTTP, HTTPS allowlist, public pinned DNS and robots."""
from dataclasses import dataclass
import hashlib
import http.client
import ipaddress
import json
from pathlib import Path
import socket
import threading
import time
from urllib.parse import quote, urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

from app.config import Settings
from app.errors import ProcessingError

_locks_guard = threading.Lock()
_locks: dict[str, threading.Lock] = {}
_last: dict[str, float] = {}
_delays: dict[str, float] = {}


@dataclass
class Response:
    url: str
    status: int
    headers: dict
    content: bytes

    @property
    def text(self):
        import re
        content_type = self.headers.get("content-type", "")
        match = re.search(r"charset=([^;\s]+)", content_type, re.I)
        encoding = match.group(1).strip('"') if match else "windows-1251" if "1prime.ru" in self.url else "utf-8"
        try:
            return self.content.decode(encoding, "replace")
        except LookupError:
            return self.content.decode("utf-8", "replace")


def validate_url(url: str, allowed_hosts: set[str]):
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname not in allowed_hosts or parsed.port not in (None, 443) or parsed.username or parsed.password:
        raise ProcessingError("unsafe_url", "URL источника не разрешён")
    return parsed


def public_connection(address, timeout=None, source_address=None):
    host, port = address
    addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
        raise ProcessingError("unsafe_address", "Источник разрешается в непубличный адрес")
    last_error = None
    for family, socktype, proto, _, sockaddr in addresses:
        sock = socket.socket(family, socktype, proto)
        sock.settimeout(timeout)
        try:
            if source_address:
                sock.bind(source_address)
            sock.connect(sockaddr)  # exact checked sockaddr; no second DNS lookup
            return sock
        except OSError as exc:
            last_error = exc
            sock.close()
    raise last_error or OSError("No source address")


class SourceHTTP:
    def __init__(self, settings: Settings, hosts: set[str]):
        self.settings, self.hosts = settings, hosts
        self.cache = Path(settings.files_dir) / "http-cache"
        self.cache.mkdir(parents=True, exist_ok=True)

    def _raw(self, method, url, body=None, headers=None) -> Response:
        parsed = validate_url(url, self.hosts)
        with _locks_guard:
            lock = _locks.setdefault(parsed.hostname, threading.Lock())
        with lock:
            delay = max(self.settings.collector_delay_seconds, _delays.get(parsed.hostname, 0))
            time.sleep(max(0, delay - (time.monotonic() - _last.get(parsed.hostname, 0))))
            connection = http.client.HTTPSConnection(parsed.hostname, timeout=self.settings.collector_timeout_seconds)
            connection._create_connection = public_connection
            try:
                path = quote(urlunsplit(("", "", parsed.path or "/", parsed.query, "")), safe="/%?=&+:;,@!$'()*-._~")
                connection.request(method, path, body=body, headers={"User-Agent": self.settings.collector_user_agent,
                                   "Accept-Encoding": "identity", **(headers or {})})
                response = connection.getresponse()
                limit = self.settings.max_archive_bytes
                if int(response.getheader("Content-Length", "0")) > limit:
                    raise ProcessingError("download_too_large", "Источник отдаёт слишком большой файл", 413)
                content = response.read(limit + 1)
                if len(content) > limit:
                    raise ProcessingError("download_too_large", "Превышен лимит скачивания", 413)
                return Response(url, response.status, {k.lower(): v for k, v in response.getheaders()}, content)
            except (OSError, http.client.HTTPException, ValueError) as exc:
                raise ProcessingError("source_unavailable", "Источник недоступен или ответ некорректен") from exc
            finally:
                _last[parsed.hostname] = time.monotonic()
                connection.close()

    def _cached(self, method, url, body=None, headers=None) -> Response:
        key = hashlib.sha256(method.encode() + url.encode() + (body or b"")).hexdigest()
        path = self.cache / (key + ".json")
        body_path = self.cache / (key + ".body")
        cached = None
        try:
            cached = json.loads(path.read_text(encoding="utf-8"))
            content = body_path.read_bytes()
            if time.time() - cached["time"] < self.settings.collector_cache_seconds:
                return Response(url, cached["status"], cached["headers"], content)
        except (OSError, ValueError, KeyError):
            cached = None
        conditional = dict(headers or {})
        if cached and method == "GET":
            for name, key_header in [("etag", "If-None-Match"), ("last-modified", "If-Modified-Since")]:
                if cached["headers"].get(name):
                    conditional[key_header] = cached["headers"][name]
        for attempt in range(3):
            result = self._raw(method, url, body, conditional)
            if result.status != 429 and result.status < 500:
                break
            if attempt < 2:
                try:
                    delay = float(result.headers.get("retry-after", 2 ** attempt))
                except ValueError:
                    delay = 2 ** attempt
                # Don't retry earlier than a long Retry-After; let the operator retry later.
                if delay > 10:
                    break
                time.sleep(max(1, delay))
        if result.status == 304 and cached:
            result = Response(url, cached["status"], cached["headers"], body_path.read_bytes())
        if result.status in (200, 404):
            payload = json.dumps({"status": result.status, "headers": result.headers, "time": time.time()})
            import os, tempfile
            with tempfile.NamedTemporaryFile(dir=self.cache, delete=False) as body_file:
                body_file.write(result.content)
                temporary_body_path = Path(body_file.name)
            try:
                os.replace(temporary_body_path, body_path)
            finally:
                temporary_body_path.unlink(missing_ok=True)
            with tempfile.NamedTemporaryFile(dir=self.cache, mode="w", encoding="utf-8", delete=False) as temporary:
                temporary.write(payload)
                temporary_path = Path(temporary.name)
            try:
                os.replace(temporary_path, path)
            finally:
                temporary_path.unlink(missing_ok=True)
        return result

    def request(self, url: str, method="GET", body: bytes | None = None, headers=None) -> Response:
        for _ in range(6):
            parsed = validate_url(url, self.hosts)
            robots_url = "https://" + parsed.netloc + "/robots.txt"
            robots = self._cached("GET", robots_url)
            if robots.status == 200:
                parser = RobotFileParser()
                parser.parse(robots.text.splitlines())
                if not parser.can_fetch(self.settings.collector_user_agent, url):
                    raise ProcessingError("robots_denied", "Скачивание запрещено robots.txt")
                crawl_delay = parser.crawl_delay(self.settings.collector_user_agent) or parser.crawl_delay("*")
                rate = parser.request_rate(self.settings.collector_user_agent) or parser.request_rate("*")
                _delays[parsed.hostname] = max(float(crawl_delay or 0), rate.seconds / rate.requests if rate and rate.requests else 0)
            elif robots.status not in (404, 410):
                raise ProcessingError("robots_unavailable", "Нельзя подтвердить правила robots.txt")
            result = self._cached(method, url, body, headers)
            if result.status in (301, 302, 303, 307, 308):
                url = urljoin(url, result.headers.get("location", ""))
                if result.status in (301, 302, 303):
                    method, body, headers = "GET", None, None
                continue
            if result.status == 403 or b"captcha" in result.content[:2000].lower():
                raise ProcessingError("source_blocked", "Источник требует ручного доступа; защита не обходится")
            if result.status == 404:
                raise ProcessingError("source_not_found", "Страница эмитента или документа не найдена")
            if result.status >= 400:
                raise ProcessingError("source_http_error", f"Источник вернул HTTP {result.status}")
            return result
        raise ProcessingError("too_many_redirects", "Слишком много перенаправлений источника")
