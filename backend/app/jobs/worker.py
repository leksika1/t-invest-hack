import asyncio
from datetime import datetime, timedelta, timezone
import logging
import uuid

from sqlalchemy import select, update

from app.config import get_settings
from app.db import get_sessionmaker
from app.errors import ProcessingError
from app.jobs.handlers import handle
from app.models import Job

log = logging.getLogger(__name__)


def claim(factory, worker_id, settings):
    now = datetime.now(timezone.utc)
    with factory.begin() as session:
        # A crashed worker's leases are requeued; handlers are replay-safe.
        expired = session.scalars(select(Job).where(Job.status == "running", Job.lease_until < now).with_for_update(skip_locked=True)).all()
        for job in expired:
            job.worker_id = None
            if job.attempts >= settings.job_max_attempts:
                job.status, job.finished_at = "failed", now
                job.error = {"code": "lease_expired", "message": "Исчерпаны попытки после рестарта воркера"}
            else:
                job.status = "queued"
        session.flush()
        job = session.scalar(select(Job).where(Job.status == "queued").order_by(Job.created_at, Job.id).with_for_update(skip_locked=True).limit(1))
        if job is None:
            return None
        job.status, job.worker_id = "running", worker_id
        job.attempts += 1
        job.started_at = job.started_at or now
        job.lease_until = now + timedelta(seconds=settings.job_lease_seconds)
        session.flush()
        session.expunge(job)
        return job


def heartbeat(factory, job_id, worker_id, settings):
    with factory.begin() as session:
        session.execute(update(Job).where(Job.id == job_id, Job.worker_id == worker_id, Job.status == "running")
                        .values(lease_until=datetime.now(timezone.utc) + timedelta(seconds=settings.job_lease_seconds)))


def finish(factory, job_id, worker_id, result=None, error=None):
    with factory.begin() as session:
        session.execute(update(Job).where(Job.id == job_id, Job.worker_id == worker_id, Job.status == "running")
                        .values(status="failed" if error else "succeeded", step="finalize", progress=100,
                                result=result or {}, error=error, finished_at=datetime.now(timezone.utc), lease_until=None))


async def run_worker(stop: asyncio.Event):
    settings, worker_id = get_settings(), str(uuid.uuid4())
    factory = get_sessionmaker()
    while not stop.is_set():
        try:
            job = await asyncio.to_thread(claim, factory, worker_id, settings)
            if job:
                task = asyncio.create_task(asyncio.to_thread(handle, factory, job))
                while not task.done():
                    await asyncio.wait({task}, timeout=settings.job_lease_seconds / 3)
                    if not task.done():
                        try:
                            await asyncio.to_thread(heartbeat, factory, job.id, worker_id, settings)
                        except Exception:
                            log.exception("Heartbeat failed: job_id=%s", job.id)
                            # Keep awaiting the handler; never orphan an executing task.
                try:
                    result = task.result()
                    await asyncio.to_thread(finish, factory, job.id, worker_id, result)
                except Exception as exc:
                    error = exc.as_dict() if isinstance(exc, ProcessingError) else {"code": "job_failed", "message": "Ошибка фоновой обработки"}
                    log.exception("Job failed: job_id=%s type=%s", job.id, job.type)
                    await asyncio.to_thread(finish, factory, job.id, worker_id, error=error)
            else:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=settings.job_poll_seconds)
                except TimeoutError:
                    pass
        except Exception:
            log.exception("Worker database/claim error")
            try:
                await asyncio.wait_for(stop.wait(), timeout=5)
            except TimeoutError:
                pass
