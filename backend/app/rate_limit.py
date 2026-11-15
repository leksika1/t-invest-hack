"""DB-backed sliding window shared by backend processes; no trusting XFF."""
from datetime import datetime, timedelta, timezone
import hashlib
import math

from sqlalchemy import delete, func, select, text
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

from app.config import get_settings
from app.db import get_sessionmaker
from app.models import RateLimitEvent


def consume(factory, client, action, limit, window):
    now = datetime.now(timezone.utc)
    key = hashlib.sha256((client + ':' + action).encode()).hexdigest()
    with factory.begin() as session:
        session.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': int(key[:15], 16)})
        session.execute(delete(RateLimitEvent).where(RateLimitEvent.created_at < now - timedelta(seconds=window)))
        oldest, count = session.execute(select(func.min(RateLimitEvent.created_at), func.count()).where(RateLimitEvent.key == key)).one()
        if count >= limit:
            return max(1, math.ceil(window - (now - oldest).total_seconds()))
        session.add(RateLimitEvent(key=key, created_at=now))
    return None


class RateLimitMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'http' and scope['method'] == 'POST':
            path = scope['path'].rstrip('/')
            action = 'cards' if path == '/api/cards' else 'uploads' if path.startswith('/api/issues/') and path.endswith('/documents') else 'collect' if path.startswith('/api/issues/') and path.endswith('/collect') else None
            if action:
                settings = get_settings()
                try:
                    retry = await run_in_threadpool(consume, get_sessionmaker(), (scope.get('client') or ('unknown', 0))[0], action,
                                                    getattr(settings, 'rate_limit_' + action), settings.rate_limit_window_seconds)
                except Exception:
                    return await JSONResponse({'detail': {'code': 'rate_limit_unavailable', 'message': 'Ограничитель запросов недоступен'}}, 503)(scope, receive, send)
                if retry:
                    return await JSONResponse({'detail': {'code': 'rate_limited', 'message': 'Слишком много запросов, повторите позже'}}, 429, headers={'Retry-After': str(retry)})(scope, receive, send)
        await self.app(scope, receive, send)
