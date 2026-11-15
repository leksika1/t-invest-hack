import asyncio
from types import SimpleNamespace
import time
import uuid

import pytest

from app.errors import ProcessingError
from app.jobs import worker


@pytest.mark.parametrize("fail", [False, True])
def test_async_worker_heartbeats_and_finishes(monkeypatch, fail):
    async def exercise():
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        job = SimpleNamespace(id=uuid.uuid4(), type="ingest", payload={"document_ids": []})
        beats, endings = [], []
        monkeypatch.setattr(worker, "get_settings", lambda: SimpleNamespace(job_lease_seconds=0.03, job_poll_seconds=0.001))
        monkeypatch.setattr(worker, "get_sessionmaker", lambda: object())
        monkeypatch.setattr(worker, "claim", lambda *a: job)
        monkeypatch.setattr(worker, "heartbeat", lambda *a: beats.append(a[1]))
        def handle(*args):
            time.sleep(0.05)
            if fail:
                raise ProcessingError("parse_failed", "test failure")
            return {"document_ids": []}
        monkeypatch.setattr(worker, "handle", handle)
        def finish(*args, **kwargs):
            endings.append((args, kwargs))
            loop.call_soon_threadsafe(stop.set)
        monkeypatch.setattr(worker, "finish", finish)
        await asyncio.wait_for(worker.run_worker(stop), timeout=2)
        assert beats and len(endings) == 1
        if fail:
            assert endings[0][1]["error"]["code"] == "parse_failed"
        else:
            assert endings[0][0][-1] == {"document_ids": []}
    asyncio.run(exercise())


def test_idle_worker_stops_without_claim(monkeypatch):
    async def exercise():
        stop = asyncio.Event(); stop.set()
        monkeypatch.setattr(worker, "get_sessionmaker", lambda: object())
        await worker.run_worker(stop)
    asyncio.run(exercise())
