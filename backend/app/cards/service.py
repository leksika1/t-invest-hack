from datetime import datetime, timezone
from decimal import Decimal
import json
import time
import uuid

from sqlalchemy import func, select, text

from app.cards.schema import DISCLAIMER, FIELDS, SPECS, extraction_model
from app.cards import prompts
from app.cards.export import REASONS
from app.cards.retriever import context_token_bound, full_document_context, retrieve
from app.cards.verification import degraded_field, result_template, verify
from app.config import get_settings
from app.documents.sources import MoexIssSource
from app.errors import ProcessingError
from app.iss.client import IssError
from app.issues.service import document_json, ensure_issue, new_job, update_iss
from app.llm.gateway import GatewayError, LLMGateway, finite_decimal
from app.models import Card, CardField, Document, Issue, Job, LLMCall, Page


def json_safe(value):
    return json.loads(json.dumps(value, default=lambda obj: obj.isoformat() if isinstance(obj, datetime) else str(obj), ensure_ascii=False))


def create_card(session, isin):
    issue = ensure_issue(session, isin)
    session.scalar(select(Issue).where(Issue.id == issue.id).with_for_update())
    active = session.scalar(select(Job).where(Job.type == 'collect', Job.payload['issue_id'].astext == str(issue.id), Job.status.in_(['queued', 'running'])).limit(1))
    if active:
        raise ProcessingError('collection_in_progress', 'Дождитесь завершения текущего автосбора', 409)
    version = (session.scalar(select(func.max(Card.version)).where(Card.issue_id == issue.id)) or 0) + 1
    documents = session.scalars(select(Document).where(Document.issue_id == issue.id, Document.enabled.is_(True)).order_by(Document.created_at, Document.id)).all()
    had_collect = session.scalar(select(Job.id).where(Job.type == 'collect', Job.payload['issue_id'].astext == str(issue.id)).limit(1)) is not None
    if not had_collect:
        had_collect = session.scalar(select(Card.id).where(Card.issue_id == issue.id, Card.input_snapshot['collection_done'].as_boolean().is_(True)).limit(1)) is not None
    card = Card(id=uuid.uuid4(), issue_id=issue.id, version=version, status='processing', journal={},
                input_snapshot={'document_ids': [str(d.id) for d in documents],
                                'documents': [json_safe(document_json(session, d)) for d in documents],
                                'auto_collect': not documents and not had_collect and session.scalar(select(Document.id).where(Document.issue_id == issue.id).limit(1)) is None,
                                'prepared': False, 'schema_version': '2', 'prompt_version': '2'})
    session.add(card)
    session.flush()
    job = new_job(session, 'build_card', {'issue_id': str(issue.id), 'card_id': str(card.id)})
    card.job_id = job.id
    for spec in SPECS:
        session.add(CardField(card_id=card.id, code=spec.code, status='needs_review', reason='processing',
                              not_found=False, source={}, evidence=[]))
    session.flush()
    return card, job


def field_json(field):
    return {'code': field.code, 'value': field.value, 'quote': field.quote, 'document_id': field.document_id,
            'page': field.page, 'clause': field.clause, 'status': field.status, 'iss_value': field.iss_value,
            'confidence': field.confidence, 'source': field.source, 'evidence': field.evidence,
            'reason': field.reason, 'reason_message': REASONS.get(field.reason, field.reason), 'not_found': field.not_found, 'user_status': field.user_status,
            'user_value': field.user_value, 'reviewed_at': field.reviewed_at}


def card_json(session, card):
    fields = {f.code: f for f in session.scalars(select(CardField).where(CardField.card_id == card.id))}
    snapshot = card.input_snapshot
    return {'id': card.id, 'isin': session.get(Issue, card.issue_id).isin, 'version': card.version,
            'status': card.status, 'job_id': card.job_id, 'created_at': card.created_at, 'updated_at': card.updated_at,
            'header': snapshot.get('header', {}), 'documents': snapshot.get('documents', []),
            'iss_fields': {s.code: field_json(fields[s.code]) for s in SPECS if s.iss and s.code in fields},
            'risk_fields': {s.code: field_json(fields[s.code]) for s in SPECS if not s.iss and s.code in fields},
            'journal': card.journal, 'disclaimer': DISCLAIMER}


def _assert_owner(session, job):
    current = session.get(Job, job.id)
    if job.worker_id and current.worker_id != job.worker_id:
        raise ProcessingError('lease_lost', 'Задача передана другому воркеру')


def build_card(factory, job, gateway_factory=LLMGateway):
    from app.jobs.handlers import collect, parse_document, progress, _progress_range
    settings = get_settings()
    card_id = uuid.UUID(job.payload['card_id'])
    started = time.monotonic()
    with factory.begin() as lock:
        lock.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': card_id.int % (2**63 - 1)})
        with factory() as session:
            card = session.get(Card, card_id)
            if card.status == 'ready':
                return {'card_id': str(card_id)}
            issue_id, snapshot = card.issue_id, dict(card.input_snapshot)
            if snapshot.get('prepared') and (snapshot.get('model') != settings.llm_model or snapshot.get('embedding_model') != (settings.embedding_model or None)):
                raise ProcessingError('card_config_changed', 'Модель изменилась после фиксации входов; создайте новую версию карточки')
        warnings = list(snapshot.get('warnings', []))
        if not snapshot.get('prepared'):
            if snapshot.get('auto_collect') and not snapshot.get('collection_done'):
                token = _progress_range.set((0, 30))
                try:
                    collected = collect(factory, job.id, {'issue_id': str(issue_id)}, settings)
                finally:
                    _progress_range.reset(token)
                warnings.extend(collected.get('warnings', []))
                with factory.begin() as session:
                    _assert_owner(session, job)
                    card = session.get(Card, card_id)
                    documents = session.scalars(select(Document).where(Document.issue_id == issue_id, Document.enabled.is_(True))).all()
                    snapshot = {**snapshot, 'document_ids': [str(d.id) for d in documents], 'collection_done': True, 'collection': collected}
                    card.input_snapshot = json_safe(snapshot)
            # New version -> fresh ISS; errors retain the last snapshot with warning.
            with factory() as session:
                isin = session.get(Issue, issue_id).isin
            try:
                resolved = MoexIssSource().resolve(isin)
                with factory.begin() as session:
                    update_iss(session.get(Issue, issue_id), resolved)
            except IssError:
                warnings.append('iss_unavailable_or_stale')
            documents = [uuid.UUID(identifier) for identifier in snapshot['document_ids']]
            gateway = gateway_factory(factory=factory, settings=settings, job_id=job.id)
            for index, identifier in enumerate(documents):
                try:
                    outcome = parse_document(factory, identifier, settings, gateway=gateway)
                    if outcome and outcome.get('warning'):
                        warnings.append(outcome['warning'])
                except ProcessingError as exc:
                    warnings.append(exc.code)
                progress(factory, job.id, 'index', 30 + int(15 * (index + 1) / len(documents)))
            with factory.begin() as session:
                _assert_owner(session, job)
                card, issue = session.get(Card, card_id), session.get(Issue, issue_id)
                warnings.extend(issue.normalized.get('warnings', []))
                selected = session.scalars(select(Document).where(Document.id.in_(documents), Document.issue_id == issue_id)).all()
                failed = [str(d.id) for d in selected if d.status != 'ready']
                if failed:
                    warnings.append('some_documents_not_ready')
                snapshot = {**snapshot, 'document_ids': [str(d.id) for d in selected if d.status == 'ready'],
                            'documents': [{**json_safe(document_json(session, d)), 'enabled': True} for d in selected],
                            'failed_document_ids': failed, 'header': issue.header, 'isin': issue.isin,
                            'secid': issue.secid, 'iss_raw': issue.iss_data, 'iss_fields': issue.normalized.get('fields', {}),
                            'iss_fetched_at': str(issue.iss_updated_at) if issue.iss_updated_at else None,
                            'model': settings.llm_model, 'embedding_model': settings.embedding_model or None,
                            'quote_fuzzy_threshold': settings.quote_fuzzy_threshold, 'min_confidence': settings.extraction_min_confidence,
                            'full_context_max_tokens': settings.extraction_full_context_max_tokens,
                            'warnings': list(dict.fromkeys(warnings)), 'prepared': True}
                card.input_snapshot = json_safe(snapshot)
        documents = [uuid.UUID(identifier) for identifier in snapshot['document_ids']]
        iss_url = settings.iss_base_url.rstrip('/') + '/securities/' + (snapshot.get('secid') or snapshot['isin']) + '.json'
        configuration_error = settings.llm_configuration_error
        if configuration_error:
            warnings.append(configuration_error)
        with factory() as session:
            pages = {(str(p.document_id), p.number): p.text for p in session.scalars(select(Page).where(Page.document_id.in_(documents)))}
        ready_documents = [doc for doc in snapshot['documents'] if str(doc['id']) in snapshot['document_ids']]
        full_context = full_document_context(ready_documents, pages, snapshot.get('full_context_max_tokens', settings.extraction_full_context_max_tokens))
        for index, spec in enumerate(SPECS):
            with factory() as session:
                field = session.scalar(select(CardField).where(CardField.card_id == card_id, CardField.code == spec.code))
                field_id, done = field.id, field.reason != 'processing'
            if done:
                continue  # persisted fields survive worker restart without another paid call
            iss_value = snapshot.get('iss_fields', {}).get(spec.code) if spec.iss else None
            if configuration_error:
                result = degraded_field(spec.code, iss_value, iss_url, reason=configuration_error)
            else:
                gateway = gateway_factory(factory=factory, settings=settings, job_id=job.id, field_id=field_id)
                if not spec.iss and full_context is not None:
                    context, retrieval_warnings = full_context, []
                else:
                    context, retrieval_warnings = retrieve(factory, issue_id, documents, spec.hints, settings, gateway, spec.code)
                warnings.extend(retrieval_warnings)
                if not context:
                    result = result_template(spec.code, iss_value)
                    if spec.iss:
                        result = degraded_field(spec.code, iss_value, iss_url)
                        if result['source'].get('kind') == 'moex_iss':
                            result['reason'] = 'iss_not_confirmed'
                        if not documents:
                            warnings.append('iss_only_no_documents')
                    elif snapshot.get('failed_document_ids'):
                        result.update(status='needs_review', reason='document_processing_failed')
                else:
                    try:
                        extracted = gateway.extract(spec.code, prompts.messages(spec, context), extraction_model(spec.code))
                        result = verify(spec.code, extracted, pages, context, settings, iss_value, iss_url=iss_url)
                    except GatewayError as exc:
                        warnings.append(exc.code)
                        result = degraded_field(spec.code, iss_value, iss_url, reason=exc.code)
                if result['status'] == 'not_found' and snapshot.get('failed_document_ids'):
                    result.update(status='needs_review', reason='document_processing_failed')
            if result.get('source', {}).get('kind') == 'document':
                source_doc = next((d for d in snapshot['documents'] if d['id'] == str(result['document_id'])), {})
                result['source'].update(name=source_doc.get('filename'), url=next((o['download_url'] for o in source_doc.get('origins', []) if o.get('download_url')), None))
            with factory.begin() as session:
                _assert_owner(session, job)
                field = session.get(CardField, field_id)
                for key, value in result.items():
                    if key == 'code':
                        continue
                    setattr(field, key, uuid.UUID(value) if key == 'document_id' and value else value)
            progress(factory, job.id, 'extract_fields', 45 + int(50 * (index + 1) / len(SPECS)))
        with factory.begin() as session:
            _assert_owner(session, job)
            card = session.get(Card, card_id)
            calls = session.scalars(select(LLMCall).where(LLMCall.job_id == job.id)).all()
            documents_meta = snapshot.get('documents', [])
            unknown = sum(c.cost_usd is None for c in calls)
            card.journal = {'documents_used': len(documents), 'pages': sum(d['pages'] for d in documents_meta if d['id'] in snapshot['document_ids']),
                            'risk_context_mode': 'none' if not documents else 'full_document' if full_context is not None else 'retrieval',
                            'risk_context_tokens_upper_bound': context_token_bound(full_context) if full_context is not None else None,
                            'ocr_pages': sum(d['ocr_pages'] for d in documents_meta if d['id'] in snapshot['document_ids']),
                            'models': sorted({c.model for c in calls}), 'tokens_in': sum(c.tokens_in or 0 for c in calls),
                            'tokens_out': sum(c.tokens_out or 0 for c in calls), 'unknown_cost_calls': unknown,
                            'cost_usd': None if unknown else str(sum((finite_decimal(c.cost_usd) or Decimal(0)) for c in calls)),
                            'known_cost_usd': str(sum((finite_decimal(c.cost_usd) or Decimal(0)) for c in calls)),
                            'duration_ms': int((time.monotonic() - started) * 1000), 'warnings': list(dict.fromkeys(warnings)),
                            'errors': [c.error for c in calls if c.error]}
            card.status = 'ready'
            return {'card_id': str(card_id)}
