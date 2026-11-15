from datetime import datetime, timezone
import hashlib
import json
import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.documents.storage import clean_filename, save_pdf, validate_pdf
from app.models import Document, DocumentOrigin, Issue, Job


def ensure_issue(session, isin):
    session.execute(insert(Issue).values(id=uuid.uuid4(), isin=isin, header={}, iss_data={}, normalized={}).on_conflict_do_nothing(index_elements=[Issue.isin]))
    return session.scalar(select(Issue).where(Issue.isin == isin))


def update_iss(issue, resolved):
    issue.secid = resolved["secid"]
    issue.header = resolved["header"]
    issue.iss_data = resolved["raw"]
    issue.normalized = {"fields": resolved["fields"], "warnings": resolved["warnings"]}
    issue.iss_updated_at = datetime.now(timezone.utc)
    issue.iss_error = None


def put_document(session, issue_id, content, filename, document_type, settings, origin):
    page_count = validate_pdf(content, settings)
    sha256, path = save_pdf(content, settings)
    document_id = uuid.uuid4()
    result = session.execute(insert(Document).values(id=document_id, issue_id=issue_id, sha256=sha256, path=path,
                filename=clean_filename(filename), type=document_type, pages=page_count, ocr_pages=0, enabled=True, status="queued")
                .on_conflict_do_nothing(index_elements=[Document.issue_id, Document.sha256]).returning(Document.id)).scalar_one_or_none()
    duplicate = result is None
    document = session.scalar(select(Document).where(Document.issue_id == issue_id, Document.sha256 == sha256))
    origin_key = hashlib.sha256(json.dumps(origin, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    session.execute(insert(DocumentOrigin).values(id=uuid.uuid4(), document_id=document.id, origin_key=origin_key,
                    **origin).on_conflict_do_nothing(index_elements=[DocumentOrigin.document_id, DocumentOrigin.origin_key]))
    return document, duplicate


def new_job(session, job_type, payload):
    job = Job(type=job_type, payload=payload, result={}, status="queued", step="queued", progress=0, attempts=0)
    session.add(job)
    session.flush()
    return job


def origins_for(session, document_id):
    return [{"source": o.source, "source_url": o.source_url, "download_url": o.download_url,
             "published_at": o.published_at, "fetched_at": o.fetched_at, "archive": o.archive}
            for o in session.scalars(select(DocumentOrigin).where(DocumentOrigin.document_id == document_id).order_by(DocumentOrigin.created_at, DocumentOrigin.id))]


def document_json(session, document):
    return {"id": document.id, "filename": document.filename, "type": document.type, "sha256": document.sha256,
            "status": document.status, "enabled": document.enabled, "pages": document.pages, "ocr_pages": document.ocr_pages,
            "error": document.error, "origins": origins_for(session, document.id), "created_at": document.created_at}
