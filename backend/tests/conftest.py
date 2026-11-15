import os
from pathlib import Path
import socket
import ipaddress

import pytest


@pytest.fixture(autouse=True)
def offline_unit_tests(request, monkeypatch):
    if 'tests/e2e' not in request.node.nodeid.replace('\\', '/') and request.node.path.name != 'test_demo.py':
        monkeypatch.setenv('LLM_MODE', 'live')
    if request.node.get_closest_marker("db") or "tests/e2e" in request.node.nodeid.replace("\\", "/"):
        return
    monkeypatch.setattr('app.rate_limit.consume', lambda *args: None)

    connect = socket.socket.connect
    def blocked(sock, address):
        # Windows asyncio implements socketpair using a loopback connection.
        if isinstance(address, tuple):
            try:
                if ipaddress.ip_address(address[0]).is_loopback:
                    return connect(sock, address)
            except ValueError:
                pass
        raise AssertionError("Unit tests must not use the network")

    monkeypatch.setattr(socket.socket, "connect", blocked)


@pytest.fixture
def pdf_bytes():
    import pymupdf
    doc = pymupdf.open()
    page = doc.new_page()
    # Built-in Droid fallback via HTML supports Russian on Windows and Linux.
    page.insert_htmlbox(page.rect, "<p>9.5.1. Эмитент обязуется соблюдать ковенанты.</p><p>9.5.2. Досрочное погашение облигаций.</p>")
    data = doc.tobytes()
    doc.close()
    return data


@pytest.fixture
def db_factory(monkeypatch):
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set; PostgreSQL tests are opt-in")
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url
    from sqlalchemy.orm import sessionmaker
    from alembic import command
    from alembic.config import Config
    from app.config import get_settings
    from app.db import get_engine

    assert make_url(url).database.endswith("_test"), "Use a dedicated *_test database"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv('LLM_API_KEY', '')
    monkeypatch.setenv('EMBEDDING_API_KEY', '')
    monkeypatch.setenv('EMBEDDING_MODEL', '')
    get_settings.cache_clear()
    get_engine.cache_clear()
    try:
        command.upgrade(Config(str(Path(__file__).parents[1] / "alembic.ini")), "head")
        engine = create_engine(url)
        from app.models import Base
        with engine.begin() as connection:
            connection.execute(text("TRUNCATE " + ", ".join(t.name for t in Base.metadata.sorted_tables) + " CASCADE"))
        yield sessionmaker(engine, expire_on_commit=False)
        engine.dispose()
    finally:
        if get_engine.cache_info().currsize:
            get_engine().dispose()
        get_engine.cache_clear()
        get_settings.cache_clear()
