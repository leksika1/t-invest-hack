"""E2E-тесты по HTTP против поднятого стека (nginx → backend → Postgres).

Запускаются, только если задан E2E_BASE_URL, например http://127.0.0.1:18080.
Без него пропускаются — обычный `pytest` гоняет только unit-тесты.
"""

import os

import httpx
import pytest

BASE_URL = os.environ.get("E2E_BASE_URL", "").rstrip("/")


def pytest_collection_modifyitems(config, items):
    if BASE_URL:
        return
    skip = pytest.mark.skip(reason="E2E_BASE_URL не задан")
    for item in items:
        if "tests/e2e" in item.nodeid.replace("\\", "/"):
            item.add_marker(skip)


@pytest.fixture(scope="session")
def http():
    with httpx.Client(base_url=BASE_URL, timeout=15) as client:
        yield client
