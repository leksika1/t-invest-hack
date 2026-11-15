from datetime import date
from decimal import Decimal
import secrets
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.issues.router import database
from app.llm.gateway import finite_decimal, utc_bounds
from app.models import CardField, Job, LLMCall


def authorize(x_admin_token: str | None = Header(None)):
    token = get_settings().admin_token
    if not token:
        raise HTTPException(503, detail={'code': 'admin_not_configured', 'message': 'Admin API не настроен'})
    if x_admin_token is None or not secrets.compare_digest(token.encode('utf-8'), x_admin_token.encode('utf-8')):
        raise HTTPException(401, detail='Неверный admin-токен')


router = APIRouter(prefix='/api/admin', tags=['admin'], dependencies=[Depends(authorize)])


@router.get('/llm-calls')
def calls(job_id: uuid.UUID | None = None, date: date | None = None, error: bool | None = None,
          limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0), session: Session = Depends(database)):
    statement = select(LLMCall)
    if job_id is not None:
        statement = statement.where(LLMCall.job_id == job_id)
    if date:
        start, end = utc_bounds(date)
        statement = statement.where(LLMCall.created_at >= start, LLMCall.created_at < end)
    if error is not None:
        statement = statement.where(LLMCall.error.is_not(None) if error else LLMCall.error.is_(None))
    total = session.scalar(select(func.count()).select_from(statement.subquery()))
    rows = session.scalars(statement.order_by(LLMCall.created_at.desc(), LLMCall.id.desc()).limit(limit).offset(offset))
    items = []
    for call in rows:
        field = session.get(CardField, call.field_id) if call.field_id else None
        items.append({'id': call.id, 'job_id': call.job_id, 'field_code': field.code if field else None,
                      'purpose': call.purpose, 'attempt': call.attempt, 'model': call.model, 'tokens_in': call.tokens_in,
                      'tokens_out': call.tokens_out, 'cost_usd': call.cost_usd, 'cost_source': call.cost_source,
                      'reserved_usd': str(call.reserved_usd), 'state': call.state, 'duration_ms': call.duration_ms,
                      'error': call.error, 'created_at': call.created_at, 'provider_response_id': call.provider_response_id})
    return {'items': items, 'total': total, 'limit': limit, 'offset': offset}


@router.get('/stats')
def stats(date: date | None = None, session: Session = Depends(database)):
    settings = get_settings()
    start, end = utc_bounds(date)
    rows = session.scalars(select(LLMCall).where(LLMCall.created_at >= start, LLMCall.created_at < end)).all()
    spent = sum((finite_decimal(c.cost_usd) or Decimal(0)) for c in rows)
    held = sum((c.reserved_usd or Decimal(0)) for c in rows if c.cost_usd is None)
    jobs = dict(session.execute(select(Job.status, func.count()).where(Job.created_at >= start, Job.created_at < end).group_by(Job.status)).all())
    return {'date': start.date(), 'timezone': 'UTC', 'spent_usd': str(spent), 'reserved_usd': str(held),
            'daily_budget_usd': str(settings.llm_daily_budget_usd), 'unknown_cost_calls': sum(c.cost_usd is None for c in rows),
            **settings.llm_proxy_info,
            'calls': len(rows), 'errors': sum(c.error is not None for c in rows),
            'jobs': {status: jobs.get(status, 0) for status in ('queued', 'running', 'succeeded', 'failed')}}
