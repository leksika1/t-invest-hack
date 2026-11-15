from datetime import datetime, timezone
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.cards.export import render_markdown
from app.cards.schema import CardRequest, FIELDS, Review, validate_value
from app.cards.service import card_json, create_card, field_json
from app.issues.router import commit_and_return, database
from app.models import Card, CardField, Issue

router = APIRouter(prefix='/api/cards', tags=['cards'])


def required(session, identifier):
    card = session.get(Card, identifier)
    if card is None:
        raise HTTPException(404, detail='Карточка не найдена')
    return card


@router.post('', status_code=202)
def build_card(req: CardRequest, session: Session = Depends(database)):
    card, job = create_card(session, req.isin)
    return commit_and_return(session, {'card_id': card.id, 'job_id': job.id, 'status': job.status})


@router.get('')
def list_cards(isin: str | None = None, limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0), session: Session = Depends(database)):
    statement = select(Card).join(Issue)
    if isin is not None:
        try:
            normalized = CardRequest(isin=isin).isin
        except ValueError:
            raise HTTPException(422, detail='Некорректный ISIN')
        statement = statement.where(Issue.isin == normalized)
    total = session.scalar(select(func.count()).select_from(statement.subquery()))
    rows = list(session.scalars(statement.order_by(Card.created_at.desc(), Card.id.desc()).limit(limit).offset(offset)))
    statuses = {}
    if rows:
        for card_id, status, count in session.execute(select(CardField.card_id, CardField.status, func.count()).where(CardField.card_id.in_([c.id for c in rows])).group_by(CardField.card_id, CardField.status)):
            statuses.setdefault(card_id, {})[status] = count
    return {'items': [{'id': c.id, 'isin': session.get(Issue, c.issue_id).isin, 'header': c.input_snapshot.get('header', {}), 'field_statuses': statuses.get(c.id, {}), 'version': c.version, 'status': c.status, 'created_at': c.created_at, 'updated_at': c.updated_at} for c in rows],
            'total': total, 'limit': limit, 'offset': offset}


@router.get('/{card_id}')
def get_card(card_id: uuid.UUID, session: Session = Depends(database)):
    return card_json(session, required(session, card_id))


@router.patch('/{card_id}/fields/{code}')
def review_field(card_id: uuid.UUID, code: str, review: Review, session: Session = Depends(database)):
    card = session.scalar(select(Card).where(Card.id == card_id).with_for_update())
    if card is None or code not in FIELDS:
        raise HTTPException(404, detail='Карточка или поле не найдены')
    if card.status != 'ready':
        raise HTTPException(409, detail='Карточка ещё не готова')
    field = session.scalar(select(CardField).where(CardField.card_id == card_id, CardField.code == code))
    if review.user_status == 'confirmed' and field.value is None:
        raise HTTPException(409, detail='Нельзя подтвердить поле без значения; используйте исправление')
    try:
        corrected = validate_value(code, review.user_value) if review.user_status == 'corrected' else None
    except ValueError:
        raise HTTPException(422, detail='Исправленное значение не соответствует схеме поля')
    field.user_status, field.user_value = review.user_status, corrected
    field.reviewed_at = datetime.now(timezone.utc)
    card.updated_at = field.reviewed_at
    return field_json(field)


@router.get('/{card_id}/export.md')
def export_card(card_id: uuid.UUID, session: Session = Depends(database)):
    card = required(session, card_id)
    if card.status != 'ready':
        raise HTTPException(409, detail='Карточка ещё не готова')
    return Response(render_markdown(card_json(session, card)).encode('utf-8'), media_type='text/markdown; charset=utf-8',
                    headers={'Content-Disposition': f'attachment; filename="bond-{card_id}.md"'})


@router.get('/{card_id}/export.pdf')
def export_pdf(card_id: uuid.UUID, session: Session = Depends(database)):
    from app.cards.report import render_pdf
    card = required(session, card_id)
    if card.status != 'ready':
        raise HTTPException(409, detail='Карточка ещё не готова')
    data = card_json(session, card)
    return Response(render_pdf(data), media_type='application/pdf',
                    headers={'Content-Disposition': f'attachment; filename="card-{data["isin"]}-v{card.version}.pdf"'})
